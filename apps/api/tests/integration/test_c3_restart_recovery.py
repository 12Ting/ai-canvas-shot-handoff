import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from ai_canvas.application.agent_proposal_service import AgentProposalService
from ai_canvas.application.control_config_service import ControlConfigService
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_changeset_service import GraphChangeSetService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.model_profile_service import ModelProfileService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.run_control_service import RunControlService
from ai_canvas.domain.control.canonical import canonical_sha256
from ai_canvas.domain.model_endpoints.policy import validate_endpoint
from ai_canvas.infrastructure.db.models import AgentProposalRow, GraphBranchHeadRow, RunRequestRow
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


class NeverCalledAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, **kwargs):
        self.calls += 1
        raise AssertionError("startup reconciliation must never call provider")


def _activate(project_id, control):
    current = control.get_active()
    pending = ModelProfileService(control).request_declaration(
        audit_project_id=project_id,
        base_config_revision_id=current.id,
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
            "tool_permissions": ["RUN_REQUEST_DRAFT"],
            "cost_mode": "HARD",
            "currency": "CNY",
            "cost_bound_minor": 0,
            "max_output_tokens": 512,
            "timeout_seconds": 5,
            "credential_kind": "NONE",
            "fallback_model_profile_revision_id": None,
        },
        actor_id="admin",
    )
    control.approve(
        pending.id,
        expected_changeset_sha256=pending.changeset_sha256,
        actor_id="admin",
    )
    control.apply(
        pending.id,
        expected_changeset_sha256=pending.changeset_sha256,
        actor_id="admin",
    )


def test_dispatching_proposal_becomes_unknown_terminal_without_resend(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("C3 restart")
        events = EventSpineService(session_factory)
        control = ControlConfigService(session_factory, events)
        _activate(project.id, control)
        with session_factory() as session:
            head = session.get(GraphBranchHeadRow, str(project.selected_branch_id))
            assert head is not None
        payload = {"model": "loopback-c3", "messages": [], "stream": False}
        endpoint = validate_endpoint(
            "http://127.0.0.1:8765/v1/chat/completions",
            locality="LOCAL_LOOPBACK",
        )
        run_control = RunControlService(session_factory, events)
        run = run_control.request_agent(
            project.id,
            branch_id=project.selected_branch_id,
            expected_head_revision_id=project.active_revision_id,
            expected_head_version=head.version,
            expected_selection_version=project.selection_version,
            scope={"kind": "NODE"},
            provider_payload=payload,
            payload_sha256=canonical_sha256(payload),
            endpoint_policy=endpoint,
            preview={"contexts": []},
            actor_id="local-session",
        )
        now = run.egress_plan.preview.get("created_at")
        del now
        with session_factory.begin() as session:
            stored_run = session.get(RunRequestRow, str(run.id))
            row = AgentProposalRow(
                id=str(uuid4()),
                project_id=str(project.id),
                run_request_id=str(run.id),
                model_profile_revision_id=str(run.model_profile_revision_id),
                branch_id=str(project.selected_branch_id),
                branch_head_version=head.version,
                selection_version=project.selection_version,
                node_id=str(uuid4()),
                scope_json=json.dumps({"scope": {"kind": "NODE"}}),
                scope_sha256=run.scope_sha256,
                provider_payload_json=json.dumps(payload, separators=(",", ":"), sort_keys=True),
                provider_payload_sha256=stored_run.payload_sha256,
                state="DISPATCHING",
                output_json=None,
                output_sha256=None,
                graph_change_set_id=None,
                failure_code=None,
                created_at=stored_run.created_at,
                updated_at=stored_run.created_at,
                dispatched_at=stored_run.created_at,
                completed_at=None,
            )
            session.add(row)
            proposal_id = row.id
        adapter = NeverCalledAdapter()
        service = AgentProposalService(
            session_factory,
            GraphService(session_factory),
            run_control,
            GraphChangeSetService(
                session_factory,
                GraphService(session_factory),
                events,
            ),
            events,
            adapter=adapter,
        )

        assert service.reconcile_dispatching() == 1
        assert service.reconcile_dispatching() == 0
        assert adapter.calls == 0
        recovered = service.get(project.id, proposal_id)
        assert recovered.state == "UNKNOWN_TERMINAL"
        assert recovered.failure_code == "DISPATCH_OUTCOME_UNKNOWN_AFTER_RESTART"
        with session_factory() as session:
            assert session.get(RunRequestRow, str(run.id)).state == "TERMINAL"
            events_for_run = list(
                session.scalars(
                    select(AgentProposalRow).where(AgentProposalRow.id == proposal_id)
                )
            )
            assert len(events_for_run) == 1
    finally:
        engine.dispose()
