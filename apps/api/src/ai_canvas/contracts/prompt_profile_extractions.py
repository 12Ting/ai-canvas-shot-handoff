from datetime import datetime
from typing import Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_canvas.contracts.prompts import PromptBlockKind
from ai_canvas.contracts.resources import PromptResourcePayload
from ai_canvas.contracts.run_control import AgentRunRequestResponse


FormatRuleCode: TypeAlias = Literal[
    "SECTION_PER_BLOCK",
    "LABELLED_BLOCKS",
    "COMMA_DELIMITED",
    "SINGLE_PARAGRAPH",
    "PRESERVE_LANGUAGE",
    "CONCISE_SENTENCES",
]
CameraRuleCode: TypeAlias = Literal[
    "EXPLICIT_SHOT_SIZE",
    "EXPLICIT_CAMERA_ANGLE",
    "EXPLICIT_LENS",
    "EXPLICIT_CAMERA_MOVEMENT",
]
PositiveRuleCode: TypeAlias = Literal[
    "INCLUDE_LIGHTING",
    "INCLUDE_PALETTE",
    "INCLUDE_MATERIAL",
    "INCLUDE_STYLE_TERMS",
    "INCLUDE_SPATIAL_RELATION",
]
NegativeRuleCode: TypeAlias = Literal[
    "EXPLICIT_NEGATIVE_BLOCK",
    "NO_TEXT_WATERMARK",
    "NO_IDENTITY_DRIFT",
    "NO_ANATOMY_DEFECTS",
]
ApplicabilityCode: TypeAlias = Literal[
    "IMAGE_PROMPT",
    "VIDEO_PROMPT",
    "GENERAL_VISUAL_PROMPT",
]
ProfileRuleCode: TypeAlias = (
    FormatRuleCode
    | CameraRuleCode
    | PositiveRuleCode
    | NegativeRuleCode
    | ApplicabilityCode
)


class PromptProfileDraftV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["prompt_profile_draft"] = "prompt_profile_draft"
    schema_version: Literal[1] = 1
    content_order: list[PromptBlockKind] = Field(min_length=1, max_length=8)
    detail_density: Literal["LOW", "MEDIUM", "HIGH"]
    format_rule_codes: list[FormatRuleCode] = Field(min_length=1, max_length=6)
    camera_rule_codes: list[CameraRuleCode] = Field(default_factory=list, max_length=4)
    positive_rule_codes: list[PositiveRuleCode] = Field(default_factory=list, max_length=5)
    negative_rule_codes: list[NegativeRuleCode] = Field(default_factory=list, max_length=4)
    applicability_codes: list[ApplicabilityCode] = Field(min_length=1, max_length=3)
    summary: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_unique_entries(self) -> "PromptProfileDraftV1":
        collections = (
            self.content_order,
            self.format_rule_codes,
            self.camera_rule_codes,
            self.positive_rule_codes,
            self.negative_rule_codes,
            self.applicability_codes,
        )
        if any(len(values) != len(set(values)) for values in collections):
            raise ValueError("prompt profile draft entries must be unique")
        return self


class CreatePromptProfileExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_graph_revision_id: UUID
    branch_id: UUID
    expected_branch_head_revision_id: UUID
    expected_branch_head_version: int = Field(ge=1)
    expected_selection_version: int = Field(ge=1)
    source_node_id: UUID
    instruction: str = Field(min_length=1, max_length=20_000)


class MaterializePromptProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(min_length=1, max_length=200)
    resource_id: UUID | None = None
    base_resource_revision_id: UUID | None = None
    tags: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_resource_target(self) -> "MaterializePromptProfileRequest":
        if (self.resource_id is None) != (self.base_resource_revision_id is None):
            raise ValueError(
                "resource_id and base_resource_revision_id must be supplied together"
            )
        return self


PromptProfileExtractionState = Literal[
    "PENDING_APPROVAL",
    "PENDING_EXECUTION",
    "DISPATCHING",
    "SUCCEEDED",
    "FAILED",
    "UNKNOWN_TERMINAL",
    "REJECTED",
    "DRAFT_REJECTED",
    "MATERIALIZED",
]


class PromptProfileExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    project_id: UUID
    run_request: AgentRunRequestResponse
    source_node_id: UUID
    source_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: PromptProfileExtractionState
    output: PromptProfileDraftV1 | None = None
    output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    compiled_profile: PromptResourcePayload | None = None
    materialized_resource_revision_id: UUID | None = None
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime
