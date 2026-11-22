from pathlib import Path

import pytest
from sqlalchemy import func, select

from ai_canvas.application.control_config_service import ControlConfigService
from ai_canvas.application.errors import ConfigChangeSetBaseRevisionStale
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.model_profile_service import ModelProfileService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.infrastructure.db.models import (
    ModelProfileRevisionRow,
    ModelProfileRow,
    ProviderRow,
)
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


def _profile_input(endpoint: str = "http://127.0.0.1:8765/v1/chat/completions") -> dict:
    return {
        "role": "CANVAS_AGENT",
        "model_id": "c3-loopback",
        "model_version": "1",
        "endpoint_url": endpoint,
        "locality": "LOCAL_LOOPBACK",
        "data_residency": "LOCAL_DEVICE",
        "allowed_content_categories": ["USER_INSTRUCTION", "SELECTED_NODE_TEXT"],
        "retention_policy": "NO_RETENTION_DECLARED",
        "retention_note": "loopback witness",
        "tool_permissions": ["GRAPH_CHANGESET_DRAFT", "RUN_REQUEST_DRAFT"],
        "cost_mode": "HARD",
        "currency": "CNY",
        "cost_bound_minor": 0,
        "max_output_tokens": 2048,
        "timeout_seconds": 15,
        "credential_kind": "NONE",
        "fallback_model_profile_revision_id": None,
    }


def test_profile_declaration_is_applied_atomically_through_config_changeset(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("C3 model profile")
        events = EventSpineService(session_factory)
        control = ControlConfigService(session_factory, events)
        service = ModelProfileService(control)
        before = control.get_active()

        pending = service.request_declaration(
            audit_project_id=project.id,
            base_config_revision_id=before.id,
            provider={
                "provider_key": "loopback",
                "display_name": "C3 Loopback",
                "adapter_kind": "OPENAI_COMPATIBLE_CHAT_JSON_V1",
            },
            profile_key="canvas-agent-default",
            revision=_profile_input(),
            actor_id="local-control-admin",
        )
        assert pending.state == "PENDING_APPROVAL"
        assert pending.operations[0]["op"] == "declare_model_profile_revision"
        assert pending.risk_summary["classes"] == ["CONTROL_CONFIG_WRITE", "MODEL_EGRESS"]

        control.approve(
            pending.id,
            expected_changeset_sha256=pending.changeset_sha256,
            actor_id="local-control-admin",
        )
        applied = control.apply(
            pending.id,
            expected_changeset_sha256=pending.changeset_sha256,
            actor_id="local-control-admin",
        )
        active = control.get_active()

        assert applied.state == "APPLIED"
        assert active.id != before.id
        assert str(active.active_canvas_agent_profile_revision_id) == pending.operations[0][
            "model_profile_revision_id"
        ]
        with session_factory() as session:
            assert session.scalar(select(func.count()).select_from(ProviderRow)) == 1
            assert session.scalar(select(func.count()).select_from(ModelProfileRow)) == 1
            revision = session.scalar(select(ModelProfileRevisionRow))
            assert revision is not None
            assert revision.declaration_sha256 == pending.operations[0]["declaration_sha256"]
            assert "secret" not in revision.declaration_json.lower()
            assert "api_key" not in revision.declaration_json.lower()
    finally:
        engine.dispose()


def test_profile_declaration_rejects_stale_base_without_partial_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("C3 stale profile")
        events = EventSpineService(session_factory)
        control = ControlConfigService(session_factory, events)
        service = ModelProfileService(control)
        stale = control.get_active().id
        first = service.request_declaration(
            audit_project_id=project.id,
            base_config_revision_id=stale,
            provider={
                "provider_key": "loopback",
                "display_name": "C3 Loopback",
                "adapter_kind": "OPENAI_COMPATIBLE_CHAT_JSON_V1",
            },
            profile_key="canvas-agent-default",
            revision=_profile_input(),
            actor_id="local-control-admin",
        )
        control.approve(
            first.id,
            expected_changeset_sha256=first.changeset_sha256,
            actor_id="local-control-admin",
        )
        control.apply(
            first.id,
            expected_changeset_sha256=first.changeset_sha256,
            actor_id="local-control-admin",
        )

        with pytest.raises(ConfigChangeSetBaseRevisionStale):
            service.request_declaration(
                audit_project_id=project.id,
                base_config_revision_id=stale,
                provider={
                    "provider_key": "other",
                    "display_name": "Other",
                    "adapter_kind": "OPENAI_COMPATIBLE_CHAT_JSON_V1",
                },
                profile_key="other-agent",
                revision=_profile_input(),
                actor_id="local-control-admin",
            )
        with session_factory() as session:
            assert session.scalar(select(func.count()).select_from(ProviderRow)) == 1
            assert session.scalar(select(func.count()).select_from(ModelProfileRevisionRow)) == 1
    finally:
        engine.dispose()
