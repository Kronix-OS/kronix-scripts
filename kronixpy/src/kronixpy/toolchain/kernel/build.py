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
    None,
]
type _InstallFunc = Callable[
    [KernelToolchainBuilder],
    Optional[CompletedProcess[str] | list[CompletedProcess[str]]],
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


SamePathMarkerType = NewType("SamePathMarkerType", NoneType)
SamePath = SamePathMarkerType(None)
assert None is not SamePath
assert SamePath is SamePath

SameEnvMarkerType = NewType("SameEnvMarkerType", NoneType)
SameEnv = SameEnvMarkerType(None)
assert None is not SameEnv
assert SameEnv is SameEnv


class Step(Generic[_P, _R, _RR]):
    type SubStepArgsType = tuple[
        _StepFn[_P, _RR],
        BuildAction,
        Optional[Path | SamePathMarkerType],
        Optional[dict[str, str] | SameEnvMarkerType],
    ]

    def __init__(
        self: Self,
        step_fn: _StepFn[_P, _R],
        pkg: ToolchainComponent,
        action: BuildAction,
        *substeps: SubStepArgsType,
    ):
        self._fn = step_fn
        self._pkg = pkg
        self._action = action
        if len(substeps) != 0:
            self._substeps = list(substeps)
        else:
            self._substeps = None
        return None

    @overload
    @classmethod
    def _do_call(
        cls: type[Self],
        is_substep: TrueLiteral,
        substeps: NoneType,
        path: Optional[Path | SamePathMarkerType],
        env: Optional[dict[str, str] | SameEnvMarkerType],
        fn: _StepFn[_P, _RR],
        pkg: ToolchainComponent,
        action: BuildAction,
        builder: "KernelToolchainBuilder",
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> Optional[_RR]: ...

    @overload
    @classmethod
    def _do_call(
        cls: type[Self],
        is_substep: FalseLiteral,
        substeps: Optional[SubStepArgsType],
        path: UnusedType,
        env: UnusedType,
        fn: _StepFn[_P, _R],
        pkg: UnusedType,
        action: UnusedType,
        builder: "KernelToolchainBuilder",
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> Optional[_R | tuple[_R, list[Optional[_RR]]]]: ...

    @classmethod
    def _do_call(
        cls: type[Self],
        is_substep: bool,
        substeps,
        path,
        env,
        fn,
        pkg,
        action,
        builder: "KernelToolchainBuilder",
        *args: _P.args,
        **kwargs: _P.kwargs,
    ):
        if is_substep:
            assert action.is_substep
            pinfo(f"SUBSTEP - {action.action(pkg)}")
        else:
            assert action.is_step
            pinfo("-" * 80)
            pinfo(f"STEP n°{_STEP_COUNT.mapget(add, 1)} - {action.action(pkg)}")

        result = None
        try:
            pinfo(f"{action.start(pkg)}")
            result = fn(
                pkg,
                path,
                env,
                *args,
                **kwargs,
            )
        except KeyboardInterrupt:
            raise
        except AlreadyPrinted:
            perror(f"{action.failure(pkg)}")
        except BaseException as e:
            pwarning(f"{action.failure(pkg)}: {traceback.format_exc()}")
            prompt = input("Continue ? [y/N]: ")
            if prompt.lower() != "y":
                perror("aborting")
                raise AlreadyPrinted(e)
            else:
                pwarning("continuing...")
        pinfo(action.success(pkg))
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
    ) -> Optional[_R | tuple[_R, list[_RR]]]:
        def _call_with_substeps(
            pkg: ToolchainComponent,
            path: Optional[Path] = None,
            env: Optional[dict[str, str]] = None,
            /,
            *fwargs: _P.args,
            **fwkwargs: _P.kwargs,
        ) -> tuple[_R, list[_RR]]:
            def _call_substep(func, p, e):
                if p is SamePath:
                    p = path
                if e is SameEnv:
                    e = env

            result = self._fn(pkg, path, env, *fwargs, **fwkwargs)
            subresults = [func()]
            return result, subresults

        # if self._substeps is not None:
        #    return self._do_call(
        #        False,
        #        _call_with_substeps,
        #        self._pkg,
        #        self._action,
        #        builder,
        #        *args,
        #        **kwargs,
        #    )
        path = self._get_path(self._pkg, self._action, builder)
        env = make_env_for(builder, self._pkg, self._action)
        return self._do_call(
            False,
            self._substeps,
            path,
            env,
            self._fn,
            self._pkg,
            self._action,
            builder,
            *args,
            **kwargs,
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
        return run_executable(
            "make", [*MAKEFLAGS, target], universal_newlines=True, check=True
        )
    return unreachable()


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
    return lambda builder: Step(download_latest, pkg, BuildAction.DOWNLOAD)(
        builder, url=url, archive_url=archive_url, sig_url=sig_url
    )


def _get_configure_func(pkg: ToolchainComponent) -> _ConfigureFunc:
    def _create_pkg_build_directory(builder: KernelToolchainBuilder):
        return (builder.build_directory / pkg).mkdir()

    match pkg:
        case ToolchainComponent.BINUTILS:
            [
                "--target=" + ENV_VARS["TARGET"],
                "--prefix=" + ENV_VARS["PREFIX"],
                "--with-sysroot",
                "--disable-werror",
            ]
            pass


def _get_build_func(pkg: ToolchainComponent) -> _BuildFunc:
    pass


def _get_install_func(pkg: ToolchainComponent) -> _InstallFunc:
    def _gcc_inst(builder: KernelToolchainBuilder) -> list[CompletedProcess[str]]:
        completed = []
        for target in [
            "install-gcc",
            "install-target-libgcc",
            "install-target-libstdc++-v3",
        ]:
            completed.append(
                Step(make, ToolchainComponent.GCC, BuildAction.INSTALL)(
                    builder, target=target
                )
            )
        return completed

    match pkg:
        case (
            ToolchainComponent.BINUTILS
            | ToolchainComponent.NASM
            | ToolchainComponent.QEMU
            | ToolchainComponent.LIMINE
            | ToolchainComponent.GDB
        ):
            return lambda builder: Step(make, pkg, BuildAction.INSTALL)(
                builder, target="install"
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
        todo()
    # TODO: maybe arch ?
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
