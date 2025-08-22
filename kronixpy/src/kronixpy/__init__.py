import argparse
from sys import intern
from enum import StrEnum
from typing import Self, Any, cast, Callable, Optional, NoReturn
import time
from functools import wraps
from .generate.secinfo import main as secinfo_main
from .toolchain.kernel.build import main as ktoolchain_build_main
from .utils.errprint import *
from .common import *

__author__ = "Axel PASCON <axelpascon@nullware.dev>"


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


def timeit[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    funcname = intern(func.__qualname__)

    @wraps(func)
    def _decorated(*args: P.args, **kwargs: P.kwargs) -> R:
        currtime = time.time()
        try:
            return func(*args, **kwargs)
        finally:
            pinfo(
                f"function `{funcname}` ran for a total of {time.time() - currtime:.3f} seconds"
            )

    return _decorated


@timeit
def main() -> int:
    args = CliArgs().parse_args()
    return args()
