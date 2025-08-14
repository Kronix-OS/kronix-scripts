from bs4 import BeautifulSoup
import json
from string import Template
import tarfile
import requests
import ftplib
import re
import subprocess
import traceback
import os
import shutil
import argparse
import inspect
from typing import Optional, Any, Callable
import gnupg
import functools
import time
import semver
import download
from .errprint import (
    pdebug,
    pinfo,
    pwarning,
    perror,
    get_debug_mode,
    set_debug_mode,
    set_logfile,
    get_logfile,
)


def _joinpaths(*args: str) -> str:
    return os.path.realpath(os.path.join(*args))


def timefunc(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        pinfo(
            f"{func.__name__} took {time.strftime('%H:%M:%S', time.gmtime(end - start))}"
        )
        return result

    return wrapper


set_logfile(
    _joinpaths(os.getcwd(), os.path.splitext(os.path.basename(__file__))[0] + ".log")
)
_usrhomedir = os.environ.get("HOME", "/root")
gpg = gnupg.GPG(gnupghome=_joinpaths(_usrhomedir, ".gnupg"))

MAKEJOBS: int = (os.cpu_count() or 1) + 1
MAKELOAD: float = MAKEJOBS * 0.75
MAKEFLAGS: list[str] = [f"--jobs={MAKEJOBS}", f"--load-average={MAKELOAD}"]
pinfo(f"MAKEFLAGS: {MAKEFLAGS}")


def _is_child_path(parent: str, child: str) -> bool:
    return os.path.commonpath([parent, child]) == parent


TOOLCHAIN_ROOT: Optional[str] = None


def get_toolchain_dir(dir: Optional[str] = None) -> str:
    assert TOOLCHAIN_ROOT is not None
    if dir is None:
        return TOOLCHAIN_ROOT
    else:
        return _joinpaths(TOOLCHAIN_ROOT, dir)


def _rm_toolchain_dirs(exclude: list[str] | None = None) -> None:
    pinfo("Recreating toolchain directories")

    def _rmnonexistent(exclude: list[str]) -> list[str]:
        return [ex for ex in exclude if os.path.exists(ex)]

    def _rmredundant(excl: list[str]) -> list[str]:
        cpy: list[str] = []
        for ex in excl:
            if os.path.isdir(ex):
                cpy.append(ex)
                excl.remove(ex)
        for ex in excl:
            dirn = os.path.dirname(ex)
            add: bool = True
            for c in cpy:
                if _is_child_path(c, dirn):
                    add = False
                    break
            if add:
                cpy.append(dirn)
        return cpy

    real_exclude: list[str] = exclude if exclude is not None else []
    real_exclude = _rmnonexistent(real_exclude)
    real_exclude = _rmredundant(real_exclude)

    def _not_excluded(path: str) -> bool:
        for ex in real_exclude:
            if _is_child_path(ex, path):
                return False
        return True

    for dirent in os.scandir(get_toolchain_dir()):
        if dirent.is_dir() and _not_excluded(dirent.path):
            pinfo(f"Removing {dirent.path}")
            shutil.rmtree(dirent.path)
        elif _not_excluded(dirent.path):
            pinfo(f"Removing {dirent.path}")
            os.remove(dirent.path)
    create_dir(get_toolchain_dir("src"))
    create_dir(get_toolchain_dir("build"))
    create_dir(get_toolchain_dir("install"))
    return None


def sort_versions(versions: list[str]) -> list[str]:
    return semver.sort(versions, reverse=True)


def get_latest_version_from_http(http_url: str, pkgname: str) -> str:
    REG_VERSION_STRING = r"(%s-)?\d+\.\d+.*" % pkgname
    pdebug(f"REG_VERSION_STRING: {REG_VERSION_STRING}")
    REG_VERSION = re.compile(REG_VERSION_STRING)
    pinfo(f"Gathering {pkgname} versions from {http_url}")
    response = requests.get(http_url)
    soup = BeautifulSoup(response.text, "html.parser")
    links = soup.find_all("a")
    versions = [
        link.get("href") for link in links if REG_VERSION.match(link.get("href"))
    ]
    cpy: list[str] = []
    for version in versions:
        if version.startswith(pkgname):
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
    versions = sort_versions(versions)
    if get_debug_mode():
        with open(_joinpaths(os.getcwd(), f"sorted_{pkgname}_versions.txt"), "w") as f:
            pdebug(f"Dumping versions to file {f.name}")
            todump = "\n".join(versions)
            f.write(todump)
            f.flush()
            f.close()
    return versions[0]


def get_latest_version_from_ftp(ftp_url: str, pkgname: str) -> str:
    REG_VERSION_STRING = r"(%s-)?\d+\.\d+.*" % pkgname
    pdebug(f"REG_VERSION_STRING: {REG_VERSION_STRING}")
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
    pinfo(f"Gathering {pkgname} versions from {actual_ftp}/{directory}")
    with ftplib.FTP(actual_ftp) as ftp:
        ftp.login()
        ftp.cwd(directory)
        versions = ftp.nlst()
        versions = [version for version in versions if REG_VERSION.match(version)]
        cpy: list[str] = []
        for version in versions:
            if version.startswith(pkgname):
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
        versions = sort_versions(versions)
        if get_debug_mode():
            with open(
                _joinpaths(os.getcwd(), f"sorted_{pkgname}_versions.txt"), "w"
            ) as f:
                pdebug(f"Dumping versions to file {f.name}")
                todump = "\n".join(versions)
                f.write(todump)
                f.flush()
                f.close()
    return versions[0]


def get_latest_version(base_url: str, package_name: str) -> str:
    if base_url.startswith("http"):
        return get_latest_version_from_http(base_url, package_name)
    elif base_url.startswith("ftp"):
        return get_latest_version_from_ftp(base_url, package_name)
    else:
        raise ValueError("Unknown protocol")


GNU_MIRROR: str = "ftp://ftp.gnu.org/gnu/"

GCC_BASE_URL: str = f"{GNU_MIRROR}/gcc/"
GCC_VERSION: str = ""

QEMU_TARGETS: list[str] = [
    "x86_64-softmmu",
    "i386-softmmu",
    "aarch64-softmmu",
    "arm-softmmu",
    "riscv64-softmmu",
    "riscv32-softmmu",
]

gcc_additional_config: list[str] = []

__pkgconfigpath = os.environ.get("PKG_CONFIG_PATH")

ENV_VARS: dict[str, str] = {
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
    "CFLAGS_FOR_TARGET": "-O2 -g -mno-red-zone -mcmodel=kernel -frecord-gcc-switches",
    "CXXFLAGS_FOR_TARGET": "-O2 -g -mno-red-zone -mcmodel=kernel -frecord-gcc-switches",
    "TARGET": "x86_64-elf",
    "PREFIX": get_toolchain_dir("install"),
    "PATH": _joinpaths(get_toolchain_dir("install"), "bin")
    + ":"
    + os.environ.get(
        "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    ),
    "PKG_CONFIG_PATH": "/usr/local/lib/pkgconfig/:/usr/local/lib64/pkgconfig/"
    + (":" if __pkgconfigpath is not None else "")
    + (__pkgconfigpath or ""),
}


def _add_gcc_config_opts(flags: list[str]) -> None:
    global gcc_additional_config
    pdebug(f"Adding GCC config options: {' '.join(flags)}")
    gcc_additional_config += flags
    return None


def _add_cfamily_flags_for_target(flags: list[str]) -> None:
    global ENV_VARS
    custom_flags: str = " ".join(flags)
    pdebug(f"Adding C-family flags for target: {custom_flags}")
    ENV_VARS["CFLAGS_FOR_TARGET"] += " " + custom_flags
    ENV_VARS["CXXFLAGS_FOR_TARGET"] += " " + custom_flags
    return None


def download_gcc(base_url: str, version: str, destdir: str, destarchive: str) -> None:
    for dirent in os.scandir(destdir):
        if os.path.basename(dirent.path).startswith("gcc"):
            pinfo(f"Removing {dirent.path}")
            if dirent.is_dir():
                shutil.rmtree(dirent.path)
            else:
                os.remove(dirent.path)
    destination: str = _joinpaths(destdir, destarchive)
    if base_url.startswith("http"):
        pinfo(
            f"Downloading {base_url}/gcc-{version}/gcc-{version}.tar.xz to {destination}"
        )
        tarbytes = download.from_http(f"{base_url}/gcc-{version}/gcc-{version}.tar.xz")
        pinfo(
            f"Downloading {base_url}/gcc-{version}/gcc-{version}.tar.xz.sig to {destination}.sig"
        )
        sigbytes = download.from_http(
            f"{base_url}/gcc-{version}/gcc-{version}.tar.xz.sig"
        )
    elif base_url.startswith("ftp"):
        pinfo(
            f"Downloading {base_url}/gcc-{version}/gcc-{version}.tar.xz to {destination}"
        )
        tarbytes = download.from_ftp(f"{base_url}/gcc-{version}/gcc-{version}.tar.xz")
        pinfo(
            f"Downloading {base_url}/gcc-{version}/gcc-{version}.tar.xz.sig to {destination}.sig"
        )
        sigbytes = download.from_ftp(
            f"{base_url}/gcc-{version}/gcc-{version}.tar.xz.sig"
        )
    else:
        raise ValueError("Unknown protocol")
    with open(destination, "wb") as f:
        f.write(tarbytes)
        f.flush()
        f.close()
    pinfo(f"Downloaded {destination}")
    with open(f"{destination}.sig", "wb") as f:
        f.write(sigbytes)
        f.flush()
        f.close()
    pinfo(f"Downloaded {destination}.sig")
    pinfo(f"Verifying {destination}")
    global gpg
    with open(f"{destination}.sig", "rb") as f:
        verified = gpg.verify_file(f, destination)
        if not verified:
            pdebug(f"Signature verification: {verified}")
            raise ValueError(f"Signature verification failed for {destination}")
    pinfo(f"Verified {destination}")
    return None


def download_gdb(base_url: str, version: str, destdir: str, destarchive: str) -> None:
    if version == "latest":
        version = get_latest_version(base_url + "/gdb/", "gdb")
        pdebug(f"Latest gdb version: {version}")
    for dirent in os.scandir(destdir):
        if os.path.basename(dirent.path).startswith("gdb"):
            pinfo(f"Removing {dirent.path}")
            if dirent.is_dir():
                shutil.rmtree(dirent.path)
            else:
                os.remove(dirent.path)
    destination: str = _joinpaths(destdir, destarchive)
    if base_url.startswith("http"):
        pinfo(f"Downloading {base_url}/gdb/gdb-{version}.tar.xz to {destination}")
        tarbytes = download.from_http(f"{base_url}/gdb/gdb-{version}.tar.xz")
        pinfo(
            f"Downloading {base_url}/gdb/gdb-{version}.tar.xz.sig to {destination}.sig"
        )
        sigbytes = download.from_http(f"{base_url}/gdb/gdb-{version}.tar.xz.sig")
    elif base_url.startswith("ftp"):
        pinfo(f"Downloading {base_url}/gdb/gdb-{version}.tar.xz to {destination}")
        tarbytes = download.from_ftp(f"{base_url}/gdb/gdb-{version}.tar.xz")
        pinfo(
            f"Downloading {base_url}/gdb/gdb-{version}.tar.xz.sig to {destination}.sig"
        )
        sigbytes = download.from_ftp(f"{base_url}/gdb/gdb-{version}.tar.xz.sig")
    else:
        raise ValueError("Unknown protocol")
    with open(destination, "wb") as f:
        f.write(tarbytes)
        f.flush()
        f.close()
    pinfo(f"Downloaded {destination}")
    with open(f"{destination}.sig", "wb") as f:
        f.write(sigbytes)
        f.flush()
        f.close()
    pinfo(f"Downloaded {destination}.sig")
    pinfo(f"Verifying {destination}")
    global gpg
    with open(f"{destination}.sig", "rb") as f:
        verified = gpg.verify_file(f, destination)
        if not verified:
            pdebug(f"Signature verification: {verified}")
            raise ValueError(f"Signature verification failed for {destination}")
    pinfo(f"Verified {destination}")
    return None


def download_binutils(
    base_url: str, version: str, destdir: str, destarchive: str
) -> None:
    if version == "latest":
        version = get_latest_version(base_url + "/binutils/", "binutils")
        pdebug(f"Latest binutils version: {version}")
    for dirent in os.scandir(destdir):
        if os.path.basename(dirent.path).startswith("binutils"):
            pinfo(f"Removing {dirent.path}")
            if dirent.is_dir():
                shutil.rmtree(dirent.path)
            else:
                os.remove(dirent.path)
    destination: str = _joinpaths(destdir, destarchive)
    if base_url.startswith("http"):
        pinfo(
            f"Downloading {base_url}/binutils/binutils-{version}.tar.xz to {destination}"
        )
        tarbytes = download.from_http(f"{base_url}/binutils/binutils-{version}.tar.xz")
        pinfo(
            f"Downloading {base_url}/binutils/binutils-{version}.tar.xz.sig to {destination}.sig"
        )
        sigbytes = download.from_http(
            f"{base_url}/binutils/binutils-{version}.tar.xz.sig"
        )
    elif base_url.startswith("ftp"):
        pinfo(
            f"Downloading {base_url}/binutils/binutils-{version}.tar.xz to {destination}"
        )
        tarbytes = download.from_ftp(f"{base_url}/binutils/binutils-{version}.tar.xz")
        pinfo(
            f"Downloading {base_url}/binutils/binutils-{version}.tar.xz.sig to {destination}.sig"
        )
        sigbytes = download.from_ftp(
            f"{base_url}/binutils/binutils-{version}.tar.xz.sig"
        )
    else:
        raise ValueError("Unknown protocol")
    with open(destination, "wb") as f:
        f.write(tarbytes)
        f.flush()
        f.close()
    pinfo(f"Downloaded {destination}")
    with open(f"{destination}.sig", "wb") as f:
        f.write(sigbytes)
        f.flush()
        f.close()
    pinfo(f"Downloaded {destination}.sig")
    pinfo(f"Verifying {destination}")
    global gpg
    with open(f"{destination}.sig", "rb") as f:
        verified = gpg.verify_file(f, destination)
        if not verified:
            pdebug(f"Signature verification: {verified}")
            raise ValueError(f"Signature verification failed for {destination}")
    pinfo(f"Verified {destination}")
    return None


def untar_archive(archive: str) -> str:
    src: str
    curr_dir = os.getcwd()
    os.chdir(os.path.dirname(archive))
    with tarfile.open(archive, "r") as tar:
        src = _joinpaths(os.path.dirname(archive), tar.getnames()[0].split("/")[0])
        pinfo(f"Extracting {archive} to {src}")
        tar.extractall()
    os.chdir(curr_dir)
    os.sync()
    pinfo(f"Finished extracting {archive}")
    return src


class DeferredWrapper:
    def __init__(self, func: Callable[..., Any], *args, **kwargs) -> None:
        self.func = func
        self.args = args
        self.kwargs = kwargs
        return None

    def __call__(self) -> Any:
        def _expand_deferred(obj: Any) -> Any:
            if is_deferred(obj):
                return obj()
            return obj

        self.args = [_expand_deferred(arg) for arg in self.args]
        self.kwargs = {
            key: _expand_deferred(value) for key, value in self.kwargs.items()
        }
        return self.func(*self.args, **self.kwargs)


def is_deferred(obj: Any) -> bool:
    return isinstance(obj, DeferredWrapper)


def is_lambda(func: Any) -> bool:
    return hasattr(func, "__name__") and func.__name__ == "<lambda>"


def parse_cmdline() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and extract GCC sources")
    parser.add_argument(
        "--target-architecture",
        type=str,
        default="x86_64-elf",
        help="Target architecture",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument(
        "--gnu-mirror", type=str, default=GNU_MIRROR, help="GNU mirror URL"
    )
    parser.add_argument(
        "--gcc-version",
        type=str,
        default=DeferredWrapper(get_latest_version, GCC_BASE_URL, "gcc"),
        help="Version of GCC to download",
    )
    parser.add_argument(
        "--rebuild-package",
        type=str,
        default="all",
        help="Rebuild only the specified comma-separated packages",
    )  # For ex. : --rebuild-package=gcc,binutils,gdb
    parser.add_argument(
        "--with-target-arch", type=str, default="alderlake", help="Target architecture"
    )
    parser.add_argument(
        "--with-target-tune", type=str, default="alderlake", help="Target tune"
    )
    return parser.parse_args()


def create_dir(path: str, eok: bool = True) -> None:
    pinfo(f"Creating directory {path}")
    os.makedirs(path, exist_ok=eok)
    return None


def gcc_prerequisites(extracted_dir: str) -> None:
    pinfo("Checking prerequisites")
    try:
        subprocess.run(
            ["./contrib/download_prerequisites"], cwd=extracted_dir, check=True
        )
    except subprocess.CalledProcessError as e:
        pwarning(f"{str(e)}: prerequisites not installed")
    pinfo("Prerequisites installed")
    return None


def _build_env_list(package: str) -> list[str]:
    def _rm_mcmodel(envstr: str) -> str:
        return (
            envstr.replace("-mcmodel=kernel", "")
            if package.lower() == "limine"
            else envstr
        )

    env: list[str] = ["/usr/bin/env"]
    pdebug(f"{package} environment:")
    if package.lower() == "limine":
        env.append(f"CC_FOR_TARGET={ENV_VARS['TARGET']}-gcc")
        pdebug(f"CC_FOR_TARGET={ENV_VARS['TARGET']}-gcc")
    for key, value in ENV_VARS.items():
        envstr = _rm_mcmodel(f"{key}={value}")
        env.append(envstr)
        pdebug(envstr)
    return env


def build_gcc(src: str) -> None:
    extracted_dir = src
    gcc_prerequisites(extracted_dir)
    currdir = os.getcwd()
    builddir = _joinpaths(get_toolchain_dir("build"), extracted_dir.split("/")[-1])
    create_dir(builddir)
    os.chdir(builddir)
    pinfo(f"Configuring GCC in {builddir}")
    env: list[str] = _build_env_list("GCC")

    try:
        global gcc_additional_config
        params: list[str] = [
            *env,
            _joinpaths(extracted_dir, "configure"),
            "--target=" + ENV_VARS["TARGET"],
            "--prefix=" + ENV_VARS["PREFIX"],
            "--enable-languages=c,c++",
            "--enable-decimal-float=yes",
            "--enable-fixed-point=auto",
            "--enable-lto",
            "--enable-long-long",
            "--with-long-double-128",
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
            "--disable-libstdcxx",
            "--disable-thread",
            "--with-gnu-as",
            "--with-gnu-ld",
            *gcc_additional_config,
        ]
        subprocess.run(params, check=True)
    except subprocess.CalledProcessError as e:

        pwarning(f"GCC not configured: {traceback.format_exc()}")
        prompt = input("Continue without GCC build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Configured GCC")

    if (
        ENV_VARS["CXXFLAGS_FOR_TARGET"].find("-mcmodel=kernel") != -1
        or ENV_VARS["CFLAGS_FOR_TARGET"].find("-mcmodel=kernel") != -1
    ):
        pinfo("Start first part of GCC build")
        try:
            subprocess.run(
                [
                    *env,
                    "make",
                    *MAKEFLAGS,
                    "configure-target-libgcc",
                    "CFLAGS_FOR_TARGET=" + ENV_VARS["CFLAGS_FOR_TARGET"],
                    "CXXFLAGS_FOR_TARGET=" + ENV_VARS["CXXFLAGS_FOR_TARGET"],
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:

            pwarning(f"First part of GCC build failed: {traceback.format_exc()}")
            prompt = input("Continue without building GCC? [y/N]: ")
            if prompt.lower() == "y":
                return None
            else:
                perror("Aborting")
                sys.exit(1)
        pinfo("First part of GCC build succeded")
        pinfo("Patching libgcc to enable building with -mcmodel=kernel")
        try:
            subprocess.run(
                [
                    *env,
                    "sed",
                    "-i",
                    "s/PICFLAG/DISABLED_PICFLAG/g",
                    _joinpaths(ENV_VARS["TARGET"], "libgcc", "Makefile"),
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:

            pwarning(f"libgcc not patched: {traceback.format_exc()}")
            prompt = input("Continue without patching libgcc? [y/N]: ")
            if prompt.lower() == "y":
                return None
            else:
                perror("Aborting")
                sys.exit(1)
        pinfo("Patched libgcc")
        pinfo("Start second part of GCC build")
        try:
            subprocess.run(
                [
                    *env,
                    "make",
                    *MAKEFLAGS,
                    "all",
                    "CFLAGS_FOR_TARGET=" + ENV_VARS["CFLAGS_FOR_TARGET"],
                    "CXXFLAGS_FOR_TARGET=" + ENV_VARS["CXXFLAGS_FOR_TARGET"],
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:

            pwarning(f"Second part of GCC build failed: {traceback.format_exc()}")
            prompt = input("Continue without building GCC? [y/N]: ")
            if prompt.lower() == "y":
                return None
            else:
                perror("Aborting")
                sys.exit(1)
        pinfo("Second part of GCC build succeded")
    else:
        pinfo("Building GCC")
        try:
            subprocess.run(
                [
                    *env,
                    "make",
                    *MAKEFLAGS,
                    "all",
                    "CFLAGS_FOR_TARGET=" + ENV_VARS["CFLAGS_FOR_TARGET"],
                    "CXXFLAGS_FOR_TARGET=" + ENV_VARS["CXXFLAGS_FOR_TARGET"],
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:

            pwarning(f"GCC not built: {traceback.format_exc()}")
            prompt = input("Continue without GCC build? [y/N]: ")
            if prompt.lower() == "y":
                return None
            else:
                perror("Aborting")
                sys.exit(1)
        pinfo("Built GCC")

    pinfo("Installing GCC")
    try:
        subprocess.run(
            [
                *env,
                "make",
                "install",
                "CFLAGS_FOR_TARGET=" + ENV_VARS["CFLAGS_FOR_TARGET"],
                "CXXFLAGS_FOR_TARGET=" + ENV_VARS["CXXFLAGS_FOR_TARGET"],
            ],
            check=True,
        )
    except subprocess.CalledProcessError as e:

        pwarning(f"GCC not installed: {traceback.format_exc()}")
        prompt = input("Continue without GCC build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Installed GCC")
    # if True:
    #    os.chdir(currdir)
    #    return None
    pinfo("Building libgcc")
    try:
        subprocess.run(
            [
                *env,
                "make",
                *MAKEFLAGS,
                "all-target-libgcc",
                "CFLAGS_FOR_TARGET=" + ENV_VARS["CFLAGS_FOR_TARGET"],
                "CXXFLAGS_FOR_TARGET=" + ENV_VARS["CXXFLAGS_FOR_TARGET"],
            ],
            check=True,
        )
    except subprocess.CalledProcessError as e:

        pwarning(f"libgcc not built: {traceback.format_exc()}")
        prompt = input("Continue without GCC build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Built libgcc")
    pinfo("Installing libgcc")
    try:
        subprocess.run(
            [
                *env,
                "make",
                "install-target-libgcc",
                "CFLAGS_FOR_TARGET=" + ENV_VARS["CFLAGS_FOR_TARGET"],
                "CXXFLAGS_FOR_TARGET=" + ENV_VARS["CXXFLAGS_FOR_TARGET"],
            ],
            check=True,
        )
    except subprocess.CalledProcessError as e:

        pwarning(f"libgcc not installed: {traceback.format_exc()}")
        prompt = input("Continue without GCC build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Installed libgcc")
    os.chdir(currdir)
    return None


def build_binutils(src: str) -> None:
    extracted_dir = src
    currdir = os.getcwd()
    builddir = _joinpaths(get_toolchain_dir("build"), extracted_dir.split("/")[-1])
    create_dir(builddir)
    os.chdir(builddir)
    pinfo(f"Configuring binutils in {builddir}")
    env: list[str] = _build_env_list("Binutils")
    try:
        params: list[str] = [
            *env,
            _joinpaths(extracted_dir, "configure"),
            "--target=" + ENV_VARS["TARGET"],
            "--prefix=" + ENV_VARS["PREFIX"],
            "--with-sysroot",
            "--disable-werror",
        ]
        subprocess.run(params, check=True)
    except subprocess.CalledProcessError as e:
        pwarning(f"Binutils not configured: {traceback.format_exc()}")
        prompt = input("Continue without binutils build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Configured binutils")
    pinfo("Building binutils")
    try:
        subprocess.run([*env, "make", *MAKEFLAGS], check=True)
    except subprocess.CalledProcessError as e:
        pwarning(f"Binutils not built: {traceback.format_exc()}")
        prompt = input("Continue without binutils build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Built binutils")
    pinfo("Installing binutils")
    try:
        subprocess.run([*env, "make", "install"], check=True)
    except subprocess.CalledProcessError as e:
        pwarning(f"Binutils not installed: {traceback.format_exc()}")
        prompt = input("Continue without binutils build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Installed binutils")
    os.chdir(currdir)
    return None


def build_gdb(src: str) -> None:
    extracted_dir = src
    currdir = os.getcwd()
    builddir = _joinpaths(get_toolchain_dir("build"), extracted_dir.split("/")[-1])
    create_dir(builddir)
    os.chdir(builddir)
    pinfo(f"Configuring GDB in {builddir}")
    env: list[str] = _build_env_list("GDB")
    try:
        params: list[str] = [
            *env,
            _joinpaths(extracted_dir, "configure"),
            "--target=" + ENV_VARS["TARGET"],
            "--prefix=" + ENV_VARS["PREFIX"],
            "--disable-werror",
            "--with-python=/usr/bin/python3",
            "--with-guile=guile-3.0",
        ]
        subprocess.run(params, check=True)
    except subprocess.CalledProcessError as e:
        pwarning(f"GDB not configured: {traceback.format_exc()}")
        prompt = input("Continue without GDB build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Configured GDB")
    pinfo("Building GDB")
    try:
        subprocess.run([*env, "make", *MAKEFLAGS], check=True)
    except subprocess.CalledProcessError as e:
        pwarning(f"GDB not built: {traceback.format_exc()}")
        prompt = input("Continue without GDB build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Built GDB")
    pinfo("Installing GDB")
    try:
        subprocess.run([*env, "make", "install"], check=True)
    except subprocess.CalledProcessError as e:
        pwarning(f"GDB not installed: {traceback.format_exc()}")
        prompt = input("Continue without GDB build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Installed GDB")
    os.chdir(currdir)
    return None


def download_limine(destdir: str, destarchive: str) -> None:
    temp = Template(
        """https://github.com/limine-bootloader/limine/releases/download/v${version}/limine-${version}.tar.xz"""
    )
    LIMINE_REPO = "limine-bootloader/limine"
    LIMINE_API = f"https://api.github.com/repos/{LIMINE_REPO}/releases/latest"
    pinfo(f"Getting latest release from {LIMINE_API}")
    response = requests.get(LIMINE_API)
    if response.status_code != 200:
        raise ValueError(f"Failed to get latest release from {LIMINE_API}")
    release = json.loads(response.text)
    version: str = release["tag_name"]
    if version.startswith("v"):
        version = version[1:]
    pinfo(f"Latest Limine release: {version}")
    for dirent in os.scandir(destdir):
        if os.path.basename(dirent.path).startswith("limine"):
            pinfo(f"Removing {dirent.path}")
            if dirent.is_dir():
                shutil.rmtree(dirent.path)
            else:
                os.remove(dirent.path)
    destination: str = _joinpaths(destdir, destarchive)
    pinfo(f"Downloading Limine {version} to {destination}")
    tarbytes = download.from_http(temp.substitute(version=version))
    with open(destination, "wb") as f:
        f.write(tarbytes)
        f.flush()
        f.close()
    pinfo(f"Downloaded {destination}")
    return None


def build_limine(src: str) -> None:
    extracted_dir = src
    currdir = os.getcwd()
    builddir = _joinpaths(get_toolchain_dir("build"), extracted_dir.split("/")[-1])
    create_dir(builddir)
    os.chdir(builddir)
    pinfo(f"Configuring Limine in {builddir}")
    env: list[str] = _build_env_list("Limine")
    try:
        params: list[str] = [
            *env,
            _joinpaths(extracted_dir, "configure"),
            "--prefix=" + ENV_VARS["PREFIX"],
            "--enable-uefi-x86-64",
            "--enable-uefi-ia32",
            "--enable-bios-cd",
            "--enable-bios-pxe",
            "--enable-bios",
            "--enable-uefi-ia32",
            "--enable-uefi-x86-64",
            "--enable-uefi-cd",
            "--enable-lto",
            "STRIP=strip",
        ]
        subprocess.run(params, check=True)
    except subprocess.CalledProcessError as e:
        pwarning(f"Limine not configured: {traceback.format_exc()}")
        prompt = input("Continue without Limine build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Configured Limine")
    pinfo("Building Limine")
    try:
        subprocess.run([*env, "make", *MAKEFLAGS], check=True)
    except subprocess.CalledProcessError as e:
        pwarning(f"Limine not built: {traceback.format_exc()}")
        prompt = input("Continue without Limine build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Built Limine")
    pinfo("Installing Limine")
    try:
        subprocess.run([*env, "make", "install"], check=True)
    except subprocess.CalledProcessError as e:
        pwarning(f"Limine not installed: {traceback.format_exc()}")
        prompt = input("Continue without Limine build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Installed Limine")
    os.chdir(currdir)
    return None


def download_qemu(destdir: str, destarchive: str) -> None:
    QEMU_LINK = "https://download.qemu.org/qemu-9.1.0.tar.xz"
    QEMU_SIG = "https://download.qemu.org/qemu-9.1.0.tar.xz.sig"
    destination: str = _joinpaths(destdir, destarchive)
    pinfo(f"Downloading {QEMU_LINK} to {destination}")
    tarbytes = download.from_http(QEMU_LINK)
    pinfo(f"Downloading {QEMU_SIG} to {destination}.sig")
    sigbytes = download.from_http(QEMU_SIG)
    with open(destination, "wb") as f:
        f.write(tarbytes)
        f.flush()
        f.close()
    pinfo(f"Downloaded {destination}")
    with open(f"{destination}.sig", "wb") as f:
        f.write(sigbytes)
        f.flush()
        f.close()
    pinfo(f"Downloaded {destination}.sig")
    pinfo(f"Verifying {destination}")
    global gpg
    with open(f"{destination}.sig", "rb") as f:
        verified = gpg.verify_file(f, destination)
        if not verified:
            pdebug(f"Signature verification: {verified}")
            raise ValueError(f"Signature verification failed for {destination}")
    pinfo(f"Verified {destination}")
    return None


def download_nasm(destdir: str, destarchive: str) -> None:
    LATEST_VERSION = get_latest_version(
        "https://www.nasm.us/pub/nasm/releasebuilds/", "nasm"
    )
    NASM_LINK = f"https://www.nasm.us/pub/nasm/releasebuilds/{LATEST_VERSION}/nasm-{LATEST_VERSION}.tar.xz"
    destination: str = _joinpaths(destdir, destarchive)
    pinfo(f"Downloading {NASM_LINK} to {destination}")
    tarbytes = download.from_http(NASM_LINK)
    with open(destination, "wb") as f:
        f.write(tarbytes)
        f.flush()
        f.close()
    pinfo(f"Downloaded {destination}")
    return None


def build_nasm(src: str) -> None:
    extracted_dir = src
    currdir = os.getcwd()
    builddir = _joinpaths(get_toolchain_dir("build"), extracted_dir.split("/")[-1])
    create_dir(builddir)
    os.chdir(builddir)
    pinfo(f"Configuring nasm in {builddir}")
    env: list[str] = _build_env_list("NASM")
    try:
        params: list[str] = [
            *env,
            _joinpaths(extracted_dir, "configure"),
            "--prefix=" + ENV_VARS["PREFIX"],
            "--enable-lto",
        ]
        subprocess.run(params, check=True)
    except subprocess.CalledProcessError as e:
        pwarning(f"nasm not configured: {traceback.format_exc()}")
        prompt = input("Continue without nasm build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Configured nasm")
    pinfo("Building nasm")
    try:
        subprocess.run(
            [
                *env,
                "make",
                *MAKEFLAGS,
                f"RANLIB={ENV_VARS['RANLIB']}",
                f"AR={ENV_VARS['AR']}",
            ],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        pwarning(f"nasm not built: {traceback.format_exc()}")
        prompt = input("Continue without nasm build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Built nasm")
    pinfo("Installing nasm")
    try:
        subprocess.run(
            [
                *env,
                "make",
                "install",
                f"RANLIB={ENV_VARS['RANLIB']}",
                f"AR={ENV_VARS['AR']}",
            ],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        pwarning(f"nasm not installed: {traceback.format_exc()}")
        prompt = input("Continue without nasm build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Installed nasm")
    os.chdir(currdir)
    return None


QEMU_CPU: str = "x86_64"


def _set_qemu_cpu(cpu: str) -> None:
    global QEMU_CPU
    QEMU_CPU = cpu
    return None


def _get_qemu_cpu() -> str:
    global QEMU_CPU
    return QEMU_CPU


def build_qemu(src: str) -> None:
    extracted_dir = src
    currdir = os.getcwd()
    builddir = _joinpaths(get_toolchain_dir("build"), extracted_dir.split("/")[-1])
    create_dir(builddir)
    os.chdir(builddir)
    pinfo(f"Configuring QEMU in {builddir}")
    env: list[str] = _build_env_list("QEMU")
    try:
        global QEMU_TARGETS
        pdebug(f"QEMU build for host CPU: {_get_qemu_cpu()}")
        target_list: str = ",".join(QEMU_TARGETS)
        qemu_config_flags: list[str] = [
            # Prefer Makefiles over Ninja
            # TODO: "-Dsomething=Makefiles",
            # "--extra-cflags=-march=%s -mtune=%s" % (_get_qemu_cpu(), _get_qemu_cpu()),
            # "--extra-cxxflags=-march=%s -mtune=%s" % (_get_qemu_cpu(), _get_qemu_cpu()),
            "--prefix=" + ENV_VARS["PREFIX"],
            f"--target-list={target_list}",
            # "--static",
            "--python=/usr/bin/python3",
            # f"--cpu={_get_qemu_cpu()}",
            f"--gdb={_joinpaths(get_toolchain_dir("install"), 'bin', ENV_VARS['TARGET'])}-gdb",
            "--enable-lto",
            "--enable-strip",
            # "--enable-system",
            "--enable-virtfs",
            "--enable-plugins",
            # "--disable-xkbcommon"
        ]
        params: list[str] = [
            *env,
            _joinpaths(extracted_dir, "configure"),
            *qemu_config_flags,
        ]
        subprocess.run(params, check=True)
    except subprocess.CalledProcessError as e:
        pwarning(f"QEMU not configured: {traceback.format_exc()}")
        prompt = input("Continue without QEMU build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Configured QEMU")
    pinfo("Building QEMU")
    try:
        subprocess.run([*env, "make", *MAKEFLAGS], check=True)
    except subprocess.CalledProcessError as e:
        pwarning(f"QEMU not built: {traceback.format_exc()}")
        prompt = input("Continue without QEMU build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Built QEMU")
    pinfo("Installing QEMU")
    try:
        subprocess.run([*env, "make", "install"], check=True)
    except subprocess.CalledProcessError as e:
        pwarning(f"QEMU not installed: {traceback.format_exc()}")
        prompt = input("Continue without QEMU build? [y/N]: ")
        if prompt.lower() == "y":
            return None
        else:
            perror("Aborting")
            sys.exit(1)
    pinfo("Installed QEMU")
    os.chdir(currdir)
    return None


def is_function_passed(func: Any) -> bool:
    """
    Check if a function contains the `pass` statement
    """
    lines, _ = inspect.getsourcelines(func)
    lines = [line.strip() for line in lines]
    return "pass" in lines


def make_buildproc(to_rebuild: list[str]) -> list[DeferredWrapper]:
    def _get_download_func(package: str) -> DeferredWrapper:
        if package.lower() == "binutils":
            return DeferredWrapper(
                download_binutils,
                GNU_MIRROR,
                "latest",
                get_toolchain_dir("src"),
                "binutils.tar.xz",
            )
        elif package.lower() == "gcc":
            return DeferredWrapper(
                download_gcc,
                GCC_BASE_URL,
                DeferredWrapper(lambda: GCC_VERSION),
                get_toolchain_dir("src"),
                "gcc.tar.xz",
            )
        elif package.lower() == "nasm":
            return DeferredWrapper(
                download_nasm, get_toolchain_dir("src"), "nasm.tar.xz"
            )
        elif package.lower() == "gdb":
            return DeferredWrapper(
                download_gdb,
                GNU_MIRROR,
                "latest",
                get_toolchain_dir("src"),
                "gdb.tar.xz",
            )
        elif package.lower() == "limine":
            return DeferredWrapper(
                download_limine, get_toolchain_dir("src"), "limine.tar.xz"
            )
        elif package.lower() == "qemu":
            return DeferredWrapper(
                download_qemu, get_toolchain_dir("src"), "qemu.tar.xz"
            )
        else:
            raise ValueError("Unknown package")

    def _get_untar_func(package: str) -> DeferredWrapper:
        if package.lower() == "binutils":
            return DeferredWrapper(
                untar_archive, _joinpaths(get_toolchain_dir("src"), "binutils.tar.xz")
            )
        elif package.lower() == "gcc":
            return DeferredWrapper(
                untar_archive, _joinpaths(get_toolchain_dir("src"), "gcc.tar.xz")
            )
        elif package.lower() == "nasm":
            return DeferredWrapper(
                untar_archive, _joinpaths(get_toolchain_dir("src"), "nasm.tar.xz")
            )
        elif package.lower() == "gdb":
            return DeferredWrapper(
                untar_archive, _joinpaths(get_toolchain_dir("src"), "gdb.tar.xz")
            )
        elif package.lower() == "limine":
            return DeferredWrapper(
                untar_archive, _joinpaths(get_toolchain_dir("src"), "limine.tar.xz")
            )
        elif package.lower() == "qemu":
            return DeferredWrapper(
                untar_archive, _joinpaths(get_toolchain_dir("src"), "qemu.tar.xz")
            )
        else:
            raise ValueError("Unknown package")

    def _get_build_func(package: str, src: DeferredWrapper) -> DeferredWrapper:
        if package.lower() == "binutils":
            return DeferredWrapper(build_binutils, src)
        elif package.lower() == "gcc":
            return DeferredWrapper(build_gcc, src)
        elif package.lower() == "nasm":
            return DeferredWrapper(build_nasm, src)
        elif package.lower() == "gdb":
            return DeferredWrapper(build_gdb, src)
        elif package.lower() == "limine":
            return DeferredWrapper(build_limine, src)
        elif package.lower() == "qemu":
            return DeferredWrapper(build_qemu, src)
        else:
            raise ValueError("Unknown package")

    ordered_packages: list[str] = ["binutils", "gcc", "nasm", "gdb", "limine", "qemu"]
    buildproc: list[DeferredWrapper] = []
    real_to_rebuild: list[str] = ordered_packages if ("all" in to_rebuild) else []
    real_not_to_rebuild: list[str] = []
    if len(real_to_rebuild) == 0:
        for package in ordered_packages:
            if package in to_rebuild:
                real_to_rebuild.append(package)
            else:
                real_not_to_rebuild.append(package)
    excluded_dirs: list[str] = []
    for package in real_to_rebuild:
        excluded_dirs.append(_joinpaths(get_toolchain_dir("src"), f"{package}.tar.xz"))
    if len(real_not_to_rebuild) != 0:
        excluded_dirs.append(get_toolchain_dir("install"))
    buildproc.append(DeferredWrapper(_rm_toolchain_dirs, excluded_dirs))
    for package in real_to_rebuild:
        buildproc.append(_get_download_func(package))
    for package in real_to_rebuild:
        buildproc.append(_get_build_func(package, _get_untar_func(package)))
    if get_debug_mode():
        for package in real_to_rebuild:
            pdebug(f"Package {package} will be rebuilt")
    return buildproc


@timefunc
def main() -> int:
    def ck_is_gcc_version(value: str) -> str:
        if not re.match(r"^\d+\.\d+\.\d+$", value):
            raise argparse.ArgumentTypeError("Version must be in the form X.Y.Z")
        return value

    def format_rebuilt_packages(value: str) -> list[str]:
        return value.split(",")

    try:
        global GCC_VERSION
        global ENV_VARS

        cmdline_args = parse_cmdline()
        set_debug_mode(cmdline_args.debug)
        pdebug("Debug mode enabled")
        if not isinstance(cmdline_args.with_target_arch, str) or not isinstance(
            cmdline_args.with_target_tune, str
        ):
            raise ValueError("Invalid target architecture or tune")
        _add_cfamily_flags_for_target(
            [
                f"-march={cmdline_args.with_target_arch.strip()}",
                f"-mtune={cmdline_args.with_target_tune.strip()}",
            ]
        )
        _add_gcc_config_opts(
            [
                "--with-arch=" + cmdline_args.with_target_arch.strip(),
                "--with-tune=" + cmdline_args.with_target_tune.strip(),
            ]
        )
        _set_qemu_cpu(cmdline_args.with_target_arch.strip())
        cmdline_args.rebuild_package = format_rebuilt_packages(
            cmdline_args.rebuild_package
        )
        cmdline_args.rebuild_package = [
            item.lower() for item in cmdline_args.rebuild_package
        ]
        pdebug(f"Rebuilding packages (unordered): {cmdline_args.rebuild_package}")
        buildproc = make_buildproc(cmdline_args.rebuild_package)
        if (
            "gcc" in cmdline_args.rebuild_package
            or "all" in cmdline_args.rebuild_package
        ):
            cmdline_args.gcc_version = (
                cmdline_args.gcc_version()
                if is_deferred(cmdline_args.gcc_version)
                else cmdline_args.gcc_version
            )
            GCC_VERSION = ck_is_gcc_version(cmdline_args.gcc_version)
        GNU_MIRROR = cmdline_args.gnu_mirror
        pinfo(f"GNU mirror: {GNU_MIRROR}")
        ENV_VARS["TARGET"] = cmdline_args.target_architecture
        pinfo(f"Target architecture: {ENV_VARS['TARGET']}")
        # ENV_VARS["PREFIX"] = _joinpaths(get_toolchain_dir("install"), ENV_VARS["TARGET"])

        pdebug("GCC version: " + GCC_VERSION)
        for defered in buildproc:
            if is_function_passed(defered.func):
                continue
            defered()

        return 0
    except Exception as e:

        perror(traceback.format_exc())
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
