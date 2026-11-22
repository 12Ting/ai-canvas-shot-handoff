import hashlib
import io
import os
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module, util
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

import pytest


class FailingReadHandle(io.BytesIO):
    def read(self, _size: int = -1) -> bytes:
        raise OSError("injected hash read failure")


def test_open_range_reads_only_requested_bytes_from_validated_handle(tmp_path: Path) -> None:
    module_name = "ai_canvas.infrastructure.media.files"
    assert util.find_spec(module_name) is not None
    module = import_module(module_name)
    open_range = getattr(module, "open_range", None)
    assert callable(open_range)

    source = tmp_path / "source.bin"
    source.write_bytes(b"abcdef")

    assert b"".join(
        open_range(
            source,
            start=2,
            length=3,
            expected_size=6,
            trusted_root=tmp_path,
        )
    ) == b"cde"


def test_verified_external_lease_hashes_and_ranges_from_one_retained_handle(
    tmp_path: Path,
) -> None:
    from ai_canvas.infrastructure.media.files import SharedFileLease, UnsafeMediaFileError, open_range

    source = tmp_path / "external.bin"
    original = b"immutable-external-bytes"
    replacement_bytes = b"replacement-external!!!!"
    assert len(replacement_bytes) == len(original)
    source.write_bytes(original)
    metadata = source.stat()
    lease = SharedFileLease.acquire_verified(
        source,
        trusted_root=tmp_path,
        expected_size=len(original),
        expected_identity=f"{metadata.st_dev}:{metadata.st_ino}",
        expected_modified_at=datetime.fromtimestamp(metadata.st_mtime, timezone.utc),
        expected_sha256=hashlib.sha256(original).hexdigest(),
    )
    try:
        assert lease.verified_sha256 == hashlib.sha256(original).hexdigest()
        assert b"".join(
            open_range(
                source,
                start=2,
                length=4,
                expected_size=len(original),
                trusted_root=tmp_path,
                expected_identity=lease.file_identity,
                lease=lease,
            )
        ) == original[2:6]

        replacement = tmp_path / "replacement.bin"
        replacement.write_bytes(replacement_bytes)
        os.utime(replacement, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        try:
            os.replace(replacement, source)
        except OSError:
            assert os.name == "nt"
            assert b"".join(lease.read_range(start=0, length=len(original), chunk_bytes=4)) == original
        else:
            with pytest.raises(UnsafeMediaFileError, match="changed"):
                b"".join(lease.read_range(start=0, length=len(original), chunk_bytes=4))
    finally:
        lease.close()


def test_external_lease_blocks_platform_cooperative_writers(tmp_path: Path) -> None:
    from ai_canvas.infrastructure.media.files import SharedFileLease

    source = tmp_path / "locked.bin"
    content = b"locked-content"
    source.write_bytes(content)
    metadata = source.stat()
    lease = SharedFileLease.acquire_verified(
        source,
        trusted_root=tmp_path,
        expected_size=len(content),
        expected_identity=f"{metadata.st_dev}:{metadata.st_ino}",
        expected_modified_at=datetime.fromtimestamp(metadata.st_mtime, timezone.utc),
        expected_sha256=hashlib.sha256(content).hexdigest(),
    )
    try:
        if os.name == "nt":
            with pytest.raises(OSError):
                source.write_bytes(b"changed-value!")
        else:
            import fcntl

            with source.open("r+b") as writer:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(writer.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        lease.close()


def test_verified_external_lease_normalizes_hash_read_failure_and_closes_handle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_canvas.infrastructure.media import files

    source = tmp_path / "read-failure.bin"
    source.write_bytes(b"read failure")
    failing_handle = FailingReadHandle(source.read_bytes())

    def open_failing_handle(*_args, **_kwargs):
        return source.absolute(), failing_handle, source.stat()

    monkeypatch.setattr(files, "_open_validated_handle", open_failing_handle)

    with pytest.raises(files.UnsafeMediaFileError) as failure:
        files.SharedFileLease.acquire_verified(
            source,
            trusted_root=tmp_path,
            expected_size=source.stat().st_size,
            expected_identity="unused-by-injected-open",
            expected_modified_at=datetime.fromtimestamp(source.stat().st_mtime, timezone.utc),
            expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        )

    assert failure.value.health == "UNAVAILABLE"
    assert isinstance(failure.value.__cause__, OSError)
    assert failing_handle.closed


@pytest.mark.parametrize("operation", ("fstat", "fdopen"))
def test_validated_open_normalizes_descriptor_io_failures_and_closes_descriptor(
    tmp_path: Path,
    monkeypatch,
    operation: str,
) -> None:
    from ai_canvas.infrastructure.media import files

    source = tmp_path / f"{operation}-failure.bin"
    source.write_bytes(b"descriptor failure")
    opened_descriptors: list[int] = []
    real_fstat = os.fstat

    def open_descriptor(_candidate: Path) -> int:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        opened_descriptors.append(descriptor)
        return descriptor

    def fail_operation(*_args, **_kwargs):
        raise OSError(f"injected {operation} failure")

    monkeypatch.setattr(files, "_open_shared_read_descriptor", open_descriptor)
    monkeypatch.setattr(files.os, operation, fail_operation)

    try:
        with pytest.raises(files.UnsafeMediaFileError) as failure:
            files._open_validated_handle(
                source,
                trusted_root=tmp_path,
                expected_size=source.stat().st_size,
            )

        assert failure.value.health == "UNAVAILABLE"
        assert isinstance(failure.value.__cause__, OSError)
        assert len(opened_descriptors) == 1
        with pytest.raises(OSError):
            real_fstat(opened_descriptors[0])
    finally:
        for descriptor in opened_descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass


def test_pinned_range_survives_pre_iteration_retire_and_closes_after_release(
    tmp_path: Path,
) -> None:
    from ai_canvas.infrastructure.media.files import SharedFileLease

    source = tmp_path / "pinned.bin"
    content = b"pinned-content"
    source.write_bytes(content)
    metadata = source.stat()
    lease = SharedFileLease.acquire_verified(
        source,
        trusted_root=tmp_path,
        expected_size=len(content),
        expected_identity=f"{metadata.st_dev}:{metadata.st_ino}",
        expected_modified_at=datetime.fromtimestamp(metadata.st_mtime, timezone.utc),
        expected_sha256=hashlib.sha256(content).hexdigest(),
    )
    retained_handle = lease._source

    pin = lease.pin()
    lease.retire()

    assert not retained_handle.closed
    assert b"".join(pin.read_range(start=0, length=len(content), chunk_bytes=3)) == content
    assert retained_handle.closed
    with pytest.raises(OSError, match="retired"):
        lease.pin()


def test_retire_is_nonblocking_while_pinned_range_is_backpressured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_canvas.infrastructure.media import files

    source = tmp_path / "backpressured.bin"
    source.write_bytes(b"x")
    metadata = source.stat()
    lease = files.SharedFileLease.acquire_verified(
        source,
        trusted_root=tmp_path,
        expected_size=1,
        expected_identity=f"{metadata.st_dev}:{metadata.st_ino}",
        expected_modified_at=datetime.fromtimestamp(metadata.st_mtime, timezone.utc),
        expected_sha256=hashlib.sha256(b"x").hexdigest(),
    )
    retained_handle = lease._source
    pin = lease.pin()
    read_started = Event()
    release_read = Event()

    def blocking_chunks(*_args, **_kwargs):
        read_started.set()
        if not release_read.wait(timeout=2):
            raise AssertionError("test did not release pinned range")
        yield b"x"

    monkeypatch.setattr(files, "_read_chunks", blocking_chunks)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            read_future = executor.submit(
                lambda: b"".join(pin.read_range(start=0, length=1, chunk_bytes=1))
            )
            assert read_started.wait(timeout=1)
            retire_future = executor.submit(lease.retire)
            assert retire_future.result(timeout=0.25) is None
            assert not retained_handle.closed
            release_read.set()
            assert read_future.result(timeout=2) == b"x"
        assert retained_handle.closed
    finally:
        release_read.set()
        lease.retire()


@pytest.mark.parametrize("close_raises", (False, True))
@pytest.mark.skipif(os.name != "nt", reason="Windows CloseHandle contract")
def test_windows_open_osfhandle_failure_configures_closehandle_and_preserves_error(
    tmp_path: Path,
    monkeypatch,
    close_raises: bool,
) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    from ai_canvas.infrastructure.media import files

    class FakeFunction:
        def __init__(self, result: int) -> None:
            self.result = result
            self.argtypes = None
            self.restype = None
            self.calls: list[tuple[object, ...]] = []

        def __call__(self, *args):
            self.calls.append(args)
            if close_raises and self is kernel32.CloseHandle:
                raise OSError("injected CloseHandle failure")
            return self.result

    class FakeKernel32:
        def __init__(self) -> None:
            self.CreateFileW = FakeFunction(123)
            self.CloseHandle = FakeFunction(0)

    kernel32 = FakeKernel32()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32)

    def fail_open_osfhandle(*_args, **_kwargs):
        raise OSError("original open_osfhandle failure")

    monkeypatch.setattr(msvcrt, "open_osfhandle", fail_open_osfhandle)
    source = tmp_path / "windows-handle.bin"
    source.write_bytes(b"x")

    with pytest.raises(OSError, match="original open_osfhandle failure"):
        files._open_shared_read_descriptor(source)

    assert kernel32.CloseHandle.argtypes == (wintypes.HANDLE,)
    assert kernel32.CloseHandle.restype is wintypes.BOOL
    assert kernel32.CloseHandle.calls == [(123,)]
