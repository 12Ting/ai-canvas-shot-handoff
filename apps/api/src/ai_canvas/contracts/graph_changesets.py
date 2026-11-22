from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ai_canvas.contracts.graph import GraphSnapshot
from ai_canvas.contracts.branches import BranchApplicationTarget, StaleImpactResponse


class ReplaceGraphSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["replace_snapshot"] = "replace_snapshot"
    snapshot: GraphSnapshot


class CreateGraphChangeSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["graph_change_set"] = "graph_change_set"
    schema_version: Literal[1] = 1
    base_graph_revision_id: UUID
    client_mutation_id: UUID
    operations: list[ReplaceGraphSnapshot] = Field(min_length=1, max_length=1)
    application_target: BranchApplicationTarget | None = Field(
        default=None,
        discriminator="mode",
    )


class GraphChangeSetApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVED", "REJECTED"] = "APPROVED"
    expected_changeset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed_stale_impact_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class GraphChangeSetBaseRevisionStaleProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["graph_base_revision_stale"] = "graph_base_revision_stale"
    current_revision_id: UUID


GraphChangeSetState = Literal["PENDING_APPROVAL", "APPROVED", "APPLIED", "REJECTED"]


class ResourceBindingDiffItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding_id: UUID
    scope_kind: Literal["PROJECT", "SHOT", "NODE"]
    scope_id: UUID | None
    binding_key: str
    resource_kind: Literal["STYLE", "CHARACTER", "PROMPT", "MEDIA"]
    resource_revision_id: UUID


class ResourceBindingUpdatedDiffItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding_id: UUID
    scope_kind: Literal["PROJECT", "SHOT", "NODE"]
    scope_id: UUID | None
    binding_key: str
    old_resource_revision_id: UUID
    new_resource_revision_id: UUID


class PromptDocumentChangedDiffItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: UUID
    old_document_sha256: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    new_document_sha256: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    old_locked_block_count: int = Field(ge=0)
    new_locked_block_count: int = Field(ge=0)


class GraphChangeSetDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["replace_snapshot"] = "replace_snapshot"
    from_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    to_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource_bindings_added: list[ResourceBindingDiffItem] = Field(default_factory=list)
    resource_bindings_removed: list[ResourceBindingDiffItem] = Field(default_factory=list)
    resource_bindings_updated: list[ResourceBindingUpdatedDiffItem] = Field(default_factory=list)
    prompt_documents_changed: list[PromptDocumentChangedDiffItem] = Field(default_factory=list)


class GraphChangeSetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    project_id: UUID
    correlation_id: UUID
    base_graph_revision_id: UUID
    changeset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    diff: GraphChangeSetDiff
    risk_summary: dict[str, Any]
    state: GraphChangeSetState
    applied_graph_revision_id: UUID | None
    application_target: BranchApplicationTarget
    impact: StaleImpactResponse
    created_branch_id: UUID | None
    applied_branch_id: UUID | None
    created_at: datetime
