from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from ai_canvas.contracts.graph import MediaKind


B1_MEDIA_MAX_BYTES = 64 * 1024 * 1024
ImportSessionState: TypeAlias = Literal[
    "CREATED",
    "RECEIVING",
    "VALIDATING",
    "COMPLETED",
    "FAILED_RETRYABLE",
    "FAILED_FINAL",
]
UntrustedFilename: TypeAlias = Annotated[
    str,
    StringConstraints(min_length=1, max_length=512),
]
DisplayFilename: TypeAlias = Annotated[
    str,
    StringConstraints(min_length=1, max_length=512),
]
MediaType: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
SourceKind: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]


def _normalize_artifact_response_timestamp(value: datetime) -> datetime:
    if value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


ArtifactResponseTimestamp: TypeAlias = Annotated[
    datetime,
    AfterValidator(_normalize_artifact_response_timestamp),
]


class ArtifactImportFailureCode(str, Enum):
    UPLOAD_INTERRUPTED = "upload_interrupted"
    PROBE_TIMEOUT = "probe_timeout"
    PROBE_UNAVAILABLE = "probe_unavailable"
    SIZE_MISMATCH = "size_mismatch"
    CORRUPT_MEDIA = "corrupt_media"
    UNSUPPORTED_MEDIA = "unsupported_media"
    INCOMPATIBLE_DECLARATION = "incompatible_declaration"


class ArtifactImportConflictReason(str, Enum):
    DECLARATION_MISMATCH = "declaration_mismatch"
    CONTENT_REPLAY_MISMATCH = "content_replay_mismatch"
    UPLOAD_IN_PROGRESS = "upload_in_progress"
    INVALID_REPLACEMENT = "invalid_replacement"
    INVALID_STATE = "invalid_state"
    DISCARD_PRECONDITION_FAILED = "discard_precondition_failed"
    RESULT_REFERENCED = "result_referenced"


class CreateArtifactImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_import_id: UUID
    filename: UntrustedFilename
    expected_byte_size: int = Field(gt=0, le=B1_MEDIA_MAX_BYTES)
    requested_media_kind: MediaKind
    declared_content_type: MediaType | None = None
    replace_revision_id: UUID | None = None


class DiscardArtifactImportResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_import_id: UUID
    expected_revision_id: UUID | None = None


class DiscardArtifactImportResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["discarded", "already_discarded"]
    content_cleanup: Literal[
        "removed",
        "not_present",
        "retained_shared",
        "not_applicable",
        "pending",
    ]


class ImageArtifactMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["image"] = "image"
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class VideoArtifactMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["video"] = "video"
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    frames_per_second: float = Field(gt=0, allow_inf_nan=False)


ArtifactMetadata: TypeAlias = Annotated[
    ImageArtifactMetadata | VideoArtifactMetadata,
    Field(discriminator="kind"),
]


class ArtifactRevisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: UUID
    artifact_id: UUID
    sequence: int = Field(gt=0)
    media_kind: MediaKind
    media_type: MediaType
    byte_size: int = Field(gt=0)
    display_filename: DisplayFilename
    metadata: ArtifactMetadata
    source_kind: SourceKind
    source_project_id: UUID
    origin_project_id: UUID
    created_at: ArtifactResponseTimestamp

    @model_validator(mode="after")
    def validate_metadata_kind(self) -> "ArtifactRevisionResponse":
        if self.media_kind != self.metadata.kind:
            raise ValueError("media_kind must match metadata.kind")
        return self


class ArtifactImportSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    import_id: UUID
    source_project_id: UUID
    client_import_id: UUID
    replace_revision_id: UUID | None = None
    requested_media_kind: MediaKind
    original_filename: UntrustedFilename
    declared_content_type: MediaType | None = None
    expected_byte_size: int = Field(gt=0, le=B1_MEDIA_MAX_BYTES)
    state: ImportSessionState
    received_byte_size: int = Field(ge=0)
    failure_code: ArtifactImportFailureCode | None = None
    created_at: ArtifactResponseTimestamp
    updated_at: ArtifactResponseTimestamp
    artifact_revision: ArtifactRevisionResponse | None = None

    @model_validator(mode="after")
    def validate_state_payload(self) -> "ArtifactImportSessionResponse":
        completed = self.state == "COMPLETED"
        if completed != (self.artifact_revision is not None):
            raise ValueError("only a completed import has an artifact_revision")
        failed = self.state in {"FAILED_RETRYABLE", "FAILED_FINAL"}
        if failed != (self.failure_code is not None):
            raise ValueError("only a failed import has a failure_code")
        if self.artifact_revision is not None:
            if self.artifact_revision.media_kind != self.requested_media_kind:
                raise ValueError("completed revision kind must match requested_media_kind")
            if self.artifact_revision.source_project_id != self.source_project_id:
                raise ValueError("completed revision source project must match import session")
            if self.artifact_revision.source_kind != "import":
                raise ValueError("completed import revision must have source_kind import")
            if not (
                self.received_byte_size
                == self.expected_byte_size
                == self.artifact_revision.byte_size
            ):
                raise ValueError("completed import byte sizes must match")
        return self
