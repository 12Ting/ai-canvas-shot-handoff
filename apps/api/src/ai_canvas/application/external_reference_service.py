from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.errors import ProjectNotFound
from ai_canvas.application.storage_lifecycle_service import StorageLifecycleService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.config import Settings
from ai_canvas.domain.media.external_reference import (
    ExternalFileObservation,
    ExternalRootGrant,
    ExternalRootUnavailable,
    ExternalRootViolation,
    create_external_root_grant,
    observe_granted_file,
)
from ai_canvas.domain.media.probe import MediaProbeResult, probe_image, probe_video
from ai_canvas.infrastructure.artifacts import (
    ArtifactStore,
    ArtifactStoreError,
    StagedArtifact,
    sanitize_filename,
)
from ai_canvas.infrastructure.db.models import (
    ArtifactRevisionRow,
    ArtifactRow,
    ExternalRootGrantRow,
    ExternalSnapshotRow,
    ProjectRow,
    StoredBlobRow,
)
from ai_canvas.infrastructure.db.blob_lifecycle_repository import (
    BlobLifecycleRepository,
)
from ai_canvas.infrastructure.media.files import SharedFileLease, UnsafeMediaFileError


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ExternalRootGrantResult:
    id: UUID
    display_name: str
    canonical_path: Path


@dataclass(frozen=True, slots=True)
class ExternalReferenceState:
    revision_id: UUID
    artifact_id: UUID
    health: str
    storage_kind: str
    snapshot_id: UUID


@dataclass(frozen=True, slots=True)
class ExternalStreamSource:
    path: Path
    byte_size: int
    media_type: str
    etag: str


@dataclass(frozen=True, slots=True)
class ExternalLeasedStreamSource:
    path: Path
    byte_size: int
    media_type: str
    etag: str
    file_identity: str
    trusted_root: Path
    lease: SharedFileLease
    capture: ExternalLeaseCapture


@dataclass(frozen=True, slots=True)
class ExternalLeaseCapture:
    revision_id: str
    snapshot_id: str
    root_grant_id: str
    subject: str
    root_path: Path
    root_volume_identity: str
    root_file_identity: str
    canonical_path: Path
    relative_path: str
    file_identity: str
    sha256: str
    byte_size: int
    modified_at: datetime
    media_kind: str
    media_type: str
    metadata_json: str
    confirmed_by_subject: str
    confirmed_at: datetime


@dataclass(frozen=True, slots=True)
class ManagedCopyResult:
    revision_id: UUID
    artifact_id: UUID
    storage_kind: str


ExternalProbe = Callable[[Path, Literal["image", "video"], Path], MediaProbeResult]


class ExternalReferenceService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        settings: Settings,
        storage: StorageLifecycleService | None = None,
        write_gate: WriteGate | None = None,
        probe: ExternalProbe | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self._storage = storage
        self._write_gate = write_gate or WriteGate()
        self._probe = probe or self._probe_external_file

    def grant_root(
        self,
        selected_root: Path,
        *,
        subject: str = "local",
        display_name: str | None = None,
    ) -> ExternalRootGrantResult:
        grant = create_external_root_grant(
            selected_root,
            subject=subject,
            display_name=display_name,
        )
        with self._write_gate, self.session_factory.begin() as session:
            existing = session.scalar(
                select(ExternalRootGrantRow).where(
                    ExternalRootGrantRow.subject == subject,
                    ExternalRootGrantRow.canonical_path == str(grant.canonical_path),
                    ExternalRootGrantRow.revoked_at.is_(None),
                )
            )
            if existing is None:
                session.add(
                    ExternalRootGrantRow(
                        id=str(grant.id),
                        subject=grant.subject,
                        display_name=grant.display_name,
                        canonical_path=str(grant.canonical_path),
                        volume_identity=grant.volume_identity,
                        root_file_identity=grant.root_file_identity,
                        created_at=grant.created_at,
                        revoked_at=None,
                    )
                )
                result = grant
            else:
                result = self._grant_from_row(existing)
        return ExternalRootGrantResult(
            id=result.id,
            display_name=result.display_name,
            canonical_path=result.canonical_path,
        )

    def create_reference(
        self,
        *,
        project_id: UUID,
        grant_id: UUID,
        relative_path: str,
        requested_media_kind: Literal["image", "video"],
        subject: str = "local",
    ) -> ExternalReferenceState:
        grant = self._active_grant(grant_id, subject=subject)
        observation = self._observe(grant, relative_path)
        probe = self._probe(observation.canonical_path, requested_media_kind, grant.canonical_path)
        if probe.kind != requested_media_kind:
            raise ExternalRootViolation("external file probe kind disagrees with its declaration")
        now = utc_now()
        with self._write_gate, self.session_factory.begin() as session:
            if session.get(ProjectRow, str(project_id)) is None:
                raise ProjectNotFound(str(project_id))
            grant_row = session.get(ExternalRootGrantRow, str(grant_id))
            if grant_row is None or grant_row.revoked_at is not None or grant_row.subject != subject:
                raise ExternalRootUnavailable("external root grant is no longer active")
            snapshot = self._snapshot_row(
                grant_id=grant_id,
                observation=observation,
                probe=probe,
                subject=subject,
                confirmed_at=now,
            )

            artifact = ArtifactRow(
                id=str(uuid4()),
                origin_project_id=str(project_id),
                kind=probe.kind,
                created_at=now,
            )
            revision = ArtifactRevisionRow(
                id=str(uuid4()),
                artifact_id=artifact.id,
                sequence=1,
                blob_sha256=None,
                storage_kind="EXTERNAL_REFERENCE",
                media_health="AVAILABLE",
                external_snapshot_id=snapshot.id,
                source_kind="external_reference",
                source_project_id=str(project_id),
                source_import_session_id=None,
                display_filename=sanitize_filename(Path(observation.relative_path).name),
                created_at=now,
            )
            session.add_all((snapshot, artifact))
            session.flush()
            session.add(revision)
            session.flush()
            return ExternalReferenceState(
                revision_id=UUID(revision.id),
                artifact_id=UUID(artifact.id),
                health=revision.media_health,
                storage_kind=revision.storage_kind,
                snapshot_id=UUID(snapshot.id),
            )

    def get_grant(self, grant_id: UUID, *, subject: str = "local") -> ExternalRootGrant:
        return self._active_grant(grant_id, subject=subject)

    def verify(self, revision_id: UUID, *, subject: str = "local") -> ExternalReferenceState:
        with self._write_gate, self.session_factory.begin() as session:
            revision = session.get(ArtifactRevisionRow, str(revision_id))
            if revision is None or revision.storage_kind != "EXTERNAL_REFERENCE":
                raise LookupError(str(revision_id))
            snapshot = (
                session.get(ExternalSnapshotRow, revision.external_snapshot_id)
                if revision.external_snapshot_id is not None
                else None
            )
            grant_row = session.get(ExternalRootGrantRow, snapshot.root_grant_id) if snapshot else None
            if snapshot is None or grant_row is None or grant_row.revoked_at is not None:
                revision.media_health = "UNAVAILABLE"
                return self._state(revision, snapshot)
            grant = self._grant_from_row(grant_row)
            try:
                observation = self._observe(grant, snapshot.relative_path)
            except (ExternalRootUnavailable, ExternalRootViolation):
                revision.media_health = "UNAVAILABLE"
                return self._state(revision, snapshot)
            if (
                observation.file_identity != snapshot.file_identity
                or observation.sha256 != snapshot.sha256
                or observation.byte_size != snapshot.byte_size
                or observation.modified_at != self._as_utc(snapshot.modified_at)
            ):
                revision.media_health = "DRIFTED"
            else:
                revision.media_health = "AVAILABLE"
            return self._state(revision, snapshot)

    def reconfirm(
        self,
        *,
        revision_id: UUID,
        grant_id: UUID,
        relative_path: str,
        subject: str = "local",
    ) -> ExternalReferenceState:
        grant = self._active_grant(grant_id, subject=subject)
        observation = self._observe(grant, relative_path)
        with self.session_factory() as session:
            previous = session.get(ArtifactRevisionRow, str(revision_id))
            artifact = session.get(ArtifactRow, previous.artifact_id) if previous is not None else None
            if (
                previous is None
                or previous.discarded_at is not None
                or previous.storage_kind != "EXTERNAL_REFERENCE"
                or artifact is None
            ):
                raise LookupError(str(revision_id))
            requested_media_kind: Literal["image", "video"] = artifact.kind  # type: ignore[assignment]
            source_project_id = previous.source_project_id
        probe = self._probe(observation.canonical_path, requested_media_kind, grant.canonical_path)
        if probe.kind != requested_media_kind:
            raise ExternalRootViolation("external file probe kind disagrees with the original artifact")
        now = utc_now()
        with self._write_gate, self.session_factory.begin() as session:
            previous = session.get(ArtifactRevisionRow, str(revision_id))
            artifact = session.get(ArtifactRow, previous.artifact_id) if previous is not None else None
            grant_row = session.get(ExternalRootGrantRow, str(grant_id))
            if (
                previous is None
                or previous.discarded_at is not None
                or previous.storage_kind != "EXTERNAL_REFERENCE"
                or artifact is None
                or grant_row is None
                or grant_row.revoked_at is not None
                or grant_row.subject != subject
            ):
                raise LookupError(str(revision_id))
            sequence = int(
                session.scalar(
                    select(func.coalesce(func.max(ArtifactRevisionRow.sequence), 0)).where(
                        ArtifactRevisionRow.artifact_id == artifact.id
                    )
                )
                or 0
            ) + 1
            snapshot = self._snapshot_row(
                grant_id=grant_id,
                observation=observation,
                probe=probe,
                subject=subject,
                confirmed_at=now,
            )
            session.add(snapshot)
            session.flush()
            revision = ArtifactRevisionRow(
                id=str(uuid4()),
                artifact_id=artifact.id,
                sequence=sequence,
                blob_sha256=None,
                storage_kind="EXTERNAL_REFERENCE",
                media_health="AVAILABLE",
                external_snapshot_id=snapshot.id,
                source_kind="external_reconfirm",
                source_project_id=source_project_id,
                source_import_session_id=None,
                display_filename=sanitize_filename(Path(observation.relative_path).name),
                created_at=now,
            )
            session.add(revision)
            session.flush()
            return self._state(revision, snapshot)

    def stream_source(self, revision_id: UUID, *, subject: str = "local") -> ExternalStreamSource:
        state = self.verify(revision_id, subject=subject)
        if state.health != "AVAILABLE":
            from ai_canvas.application.errors import MediaResourceUnavailable

            raise MediaResourceUnavailable(str(revision_id), state.health)
        with self.session_factory() as session:
            revision = session.get(ArtifactRevisionRow, str(revision_id))
            snapshot = (
                session.get(ExternalSnapshotRow, revision.external_snapshot_id)
                if revision is not None and revision.external_snapshot_id is not None
                else None
            )
            grant_row = session.get(ExternalRootGrantRow, snapshot.root_grant_id) if snapshot else None
            if revision is None or snapshot is None or grant_row is None:
                raise LookupError(str(revision_id))
            grant = self._grant_from_row(grant_row)
        try:
            observation = self._observe(grant, snapshot.relative_path)
        except (ExternalRootUnavailable, ExternalRootViolation) as error:
            raise MediaResourceUnavailable(str(revision_id), "UNAVAILABLE") from error
        if (
            observation.file_identity != snapshot.file_identity
            or observation.sha256 != snapshot.sha256
            or observation.byte_size != snapshot.byte_size
            or observation.modified_at != self._as_utc(snapshot.modified_at)
        ):
            raise MediaResourceUnavailable(str(revision_id), "DRIFTED")
        return ExternalStreamSource(
            path=observation.canonical_path,
            byte_size=observation.byte_size,
            media_type=snapshot.media_type,
            etag=observation.sha256,
        )

    def acquire_stream_lease(
        self,
        revision_id: UUID,
        *,
        subject: str = "local",
    ) -> ExternalLeasedStreamSource:
        from ai_canvas.application.errors import MediaResourceUnavailable

        capture = self._capture_external_lease(revision_id, subject=subject)
        lease: SharedFileLease | None = None
        failure: BaseException | None = None
        health = "UNAVAILABLE"
        try:
            self._validate_captured_root(capture)
            lease = SharedFileLease.acquire_verified(
                capture.canonical_path,
                trusted_root=capture.root_path,
                expected_size=capture.byte_size,
                expected_identity=capture.file_identity,
                expected_modified_at=capture.modified_at,
                expected_sha256=capture.sha256,
            )
            self._validate_captured_root(capture)
        except (ExternalRootUnavailable, ExternalRootViolation) as error:
            failure = error
        except UnsafeMediaFileError as error:
            failure = error
            health = error.health
        except BaseException:
            if lease is not None:
                lease.close()
            raise
        if failure is not None:
            if lease is not None:
                lease.close()
            self._persist_external_capture_health(capture, health=health)
            raise MediaResourceUnavailable(str(revision_id), health) from failure
        assert lease is not None
        if not self._accept_external_capture(capture):
            lease.close()
            raise MediaResourceUnavailable(str(revision_id), "UNAVAILABLE")
        return ExternalLeasedStreamSource(
            path=lease.path,
            byte_size=capture.byte_size,
            media_type=capture.media_type,
            etag=capture.sha256,
            file_identity=capture.file_identity,
            trusted_root=capture.root_path,
            lease=lease,
            capture=capture,
        )

    def stream_lease_capture_is_current(
        self,
        session: Session,
        source: ExternalLeasedStreamSource,
    ) -> bool:
        revision, snapshot, grant_row = self._reload_external_capture(
            session,
            source.capture,
        )
        return self._external_capture_matches(
            source.capture,
            revision=revision,
            snapshot=snapshot,
            grant_row=grant_row,
        )

    def _capture_external_lease(
        self,
        revision_id: UUID,
        *,
        subject: str,
    ) -> ExternalLeaseCapture:
        from ai_canvas.application.errors import MediaResourceUnavailable

        capture: ExternalLeaseCapture | None = None
        with self._write_gate, self.session_factory.begin() as session:
            revision = session.get(ArtifactRevisionRow, str(revision_id))
            if (
                revision is None
                or revision.discarded_at is not None
                or revision.storage_kind != "EXTERNAL_REFERENCE"
            ):
                raise LookupError(str(revision_id))
            snapshot = (
                session.get(ExternalSnapshotRow, revision.external_snapshot_id)
                if revision.external_snapshot_id is not None
                else None
            )
            grant_row = (
                session.get(ExternalRootGrantRow, snapshot.root_grant_id)
                if snapshot is not None
                else None
            )
            if (
                snapshot is None
                or grant_row is None
                or grant_row.revoked_at is not None
                or grant_row.subject != subject
            ):
                revision.media_health = "UNAVAILABLE"
            else:
                capture = ExternalLeaseCapture(
                    revision_id=revision.id,
                    snapshot_id=snapshot.id,
                    root_grant_id=grant_row.id,
                    subject=subject,
                    root_path=Path(grant_row.canonical_path),
                    root_volume_identity=grant_row.volume_identity,
                    root_file_identity=grant_row.root_file_identity,
                    canonical_path=Path(snapshot.canonical_path),
                    relative_path=snapshot.relative_path,
                    file_identity=snapshot.file_identity,
                    sha256=snapshot.sha256,
                    byte_size=snapshot.byte_size,
                    modified_at=self._as_utc(snapshot.modified_at),
                    media_kind=snapshot.media_kind,
                    media_type=snapshot.media_type,
                    metadata_json=snapshot.metadata_json,
                    confirmed_by_subject=snapshot.confirmed_by_subject,
                    confirmed_at=self._as_utc(snapshot.confirmed_at),
                )
        if capture is None:
            raise MediaResourceUnavailable(str(revision_id), "UNAVAILABLE")
        return capture

    @staticmethod
    def _validate_captured_root(capture: ExternalLeaseCapture) -> None:
        observed_root = create_external_root_grant(
            capture.root_path,
            subject=capture.subject,
        )
        if (
            observed_root.canonical_path != capture.root_path
            or observed_root.volume_identity != capture.root_volume_identity
            or observed_root.root_file_identity != capture.root_file_identity
        ):
            raise ExternalRootViolation("external root identity changed")

    def _accept_external_capture(self, capture: ExternalLeaseCapture) -> bool:
        with self._write_gate, self.session_factory.begin() as session:
            revision, snapshot, grant_row = self._reload_external_capture(
                session,
                capture,
            )
            if not self._external_capture_matches(
                capture,
                revision=revision,
                snapshot=snapshot,
                grant_row=grant_row,
            ):
                return False
            assert revision is not None
            revision.media_health = "AVAILABLE"
            return True

    def _persist_external_capture_health(
        self,
        capture: ExternalLeaseCapture,
        *,
        health: str,
    ) -> None:
        with self._write_gate, self.session_factory.begin() as session:
            revision, snapshot, grant_row = self._reload_external_capture(
                session,
                capture,
            )
            if self._external_capture_matches(
                capture,
                revision=revision,
                snapshot=snapshot,
                grant_row=grant_row,
            ):
                assert revision is not None
                revision.media_health = health

    @staticmethod
    def _reload_external_capture(
        session: Session,
        capture: ExternalLeaseCapture,
    ) -> tuple[
        ArtifactRevisionRow | None,
        ExternalSnapshotRow | None,
        ExternalRootGrantRow | None,
    ]:
        revision = session.get(ArtifactRevisionRow, capture.revision_id)
        snapshot = session.get(ExternalSnapshotRow, capture.snapshot_id)
        grant_row = session.get(ExternalRootGrantRow, capture.root_grant_id)
        return revision, snapshot, grant_row

    def _external_capture_matches(
        self,
        capture: ExternalLeaseCapture,
        *,
        revision: ArtifactRevisionRow | None,
        snapshot: ExternalSnapshotRow | None,
        grant_row: ExternalRootGrantRow | None,
    ) -> bool:
        return bool(
            revision is not None
            and revision.discarded_at is None
            and revision.storage_kind == "EXTERNAL_REFERENCE"
            and revision.external_snapshot_id == capture.snapshot_id
            and snapshot is not None
            and snapshot.id == capture.snapshot_id
            and snapshot.root_grant_id == capture.root_grant_id
            and Path(snapshot.canonical_path) == capture.canonical_path
            and snapshot.relative_path == capture.relative_path
            and snapshot.file_identity == capture.file_identity
            and snapshot.sha256 == capture.sha256
            and snapshot.byte_size == capture.byte_size
            and self._as_utc(snapshot.modified_at) == capture.modified_at
            and snapshot.media_kind == capture.media_kind
            and snapshot.media_type == capture.media_type
            and snapshot.metadata_json == capture.metadata_json
            and snapshot.confirmed_by_subject == capture.confirmed_by_subject
            and self._as_utc(snapshot.confirmed_at) == capture.confirmed_at
            and grant_row is not None
            and grant_row.id == capture.root_grant_id
            and grant_row.subject == capture.subject
            and grant_row.revoked_at is None
            and Path(grant_row.canonical_path) == capture.root_path
            and grant_row.volume_identity == capture.root_volume_identity
            and grant_row.root_file_identity == capture.root_file_identity
        )

    def import_copy(self, revision_id: UUID, *, subject: str = "local") -> ManagedCopyResult:
        source = self.stream_source(revision_id, subject=subject)
        with self.session_factory() as session:
            external_revision = session.get(ArtifactRevisionRow, str(revision_id))
            snapshot = (
                session.get(ExternalSnapshotRow, external_revision.external_snapshot_id)
                if external_revision is not None and external_revision.external_snapshot_id is not None
                else None
            )
            artifact = session.get(ArtifactRow, external_revision.artifact_id) if external_revision else None
            if external_revision is None or snapshot is None or artifact is None:
                raise LookupError(str(revision_id))
            source_project_id = external_revision.source_project_id
            metadata_json = snapshot.metadata_json
            media_kind = snapshot.media_kind
            media_type = snapshot.media_type
        store = ArtifactStore(
            self.settings.data_dir,
            max_bytes=self.settings.media_single_file_max_bytes,
            chunk_size=self.settings.artifact_upload_chunk_bytes,
        )
        copy_id = uuid4()
        reservation_id: str | None = None
        staged: StagedArtifact | None = None
        promotion_attempted = False
        promotion = None
        ticket_owned = False
        try:
            if self._storage is not None:
                reservation = self._storage.reserve(
                    operation_type="EXTERNAL_IMPORT_COPY",
                    operation_id=str(copy_id),
                    expected_added_bytes=source.byte_size,
                    ttl_seconds=self.settings.artifact_staging_cleanup_age_seconds,
                )
                reservation_id = reservation.id
            staged = store.stage_chunks(
                copy_id,
                self._file_chunks(source.path),
                declared_size=source.byte_size,
            )
            if staged.sha256 != source.etag or staged.byte_size != source.byte_size:
                raise ExternalRootViolation("external source changed while importing its managed copy")
            # Recheck the source immediately before publishing the managed copy.
            self.stream_source(revision_id, subject=subject)
            promotion_attempted = True
            promotion = store.prepare_promotion(staged)
            ticket_owned = True
            with self._write_gate:
                with self.session_factory() as session:
                    current_revision = session.get(ArtifactRevisionRow, str(revision_id))
                    current_artifact = (
                        session.get(ArtifactRow, current_revision.artifact_id)
                        if current_revision is not None
                        else None
                    )
                    if current_revision is None or current_artifact is None:
                        raise LookupError(str(revision_id))
                    BlobLifecycleRepository.assert_ingest_reusable(
                        session.get(StoredBlobRow, staged.sha256),
                        verified_new_bytes=True,
                    )
                store.promote_verified(promotion)
                ticket_owned = False
                now = utc_now()
                with self.session_factory.begin() as session:
                    external_revision = session.get(ArtifactRevisionRow, str(revision_id))
                    artifact = (
                        session.get(ArtifactRow, external_revision.artifact_id)
                        if external_revision is not None
                        else None
                    )
                    if external_revision is None or artifact is None:
                        raise LookupError(str(revision_id))
                    blob = session.get(StoredBlobRow, staged.sha256)
                    if blob is None:
                        blob = StoredBlobRow(
                            sha256=staged.sha256,
                            byte_size=staged.byte_size,
                            media_kind=media_kind,
                            media_type=media_type,
                            metadata_json=metadata_json,
                            created_at=now,
                        )
                        session.add(blob)
                        session.flush()
                    else:
                        BlobLifecycleRepository().reactivate_verified_purged_blob(
                            session,
                            staged.sha256,
                            verified_new_bytes=True,
                        )
                    if (
                        blob is not None
                        and (
                            blob.byte_size != staged.byte_size
                            or blob.media_kind != media_kind
                            or blob.media_type != media_type
                            or blob.metadata_json != metadata_json
                        )
                    ):
                        raise ExternalRootViolation(
                            "managed blob metadata conflicts with the external snapshot"
                        )
                    sequence = int(
                        session.scalar(
                            select(func.coalesce(func.max(ArtifactRevisionRow.sequence), 0)).where(
                                ArtifactRevisionRow.artifact_id == artifact.id
                            )
                        )
                        or 0
                    ) + 1
                    revision = ArtifactRevisionRow(
                        id=str(uuid4()),
                        artifact_id=artifact.id,
                        sequence=sequence,
                        blob_sha256=blob.sha256,
                        storage_kind="MANAGED_BLOB",
                        media_health="AVAILABLE",
                        external_snapshot_id=None,
                        source_kind="external_import_copy",
                        source_project_id=source_project_id,
                        source_import_session_id=None,
                        display_filename=sanitize_filename(source.path.name),
                        created_at=now,
                    )
                    session.add(revision)
                    session.flush()
                    BlobLifecycleRepository().recompute_for_sha(session, blob.sha256)
                    return ManagedCopyResult(
                        revision_id=UUID(revision.id),
                        artifact_id=UUID(artifact.id),
                        storage_kind=revision.storage_kind,
                    )
        except Exception:
            if staged is not None and not promotion_attempted:
                try:
                    store.discard(staged)
                except (ArtifactStoreError, OSError):
                    pass
            raise
        finally:
            if ticket_owned and promotion is not None:
                try:
                    store.revoke_promotion(promotion)
                finally:
                    ticket_owned = False
            if reservation_id is not None and self._storage is not None:
                self._storage.release(reservation_id)

    def _active_grant(self, grant_id: UUID, *, subject: str) -> ExternalRootGrant:
        with self.session_factory() as session:
            row = session.get(ExternalRootGrantRow, str(grant_id))
            if row is None or row.revoked_at is not None or row.subject != subject:
                raise ExternalRootUnavailable("external root grant is unavailable")
            return self._grant_from_row(row)

    def _observe(self, grant: ExternalRootGrant, relative_path: str) -> ExternalFileObservation:
        current = create_external_root_grant(
            grant.canonical_path,
            subject=grant.subject,
            display_name=grant.display_name,
        )
        if (
            current.volume_identity != grant.volume_identity
            or current.root_file_identity != grant.root_file_identity
        ):
            raise ExternalRootUnavailable("external root identity changed")
        return observe_granted_file(grant, relative_path)

    def _probe_external_file(
        self,
        path: Path,
        requested_kind: Literal["image", "video"],
        root: Path,
    ) -> MediaProbeResult:
        if requested_kind == "image":
            return probe_image(
                path,
                trusted_root=root,
                max_pixels=self.settings.artifact_image_max_pixels,
                max_side=self.settings.artifact_image_max_side,
            )
        return probe_video(
            path,
            trusted_root=root,
            ffprobe_path=self.settings.ffprobe_path,
            metadata_timeout=self.settings.artifact_probe_metadata_timeout_seconds,
            full_scan_timeout=self.settings.artifact_probe_full_scan_timeout_seconds,
            output_limit=self.settings.artifact_probe_output_limit_bytes,
            max_duration=self.settings.artifact_video_max_duration_seconds,
            max_side=self.settings.artifact_video_max_side,
            max_fps=self.settings.artifact_video_max_fps,
        )

    def _file_chunks(self, path: Path):
        try:
            with path.open("rb") as source:
                while chunk := source.read(self.settings.artifact_upload_chunk_bytes):
                    yield chunk
        except OSError as error:
            raise ExternalRootUnavailable("external file is unavailable") from error

    @staticmethod
    def _grant_from_row(row: ExternalRootGrantRow) -> ExternalRootGrant:
        return ExternalRootGrant(
            id=UUID(row.id),
            subject=row.subject,
            display_name=row.display_name,
            canonical_path=Path(row.canonical_path),
            volume_identity=row.volume_identity,
            root_file_identity=row.root_file_identity,
            created_at=ExternalReferenceService._as_utc(row.created_at),
        )

    @staticmethod
    def _snapshot_row(
        *,
        grant_id: UUID,
        observation: ExternalFileObservation,
        probe: MediaProbeResult,
        subject: str,
        confirmed_at: datetime,
    ) -> ExternalSnapshotRow:
        return ExternalSnapshotRow(
            id=str(uuid4()),
            root_grant_id=str(grant_id),
            canonical_path=str(observation.canonical_path),
            relative_path=observation.relative_path,
            file_identity=observation.file_identity,
            sha256=observation.sha256,
            byte_size=observation.byte_size,
            modified_at=observation.modified_at,
            media_kind=probe.kind,
            media_type=probe.media_type,
            metadata_json=probe.metadata.model_dump_json(),
            confirmed_by_subject=subject,
            confirmed_at=confirmed_at,
        )

    @staticmethod
    def _state(
        revision: ArtifactRevisionRow,
        snapshot: ExternalSnapshotRow | None,
    ) -> ExternalReferenceState:
        if snapshot is None or revision.external_snapshot_id is None:
            raise LookupError(revision.id)
        return ExternalReferenceState(
            revision_id=UUID(revision.id),
            artifact_id=UUID(revision.artifact_id),
            health=revision.media_health,
            storage_kind=revision.storage_kind,
            snapshot_id=UUID(snapshot.id),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
