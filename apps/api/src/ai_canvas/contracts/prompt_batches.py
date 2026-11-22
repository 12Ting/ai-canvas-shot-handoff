from datetime import datetime
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_canvas.contracts.branches import BranchApplicationTarget
from ai_canvas.contracts.graph import PortName
from ai_canvas.contracts.graph_changesets import GraphChangeSetResponse
from ai_canvas.contracts.prompts import PromptDocumentV1
from ai_canvas.contracts.run_control import AgentRunRequestResponse


class SelectedShotsBatchSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["SELECTED"] = "SELECTED"
    shot_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_unique_shots(self) -> "SelectedShotsBatchSelection":
        if len(self.shot_ids) != len(set(self.shot_ids)):
            raise ValueError("selected shot ids must be unique")
        return self


class AllShotsBatchSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["ALL"] = "ALL"


PromptBatchSelection: TypeAlias = Annotated[
    SelectedShotsBatchSelection | AllShotsBatchSelection,
    Field(discriminator="mode"),
]


class CreatePromptBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_graph_revision_id: UUID
    selection: PromptBatchSelection
    prompt_profile_binding_key: PortName
    application_target: BranchApplicationTarget
    instruction: str = Field(min_length=1, max_length=20_000)
    client_mutation_id: UUID


class PromptBatchPreflightItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: UUID
    shot_node_id: UUID
    prompt_node_id: UUID
    shot_order: int = Field(ge=0)


class PromptBatchPreflightBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_id: UUID
    code: str


class PromptBatchPreflightResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligible: bool
    items: list[PromptBatchPreflightItem]
    blockers: list[PromptBatchPreflightBlocker]


class PromptBlockReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: UUID
    replacement_text: str = Field(max_length=100_000)


class PromptBatchItemDraftV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["prompt_batch_item_draft"] = "prompt_batch_item_draft"
    schema_version: Literal[1] = 1
    shot_id: UUID
    prompt_node_id: UUID
    base_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    replacements: list[PromptBlockReplacement] = Field(min_length=1, max_length=5_000)
    summary: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_unique_replacements(self) -> "PromptBatchItemDraftV1":
        block_ids = [replacement.block_id for replacement in self.replacements]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("replacement block ids must be unique")
        return self


class PromptBatchItemDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["ACCEPTED", "REJECTED"]
    attempt_id: UUID
    expected_output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_decision_version: int = Field(ge=0)
    client_mutation_id: UUID


class PromptBatchRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failed_item_ids: list[UUID] = Field(min_length=1, max_length=100)
    expected_batch_version: int = Field(ge=1)
    client_mutation_id: UUID

    @model_validator(mode="after")
    def validate_unique_items(self) -> "PromptBatchRetryRequest":
        if len(self.failed_item_ids) != len(set(self.failed_item_ids)):
            raise ValueError("failed item ids must be unique")
        return self


class PromptBatchApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVED", "REJECTED"] = "APPROVED"
    expected_request_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PromptBatchFinalizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_batch_version: int = Field(ge=1)
    client_mutation_id: UUID


class PromptBatchAcceptAllRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_batch_version: int = Field(ge=1)
    client_mutation_id: UUID


class ExecutePromptBatchItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_attempt_id: UUID
    expected_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PromptBatchAttemptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    attempt_number: int = Field(ge=1)
    parent_attempt_id: UUID | None = None
    run_request: AgentRunRequestResponse
    state: Literal[
        "PENDING_APPROVAL",
        "PENDING_EXECUTION",
        "DISPATCHING",
        "SUCCEEDED",
        "FAILED",
        "UNKNOWN_TERMINAL",
        "REJECTED",
    ]
    output: PromptBatchItemDraftV1 | None = None
    output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    compiled_document_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    compiled_document: PromptDocumentV1 | None = None
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime


class PromptBatchDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    attempt_id: UUID
    decision: Literal["ACCEPTED", "REJECTED"]
    decision_sequence: int = Field(ge=1)
    expected_output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor_id: str
    created_at: datetime


class PromptBatchItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    shot_id: UUID
    shot_node_id: UUID
    prompt_node_id: UUID
    shot_order: int = Field(ge=0)
    base_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_document: PromptDocumentV1
    protected_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protected_manifest: list[dict[str, Any]]
    editable_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    editable_manifest: list[dict[str, Any]]
    prompt_profile_resource_revision_id: UUID
    prompt_profile_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_profile_scope_kind: Literal["PROJECT", "SHOT", "NODE"]
    prompt_profile_scope_id: UUID | None = None
    decision_version: int = Field(ge=0)
    current_attempt: PromptBatchAttemptResponse | None = None
    latest_decision: PromptBatchDecisionResponse | None = None
    attempts: list[PromptBatchAttemptResponse]
    decisions: list[PromptBatchDecisionResponse]


class PromptBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    project_id: UUID
    correlation_id: UUID
    selection_mode: Literal["SELECTED", "ALL"]
    binding_key: str
    base_graph_revision_id: UUID
    base_graph_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch_id: UUID
    expected_branch_head_version: int = Field(ge=1)
    expected_selection_version: int = Field(ge=1)
    application_target: dict[str, Any]
    model_profile_revision_id: UUID
    config_revision_id: UUID
    item_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retry_request_set_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    version: int = Field(ge=1)
    phase: Literal[
        "PENDING_APPROVAL",
        "PENDING_EXECUTION",
        "IN_PROGRESS",
        "REVIEW_PENDING",
        "RETRY_APPROVAL_REQUIRED",
        "FINALIZED",
        "REJECTED",
    ]
    items: list[PromptBatchItemResponse]
    graph_change_set: GraphChangeSetResponse | None = None
    final_decision_set_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime
