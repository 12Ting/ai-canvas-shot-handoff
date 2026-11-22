from pathlib import Path
from uuid import uuid4

from ai_canvas.application.agent_proposal_service import AgentProposalService
from ai_canvas.application.branch_service import BranchService
from ai_canvas.application.control_config_service import ControlConfigService
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_changeset_service import GraphChangeSetService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.model_profile_service import ModelProfileService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.run_control_service import RunControlService
from ai_canvas.contracts.agent_proposals import CreateAgentProposalRequest
from ai_canvas.contracts.graph import CreateGraphRevisionRequest
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)
from ai_canvas.infrastructure.model_providers.openai_compatible import ModelProviderResult


class FakeAdapter:
    def __init__(self, output: bytes) -> None:
        self.output = output
        self.calls = []

    def send(self, **kwargs):
        self.calls.append(kwargs)
        return ModelProviderResult(
            output_json=self.output,
            response_sha256="f" * 64,
            usage={"total_tokens": 12},
            latency_ms=3,
        )


def _activate_profile(project_id, control):
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
            "allowed_content_categories": ["USER_INSTRUCTION", "SELECTED_NODE_TEXT"],
            "retention_policy": "NO_RETENTION_DECLARED",
            "retention_note": "",
            "tool_permissions": ["GRAPH_CHANGESET_DRAFT", "RUN_REQUEST_DRAFT"],
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


def test_agent_graph_output_creates_pending_new_branch_changeset_once(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        projects = ProjectService(session_factory)
        project = projects.create("C3 Agent graph")
        node_id = uuid4()
        graphs = GraphService(session_factory)
        graphs.create_revision(
            project.id,
            CreateGraphRevisionRequest.model_validate(
                {
                    "base_revision_id": str(project.active_revision_id),
                    "client_mutation_id": str(uuid4()),
                    "snapshot": {
                        "schema_version": 2,
                        "nodes": [
                            {
                                "id": str(node_id),
                                "kind": "note",
                                "position": {"x": 1, "y": 2},
                                "data": {"text": "原文🙂保持"},
                            }
                        ],
                        "edges": [],
                        "shots": [],
                    },
                }
            ),
        )
        project = projects.get(project.id)
        workspace = BranchService(session_factory, graphs).workspace(project.id)
        selected = next(branch for branch in workspace.branches if branch.is_selected)
        events = EventSpineService(session_factory)
        control = ControlConfigService(session_factory, events)
        _activate_profile(project.id, control)
        adapter = FakeAdapter(
            (
                '{"kind":"graph_change_set_draft","operation":{"node_id":"'
                + str(node_id)
                + '","op":"replace_text","replacement_text":"新文"},'
                '"schema_version":1,"summary":"replace one node"}'
            ).encode("utf-8")
        )
        graph_changes = GraphChangeSetService(session_factory, graphs, events)
        service = AgentProposalService(
            session_factory,
            graphs,
            RunControlService(session_factory, events),
            graph_changes,
            events,
            adapter=adapter,
        )
        request = service.request(
            project.id,
            CreateAgentProposalRequest(
                base_graph_revision_id=project.active_revision_id,
                branch_id=project.selected_branch_id,
                expected_branch_head_revision_id=project.active_revision_id,
                expected_branch_head_version=selected.head.version,
                expected_selection_version=project.selection_version,
                node_id=node_id,
                scope={"kind": "NODE"},
                instruction="改写这个节点",
                context_categories=["USER_INSTRUCTION", "SELECTED_NODE_TEXT"],
                new_branch_name="AI 提案分支",
            ),
            actor_id="local-session",
        )
        assert len(adapter.calls) == 0
        service.approve(
            project.id,
            request.id,
            expected_request_sha256=request.run_request.request_sha256,
            actor_id="local-session",
        )
        assert len(adapter.calls) == 0
        completed = service.execute(project.id, request.id, actor_id="local-session")

        assert completed.state == "SUCCEEDED"
        assert len(adapter.calls) == 1
        assert completed.graph_change_set is not None
        assert completed.graph_change_set.state == "PENDING_APPROVAL"
        assert completed.graph_change_set.application_target.mode == "NEW_BRANCH"
        assert completed.graph_change_set.application_target.branch_name == "AI 提案分支"
        assert projects.get(project.id).active_revision_id == project.active_revision_id
    finally:
        engine.dispose()
