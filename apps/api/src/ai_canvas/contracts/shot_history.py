from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ai_canvas.contracts.artifact import (
    ArtifactResponseTimestamp,
    ArtifactRevisionResponse,
)


ShotSlot: TypeAlias = Literal["image", "video", "reference"]
ShotSlotAssociationAction: TypeAlias = Literal["associated", "cleared", "removed"]
AssociationCursor: TypeAlias = Annotated[
    str,
    StringConstraints(min_length=1, max_length=2_048),
]


class ShotSlotAssociationHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_revision_id: UUID
    graph_revision_sequence: int = Field(ge=0)
    event_ordinal: int = Field(ge=0, le=127)
    occurred_at: ArtifactResponseTimestamp
    action: ShotSlotAssociationAction
    reference_index: int | None = Field(default=None, ge=0, le=63)
    artifact_revision: ArtifactRevisionResponse | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> "ShotSlotAssociationHistoryItem":
        if self.action == "cleared":
            if self.artifact_revision is not None or self.reference_index is not None:
                raise ValueError("cleared events cannot contain an artifact or reference index")
        elif self.artifact_revision is None:
            raise ValueError("associated and removed events require an artifact revision")
        return self


class ShotSlotAssociationHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    shot_id: UUID
    slot: ShotSlot
    anchor_graph_revision_id: UUID
    anchor_graph_sequence: int = Field(ge=0)
    items: list[ShotSlotAssociationHistoryItem]
    next_cursor: AssociationCursor | None = None

    @model_validator(mode="after")
    def validate_slot_payloads(self) -> "ShotSlotAssociationHistoryResponse":
        expected_kind = "video" if self.slot == "video" else "image"
        for item in self.items:
            if self.slot == "reference":
                if item.action == "cleared" or item.reference_index is None:
                    raise ValueError(
                        "reference history requires associated/removed events with an index"
                    )
            elif item.action == "removed" or item.reference_index is not None:
                raise ValueError(
                    "image and video history only support associated/cleared events"
                )
            if (
                item.artifact_revision is not None
                and item.artifact_revision.media_kind != expected_kind
            ):
                raise ValueError("artifact media kind does not match the history slot")
        return self
