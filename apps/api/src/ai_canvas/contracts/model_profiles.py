from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


ProviderAdapterKind = Literal["OPENAI_COMPATIBLE_CHAT_JSON_V1"]
ModelProfileRole = Literal["CANVAS_AGENT", "PROMPT_EDITOR"]
ModelLocality = Literal["LOCAL_LOOPBACK", "REMOTE_HTTPS"]
DataResidency = Literal["LOCAL_DEVICE", "CN", "US", "EU", "UNKNOWN"]
ContentCategory = Literal[
    "USER_INSTRUCTION",
    "SELECTED_NODE_TEXT",
    "SELECTED_TEXT",
    "ADJACENT_NODE_TEXT",
    "RESOURCE_TEXT_METADATA",
    "PROMPT_EXAMPLE_DOCUMENT",
    "PROMPT_TARGET_DOCUMENT",
    "PROMPT_PROFILE_RULES",
    "PRESERVE_CONSTRAINTS",
]
RetentionPolicy = Literal[
    "NO_RETENTION_DECLARED",
    "PROVIDER_DECLARED",
    "UNKNOWN",
]
ModelToolPermission = Literal[
    "GRAPH_CHANGESET_DRAFT",
    "RUN_REQUEST_DRAFT",
    "PROMPT_PROFILE_DRAFT",
    "PROMPT_BATCH_ITEM_DRAFT",
]
CostMode = Literal["HARD", "ESTIMATED", "UNKNOWN"]
CredentialKind = Literal["NONE", "BEARER_ENV"]


class ProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_key: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
    )
    display_name: str = Field(min_length=1, max_length=100)
    adapter_kind: ProviderAdapterKind = "OPENAI_COMPATIBLE_CHAT_JSON_V1"


class ModelProfileRevisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ModelProfileRole = "CANVAS_AGENT"
    model_id: str = Field(min_length=1, max_length=256)
    model_version: str | None = Field(default=None, max_length=128)
    endpoint_url: str = Field(min_length=1, max_length=512)
    locality: ModelLocality
    data_residency: DataResidency
    allowed_content_categories: list[ContentCategory] = Field(min_length=1, max_length=9)
    retention_policy: RetentionPolicy
    retention_note: str = Field(default="", max_length=500)
    tool_permissions: list[ModelToolPermission] = Field(min_length=1, max_length=2)
    cost_mode: CostMode
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    cost_bound_minor: int | None = Field(default=None, ge=0)
    max_output_tokens: int = Field(ge=1, le=32_768)
    timeout_seconds: int = Field(ge=1, le=120)
    credential_kind: CredentialKind
    fallback_model_profile_revision_id: UUID | None = None

    @model_validator(mode="after")
    def validate_collections_and_cost(self) -> "ModelProfileRevisionInput":
        if len(set(self.allowed_content_categories)) != len(
            self.allowed_content_categories
        ):
            raise ValueError("allowed_content_categories must be unique")
        if len(set(self.tool_permissions)) != len(self.tool_permissions):
            raise ValueError("tool_permissions must be unique")
        canvas_categories = {
            "USER_INSTRUCTION",
            "SELECTED_NODE_TEXT",
            "SELECTED_TEXT",
            "ADJACENT_NODE_TEXT",
            "RESOURCE_TEXT_METADATA",
        }
        prompt_categories = {
            "USER_INSTRUCTION",
            "PROMPT_EXAMPLE_DOCUMENT",
            "PROMPT_TARGET_DOCUMENT",
            "PROMPT_PROFILE_RULES",
            "PRESERVE_CONSTRAINTS",
        }
        canvas_permissions = {"GRAPH_CHANGESET_DRAFT", "RUN_REQUEST_DRAFT"}
        prompt_permissions = {
            "PROMPT_PROFILE_DRAFT",
            "PROMPT_BATCH_ITEM_DRAFT",
        }
        if self.role == "CANVAS_AGENT":
            if not set(self.allowed_content_categories) <= canvas_categories:
                raise ValueError("CANVAS_AGENT content categories are out of scope")
            if not set(self.tool_permissions) <= canvas_permissions:
                raise ValueError("CANVAS_AGENT output permissions are out of scope")
        else:
            if not set(self.allowed_content_categories) <= prompt_categories:
                raise ValueError("PROMPT_EDITOR content categories are out of scope")
            if not set(self.tool_permissions) <= prompt_permissions:
                raise ValueError("PROMPT_EDITOR output permissions are out of scope")
        if self.cost_mode in {"HARD", "ESTIMATED"}:
            if self.currency is None:
                raise ValueError(f"currency is required for {self.cost_mode} cost mode")
            if self.cost_bound_minor is None:
                raise ValueError(
                    f"cost_bound_minor is required for {self.cost_mode} cost mode"
                )
        elif self.currency is not None or self.cost_bound_minor is not None:
            raise ValueError("currency and cost_bound_minor must be null for UNKNOWN cost mode")
        return self


class ModelProfileRevisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    sequence: int = Field(ge=1)
    provider_id: UUID
    declaration: dict[str, Any]
    declaration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    credential_available: bool
    created_at: datetime


class ModelProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    profile_key: str
    role: ModelProfileRole
    active_revision_id: UUID | None
    created_at: datetime


class ModelProfileListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles: list[ModelProfileResponse]


class ModelProfileRevisionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    revisions: list[ModelProfileRevisionResponse]
