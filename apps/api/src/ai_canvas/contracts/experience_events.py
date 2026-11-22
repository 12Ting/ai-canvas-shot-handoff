from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


ActorKind = Literal["HUMAN", "AI_CANVAS", "SYSTEM"]
ExperienceEventType = Literal[
    "graph_change_set.requested",
    "graph_change_set.applied",
    "graph_change_set.rejected",
    "config_change_set.requested",
    "config_change_set.applied",
    "config_change_set.rejected",
    "run_request.requested",
    "run_request.rejected",
    "egress_plan.sealed",
    "run_authorization.issued",
    "run_authorization.consumed",
    "run_authorization.expired",
    "run_authorization.invalidated",
    "run.dry_run_succeeded",
    "run.dry_run_failed",
    "approval.recorded",
    "resource.created",
    "resource.revision_created",
    "resource.archived",
    "graph_branch.created",
    "graph_branch.head_advanced",
    "selection_promotion.requested",
    "selection_promotion.applied",
    "selection_promotion.rejected",
    "selection_promotion.undone",
    "node_materialization.staled",
    "model_profile.revision_created",
    "agent_proposal.dispatched",
    "agent_proposal.succeeded",
    "agent_proposal.failed",
    "agent_proposal.unknown_terminal",
    "prompt_profile_extraction.requested",
    "prompt_profile_extraction.approved",
    "prompt_profile_extraction.rejected",
    "prompt_profile_extraction.dispatched",
    "prompt_profile_extraction.succeeded",
    "prompt_profile_extraction.failed",
    "prompt_profile_extraction.unknown_terminal",
    "prompt_profile_extraction.materialized",
    "prompt_batch.requested",
    "prompt_batch.approved",
    "prompt_batch.rejected",
    "prompt_batch.retry_requested",
    "prompt_batch.retry_approved",
    "prompt_batch.finalized",
    "prompt_batch_item.dispatched",
    "prompt_batch_item.succeeded",
    "prompt_batch_item.failed",
    "prompt_batch_item.unknown_terminal",
    "prompt_batch_item.decision_recorded",
]


class ExperienceEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    stream_position: int = Field(ge=1)
    occurred_at: datetime
    project_id: UUID
    correlation_id: UUID
    causation_event_id: UUID | None
    actor_kind: ActorKind
    actor_id: str = Field(min_length=1, max_length=128)
    subject_type: str = Field(min_length=1, max_length=64)
    subject_id: UUID
    event_type: ExperienceEventType
    schema_version: Literal[1] = 1
    payload: dict[str, Any]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    envelope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExperienceEventPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[ExperienceEventResponse] = Field(max_length=100)
    next_after: int = Field(ge=0)
    high_watermark: int = Field(ge=0)


class EventCursorAckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ack_stream_position: int = Field(ge=0)


class EventCursorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consumer_key: str = Field(min_length=1, max_length=128)
    last_ack_stream_position: int = Field(ge=0)
