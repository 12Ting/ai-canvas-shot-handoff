from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_canvas.contracts.model_profiles import (
    ModelProfileRevisionInput,
    ProviderInput,
)


def valid_profile(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "role": "CANVAS_AGENT",
        "model_id": "local-structured-model",
        "model_version": "2026-09",
        "endpoint_url": "http://127.0.0.1:11434/v1/chat/completions",
        "locality": "LOCAL_LOOPBACK",
        "data_residency": "LOCAL_DEVICE",
        "allowed_content_categories": ["USER_INSTRUCTION", "SELECTED_TEXT"],
        "retention_policy": "NO_RETENTION_DECLARED",
        "retention_note": "The local witness stores no request body.",
        "tool_permissions": ["GRAPH_CHANGESET_DRAFT", "RUN_REQUEST_DRAFT"],
        "cost_mode": "HARD",
        "currency": "USD",
        "cost_bound_minor": 0,
        "max_output_tokens": 2048,
        "timeout_seconds": 30,
        "credential_kind": "NONE",
        "fallback_model_profile_revision_id": None,
    }
    body.update(overrides)
    return body


def test_provider_contract_accepts_only_normalized_fixed_adapter() -> None:
    provider = ProviderInput(
        provider_key="local-agent",
        display_name="Local Agent",
        adapter_kind="OPENAI_COMPATIBLE_CHAT_JSON_V1",
    )

    assert provider.provider_key == "local-agent"
    with pytest.raises(ValidationError):
        ProviderInput(
            provider_key="Local Agent",
            display_name="Local Agent",
            adapter_kind="OPENAI_COMPATIBLE_CHAT_JSON_V1",
        )
    with pytest.raises(ValidationError):
        ProviderInput(
            provider_key="local-agent",
            display_name="Local Agent",
            adapter_kind="ARBITRARY_HTTP",
        )


def test_model_profile_roles_have_disjoint_output_permissions_and_forbid_secrets() -> None:
    profile = ModelProfileRevisionInput.model_validate(valid_profile())

    assert profile.role == "CANVAS_AGENT"
    assert profile.tool_permissions == [
        "GRAPH_CHANGESET_DRAFT",
        "RUN_REQUEST_DRAFT",
    ]
    prompt_editor = ModelProfileRevisionInput.model_validate(
        valid_profile(
            role="PROMPT_EDITOR",
            allowed_content_categories=[
                "USER_INSTRUCTION",
                "PROMPT_TARGET_DOCUMENT",
                "PROMPT_PROFILE_RULES",
            ],
            tool_permissions=[
                "PROMPT_PROFILE_DRAFT",
                "PROMPT_BATCH_ITEM_DRAFT",
            ],
        )
    )
    assert prompt_editor.role == "PROMPT_EDITOR"

    with pytest.raises(ValidationError, match="PROMPT_EDITOR"):
        ModelProfileRevisionInput.model_validate(
            valid_profile(
                role="PROMPT_EDITOR",
                tool_permissions=["GRAPH_CHANGESET_DRAFT"],
            )
        )
    with pytest.raises(ValidationError, match="CANVAS_AGENT"):
        ModelProfileRevisionInput.model_validate(
            valid_profile(tool_permissions=["PROMPT_BATCH_ITEM_DRAFT"])
        )
    with pytest.raises(ValidationError):
        ModelProfileRevisionInput.model_validate(valid_profile(api_key="secret-canary"))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"tool_permissions": []}, "tool_permissions"),
        ({"tool_permissions": ["CONFIG_CHANGESET"]}, "tool_permissions"),
        ({"cost_mode": "HARD", "currency": None}, "currency"),
        ({"cost_mode": "HARD", "cost_bound_minor": None}, "cost_bound_minor"),
        ({"cost_mode": "UNKNOWN", "currency": "USD"}, "currency"),
        ({"credential_kind": "BEARER_ENV", "bearer_token": "secret"}, "bearer_token"),
        ({"max_output_tokens": 0}, "max_output_tokens"),
        ({"timeout_seconds": 121}, "timeout_seconds"),
    ],
)
def test_model_profile_rejects_invalid_permission_cost_and_secret_shapes(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError) as error:
        ModelProfileRevisionInput.model_validate(valid_profile(**overrides))

    assert message in str(error.value)


def test_model_profile_accepts_declared_fallback_without_activating_it() -> None:
    fallback_id = uuid4()
    profile = ModelProfileRevisionInput.model_validate(
        valid_profile(
            cost_mode="ESTIMATED",
            currency="CNY",
            cost_bound_minor=100,
            fallback_model_profile_revision_id=fallback_id,
        )
    )

    assert profile.fallback_model_profile_revision_id == fallback_id
