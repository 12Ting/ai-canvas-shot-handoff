from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.errors import ArtifactRevisionNotFound, MediaResourceUnavailable
from ai_canvas.application.storage_lifecycle_service import StorageLifecycleService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.config import Settings
from ai_canvas.domain.media.probe import MediaProbeError, MediaProbeResult, probe_video
from ai_canvas.infrastructure.artifacts import (
    ArtifactStore,
    ArtifactStoreError,
    PreparedPromotion,
    StagedArtifact,
)
from ai_canvas.infrastructure.db.models import (
    ArtifactRenditionRow,
    ArtifactRevisionRow,
    ExternalSnapshotRow,
    MaintenanceJobRow,
    StoredBlobRow,
)
from ai_canvas.infrastructure.db.blob_lifecycle_repository import (
    BlobLifecycleConflict,
    BlobLifecycleRepository,
)

if TYPE_CHECKING:
    from ai_canvas.application.external_reference_service import ExternalReferenceService

TranscodeProfile = Literal["preview_1080p", "preview_720p"]
_TRANSCODE_PENDING_RENDITION_STATE = "QUEUED"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class PreviewProfile:
    name: TranscodeProfile
    width: int
    height: int
    frames_per_second: float
    audio_sample_rate: int
    audio_bitrate_kbps: int
    estimated_output_bytes: int


class TranscodeConfirmationRequired(RuntimeError):
    def __init__(
        self,
        *,
        confirmation_token: str,
        profile: TranscodeProfile,
        width: int,
        height: int,
        estimated_output_bytes: int,
    ) -> None:
        self.confirmation_token = confirmation_token
        self.profile = profile
        self.width = width
        self.height = height
        self.estimated_output_bytes = estimated_output_bytes
        super().__init__("transcode requires explicit confirmation")


@dataclass(frozen=True, slots=True)
class TranscodeJobRequest:
    job_id: UUID
    rendition_id: UUID
    profile: PreviewProfile
    reused: bool


def build_preview_transcode_argv(
    *,
    executable: str,
    source_path: Path,
    destination_path: Path,
    profile: PreviewProfile,
) -> list[str]:
    """Build the only FFmpeg invocation accepted for preview renditions.

    The application never accepts user-provided FFmpeg flags.  Keeping this
    as an argv list also avoids a shell boundary for filenames and paths.
    """

    if not executable:
        raise ValueError("ffmpeg executable is required")
    if source_path == destination_path:
        raise ValueError("transcode source and destination must differ")
    return [
        executable,
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        f"scale={profile.width}:{profile.height}:force_original_aspect_ratio=decrease",
        "-r",
        f"{profile.frames_per_second:g}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        str(profile.audio_sample_rate),
        "-b:a",
        f"{profile.audio_bitrate_kbps}k",
        "-movflags",
        "+faststart",
        "-y",
        str(destination_path),
    ]


def resolve_preview_profile(
    profile: str,
    *,
    width: int,
    height: int,
    frames_per_second: float,
    duration_seconds: float,
) -> PreviewProfile:
    caps: dict[TranscodeProfile, tuple[int, int]] = {
        "preview_1080p": (1920, 1080),
        "preview_720p": (1280, 720),
    }
    if profile not in caps:
        raise ValueError("only preview_1080p and preview_720p are supported")
    if min(width, height, frames_per_second, duration_seconds) <= 0:
        raise ValueError("source media dimensions, fps, and duration must be positive")
    profile_name: TranscodeProfile = profile  # type: ignore[assignment]
    max_width, max_height = caps[profile_name]
    scale = min(1.0, max_width / width, max_height / height)
    output_width = max(1, int(width * scale))
    output_height = max(1, int(height * scale))
    if output_width > 2:
        output_width -= output_width % 2
    if output_height > 2:
        output_height -= output_height % 2
    output_fps = min(float(frames_per_second), 30.0)
    video_bitrate = 5_000_000 if profile_name == "preview_1080p" else 2_500_000
    estimated = math.ceil(duration_seconds * (video_bitrate + 192_000) / 8 * 1.2)
    return PreviewProfile(
        name=profile_name,
        width=output_width,
        height=output_height,
        frames_per_second=output_fps,
        audio_sample_rate=48_000,
        audio_bitrate_kbps=192,
        estimated_output_bytes=max(1, estimated),
    )


class TranscodeJobService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        settings: Settings | None = None,
        external_references: ExternalReferenceService | None = None,
        storage: StorageLifecycleService | None = None,
        write_gate: WriteGate | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self._external_references = external_references
        self._storage = storage
        self._write_gate = write_gate or WriteGate()
        self.ffmpeg_runner = self._run_ffmpeg
        self.ffmpeg_executable: str | None = None

    def request(
        self,
        revision_id: UUID,
        *,
        profile: str,
        confirmation_token: str | None,
    ) -> TranscodeJobRequest:
        source = self._source(revision_id)
        resolved = resolve_preview_profile(
            profile,
            width=source.width,
            height=source.height,
            frames_per_second=source.frames_per_second,
            duration_seconds=source.duration_seconds,
        )
        token = self._confirmation_token(revision_id, source.sha256, resolved)
        if confirmation_token != token:
            raise TranscodeConfirmationRequired(
                confirmation_token=token,
                profile=resolved.name,
                width=resolved.width,
                height=resolved.height,
                estimated_output_bytes=resolved.estimated_output_bytes,
            )
        operation_key = f"transcode:{revision_id}:{source.sha256}:{resolved.name}"
        now = utc_now()
        with self._write_gate, self.session_factory.begin() as session:
            existing = session.scalar(
                select(MaintenanceJobRow).where(MaintenanceJobRow.operation_key == operation_key)
            )
            rendition = session.scalar(
                select(ArtifactRenditionRow).where(
                    ArtifactRenditionRow.source_revision_id == str(revision_id),
                    ArtifactRenditionRow.profile == resolved.name,
                )
            )
            if existing is not None and rendition is not None:
                return TranscodeJobRequest(
                    job_id=UUID(existing.id),
                    rendition_id=UUID(rendition.id),
                    profile=resolved,
                    reused=True,
                )
            if existing is not None or rendition is not None:
                raise RuntimeError("transcode job and rendition persistence disagree")
            job = MaintenanceJobRow(
                id=str(uuid4()),
                job_type="TRANSCODE",
                operation_key=operation_key,
                payload_json=json.dumps(
                    {
                        "revision_id": str(revision_id),
                        "source_sha256": source.sha256,
                        "profile": resolved.name,
                        "width": resolved.width,
                        "height": resolved.height,
                        "frames_per_second": resolved.frames_per_second,
                        "estimated_output_bytes": resolved.estimated_output_bytes,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                state="QUEUED",
                attempt=0,
                progress=0.0,
                error_code=None,
                error_message=None,
                lease_owner=None,
                lease_expires_at=None,
                created_at=now,
                updated_at=now,
            )
            rendition = ArtifactRenditionRow(
                id=str(uuid4()),
                source_revision_id=str(revision_id),
                profile=resolved.name,
                blob_sha256=None,
                byte_size=None,
                media_type="video/mp4",
                metadata_json=None,
                state="QUEUED",
                job_id=job.id,
                created_at=now,
                updated_at=now,
                removed_at=None,
            )
            session.add_all((job, rendition))
            session.flush()
            return TranscodeJobRequest(
                job_id=UUID(job.id),
                rendition_id=UUID(rendition.id),
                profile=resolved,
                reused=False,
            )

    def execute(self, job_id: UUID | str) -> bool:
        """Run one queued rendition job and publish only a fully verified CAS result."""

        if self.settings is None:
            raise RuntimeError("transcode execution requires application settings")
        parsed_job_id = UUID(str(job_id))
        reservation_id: str | None = None
        with self._write_gate, self.session_factory.begin() as session:
            job = session.get(MaintenanceJobRow, str(parsed_job_id))
            if job is None or job.job_type != "TRANSCODE":
                raise LookupError(str(parsed_job_id))
            if job.state == "SUCCEEDED":
                return True
            if job.state not in {"QUEUED", "RUNNING"}:
                return False
            job.state = "RUNNING"
            job.progress = 0.05
            job.error_code = None
            job.error_message = None
            job.updated_at = utc_now()
            payload = json.loads(job.payload_json)
        try:
            revision_id = UUID(str(payload["revision_id"]))
            profile = resolve_preview_profile(
                str(payload["profile"]),
                width=int(payload["width"]),
                height=int(payload["height"]),
                frames_per_second=float(payload["frames_per_second"]),
                duration_seconds=self._source(revision_id).duration_seconds,
            )
            if self._storage is not None:
                reservation = self._storage.reserve(
                    operation_type="TRANSCODE",
                    operation_id=str(parsed_job_id),
                    expected_added_bytes=profile.estimated_output_bytes * 2,
                    ttl_seconds=max(60, self.settings.media_transcode_timeout_seconds + 60),
                )
                reservation_id = reservation.id
            source_path = self._source_path(revision_id)
            destination = self._temporary_output_path(parsed_job_id)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.ffmpeg_runner(
                build_preview_transcode_argv(
                    executable=self._ffmpeg_executable(),
                    source_path=source_path,
                    destination_path=destination,
                    profile=profile,
                ),
                timeout=self.settings.media_transcode_timeout_seconds,
            )
            probe = self._probe_preview(destination)
            store = self._store()
            staged = self._stage_preview(destination, store=store)
            promotion_attempted = False
            ticket_owned = False
            try:
                promotion_attempted = True
                promotion = store.prepare_promotion(staged)
                ticket_owned = True
                try:
                    try:
                        self._publish_execution(parsed_job_id, store, promotion, staged, probe)
                    finally:
                        # _publish_execution always promotes or revokes before it exits.
                        ticket_owned = False
                finally:
                    if ticket_owned:
                        try:
                            store.revoke_promotion(promotion)
                        finally:
                            ticket_owned = False
            except (ArtifactStoreError, RuntimeError, SQLAlchemyError):
                if not promotion_attempted:
                    store.discard(staged)
                raise
            return True
        except (
            ArtifactStoreError,
            MediaProbeError,
            OSError,
            RuntimeError,
            SQLAlchemyError,
            ValueError,
        ) as error:
            self._mark_execution_failed(parsed_job_id, error)
            return False
        finally:
            if reservation_id is not None and self._storage is not None:
                self._storage.release(reservation_id)
            try:
                self._temporary_output_path(parsed_job_id).unlink(missing_ok=True)
            except OSError:
                pass

    def _source_path(self, revision_id: UUID) -> Path:
        assert self.settings is not None
        with self.session_factory() as session:
            revision = session.get(ArtifactRevisionRow, str(revision_id))
            if revision is None or revision.discarded_at is not None:
                raise ArtifactRevisionNotFound(str(revision_id))
            if revision.media_health != "AVAILABLE":
                raise MediaResourceUnavailable(str(revision_id), revision.media_health)
            if revision.storage_kind == "MANAGED_BLOB" and revision.blob_sha256 is not None:
                blob = session.get(StoredBlobRow, revision.blob_sha256)
                if blob is None:
                    raise ArtifactRevisionNotFound(str(revision_id))
                digest = blob.sha256
                byte_size = blob.byte_size
            else:
                digest = None
                byte_size = None
        if digest is not None and byte_size is not None:
            return self._store().verify_blob(digest, byte_size).path
        if self._external_references is not None:
            return self._external_references.stream_source(revision_id).path
        raise MediaResourceUnavailable(str(revision_id), "UNAVAILABLE")

    def _temporary_output_path(self, job_id: UUID) -> Path:
        assert self.settings is not None
        return self.settings.data_dir / "artifacts" / ".transcode" / f"{job_id}.mp4"

    def _ffmpeg_executable(self) -> str:
        assert self.settings is not None
        if self.ffmpeg_executable is not None:
            return self.ffmpeg_executable
        configured = self.settings.ffmpeg_path
        if configured is not None:
            resolved = configured.resolve(strict=True)
            if not resolved.is_file():
                raise RuntimeError("configured ffmpeg path is not a file")
            return str(resolved)
        discovered = shutil.which("ffmpeg")
        if discovered is None:
            raise RuntimeError("ffmpeg is unavailable")
        return discovered

    @staticmethod
    def _run_ffmpeg(argv: list[str], *, timeout: float) -> None:
        try:
            completed = subprocess.run(
                argv,
                check=False,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError("ffmpeg execution failed") from error
        if completed.returncode != 0:
            raise RuntimeError("ffmpeg rejected the transcode request")

    def _probe_preview(self, destination: Path) -> MediaProbeResult:
        assert self.settings is not None
        return probe_video(
            destination,
            trusted_root=destination.parent,
            ffprobe_path=self.settings.ffprobe_path,
            metadata_timeout=self.settings.artifact_probe_metadata_timeout_seconds,
            full_scan_timeout=self.settings.artifact_probe_full_scan_timeout_seconds,
            output_limit=self.settings.artifact_probe_output_limit_bytes,
            max_duration=self.settings.artifact_video_max_duration_seconds,
            max_side=self.settings.artifact_video_max_side,
            max_fps=30,
        )

    def _stage_preview(
        self,
        destination: Path,
        *,
        store: ArtifactStore | None = None,
    ) -> StagedArtifact:
        try:
            byte_size = destination.stat().st_size
        except OSError as error:
            raise RuntimeError("ffmpeg did not produce a readable preview") from error
        if byte_size < 1:
            raise RuntimeError("ffmpeg produced an empty preview")
        artifact_store = store or self._store()
        return artifact_store.stage_chunks(
            uuid4(),
            self._file_chunks(destination),
            declared_size=byte_size,
        )

    def _publish_execution(
        self,
        job_id: UUID,
        store: ArtifactStore,
        promotion: PreparedPromotion,
        staged: StagedArtifact,
        probe: MediaProbeResult,
    ) -> None:
        with self._write_gate:
            try:
                with self.session_factory() as session:
                    job = session.get(MaintenanceJobRow, str(job_id))
                    rendition = session.scalar(
                        select(ArtifactRenditionRow).where(
                            ArtifactRenditionRow.job_id == str(job_id)
                        )
                    )
                    if job is None or job.state != "RUNNING":
                        raise RuntimeError("transcode job changed before publication")
                    if rendition is None or not self._rendition_is_publishable(rendition):
                        raise RuntimeError("transcode rendition changed before publication")
                    BlobLifecycleRepository.assert_ingest_reusable(
                        session.get(StoredBlobRow, staged.sha256),
                        verified_new_bytes=True,
                    )
                stored = store.promote_verified(promotion)
                now = utc_now()
                with self.session_factory.begin() as session:
                    job = session.get(MaintenanceJobRow, str(job_id))
                    rendition = session.scalar(
                        select(ArtifactRenditionRow).where(
                            ArtifactRenditionRow.job_id == str(job_id)
                        )
                    )
                    if job is None or job.state != "RUNNING":
                        raise RuntimeError("transcode job changed before publication")
                    if rendition is None or not self._rendition_is_publishable(rendition):
                        raise RuntimeError("transcode rendition changed before publication")
                    blob = session.get(StoredBlobRow, stored.sha256)
                    metadata_json = probe.metadata.model_dump_json()
                    if blob is None:
                        blob = StoredBlobRow(
                            sha256=stored.sha256,
                            byte_size=stored.byte_size,
                            media_kind="video",
                            media_type=probe.media_type,
                            metadata_json=metadata_json,
                            created_at=now,
                        )
                        session.add(blob)
                    else:
                        BlobLifecycleRepository().reactivate_verified_purged_blob(
                            session,
                            stored.sha256,
                            verified_new_bytes=True,
                        )
                    if (
                        blob is not None
                        and (
                            blob.byte_size != stored.byte_size
                            or blob.media_kind != "video"
                            or blob.media_type != probe.media_type
                            or blob.metadata_json != metadata_json
                        )
                    ):
                        raise RuntimeError(
                            "transcode output conflicts with an existing content-addressed blob"
                        )
                    rendition.blob_sha256 = stored.sha256
                    rendition.byte_size = stored.byte_size
                    rendition.media_type = probe.media_type
                    rendition.metadata_json = metadata_json
                    rendition.state = "READY"
                    rendition.updated_at = now
                    session.flush()
                    BlobLifecycleRepository().recompute_for_sha(session, stored.sha256)
                    job.state = "SUCCEEDED"
                    job.progress = 1.0
                    job.lease_owner = None
                    job.lease_expires_at = None
                    job.updated_at = now
            except BaseException as error:
                try:
                    store.revoke_promotion(promotion)
                except BaseException as cleanup_error:
                    raise cleanup_error from error
                raise

    def _mark_execution_failed(self, job_id: UUID, error: Exception) -> None:
        with self._write_gate, self.session_factory.begin() as session:
            job = session.get(MaintenanceJobRow, str(job_id))
            rendition = session.scalar(select(ArtifactRenditionRow).where(ArtifactRenditionRow.job_id == str(job_id)))
            if job is None or job.state == "SUCCEEDED":
                return
            job.state = "FAILED"
            job.error_code = (
                error.code if isinstance(error, BlobLifecycleConflict) else "TRANSCODE_FAILED"
            )
            job.error_message = type(error).__name__
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = utc_now()
            if rendition is not None and self._rendition_is_publishable(rendition):
                rendition.state = "FAILED"
                rendition.updated_at = job.updated_at

    @staticmethod
    def _rendition_is_publishable(rendition: ArtifactRenditionRow) -> bool:
        return (
            rendition.state == _TRANSCODE_PENDING_RENDITION_STATE
            and rendition.removed_at is None
            and rendition.blob_sha256 is None
        )

    def remove_rendition(self, rendition_id: UUID) -> bool:
        with self._write_gate, self.session_factory.begin() as session:
            rendition = session.get(ArtifactRenditionRow, str(rendition_id))
            if rendition is None:
                return False
            if rendition.removed_at is not None:
                return False
            rendition.removed_at = utc_now()
            rendition.state = "REMOVED"
            rendition.updated_at = rendition.removed_at
            if rendition.blob_sha256 is not None:
                BlobLifecycleRepository().recompute_for_sha(session, rendition.blob_sha256)
            return True

    def _store(self) -> ArtifactStore:
        assert self.settings is not None
        return ArtifactStore(
            self.settings.data_dir,
            max_bytes=self.settings.media_single_file_max_bytes,
            chunk_size=self.settings.artifact_upload_chunk_bytes,
        )

    def _file_chunks(self, path: Path):
        assert self.settings is not None
        with path.open("rb") as source:
            while chunk := source.read(self.settings.artifact_upload_chunk_bytes):
                yield chunk

    @staticmethod
    def _confirmation_token(revision_id: UUID, source_sha256: str, profile: PreviewProfile) -> str:
        payload = json.dumps(
            {
                "revision_id": str(revision_id),
                "source_sha256": source_sha256,
                "profile": profile.name,
                "width": profile.width,
                "height": profile.height,
                "frames_per_second": profile.frames_per_second,
                "estimated_output_bytes": profile.estimated_output_bytes,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _source(self, revision_id: UUID) -> _VideoSource:
        with self.session_factory() as session:
            revision = session.get(ArtifactRevisionRow, str(revision_id))
            if revision is None or revision.discarded_at is not None:
                raise ArtifactRevisionNotFound(str(revision_id))
            if revision.media_health != "AVAILABLE":
                raise MediaResourceUnavailable(str(revision_id), revision.media_health)
            if revision.storage_kind == "MANAGED_BLOB" and revision.blob_sha256 is not None:
                blob = session.get(StoredBlobRow, revision.blob_sha256)
                if blob is None:
                    raise ArtifactRevisionNotFound(str(revision_id))
                media_type = blob.media_type
                metadata_json = blob.metadata_json
                source_sha256 = blob.sha256
            elif revision.storage_kind == "EXTERNAL_REFERENCE" and revision.external_snapshot_id is not None:
                snapshot = session.get(ExternalSnapshotRow, revision.external_snapshot_id)
                if snapshot is None:
                    raise ArtifactRevisionNotFound(str(revision_id))
                media_type = snapshot.media_type
                metadata_json = snapshot.metadata_json
                source_sha256 = snapshot.sha256
            else:
                raise MediaResourceUnavailable(str(revision_id), revision.media_health)
        if not media_type.startswith("video/"):
            raise ValueError("only video revisions support preview transcode")
        try:
            metadata = json.loads(metadata_json)
            width = int(metadata["width"])
            height = int(metadata["height"])
            duration_seconds = float(metadata["duration_seconds"])
            frames_per_second = float(metadata["frames_per_second"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("video revision metadata is invalid") from error
        return _VideoSource(
            sha256=source_sha256,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            frames_per_second=frames_per_second,
        )


@dataclass(frozen=True, slots=True)
class _VideoSource:
    sha256: str
    width: int
    height: int
    duration_seconds: float
    frames_per_second: float
