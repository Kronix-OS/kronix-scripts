import os
import re
from enum import StrEnum, ReprEnum
from typing import Self, Any, Optional, Callable, Iterable, Iterator, Container
from ..utils import unreachable, isoneof
from operator import eq

MAKEJOBS: int = (os.cpu_count() or 1) + 1
MAKELOAD: float = MAKEJOBS * 0.8
MAKEFLAGS: list[str] = [f"--jobs={MAKEJOBS}", f"--load-average={MAKELOAD}"]


class _Result[_T]:
    def __init__(self: Self, value: _T):
        self.value = value
        return None


def _throws(fn: Callable, *args, **kwargs) -> tuple[bool, Optional[_Result[Any]]]:
    try:
        return False, _Result(fn(*args, **kwargs))
    except:
        pass
    return True, None


class ToolchainComponent(StrEnum):
    # fmt: off
    ALL      = "all"
    BINUTILS = "binutils"
    GCC      = "gcc"
    NASM     = "nasm"
    GDB      = "gdb"
    QEMU     = "qemu"
    LIMINE   = "limine"
    # fmt: on

    @property
    def is_gnu_pkg(self: Self) -> bool:
        return isoneof(self)(
            ToolchainComponent.BINUTILS, ToolchainComponent.GCC, ToolchainComponent.GDB
        )

    @classmethod
    def toolchain_build_order(
        cls: type[Self], packages: Container["ToolchainComponent"]
    ) -> Iterator["ToolchainComponent"]:
        for pkg in cls:
            if pkg in packages:
                yield pkg
        return None


class BuildAction(tuple[str, str], ReprEnum):
    DOWNLOAD = "downloaded", "downloading"
    CONFIGURE = "configured", "configuring"
    BUILD = "built", "building"
    INSTALL = "installed", "installing"

    def action(
        self: Self,
        pkg: ToolchainComponent,
        part: Optional[int] = None,
        desc: Optional[str] = None,
    ) -> str:
        match part, desc:
            case None, _:
                return f"{self.name.lower()} {pkg}"
            case somepart, None:
                return f"{self.name.lower()} {pkg} (part {somepart})"
            case somepart, somedesc:
                return f"{self.name.lower()} {pkg} (part {somepart}: {somedesc})"
        return unreachable()

    def success(
        self: Self,
        pkg: ToolchainComponent,
        part: Optional[int] = None,
        desc: Optional[str] = None,
    ) -> str:
        match part, desc:
            case None, _:
                return f"succesfully {self[0]} {pkg}"
            case somepart, None:
                return f"succesfully {self[0]} {pkg} (part {somepart})"
            case somepart, somedesc:
                return f"succesfully {self[0]} {pkg} (part {somepart}: {somedesc})"
        return unreachable()

    def failure(
        self: Self,
        pkg: ToolchainComponent,
        part: Optional[int] = None,
        desc: Optional[str] = None,
    ) -> str:
        return f"could not {self.action(pkg, part, desc)}"

    def start(
        self: Self,
        pkg: ToolchainComponent,
        part: Optional[int] = None,
        desc: Optional[str] = None,
    ) -> str:
        match part, desc:
            case None, _:
                return f"start {self[1]} {pkg}"
            case somepart, None:
                return f"start {self[1]} {pkg} (part {somepart})"
            case somepart, somedesc:
                return f"start {self[1]} {pkg} (part {somepart}: {somedesc})"
        return unreachable()
