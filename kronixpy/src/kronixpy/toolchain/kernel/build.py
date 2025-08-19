from ... import Namespace, Arch, common_main
from ...utils.errprint import *
from ...utils.mutex import RMutex
from ...utils import *
from .. import ToolchainComponent, MAKEJOBS, MAKEFLAGS, MAKELOAD, BuildAction
from pathlib import Path
from shutil import rmtree
from typing import Self, Callable, Iterable, TypeVar, Unpack, Any, ClassVar
from functools import cached_property, cache as cached, lru_cache as lru_cached
from multiprocessing.pool import ThreadPool
import subprocess


type _DownloadFunc = None
type _BuildFunc = None
type _InstallFunc = Callable[[KernelToolchainBuilder], None]


class Step:
    def __init__(
        self: Self, step_fn: Callable, pkg: ToolchainComponent, action: BuildAction
    ):
        self._fn = step_fn
        self._pkg = pkg
        self._action = action
        return None

    def __call__(self: Self, *args, **kwargs) -> Any:
        pinfo(f"STEP - {self._action.action(self._pkg)}")
        result = None
        try:
            pinfo(f"STEP - {self._action.processing(self._pkg)}", end=None)
            result = self._fn(
                target=self._target(),
                *args,
                **kwargs,
                __run_fn=lambda xs: self._run(xs),
            )
        except:
            pwarning(f"Binutils not configured: {traceback.format_exc()}")
            prompt = input("Continue without binutils build? [y/N]: ")
            if prompt.lower() == "y":
                return None
            else:
                perror("Aborting")
                sys.exit(1)
            pass
        pinfo("Configured binutils")
        return result

    def _run(self: Self, cmd: list[str]):
        with subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            bufsize=1,
            universal_newlines=True,
        ) as p:
            for line in p.stdout or []:
                print(line, end="")  # process line here
            if p.returncode != 0:
                raise subprocess.CalledProcessError(p.returncode, p.args)
        return None


def make(
    target: str,
    /,
    __run_fn: Callable[[list[str]], Any],
    path: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
) -> Any:
    with save_env(env=env, path=path) as savedenv:
        return __run_fn(["make", *MAKEFLAGS, target])
    return unreachable()


@lru_cached(typed=True)
def _get_download_func(pkg: ToolchainComponent) -> _DownloadFunc:
    pass


@lru_cached(typed=True)
def _get_build_func(pkg: ToolchainComponent) -> _BuildFunc:
    pass


@lru_cached(typed=True)
def _get_install_func(pkg: ToolchainComponent) -> _InstallFunc:
    def _install_func(builder: KernelToolchainBuilder):
        with save_env(path=(builder.build_directory / pkg)) as pathsave:
            match pkg:
                case ToolchainComponent.BINUTILS:
                    return todo()
                case ToolchainComponent.NASM:
                    return todo()
                case ToolchainComponent.GCC:
                    return todo()
                case ToolchainComponent.GDB:
                    return todo()
                case ToolchainComponent.QEMU:
                    return todo()
                case ToolchainComponent.LIMINE:
                    return todo()
                case other:
                    raise ValueError(f"unknown value `{other}`")

    return _install_func


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
            # TODO: maybe `-ffreestanding` and/or `-fno-stdlib` or similar
            "CFLAGS_FOR_TARGET": "-O2 -g -mno-red-zone -mcmodel=kernel -frecord-gcc-switches",
            "CXXFLAGS_FOR_TARGET": "-O2 -g -mno-red-zone -mcmodel=kernel -frecord-gcc-switches",
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
    def cxx_flags(self: Self) -> str:
        return self._env["CXXFLAGS_FOR_TARGET"]

    @cxx_flags.setter
    def cxx_flags(self: Self, flags: Iterable[str]):
        self._env["CXXFLAGS_FOR_TARGET"] = " ".join(flags)
        return None

    @property
    def c_flags(self: Self) -> str:
        return self._env["CFLAGS_FOR_TARGET"]

    @c_flags.setter
    def c_flags(self: Self, flags: Iterable[str]):
        self._env["CFLAGS_FOR_TARGET"] = " ".join(flags)
        return None

    def prepare(self: Self):
        rmtree(self.install_directory, ignore_errors=True)
        self.install_directory.mkdir(parents=True, exist_ok=False)
        for pkg in self._packages:
            pkgdir = self.build_directory / pkg
            if pkgdir.exists(follow_symlinks=False):
                rmtree(pkgdir)
        for pkgdir in filter(
            lambda d: d.is_dir(follow_symlinks=False), self.build_directory.iterdir()
        ):
            _get_install_func(ToolchainComponent(pkgdir.name))(self)
        return None

    def download(self: Self):
        threads = ThreadPool()
        for pkg in self._packages:
            pass


def _parse_package_list(args: Namespace) -> list[ToolchainComponent]:
    commasep: str = args.rebuild_package
    packages = [ToolchainComponent(x) for x in commasep.split(",")]
    if ToolchainComponent.ALL in packages:
        return list(iter(ToolchainComponent))
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
    except Exception as e:
        perror(f"failed to build kernel toolchain: {e}")
        return 1
    return 0
