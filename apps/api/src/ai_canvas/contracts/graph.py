from collections.abc import Mapping
from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from ai_canvas.contracts.prompts import PromptDocumentV1


NodeKind = Literal["text_source", "prompt", "note"]
PortName: TypeAlias = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$",
    ),
]


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)


class ViewportState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(default=0, allow_inf_nan=False)
    y: float = Field(default=0, allow_inf_nan=False)
    zoom: float = Field(default=1, ge=0.1, le=4, allow_inf_nan=False)


class NodeTextData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(default="", max_length=100_000)


class GraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    kind: NodeKind
    position: Point
    data: NodeTextData


class GraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    source: UUID
    source_port: PortName
    target: UUID
    target_port: PortName


class GraphSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    nodes: list[GraphNode] = Field(default_factory=list, max_length=5_000)
    edges: list[GraphEdge] = Field(default_factory=list, max_length=10_000)


MediaKind = Literal["image", "video"]
ResourceKind: TypeAlias = Literal["STYLE", "CHARACTER", "PROMPT", "MEDIA"]


class MediaInputData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_revision_id: UUID | None = None
    media_kind: MediaKind | None = None

    @model_validator(mode="after")
    def validate_revision_kind_pair(self) -> "MediaInputData":
        if (self.artifact_revision_id is None) != (self.media_kind is None):
            raise ValueError(
                "artifact_revision_id and media_kind must both be null or both be present"
            )
        return self


class ShotNodeData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shot_id: UUID


class TextSourceNodeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    kind: Literal["text_source"]
    position: Point
    data: NodeTextData


class PromptNodeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    kind: Literal["prompt"]
    position: Point
    data: NodeTextData


class NoteNodeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    kind: Literal["note"]
    position: Point
    data: NodeTextData


class MediaInputNodeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    kind: Literal["media_input"]
    position: Point
    data: MediaInputData


class ShotNodeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    kind: Literal["shot"]
    position: Point
    data: ShotNodeData


GraphNodeV2: TypeAlias = Annotated[
    TextSourceNodeV2 | PromptNodeV2 | NoteNodeV2 | MediaInputNodeV2 | ShotNodeV2,
    Field(discriminator="kind"),
]


class Shot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    order: int = Field(ge=0, le=9_007_199_254_740_991)
    image_revision_id: UUID | None = None
    video_revision_id: UUID | None = None
    reference_revision_ids: list[UUID] = Field(default_factory=list, max_length=64)

    @field_validator("reference_revision_ids", mode="before")
    @classmethod
    def validate_raw_reference_limit(cls, value: object) -> object:
        if isinstance(value, (list, tuple)) and len(value) > 64:
            raise ValueError("a shot may contain at most 64 reference revisions")
        return value


class GraphSnapshotV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2] = 2
    nodes: list[GraphNodeV2] = Field(default_factory=list, max_length=5_000)
    edges: list[GraphEdge] = Field(default_factory=list, max_length=10_000)
    shots: list[Shot] = Field(default_factory=list, max_length=5_000)

    @model_validator(mode="before")
    @classmethod
    def validate_raw_reference_budget(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        raw_shots = value.get("shots")
        if not isinstance(raw_shots, (list, tuple)):
            return value
        total = 0
        for raw_shot in raw_shots:
            if not isinstance(raw_shot, Mapping):
                continue
            raw_references = raw_shot.get("reference_revision_ids")
            if isinstance(raw_references, (list, tuple)):
                total += len(raw_references)
                if total > 10_000:
                    raise ValueError(
                        "a graph may contain at most 10,000 shot reference revisions"
                    )
        return value


class PromptNodeDataV3(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: PromptDocumentV1


class TextSourceNodeV3(TextSourceNodeV2):
    pass


class PromptNodeV3(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    kind: Literal["prompt"]
    position: Point
    data: PromptNodeDataV3


class NoteNodeV3(NoteNodeV2):
    pass


class MediaInputNodeV3(MediaInputNodeV2):
    pass


class ShotNodeV3(ShotNodeV2):
    pass


GraphNodeV3: TypeAlias = Annotated[
    TextSourceNodeV3 | PromptNodeV3 | NoteNodeV3 | MediaInputNodeV3 | ShotNodeV3,
    Field(discriminator="kind"),
]


class ResourceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    resource_kind: ResourceKind
    binding_key: PortName
    scope_kind: Literal["PROJECT", "SHOT", "NODE"]
    scope_id: UUID | None
    resource_revision_id: UUID

    @model_validator(mode="after")
    def validate_scope_id(self) -> "ResourceBinding":
        if self.scope_kind == "PROJECT" and self.scope_id is not None:
            raise ValueError("PROJECT resource binding scope_id must be null")
        if self.scope_kind != "PROJECT" and self.scope_id is None:
            raise ValueError("SHOT and NODE resource binding scope_id must be present")
        return self


class GraphSnapshotV3(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[3] = 3
    nodes: list[GraphNodeV3] = Field(default_factory=list, max_length=5_000)
    edges: list[GraphEdge] = Field(default_factory=list, max_length=10_000)
    shots: list[Shot] = Field(default_factory=list, max_length=5_000)
    resource_bindings: list[ResourceBinding] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="before")
    @classmethod
    def validate_raw_reference_budget(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        raw_shots = value.get("shots")
        if not isinstance(raw_shots, (list, tuple)):
            return value
        total = 0
        for raw_shot in raw_shots:
            if not isinstance(raw_shot, Mapping):
                continue
            raw_references = raw_shot.get("reference_revision_ids")
            if isinstance(raw_references, (list, tuple)):
                total += len(raw_references)
                if total > 10_000:
                    raise ValueError(
                        "a graph may contain at most 10,000 shot reference revisions"
                    )
        return value


def _default_v1_schema_version(value: object) -> object:
    if isinstance(value, Mapping) and "schema_version" not in value:
        copied = dict(value)
        copied["schema_version"] = 1
        return copied
    return value


GraphSnapshot: TypeAlias = Annotated[
    Annotated[
        GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3,
        Field(discriminator="schema_version"),
    ],
    BeforeValidator(_default_v1_schema_version),
]


class CreateGraphRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision_id: UUID
    client_mutation_id: UUID
    snapshot: GraphSnapshot


class GraphRevisionResponse(BaseModel):
    revision_id: UUID
    graph_document_id: UUID
    sequence: int
    snapshot_hash: str
    snapshot: GraphSnapshot
    viewport: ViewportState


class GraphRevisionConflictProblem(BaseModel):
    code: Literal["graph_revision_conflict"] = "graph_revision_conflict"
    current_revision_id: UUID


class ClientMutationReuseProblem(BaseModel):
    code: Literal["client_mutation_reuse"] = "client_mutation_reuse"
    client_mutation_id: UUID
