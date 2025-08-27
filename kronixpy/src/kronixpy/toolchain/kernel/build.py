from ...common import Namespace, Arch, common_main
from ...utils.errprint import *
from ...utils.mutex import Mutex
from ...utils import *
from .. import ToolchainComponent, MAKEJOBS, MAKEFLAGS, MAKELOAD, BuildAction
from ...utils import semver, download
from pathlib import Path
from shutil import rmtree as rm, move as mv
from types import NoneType
from typing import (
    NewType,
    TypeAlias,
    Self,
    Iterable,
    TypeVar,
    Unpack,
    Any,
    ClassVar,
    IO,
    ParamSpec,
    TypedDict,
    NotRequired,
    Concatenate,
    Protocol,
    runtime_checkable,
    TypeVarTuple,
)
from abc import abstractmethod
from collections.abc import Callable
from functools import cached_property, cache as cached, lru_cache as lru_cached
from multiprocessing.pool import ThreadPool
from operator import add
import subprocess
from subprocess import CompletedProcess
import traceback
import requests
from contextlib import closing
from bs4 import BeautifulSoup
import re
import ftplib
from string import Template
import tarfile
import zipfile
from ...utils import FrozenDict

type _DownloadFunc = Callable[
    [KernelToolchainBuilder],
    None,
]
type _ConfigureFunc = Callable[
    [KernelToolchainBuilder],
    None,
]
type _BuildFunc = Callable[
    [KernelToolchainBuilder],
    Any,
]
type _InstallFunc = Callable[
    [KernelToolchainBuilder],
    Optional[CompletedProcess[str] | dict[int, CompletedProcess[str]]],
]


def _untar_or_unzip(
    pkg: ToolchainComponent, exts: list[str], archive: io.BytesIO, to: Path
):
    pdebug(f"decompressing `{pkg}.{'.'.join(exts)}` to `{to}`")
    to.mkdir()
    exceptions = []
    archive.seek(0)

    def _rm_nested_dir():
        iterator = to.iterdir()
        inner = next(iterator)
        try:
            next(iterator)
        except StopIteration:
            pass
        else:
            return None

        for path in inner.iterdir():
            mv(path, to)

        inner.rmdir()

        return None

    try:
        with tarfile.open(fileobj=archive, mode="r") as tar:
            tar.extractall(to)
    except BaseException as e:
        exceptions.append(e)
    else:
        return _rm_nested_dir()

    try:
        with zipfile.ZipFile(archive, "r") as zip:
            zip.extractall(to)
    except BaseException as e:
        exceptions.append(e)
    else:
        return _rm_nested_dir()

    raise ExceptionGroup("could not decompress in-memory archive", exceptions)


_STEP_COUNT: Mutex[int] = Mutex(0)

if False:
    _P = ParamSpec("_P", infer_variance=True)
else:
    _P = ParamSpec("_P")
_R = TypeVar("_R", infer_variance=True)
_RR = TypeVar("_RR", infer_variance=True)


@runtime_checkable
class _StepFn(Protocol[_P, _R]):
    @abstractmethod
    def __call__(
        self: Self,
        pkg: ToolchainComponent,
        path: Optional[Path] = None,
        env: Optional[dict[str, str]] = None,
        /,
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> _R: ...


# fmt: off
class SamePathMarkerType:
    pass
SamePath = SamePathMarkerType()
# fmt: on
assert None is not SamePath
assert SamePath is SamePath
assert not isinstance(None, SamePathMarkerType)
assert isinstance(SamePath, SamePathMarkerType)


# fmt: off
class SameEnvMarkerType:
    pass
SameEnv = SameEnvMarkerType()
# fmt: on
assert None is not SameEnv
assert SameEnv is SameEnv
assert not isinstance(None, SameEnvMarkerType)
assert isinstance(SameEnv, SameEnvMarkerType)

StepInfo = NewType("StepInfo", tuple[BuildAction, Optional[tuple[int, str]]])


class Step(Generic[_P, _R, _RR]):
    type SubStepArgs = tuple[
        _StepFn[_P, _RR],
        StepInfo,
        Optional[Path | SamePathMarkerType],
        Optional[dict[str, str] | SameEnvMarkerType],
    ]

    def __init__(
        self: Self,
        step_fn: _StepFn[_P, _R],
        pkg: ToolchainComponent,
        action: BuildAction,
        *substeps: SubStepArgs,
    ):
        self._fn = step_fn
        self._pkg = pkg
        self._action = action
        if len(substeps) != 0:
            self._substeps = list(substeps)
        else:
            self._substeps = None
        return None

    @classmethod
    def _do_call[_Res](
        cls: type[Self],
        is_substep: bool,
        fn: Callable[[], _Res],
        pkg: ToolchainComponent,
        info: StepInfo,
    ) -> Optional[_Res]:
        action, substep_info = info
        if is_substep:
            assert substep_info is not None
            part, desc = substep_info
            pinfo(f"SUBSTEP - {action.action(pkg, part, desc)}")
        else:
            assert substep_info is None
            part, desc = None, None
            pinfo("-" * 80)
            pinfo(f"STEP n°{_STEP_COUNT.mapget(add, 1)} - {action.action(pkg)}")

        result = None
        try:
            pinfo(f"{action.start(pkg, part, desc)}")
            result = fn()
        except KeyboardInterrupt:
            raise
        except AlreadyPrinted:
            perror(f"{action.failure(pkg, part, desc)}")
        except BaseException as e:
            pwarning(f"{action.failure(pkg, part, desc)}: {traceback.format_exc()}")
            prompt = input("Continue ? [y/N]: ")
            if prompt.lower() != "y":
                perror("aborting")
                raise AlreadyPrinted(e)
            else:
                pwarning("continuing...")
        pinfo(action.success(pkg, part, desc))
        return result

    @classmethod
    def _get_path(
        cls: type[Self],
        pkg: ToolchainComponent,
        action: BuildAction,
        builder: "KernelToolchainBuilder",
    ) -> Path:
        match action:
            case BuildAction.DOWNLOAD:
                return builder.src_directory
            case BuildAction.CONFIGURE | BuildAction.INSTALL | BuildAction.BUILD:
                return builder.build_directory / pkg
        return unreachable()

    def __call__(
        self: Self,
        builder: "KernelToolchainBuilder",
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> Optional[_R | tuple[_R, dict[int, Optional[_RR]]]]:
        def _call_normal() -> _R:
            assert self._substeps is None
            path = self._get_path(self._pkg, self._action, builder)
            env = make_env_for(builder, self._pkg, self._action)
            return self._fn(self._pkg, path, env, *args, **kwargs)

        def _call_with_substeps() -> tuple[_R, dict[int, Optional[_RR]]]:
            assert self._substeps is not None
            path = self._get_path(self._pkg, self._action, builder)
            env = make_env_for(builder, self._pkg, self._action)
            res = self._fn(self._pkg, path, env, *args, **kwargs)
            subresults = {}
            for subfn, (subact, substep_info), subpath, subenv in self._substeps:
                assert substep_info is not None
                part, _ = substep_info
                if isinstance(subenv, SameEnvMarkerType):
                    prepared_env = env
                else:
                    prepared_env = subenv
                if isinstance(subpath, SamePathMarkerType):
                    prepared_path = path
                else:
                    prepared_path = subpath
                subresults[part] = self._do_call(
                    True,
                    lambda: subfn(
                        self._pkg, prepared_path, prepared_env, *args, **kwargs
                    ),
                    self._pkg,
                    StepInfo((subact, substep_info)),
                )

            return res, subresults

        if self._substeps is not None:
            return self._do_call(
                False, _call_with_substeps, self._pkg, StepInfo((self._action, None))
            )
        return self._do_call(
            False, _call_normal, self._pkg, StepInfo((self._action, None))
        )


def make(
    pkg: ToolchainComponent,
    path: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    /,
    *,
    target: str,
) -> CompletedProcess[str]:
    with save_env(env=env, path=path) as savedenv:
        if pkg == ToolchainComponent.NASM:
            return run_executable(
                "make",
                [
                    *MAKEFLAGS,
                    target,
                    "AR=" + os.environ["AR"],
                    "RANLIB=" + os.environ["RANLIB"],
                ],
                universal_newlines=True,
                check=True,
            )
        else:
            return run_executable(
                "make", [*MAKEFLAGS, target], universal_newlines=True, check=True
            )
    return unreachable()


def noop(
    pkg: ToolchainComponent,
    path: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    /,
):
    return None


_REG_VERSION_STRING = r"^/?(%s-)?(?P<version>\d+\.\d+(\.\d+)?)((\.(zip|tar.*)|/)?)$"
REG_VERSION: FrozenDict[ToolchainComponent, re.Pattern[str]] = FrozenDict(
    map(
        lambda pkg: (pkg, re.compile(_REG_VERSION_STRING % pkg)),
        filter(
            lambda component: component != ToolchainComponent.ALL,
            iter(ToolchainComponent),
        ),
    )
)


def _get_latest_version_from_ftp(ftp_url: str, pkg: ToolchainComponent) -> str:
    pdebug(str(REG_VERSION[pkg]))
    if not "://" in ftp_url:
        actual_ftp = ftp_url
    else:
        actual_ftp = ftp_url.split("://")[1]
    if "/" in actual_ftp:
        directory = "/".join(actual_ftp.split("/")[1:])
    else:
        directory = ""
    actual_ftp = actual_ftp.split("/")[0]
    with ftplib.FTP(actual_ftp) as ftp:
        ftp.login()
        ftp.cwd(directory)
        versions = ftp.nlst()
        versions: list[str] = [
            matched_version.group("version")
            for matched_version in map(REG_VERSION[pkg].match, versions)
            if bool(matched_version)
        ]

        versions = semver.sort(versions, reverse=True)
    return versions[0]


def _get_latest_version_from_http(http_url: str, pkg: ToolchainComponent) -> str:
    pdebug(str(REG_VERSION[pkg]))
    # TODO: match against link text instead of link href
    response = requests.get(http_url)
    soup = BeautifulSoup(response.text, "html.parser")
    links = soup.find_all("a")
    versions: list[str] = [
        matched_version.group("version")
        for matched_version in map(REG_VERSION[pkg].match, map(lambda l: l.text, links))
        if bool(matched_version)
    ]

    versions = semver.sort(versions, reverse=True)
    return versions[0]


def download_latest(
    pkg: ToolchainComponent,
    path: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    /,
    *,
    url: str,
    archive_url: Template,
    sig_url: Optional[Template],
):
    with save_env(env=env, path=path):
        pdebug(f"retrieving latest {pkg} version...")
        if url.startswith("http"):
            vers = _get_latest_version_from_http(url, pkg)
        elif url.startswith("ftp"):
            vers = _get_latest_version_from_ftp(url, pkg)
        else:
            return unreachable(f"`{url}` is neither an ftp url nor an http url")
        pdebug(f"latest {pkg} version is {vers}")

        if archive_url.template.startswith("http"):
            dl_archive = download.from_http
        elif archive_url.template.startswith("ftp"):
            dl_archive = download.from_ftp
        else:
            return unreachable(
                f"`{archive_url.template}` is neither an ftp url nor an http url"
            )
        archive_suffixes = archive_url.template.split("/")[-1].split(".")[1:]
        assert isoneof(len(archive_suffixes))(1, 2)
        if archive_suffixes[0] == "tar":
            archive_suffix = f".{archive_suffixes[0]}.{archive_suffixes[1]}"
        else:
            archive_suffix = f".{archive_suffixes[0]}"
        pdebug(
            f"downloading archive at {archive_url.safe_substitute(version=vers, pkg=pkg)}"
        )
        archive = dl_archive(archive_url.safe_substitute(version=vers, pkg=pkg))
        assert archive is not None

        with closing(archive) as archive:
            if sig_url is not None:
                if sig_url.template.startswith("http"):
                    dl_sig = download.from_http
                elif sig_url.template.startswith("ftp"):
                    dl_sig = download.from_ftp
                else:
                    return unreachable(
                        f"`{sig_url.template}` is neither an ftp url nor an http url"
                    )
                sig = Path(pkg).with_suffix(f"{archive_suffix}.sig")
                pdebug(
                    f"downloading signature at {sig_url.safe_substitute(version=vers, pkg=pkg)}"
                )
                dl_sig(
                    sig_url.safe_substitute(version=vers, pkg=pkg),
                    to=sig,
                )
            else:
                pwarning(f"no signature file for package {pkg}")
                sig = None

            if sig is not None:
                verify_data(archive, sig)

            return _untar_or_unzip(pkg, archive_suffixes, archive, Path(pkg))

    return unreachable()


def configure(
    pkg: ToolchainComponent,
    path: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    /,
    *configure_args: str,
    pkgdir: Path,
):
    with save_env(env=env, path=path) as savedenv:
        run_executable(pkgdir / "configure", configure_args, check=True)
    return None


def substep(
    fn: _StepFn[_P, _R],
    action: BuildAction,
    part: int,
    desc: str,
    path: Optional[Path | SamePathMarkerType] = None,
    env: Optional[dict[str, str] | SameEnvMarkerType] = None,
) -> Step.SubStepArgs:
    return (fn, StepInfo((action, (part, desc))), path, env)


def _get_download_func(pkg: ToolchainComponent) -> _DownloadFunc:
    match pkg:
        case gnu if gnu.is_gnu_pkg:
            # _base_url = "https://ftp.gnu.org/gnu"
            _base_url = "https://ftpmirror.gnu.org"
            url = f"https://ftp.gnu.org/gnu/{pkg}/"
            if gnu == ToolchainComponent.GCC:
                archive_url = Template(
                    f"{_base_url}/${{pkg}}/${{pkg}}-${{version}}/${{pkg}}-${{version}}.tar.xz"
                )
                sig_url = Template(
                    f"{_base_url}/${{pkg}}/${{pkg}}-${{version}}/${{pkg}}-${{version}}.tar.xz.sig"
                )
            else:
                archive_url = Template(
                    f"{_base_url}/${{pkg}}/${{pkg}}-${{version}}.tar.xz"
                )
                sig_url = Template(
                    f"{_base_url}/${{pkg}}/${{pkg}}-${{version}}.tar.xz.sig"
                )
        case ToolchainComponent.NASM:
            _base_url = "https://www.nasm.us/pub/nasm/releasebuilds"
            url = _base_url
            archive_url = Template(
                f"{_base_url}/${{version}}/${{pkg}}-${{version}}.tar.xz"
            )
            sig_url = None
        case ToolchainComponent.QEMU:
            _base_url = "https://download.qemu.org"
            url = _base_url
            archive_url = Template(f"{_base_url}/${{pkg}}-${{version}}.tar.xz")
            sig_url = Template(f"{_base_url}/${{pkg}}-${{version}}.tar.xz.sig")
        case _:
            return unreachable(f"unknown or unsupported package `{pkg}`")
    return lambda builder: assert_none(
        Step(download_latest, pkg, BuildAction.DOWNLOAD)(
            builder, url=url, archive_url=archive_url, sig_url=sig_url
        )
    )


def gcc_prerequisites(
    pkg: ToolchainComponent,
    path: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    /,
):
    with save_env(env=env, path=path) as savedenv:
        run_executable("./contrib/download_prerequisites", [])
    return None


def sed(
    pkg: ToolchainComponent,
    path: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    /,
    *sed_args: str,
    sed_script: str,
    in_place: Optional[Path],
):
    with save_env(env=env, path=path) as savedenv:
        if in_place is not None:
            run_executable("sed", [*sed_args, "-i", "-e", sed_script, in_place])
        else:
            run_executable("sed", [*sed_args, "-e", sed_script])
    return None


def _get_configure_func(pkg: ToolchainComponent) -> _ConfigureFunc:
    def _create_pkg_build_directory(builder: KernelToolchainBuilder):
        return (builder.build_directory / pkg).mkdir()

    def _gcc_conf(builder: KernelToolchainBuilder):
        _create_pkg_build_directory(builder)
        gcc_conf_args = [
            "--target=" + builder.target,
            "--prefix=" + stringify(builder.prefix),
            "--enable-languages=c,c++",
            "--enable-decimal-float=yes",
            "--enable-fixed-point=auto",
            "--enable-lto",
            "--enable-long-long",
            "--with-long-double-128",
            "--with-static-standard-libraries",
            "--enable-plugin",
            "--without-headers",
            "--disable-shared",
            "--disable-host-shared",
            "--disable-libssp",
            "--disable-libstdcxx-pch",
            "--disable-libada",
            "--disable-libsanitizer",
            "--disable-libquadmath",
            "--disable-libgomp",
            "--disable-hosted-libstdcxx",
            "--enable-libstdcxx-static-eh-pool",
            "--with-libstdcxx-eh-pool-obj-count=64",
            "--disable-thread",
            "--with-gnu-as",
            "--with-gnu-ld",
            "--with-arch=" + builder._default_arch,
            "--with-tune=" + builder._default_tune,
        ]
        substeps = [
            substep(
                gcc_prerequisites,
                BuildAction.CONFIGURE,
                1,
                "download gcc prerequisites",
                builder.src_directory / "gcc",
                SameEnv,
            ),
            substep(
                lambda pkg, path=None, env=None: configure(
                    pkg,
                    path,
                    env,
                    *gcc_conf_args,
                    pkgdir=(builder.src_directory / "gcc"),
                ),
                BuildAction.CONFIGURE,
                2,
                "run configure script",
                SamePath,
                SameEnv,
            ),
        ]
        step = Step(noop, ToolchainComponent.GCC, BuildAction.CONFIGURE, *substeps)
        step(builder)
        return None

    configure_args: Callable[[KernelToolchainBuilder], list[str]]
    match pkg:
        case ToolchainComponent.GCC:
            return _gcc_conf
        case ToolchainComponent.BINUTILS:
            configure_args = lambda builder: [
                "--target=" + builder.target,
                "--prefix=" + stringify(builder.prefix),
                "--with-sysroot",
                "--disable-werror",
            ]
        case ToolchainComponent.NASM:
            configure_args = lambda builder: [
                "--prefix=" + stringify(builder.prefix),
                "--enable-lto",
            ]
        case ToolchainComponent.GDB:
            configure_args = lambda builder: [
                "--target=" + builder.target,
                "--prefix=" + stringify(builder.prefix),
                "--disable-werror",
                "--with-python=/usr/bin/python3",
                "--with-guile=guile-3.0",
            ]
        case ToolchainComponent.QEMU:
            configure_args = lambda builder: [
                "--prefix=" + stringify(builder.prefix),
                "--target-list=" + builder._arch.to_qemu_target_list(),
                "--python=/usr/bin/python3",
                "--gdb="
                + stringify(builder.install_directory / "bin" / builder.target)
                + "-gdb",
                "--enable-lto",
                "--enable-strip",
                "--enable-tcg-interpreter",
                "--disable-docs",
                "--enable-virtfs",
                "--enable-plugins",
            ]
        case _:
            return unreachable(f"unknown or unsupported package `{pkg}`")

    def _retfn(
        cfgargs: Callable[[KernelToolchainBuilder], list[str]],
        builder: KernelToolchainBuilder,
    ):
        _create_pkg_build_directory(builder)
        return assert_none(
            Step(configure, pkg, BuildAction.CONFIGURE)(
                builder,
                *cfgargs(builder),
                pkgdir=(builder.src_directory / pkg),
            )
        )

    return bind(_retfn, configure_args)


def _get_build_func(pkg: ToolchainComponent) -> _BuildFunc:
    def _build_gcc(builder: KernelToolchainBuilder):
        counter = 1

        def _mk_sed_substep() -> Step.SubStepArgs:
            nonlocal counter
            ret = substep(
                bind(
                    sed,
                    sed_script="s/PICFLAG/DISABLED_PICFLAG/g",
                    in_place=(Path(builder.target) / "libgcc" / "Makefile"),
                ),
                BuildAction.BUILD,
                counter,
                "patch libgcc to enable building with -mcmodel=kernel",
                SamePath,
                SameEnv,
            )
            counter += 1
            return ret

        def _mk_make_substep(
            what: str, msg: Optional[str] = None, tgt: str = "all"
        ) -> Step.SubStepArgs:
            nonlocal counter
            ret = substep(
                bind(make, target=f"{tgt}-{what}"),
                BuildAction.BUILD,
                counter,
                msg or f"build {what}",
                SamePath,
                SameEnv,
            )
            counter += 1
            return ret

        substeps = [
            _mk_make_substep(
                "target-libgcc", msg="configure target-libgcc", tgt="configure"
            ),
            _mk_sed_substep(),
            _mk_make_substep("gcc"),
            _mk_make_substep("target-libgcc"),
            _mk_make_substep("target-libstdc++-v3"),
        ]
        step = Step(noop, ToolchainComponent.GCC, BuildAction.BUILD, *substeps)
        return step(builder)

    match pkg:
        case ToolchainComponent.GCC:
            return _build_gcc
        case (
            ToolchainComponent.BINUTILS
            | ToolchainComponent.NASM
            | ToolchainComponent.GDB
            | ToolchainComponent.QEMU
        ):
            return lambda builder: Step(make, pkg, BuildAction.BUILD)(
                builder, target="all"
            )
        case _:
            return unreachable(f"unknown or unsupported package `{pkg}`")

    return unreachable()


def _get_install_func(pkg: ToolchainComponent) -> _InstallFunc:
    def _assert_not_tuple(value):
        assert not isinstance(value, tuple)
        return value

    def _gcc_inst(builder: KernelToolchainBuilder) -> dict[int, CompletedProcess[str]]:
        counter = 1

        def _mk_substep(what: str) -> Step.SubStepArgs:
            nonlocal counter
            ret = substep(
                bind(make, target=f"install-{what}"),
                BuildAction.INSTALL,
                counter,
                f"install {what}",
                SamePath,
                SameEnv,
            )
            counter += 1
            return ret

        substeps = [
            _mk_substep("gcc"),
            _mk_substep("target-libgcc"),
            _mk_substep("target-libstdc++-v3"),
        ]
        step = Step(noop, ToolchainComponent.GCC, BuildAction.INSTALL, *substeps)
        res = assert_some(step(builder))
        return dict(map((lambda kv: (kv[0], assert_some(kv[1]))), res[1].items()))

    match pkg:
        case (
            ToolchainComponent.BINUTILS
            | ToolchainComponent.NASM
            | ToolchainComponent.QEMU
            | ToolchainComponent.GDB
        ):
            return lambda builder: _assert_not_tuple(
                Step(make, pkg, BuildAction.INSTALL)(builder, target="install")
            )
        case ToolchainComponent.GCC:
            return _gcc_inst
        case _:
            return unreachable(f"unknown or unsupported package `{pkg}`")
    return unreachable()


def make_env_for(
    builder: "KernelToolchainBuilder", pkg: ToolchainComponent, action: BuildAction
) -> dict[str, str]:
    env = deepcopy(builder._env)
    env[PATHVAR] = list_to_pathvar(builder._pathenv)
    env["PREFIX"] = str(builder.prefix)
    c_flags = builder.c_flags
    c_flags.append(f"-march={builder._default_arch}")
    c_flags.append(f"-mtune={builder._default_tune}")
    cxx_flags = builder.cxx_flags
    cxx_flags.append(f"-march={builder._default_arch}")
    cxx_flags.append(f"-mtune={builder._default_tune}")
    env["CFLAGS_FOR_TARGET"] = " ".join(c_flags)
    env["CXXFLAGS_FOR_TARGET"] = " ".join(cxx_flags)
    env["TARGET"] = builder.target

    if pkg == ToolchainComponent.GCC and action == BuildAction.CONFIGURE:
        pass  # TODO: maybe arch ?
    return env


class KernelToolchainBuilder:
    def __init__(
        self: Self,
        packages: Iterable[ToolchainComponent],
        dir: Path,
        arch: Arch,
        default_arch: str,
        default_tune: str,
    ):
        self._packages = set(packages)
        pdebug(", ".join(self._packages))
        self._dir = dir
        self._arch = arch
        self._default_arch = default_arch
        self._default_tune = default_tune
        self._pathenv = get_path()
        self._pathenv.insert(0, str(self.install_directory / "bin"))

        # TODO: maybe `-ffreestanding` and/or `-fno-stdlib` or similar
        self._cflags = [
            "-O2",
            "-g",
            "-mno-red-zone",
            "-mcmodel=kernel",
            "-frecord-gcc-switches",
        ]
        self._cxxflags = [
            "-O2",
            "-g",
            "-mno-red-zone",
            "-mcmodel=kernel",
            "-frecord-gcc-switches",
        ]

        pkgconfpath = os.environ.get("PKG_CONFIG_PATH")
        if pkgconfpath is None:
            pkgconfpath = []
        else:
            pkgconfpath = list_from_pathvar(pkgconfpath)
        self._env: dict[str, str] = {
            "CC": "/usr/bin/gcc",
            "CXX": "/usr/bin/g++",
            "AR": "/usr/bin/gcc-ar",
            "NM": "/usr/bin/gcc-nm",
            "RANLIB": "/usr/bin/gcc-ranlib",
            "LD": "/usr/bin/ld",
            "AS": "/usr/bin/as",
            "OBJCOPY": "/usr/bin/objcopy",
            "OBJDUMP": "/usr/bin/objdump",
            "READELF": "/usr/bin/readelf",
            "STRIP": "/usr/bin/strip",
            "SIZE": "/usr/bin/size",
            "STRINGS": "/usr/bin/strings",
            "ADDR2LINE": "/usr/bin/addr2line",
            "PKG_CONFIG_PATH": list_to_pathvar(
                [
                    "/usr/local/lib/pkgconfig/",
                    "/usr/local/lib64/pkgconfig/",
                    *pkgconfpath,
                ]
            ),
        }
        return None

    @property
    def root_directory(self: Self) -> Path:
        return self._dir

    @property
    def src_directory(self: Self) -> Path:
        return self._dir / "src"

    @property
    def build_directory(self: Self) -> Path:
        return self._dir / "build"

    @property
    def install_directory(self: Self) -> Path:
        return self._dir / "install"

    prefix = install_directory

    @property
    def target(self: Self) -> str:
        return self._arch.to_kernel_triplet()

    @property
    def cxx_flags(self: Self) -> list[str]:
        return self._cxxflags

    @cxx_flags.setter
    def cxx_flags(self: Self, flags: Iterable[str]):
        self._cxxflags = list(flags)
        return None

    @property
    def c_flags(self: Self) -> list[str]:
        return self._cflags

    @c_flags.setter
    def c_flags(self: Self, flags: Iterable[str]):
        self._cflags = list(flags)
        return None

    def prepare(self: Self, /, __builddir_not_found=False):
        if not self.root_directory.exists(follow_symlinks=False):
            if __builddir_not_found:
                pinfo("could not find build directory")
            else:
                pinfo("could not find root directory")
            self.root_directory.mkdir(parents=True)
            self.src_directory.mkdir()
            self.build_directory.mkdir()
            self.install_directory.mkdir()
            self._packages = set(
                filter(
                    # see `_parse_package_list` for more info
                    lambda component: (
                        component != ToolchainComponent.LIMINE
                        and component != ToolchainComponent.ALL
                    ),
                    iter(ToolchainComponent),
                )
            )
            pinfo(f"rebuilding every packages: {", ".join(self._packages)}")
            return None

        if not self.build_directory.exists(follow_symlinks=False):
            rm(self.root_directory)
            return self.prepare(__builddir_not_found=True)

        rm(self.install_directory, ignore_errors=True)
        self.install_directory.mkdir()
        for pkg in self._packages:
            pkgdir = self.build_directory / pkg
            if pkgdir.exists(follow_symlinks=False):
                rm(pkgdir)
        for pkgdir in filter(
            lambda d: d.is_dir(follow_symlinks=False), self.build_directory.iterdir()
        ):
            _get_install_func(ToolchainComponent(pkgdir.name))(self)

        if self.src_directory.exists(follow_symlinks=False):
            for fsobj in filter(
                lambda d: any([d.name.startswith(pkg) for pkg in self._packages]),
                self.src_directory.iterdir(),
            ):
                if fsobj.is_dir(follow_symlinks=False):
                    rm(fsobj)
                else:
                    fsobj.unlink()
        else:
            self.src_directory.mkdir()

        return None

    def download(self: Self):
        for pkg in ToolchainComponent.toolchain_build_order(self._packages):
            _get_download_func(pkg)(self)
        return None

    def build_and_install(self: Self):
        for pkg in ToolchainComponent.toolchain_build_order(self._packages):
            _get_configure_func(pkg)(self)
            _get_build_func(pkg)(self)
            _get_install_func(pkg)(self)
        return None


def _parse_package_list(args: Namespace) -> list[ToolchainComponent]:
    commasep: str = args.rebuild_package
    packages = [ToolchainComponent(x) for x in commasep.split(",")]
    if ToolchainComponent.ALL in packages:
        return list(
            filter(
                # Limine is handled separately when building the kernel
                lambda component: component != ToolchainComponent.LIMINE
                and component != ToolchainComponent.ALL,
                iter(ToolchainComponent),
            )
        )
    return packages


def main(args: Namespace) -> int:
    try:
        common_main(args)
        builder = KernelToolchainBuilder(
            _parse_package_list(args),
            Path(args.toolchain_dir).resolve(),
            Arch.coerce_from(args.architecture),
            args.with_target_arch,
            args.with_target_tune,
        )
        builder.prepare()
        builder.download()
        builder.build_and_install()
    except AlreadyPrinted:
        perror("failed to build kernel toolchain")
        return 1
    except BaseException:
        perror(f"failed to build kernel toolchain: {traceback.format_exc()}")
        return 1
    return 0


# _add_gcc_config_opts(
#     [
#         "--with-arch=" + cmdline_args.with_target_arch.strip(),
#         "--with-tune=" + cmdline_args.with_target_tune.strip(),
#     ]
# )
# _set_qemu_cpu(cmdline_args.with_target_arch.strip())
