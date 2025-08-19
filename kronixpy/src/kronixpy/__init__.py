import argparse
from enum import StrEnum
from typing import Self, Any, cast, Callable, Optional, NoReturn
from .generate.secinfo import main as secinfo_main
from .toolchain.kernel.build import main as ktoolchain_build_main
from .utils.errprint import *

__author__ = "Axel PASCON <axelpascon@nullware.dev>"


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


PARSER_DESCRIPTOR = {
    "args": [
        {
            "names": ["--log"],
            "type": str,
            "default": None,
            "help": "Log to the specified file",
        },
        {
            "names": ["--debug"],
            "action": "store_true",
            "help": "Enable printing of various debug information",
        },
    ],
    "subcommands": {
        "toolchain": {
            "help": "Manipulate toolchains",
            "args": [
                {
                    "names": ["-d", "--toolchain-dir"],
                    "type": str,
                    "required": True,
                    "help": "The toolchain directory",
                }
            ],
            "subcommands": {
                "kernel": {
                    "help": "Manipulate kernel toolchain (i.e. the freestanding ELF toolchain)",
                    "args": [
                        {
                            "names": ["-a", "--architecture"],
                            "choices": [x.value for x in Arch],
                            "default": "amd64",
                            "help": "Target architecture",
                        }
                    ],
                    "subcommands": {
                        "build": {
                            "help": "Download and build kernel's toolchain",
                            "args": [
                                {
                                    # For ex. : --rebuild-package=gcc,binutils,gdb
                                    "names": ["-p", "--rebuild-package"],
                                    "type": str,  # TODO: make it `choices` instead
                                    "default": "all",
                                    "help": "Rebuild only the specified comma-separated packages",
                                },
                                {
                                    "names": ["--with-target-arch"],
                                    "type": str,
                                    "default": "native",
                                    "help": "Default toolchain's target architecture",
                                },
                                {
                                    "names": ["--with-target-tune"],
                                    "type": str,
                                    "default": "native",
                                    "help": "Default toolchain's target tune",
                                },
                            ],
                            "func": ktoolchain_build_main,
                        }
                    },
                }
            },
        },
        "generate": {
            "help": "Generate various pieces of code",
            "subcommands": {
                "secinfo": {
                    "args": [
                        {
                            "names": ["-c", "--output-cfile"],
                            "type": str,
                            "required": False,
                            "default": None,
                            "help": "Output C file",
                        },
                        {
                            "names": ["-r", "--output-rsfile"],
                            "type": str,
                            "required": False,
                            "default": None,
                            "help": "Output Rust file",
                        },
                        {
                            "names": ["-l", "--output-linkerfile"],
                            "type": str,
                            "required": False,
                            "default": None,
                            "help": "Output linker file",
                        },
                        {
                            "names": ["-i", "--input-linkerfile"],
                            "type": str,
                            "required": True,
                            "help": "Input templated linker file",
                        },
                        {
                            "names": ["sections"],
                            "type": str,
                            "nargs": "+",
                            "help": "Sections to generate info for",
                        },
                    ],
                    "func": secinfo_main,
                }
            },
        },
    },
}


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


class CliArgs:
    def __init__(self):
        self.parser = argparse.ArgumentParser(
            description="Python CLI to configure and work with Kronix kernel source code"
        )

        subcommands: dict[str, dict] = PARSER_DESCRIPTOR["subcommands"]
        args: list = PARSER_DESCRIPTOR["args"]

        type(self).add_subcommands(self.parser, subcommands)
        type(self).add_args(self.parser, args)

        return None

    @classmethod
    def add_subcommands(
        cls: type[Self], instance: argparse.ArgumentParser, subcommands: dict[str, dict]
    ):
        sub = instance.add_subparsers()

        for subcmd, dic in subcommands.items():
            subsubcmds = dic.pop("subcommands", None)
            subargs = dic.pop("args", None)
            if subsubcmds is None:
                func = dic.pop("func")
            else:
                func = None

            parser = sub.add_parser(subcmd, **dic)

            if isinstance(subsubcmds, dict):
                cls.add_subcommands(parser, subsubcmds)
            else:
                assert func is not None
                parser.set_defaults(func=func)

            if isinstance(subargs, list):
                cls.add_args(parser, subargs)

        return None

    @classmethod
    def add_args(
        cls: type[Self], instance: argparse.ArgumentParser, args: list[dict[str, Any]]
    ):
        for arg in args:
            names: list[str] = arg.pop("names")
            instance.add_argument(*names, **arg)
        return None

    def parse_args(self, *args, **kwargs):
        return self.parser.parse_args(
            namespace=Namespace(parser_help=lambda: self.parser.print_help()),
            *args,
            **kwargs,
        )

def common_main(args: Namespace):
    if args.log is not None:
        assert isinstance(args.log, str)
        set_logfile(args.log)
    assert isinstance(args.debug, bool)
    set_debug_mode(args.debug)

def main() -> int:
    args = CliArgs().parse_args()
    return args()
