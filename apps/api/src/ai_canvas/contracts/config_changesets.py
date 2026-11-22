from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ai_canvas.contracts.model_profiles import ModelProfileRevisionInput, ProviderInput


class ReplaceDryRunMaxTasks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["replace"] = "replace"
    path: Literal["/dry_run_max_tasks"] = "/dry_run_max_tasks"
    value: int = Field(ge=1, le=100)


class DeclareModelProfileRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["declare_model_profile_revision"] = "declare_model_profile_revision"
    provider_id: UUID
    provider: ProviderInput
    model_profile_id: UUID
    profile_key: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
    )
    model_profile_revision_id: UUID
    revision: ModelProfileRevisionInput
    declaration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    activate: Literal[True] = True


ConfigOperation = Annotated[
    ReplaceDryRunMaxTasks | DeclareModelProfileRevision,
    Field(discriminator="op"),
]


class CreateConfigChangeSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["config_change_set"] = "config_change_set"
    schema_version: Literal[1] = 1
    audit_project_id: UUID
    base_config_revision_id: UUID
    operations: list[ConfigOperation] = Field(min_length=1, max_length=1)


class ConfigChangeSetApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVED", "REJECTED"] = "APPROVED"
    expected_changeset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


ConfigChangeSetState = Literal["PENDING_APPROVAL", "APPROVED", "APPLIED", "REJECTED"]


class ControlConfigRevisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    sequence: int = Field(ge=1)
    dry_run_max_tasks: int = Field(ge=1, le=100)
    active_canvas_agent_profile_revision_id: UUID | None = None
    active_prompt_editor_profile_revision_id: UUID | None = None
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


class ConfigChangeSetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    audit_project_id: UUID
    correlation_id: UUID
    base_config_revision_id: UUID
    changeset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operations: list[dict[str, Any]]
    risk_summary: dict[str, Any]
    state: ConfigChangeSetState
    created_at: datetime
