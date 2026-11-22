from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Lock, Thread, current_thread
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.errors import (
    ArtifactRevisionNotFound,
    MediaResourceUnavailable,
    MediaSessionUnauthorized,
)
from ai_canvas.application.external_reference_service import (
    ExternalLeasedStreamSource,
    ExternalReferenceService,
)
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.config import Settings
from ai_canvas.infrastructure.artifacts import ArtifactStore
from ai_canvas.infrastructure.db.models import (
    ArtifactRevisionRow,
    ExternalRootGrantRow,
    ExternalSnapshotRow,
    MediaSessionGrantRow,
    MediaSessionRow,
    StoredBlobRow,
)
from ai_canvas.infrastructure.media.files import (
    SharedFileLease,
    SharedFileLeasePin,
    UnsafeMediaFileError,
)

MEDIA_SESSION_COOKIE = "ai_canvas_media_session"
MEDIA_SESSION_SUBJECT = "local"
MEDIA_SESSION_RESOURCE_KIND = "artifact_revision"

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class IssuedMediaSession:
    token: str
    revision_id: UUID
    idle_expires_at: datetime
    absolute_expires_at: datetime


@dataclass(frozen=True, slots=True)
class PreparedManagedRevision:
    revision_id: str
    artifact_id: str
    sequence: int
    blob_sha256: str
    media_health: str
    source_kind: str
    source_project_id: str | None
    source_import_session_id: str | None
    display_filename: str
    created_at: datetime
    blob_byte_size: int
    blob_media_kind: str
    blob_media_type: str
    blob_metadata_json: str


@dataclass(frozen=True, slots=True)
class MediaStreamResource:
    path: Path
    byte_size: int
    media_type: str
    etag: str
    trusted_root: Path
    expected_identity: str | None = None
    lease: SharedFileLeasePin | None = None

    def release(self) -> None:
        if self.lease is not None:
            self.lease.release()


class MediaStreamService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        settings: Settings,
        write_gate: WriteGate | None = None,
        now: Callable[[], datetime] = utc_now,
        external_references: ExternalReferenceService | None = None,
        cleanup_interval_seconds: float = 1.0,
    ) -> None:
        if cleanup_interval_seconds <= 0:
            raise ValueError("media lease cleanup interval must be positive")
        self.session_factory = session_factory
        self.settings = settings
        self._write_gate = write_gate or WriteGate()
        self._now = now
        self._external_references = external_references
        self._external_leases: dict[tuple[str, str], SharedFileLease] = {}
        self._lease_registry_lock = Lock()
        self._cleanup_interval_seconds = cleanup_interval_seconds
        self._cleanup_stop = Event()
        self._cleanup_thread: Thread | None = None
        self._cleanup_lifecycle_lock = Lock()

    def issue(
        self,
        revision_id: UUID,
        *,
        subject: str = MEDIA_SESSION_SUBJECT,
        existing_token: str | None = None,
    ) -> IssuedMediaSession:
        prepared = self._prepare_revision_for_issue(revision_id, subject=subject)
        now = self._now()
        prepared_external = prepared if isinstance(prepared, ExternalLeasedStreamSource) else None
        retired_leases: list[SharedFileLease] = []
        installed_external = False
        try:
            with self._write_gate:
                with self.session_factory.begin() as session:
                    self._revalidate_prepared_revision(
                        session,
                        revision_id=revision_id,
                        prepared=prepared,
                    )
                    media_session = self._reusable_session(
                        session,
                        subject=subject,
                        existing_token=existing_token,
                        now=now,
                    )
                    if media_session is None:
                        token = secrets.token_urlsafe(32)
                        absolute_expires_at = now + timedelta(
                            seconds=self.settings.media_session_absolute_seconds
                        )
                        idle_expires_at = min(
                            now + timedelta(seconds=self.settings.media_session_idle_seconds),
                            absolute_expires_at,
                        )
                        media_session = MediaSessionRow(
                            id=token,
                            subject=subject,
                            created_at=now,
                            last_used_at=now,
                            idle_expires_at=idle_expires_at,
                            absolute_expires_at=absolute_expires_at,
                            revoked_at=None,
                        )
                        session.add(media_session)
                        session.flush()
                    else:
                        token = media_session.id
                        absolute_expires_at = as_utc(media_session.absolute_expires_at)
                        idle_expires_at = min(
                            now + timedelta(seconds=self.settings.media_session_idle_seconds),
                            absolute_expires_at,
                        )
                        media_session.last_used_at = now
                        media_session.idle_expires_at = idle_expires_at

                    grant_key = (token, MEDIA_SESSION_RESOURCE_KIND, str(revision_id))
                    if session.get(MediaSessionGrantRow, grant_key) is None:
                        session.add(
                            MediaSessionGrantRow(
                                media_session_id=token,
                                resource_kind=MEDIA_SESSION_RESOURCE_KIND,
                                resource_id=str(revision_id),
                                granted_at=now,
                            )
                        )
                if prepared_external is not None:
                    previous = self._replace_external_lease(
                        token=token,
                        revision_id=revision_id,
                        lease=prepared_external.lease,
                    )
                    installed_external = True
                    if previous is not None:
                        retired_leases.append(previous)
        except BaseException:
            if prepared_external is not None and not installed_external:
                prepared_external.lease.retire()
            raise
        finally:
            for lease in retired_leases:
                lease.retire()
        return IssuedMediaSession(
            token=token,
            revision_id=revision_id,
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
        )

    def expire_sessions_after_startup(self) -> None:
        with self._write_gate, self.session_factory.begin() as session:
            session.execute(delete(MediaSessionRow))
        self._retire_leases(self._take_all_external_leases())

    def start_cleanup(self) -> None:
        with self._cleanup_lifecycle_lock:
            if self._cleanup_thread is not None and self._cleanup_thread.is_alive():
                return
            self._cleanup_stop.clear()
            self._cleanup_thread = Thread(
                target=self._cleanup_loop,
                name="media-external-lease-cleanup",
                daemon=True,
            )
            self._cleanup_thread.start()

    def close(self) -> None:
        with self._cleanup_lifecycle_lock:
            cleanup_thread = self._cleanup_thread
            self._cleanup_stop.set()
        if cleanup_thread is not None and cleanup_thread is not current_thread():
            cleanup_thread.join()
            if cleanup_thread.is_alive():
                raise RuntimeError("media cleanup thread did not terminate")
        with self._cleanup_lifecycle_lock:
            if self._cleanup_thread is cleanup_thread and (
                cleanup_thread is None or not cleanup_thread.is_alive()
            ):
                self._cleanup_thread = None
        self._retire_leases(self._take_all_external_leases())

    def prune_external_leases(self) -> None:
        now = self._now()
        with self._write_gate, self.session_factory.begin() as session:
            expired_tokens = self._delete_expired_sessions(session, now=now)
        retired = self._remove_external_leases(expired_tokens)
        self._retire_leases(retired)

    def _prune_external_leases_if_gate_available(self) -> None:
        expired_tokens: set[str] = set()
        with self._write_gate.try_acquire() as acquired:
            if not acquired or self._cleanup_stop.is_set():
                return
            now = self._now()
            if self._cleanup_stop.is_set():
                return
            with self.session_factory.begin() as session:
                expired_tokens = self._delete_expired_sessions(session, now=now)
        retired = self._remove_external_leases(expired_tokens)
        self._retire_leases(retired)

    @staticmethod
    def _delete_expired_sessions(session: Session, *, now: datetime) -> set[str]:
        expired_tokens = set(
            session.scalars(
                select(MediaSessionRow.id).where(
                    or_(
                        MediaSessionRow.revoked_at.is_not(None),
                        MediaSessionRow.idle_expires_at <= now,
                        MediaSessionRow.absolute_expires_at <= now,
                    )
                )
            )
        )
        if expired_tokens:
            session.execute(
                delete(MediaSessionRow).where(MediaSessionRow.id.in_(expired_tokens))
            )
        return expired_tokens

    def stream_resource(self, *, revision_id: UUID, token: str | None) -> MediaStreamResource:
        if not token:
            raise MediaSessionUnauthorized()
        now = self._now()
        external_pin: SharedFileLeasePin | None = None
        try:
            with self._write_gate, self.session_factory.begin() as session:
                media_session = session.get(MediaSessionRow, token)
                grant = session.get(
                    MediaSessionGrantRow,
                    (token, MEDIA_SESSION_RESOURCE_KIND, str(revision_id)),
                )
                if (
                    media_session is None
                    or media_session.subject != MEDIA_SESSION_SUBJECT
                    or media_session.revoked_at is not None
                    or as_utc(media_session.idle_expires_at) <= now
                    or as_utc(media_session.absolute_expires_at) <= now
                ):
                    raise MediaSessionUnauthorized()
                if grant is None:
                    raise MediaSessionUnauthorized()
                media_session.last_used_at = now
                media_session.idle_expires_at = min(
                    now + timedelta(seconds=self.settings.media_session_idle_seconds),
                    as_utc(media_session.absolute_expires_at),
                )
                revision = session.get(ArtifactRevisionRow, str(revision_id))
                if revision is None or revision.discarded_at is not None:
                    raise ArtifactRevisionNotFound(str(revision_id))

                external_values: tuple[SharedFileLeasePin, int, str, str, str, Path] | None = None
                if revision.storage_kind == "MANAGED_BLOB":
                    blob = self._managed_blob(session, revision_id)
                    digest = blob.sha256
                    byte_size = blob.byte_size
                    media_type = blob.media_type
                elif revision.storage_kind == "EXTERNAL_REFERENCE":
                    snapshot = (
                        session.get(ExternalSnapshotRow, revision.external_snapshot_id)
                        if revision.external_snapshot_id is not None
                        else None
                    )
                    root_grant = (
                        session.get(ExternalRootGrantRow, snapshot.root_grant_id)
                        if snapshot is not None
                        else None
                    )
                    with self._lease_registry_lock:
                        lease = self._external_leases.get((token, str(revision_id)))
                        if lease is not None:
                            try:
                                external_pin = lease.pin()
                            except UnsafeMediaFileError:
                                external_pin = None
                    if (
                        revision.media_health != "AVAILABLE"
                        or snapshot is None
                        or root_grant is None
                        or root_grant.revoked_at is not None
                        or root_grant.subject != media_session.subject
                        or external_pin is None
                    ):
                        raise MediaResourceUnavailable(str(revision_id), revision.media_health)
                    digest = None
                    byte_size = None
                    media_type = None
                    external_values = (
                        external_pin,
                        snapshot.byte_size,
                        snapshot.media_type,
                        snapshot.sha256,
                        snapshot.file_identity,
                        Path(root_grant.canonical_path),
                    )
                else:
                    raise MediaResourceUnavailable(str(revision_id), revision.media_health)
        except BaseException:
            if external_pin is not None:
                external_pin.release()
            raise

        if external_values is not None:
            lease, external_size, external_type, external_etag, identity, trusted_root = external_values
            return MediaStreamResource(
                path=lease.path,
                byte_size=external_size,
                media_type=external_type,
                etag=external_etag,
                trusted_root=trusted_root,
                expected_identity=identity,
                lease=lease,
            )

        assert digest is not None and byte_size is not None and media_type is not None
        store = ArtifactStore(
            self.settings.data_dir,
            max_bytes=self.settings.media_single_file_max_bytes,
            chunk_size=self.settings.artifact_upload_chunk_bytes,
        )
        return MediaStreamResource(
            path=store.blob_path(digest),
            byte_size=byte_size,
            media_type=media_type,
            etag=digest,
            trusted_root=store.blob_root,
        )

    def _prepare_revision_for_issue(
        self,
        revision_id: UUID,
        *,
        subject: str,
    ) -> PreparedManagedRevision | ExternalLeasedStreamSource:
        with self.session_factory() as session:
            revision = session.get(ArtifactRevisionRow, str(revision_id))
            if revision is None or revision.discarded_at is not None:
                raise ArtifactRevisionNotFound(str(revision_id))
            storage_kind = revision.storage_kind
            health = revision.media_health
            if storage_kind == "MANAGED_BLOB":
                blob = self._managed_blob(session, revision_id)
                prepared_managed = PreparedManagedRevision(
                    revision_id=revision.id,
                    artifact_id=revision.artifact_id,
                    sequence=revision.sequence,
                    blob_sha256=blob.sha256,
                    media_health=revision.media_health,
                    source_kind=revision.source_kind,
                    source_project_id=revision.source_project_id,
                    source_import_session_id=revision.source_import_session_id,
                    display_filename=revision.display_filename,
                    created_at=as_utc(revision.created_at),
                    blob_byte_size=blob.byte_size,
                    blob_media_kind=blob.media_kind,
                    blob_media_type=blob.media_type,
                    blob_metadata_json=blob.metadata_json,
                )
        if storage_kind == "EXTERNAL_REFERENCE":
            if self._external_references is None:
                raise MediaResourceUnavailable(str(revision_id), "UNAVAILABLE")
            source = self._external_references.acquire_stream_lease(
                revision_id,
                subject=subject,
            )
            if source.byte_size < 1:
                source.lease.retire()
                raise MediaResourceUnavailable(str(revision_id), "UNAVAILABLE")
            return source
        if storage_kind != "MANAGED_BLOB" or health != "AVAILABLE":
            raise MediaResourceUnavailable(str(revision_id), health)
        return prepared_managed

    def _revalidate_prepared_revision(
        self,
        session: Session,
        *,
        revision_id: UUID,
        prepared: PreparedManagedRevision | ExternalLeasedStreamSource,
    ) -> None:
        revision = session.get(ArtifactRevisionRow, str(revision_id))
        if revision is None or revision.discarded_at is not None:
            raise ArtifactRevisionNotFound(str(revision_id))
        if isinstance(prepared, ExternalLeasedStreamSource):
            if (
                self._external_references is None
                or not self._external_references.stream_lease_capture_is_current(
                    session,
                    prepared,
                )
            ):
                raise MediaResourceUnavailable(str(revision_id), revision.media_health)
            return

        blob = (
            session.get(StoredBlobRow, prepared.blob_sha256)
            if revision.blob_sha256 is not None
            else None
        )
        if not (
            revision.id == prepared.revision_id
            and revision.artifact_id == prepared.artifact_id
            and revision.sequence == prepared.sequence
            and revision.storage_kind == "MANAGED_BLOB"
            and revision.media_health == prepared.media_health == "AVAILABLE"
            and revision.blob_sha256 == prepared.blob_sha256
            and revision.external_snapshot_id is None
            and revision.source_kind == prepared.source_kind
            and revision.source_project_id == prepared.source_project_id
            and revision.source_import_session_id == prepared.source_import_session_id
            and revision.display_filename == prepared.display_filename
            and as_utc(revision.created_at) == prepared.created_at
            and blob is not None
            and blob.byte_size == prepared.blob_byte_size
            and blob.media_kind == prepared.blob_media_kind
            and blob.media_type == prepared.blob_media_type
            and blob.metadata_json == prepared.blob_metadata_json
        ):
            raise MediaResourceUnavailable(str(revision_id), revision.media_health)

    @staticmethod
    def _active(media_session: MediaSessionRow, *, subject: str, now: datetime) -> bool:
        return (
            media_session.subject == subject
            and media_session.revoked_at is None
            and as_utc(media_session.idle_expires_at) > now
            and as_utc(media_session.absolute_expires_at) > now
        )

    def _reusable_session(
        self,
        session: Session,
        *,
        subject: str,
        existing_token: str | None,
        now: datetime,
    ) -> MediaSessionRow | None:
        if existing_token is not None:
            existing = session.get(MediaSessionRow, existing_token)
            if existing is not None and self._active(existing, subject=subject, now=now):
                return existing
        candidates = session.scalars(
            select(MediaSessionRow)
            .where(
                MediaSessionRow.subject == subject,
                MediaSessionRow.revoked_at.is_(None),
                MediaSessionRow.idle_expires_at > now,
                MediaSessionRow.absolute_expires_at > now,
            )
            .order_by(MediaSessionRow.created_at.desc())
        )
        return candidates.first()

    def _replace_external_lease(
        self,
        *,
        token: str,
        revision_id: UUID,
        lease: SharedFileLease,
    ) -> SharedFileLease | None:
        key = (token, str(revision_id))
        with self._lease_registry_lock:
            previous = self._external_leases.get(key)
            self._external_leases[key] = lease
        return previous if previous is not lease else None

    def _remove_external_leases(self, tokens: set[str]) -> list[SharedFileLease]:
        with self._lease_registry_lock:
            keys = [key for key in self._external_leases if key[0] in tokens]
            return [self._external_leases.pop(key) for key in keys]

    def _take_all_external_leases(self) -> list[SharedFileLease]:
        with self._lease_registry_lock:
            leases = list(self._external_leases.values())
            self._external_leases.clear()
            return leases

    @staticmethod
    def _retire_leases(leases: list[SharedFileLease]) -> None:
        for lease in leases:
            lease.retire()

    def _cleanup_loop(self) -> None:
        try:
            while not self._cleanup_stop.wait(self._cleanup_interval_seconds):
                try:
                    self._prune_external_leases_if_gate_available()
                except Exception:
                    logger.exception("media external lease cleanup failed")
        finally:
            with self._cleanup_lifecycle_lock:
                if self._cleanup_thread is current_thread():
                    self._cleanup_thread = None

    @staticmethod
    def _managed_blob(session: Session, revision_id: UUID) -> StoredBlobRow:
        revision = session.get(ArtifactRevisionRow, str(revision_id))
        if revision is None or revision.discarded_at is not None:
            raise ArtifactRevisionNotFound(str(revision_id))
        if revision.media_health != "AVAILABLE" or revision.storage_kind != "MANAGED_BLOB":
            raise MediaResourceUnavailable(str(revision_id), revision.media_health)
        if revision.blob_sha256 is None:
            raise MediaResourceUnavailable(str(revision_id), revision.media_health)
        blob = session.get(StoredBlobRow, revision.blob_sha256)
        if blob is None:
            raise ArtifactRevisionNotFound(str(revision_id))
        return blob
