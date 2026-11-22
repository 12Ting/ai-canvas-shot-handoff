from datetime import datetime
from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_canvas.contracts.model_profiles import ContentCategory
from ai_canvas.contracts.graph_changesets import GraphChangeSetResponse
from ai_canvas.contracts.run_control import AgentRunRequestResponse


class NodeAgentScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["NODE"] = "NODE"


class TextSelectionAgentScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["TEXT_SELECTION"] = "TEXT_SELECTION"
    start_utf16: int = Field(ge=0)
    end_utf16: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_order(self) -> "TextSelectionAgentScope":
        if self.end_utf16 < self.start_utf16:
            raise ValueError("end_utf16 must be greater than or equal to start_utf16")
        return self


AgentScope: TypeAlias = Annotated[
    NodeAgentScope | TextSelectionAgentScope,
    Field(discriminator="kind"),
]


class CreateAgentProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_graph_revision_id: UUID
    branch_id: UUID
    expected_branch_head_revision_id: UUID
    expected_branch_head_version: int = Field(ge=1)
    expected_selection_version: int = Field(ge=1)
    node_id: UUID
    scope: AgentScope
    instruction: str = Field(min_length=1, max_length=20_000)
    context_categories: list[ContentCategory] = Field(min_length=1, max_length=5)
    new_branch_name: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_context_categories(self) -> "CreateAgentProposalRequest":
        if len(set(self.context_categories)) != len(self.context_categories):
            raise ValueError("context_categories must be unique")
        if "USER_INSTRUCTION" not in self.context_categories:
            raise ValueError("USER_INSTRUCTION context is required")
        return self


class ReplaceAgentTextOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["replace_text"] = "replace_text"
    node_id: UUID
    replacement_text: str = Field(max_length=100_000)


class AgentGraphChangeSetDraftV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["graph_change_set_draft"] = "graph_change_set_draft"
    schema_version: Literal[1] = 1
    operation: ReplaceAgentTextOperation
    summary: str = Field(min_length=1, max_length=500)


class AgentRunRequestDraftV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["run_request_draft"] = "run_request_draft"
    schema_version: Literal[1] = 1
    operation_kind: Literal["DRY_RUN"] = "DRY_RUN"
    target_node_ids: list[UUID] = Field(min_length=1, max_length=1)
    task_count: Literal[1] = 1
    reason: str = Field(min_length=1, max_length=500)


AgentOutputDraft: TypeAlias = Annotated[
    AgentGraphChangeSetDraftV1 | AgentRunRequestDraftV1,
    Field(discriminator="kind"),
]


AgentProposalState = Literal[
    "PENDING_APPROVAL",
    "PENDING_EXECUTION",
    "DISPATCHING",
    "SUCCEEDED",
    "FAILED",
    "UNKNOWN_TERMINAL",
    "REJECTED",
]


class AgentProposalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    project_id: UUID
    run_request: AgentRunRequestResponse
    state: AgentProposalState
    output: AgentOutputDraft | None = Field(default=None, discriminator="kind")
    graph_change_set: GraphChangeSetResponse | None = None
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime
