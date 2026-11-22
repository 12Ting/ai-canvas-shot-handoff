from __future__ import annotations

import asyncio
import ctypes
import errno
import hashlib
import os
import re
import stat
import threading
import time
import unicodedata
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, TypeVar
from uuid import UUID, uuid4

from ai_canvas.contracts.artifact import (
    ArtifactImportConflictReason,
    ArtifactImportFailureCode,
    B1_MEDIA_MAX_BYTES,
)


DEFAULT_CHUNK_BYTES = 1024 * 1024
DEFAULT_IDLE_TIMEOUT_SECONDS = 30.0
DEFAULT_TOTAL_TIMEOUT_SECONDS = 900.0
DEFAULT_STAGING_CLEANUP_AGE_SECONDS = 3_600.0
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_STAGING_NAME_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.part\Z"
)
_PROMOTION_CANDIDATE_NAME_RE = re.compile(r"[0-9a-f]{32}\.candidate\Z")
_MAX_PROMOTION_CANDIDATE_ATTEMPTS = 16
_WINDOWS_INVALID_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_BASENAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
@dataclass
class _DigestLockEntry:
    lock: threading.Lock
    references: int = 0


_DIGEST_LOCKS: dict[str, _DigestLockEntry] = {}
_DIGEST_LOCKS_GUARD = threading.Lock()
_T = TypeVar("_T")


class ArtifactStoreError(RuntimeError):
    """Internal typed failure with a closed, externally safe classification."""

    failure_code: ArtifactImportFailureCode

    def __init__(self, message: str, failure_code: ArtifactImportFailureCode) -> None:
        self.failure_code = failure_code
        super().__init__(message)


class ArtifactStorageIOError(ArtifactStoreError):
    def __init__(self) -> None:
        super().__init__(
            "artifact storage is temporarily unavailable",
            ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
        )


class ArtifactStagingConflictError(RuntimeError):
    reason = ArtifactImportConflictReason.UPLOAD_IN_PROGRESS

    def __init__(self) -> None:
        super().__init__("an artifact upload is already in progress")


class UploadSizeError(ArtifactStoreError):
    def __init__(self, message: str = "artifact byte size does not match its declaration") -> None:
        super().__init__(message, ArtifactImportFailureCode.SIZE_MISMATCH)


class EmptyArtifactError(ArtifactStoreError):
    def __init__(self) -> None:
        super().__init__("artifact content is empty", ArtifactImportFailureCode.CORRUPT_MEDIA)


class StoredBlobIntegrityError(ArtifactStoreError):
    def __init__(self) -> None:
        super().__init__(
            "an existing content-addressed blob failed integrity verification",
            ArtifactImportFailureCode.CORRUPT_MEDIA,
        )


class UnsafeStagingPathError(ArtifactStoreError):
    def __init__(self) -> None:
        super().__init__(
            "the staging path is not a server-generated artifact path",
            ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
        )


class PreparedPromotionInvalidError(ArtifactStoreError):
    def __init__(self) -> None:
        super().__init__(
            "the prepared artifact promotion is no longer valid",
            ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
        )


class PreparedPromotionPartialError(ArtifactStoreError):
    """A private takeover completed but could not be durably reconciled."""

    def __init__(self) -> None:
        super().__init__(
            "artifact promotion ownership requires recovery",
            ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
        )


class UploadDeadlineExceeded(ArtifactStoreError):
    def __init__(self, kind: Literal["idle", "total"]) -> None:
        self.kind = kind
        super().__init__(
            f"artifact upload {kind} deadline exceeded",
            ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
        )


@dataclass(frozen=True, slots=True)
class StagedArtifact:
    import_id: UUID
    path: Path
    sha256: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    path: Path
    sha256: str
    byte_size: int
    reused: bool


_PROMOTION_ISSUER = object()


class PreparedPromotion:
    """Opaque, store-issued capability for one verified promotion attempt."""

    __slots__ = ("__weakref__",)

    def __new__(cls, issuer: object | None = None) -> PreparedPromotion:
        if issuer is not _PROMOTION_ISSUER:
            raise TypeError("prepared promotions can only be issued by ArtifactStore")
        return super().__new__(cls)


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int
    mode: int
    byte_size: int
    modified_ns: int
    changed_ns: int
    link_count: int
    file_attributes: int


@dataclass(frozen=True, slots=True)
class _PreparedPromotionRecord:
    store_token: object
    staged: StagedArtifact
    data_dir: Path
    candidate_root: Path
    candidate_path: Path
    destination: Path
    candidate_identity: _FileIdentity
    destination_identity: _FileIdentity | None
    finalizer: weakref.finalize


_PROMOTION_TICKETS: weakref.WeakKeyDictionary[
    PreparedPromotion,
    _PreparedPromotionRecord,
] = weakref.WeakKeyDictionary()
_PROMOTION_TICKETS_LOCK = threading.Lock()


class _StagingWriter:
    """Own one exact staging path and incrementally validate its byte stream."""

    def __init__(
        self,
        store: ArtifactStore,
        import_id: UUID,
        path: Path,
        declared_size: int | None,
        output: BinaryIO,
    ) -> None:
        self.store = store
        self.import_id = import_id
        self.path = path
        self.declared_size = declared_size
        self.output = output
        self.digest = hashlib.sha256()
        self.byte_size = 0

    def write(self, chunk: bytes | bytearray | memoryview) -> None:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("artifact chunks must be bytes-like")
        view = memoryview(chunk)
        for offset in range(0, len(view), self.store.chunk_size):
            piece = view[offset : offset + self.store.chunk_size]
            next_size = self.byte_size + len(piece)
            if next_size > self.store.max_bytes or (
                self.declared_size is not None and next_size > self.declared_size
            ):
                raise UploadSizeError("streamed artifact content exceeds its limit")
            self.output.write(piece)
            self.digest.update(piece)
            self.byte_size = next_size

    def finish(self) -> StagedArtifact:
        if self.byte_size == 0:
            raise EmptyArtifactError
        if self.declared_size is not None and self.byte_size != self.declared_size:
            raise UploadSizeError()
        self.output.flush()
        os.fsync(self.output.fileno())
        self.output.close()
        return StagedArtifact(
            self.import_id,
            self.path,
            self.digest.hexdigest(),
            self.byte_size,
        )

    def abort(self) -> None:
        try:
            if not self.output.closed:
                self.output.close()
        finally:
            self.store._unlink_exact_staging_path(self.path, import_id=self.import_id)


class UploadDeadline:
    """Tracks both no-progress and wall-clock deadlines for one upload."""

    def __init__(
        self,
        *,
        started_at: float | None = None,
        idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
        total_timeout_seconds: float = DEFAULT_TOTAL_TIMEOUT_SECONDS,
    ) -> None:
        if idle_timeout_seconds <= 0 or total_timeout_seconds <= 0:
            raise ValueError("upload deadlines must be positive")
        self.idle_timeout_seconds = float(idle_timeout_seconds)
        self.total_timeout_seconds = float(total_timeout_seconds)
        self.started_at = time.monotonic() if started_at is None else float(started_at)
        self.last_activity_at = self.started_at

    def note_chunk(self, *, now: float | None = None) -> None:
        observed = time.monotonic() if now is None else float(now)
        if observed < self.started_at:
            raise ValueError("upload activity cannot precede its start")
        self.last_activity_at = observed

    def check(self, *, now: float | None = None) -> None:
        observed = time.monotonic() if now is None else float(now)
        if observed - self.started_at >= self.total_timeout_seconds:
            raise UploadDeadlineExceeded("total")
        if observed - self.last_activity_at >= self.idle_timeout_seconds:
            raise UploadDeadlineExceeded("idle")

    def timeout_for_next_chunk(self, *, now: float | None = None) -> float:
        observed = time.monotonic() if now is None else float(now)
        self.check(now=observed)
        total_remaining = self.total_timeout_seconds - (observed - self.started_at)
        idle_remaining = self.idle_timeout_seconds - (observed - self.last_activity_at)
        return min(total_remaining, idle_remaining)


async def next_chunk_with_deadline(
    iterator: AsyncIterator[_T],
    deadline: UploadDeadline,
    *,
    now: Callable[[], float] = time.monotonic,
    wait_for: Callable[[Awaitable[_T], float], Awaitable[_T]] = asyncio.wait_for,
) -> _T:
    """Await one chunk without allowing either upload deadline to drift."""

    timeout = deadline.timeout_for_next_chunk(now=now())
    try:
        item = await wait_for(iterator.__anext__(), timeout)
    except asyncio.TimeoutError as error:
        observed = now()
        try:
            deadline.check(now=observed)
        except UploadDeadlineExceeded:
            raise
        kind: Literal["idle", "total"] = (
            "total"
            if deadline.total_timeout_seconds - (observed - deadline.started_at)
            <= deadline.idle_timeout_seconds - (observed - deadline.last_activity_at)
            else "idle"
        )
        raise UploadDeadlineExceeded(kind) from error
    deadline.note_chunk(now=now())
    return item


def sanitize_filename(untrusted: str, *, max_length: int = 255) -> str:
    """Return a display-only filename with path and Windows hazards removed."""

    if max_length < 1:
        raise ValueError("max_length must be positive")
    normalized = unicodedata.normalize("NFC", str(untrusted)).replace("\\", "/")
    component = normalized.rsplit("/", 1)[-1]
    safe_characters: list[str] = []
    for character in component:
        category = unicodedata.category(character)
        if category in {"Cc", "Cf"}:
            continue
        safe_characters.append(
            "_" if character in _WINDOWS_INVALID_FILENAME_CHARACTERS else character
        )
    safe = "".join(safe_characters).strip(" .")
    if not safe or safe in {".", ".."}:
        safe = "upload"
    basename = safe.split(".", 1)[0].rstrip(" .").upper()
    if basename in _WINDOWS_RESERVED_BASENAMES:
        safe = f"_{safe}"
    safe = safe[:max_length].rstrip(" .")
    return safe or "upload"


class ArtifactStore:
    def __init__(
        self,
        data_dir: Path,
        *,
        max_bytes: int = B1_MEDIA_MAX_BYTES,
        chunk_size: int = DEFAULT_CHUNK_BYTES,
    ) -> None:
        if max_bytes < 1 or chunk_size < 1:
            raise ValueError("artifact size limits must be positive")
        self.data_dir = _absolute_lexical(Path(data_dir))
        self.max_bytes = max_bytes
        self.chunk_size = chunk_size
        self.artifacts_root = self.data_dir / "artifacts"
        self.staging_root = self.artifacts_root / ".staging"
        self.promotion_root = self.artifacts_root / ".promotion"
        self.blob_root = self.artifacts_root / "blobs" / "sha256"
        self._promotion_store_token = object()

    def staging_path(self, import_id: UUID) -> Path:
        if not isinstance(import_id, UUID):
            raise TypeError("import_id must be a UUID")
        return self.staging_root / f"{import_id}.part"

    def blob_path(self, digest: str) -> Path:
        if _DIGEST_RE.fullmatch(digest) is None:
            raise ValueError("SHA-256 digest must be 64 lowercase hexadecimal characters")
        return self.blob_root / digest[:2] / digest[2:4] / digest

    def verify_blob(self, digest: str, expected_size: int) -> StoredArtifact:
        """Verify one DB-referenced CAS object without mutating it."""

        if expected_size < 1:
            raise ValueError("expected blob size must be positive")
        destination = self.blob_path(digest)
        with _digest_lock(destination):
            try:
                _require_trusted_directory_chain(self.data_dir, destination.parent)
                metadata = destination.lstat()
            except (FileNotFoundError, UnsafeStagingPathError) as error:
                raise StoredBlobIntegrityError from error
            except OSError as error:
                raise ArtifactStorageIOError from error
            if not stat.S_ISREG(metadata.st_mode) or _stat_is_reparse(metadata):
                raise StoredBlobIntegrityError
            if not _file_matches(
                destination,
                expected_digest=digest,
                expected_size=expected_size,
                chunk_size=self.chunk_size,
            ):
                raise StoredBlobIntegrityError
        return StoredArtifact(destination, digest, expected_size, reused=True)

    def read_blob(self, digest: str, expected_size: int) -> bytes:
        """Read one complete CAS object and return bytes only after verification."""

        if expected_size < 1:
            raise ValueError("expected blob size must be positive")
        destination = self.blob_path(digest)
        with _digest_lock(destination):
            try:
                _require_trusted_directory_chain(self.data_dir, destination.parent)
                metadata = destination.lstat()
            except (FileNotFoundError, UnsafeStagingPathError) as error:
                raise StoredBlobIntegrityError from error
            except OSError as error:
                raise ArtifactStorageIOError from error
            if not stat.S_ISREG(metadata.st_mode) or _stat_is_reparse(metadata):
                raise StoredBlobIntegrityError
            content = bytearray()
            computed = hashlib.sha256()
            try:
                with destination.open("rb") as source:
                    while chunk := source.read(self.chunk_size):
                        content.extend(chunk)
                        computed.update(chunk)
                        if len(content) > expected_size:
                            raise StoredBlobIntegrityError
            except StoredBlobIntegrityError:
                raise
            except OSError as error:
                raise ArtifactStorageIOError from error
            if len(content) != expected_size or computed.hexdigest() != digest:
                raise StoredBlobIntegrityError
            return bytes(content)

    def remove_blob(self, digest: str, expected_size: int) -> bool:
        """Remove exactly one verified CAS object; return false when it is absent."""

        if expected_size < 1:
            raise ValueError("expected blob size must be positive")
        destination = self.blob_path(digest)
        with _digest_lock(destination):
            try:
                if not _trusted_directory_chain_exists(self.data_dir, destination.parent):
                    return False
                metadata = destination.lstat()
            except FileNotFoundError:
                return False
            except UnsafeStagingPathError:
                raise
            except OSError as error:
                raise ArtifactStorageIOError from error
            if not stat.S_ISREG(metadata.st_mode) or _stat_is_reparse(metadata):
                raise StoredBlobIntegrityError
            if not _file_matches(
                destination,
                expected_digest=digest,
                expected_size=expected_size,
                chunk_size=self.chunk_size,
            ):
                raise StoredBlobIntegrityError
            try:
                destination.unlink()
                if os.name == "nt":
                    _fsync_directory_best_effort(destination.parent)
                else:
                    _fsync_directory_strict(destination.parent)
            except FileNotFoundError:
                return False
            except OSError as error:
                raise ArtifactStorageIOError from error
        return True

    def stage_chunks(
        self,
        import_id: UUID,
        chunks: Iterable[bytes],
        *,
        declared_size: int | None,
    ) -> StagedArtifact:
        writer = self._open_staging_writer(import_id, declared_size=declared_size)
        try:
            for chunk in chunks:
                writer.write(chunk)
            return writer.finish()
        except BaseException as error:
            try:
                writer.abort()
            except OSError as cleanup_error:
                raise ArtifactStorageIOError from cleanup_error
            if isinstance(error, OSError):
                raise ArtifactStorageIOError from error
            raise

    async def stage_async_chunks(
        self,
        import_id: UUID,
        chunks: AsyncIterator[bytes],
        *,
        declared_size: int | None,
        deadline: UploadDeadline,
    ) -> StagedArtifact:
        if not isinstance(deadline, UploadDeadline):
            raise TypeError("deadline must be an UploadDeadline")
        self._validate_declared_size(declared_size)
        iterator = chunks.__aiter__()
        opened_writer: _StagingWriter | None = None

        def open_writer() -> _StagingWriter:
            nonlocal opened_writer
            opened_writer = self._open_staging_writer(import_id, declared_size=declared_size)
            return opened_writer

        try:
            writer = await _run_in_worker(open_writer)
        except asyncio.CancelledError:
            if opened_writer is not None:
                try:
                    await _run_in_worker(opened_writer.abort)
                except BaseException:
                    pass
            raise
        try:
            deadline.check()
            while True:
                try:
                    chunk = await next_chunk_with_deadline(iterator, deadline)
                except StopAsyncIteration:
                    break
                await _run_in_worker(writer.write, chunk)
                deadline.check()
            staged = await _run_in_worker(writer.finish)
            deadline.check()
            return staged
        except BaseException as error:
            try:
                await _run_in_worker(writer.abort)
            except OSError as cleanup_error:
                raise ArtifactStorageIOError from cleanup_error
            if isinstance(error, OSError):
                raise ArtifactStorageIOError from error
            raise

    def promote(
        self,
        staged: StagedArtifact,
        *,
        after_promote: Callable[[Path], None] | None = None,
    ) -> StoredArtifact:
        staging_path = self._validated_staged_artifact_path(staged)
        destination = self.blob_path(staged.sha256)
        with _digest_lock(destination):
            try:
                _require_trusted_directory_chain(self.data_dir, staging_path.parent)
                if not _file_matches(
                    staging_path,
                    expected_digest=staged.sha256,
                    expected_size=staged.byte_size,
                    chunk_size=self.chunk_size,
                ):
                    raise StoredBlobIntegrityError
                _ensure_trusted_directory_chain(self.data_dir, destination.parent)
                if os.path.lexists(destination):
                    _require_trusted_directory_chain(self.data_dir, destination.parent)
                    if not _file_matches(
                        destination,
                        expected_digest=staged.sha256,
                        expected_size=staged.byte_size,
                        chunk_size=self.chunk_size,
                    ):
                        raise StoredBlobIntegrityError
                    self._discard_staging_after_durable_reuse(staged, destination)
                    return StoredArtifact(destination, staged.sha256, staged.byte_size, reused=True)
                _require_trusted_directory_chain(self.data_dir, staging_path.parent)
                _require_trusted_directory_chain(self.data_dir, destination.parent)
                try:
                    _atomic_promote(staging_path, destination)
                except FileExistsError:
                    if not _file_matches(
                        destination,
                        expected_digest=staged.sha256,
                        expected_size=staged.byte_size,
                        chunk_size=self.chunk_size,
                    ):
                        raise StoredBlobIntegrityError from None
                    self._discard_staging_after_durable_reuse(staged, destination)
                    return StoredArtifact(destination, staged.sha256, staged.byte_size, reused=True)
                _require_trusted_directory_chain(self.data_dir, destination.parent)
                if os.name == "nt":
                    _fsync_directory_best_effort(destination.parent)
                stored = StoredArtifact(destination, staged.sha256, staged.byte_size, reused=False)
            except StoredBlobIntegrityError:
                self.discard(staged)
                raise
            except OSError as error:
                raise ArtifactStorageIOError from error
        if after_promote is not None:
            try:
                after_promote(destination)
            except OSError as error:
                raise ArtifactStorageIOError from error
        return stored

    def prepare_promotion(self, staged: StagedArtifact) -> PreparedPromotion:
        """Take ownership, verify private bytes, and issue one opaque capability.

        The private candidate name is unique, so neither its takeover nor its
        full-file validation needs the per-digest publisher lock.  Keeping that
        expensive work outside the lock lets an already-sealed ticket publish
        while another upload of the same digest is still hashing.
        """

        staging_path = self._validated_staged_artifact_path(staged)
        destination = self.blob_path(staged.sha256)
        candidate_path: Path | None = None
        candidate_identity: _FileIdentity | None = None
        destination_identity: _FileIdentity | None = None
        try:
            _require_trusted_directory_chain(self.data_dir, staging_path.parent)
            self._prepare_promotion_root()
            for _attempt in range(_MAX_PROMOTION_CANDIDATE_ATTEMPTS):
                candidate_path = self.promotion_root / f"{uuid4().hex}.candidate"
                _require_trusted_directory_chain(self.data_dir, candidate_path.parent)
                try:
                    _atomic_takeover(staging_path, candidate_path)
                except OSError as error:
                    if _destination_exists_error(error):
                        candidate_path = None
                        continue
                    recovered_identity = _current_file_identity(
                        candidate_path,
                        invalid_error=PreparedPromotionPartialError,
                    )
                    if recovered_identity is not None:
                        raise PreparedPromotionPartialError from error
                    raise
                candidate_identity = _current_file_identity(
                    candidate_path,
                    invalid_error=StoredBlobIntegrityError,
                )
                if candidate_identity is None:
                    raise PreparedPromotionPartialError
                break
            else:
                raise ArtifactStorageIOError
        except BaseException as error:
            if isinstance(error, ArtifactStoreError):
                raise
            if isinstance(error, OSError):
                raise ArtifactStorageIOError from error
            raise

        assert candidate_path is not None
        assert candidate_identity is not None
        try:
            _sync_takeover_directory(candidate_path.parent)
            _sync_takeover_directory(staging_path.parent)
        except OSError as error:
            try:
                self._rollback_private_takeover(
                    staging_path=staging_path,
                    candidate_path=candidate_path,
                    candidate_identity=candidate_identity,
                )
            except PreparedPromotionPartialError as rollback_error:
                raise rollback_error from error
            raise ArtifactStorageIOError from error

        try:
            verified_identity = _verified_file_identity(
                candidate_path,
                expected_digest=staged.sha256,
                expected_size=staged.byte_size,
                chunk_size=self.chunk_size,
            )
            if verified_identity != candidate_identity:
                raise StoredBlobIntegrityError
            _ensure_trusted_directory_chain(self.data_dir, destination.parent)
            if os.path.lexists(destination):
                _require_trusted_directory_chain(self.data_dir, destination.parent)
                destination_identity = _verified_file_identity(
                    destination,
                    expected_digest=staged.sha256,
                    expected_size=staged.byte_size,
                    chunk_size=self.chunk_size,
                )
            else:
                destination_identity = None
        except BaseException as error:
            try:
                _unlink_promotion_candidate(
                    data_dir=self.data_dir,
                    candidate_root=self.promotion_root,
                    candidate_path=candidate_path,
                    expected_identity=candidate_identity,
                )
            except ArtifactStoreError as cleanup_error:
                raise cleanup_error from error
            if isinstance(error, ArtifactStoreError):
                raise
            if isinstance(error, OSError):
                raise ArtifactStorageIOError from error
            raise
        ticket = PreparedPromotion(_PROMOTION_ISSUER)
        finalizer = weakref.finalize(
            ticket,
            _finalize_promotion_candidate,
            self.data_dir,
            self.promotion_root,
            candidate_path,
            candidate_identity,
        )
        assert candidate_identity is not None
        record = _PreparedPromotionRecord(
            store_token=self._promotion_store_token,
            staged=staged,
            data_dir=self.data_dir,
            candidate_root=self.promotion_root,
            candidate_path=candidate_path,
            destination=destination,
            candidate_identity=candidate_identity,
            destination_identity=destination_identity,
            finalizer=finalizer,
        )
        with _PROMOTION_TICKETS_LOCK:
            _PROMOTION_TICKETS[ticket] = record
        return ticket

    def _rollback_private_takeover(
        self,
        *,
        staging_path: Path,
        candidate_path: Path,
        candidate_identity: _FileIdentity,
    ) -> None:
        """Restore the exact private file after a post-rename durability error.

        Failure is intentionally non-destructive: the unique candidate or the
        restored public staging file remains in an owned, accounted location.
        """

        try:
            current_candidate = _current_file_identity(
                candidate_path,
                invalid_error=PreparedPromotionPartialError,
            )
            if current_candidate != candidate_identity or os.path.lexists(staging_path):
                raise PreparedPromotionPartialError
            _atomic_takeover(candidate_path, staging_path)
            restored_identity = _current_file_identity(
                staging_path,
                invalid_error=PreparedPromotionPartialError,
            )
            if restored_identity is None or not _same_physical_file_identity(
                candidate_identity,
                restored_identity,
            ):
                raise PreparedPromotionPartialError
            _sync_takeover_directory(staging_path.parent)
            _sync_takeover_directory(candidate_path.parent)
        except PreparedPromotionPartialError:
            raise
        except (ArtifactStoreError, OSError) as error:
            raise PreparedPromotionPartialError from error

    def promote_verified(self, ticket: PreparedPromotion) -> StoredArtifact:
        """Consume one capability and publish using only O(1) identity checks.

        Every attempt consumes the capability. A failed attempt safely removes
        the private candidate when its recorded identity is still authoritative.
        """

        record = self._take_promotion_ticket(ticket)
        assert record is not None
        with _digest_lock(record.destination):
            try:
                _require_trusted_directory_chain(self.data_dir, record.candidate_path.parent)
                _require_trusted_directory_chain(self.data_dir, record.destination.parent)
                if (
                    _current_file_identity(record.candidate_path)
                    != record.candidate_identity
                ):
                    raise PreparedPromotionInvalidError
                current_destination = _current_file_identity(record.destination)
                if current_destination != record.destination_identity:
                    raise PreparedPromotionInvalidError
                if record.destination_identity is not None:
                    self._discard_candidate_after_durable_reuse(
                        record,
                    )
                    return StoredArtifact(
                        record.destination,
                        record.staged.sha256,
                        record.staged.byte_size,
                        reused=True,
                    )
                try:
                    _atomic_promote(record.candidate_path, record.destination)
                except FileExistsError as error:
                    raise PreparedPromotionInvalidError from error
                _require_trusted_directory_chain(self.data_dir, record.destination.parent)
                if os.name == "nt":
                    _fsync_directory_best_effort(record.destination.parent)
                return StoredArtifact(
                    record.destination,
                    record.staged.sha256,
                    record.staged.byte_size,
                    reused=False,
                )
            except ArtifactStoreError as error:
                try:
                    self._cleanup_consumed_candidate(record)
                except ArtifactStoreError as cleanup_error:
                    raise cleanup_error from error
                raise
            except OSError as error:
                try:
                    self._cleanup_consumed_candidate(record)
                except ArtifactStoreError as cleanup_error:
                    raise cleanup_error from error
                raise ArtifactStorageIOError from error

    def revoke_promotion(self, ticket: PreparedPromotion) -> bool:
        """Consume a prepared promotion and remove only its private candidate."""

        record = self._take_promotion_ticket(ticket, missing_ok=True)
        if record is None:
            return False
        self._cleanup_consumed_candidate(record)
        return True

    def _take_promotion_ticket(
        self,
        ticket: PreparedPromotion,
        *,
        missing_ok: bool = False,
    ) -> _PreparedPromotionRecord | None:
        if type(ticket) is not PreparedPromotion:
            raise PreparedPromotionInvalidError
        with _PROMOTION_TICKETS_LOCK:
            record = _PROMOTION_TICKETS.get(ticket)
        if record is None:
            if missing_ok:
                return None
            raise PreparedPromotionInvalidError
        if record.store_token is not self._promotion_store_token:
            raise PreparedPromotionInvalidError
        with _PROMOTION_TICKETS_LOCK:
            record = _PROMOTION_TICKETS.pop(ticket, None)
        if record is None:
            if missing_ok:
                return None
            raise PreparedPromotionInvalidError
        record.finalizer.detach()
        return record

    @staticmethod
    def _cleanup_consumed_candidate(record: _PreparedPromotionRecord) -> None:
        try:
            _unlink_promotion_candidate(
                data_dir=record.data_dir,
                candidate_root=record.candidate_root,
                candidate_path=record.candidate_path,
                expected_identity=record.candidate_identity,
            )
        except PreparedPromotionInvalidError as error:
            if os.name == "nt":
                raise
            candidate_identity = _current_file_identity(record.candidate_path)
            destination_identity = _current_file_identity(record.destination)
            if (
                candidate_identity is None
                or destination_identity is None
                or not _same_physical_file_identity(candidate_identity, destination_identity)
                or not _same_physical_file_identity(record.candidate_identity, candidate_identity)
            ):
                raise error
            try:
                _fsync_directory_strict(record.destination.parent)
                _unlink_promotion_candidate(
                    data_dir=record.data_dir,
                    candidate_root=record.candidate_root,
                    candidate_path=record.candidate_path,
                    expected_identity=record.candidate_identity,
                    allow_same_physical_identity=True,
                )
            except (ArtifactStoreError, OSError) as cleanup_error:
                raise PreparedPromotionPartialError from cleanup_error

    def discard(self, staged: StagedArtifact) -> None:
        path = self._validated_staged_artifact_path(staged, require_exists=False)
        self._unlink_exact_staging_path(path, import_id=staged.import_id)

    def _discard_staging_after_durable_reuse(
        self,
        staged: StagedArtifact,
        destination: Path,
    ) -> None:
        if os.name != "nt":
            _fsync_directory_strict(destination.parent)
        self.discard(staged)
        if os.name != "nt":
            _fsync_directory_strict(staged.path.parent)

    def _discard_candidate_after_durable_reuse(
        self,
        record: _PreparedPromotionRecord,
    ) -> None:
        if os.name != "nt":
            _fsync_directory_strict(record.destination.parent)
        self._cleanup_consumed_candidate(record)
        if os.name != "nt":
            _fsync_directory_strict(record.candidate_root)

    def cleanup_staging(
        self,
        *,
        max_age_seconds: float = DEFAULT_STAGING_CLEANUP_AGE_SECONDS,
        now: float | None = None,
    ) -> list[Path]:
        try:
            return self._cleanup_staging(max_age_seconds=max_age_seconds, now=now)
        except OSError as error:
            raise ArtifactStorageIOError from error

    def reconcile_promotion_candidates(self) -> tuple[Path, ...]:
        """Remove candidates abandoned before a process could publish or revoke them.

        This runs under the process-wide data-directory lock at application
        startup.  A POSIX publish may have linked a candidate into CAS before a
        durability error; when both names resolve to the same bytes, make the
        CAS directory durable before removing only the redundant candidate.
        """

        if not _trusted_directory_chain_exists(self.data_dir, self.promotion_root):
            return ()
        _require_trusted_directory_chain(self.data_dir, self.promotion_root)
        removed: list[Path] = []
        try:
            with os.scandir(self.promotion_root) as entries:
                candidates = sorted(
                    (entry for entry in entries if _PROMOTION_CANDIDATE_NAME_RE.fullmatch(entry.name)),
                    key=lambda entry: entry.name,
                )
        except OSError as error:
            raise ArtifactStorageIOError from error
        for entry in candidates:
            candidate_path = self.promotion_root / entry.name
            try:
                # Windows DirEntry metadata can omit identity fields that direct
                # lstat exposes, which would make a stable candidate look changed.
                metadata = candidate_path.lstat()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise ArtifactStorageIOError from error
            if not stat.S_ISREG(metadata.st_mode) or _stat_is_reparse(metadata):
                raise PreparedPromotionPartialError
            candidate_identity = _file_identity_from_metadata(metadata)
            digest = _stable_file_digest(candidate_path, candidate_identity, self.chunk_size)
            destination = self.blob_path(digest)
            destination_identity = _current_file_identity(destination)
            if (
                destination_identity is not None
                and _same_physical_file_identity(candidate_identity, destination_identity)
            ):
                _require_trusted_directory_chain(self.data_dir, destination.parent)
                if os.name != "nt":
                    try:
                        _fsync_directory_strict(destination.parent)
                    except OSError as error:
                        raise PreparedPromotionPartialError from error
                _unlink_promotion_candidate(
                    data_dir=self.data_dir,
                    candidate_root=self.promotion_root,
                    candidate_path=candidate_path,
                    expected_identity=candidate_identity,
                    allow_same_physical_identity=True,
                )
            else:
                _unlink_promotion_candidate(
                    data_dir=self.data_dir,
                    candidate_root=self.promotion_root,
                    candidate_path=candidate_path,
                    expected_identity=candidate_identity,
                )
            removed.append(candidate_path)
        return tuple(removed)

    def _cleanup_staging(
        self,
        *,
        max_age_seconds: float,
        now: float | None,
    ) -> list[Path]:
        if max_age_seconds <= 0:
            raise ValueError("staging cleanup age must be positive")
        if not _trusted_directory_chain_exists(self.data_dir, self.staging_root):
            return []
        cutoff = (time.time() if now is None else now) - max_age_seconds
        removed: list[Path] = []
        with os.scandir(self.staging_root) as entries:
            for entry in entries:
                if _STAGING_NAME_RE.fullmatch(entry.name) is None:
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if _stat_is_reparse(metadata) or metadata.st_mtime > cutoff:
                    continue
                path = self.staging_root / entry.name
                import_id = UUID(entry.name.removesuffix(".part"))
                self._unlink_exact_staging_path(path, import_id=import_id)
                removed.append(path)
        return sorted(removed, key=lambda item: item.name)

    def _prepare_staging_root(self) -> None:
        _ensure_trusted_directory_chain(self.data_dir, self.staging_root)

    def _prepare_promotion_root(self) -> None:
        _ensure_trusted_directory_chain(self.data_dir, self.promotion_root)
        if os.name != "nt":
            try:
                self.promotion_root.chmod(0o700)
            except OSError as error:
                raise ArtifactStorageIOError from error
        _require_trusted_directory_chain(self.data_dir, self.promotion_root)

    def _validate_declared_size(self, declared_size: int | None) -> None:
        if declared_size is not None and (declared_size < 1 or declared_size > self.max_bytes):
            raise UploadSizeError("declared artifact size is outside the configured limit")

    def _open_staging_writer(
        self,
        import_id: UUID,
        *,
        declared_size: int | None,
    ) -> _StagingWriter:
        self._validate_declared_size(declared_size)
        path = self.staging_path(import_id)
        self._prepare_staging_root()
        _require_trusted_directory_chain(self.data_dir, path.parent)
        try:
            output = path.open("xb")
        except FileExistsError:
            raise ArtifactStagingConflictError from None
        except OSError as error:
            raise ArtifactStorageIOError from error
        return _StagingWriter(self, import_id, path, declared_size, output)

    def _validated_staged_artifact_path(
        self,
        staged: StagedArtifact,
        *,
        require_exists: bool = True,
    ) -> Path:
        if not isinstance(staged.import_id, UUID):
            raise UnsafeStagingPathError
        expected = _absolute_lexical(self.staging_path(staged.import_id))
        supplied = _absolute_lexical(staged.path)
        if supplied != expected or _STAGING_NAME_RE.fullmatch(supplied.name) is None:
            raise UnsafeStagingPathError
        chain_exists = _trusted_directory_chain_exists(self.data_dir, supplied.parent)
        if require_exists and not chain_exists:
            raise UnsafeStagingPathError
        if not require_exists and not chain_exists:
            return supplied
        if require_exists:
            try:
                metadata = supplied.lstat()
            except FileNotFoundError as error:
                raise UnsafeStagingPathError from error
            except OSError as error:
                raise ArtifactStorageIOError from error
            if not stat.S_ISREG(metadata.st_mode) or _stat_is_reparse(metadata):
                raise UnsafeStagingPathError
        elif supplied.exists() and _is_symlink_or_reparse(supplied):
            raise UnsafeStagingPathError
        return supplied

    def _unlink_exact_staging_path(self, path: Path, *, import_id: UUID) -> None:
        expected = _absolute_lexical(self.staging_path(import_id))
        supplied = _absolute_lexical(path)
        if supplied != expected or _STAGING_NAME_RE.fullmatch(supplied.name) is None:
            raise UnsafeStagingPathError
        try:
            if not _trusted_directory_chain_exists(self.data_dir, supplied.parent):
                return
            metadata = supplied.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise ArtifactStorageIOError from error
        if not stat.S_ISREG(metadata.st_mode) or _stat_is_reparse(metadata):
            raise UnsafeStagingPathError
        try:
            supplied.unlink()
        except FileNotFoundError:
            return
        except OSError as error:
            raise ArtifactStorageIOError from error


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


async def _run_in_worker(operation: Callable[..., _T], *args: object) -> _T:
    worker = asyncio.create_task(asyncio.to_thread(operation, *args))
    cancellation: asyncio.CancelledError | None = None
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
        except BaseException:
            break
    if cancellation is not None:
        try:
            worker.result()
        except BaseException:
            pass
        raise cancellation
    return worker.result()


def _trusted_directory_paths(anchor: Path, target: Path) -> list[Path]:
    trusted_anchor = _absolute_lexical(anchor)
    trusted_target = _absolute_lexical(target)
    try:
        relative = trusted_target.relative_to(trusted_anchor)
    except ValueError as error:
        raise UnsafeStagingPathError from error
    paths = [trusted_anchor]
    current = trusted_anchor
    for component in relative.parts:
        current /= component
        paths.append(current)
    return paths


def _trusted_directory_chain_exists(anchor: Path, target: Path) -> bool:
    for directory in _trusted_directory_paths(anchor, target):
        try:
            metadata = directory.lstat()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise ArtifactStorageIOError from error
        if not stat.S_ISDIR(metadata.st_mode) or _stat_is_reparse(metadata):
            raise UnsafeStagingPathError
    return True


def _require_trusted_directory_chain(anchor: Path, target: Path) -> None:
    if not _trusted_directory_chain_exists(anchor, target):
        raise UnsafeStagingPathError


def _ensure_trusted_directory_chain(anchor: Path, target: Path) -> None:
    paths = _trusted_directory_paths(anchor, target)
    try:
        paths[0].mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ArtifactStorageIOError from error
    _require_trusted_directory_chain(paths[0], paths[0])
    for directory in paths[1:]:
        _require_trusted_directory_chain(paths[0], directory.parent)
        try:
            directory.mkdir(exist_ok=True)
        except OSError as error:
            raise ArtifactStorageIOError from error
        _require_trusted_directory_chain(paths[0], directory)


@contextmanager
def _digest_lock(destination: Path):
    key = os.path.normcase(os.fspath(_absolute_lexical(destination)))
    with _DIGEST_LOCKS_GUARD:
        entry = _DIGEST_LOCKS.get(key)
        if entry is None:
            entry = _DigestLockEntry(threading.Lock())
            _DIGEST_LOCKS[key] = entry
        entry.references += 1
    acquired = False
    try:
        entry.lock.acquire()
        acquired = True
        yield
    finally:
        if acquired:
            entry.lock.release()
        with _DIGEST_LOCKS_GUARD:
            entry.references -= 1
            if entry.references == 0 and _DIGEST_LOCKS.get(key) is entry:
                del _DIGEST_LOCKS[key]


def _file_matches(
    path: Path,
    *,
    expected_digest: str,
    expected_size: int,
    chunk_size: int,
) -> bool:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ArtifactStorageIOError from error
    if not stat.S_ISREG(metadata.st_mode) or _stat_is_reparse(metadata):
        raise ArtifactStorageIOError
    digest = hashlib.sha256()
    byte_size = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(chunk_size):
                byte_size += len(chunk)
                digest.update(chunk)
    except OSError as error:
        raise ArtifactStorageIOError from error
    return byte_size == expected_size and digest.hexdigest() == expected_digest


def _stable_file_digest(
    path: Path,
    expected_identity: _FileIdentity,
    chunk_size: int,
) -> str:
    current = _current_file_identity(path, invalid_error=PreparedPromotionPartialError)
    if current != expected_identity:
        raise PreparedPromotionPartialError
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(chunk_size):
                digest.update(chunk)
    except OSError as error:
        raise ArtifactStorageIOError from error
    if _current_file_identity(path, invalid_error=PreparedPromotionPartialError) != expected_identity:
        raise PreparedPromotionPartialError
    return digest.hexdigest()


def _verified_file_identity(
    path: Path,
    *,
    expected_digest: str,
    expected_size: int,
    chunk_size: int,
) -> _FileIdentity:
    before = _current_file_identity(path, invalid_error=StoredBlobIntegrityError)
    if before is None or not _file_matches(
        path,
        expected_digest=expected_digest,
        expected_size=expected_size,
        chunk_size=chunk_size,
    ):
        raise StoredBlobIntegrityError
    after = _current_file_identity(path, invalid_error=StoredBlobIntegrityError)
    if after is None or after != before:
        raise StoredBlobIntegrityError
    return after


def _current_file_identity(
    path: Path,
    *,
    invalid_error: type[ArtifactStoreError] = PreparedPromotionInvalidError,
) -> _FileIdentity | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or _stat_is_reparse(metadata):
        raise invalid_error
    return _file_identity_from_metadata(metadata)


def _file_identity_from_metadata(metadata: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=int(getattr(metadata, "st_dev", 0)),
        inode=int(getattr(metadata, "st_ino", 0)),
        mode=int(metadata.st_mode),
        byte_size=int(metadata.st_size),
        modified_ns=int(
            getattr(metadata, "st_mtime_ns", round(float(metadata.st_mtime) * 1_000_000_000))
        ),
        changed_ns=int(
            getattr(metadata, "st_ctime_ns", round(float(metadata.st_ctime) * 1_000_000_000))
        ),
        link_count=int(getattr(metadata, "st_nlink", 0)),
        file_attributes=int(getattr(metadata, "st_file_attributes", 0)),
    )


def _same_physical_file_identity(
    before: _FileIdentity,
    after: _FileIdentity,
) -> bool:
    if (
        before.byte_size != after.byte_size
        or stat.S_IFMT(before.mode) != stat.S_IFMT(after.mode)
        or before.file_attributes != after.file_attributes
    ):
        return False
    if before.inode and after.inode:
        return before.device == after.device and before.inode == after.inode
    # Some Windows filesystems do not expose a stable inode. The no-replace
    # rename itself still transfers the unique file; a later retry re-hashes it.
    return True


def _unlink_promotion_candidate(
    *,
    data_dir: Path,
    candidate_root: Path,
    candidate_path: Path,
    expected_identity: _FileIdentity,
    allow_same_physical_identity: bool = False,
) -> bool:
    trusted_data_dir = _absolute_lexical(data_dir)
    trusted_root = _absolute_lexical(candidate_root)
    supplied = _absolute_lexical(candidate_path)
    if (
        supplied.parent != trusted_root
        or _PROMOTION_CANDIDATE_NAME_RE.fullmatch(supplied.name) is None
    ):
        raise PreparedPromotionInvalidError
    if not _trusted_directory_chain_exists(trusted_data_dir, trusted_root):
        return False
    current_identity = _current_file_identity(supplied)
    if current_identity is None:
        return False
    if current_identity != expected_identity and not (
        allow_same_physical_identity
        and _same_physical_file_identity(current_identity, expected_identity)
    ):
        raise PreparedPromotionInvalidError
    try:
        supplied.unlink()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ArtifactStorageIOError from error
    if os.name != "nt":
        _fsync_directory_strict(trusted_root)
    return True


def _finalize_promotion_candidate(
    data_dir: Path,
    candidate_root: Path,
    candidate_path: Path,
    expected_identity: _FileIdentity,
) -> None:
    try:
        _unlink_promotion_candidate(
            data_dir=data_dir,
            candidate_root=candidate_root,
            candidate_path=candidate_path,
            expected_identity=expected_identity,
        )
    except (ArtifactStoreError, OSError):
        # Destructors cannot report failures. A changed or inaccessible candidate
        # remains under the owned promotion root and is counted as transient bytes.
        pass


def _destination_exists_error(error: OSError) -> bool:
    return (
        isinstance(error, FileExistsError)
        or error.errno == errno.EEXIST
        or getattr(error, "winerror", None) in {80, 183}
    )


def _atomic_takeover(source: Path, destination: Path) -> None:
    """Move public staging into a unique private name without copying bytes.

    On POSIX a private O_EXCL placeholder reserves the unpredictable name, then
    one atomic replace transfers ownership. The placeholder is removed only
    when its exact identity still matches after a pre-rename failure.
    """

    if os.name == "nt":
        _windows_move_no_replace(source, destination)
        return
    _posix_atomic_takeover(source, destination)


def _posix_atomic_takeover(source: Path, destination: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    placeholder_identity: _FileIdentity | None = None
    try:
        descriptor = os.open(destination, flags, 0o600)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or _stat_is_reparse(metadata):
            raise PreparedPromotionPartialError
        placeholder_identity = _file_identity_from_metadata(metadata)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        os.replace(source, destination)
    except BaseException as error:
        try:
            current_identity = _current_file_identity(
                destination,
                invalid_error=PreparedPromotionPartialError,
            )
            if current_identity == placeholder_identity:
                destination.unlink()
            elif current_identity is not None:
                raise PreparedPromotionPartialError
        except (ArtifactStoreError, OSError) as cleanup_error:
            raise PreparedPromotionPartialError from cleanup_error
        raise error


def _sync_takeover_directory(directory: Path) -> None:
    if os.name != "nt":
        _fsync_directory_strict(directory)


def _windows_move_no_replace(source: Path, destination: Path) -> None:
    move_file_ex = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    move_file_ex.restype = ctypes.c_int
    movefile_write_through = 0x00000008
    if not move_file_ex(str(source), str(destination), movefile_write_through):
        raise ctypes.WinError(ctypes.get_last_error())


def _atomic_promote(source: Path, destination: Path) -> None:
    """Publish without replacement on one volume; never copy artifact bytes."""

    if os.name != "nt":
        os.link(source, destination, follow_symlinks=False)
        _fsync_directory_strict(destination.parent)
        os.unlink(source)
        _fsync_directory_strict(source.parent)
        return
    _windows_move_no_replace(source, destination)


def _fsync_directory_strict(directory: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(directory, os.O_RDONLY)
        os.fsync(descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _fsync_directory_best_effort(directory: Path) -> None:
    try:
        _fsync_directory_strict(directory)
    except OSError:
        pass


def _stat_is_reparse(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & reparse_flag)


def _is_symlink_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ArtifactStorageIOError from error
    return stat.S_ISLNK(metadata.st_mode) or _stat_is_reparse(metadata)
