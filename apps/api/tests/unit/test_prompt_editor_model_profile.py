from pathlib import Path

import pytest

from ai_canvas.application.control_config_service import ControlConfigService
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.model_profile_service import ModelProfileService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


def _revision(role: str, permission: str) -> dict[str, object]:
    categories = (
        ["USER_INSTRUCTION", "SELECTED_NODE_TEXT"]
        if role == "CANVAS_AGENT"
        else ["USER_INSTRUCTION", "PROMPT_TARGET_DOCUMENT", "PROMPT_PROFILE_RULES"]
    )
    return {
        "role": role,
        "model_id": f"{role.lower()}-loopback",
        "model_version": "1",
        "endpoint_url": "http://127.0.0.1:8765/v1/chat/completions",
        "locality": "LOCAL_LOOPBACK",
        "data_residency": "LOCAL_DEVICE",
        "allowed_content_categories": categories,
        "retention_policy": "NO_RETENTION_DECLARED",
        "retention_note": "loopback",
        "tool_permissions": [permission],
        "cost_mode": "HARD",
        "currency": "CNY",
        "cost_bound_minor": 0,
        "max_output_tokens": 2048,
        "timeout_seconds": 15,
        "credential_kind": "NONE",
        "fallback_model_profile_revision_id": None,
    }


def _apply(service: ModelProfileService, control: ControlConfigService, project_id, *, role: str):
    active = control.get_active()
    permission = (
        "GRAPH_CHANGESET_DRAFT"
        if role == "CANVAS_AGENT"
        else "PROMPT_BATCH_ITEM_DRAFT"
    )
    pending = service.request_declaration(
        audit_project_id=project_id,
        base_config_revision_id=active.id,
        provider={
            "provider_key": f"{role.lower()}-loopback",
            "display_name": role,
            "adapter_kind": "OPENAI_COMPATIBLE_CHAT_JSON_V1",
        },
        profile_key=f"{role.lower()}-default",
        revision=_revision(role, permission),
        actor_id="local-control-admin",
    )
    control.approve(
        pending.id,
        expected_changeset_sha256=pending.changeset_sha256,
        actor_id="local-control-admin",
    )
    control.apply(
        pending.id,
        expected_changeset_sha256=pending.changeset_sha256,
        actor_id="local-control-admin",
    )
    return pending


def test_canvas_agent_and_prompt_editor_active_pointers_are_independent(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        session_factory = make_session_factory(engine)
        project = ProjectService(session_factory).create("C4 prompt editor")
        control = ControlConfigService(session_factory, EventSpineService(session_factory))
        service = ModelProfileService(control)

        canvas = _apply(service, control, project.id, role="CANVAS_AGENT")
        prompt = _apply(service, control, project.id, role="PROMPT_EDITOR")
        active = control.get_active()

        assert str(active.active_canvas_agent_profile_revision_id) == canvas.operations[0][
            "model_profile_revision_id"
        ]
        assert str(active.active_prompt_editor_profile_revision_id) == prompt.operations[0][
            "model_profile_revision_id"
        ]
    finally:
        engine.dispose()


def test_ai_canvas_actor_cannot_declare_prompt_editor(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        session_factory = make_session_factory(engine)
        project = ProjectService(session_factory).create("C4 actor boundary")
        control = ControlConfigService(session_factory, EventSpineService(session_factory))
        service = ModelProfileService(control)

        with pytest.raises(PermissionError, match="trusted control actor"):
            service.request_declaration(
                audit_project_id=project.id,
                base_config_revision_id=control.get_active().id,
                provider={
                    "provider_key": "prompt-editor-loopback",
                    "display_name": "Prompt editor",
                    "adapter_kind": "OPENAI_COMPATIBLE_CHAT_JSON_V1",
                },
                profile_key="prompt-editor-default",
                revision=_revision("PROMPT_EDITOR", "PROMPT_PROFILE_DRAFT"),
                actor_id="ai_canvas",
            )
    finally:
        engine.dispose()
