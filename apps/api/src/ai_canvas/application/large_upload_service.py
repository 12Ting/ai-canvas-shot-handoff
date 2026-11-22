from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.errors import ProjectNotFound
from ai_canvas.application.storage_lifecycle_service import StorageLifecycleService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.config import Settings
from ai_canvas.contracts.artifact import (
    B1_MEDIA_MAX_BYTES,
    ArtifactRevisionResponse,
)
from ai_canvas.contracts.media_lifecycle import (
    CreateLargeUploadRequest,
    LargeUploadSessionResponse,
)
from ai_canvas.domain.media.probe import MediaProbeError, MediaProbeResult, probe_image, probe_video
from ai_canvas.infrastructure.artifacts import ArtifactStore, ArtifactStoreError, StagedArtifact
from ai_canvas.infrastructure.db.models import (
    ArtifactRevisionRow,
    ArtifactRow,
    LargeUploadPartRow,
    LargeUploadSessionRow,
    ProjectRow,
    StoredBlobRow,
)
from ai_canvas.infrastructure.db.blob_lifecycle_repository import (
    BlobLifecycleConflict,
    BlobLifecycleRepository,
)

_CONTENT_RANGE_RE = re.compile(r"bytes (\d+)-(\d+)/(\d+)\Z")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class LargeUploadPartReceipt:
    upload_id: UUID
    part_number: int
    received_byte_size: int


class LargeUploadProtocolError(ValueError):
    pass


class LargeUploadNotFound(LookupError):
    def __init__(self, upload_id: str) -> None:
        self.upload_id = upload_id
        super().__init__(upload_id)


class LargeUploadPartConflict(RuntimeError):
    pass


class LargeUploadIncomplete(RuntimeError):
    pass


def expected_part_range(*, total_bytes: int, part_size: int, part_number: int) -> tuple[int, int]:
    if total_bytes < 1 or part_size < 1 or part_number < 0:
        raise ValueError("part range values must be positive except for part number zero")
    start = part_number * part_size
    if start >= total_bytes:
        raise ValueError("part number is outside the upload size")
    return start, min(total_bytes, start + part_size) - 1


class LargeUploadService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        settings: Settings,
        storage: StorageLifecycleService,
        write_gate: WriteGate | None = None,
        now: Callable[[], datetime] = utc_now,
        probe: Callable[[Literal["image", "video"], Path], MediaProbeResult] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.storage = storage
        self._write_gate = write_gate or WriteGate()
        self._now = now
        self._probe = probe or self._probe_staged

    def create(
        self,
        project_id: UUID,
        request: CreateLargeUploadRequest,
    ) -> LargeUploadSessionResponse:
        if request.expected_byte_size <= B1_MEDIA_MAX_BYTES:
            raise LargeUploadProtocolError("files at or below 64 MiB use the B1 import route")
        if request.expected_byte_size > self.settings.media_single_file_max_bytes:
            raise LargeUploadProtocolError("media file exceeds the configured single-file limit")
        with self.session_factory() as session:
            if session.get(ProjectRow, str(project_id)) is None:
                raise ProjectNotFound(str(project_id))
            existing = session.scalar(
                select(LargeUploadSessionRow).where(
                    LargeUploadSessionRow.source_project_id == str(project_id),
                    LargeUploadSessionRow.client_upload_id == str(request.client_upload_id),
                )
            )
            if existing is not None:
                return self._response(existing)
        reservation = self.storage.reserve(
            operation_type="UPLOAD",
            operation_id=str(request.client_upload_id),
            expected_added_bytes=request.expected_byte_size,
            ttl_seconds=self.settings.artifact_staging_cleanup_age_seconds,
        )
        now = self._now()
        try:
            duplicate: LargeUploadSessionResponse | None = None
            with self._write_gate, self.session_factory.begin() as session:
                existing = session.scalar(
                    select(LargeUploadSessionRow).where(
                        LargeUploadSessionRow.source_project_id == str(project_id),
                        LargeUploadSessionRow.client_upload_id == str(request.client_upload_id),
                    )
                )
                if existing is not None:
                    duplicate = self._response(existing)
                else:
                    row = LargeUploadSessionRow(
                        id=str(uuid4()),
                        source_project_id=str(project_id),
                        client_upload_id=str(request.client_upload_id),
                        replace_revision_id=(
                            str(request.replace_revision_id)
                            if request.replace_revision_id is not None
                            else None
                        ),
                        requested_media_kind=request.requested_media_kind,
                        original_filename=request.filename,
                        declared_content_type=request.declared_content_type,
                        expected_byte_size=request.expected_byte_size,
                        received_byte_size=0,
                        part_size=self.settings.media_large_upload_part_bytes,
                        staging_key=str(uuid4()),
                        state="CREATED",
                        computed_sha256=None,
                        artifact_revision_id=None,
                        reservation_id=reservation.id,
                        failure_code=None,
                        created_at=now,
                        updated_at=now,
                        expires_at=now + timedelta(seconds=self.settings.artifact_staging_cleanup_age_seconds),
                    )
                    session.add(row)
                    session.flush()
                    response = self._response(row)
            if duplicate is not None:
                self.storage.release(reservation.id)
                return duplicate
            return response
        except (OSError, SQLAlchemyError, ValueError):
            self.storage.release(reservation.id)
            raise

    def put_part(
        self,
        *,
        upload_id: UUID,
        part_number: int,
        content: bytes,
        content_range: str,
        content_sha256: str,
    ) -> LargeUploadPartReceipt:
        if not isinstance(content, bytes):
            raise LargeUploadProtocolError("part content must be bytes")
        digest = hashlib.sha256(content).hexdigest()
        if content_sha256 != digest:
            raise LargeUploadProtocolError("part digest does not match content")
        with self._write_gate, self.session_factory.begin() as session:
            row = session.get(LargeUploadSessionRow, str(upload_id))
            if row is None:
                raise LargeUploadNotFound(str(upload_id))
            self._assert_receiving(row)
            start, end, total = self._parse_content_range(content_range)
            expected_start, expected_end = expected_part_range(
                total_bytes=row.expected_byte_size,
                part_size=row.part_size,
                part_number=part_number,
            )
            if (total, start, end) != (row.expected_byte_size, expected_start, expected_end):
                raise LargeUploadProtocolError("content range does not match the fixed upload part window")
            if len(content) != expected_end - expected_start + 1:
                raise LargeUploadProtocolError("part size does not match its content range")
            existing = session.scalar(
                select(LargeUploadPartRow).where(
                    LargeUploadPartRow.upload_session_id == row.id,
                    LargeUploadPartRow.part_number == part_number,
                )
            )
            if existing is not None:
                if (
                    existing.sha256 == digest
                    and existing.start_byte == start
                    and existing.end_byte == end
                    and existing.byte_size == len(content)
                ):
                    return LargeUploadPartReceipt(
                        upload_id=upload_id,
                        part_number=part_number,
                        received_byte_size=row.received_byte_size,
                    )
                raise LargeUploadPartConflict("part number was already uploaded with different bytes")
            part_path = self._part_path(row.staging_key, part_number)
            try:
                part_path.parent.mkdir(parents=True, exist_ok=True)
                with part_path.open("xb") as destination:
                    destination.write(content)
                    destination.flush()
            except FileExistsError as error:
                raise LargeUploadPartConflict("part storage already exists") from error
            except OSError as error:
                raise LargeUploadProtocolError("part storage is unavailable") from error
            now = self._now()
            try:
                session.add(
                    LargeUploadPartRow(
                        id=str(uuid4()),
                        upload_session_id=row.id,
                        part_number=part_number,
                        start_byte=start,
                        end_byte=end,
                        byte_size=len(content),
                        sha256=digest,
                        stored_at=now,
                    )
                )
                row.state = "RECEIVING"
                row.received_byte_size += len(content)
                row.updated_at = now
                row.expires_at = now + timedelta(seconds=self.settings.artifact_staging_cleanup_age_seconds)
                session.flush()
            except SQLAlchemyError:
                part_path.unlink(missing_ok=True)
                raise
            return LargeUploadPartReceipt(
                upload_id=upload_id,
                part_number=part_number,
                received_byte_size=row.received_byte_size,
            )

    def complete(self, upload_id: UUID) -> LargeUploadSessionResponse:
        with self._write_gate, self.session_factory.begin() as session:
            row = session.get(LargeUploadSessionRow, str(upload_id))
            if row is None:
                raise LargeUploadNotFound(str(upload_id))
            if row.state == "COMPLETED":
                return self._completed_response(session, row)
            self._assert_receiving(row)
            parts = list(
                session.scalars(
                    select(LargeUploadPartRow)
                    .where(LargeUploadPartRow.upload_session_id == row.id)
                    .order_by(LargeUploadPartRow.part_number)
                )
            )
            self._assert_complete_coverage(row, parts)
            row.state = "FINALIZING"
            row.updated_at = self._now()
            session.flush()
            staging_key = row.staging_key
            requested_kind: Literal["image", "video"] = row.requested_media_kind  # type: ignore[assignment]
            expected_size = row.expected_byte_size
            reservation_id = row.reservation_id
        store = self._store()
        staged: StagedArtifact | None = None
        promotion_attempted = False
        ticket_owned = False
        try:
            staged = store.stage_chunks(
                upload_id,
                self._part_chunks(staging_key, parts),
                declared_size=expected_size,
            )
            probe = self._probe(requested_kind, staged.path)
            if probe.kind != requested_kind:
                raise LargeUploadProtocolError("probed media kind does not match the upload declaration")
            promotion_attempted = True
            promotion = store.prepare_promotion(staged)
            ticket_owned = True
            try:
                with self._write_gate:
                    with self.session_factory() as session:
                        row = session.get(LargeUploadSessionRow, str(upload_id))
                        if row is None or row.state != "FINALIZING":
                            raise LargeUploadProtocolError(
                                "upload session changed during finalization"
                            )
                        BlobLifecycleRepository.assert_ingest_reusable(
                            session.get(StoredBlobRow, staged.sha256),
                            verified_new_bytes=True,
                        )
                    store.promote_verified(promotion)
                    ticket_owned = False
                    with self.session_factory.begin() as session:
                        row = session.get(LargeUploadSessionRow, str(upload_id))
                        if row is None or row.state != "FINALIZING":
                            raise LargeUploadProtocolError(
                                "upload session changed during finalization"
                            )
                        blob = session.get(StoredBlobRow, staged.sha256)
                        metadata_json = probe.metadata.model_dump_json()
                        if blob is None:
                            blob = StoredBlobRow(
                                sha256=staged.sha256,
                                byte_size=staged.byte_size,
                                media_kind=probe.kind,
                                media_type=probe.media_type,
                                metadata_json=metadata_json,
                                created_at=self._now(),
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
                                or blob.media_kind != probe.kind
                                or blob.media_type != probe.media_type
                                or blob.metadata_json != metadata_json
                            )
                        ):
                            raise LargeUploadProtocolError(
                                "existing CAS blob metadata does not match the completed upload"
                            )
                        artifact = ArtifactRow(
                            id=str(uuid4()),
                            origin_project_id=row.source_project_id,
                            kind=probe.kind,
                            created_at=self._now(),
                        )
                        session.add(artifact)
                        session.flush()
                        revision = ArtifactRevisionRow(
                            id=str(uuid4()),
                            artifact_id=artifact.id,
                            sequence=1,
                            blob_sha256=blob.sha256,
                            storage_kind="MANAGED_BLOB",
                            media_health="AVAILABLE",
                            external_snapshot_id=None,
                            source_kind="large_upload",
                            source_project_id=row.source_project_id,
                            source_import_session_id=None,
                            display_filename=row.original_filename,
                            created_at=self._now(),
                        )
                        session.add(revision)
                        session.flush()
                        BlobLifecycleRepository().recompute_for_sha(session, blob.sha256)
                        row.state = "COMPLETED"
                        row.received_byte_size = staged.byte_size
                        row.computed_sha256 = staged.sha256
                        row.artifact_revision_id = revision.id
                        row.failure_code = None
                        row.updated_at = self._now()
                        session.flush()
                        response = self._response(
                            row,
                            self._revision_response(revision, artifact, blob, probe),
                        )
            finally:
                if ticket_owned:
                    try:
                        store.revoke_promotion(promotion)
                    finally:
                        ticket_owned = False
            if reservation_id is not None:
                self.storage.release(reservation_id)
            self._remove_part_directory(staging_key)
            return response
        except (
            ArtifactStoreError,
            BlobLifecycleConflict,
            LargeUploadProtocolError,
            MediaProbeError,
            OSError,
            SQLAlchemyError,
            ValueError,
        ):
            if staged is not None and not promotion_attempted:
                try:
                    store.discard(staged)
                except (ArtifactStoreError, OSError):
                    staged = None
            with self._write_gate, self.session_factory.begin() as session:
                row = session.get(LargeUploadSessionRow, str(upload_id))
                if row is not None and row.state == "FINALIZING":
                    row.state = "FAILED"
                    row.failure_code = "UPLOAD_FINALIZATION_FAILED"
                    row.updated_at = self._now()
                    reservation_id = row.reservation_id
                else:
                    reservation_id = None
            if reservation_id is not None:
                self.storage.release(reservation_id)
            raise

    def get(self, upload_id: UUID) -> LargeUploadSessionResponse:
        """Return the current session state for this page-lifetime upload."""

        with self.session_factory() as session:
            row = session.get(LargeUploadSessionRow, str(upload_id))
            if row is None:
                raise LargeUploadNotFound(str(upload_id))
            if row.state == "COMPLETED":
                return self._completed_response(session, row)
            return self._response(row)

    def abort(self, upload_id: UUID) -> LargeUploadSessionResponse:
        """Abort an unfinished session and synchronously release owned staging.

        A completed upload remains immutable, but a user may logically discard
        its resulting revision. Its bytes are then retained for the explicit
        B4 GC/quarantine workflow. A session that is already finalizing is
        left intact: completion owns the staged bytes at that point and the
        caller must observe its terminal state rather than racing it.
        """

        reservation_id: str | None = None
        with self._write_gate, self.session_factory.begin() as session:
            row = session.get(LargeUploadSessionRow, str(upload_id))
            if row is None:
                raise LargeUploadNotFound(str(upload_id))
            if row.state == "COMPLETED":
                if row.artifact_revision_id is None:
                    raise LargeUploadProtocolError("completed upload is missing its artifact revision")
                revision = session.get(ArtifactRevisionRow, row.artifact_revision_id)
                if revision is None:
                    raise LargeUploadProtocolError("completed upload revision is unavailable")
                revision.discarded_at = revision.discarded_at or self._now()
                assert revision.blob_sha256 is not None
                BlobLifecycleRepository().recompute_for_sha(session, revision.blob_sha256)
                return self._completed_response(session, row)
            if row.state == "FINALIZING":
                return self._response(row)
            if row.state == "ABORTED":
                return self._response(row)
            if row.state not in {"CREATED", "RECEIVING", "FAILED", "EXPIRED"}:
                raise LargeUploadProtocolError("upload session cannot be aborted in its current state")

            # The staging key is server generated; cleanup is restricted to its
            # exact directory below data_dir/large-upload-staging.
            self._remove_part_directory(row.staging_key)
            row.state = "ABORTED"
            row.failure_code = "UPLOAD_ABORTED"
            row.updated_at = self._now()
            reservation_id = row.reservation_id
            response = self._response(row)
        if reservation_id is not None:
            self.storage.release(reservation_id)
        return response

    def expire_sessions_after_startup(self) -> None:
        reservation_ids: list[str] = []
        staging_keys: list[str] = []
        now = self._now()
        with self._write_gate, self.session_factory.begin() as session:
            rows = list(
                session.scalars(
                    select(LargeUploadSessionRow).where(
                        LargeUploadSessionRow.state.in_(("CREATED", "RECEIVING", "FINALIZING"))
                    )
                )
            )
            for row in rows:
                row.state = "EXPIRED"
                row.failure_code = "UPLOAD_SESSION_EXPIRED"
                row.updated_at = now
                staging_keys.append(row.staging_key)
                if row.reservation_id is not None:
                    reservation_ids.append(row.reservation_id)
        for reservation_id in reservation_ids:
            self.storage.release(reservation_id)
        for staging_key in staging_keys:
            self._remove_part_directory(staging_key)

    def _assert_receiving(self, row: LargeUploadSessionRow) -> None:
        if row.state not in {"CREATED", "RECEIVING"}:
            raise LargeUploadProtocolError("upload session is not accepting parts")
        if as_utc(row.expires_at) <= as_utc(self._now()):
            row.state = "EXPIRED"
            row.failure_code = "UPLOAD_SESSION_EXPIRED"
            row.updated_at = self._now()
            raise LargeUploadProtocolError("upload session has expired and cannot resume")

    @staticmethod
    def _parse_content_range(value: str) -> tuple[int, int, int]:
        match = _CONTENT_RANGE_RE.fullmatch(value)
        if match is None:
            raise LargeUploadProtocolError("content range must use bytes start-end/total")
        start, end, total = (int(group) for group in match.groups())
        if end < start or total < 1 or end >= total:
            raise LargeUploadProtocolError("content range is invalid")
        return start, end, total

    def _part_path(self, staging_key: str, part_number: int) -> Path:
        if not staging_key or part_number < 0:
            raise LargeUploadProtocolError("staging key and part number are invalid")
        return self.settings.data_dir / "large-upload-staging" / staging_key / f"{part_number:010d}.part"

    def _store(self) -> ArtifactStore:
        return ArtifactStore(
            self.settings.data_dir,
            max_bytes=self.settings.media_single_file_max_bytes,
            chunk_size=self.settings.artifact_upload_chunk_bytes,
        )

    def _probe_staged(
        self,
        kind: Literal["image", "video"],
        path: Path,
    ) -> MediaProbeResult:
        store = self._store()
        if kind == "image":
            return probe_image(
                path,
                staging_root=store.staging_root,
                max_pixels=self.settings.artifact_image_max_pixels,
                max_side=self.settings.artifact_image_max_side,
            )
        return probe_video(
            path,
            staging_root=store.staging_root,
            ffprobe_path=self.settings.ffprobe_path,
            metadata_timeout=self.settings.artifact_probe_metadata_timeout_seconds,
            full_scan_timeout=self.settings.artifact_probe_full_scan_timeout_seconds,
            output_limit=self.settings.artifact_probe_output_limit_bytes,
            max_duration=self.settings.artifact_video_max_duration_seconds,
            max_side=self.settings.artifact_video_max_side,
            max_fps=self.settings.artifact_video_max_fps,
        )

    def _part_chunks(
        self,
        staging_key: str,
        parts: list[LargeUploadPartRow],
    ):
        for part in parts:
            path = self._part_path(staging_key, part.part_number)
            with path.open("rb") as source:
                remaining = part.byte_size
                digest = hashlib.sha256()
                while chunk := source.read(self.settings.artifact_upload_chunk_bytes):
                    remaining -= len(chunk)
                    digest.update(chunk)
                    yield chunk
                if remaining != 0 or digest.hexdigest() != part.sha256:
                    raise LargeUploadProtocolError("stored part bytes no longer match their database digest")

    @staticmethod
    def _assert_complete_coverage(
        row: LargeUploadSessionRow,
        parts: list[LargeUploadPartRow],
    ) -> None:
        next_start = 0
        for expected_number, part in enumerate(parts):
            if part.part_number != expected_number or part.start_byte != next_start:
                raise LargeUploadIncomplete("upload parts do not form a continuous sequence")
            expected_start, expected_end = expected_part_range(
                total_bytes=row.expected_byte_size,
                part_size=row.part_size,
                part_number=expected_number,
            )
            if (
                (part.start_byte, part.end_byte, part.byte_size)
                != (expected_start, expected_end, expected_end - expected_start + 1)
            ):
                raise LargeUploadIncomplete("upload part does not match the fixed range window")
            next_start = part.end_byte + 1
        if next_start != row.expected_byte_size or row.received_byte_size != row.expected_byte_size:
            raise LargeUploadIncomplete("upload is missing one or more parts")

    def _remove_part_directory(self, staging_key: str) -> None:
        root = self.settings.data_dir / "large-upload-staging"
        directory = self._part_path(staging_key, 0).parent
        if directory.parent != root or directory.is_symlink():
            raise LargeUploadProtocolError("large upload staging path is invalid")
        if not directory.exists():
            return
        shutil.rmtree(directory)

    @staticmethod
    def _revision_response(
        revision: ArtifactRevisionRow,
        artifact: ArtifactRow,
        blob: StoredBlobRow,
        probe: MediaProbeResult,
    ) -> ArtifactRevisionResponse:
        return ArtifactRevisionResponse(
            revision_id=UUID(revision.id),
            artifact_id=UUID(artifact.id),
            sequence=revision.sequence,
            media_kind=artifact.kind,
            media_type=blob.media_type,
            byte_size=blob.byte_size,
            display_filename=revision.display_filename,
            metadata=probe.metadata,
            source_kind=revision.source_kind,
            source_project_id=UUID(revision.source_project_id),
            origin_project_id=UUID(artifact.origin_project_id),
            created_at=revision.created_at,
        )

    def _completed_response(
        self,
        session: Session,
        row: LargeUploadSessionRow,
    ) -> LargeUploadSessionResponse:
        if row.artifact_revision_id is None:
            raise LargeUploadProtocolError("completed upload is missing its artifact revision")
        revision = session.get(ArtifactRevisionRow, row.artifact_revision_id)
        if revision is None or revision.blob_sha256 is None:
            raise LargeUploadProtocolError("completed upload revision is unavailable")
        artifact = session.get(ArtifactRow, revision.artifact_id)
        blob = session.get(StoredBlobRow, revision.blob_sha256)
        if artifact is None or blob is None:
            raise LargeUploadProtocolError("completed upload storage is unavailable")
        return self._response(
            row,
            ArtifactRevisionResponse(
                revision_id=UUID(revision.id),
                artifact_id=UUID(artifact.id),
                sequence=revision.sequence,
                media_kind=artifact.kind,
                media_type=blob.media_type,
                byte_size=blob.byte_size,
                display_filename=revision.display_filename,
                metadata=json.loads(blob.metadata_json),
                source_kind=revision.source_kind,
                source_project_id=UUID(revision.source_project_id),
                origin_project_id=UUID(artifact.origin_project_id),
                created_at=revision.created_at,
            ),
        )

    @staticmethod
    def _response(
        row: LargeUploadSessionRow,
        artifact_revision: ArtifactRevisionResponse | None = None,
    ) -> LargeUploadSessionResponse:
        return LargeUploadSessionResponse(
            upload_id=UUID(row.id),
            source_project_id=UUID(row.source_project_id),
            client_upload_id=UUID(row.client_upload_id),
            requested_media_kind=row.requested_media_kind,
            original_filename=row.original_filename,
            expected_byte_size=row.expected_byte_size,
            received_byte_size=row.received_byte_size,
            part_size=row.part_size,
            state=row.state,
            failure_code=row.failure_code,
            created_at=row.created_at,
            updated_at=row.updated_at,
            expires_at=row.expires_at,
            artifact_revision=artifact_revision,
        )
