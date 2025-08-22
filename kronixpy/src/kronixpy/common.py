import argparse
from enum import StrEnum
from typing import Self, Callable, Optional, Any
from .utils.errprint import *

class Bootloader(StrEnum):
    # fmt: off
    LIMINE = "limine"
    GRUB2  = "grub2"
    UEFI   = "uefi"
    # fmt: on


class Arch(StrEnum):
    # fmt: off
    AMD64       = "amd64"
    X86         = "x86"
    AARCH64     = "aarch64"
    RISCV64     = "riscv64"
    POWERPC64   = "ppc64"
    SPARC64     = "sparc64"
    MIPS64      = "mips64"
    LOONGARCH64 = "loongarch64"
    # fmt: on

    def to_kernel_triplet(self) -> str:
        match self:
            case Arch.AMD64:
                return "x86_64-unknown-none-elf"
            case other:
                raise NotImplementedError(
                    f"this function is yet to be implemented for arch {other}"
                )
        return unreachable()

    def supported_by(self, bootloader: Bootloader) -> bool:
        match bootloader:
            case Bootloader.LIMINE:
                return any(
                    map(
                        lambda a: self == a,
                        [
                            type(self).AMD64,
                            type(self).X86,
                            type(self).AARCH64,
                            type(self).RISCV64,
                            type(self).POWERPC64,
                            type(self).SPARC64,
                            type(self).MIPS64,
                            type(self).LOONGARCH64,
                        ],
                    )
                )
            case other:
                raise NotImplementedError(
                    f"this function is yet to be implemented for bootloader {other}"
                )
        return unreachable()

    @classmethod
    def coerce_from(cls: type[Self], arch: str) -> Self:
        arch = arch.lower()
        exc = None
        try:
            return cls(arch)
        except ValueError as e:
            exc = e
        except:
            raise
        match arch:
            case "x86-64" | "x86_64":
                return cls(cls.AMD64)
            case "i386" | "i486" | "i586" | "i686" | "i786" | "i886" | "i986":
                return cls(cls.X86)
            case "powerpc64":
                return cls(cls.POWERPC64)
            case "arm64":
                return cls(cls.AARCH64)
        raise exc

type _PRINT_HELP_T = Callable[[], None]

class Namespace(argparse.Namespace):
    def __init__(self, **kwargs: Any):
        self.parser_help: Optional[_PRINT_HELP_T] = kwargs.pop("parser_help", None)
        super().__init__(**kwargs)
        return None

    def __call__(self) -> int:
        def _invalid_args_provided(args: argparse.Namespace) -> int:
            perror("invalid arguments provided !")
            pinfo("maybe you didn't provided a valid subcommand ?")
            if self.parser_help is not None:
                self.parser_help()
            return 1

        return getattr(self, "func", _invalid_args_provided)(self)
    
def common_main(args: Namespace):
    if args.log is not None:
        assert isinstance(args.log, str)
        set_logfile(args.log)
    assert isinstance(args.debug, bool)
    set_debug_mode(args.debug)
    return None