from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_kind: Literal["DRY_RUN"] = "DRY_RUN"
    task_count: int = Field(ge=1, le=100)
    endpoint: str | None = Field(default=None, max_length=512)
    external_data_classes: list[str] = Field(default_factory=list, max_length=0)
    cost_bound: int = Field(default=0, ge=0)
    gpu_count: int = Field(default=0, ge=0)
    real_media: bool = False
    max_retries: int = Field(default=0, ge=0, le=0)


class RunRequestApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVED", "REJECTED"] = "APPROVED"
    expected_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


RunRequestState = Literal["PENDING_APPROVAL", "APPROVED", "REJECTED", "TERMINAL"]
RunAuthorizationState = Literal["APPROVED", "CONSUMED", "EXPIRED", "INVALIDATED"]


class EgressPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["NONE"] = "NONE"
    endpoint: None = None
    external_data_classes: list[str] = Field(max_length=0)
    task_count: int = Field(ge=1, le=100)
    cost_bound: Literal[0] = 0
    gpu_count: Literal[0] = 0
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RunRequestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    project_id: UUID
    correlation_id: UUID
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    egress_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    egress_plan: EgressPlanResponse
    state: RunRequestState


class AgentEgressPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["MODEL_PROVIDER"] = "MODEL_PROVIDER"
    endpoint: str = Field(min_length=1, max_length=512)
    locality: Literal["LOCAL_LOOPBACK", "REMOTE_HTTPS"]
    endpoint_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    external_data_classes: list[str] = Field(max_length=9)
    task_count: Literal[1] = 1
    gpu_count: Literal[0] = 0
    media_execution: Literal[False] = False
    cost_mode: Literal["HARD", "ESTIMATED", "UNKNOWN"]
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    cost_bound_minor: int | None = Field(default=None, ge=0)
    data_residency: Literal["LOCAL_DEVICE", "CN", "US", "EU", "UNKNOWN"]
    retention_policy: Literal[
        "NO_RETENTION_DECLARED", "PROVIDER_DECLARED", "UNKNOWN"
    ]
    max_retries: Literal[0] = 0
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview: dict[str, Any]


class AgentRunRequestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    project_id: UUID
    correlation_id: UUID
    operation_kind: Literal[
        "AGENT_PROPOSAL",
        "PROMPT_PROFILE_EXTRACTION",
        "PROMPT_BATCH_ITEM",
    ] = "AGENT_PROPOSAL"
    model_profile_revision_id: UUID
    branch_id: UUID
    branch_head_version: int = Field(ge=1)
    selection_version: int = Field(ge=1)
    scope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    egress_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    egress_plan: AgentEgressPlanResponse
    state: RunRequestState


class RunAuthorizationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    state: RunAuthorizationState
    expires_at: datetime


class DryRunReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    run_request_id: UUID
    terminal_state: Literal["DRY_RUN_SUCCEEDED", "DRY_RUN_VALIDATION_FAILED"]
    created_at: datetime
