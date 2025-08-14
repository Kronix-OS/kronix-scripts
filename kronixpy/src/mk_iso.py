from typing import Any
import sys
import os
import re
import shutil
import argparse
import traceback
import pathlib
import subprocess
import git
import requests
import json
from enum import StrEnum
from .errprint import *
from . import semver, download


class LazyFile:
    def __init__(self, name: str, download_url: str) -> None:
        self.name = name
        self.url = download_url
        return

    def materialize(self, at: str | pathlib.Path) -> str:
        path = pathlib.Path(get_path_str(at))
        exists = path.exists()
        if path.is_dir() or not exists:
            os.makedirs(path, exist_ok=True)
            write_to = path / self.name
        else:
            write_to = path
        pinfo(f"downloading {make_tty_link(self.name, self.url)} at {write_to}")
        write_to.write_bytes(download.from_http(self.url))
        return get_path_str(write_to)


class LazyGithubDownloader:
    def __init__(self, commit_url: str) -> None:
        response = requests.get(commit_url)
        if response.status_code != 200:
            raise ValueError(
                f"failed to get release tags from {commit_url}: {response.status_code}"
            )
        self._json = json.loads(response.text)
        return

    def __getitem__(self, file: str) -> LazyFile:
        url = self.get_url_for(file)
        return LazyFile(file, url)

    def get_url_for(self, file: str) -> str:
        if "/" in file:
            raise NotImplementedError(
                "searching into sub-directories is yet to be implemented"
            )
        for f in self._json["files"]:
            if f["filename"] == file:
                return f["raw_url"]  # or contents_url ?
        raise LookupError(f"file or directory {file} does not exist")


class ProgramArgError(ValueError):
    def __init__(self, *args: object) -> None:
        super().__init__(*args)


class Arch(StrEnum):
    AMD64 = "amd64"
    X86 = "x86"
    AARCH64 = "aarch64"
    RISCV64 = "riscv64"
    POWERPC64 = "ppc64"
    SPARC64 = "sparc64"
    MIPS64 = "mips64"
    LOONGARCH64 = "loongarch64"


class Bootloader(StrEnum):
    LIMINE = "limine"
    GRUB2 = "grub2"
    UEFI = "uefi"


class IsoMaker:
    def __init__(
        self,
        root: str | pathlib.Path,
        executable: str | pathlib.Path,
        arch: Arch,
        bootloader: Bootloader,
        output: str | pathlib.Path,
        bootconf: str | pathlib.Path | None,
        bootmods: str | pathlib.Path,
    ) -> None:
        self.root = pathlib.Path(root)
        self.executable = pathlib.Path(executable)
        self.arch = arch
        self.bootloader = bootloader
        self.output = pathlib.Path(output)
        if bootconf is not None:
            self.bootconf = pathlib.Path(bootconf)
        else:
            self.bootconf = None
        self.bootmods = bootmods
        return

    def __call__(self) -> None:
        return make_iso(self)


class ProcessFailedError(Exception):
    def __init__(self, process: subprocess.CompletedProcess[str]) -> None:
        super().__init__()
        self.proc = process
        return None


def is_newer_than(fromfile: str, tofile: str) -> bool:
    if os.path.exists(tofile):
        return (
            os.stat(fromfile, follow_symlinks=False).st_mtime
            > os.stat(tofile, follow_symlinks=False).st_mtime
        )
    else:
        return True


def walk_files_in(dir: str):
    for path in os.scandir(dir):
        if path.is_file(follow_symlinks=False):
            yield path.path


def cmd(cmd: list[str]):
    pinfo(f"running '{' '.join(cmd)}'")
    return subprocess.run(cmd, encoding="utf-8")


def cmd_if_newer(cmd: list[str], **kwargs):
    if "fromfile" in kwargs.keys() and "tofile" in kwargs.keys():
        fromfile = kwargs["fromfile"]
        tofile = kwargs["tofile"]
        assert isinstance(fromfile, str) and isinstance(tofile, str)
        do_it = is_newer_than(fromfile, tofile)
        if not do_it:
            pinfo(f"{fromfile} is older than {tofile}")
            pinfo(f"no need to perform command '{' '.join(cmd)}'")
            return None
    elif "pairs" in kwargs.keys():
        pairs = kwargs["pairs"]
        assert isinstance(pairs, list) and all(
            map(
                lambda o: (isinstance(o, tuple) and len(o) == 2)
                and all(map(lambda item: isinstance(item, str), o)),
                pairs,
            )
        )
        do_it = any(map(lambda tp: is_newer_than(tp[0], tp[1]), pairs))
        if not do_it:
            fromfiles = ", ".join(map(lambda tp: tp[0], pairs))
            tofiles = ", ".join(map(lambda tp: tp[1], pairs))
            pinfo(f"{fromfiles} are all respectively older than {tofiles}")
            pinfo(f"no need to perform command '{' '.join(cmd)}'")
    else:
        raise RuntimeError("Internal error !")
    pinfo(f"running '{' '.join(cmd)}'")
    return subprocess.run(cmd, encoding="utf-8")


def _cp_common(fromfile: str, tofile: str):
    if tofile[-1] == "/" or os.path.isdir(tofile):
        if tofile[-1] != "/":
            tofile += "/"
        tofile += os.path.basename(fromfile)
    os.makedirs(os.path.dirname(tofile), exist_ok=True)
    need_update = is_newer_than(fromfile, tofile)
    return fromfile, tofile, need_update


def cp_if_newer(fromfile: str, tofile: str):
    fromfile, tofile, need_upd = _cp_common(fromfile, tofile)
    if not need_upd:
        pinfo(f"{fromfile} is older than {tofile}")
        pinfo("no need to perform any copy")
        return tofile
    pinfo(f"copying {fromfile} to {tofile}")
    fromfd = -1
    tofd = -1
    try:
        fromfd = os.open(fromfile, os.O_RDONLY)
        tofd = os.open(tofile, os.O_WRONLY | os.O_TRUNC | os.O_CREAT)
        os.sendfile(tofd, fromfd, None, os.fstat(fromfd).st_size)
    except:
        raise
    finally:
        if fromfd != -1:
            os.close(fromfd)
        if tofd != -1:
            os.close(tofd)
    return tofile


def cp(fromfile: str, tofile: str):
    fromfile, tofile, _ = _cp_common(fromfile, tofile)
    pinfo(f"copying {fromfile} to {tofile}")
    if os.path.isdir(fromfile):
        return _cp_dir(fromfile, tofile)
    fromfd = -1
    tofd = -1
    try:
        fromfd = os.open(fromfile, os.O_RDONLY)
        tofd = os.open(tofile, os.O_WRONLY | os.O_TRUNC | os.O_CREAT)
        os.sendfile(tofd, fromfd, None, os.fstat(fromfd).st_size)
    except:
        raise
    finally:
        if fromfd != -1:
            os.close(fromfd)
        if tofd != -1:
            os.close(tofd)
    return tofile


def _cp_dir(fromdir: str, todir: str, strict: bool = True):
    if os.path.exists(todir):
        if strict:
            raise RuntimeError(f"path {todir} already exists !")
        else:
            return todir
    os.mkdir(todir)
    for path in os.scandir(fromdir):
        cp(path.path, todir)
    return todir


def cargo(cargo_cmd: str, *extra: str, **kwargs):
    args = ["cargo", cargo_cmd, "-Z", "unstable-options"]
    args.extend(extra)
    if "features" in kwargs.keys() and len(kwargs["features"]) > 0:
        args.extend(
            ["--no-default-features", "--features", f'"{' '.join(kwargs['features'])}"']
        )
    if "profile" in kwargs.keys():
        args.extend(["--profile", kwargs["profile"]])
    return cmd(args)


def checkps(ps: subprocess.CompletedProcess[str]):
    if ps.returncode != 0:
        raise ProcessFailedError(ps)
    return None


def get_path_str(path: str | pathlib.Path) -> str:
    if isinstance(path, str):
        p = os.path.realpath(path)
    elif isinstance(path, pathlib.Path):
        p = path.resolve()
    else:
        raise TypeError(f"unknown type '{type(path)}' passed to 'get_path_str'")
    return os.fspath(p)


def clone_repo(url: str, path: str) -> git.Repo:
    ps = cmd(
        ["git", "clone", "--recurse-submodules", "--progress", "--verbose", url, path]
    )
    checkps(ps)
    return git.Repo(path)


def get_git_releases(
    owner: str, repo: str, allow_draft: bool = False, allow_prerelease: bool = False
) -> Any:
    GH_API = f"https://api.github.com/repos/{owner}/{repo}/releases"
    response = requests.get(GH_API)
    if response.status_code != 200:
        raise ValueError(f"failed to get releases from {GH_API}")
    releases = json.loads(response.text)
    if not allow_draft:
        releases = [release for release in releases if not release["draft"]]
    if not allow_prerelease:
        releases = [release for release in releases if not release["prerelease"]]
    return releases


def download_latest_limine_binaries(root: str | pathlib.Path) -> LazyGithubDownloader:
    MIN_VERSION = "9"
    MAX_VERSION = "10"

    def _match_in_bounds_stable_binary_release_tag(tag: str) -> re.Match[str] | None:
        regex = r"v(\d+.\d+(.\d+)?)-binary"
        matched = re.match(regex, tag)
        if matched:
            is_greater = semver.comp(MIN_VERSION, matched.groups()[0]) <= 0
            is_less = semver.comp(MAX_VERSION, matched.groups()[0]) >= 0
            return matched if is_greater and is_less else None
        else:
            return None

    def _find_greatest(versions: list[tuple[int, str]]) -> tuple[int, str]:
        greatest = 0
        length = len(versions)
        for i in range(1, length):
            curr = versions[greatest][1]
            other = versions[i][1]
            if semver.comp(other, curr) > 0:
                greatest = i
        return versions[greatest]

    # tmpdir = tempfile.TemporaryDirectory(prefix='mk_iso_limine_')

    GH_API = "https://api.github.com/repos/limine-bootloader/limine"
    response = requests.get(f"{GH_API}/tags")
    if response.status_code != 200:
        raise ValueError(
            f"failed to get release tags from {GH_API}/tags: {response.status_code}"
        )

    tags = json.loads(response.text)
    latest = _find_greatest(
        [
            (index, matched.groups()[0])
            for (index, matched) in map(
                lambda tp: (
                    tp[0],
                    _match_in_bounds_stable_binary_release_tag(tp[1]["name"]),
                ),
                enumerate(tags),
            )
            if bool(matched)
        ]
    )
    pinfo(f"greatest available Limine version is {latest[1]}")
    commit_url = tags[latest[0]]["commit"]["url"]
    return LazyGithubDownloader(commit_url)


def make_iso(args: IsoMaker):
    match args.bootloader:
        case Bootloader.LIMINE:
            assert args.bootconf is not None
            UEFI_BOOT_SUFFIXES = {
                Arch.AMD64: "X64",
                Arch.X86: "IA32",
                Arch.LOONGARCH64: "LOONGARCH64",
                Arch.AARCH64: "AA64",
                Arch.RISCV64: "RISCV64",
            }
            downloader = download_latest_limine_binaries(args.root)
            cp(get_path_str(args.executable), f"{get_path_str(args.root)}/boot/")
            cp(get_path_str(args.bootmods), f"{get_path_str(args.root)}/boot/")
            cp(get_path_str(args.bootconf), f"{get_path_str(args.root)}/boot/limine/")
            downloader[f"BOOT{UEFI_BOOT_SUFFIXES[args.arch]}.EFI"].materialize(
                f"{get_path_str(args.root)}/EFI/BOOT/"
            )
            match args.arch:
                case Arch.AMD64 | Arch.X86:
                    downloader["limine-bios-cd.bin"].materialize(
                        f"{get_path_str(args.root)}/boot/limine/"
                    )
                    downloader["limine-uefi-cd.bin"].materialize(
                        f"{get_path_str(args.root)}/boot/limine/"
                    )
                    ps = cmd(
                        [
                            "xorriso",
                            "-as",
                            "mkisofs",
                            "-b",
                            "boot/limine/limine-bios-cd.bin",
                            "-no-emul-boot",
                            "-boot-load-size",
                            "4",
                            "-boot-info-table",
                            "--efi-boot",
                            "boot/limine/limine-uefi-cd.bin",
                            "-efi-boot-part",
                            "--efi-boot-image",
                            "--protective-msdos-label",
                            get_path_str(args.root),
                            "-o",
                            get_path_str(args.output),
                        ]
                    )
                    checkps(ps)
                case Arch.AARCH64:
                    raise NotImplementedError()
                case Arch.RISCV64:
                    raise NotImplementedError()
                case Arch.LOONGARCH64:
                    raise NotImplementedError()
                case _:
                    raise ValueError(
                        f"invalid architecture or not supported by Limine: '{args.arch}'"
                    )
        case Bootloader.GRUB2:
            raise NotImplementedError()
        case Bootloader.UEFI:
            raise NotImplementedError()
        case _:
            raise ValueError(f"invalid bootloader: '{bootloader}'")


def parse_cmdline():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "executable", type=pathlib.Path, help="The name of the ELF executable"
    )
    parser.add_argument(
        "-r",
        "--iso-root",
        type=pathlib.Path,
        required=True,
        help="The directory for the ISO root",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=pathlib.Path,
        default=None,
        required=True,
        help="The output ISO file",
    )
    parser.add_argument(
        "-b", "--bootloader", type=str, default="limine", help="The bootloader to use"
    )
    parser.add_argument(
        "-c",
        "--bootloader-config",
        type=pathlib.Path,
        default=None,
        help="The path for the bootloader configuration (for example: limine.conf)",
    )
    parser.add_argument(
        "-a", "--arch", type=str, default="amd64", help="The target architecture"
    )
    parser.add_argument(
        "-m",
        "--boot-modules",
        type=pathlib.Path,
        default=None,
        required=True,
        help="The directory where the kernel's boot modules reside",
    )
    return parser.parse_args()


def validate_arch(parsed: argparse.Namespace) -> Arch:
    got: str = parsed.arch.lower()
    got = (
        got.replace("powerpc", "ppc")
        .replace("arm64", "aarch64")
        .replace("x86-64", "amd64")
        .replace("x86_64", "amd64")
    )
    if not got in Arch:
        raise ProgramArgError(f"{got} is not a known architecture")
    try:
        result = Arch(got)
        return result
    except:
        raise


def validate_bootloader(parsed: argparse.Namespace, arch: Arch) -> Bootloader:
    got: str = parsed.bootloader.lower()
    if not got in Bootloader:
        raise ProgramArgError(f"{got} is not a known bootloader")
    try:
        result = Bootloader(got)
    except:
        raise
    match (result, arch):
        case (
            Bootloader.LIMINE,
            Arch.AMD64 | Arch.X86 | Arch.LOONGARCH64 | Arch.RISCV64 | Arch.AARCH64,
        ):
            return result
        case (Bootloader.LIMINE, _):
            raise ProgramArgError(f"arch {arch} is not supported by Limine")
        case (Bootloader.GRUB2, _):
            raise NotImplementedError()
        case (Bootloader.UEFI, _):
            raise NotImplementedError()
        case _:
            # should not be reachable
            raise ProgramArgError(f"unknown bootloader: {result}")


def validate_executable(parsed: argparse.Namespace) -> pathlib.Path:
    ELF_MAGIC = b"\x7fELF"
    exe: pathlib.Path = parsed.executable
    if not exe.exists():
        raise ProgramArgError(f"provided executable named {exe} doesn't exist")
    if not exe.is_file():
        raise ProgramArgError(f"provided executable named {exe} isn't a regular file")
    with open(exe, "rb") as f:
        if f.read(len(ELF_MAGIC)) != ELF_MAGIC:
            raise ProgramArgError(
                f"provided executable named {exe} isn't an ELF executable"
            )
    return exe.resolve()


def validate_iso_root(parsed: argparse.Namespace) -> pathlib.Path:
    root: pathlib.Path = parsed.iso_root
    shutil.rmtree(root, ignore_errors=True)
    return root.resolve()


def validate_output(parsed: argparse.Namespace) -> pathlib.Path:
    out: pathlib.Path = parsed.output
    shutil.rmtree(out, ignore_errors=True)
    return out.resolve()


def validate_bootloader_config(
    parsed: argparse.Namespace, bootloader: Bootloader
) -> pathlib.Path | None:
    match bootloader:
        case Bootloader.LIMINE:
            bootconf: pathlib.Path | None = parsed.bootloader_config
            if bootconf is None:
                raise ProgramArgError(
                    "you must provide a bootloader configuration file when "
                    + "using Limine, but none was provided"
                )
            if not bootconf.exists():
                raise ProgramArgError(
                    f"provided bootloader configuration path {bootconf} doesn't exist"
                )
            if not bootconf.is_file():
                raise ProgramArgError(
                    f"provided bootloader configuration path {bootconf} isn't a regular file"
                )
        case Bootloader.GRUB2:
            raise NotImplementedError()
        case Bootloader.UEFI:
            raise NotImplementedError()
        case _:
            raise ProgramArgError(f"invalid bootloader {bootloader}")
    return bootconf.resolve() if bootconf is not None else None


def validate_boot_modules_path(parsed: argparse.Namespace) -> pathlib.Path:
    bootmods: pathlib.Path = parsed.boot_modules
    if not bootmods.exists():
        raise ProgramArgError(f"provided boot modules path {bootmods} doesn't exist")
    elif not bootmods.is_dir():
        raise ProgramArgError(
            f"provided boot modules path {bootmods} is not a directory"
        )
    return bootmods.resolve()


def main() -> int:
    try:
        parsed = parse_cmdline()
        root = validate_iso_root(parsed)
        exe = validate_executable(parsed)
        out = validate_output(parsed)
        arch = validate_arch(parsed)
        bootloader = validate_bootloader(parsed, arch)
        bootconf = validate_bootloader_config(parsed, bootloader)
        bootmods = validate_boot_modules_path(parsed)
        IsoMaker(root, exe, arch, bootloader, out, bootconf, bootmods)()
    except ProcessFailedError as e:
        perror(
            f"command '{' '.join(e.proc.args)}' exited with error code {e.proc.returncode}"
        )
        perror("terminating...")
    except Exception:
        perror(traceback.format_exc())
        perror("terminating...")
        return 1
    return 0


# TODO: try to use `landlock`
if __name__ == "__main__":
    sys.exit(main())
