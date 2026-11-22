from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ai_canvas.application.branch_service import BranchService
from ai_canvas.application.model_profile_service import ModelProfileService
from ai_canvas.config import Settings
from ai_canvas.contracts.graph import CreateGraphRevisionRequest
from ai_canvas.infrastructure.model_providers.openai_compatible import ModelProviderResult
from ai_canvas.infrastructure.db.models import ArtifactRow, GraphChangeSetRow, RunRequestRow
from ai_canvas.main import create_app


class FakeAdapter:
    def __init__(self, output: bytes) -> None:
        self.output = output
        self.calls = 0

    def send(self, **kwargs):
        self.calls += 1
        return ModelProviderResult(
            output_json=self.output,
            response_sha256="e" * 64,
            usage={},
            latency_ms=1,
        )


def _activate_profile(app, project_id):
    control = app.state.control_config_service
    active = control.get_active()
    pending = ModelProfileService(control).request_declaration(
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
            "allowed_content_categories": ["USER_INSTRUCTION", "SELECTED_NODE_TEXT"],
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


def test_agent_routes_review_approve_then_execute_exactly_once(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    token = app.state.session_manager.exchange(app.state.session_manager.bootstrap_token)
    headers = {
        "Authorization": f"Bearer {token}",
        "Origin": "http://testserver",
    }
    with TestClient(app, base_url="http://testserver") as client:
        projects = app.state.project_service
        project = projects.create("C3 agent route")
        node_id = uuid4()
        graph = app.state.graph_service.create_revision(
            project.id,
            CreateGraphRevisionRequest.model_validate(
                {
                    "base_revision_id": project.active_revision_id,
                    "client_mutation_id": uuid4(),
                    "snapshot": {
                        "schema_version": 2,
                        "nodes": [
                            {
                                "id": node_id,
                                "kind": "note",
                                "position": {"x": 0, "y": 0},
                                "data": {"text": "route witness"},
                            }
                        ],
                        "edges": [],
                        "shots": [],
                    },
                }
            ),
        )
        project = projects.get(project.id)
        workspace = BranchService(
            app.state.agent_proposal_service.session_factory,
            app.state.graph_service,
        ).workspace(project.id)
        selected = next(branch for branch in workspace.branches if branch.is_selected)
        _activate_profile(app, project.id)
        adapter = FakeAdapter(
            (
                '{"kind":"run_request_draft","operation_kind":"DRY_RUN",'
                f'"reason":"review only","schema_version":1,"target_node_ids":["{node_id}"],'
                '"task_count":1}'
            ).encode("utf-8")
        )
        app.state.agent_proposal_service.adapter = adapter

        created = client.post(
            f"/api/v1/projects/{project.id}/agent-proposals",
            json={
                "base_graph_revision_id": str(graph.revision_id),
                "branch_id": str(project.selected_branch_id),
                "expected_branch_head_revision_id": str(graph.revision_id),
                "expected_branch_head_version": selected.head.version,
                "expected_selection_version": project.selection_version,
                "node_id": str(node_id),
                "scope": {"kind": "NODE"},
                "instruction": "review this node",
                "context_categories": ["USER_INSTRUCTION", "SELECTED_NODE_TEXT"],
                "new_branch_name": "route proposal",
            },
            headers=headers,
        )
        assert created.status_code == 201
        assert adapter.calls == 0
        proposal = created.json()
        approved = client.post(
            f"/api/v1/projects/{project.id}/agent-proposals/{proposal['id']}/approval-decisions",
            json={
                "decision": "APPROVED",
                "expected_request_sha256": proposal["run_request"]["request_sha256"],
            },
            headers=headers,
        )
        assert approved.status_code == 200
        assert approved.json()["state"] == "PENDING_EXECUTION"
        assert adapter.calls == 0
        executed = client.post(
            f"/api/v1/projects/{project.id}/agent-proposals/{proposal['id']}/execute",
            json={"kind": "execute_agent_proposal"},
            headers=headers,
        )
        with app.state.agent_proposal_service.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(RunRequestRow)) == 1
            assert session.scalar(select(func.count()).select_from(GraphChangeSetRow)) == 0
            assert session.scalar(select(func.count()).select_from(ArtifactRow)) == 0

    assert executed.status_code == 200
    assert executed.json()["state"] == "SUCCEEDED"
    assert executed.json()["output"]["kind"] == "run_request_draft"
    assert executed.json()["graph_change_set"] is None
    assert adapter.calls == 1


def test_agent_routes_return_stable_not_found_problem_without_internal_detail(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    token = app.state.session_manager.exchange(app.state.session_manager.bootstrap_token)
    headers = {"Authorization": f"Bearer {token}", "Origin": "http://testserver"}
    with TestClient(app, base_url="http://testserver") as client:
        project = app.state.project_service.create("C3 missing proposal")
        missing = client.get(
            f"/api/v1/projects/{project.id}/agent-proposals/{uuid4()}",
            headers=headers,
        )
    assert missing.status_code == 404
    assert missing.json() == {"code": "agent_proposal_not_found"}
