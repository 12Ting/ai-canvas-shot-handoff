from typing import Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


PromptBlockKind: TypeAlias = Literal[
    "SUBJECT",
    "SCENE_ACTION",
    "COMPOSITION_CAMERA",
    "LIGHT_COLOR_MATERIAL_STYLE",
    "NEGATIVE_CONSTRAINTS",
    "MOTION",
    "PROVIDER_EXTENSION",
    "FREEFORM",
]


class PromptBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    kind: PromptBlockKind
    text: str = Field(max_length=100_000)
    locked: bool


class PromptSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: UUID
    start_utf16: int = Field(ge=0)
    end_utf16: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_order(self) -> "PromptSpan":
        if self.end_utf16 < self.start_utf16:
            raise ValueError("end_utf16 must be greater than or equal to start_utf16")
        return self


class PromptDocumentV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    blocks: list[PromptBlock] = Field(default_factory=list, max_length=5_000)
    rendered_text: str
    spans: list[PromptSpan] = Field(default_factory=list, max_length=5_000)
