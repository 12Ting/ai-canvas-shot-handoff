from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import BinaryIO


class UnsafeMediaFileError(OSError):
    def __init__(self, message: str, *, health: str = "UNAVAILABLE") -> None:
        super().__init__(message)
        self.health = health


class BlobLocation(str, Enum):
    CAS_ONLY = "CAS_ONLY"
    QUARANTINE_ONLY = "QUARANTINE_ONLY"
    BOTH = "BOTH"
    NEITHER = "NEITHER"


def _file_identity(metadata: os.stat_result) -> str:
    return f"{metadata.st_dev}:{metadata.st_ino}"


def _is_reparse(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def _modified_at(metadata: os.stat_result) -> datetime:
    return datetime.fromtimestamp(metadata.st_mtime, timezone.utc)


def _safe_path_metadata(path: Path, trusted_root: Path) -> tuple[Path, os.stat_result]:
    try:
        root = trusted_root.resolve(strict=True)
        root_metadata = root.lstat()
    except OSError as error:
        raise UnsafeMediaFileError("trusted media root is unavailable") from error
    if not stat.S_ISDIR(root_metadata.st_mode) or _is_reparse(root_metadata):
        raise UnsafeMediaFileError("trusted media root must be a non-reparse directory")

    candidate = path.absolute()
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise UnsafeMediaFileError("media file escapes its trusted root") from error
    current = root
    metadata = root_metadata
    try:
        for part in relative.parts:
            current /= part
            metadata = current.lstat()
            if _is_reparse(metadata):
                raise UnsafeMediaFileError("media path contains a reparse point")
    except OSError as error:
        raise UnsafeMediaFileError("media file is unavailable") from error
    if not relative.parts or not stat.S_ISREG(metadata.st_mode):
        raise UnsafeMediaFileError("media source must be a regular file")
    return candidate, metadata


def _open_shared_read_descriptor(candidate: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        handle = create_file(
            str(candidate),
            0x80000000,  # GENERIC_READ
            0x00000001,  # FILE_SHARE_READ; deny write and delete while leased
            None,
            3,  # OPEN_EXISTING
            0x00000080 | 0x00200000,  # NORMAL | OPEN_REPARSE_POINT
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return msvcrt.open_osfhandle(int(handle), flags)
        except BaseException:
            try:
                if not close_handle(handle):
                    ctypes.get_last_error()
            except BaseException:
                pass
            raise

    descriptor = os.open(candidate, flags | getattr(os, "O_NOFOLLOW", 0))
    try:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _close_descriptor_quietly(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def _close_source_quietly(source: BinaryIO) -> None:
    try:
        source.close()
    except Exception:
        pass


def _open_validated_handle(
    path: Path,
    *,
    trusted_root: Path,
    expected_size: int,
    expected_identity: str | None = None,
    expected_modified_at: datetime | None = None,
) -> tuple[Path, BinaryIO, os.stat_result]:
    if expected_size < 1:
        raise ValueError("expected media size must be positive")
    candidate, path_metadata = _safe_path_metadata(path, trusted_root)
    try:
        descriptor = _open_shared_read_descriptor(candidate)
    except OSError as error:
        raise UnsafeMediaFileError("media file cannot be opened") from error
    try:
        handle_metadata = os.fstat(descriptor)
        identity = _file_identity(handle_metadata)
        if (
            not stat.S_ISREG(handle_metadata.st_mode)
            or identity != _file_identity(path_metadata)
            or handle_metadata.st_size != path_metadata.st_size
            or handle_metadata.st_size != expected_size
            or (expected_identity is not None and identity != expected_identity)
            or (
                expected_modified_at is not None
                and _modified_at(handle_metadata) != expected_modified_at.astimezone(timezone.utc)
            )
        ):
            raise UnsafeMediaFileError(
                "media file identity, size, or modification time changed",
                health="DRIFTED",
            )
        return candidate, os.fdopen(descriptor, "rb", closefd=True), handle_metadata
    except UnsafeMediaFileError:
        _close_descriptor_quietly(descriptor)
        raise
    except OSError as error:
        _close_descriptor_quietly(descriptor)
        raise UnsafeMediaFileError("media file handle cannot be inspected") from error
    except BaseException:
        _close_descriptor_quietly(descriptor)
        raise


def _read_chunks(
    source: BinaryIO,
    *,
    start: int,
    length: int,
    chunk_bytes: int,
) -> Iterator[bytes]:
    if start < 0 or length < 1 or chunk_bytes < 1:
        raise ValueError("range start, length, and chunk size are invalid")
    source.seek(start)
    remaining = length
    while remaining:
        chunk = source.read(min(chunk_bytes, remaining))
        if not chunk:
            raise UnsafeMediaFileError("media file ended before the requested range")
        remaining -= len(chunk)
        yield chunk


class SharedFileLease:
    def __init__(
        self,
        *,
        path: Path,
        trusted_root: Path,
        source: BinaryIO,
        metadata: os.stat_result,
        verified_sha256: str | None = None,
    ) -> None:
        self.path = path
        self.trusted_root = trusted_root
        self.byte_size = metadata.st_size
        self.file_identity = _file_identity(metadata)
        self.modified_at = _modified_at(metadata)
        self.verified_sha256 = verified_sha256
        self._source = source
        self._io_lock = Lock()
        self._state_lock = Lock()
        self._closed = False
        self._retired = False
        self._pin_count = 0

    @classmethod
    def acquire(
        cls,
        path: Path,
        *,
        trusted_root: Path,
        expected_size: int,
        expected_identity: str,
        expected_modified_at: datetime,
    ) -> "SharedFileLease":
        candidate, source, metadata = _open_validated_handle(
            path,
            trusted_root=trusted_root,
            expected_size=expected_size,
            expected_identity=expected_identity,
            expected_modified_at=expected_modified_at,
        )
        return cls(
            path=candidate,
            trusted_root=trusted_root,
            source=source,
            metadata=metadata,
        )

    @classmethod
    def acquire_verified(
        cls,
        path: Path,
        *,
        trusted_root: Path,
        expected_size: int,
        expected_identity: str,
        expected_modified_at: datetime,
        expected_sha256: str,
        hash_chunk_bytes: int = 1024 * 1024,
    ) -> "SharedFileLease":
        if hash_chunk_bytes < 1:
            raise ValueError("hash chunk size must be positive")
        candidate, source, metadata = _open_validated_handle(
            path,
            trusted_root=trusted_root,
            expected_size=expected_size,
            expected_identity=expected_identity,
            expected_modified_at=expected_modified_at,
        )
        try:
            digest = hashlib.sha256()
            observed_size = 0
            source.seek(0)
            while chunk := source.read(hash_chunk_bytes):
                observed_size += len(chunk)
                digest.update(chunk)
            verified_sha256 = digest.hexdigest()
            if observed_size != expected_size or verified_sha256 != expected_sha256:
                raise UnsafeMediaFileError(
                    "media file content changed",
                    health="DRIFTED",
                )
            source.seek(0)
            return cls(
                path=candidate,
                trusted_root=trusted_root,
                source=source,
                metadata=metadata,
                verified_sha256=verified_sha256,
            )
        except UnsafeMediaFileError:
            _close_source_quietly(source)
            raise
        except OSError as error:
            _close_source_quietly(source)
            raise UnsafeMediaFileError("media file cannot be fully observed") from error
        except BaseException:
            _close_source_quietly(source)
            raise

    def close(self) -> None:
        self.retire()

    def pin(self) -> "SharedFileLeasePin":
        with self._state_lock:
            if self._retired or self._closed:
                raise UnsafeMediaFileError("media file lease is retired")
            self._pin_count += 1
        return SharedFileLeasePin(self)

    def retire(self) -> None:
        close_source = False
        with self._state_lock:
            self._retired = True
            if self._pin_count == 0 and not self._closed:
                self._closed = True
                close_source = True
        if close_source:
            _close_source_quietly(self._source)

    def _release_pin(self) -> None:
        close_source = False
        with self._state_lock:
            if self._pin_count < 1:
                raise RuntimeError("media file lease pin count underflow")
            self._pin_count -= 1
            if self._retired and self._pin_count == 0 and not self._closed:
                self._closed = True
                close_source = True
        if close_source:
            _close_source_quietly(self._source)

    def _read_pinned_range(
        self,
        *,
        start: int,
        length: int,
        chunk_bytes: int,
    ) -> Iterator[bytes]:
        with self._io_lock:
            with self._state_lock:
                if self._closed:
                    raise UnsafeMediaFileError("media file lease is closed")
            _, path_metadata = _safe_path_metadata(self.path, self.trusted_root)
            handle_metadata = os.fstat(self._source.fileno())
            if (
                _file_identity(path_metadata) != self.file_identity
                or _file_identity(handle_metadata) != self.file_identity
                or path_metadata.st_size != self.byte_size
                or handle_metadata.st_size != self.byte_size
                or _modified_at(path_metadata) != self.modified_at
                or _modified_at(handle_metadata) != self.modified_at
            ):
                raise UnsafeMediaFileError("leased media file changed")
            yield from _read_chunks(
                self._source,
                start=start,
                length=length,
                chunk_bytes=chunk_bytes,
            )

    def read_range(
        self,
        *,
        start: int,
        length: int,
        chunk_bytes: int,
    ) -> Iterator[bytes]:
        pin = self.pin()
        try:
            return pin.read_range(
                start=start,
                length=length,
                chunk_bytes=chunk_bytes,
            )
        except BaseException:
            pin.release()
            raise


class SharedFileLeasePin:
    def __init__(self, lease: SharedFileLease) -> None:
        self._lease = lease
        self._lock = Lock()
        self._claimed = False
        self._streaming = False
        self._release_requested = False
        self._released = False

    @property
    def path(self) -> Path:
        return self._lease.path

    @property
    def trusted_root(self) -> Path:
        return self._lease.trusted_root

    @property
    def byte_size(self) -> int:
        return self._lease.byte_size

    @property
    def file_identity(self) -> str:
        return self._lease.file_identity

    @property
    def modified_at(self) -> datetime:
        return self._lease.modified_at

    @property
    def verified_sha256(self) -> str | None:
        return self._lease.verified_sha256

    def release(self) -> None:
        release_lease = False
        with self._lock:
            if self._released:
                return
            if self._streaming:
                self._release_requested = True
                return
            self._released = True
            release_lease = True
        if release_lease:
            self._lease._release_pin()

    def read_range(
        self,
        *,
        start: int,
        length: int,
        chunk_bytes: int,
    ) -> Iterator[bytes]:
        with self._lock:
            if self._released:
                raise UnsafeMediaFileError("media file lease pin is released")
            if self._claimed:
                raise RuntimeError("media file lease pin range was already claimed")
            self._claimed = True

        def chunks() -> Iterator[bytes]:
            with self._lock:
                if self._released:
                    raise UnsafeMediaFileError("media file lease pin is released")
                self._streaming = True
            try:
                yield from self._lease._read_pinned_range(
                    start=start,
                    length=length,
                    chunk_bytes=chunk_bytes,
                )
            finally:
                release_lease = False
                with self._lock:
                    self._streaming = False
                    if not self._released:
                        self._released = True
                        release_lease = True
                if release_lease:
                    self._lease._release_pin()

        return chunks()


def open_range(
    path: Path,
    *,
    start: int,
    length: int,
    expected_size: int,
    trusted_root: Path,
    expected_identity: str | None = None,
    lease: SharedFileLease | SharedFileLeasePin | None = None,
    chunk_bytes: int = 1024 * 1024,
) -> Iterator[bytes]:
    if start < 0 or length < 1 or start + length > expected_size:
        raise ValueError("requested range is outside the media file")
    if lease is not None:
        if (
            lease.path != path.absolute()
            or lease.byte_size != expected_size
            or (expected_identity is not None and lease.file_identity != expected_identity)
        ):
            raise UnsafeMediaFileError("media lease does not match the requested resource")
        return lease.read_range(start=start, length=length, chunk_bytes=chunk_bytes)

    _, source, _ = _open_validated_handle(
        path,
        trusted_root=trusted_root,
        expected_size=expected_size,
        expected_identity=expected_identity,
    )

    def chunks() -> Iterator[bytes]:
        with source:
            yield from _read_chunks(
                source,
                start=start,
                length=length,
                chunk_bytes=chunk_bytes,
            )

    return chunks()


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _validate_owned_path(
    path: Path,
    *,
    data_root: Path,
    require_file: bool | None,
) -> tuple[Path, os.stat_result | None]:
    root = _absolute_lexical(data_root)
    candidate = _absolute_lexical(path)
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise UnsafeMediaFileError("owned media path escapes the data root") from error
    if not relative.parts:
        raise UnsafeMediaFileError("owned media path cannot be the data root")
    current = root
    try:
        root_metadata = root.lstat()
    except OSError as error:
        raise UnsafeMediaFileError("data root is unavailable") from error
    if not stat.S_ISDIR(root_metadata.st_mode) or _is_reparse(root_metadata):
        raise UnsafeMediaFileError("data root must be a non-reparse directory")
    metadata: os.stat_result | None = root_metadata
    for index, component in enumerate(relative.parts):
        current /= component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            metadata = None
            if require_file is True:
                raise UnsafeMediaFileError("owned media file is unavailable") from None
            break
        except OSError as error:
            raise UnsafeMediaFileError("owned media path cannot be inspected") from error
        if _is_reparse(metadata):
            raise UnsafeMediaFileError("owned media path contains a reparse point")
        is_final = index == len(relative.parts) - 1
        if is_final:
            if require_file is True and not stat.S_ISREG(metadata.st_mode):
                raise UnsafeMediaFileError("owned media source must be a regular file")
            if require_file is False:
                raise UnsafeMediaFileError("owned media destination already exists")
        elif not stat.S_ISDIR(metadata.st_mode):
            raise UnsafeMediaFileError("owned media parent is not a directory")
    return candidate, metadata


def _ensure_owned_parent(path: Path, *, data_root: Path) -> Path:
    root = _absolute_lexical(data_root)
    candidate = _absolute_lexical(path)
    try:
        relative_parent = candidate.parent.relative_to(root)
    except ValueError as error:
        raise UnsafeMediaFileError("owned media destination escapes the data root") from error
    current = root
    _validate_owned_path(current / "_probe_missing", data_root=root, require_file=None)
    for component in relative_parent.parts:
        current /= component
        try:
            current.mkdir()
        except FileExistsError:
            pass
        except OSError as error:
            raise UnsafeMediaFileError("owned media destination parent cannot be created") from error
        try:
            metadata = current.lstat()
        except OSError as error:
            raise UnsafeMediaFileError("owned media destination parent cannot be inspected") from error
        if not stat.S_ISDIR(metadata.st_mode) or _is_reparse(metadata):
            raise UnsafeMediaFileError("owned media destination parent is unsafe")
    return candidate


def classify_blob_location(
    *,
    data_root: Path,
    cas_path: Path,
    quarantine_path: Path,
) -> BlobLocation:
    _validate_owned_path(cas_path, data_root=data_root, require_file=None)
    _validate_owned_path(quarantine_path, data_root=data_root, require_file=None)

    def present(path: Path) -> bool:
        try:
            _validate_owned_path(path, data_root=data_root, require_file=True)
        except UnsafeMediaFileError as error:
            if "unavailable" in str(error):
                return False
            raise
        return True

    cas_exists = present(cas_path)
    quarantine_exists = present(quarantine_path)
    if cas_exists and quarantine_exists:
        return BlobLocation.BOTH
    if cas_exists:
        return BlobLocation.CAS_ONLY
    if quarantine_exists:
        return BlobLocation.QUARANTINE_ONLY
    return BlobLocation.NEITHER


def _move_owned_blob(
    source: Path,
    destination: Path,
    *,
    data_root: Path,
) -> None:
    source_path, source_metadata = _validate_owned_path(
        source, data_root=data_root, require_file=True
    )
    assert source_metadata is not None
    destination_path = _ensure_owned_parent(destination, data_root=data_root)
    _validate_owned_path(destination_path, data_root=data_root, require_file=False)
    try:
        parent_metadata = destination_path.parent.stat()
    except OSError as error:
        raise UnsafeMediaFileError("owned media destination parent is unavailable") from error
    if source_metadata.st_dev != parent_metadata.st_dev:
        raise UnsafeMediaFileError("owned media lifecycle move must stay on one volume")
    try:
        os.replace(source_path, destination_path)
    except OSError as error:
        raise UnsafeMediaFileError("owned media lifecycle move failed") from error


def move_blob_to_quarantine(
    *,
    data_root: Path,
    cas_path: Path,
    quarantine_path: Path,
) -> None:
    if classify_blob_location(
        data_root=data_root,
        cas_path=cas_path,
        quarantine_path=quarantine_path,
    ) is not BlobLocation.CAS_ONLY:
        raise UnsafeMediaFileError("quarantine requires exactly one CAS source")
    _move_owned_blob(cas_path, quarantine_path, data_root=data_root)


def restore_blob_from_quarantine(
    *,
    data_root: Path,
    cas_path: Path,
    quarantine_path: Path,
) -> None:
    if classify_blob_location(
        data_root=data_root,
        cas_path=cas_path,
        quarantine_path=quarantine_path,
    ) is not BlobLocation.QUARANTINE_ONLY:
        raise UnsafeMediaFileError("restore requires exactly one quarantine source")
    _move_owned_blob(quarantine_path, cas_path, data_root=data_root)


def purge_quarantined_blob(
    *,
    data_root: Path,
    cas_path: Path,
    quarantine_path: Path,
) -> None:
    location = classify_blob_location(
        data_root=data_root,
        cas_path=cas_path,
        quarantine_path=quarantine_path,
    )
    if location is BlobLocation.NEITHER:
        return
    if location is not BlobLocation.QUARANTINE_ONLY:
        raise UnsafeMediaFileError("purge refuses a non-quarantined or duplicated blob")
    source, _metadata = _validate_owned_path(
        quarantine_path, data_root=data_root, require_file=True
    )
    try:
        source.unlink()
    except FileNotFoundError:
        return
    except OSError as error:
        raise UnsafeMediaFileError("owned quarantine purge failed") from error
