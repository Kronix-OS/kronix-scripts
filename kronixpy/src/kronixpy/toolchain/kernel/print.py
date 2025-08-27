from pathlib import Path
from ...common import Namespace, Arch, common_main
from ...utils import unreachable
from .. import ToolchainComponent


def main(args: Namespace) -> int:
    common_main(args)

    arch = Arch.coerce_from(args.architecture)
    toolchain_directory = Path(args.toolchain_dir).resolve()
    pkg = args.component

    print(toolchain_directory / "install" / "bin" / f"{arch.to_kernel_triplet()}-{pkg}")

    return 0
