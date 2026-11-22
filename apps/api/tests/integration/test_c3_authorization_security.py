from pathlib import Path

import pytest
from sqlalchemy import select

from ai_canvas.application.control_config_service import ControlConfigService
from ai_canvas.application.errors import ApprovalSubjectStale
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.model_profile_service import ModelProfileService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.run_control_service import (
    RunAuthorizationConsumed,
    RunControlService,
)
from ai_canvas.domain.control.canonical import canonical_sha256
from ai_canvas.domain.model_endpoints.policy import validate_endpoint
from ai_canvas.infrastructure.db.models import (
    GraphBranchHeadRow,
    GraphSelectionPointerRow,
    RunAuthorizationRow,
)
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)
from ai_canvas.infrastructure.model_providers.credentials import CredentialUnavailable


def _activate_profile(project_id, control, model_profiles):
    active = control.get_active()
    pending = model_profiles.request_declaration(
        audit_project_id=project_id,
        base_config_revision_id=active.id,
        provider={
            "provider_key": "loopback",
            "display_name": "Loopback",
            "adapter_kind": "OPENAI_COMPATIBLE_CHAT_JSON_V1",
        },
        profile_key="canvas-agent-default",
        revision={
            "role": "CANVAS_AGENT",
            "model_id": "loopback-c3",
            "model_version": "1",
            "endpoint_url": "http://127.0.0.1:8765/v1/chat/completions",
            "locality": "LOCAL_LOOPBACK",
            "data_residency": "LOCAL_DEVICE",
            "allowed_content_categories": ["USER_INSTRUCTION"],
            "retention_policy": "NO_RETENTION_DECLARED",
            "retention_note": "",
            "tool_permissions": ["GRAPH_CHANGESET_DRAFT"],
            "cost_mode": "HARD",
            "currency": "CNY",
            "cost_bound_minor": 0,
            "max_output_tokens": 1024,
            "timeout_seconds": 10,
            "credential_kind": "NONE",
            "fallback_model_profile_revision_id": None,
        },
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


def _branch_binding(session_factory, project):
    with session_factory() as session:
        head = session.get(GraphBranchHeadRow, str(project.selected_branch_id))
        selection = session.get(GraphSelectionPointerRow, str(project.graph_document_id))
        assert head is not None and selection is not None
        return head.version, selection.version


def test_agent_authorization_binds_profile_scope_endpoint_and_consumes_once(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("C3 authorization")
        events = EventSpineService(session_factory)
        control = ControlConfigService(session_factory, events)
        _activate_profile(project.id, control, ModelProfileService(control))
        head_version, selection_version = _branch_binding(session_factory, project)
        service = RunControlService(session_factory, events)
        provider_payload = {"model": "loopback-c3", "messages": [], "stream": False}
        endpoint = validate_endpoint(
            "http://127.0.0.1:8765/v1/chat/completions",
            locality="LOCAL_LOOPBACK",
        )

        request = service.request_agent(
            project.id,
            branch_id=project.selected_branch_id,
            expected_head_revision_id=project.active_revision_id,
            expected_head_version=head_version,
            expected_selection_version=selection_version,
            scope={"kind": "NODE", "node_id": "11111111-1111-4111-8111-111111111111"},
            provider_payload=provider_payload,
            payload_sha256=canonical_sha256(provider_payload),
            endpoint_policy=endpoint,
            preview={"contexts": [{"category": "USER_INSTRUCTION", "sent_characters": 4}]},
            actor_id="local-session",
        )
        assert request.egress_plan.mode == "MODEL_PROVIDER"
        assert request.egress_plan.endpoint_identity_sha256 == endpoint.identity_sha256
        assert request.egress_plan.task_count == 1
        assert request.egress_plan.max_retries == 0

        authorization = service.approve_agent(
            project.id,
            request.id,
            expected_request_sha256=request.request_sha256,
            actor_id="local-session",
        )
        with pytest.raises(CredentialUnavailable):
            service.consume_agent(
                project.id,
                request.id,
                endpoint_identity_sha256=endpoint.identity_sha256,
                credential_available=False,
                actor_id="local-session",
            )
        consumed = service.consume_agent(
            project.id,
            request.id,
            endpoint_identity_sha256=endpoint.identity_sha256,
            credential_available=True,
            actor_id="local-session",
        )
        assert consumed.id == authorization.id
        assert consumed.state == "CONSUMED"
        with pytest.raises(RunAuthorizationConsumed):
            service.consume_agent(
                project.id,
                request.id,
                endpoint_identity_sha256=endpoint.identity_sha256,
                credential_available=True,
                actor_id="local-session",
            )
    finally:
        engine.dispose()


def test_agent_approval_rejects_changed_selection_before_authorization(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("C3 stale selection")
        events = EventSpineService(session_factory)
        control = ControlConfigService(session_factory, events)
        _activate_profile(project.id, control, ModelProfileService(control))
        head_version, selection_version = _branch_binding(session_factory, project)
        service = RunControlService(session_factory, events)
        payload = {"model": "loopback-c3", "messages": [], "stream": False}
        endpoint = validate_endpoint(
            "http://127.0.0.1:8765/v1/chat/completions",
            locality="LOCAL_LOOPBACK",
        )
        request = service.request_agent(
            project.id,
            branch_id=project.selected_branch_id,
            expected_head_revision_id=project.active_revision_id,
            expected_head_version=head_version,
            expected_selection_version=selection_version,
            scope={"kind": "NODE", "node_id": "11111111-1111-4111-8111-111111111111"},
            provider_payload=payload,
            payload_sha256=canonical_sha256(payload),
            endpoint_policy=endpoint,
            preview={"contexts": []},
            actor_id="local-session",
        )
        with session_factory.begin() as session:
            pointer = session.get(GraphSelectionPointerRow, str(project.graph_document_id))
            assert pointer is not None
            pointer.version += 1

        with pytest.raises(ApprovalSubjectStale):
            service.approve_agent(
                project.id,
                request.id,
                expected_request_sha256=request.request_sha256,
                actor_id="local-session",
            )
        with session_factory() as session:
            assert (
                session.scalar(
                    select(RunAuthorizationRow).where(
                        RunAuthorizationRow.run_request_id == str(request.id)
                    )
                )
                is None
            )
    finally:
        engine.dispose()
