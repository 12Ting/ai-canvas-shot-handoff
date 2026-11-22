from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from types import TracebackType


class WriteGate:
    def __init__(self) -> None:
        self._lock = Lock()

    def __enter__(self) -> "WriteGate":
        self._lock.acquire()
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self._lock.release()

    @contextmanager
    def try_acquire(self) -> Iterator[bool]:
        acquired = self._lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                self._lock.release()
