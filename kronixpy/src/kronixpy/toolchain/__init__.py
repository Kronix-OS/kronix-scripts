import os
import re
from enum import StrEnum, ReprEnum
from typing import Self, Any, Optional, Callable
from ..utils import unreachable
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
    NASM     = "nasm"
    GCC      = "gcc"
    GDB      = "gdb"
    QEMU     = "qemu"
    LIMINE   = "limine"
    # fmt: on


class BuildAction(tuple[str, str], ReprEnum):
    DOWNLOAD = "downloaded", "downloading"
    CONFIGURE = "configured", "configuring"
    BUILD = "built", "building"
    INSTALL = "installed", "installing"

    @property
    def part(self: Self) -> Optional[int]:
        doesnt_have_part, part = _throws(getattr, self, "_part")
        if doesnt_have_part:
            return None
        assert isinstance(part, _Result) and isinstance(part.value, int)
        return part.value

    @part.setter
    def part(self: Self, part: int):
        self._part = part
        return None

    @part.deleter
    def part(self: Self):
        if not _throws(getattr, self, "_part")[0]:
            del self._part
        return None

    @property
    def desc(self: Self) -> Optional[str]:
        doesnt_have_desc, desc = _throws(getattr, self, "_part_desc")
        if doesnt_have_desc:
            return None
        assert isinstance(desc, _Result) and isinstance(desc.value, str)
        return desc.value

    @desc.setter
    def desc(self: Self, desc: str):
        self._part_desc = desc
        return None

    @desc.deleter
    def desc(self: Self):
        if not _throws(getattr, self, "_part_desc")[0]:
            del self._part_desc
        return None

    def action(self: Self, pkg: ToolchainComponent) -> str:
        match self.part, self.desc:
            case None, _:
                return f"{self.name.lower()} {pkg}"
            case part, None:
                return f"{self.name.lower()} {pkg} (part {part})"
            case part, desc:
                return f"{self.name.lower()} {pkg} (part {part}: {desc})"
        return unreachable()

    def success(self: Self, pkg: ToolchainComponent) -> str:
        match self.part, self.desc:
            case None, _:
                return f"succesfully {self[0]} {pkg}"
            case part, None:
                return f"succesfully {self[0]} {pkg} (part {part})"
            case part, desc:
                return f"succesfully {self[0]} {pkg} (part {part}: {desc})"
        return unreachable()

    def failure(self: Self, pkg: ToolchainComponent) -> str:
        return f"could not {self.action(pkg)}"

    def processing(self: Self, pkg: ToolchainComponent) -> str:
        match self.part, self.desc:
            case None, _:
                return f"{self[1]} {pkg}"
            case part, None:
                return f"{self[1]} {pkg} (part {part})"
            case part, desc:
                return f"{self[1]} {pkg} (part {part}: {desc})"
        return unreachable()
