from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ai_canvas.contracts.graph import GraphRevisionResponse


BranchKind = Literal["MAIN", "EXPERIMENT"]
MaterializationState = Literal["UNMATERIALIZED", "CURRENT", "STALE", "ABSENT"]
PromotionState = Literal["PENDING_APPROVAL", "APPROVED", "APPLIED", "REJECTED", "STALE"]


class BranchHeadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    graph_revision_id: UUID
    version: int = Field(ge=1)
    graph_revision_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class BranchSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    graph_document_id: UUID
    name: str = Field(min_length=1, max_length=100)
    kind: BranchKind
    forked_from_branch_id: UUID | None
    forked_from_revision_id: UUID
    head: BranchHeadResponse
    created_at: datetime
    is_selected: bool = False


class SelectionPointerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    graph_document_id: UUID
    branch_id: UUID
    graph_revision_id: UUID
    version: int = Field(ge=1)


class MaterializationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unmaterialized: int = Field(ge=0)
    current: int = Field(ge=0)
    stale: int = Field(ge=0)


class DirectApplicationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["DIRECT"] = "DIRECT"
    branch_id: UUID
    expected_branch_head_revision_id: UUID
    expected_branch_head_version: int = Field(ge=1)
    expected_selection_version: int = Field(ge=1)


class NewBranchApplicationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["NEW_BRANCH"] = "NEW_BRANCH"
    base_branch_id: UUID
    branch_name: str = Field(min_length=1, max_length=100)
    expected_branch_head_revision_id: UUID
    expected_branch_head_version: int = Field(ge=1)
    expected_selection_version: int = Field(ge=1)


BranchApplicationTarget = Annotated[
    DirectApplicationTarget | NewBranchApplicationTarget,
    Field(discriminator="mode"),
]


class NodeImpactItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: UUID
    impact_kind: Literal["DIRECT", "DOWNSTREAM", "REMOVED"]
    reasons: list[str]
    old_state: MaterializationState
    projected_state: MaterializationState


class StaleImpactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[NodeImpactItem]
    summary: MaterializationSummary
    impact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation_required: bool


class PromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_mutation_id: UUID
    target_branch_id: UUID
    expected_target_branch_head_revision_id: UUID
    expected_target_branch_head_version: int = Field(ge=1)
    expected_selection_version: int = Field(ge=1)


class PromotionApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVED", "REJECTED"] = "APPROVED"
    expected_promotion_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class UndoProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_mutation_id: UUID


class PromotionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    project_id: UUID
    correlation_id: UUID
    client_mutation_id: UUID
    kind: Literal["PROMOTE", "UNDO_PROMOTION"]
    source_branch_id: UUID
    source_graph_revision_id: UUID
    source_branch_head_version: int = Field(ge=1)
    target_branch_id: UUID
    target_graph_revision_id: UUID
    target_branch_head_version: int = Field(ge=1)
    expected_selection_version: int = Field(ge=1)
    undo_of_promotion_id: UUID | None
    impact: dict[str, Any]
    impact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    promotion_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    risk_summary: dict[str, Any]
    state: PromotionState
    approval_id: UUID | None
    created_at: datetime
    applied_at: datetime | None


class BranchWorkspaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    graph_document_id: UUID
    branches: list[BranchSummary]
    selection: SelectionPointerResponse
    latest_promotion: PromotionResponse | None = None
    can_undo_latest_promotion: bool = False


class BranchGraphResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch: BranchSummary
    selection: SelectionPointerResponse
    revision: GraphRevisionResponse
    materialization: MaterializationSummary
