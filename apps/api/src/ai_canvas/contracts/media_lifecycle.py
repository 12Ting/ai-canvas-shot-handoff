from __future__ import annotations

from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_canvas.contracts.artifact import (
    ArtifactResponseTimestamp,
    ArtifactRevisionResponse,
    MediaType,
    UntrustedFilename,
)
from ai_canvas.contracts.graph import MediaKind

B4_MEDIA_MAX_BYTES = 10 * 1024**3
LARGE_UPLOAD_PART_BYTES = 8 * 1024**2


class StorageKind(str, Enum):
    MANAGED_BLOB = "MANAGED_BLOB"
    EXTERNAL_REFERENCE = "EXTERNAL_REFERENCE"


class MediaHealth(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    DRIFTED = "DRIFTED"
    QUARANTINED = "QUARANTINED"
    PURGED = "PURGED"


class LargeUploadState(str, Enum):
    CREATED = "CREATED"
    RECEIVING = "RECEIVING"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class MaintenanceJobState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CreateLargeUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_upload_id: UUID
    filename: UntrustedFilename
    expected_byte_size: int = Field(gt=0, le=B4_MEDIA_MAX_BYTES)
    requested_media_kind: MediaKind
    declared_content_type: MediaType | None = None
    replace_revision_id: UUID | None = None


class LargeUploadSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: UUID
    source_project_id: UUID
    client_upload_id: UUID
    requested_media_kind: MediaKind
    original_filename: UntrustedFilename
    expected_byte_size: int = Field(gt=0, le=B4_MEDIA_MAX_BYTES)
    received_byte_size: int = Field(ge=0)
    part_size: int = Field(gt=0)
    state: LargeUploadState
    failure_code: str | None = None
    created_at: ArtifactResponseTimestamp
    updated_at: ArtifactResponseTimestamp
    expires_at: ArtifactResponseTimestamp
    artifact_revision: ArtifactRevisionResponse | None = None

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> LargeUploadSessionResponse:
        if self.received_byte_size > self.expected_byte_size:
            raise ValueError("received_byte_size cannot exceed expected_byte_size")
        completed = self.state is LargeUploadState.COMPLETED
        if completed != (self.artifact_revision is not None):
            raise ValueError("only a completed upload has an artifact_revision")
        return self


class CreateMediaSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: UUID


class MediaSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: UUID
    idle_expires_at: ArtifactResponseTimestamp
    absolute_expires_at: ArtifactResponseTimestamp


class ExternalRootGrantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: UUID
    display_name: str


class CreateExternalReferenceFromPickerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    requested_media_kind: MediaKind


class ExternalReferenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: UUID
    artifact_id: UUID
    health: MediaHealth
    storage_kind: StorageKind
    snapshot_id: UUID


class ReconfirmExternalReferenceFromPickerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: UUID


class ManagedCopyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: UUID
    artifact_id: UUID
    storage_kind: StorageKind


class RequestRenditionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Literal["preview_1080p", "preview_720p"]
    confirmation_token: str | None = Field(default=None, min_length=64, max_length=64)


class RenditionJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    rendition_id: UUID
    profile: Literal["preview_1080p", "preview_720p"]
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frames_per_second: float = Field(gt=0, le=30)
    estimated_output_bytes: int = Field(gt=0)
    reused: bool


class GcPlanEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: UUID
    target_id: UUID
    reason: str
    logical_reclaim_bytes: int = Field(ge=0)
    physical_reclaim_bytes: int = Field(ge=0)


class StorageSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_quota_bytes: int = Field(gt=0)
    managed_blob_bytes: int = Field(ge=0)
    staging_bytes: int = Field(ge=0)
    quarantine_bytes: int = Field(ge=0)
    active_reservation_bytes: int = Field(ge=0)
    physical_free_bytes: int = Field(ge=0)


class GcPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: UUID
    summary_sha256: str = Field(min_length=64, max_length=64)
    entries: list[GcPlanEntryResponse]


class ConfirmGcPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_sha256: str = Field(min_length=64, max_length=64)


class QuarantineEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quarantine_id: UUID
    target_id: UUID
