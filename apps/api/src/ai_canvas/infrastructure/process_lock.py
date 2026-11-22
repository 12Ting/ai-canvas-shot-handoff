import msvcrt
from pathlib import Path
from typing import BinaryIO


class DataDirectoryLock:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeError("AI Canvas data directory lock is already acquired")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        handle = (self.data_dir / "runtime.lock").open("a+b")
        try:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            descriptor = handle.fileno()
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError(
                    f"AI Canvas data directory is already in use: {self.data_dir}"
                ) from error
        except BaseException:
            handle.close()
            raise
        self._handle = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()
