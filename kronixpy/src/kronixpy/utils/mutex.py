from typing import Callable, Any, Generic, TypeVar, Optional, NewType, Self
from threading import Lock, RLock
from inspect import isroutine
from contextlib import contextmanager
from functools import wraps
from . import unreachable


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
        return str(self.get())

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
            return method(self._value, *args, **kwargs)
        return unreachable()

    def mapset(self: Self, method: Callable, *args: Any, **kwargs: Any):
        with _mutex(self._mtx):
            self._value = method(self._value, *args, **kwargs)
        return None


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
