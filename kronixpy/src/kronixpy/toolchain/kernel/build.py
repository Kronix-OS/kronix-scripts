from ... import Namespace, Arch, common_main
from ...utils.errprint import *
from ...utils.mutex import Mutex
from ...utils import *
from .. import ToolchainComponent, MAKEJOBS, MAKEFLAGS, MAKELOAD, BuildAction
from ...utils import semver, download
from pathlib import Path
from shutil import rmtree
from typing import Self, Callable, Iterable, TypeVar, Unpack, Any, ClassVar, IO
from functools import cached_property, cache as cached, lru_cache as lru_cached
from multiprocessing.pool import ThreadPool
from operator import add
import subprocess
from subprocess import CompletedProcess
import traceback
import requests
from bs4 import BeautifulSoup
import re
import ftplib
from string import Template
import tarfile
import zipfile

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


def _untar_or_unzip(archive: Path, to: Path):
    pdebug(f"decompressing `{archive}` to `{to}`")
    to.mkdir()
    if archive.suffix == ".zip":
        fn = zipfile.ZipFile
        args = (archive, "r")
    elif archive.suffixes[-2] == ".tar":
        fn = tarfile.TarFile
        args = (archive, "r")
    else:
        raise ValueError(f"unknown archive kind: {archive}")

    with fn(*args) as archivefile:
        archivefile.extractall(to)

    return None


_STEP_COUNT: Mutex[int] = Mutex(1)


class Step:
    def __init__(
        self: Self,
        step_fn: Callable,
        pkg: ToolchainComponent,
        action: BuildAction,
    ):
        self._fn = step_fn
        self._pkg = pkg
        self._action = action
        return None

    def __call__(
        self: Self, builder: "KernelToolchainBuilder", *args, **kwargs
    ) -> Optional[Any]:
        pinfo(f"STEP n°{_STEP_COUNT.mapget(add, 1)} - {self._action.action(self._pkg)}")
        result = None
        try:
            pinfo(f"{self._action.start(self._pkg)}")
            match self._action:
                case BuildAction.DOWNLOAD:
                    path = builder.src_directory
                case BuildAction.CONFIGURE:
                    path = builder.src_directory / self._pkg
                case BuildAction.INSTALL | BuildAction.BUILD:
                    path = builder.build_directory / self._pkg
            result = self._fn(
                env=make_env_for(builder, self._pkg, self._action),
                path=path,
                pkg=self._pkg,
                *args,
                **kwargs,
            )
        except BaseException as e:
            pwarning(f"{self._action.failure(self._pkg)}: {traceback.format_exc()}")
            prompt = input("Continue ? [y/N]: ")
            if prompt.lower() != "y":
                perror("aborting")
                raise e
        pinfo(self._action.success(self._pkg))
        return result


def make(
    target: str,
    path: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    *args,
    **kwargs,
) -> CompletedProcess[str]:
    with save_env(env=env, path=path) as savedenv:
        pdebug(f"running `{" ".join(["make", *MAKEFLAGS, target])}`")
        return subprocess.run(
            ["make", *MAKEFLAGS, target], universal_newlines=True, check=True
        )
    return unreachable()


def _get_latest_version_from_ftp(ftp_url: str, pkg: ToolchainComponent) -> str:
    REG_VERSION_STRING = r"(%s-)?\d+\.\d+.*" % pkg
    REG_VERSION = re.compile(REG_VERSION_STRING)
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
        versions = [version for version in versions if REG_VERSION.match(version)]
        cpy: list[str] = []
        for version in versions:
            if version.startswith(pkg):
                cpy.append(version.split("-")[1])
            else:
                cpy.append(version)
        versions = cpy

        def rm_non_numeric_parts(version: str) -> str:
            def rightchar(char: str) -> bool:
                return char.isdigit() or char == "."

            def rmpoint(v: str) -> str:
                if v[-1] == ".":
                    return v[:-1]
                return v

            for i, char in enumerate(version):
                if not rightchar(char):
                    return rmpoint(version[:i])
            return version

        versions = [rm_non_numeric_parts(version) for version in versions]
        versions = semver.sort(versions, reverse=True)
    return versions[0]


def _get_latest_version_from_http(http_url: str, pkg: ToolchainComponent) -> str:
    REG_VERSION_STRING = r"(%s-)?\d+\.\d+.*" % pkg
    REG_VERSION = re.compile(REG_VERSION_STRING)
    response = requests.get(http_url)
    soup = BeautifulSoup(response.text, "html.parser")
    links = soup.find_all("a")
    versions = [
        str(link.get("href"))
        for link in links
        if REG_VERSION.match(str(link.get("href")))
    ]
    cpy: list[str] = []
    for version in versions:
        if version.startswith(pkg):
            cpy.append(version.split("-")[1])
        else:
            cpy.append(version)
    versions = cpy

    def rm_non_numeric_parts(version: str) -> str:
        def rightchar(char: str) -> bool:
            return char.isdigit() or char == "."

        def rmpoint(v: str) -> str:
            if v[-1] == ".":
                return v[:-1]
            return v

        for i, char in enumerate(version):
            if not rightchar(char):
                return rmpoint(version[:i])
        return version

    versions = [rm_non_numeric_parts(version) for version in versions]
    versions = semver.sort(versions, reverse=True)
    return versions[0]


def download_latest(
    url: str,
    pkg: ToolchainComponent,
    archive_url: Template,
    sig_url: Optional[Template],
    path: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    *args,
    **kwargs,
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
        archive_suffix = archive_url.template.split(".")
        if archive_suffix[-2] == "tar":
            archive_suffix = f".{archive_suffix[-2]}.{archive_suffix[-1]}"
        else:
            archive_suffix = f".{archive_suffix[-1]}"
        archive = Path(pkg).with_suffix(archive_suffix)
        pdebug(
            f"downloading archive at {archive_url.safe_substitute(version=vers, pkg=pkg)}"
        )
        dl_archive(
            archive_url.safe_substitute(version=vers, pkg=pkg),
            to=archive,
        )

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
            verify_file(archive, sig)

        return _untar_or_unzip(archive, Path(pkg))


def _get_download_func(pkg: ToolchainComponent) -> _DownloadFunc:
    match pkg:
        case gnu if gnu.is_gnu_pkg:
            _base_url = "https://ftp.gnu.org/gnu"
            url = f"{_base_url}/{pkg}/"
            if gnu == ToolchainComponent.GCC:
                archive_url = Template(
                    f"{_base_url}/${{pkg}}-${{version}}/${{pkg}}-${{version}}.tar.xz"
                )
                sig_url = Template(
                    f"{_base_url}/${{pkg}}-${{version}}/${{pkg}}-${{version}}.tar.xz.sig"
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

    def prepare(self: Self):
        rmtree(self.install_directory, ignore_errors=True)
        self.install_directory.mkdir(parents=True, exist_ok=False)
        for pkg in self._packages:
            pkgdir = self.build_directory / pkg
            if pkgdir.exists(follow_symlinks=False):
                rmtree(pkgdir)
        for fsobj in filter(
            lambda d: any([d.name.startswith(pkg) for pkg in self._packages]),
            self.src_directory.iterdir(),
        ):
            if fsobj.is_dir(follow_symlinks=False):
                fsobj.rmdir()
            else:
                fsobj.unlink()
        for pkgdir in filter(
            lambda d: d.is_dir(follow_symlinks=False), self.build_directory.iterdir()
        ):
            _get_install_func(ToolchainComponent(pkgdir.name))(self)
        return None

    def download(self: Self):
        with ThreadPool(processes=MAKEJOBS) as pool:
            results = pool.map_async(
                lambda pkg: _get_download_func(pkg)(self), self._packages
            )
            results.wait()
        return None


def _parse_package_list(args: Namespace) -> list[ToolchainComponent]:
    commasep: str = args.rebuild_package
    packages = [ToolchainComponent(x) for x in commasep.split(",")]
    if ToolchainComponent.ALL in packages:
        return list(
            filter(
                # Limine is handled separately when building the kernel
                lambda component: component != ToolchainComponent.LIMINE,
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
            Arch.coerce_from(args.architecure),
            args.with_target_arch,
            args.with_target_tune,
        )
        builder.prepare()
        builder.download()
    except Exception as e:
        perror(f"failed to build kernel toolchain: {e}")
        return 1
    return 0


# _add_gcc_config_opts(
#     [
#         "--with-arch=" + cmdline_args.with_target_arch.strip(),
#         "--with-tune=" + cmdline_args.with_target_tune.strip(),
#     ]
# )
# _set_qemu_cpu(cmdline_args.with_target_arch.strip())
