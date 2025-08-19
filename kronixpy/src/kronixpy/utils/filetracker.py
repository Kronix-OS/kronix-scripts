import asyncio
import itertools
from threading import RLock, Thread, Event
from dataclasses import dataclass
import dbm
from copy import deepcopy
import pickle
import os
import sys
import warnings
from os import stat_result
from xxhash import xxh128
from os.path import ALLOW_MISSING
from typing import (
    Any,
    TypeVar,
    Optional,
    Self,
    NoReturn,
    Literal,
    Generic,
    MutableMapping,
    Iterator,
    override,
    overload,
)
from types import TracebackType
from _typeshed import StrOrBytesPath
from pathlib import Path
from .mutex import Mutex
from . import BreakTo, unreachable

if sys.platform == "win32":
    from filelock import WindowsFileLock as FileLock
else:
    from filelock import UnixFileLock as FileLock


def _kind_string(path: Path) -> tuple[Optional[str], str]:
    if path.is_file(follow_symlinks=False):
        return "a", "regular file"
    if path.is_dir(follow_symlinks=False):
        return "a", "directory"
    if path.is_symlink():
        return "a", "symbolic link"
    if path.is_socket():
        return "a", "socket"
    if path.is_fifo():
        return "a", "queue file (FIFO)"
    if path.is_block_device():
        return "a", "block device"
    if path.is_char_device():
        return "a", "character device"
    if path.is_junction():
        return "a", "junction"
    if path.is_mount():
        return "a", "mount point"
    with warnings.catch_warnings(action="ignore"):
        if path.is_reserved():
            return "a", "reserved path"
    return None, "<unknown>"


type _PathExpectation = Literal[
    "a regular file",
    "a directory",
    "a symbolic link",
    "a socket",
    "a queue file (FIFO)",
    "a block device",
    "a character device",
    "a junction",
    "a mount point",
    "a reserved path",
]


def _expected(what: _PathExpectation, got: Path) -> NoReturn:
    kind_prefix, kind = _kind_string(got)
    raise ValueError(
        f"expected {what} at `{got}`, but got {kind_prefix or ''} {kind} instead"
    )


def _hash_file(filename: StrOrBytesPath) -> int:
    hashed = xxh128()
    with open(filename, "rb") as f:
        # Reusable buffer to reduce allocations.
        buf = bytearray(2**18)
        view = memoryview(buf)
        while True:
            size = f.readinto(buf)
            if size == 0:
                break  # EOF
            hashed.update(view[:size])
        return hashed.intdigest()
    return unreachable()


type _FsDir = "_FsDepth"
type _FsObj = _FsDir | _FsNotDir
type _FsModification = tuple[Optional[_FsObj], Optional[_FsObj]]


@dataclass
class _FsNotDir:
    path: str
    st_mode: int
    st_size: int
    checksum: int


@dataclass
class _FsDepth:
    path: str
    dirs: dict[str, _FsDir]
    others: dict[str, _FsNotDir]


def _compare_depth(old: _FsDepth, new: _FsDepth) -> Iterator[_FsModification]:
    olds = set(old.others) | set(old.dirs)
    news = set(new.others) | set(new.dirs)
    not_in_new = olds - news
    not_in_old = news - olds
    in_both = olds & news
    for o in not_in_new:
        if o in old.others:
            yield (old.others[o], None)
        else:
            yield (old.dirs[o], None)
    for n in not_in_old:
        if n in new.others:
            yield (None, new.others[n])
        else:
            yield (None, new.dirs[n])
    for b in in_both:
        if b in old.others:
            oobj = old.others[b]
            if b in new.others:
                nobj = new.others[b]
                if oobj != nobj:
                    yield (oobj, nobj)
            else:
                yield (oobj, new.dirs[b])
        else:
            oobj = old.dirs[b]
            if b in new.dirs:
                nobj = new.dirs[b]
                for diff in _compare_depth(oobj, nobj):
                    yield diff
            else:
                yield (oobj, new.others[b])
    return None


class _FileList:
    def __init__(self: Self, dir: Path):
        self._dir = dir.resolve(strict=True)
        self._list = self._build(self._dir)
        return None

    def _build(self: Self, dir: Path) -> _FsDepth:
        path: str = str(dir)
        dirs: dict[str, _FsDir] = {}
        others: dict[str, _FsNotDir] = {}
        for fsobj in dir.iterdir():
            fsobjpath = str(fsobj)
            if fsobj.is_dir(follow_symlinks=False):
                dirs[fsobjpath] = self._build(fsobj)
            else:
                stat = fsobj.stat(follow_symlinks=False)
                others[fsobjpath] = _FsNotDir(
                    fsobjpath, stat.st_mode, stat.st_size, _hash_file(fsobj)
                )
        return _FsDepth(path, dirs, others)

    def modifications(self: Self) -> Iterator[_FsModification]:
        old = deepcopy(self._list)
        new = self._build(self._dir)
        return _compare_depth(old, new)


KeyT = TypeVar("KeyT", infer_variance=True)


class FileTracker(Generic[KeyT], MutableMapping[KeyT, list[Path]]):
    def __init__(
        self: Self,
        persistent: StrOrBytesPath,
        directory: StrOrBytesPath,
        create: bool = False,
        parents: bool = False,
        directory_mode: Optional[int] = None,
    ):
        self._directory = Path(str(os.path.realpath(directory, strict=ALLOW_MISSING)))
        if not self._directory.exists(follow_symlinks=False):
            if create:
                if directory_mode is None:
                    self._directory.mkdir(parents=parents, exist_ok=False)
                else:
                    self._directory.mkdir(
                        mode=directory_mode, parents=parents, exist_ok=False
                    )
            else:
                raise ValueError(f"directory `{self._directory}` does not exist")
        if not self._directory.is_dir(follow_symlinks=False):
            return _expected("a directory", self._directory)

        self._persistent = Path(str(persistent)).resolve(strict=False)

        self._persistent_db = dbm.open(self._persistent, "c")

        self._lock = RLock()

        self._list = None
        self._key = None

        return None

    def start(self: Self, key: KeyT):
        with self._lock:
            self._key = key
            self._list = _FileList(self._directory)
        return self

    def stop(self: Self):
        with self._lock:
            assert self._list is not None
            assert self._key is not None
            for old, new in self._list.modifications():
                # TODO: handle errors
                if old is not None:
                    raise RuntimeError(f"{old.path} changed in some unpredictable way")
                assert new is not None
                objs = self[self._key]
                objs.append(Path(new.path))
                self[self._key] = objs
        return None

    @override
    def __len__(self: Self) -> int:
        with self._lock:
            return len(self._persistent_db)
        return unreachable()

    @override
    def __iter__(self: Self) -> Iterator[KeyT]:
        with self._lock:
            for key in self._persistent_db:
                yield type(self)._unpickle_key(key)
            return None
        return unreachable()

    @override
    def __getitem__(self: Self, key: KeyT) -> list[Path]:
        with self._lock:
            return type(self)._unpickle_value(
                self._persistent_db[type(self)._pickle_key(key)]
            )
        return unreachable()

    @override
    def __setitem__(self: Self, key: KeyT, value: list[Path]):
        with self._lock:
            self._persistent_db[pickle.dumps(key)] = pickle.dumps(value)
            return None
        return unreachable()

    @override
    def __delitem__(self: Self, key: KeyT):
        with self._lock:
            del self._persistent_db[pickle.dumps(key)]
            return None
        return unreachable()

    @classmethod
    def _pickle_key(cls: type[Self], key: KeyT) -> bytes:
        return pickle.dumps(key)

    @classmethod
    def _pickle_value(cls: type[Self], value: list[Path]) -> bytes:
        return pickle.dumps(value)

    @classmethod
    def _unpickle_key(cls: type[Self], buffer: bytes) -> KeyT:
        return pickle.loads(buffer)

    @classmethod
    def _unpickle_value(cls: type[Self], buffer: bytes) -> list[Path]:
        return pickle.loads(buffer)
