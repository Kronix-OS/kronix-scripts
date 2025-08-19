from typing import Generator, Optional, NoReturn, Self, TypeVar, Generic
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from dataclasses import dataclass
from copy import deepcopy
from .errprint import ESC


class UnreachableError(RuntimeError):
    def __init__(self: Self, msg: Optional[str] = None):
        if msg is not None:
            super().__init__(f"unreachable code reached: {msg}")
        else:
            super().__init__("unreachable code reached")
        return None


def unreachable(msg: Optional[str] = None) -> NoReturn:
    raise UnreachableError(msg)


class TodoError(NotImplementedError):
    def __init__(self, msg: Optional[str] = None):
        if msg is not None:
            super().__init__(f"TODO: {msg}")
        else:
            super().__init__("TODO")
        return None


def todo(msg: Optional[str] = None) -> NoReturn:
    raise TodoError(msg)


class BreakTo(BaseException):
    def __init__(self: Self, layers: int):
        self._layers = layers
        return None

    def as_int(self: Self) -> int:
        return self._layers

    def handle(self: Self):
        if self._layers != 0:
            raise BreakTo(self._layers - 1)
        return None


T_co = TypeVar("T_co", covariant=True)


@dataclass
class EnvSaverPair(Generic[T_co]):
    surrounding: T_co
    current: T_co


@dataclass
class EnvSaver:
    env: Optional[EnvSaverPair[dict[str, str]]]
    path: Optional[EnvSaverPair[Path]]


@contextmanager
def save_env(
    env: Optional[dict[str, str]] = None, path: Optional[Path] = None
) -> Generator[EnvSaver, None, None]:
    env_changed: bool = False
    path_changed: bool = False
    env_pair: Optional[EnvSaverPair[dict[str, str]]] = None
    path_pair: Optional[EnvSaverPair[Path]] = None

    def _setup() -> EnvSaver:
        nonlocal env, path, env_changed, path_changed, env_pair, path_pair
        if env is not None:
            env_pair = EnvSaverPair(dict(os.environ), env)
            try:
                os.environ.update(env)
            except:
                raise
            else:
                env_changed = True
        if path is not None:
            path_pair = EnvSaverPair(Path(os.curdir).resolve(strict=True), path)
            try:
                os.chdir(path.resolve(strict=True))
            except:
                raise
            else:
                path_changed = True
        return EnvSaver(env_pair, path_pair)

    def _cleanup():
        nonlocal env_changed, path_changed, env_pair, path_pair
        if path_changed:
            assert path_pair is not None
            os.chdir(path_pair.surrounding)
        if env_changed:
            assert env_pair is not None
            os.environ = env_pair.surrounding
        return None

    try:
        yield _setup()
    finally:
        _cleanup()
    return None


if sys.platform == "win32":
    _PATHVAR = "PATHEXT"

    def list_to_pathvar(paths: list[str]) -> str:
        return os.pathsep.join(map(lambda x: x.lower(), paths))

    def list_from_pathvar(path: str | list[str]) -> list[str]:
        if isinstance(path, str):
            return path.lower().split(os.pathsep)
        else:
            return path
        return unreachable()

    GLOBAL_DEFAULT_PATH = todo()

else:
    _PATHVAR = "PATH"

    def list_to_pathvar(paths: list[str]) -> str:
        return os.pathsep.join(paths)

    def list_from_pathvar(path: str | list[str]) -> list[str]:
        if isinstance(path, str):
            return path.split(os.pathsep)
        else:
            return path
        return unreachable()

    GLOBAL_DEFAULT_PATH = list_to_pathvar(
        ["/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"]
    )


def get_path(
    default: Optional[list[str]] = None, use_global_default: bool = True
) -> list[str]:
    if _PATHVAR in os.environ.keys():
        path = os.environ[_PATHVAR]
    else:
        if default is None and not use_global_default:
            raise RuntimeError(f"couldn't retrieve `{_PATHVAR}` environment variable")
        path = default or GLOBAL_DEFAULT_PATH

    return list_from_pathvar(path)


def set_path(paths: list[str]):
    os.environ[_PATHVAR] = list_to_pathvar(paths)
    return None