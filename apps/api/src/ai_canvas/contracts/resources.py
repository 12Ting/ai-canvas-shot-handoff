from datetime import datetime, timezone
from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import (
    BaseModel,
    AfterValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from ai_canvas.contracts.graph import MediaKind, ResourceKind
from ai_canvas.contracts.prompts import PromptBlockKind


DetailDensity: TypeAlias = Literal["LOW", "MEDIUM", "HIGH"]
ResourceText: TypeAlias = Annotated[
    str,
    StringConstraints(min_length=1, max_length=10_000),
]
ResourceListItem: TypeAlias = Annotated[
    str,
    StringConstraints(min_length=1, max_length=1_000),
]
ResourceSlot: TypeAlias = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$",
    ),
]


def _normalize_response_timestamp(value: datetime) -> datetime:
    if value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


ResourceTimestamp: TypeAlias = Annotated[
    datetime,
    AfterValidator(_normalize_response_timestamp),
]


class StyleResourcePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["STYLE"] = "STYLE"
    summary: ResourceText
    palette: list[ResourceListItem] = Field(default_factory=list, max_length=64)
    lighting: list[ResourceListItem] = Field(default_factory=list, max_length=64)
    materials: list[ResourceListItem] = Field(default_factory=list, max_length=64)
    camera_language: list[ResourceListItem] = Field(default_factory=list, max_length=64)
    negative_constraints: list[ResourceListItem] = Field(default_factory=list, max_length=64)


class CharacterResourcePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["CHARACTER"] = "CHARACTER"
    canonical_name: ResourceText
    identity_anchor: ResourceText
    locked_traits: list[ResourceListItem] = Field(default_factory=list, max_length=64)
    wardrobe_notes: list[ResourceListItem] = Field(default_factory=list, max_length=64)


class PromptResourcePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["PROMPT"] = "PROMPT"
    content_order: list[PromptBlockKind] = Field(min_length=1, max_length=8)
    detail_density: DetailDensity
    format_rules: list[ResourceListItem] = Field(min_length=1, max_length=64)
    camera_rules: list[ResourceListItem] = Field(default_factory=list, max_length=64)
    positive_rules: list[ResourceListItem] = Field(default_factory=list, max_length=64)
    negative_rules: list[ResourceListItem] = Field(default_factory=list, max_length=64)
    applicability: list[ResourceListItem] = Field(default_factory=list, max_length=64)
    source_example_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_content_order_is_unique(self) -> "PromptResourcePayload":
        if len(set(self.content_order)) != len(self.content_order):
            raise ValueError("content_order entries must be unique")
        return self


class MediaResourcePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["MEDIA"] = "MEDIA"
    usage_notes: ResourceText
    expected_media_kind: MediaKind | None = None


ResourcePayload: TypeAlias = Annotated[
    StyleResourcePayload
    | CharacterResourcePayload
    | PromptResourcePayload
    | MediaResourcePayload,
    Field(discriminator="kind"),
]


class ResourceArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot: ResourceSlot
    position: int = Field(ge=0, le=9_007_199_254_740_991)
    artifact_revision_id: UUID


class NormalizedResourceTag(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    display_tag: str = Field(min_length=1, max_length=100)
    normalized_tag: str = Field(min_length=1, max_length=100)


class CreateResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    payload: ResourcePayload
    tags: list[str] = Field(default_factory=list, max_length=64)
    artifacts: list[ResourceArtifactReference] = Field(default_factory=list, max_length=64)


class CreateResourceRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_revision_id: UUID
    payload: ResourcePayload
    tags: list[str] = Field(default_factory=list, max_length=64)
    artifacts: list[ResourceArtifactReference] = Field(default_factory=list, max_length=64)


class ArchiveResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResourceRevisionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    sequence: int = Field(gt=0)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: ResourceTimestamp


class ResourceRevisionResponse(ResourceRevisionSummary):
    resource_id: UUID
    schema_version: Literal[1] = 1
    payload: ResourcePayload
    tags: list[NormalizedResourceTag]
    artifacts: list[ResourceArtifactReference]
    created_by_actor: str = Field(min_length=1, max_length=128)


class ResourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    origin_project_id: UUID
    kind: ResourceKind
    name: str = Field(min_length=1, max_length=200)
    created_at: ResourceTimestamp
    archived_at: ResourceTimestamp | None
    latest_revision: ResourceRevisionResponse


class ResourceDetailResponse(ResourceResponse):
    revision_history: list[ResourceRevisionSummary]


class ResourceUpdateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding_id: UUID
    binding_key: str
    scope_kind: Literal["PROJECT", "SHOT", "NODE"]
    scope_id: UUID | None
    resource_id: UUID
    resource_kind: ResourceKind
    pinned_revision_id: UUID
    latest_revision_id: UUID
    pinned_sequence: int = Field(gt=0)
    latest_sequence: int = Field(gt=0)
