from typing import Callable, Any, Generic, TypeVar, Optional, NewType, Self
from types import TracebackType
from threading import Lock, RLock
from inspect import isroutine
from contextlib import contextmanager
from functools import wraps


class LockType:
    def __init__(self: Self):
        self._mtx = Lock()
        return None

    def acquire(self: Self) -> bool:
        return self._mtx.acquire()

    def release(self: Self):
        return self._mtx.release()


class RLockType:
    def __init__(self: Self):
        self._mtx = RLock()
        return None

    def acquire(self: Self) -> bool:
        return self._mtx.acquire()

    def release(self: Self):
        return self._mtx.release()


@contextmanager
def _mutex(lock: LockType | RLockType):
    lock.acquire()
    try:
        yield lock
    finally:
        lock.release()
    return None


T = TypeVar("T", infer_variance=True)
_T_Mtx = TypeVar("_T_Mtx", LockType, RLockType)


class _MutexBase(Generic[T, _T_Mtx]):
    def __init__(self: Self, value: T, mutex: _T_Mtx):
        self._mtx = mutex
        self._value = value
        return None

    def __str__(self: Self) -> str:
        from . import stringify
        return stringify(self.get())

    def __enter__(self: Self) -> T:
        self._mtx.acquire()
        return self._value

    def __exit__(
        self: Self,
        exc_type: Optional[type],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> bool:
        self._mtx.release()
        return False

    def get(self: Self) -> T:
        with _mutex(self._mtx):
            return self._value
        return unreachable()

    def set(self: Self, new: T):
        with _mutex(self._mtx):
            self._value = new
        return None

    def mapget(self: Self, method: Callable, *args: Any, **kwargs: Any) -> Any:
        with _mutex(self._mtx):
            self._value = method(self._value, *args, **kwargs)
            return self._value
        return unreachable()

    def getmap(self: Self, method: Callable, *args: Any, **kwargs: Any) -> Any:
        with _mutex(self._mtx):
            old = self._value
            self._value = method(self._value, *args, **kwargs)
            return old
        return unreachable()


class Mutex(_MutexBase[T, LockType]):
    def __init__(self: Self, value: T):
        return super().__init__(value, LockType())

    def __repr__(self: Self) -> str:
        return f"Mutex({self.get()})"


class RMutex(_MutexBase[T, RLockType]):
    def __init__(self: Self, value: T):
        return super().__init__(value, RLockType())

    def __repr__(self: Self) -> str:
        return f"RMutex({self.get()})"
