from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from ai_canvas.application.branch_service import BranchService
from ai_canvas.application.model_profile_service import ModelProfileService
from ai_canvas.config import Settings
from ai_canvas.contracts.graph import CreateGraphRevisionRequest
from ai_canvas.infrastructure.model_providers.base import ProviderHttpResponse
from ai_canvas.infrastructure.model_providers.openai_compatible import OpenAICompatibleChatJsonAdapter
from ai_canvas.main import create_app


class CapturingTransport:
    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        self.authorization = ""

    def send(self, request):
        self.authorization = request.headers.get("Authorization", "")
        output = (
            '{"choices":[{"message":{"content":"{\\"kind\\":\\"run_request_draft\\",'
            '\\"schema_version\\":1,\\"operation_kind\\":\\"DRY_RUN\\",'
            f'\\"target_node_ids\\":[\\"{self.node_id}\\"],\\"task_count\\":1,'
            '\\"reason\\":\\"review only\\"}"}}],"usage":{"total_tokens":1}}'
        )
        return ProviderHttpResponse(status_code=200, body=output.encode(), headers={})


def test_runtime_secret_is_absent_from_persistence_responses_events_and_build_surfaces(
    tmp_path: Path,
) -> None:
    canary = "C3_CANARY_SECRET_must_never_persist_9f2d"
    app = create_app(
        Settings(
            data_dir=tmp_path,
            public_origin="http://testserver",
            canvas_agent_api_key=SecretStr(canary),
        )
    )
    token = app.state.session_manager.exchange(app.state.session_manager.bootstrap_token)
    headers = {"Authorization": f"Bearer {token}", "Origin": "http://testserver"}
    with TestClient(app, base_url="http://testserver") as client:
        project = app.state.project_service.create("C3 secret witness")
        node_id = uuid4()
        graph = app.state.graph_service.create_revision(
            project.id,
            CreateGraphRevisionRequest.model_validate(
                {
                    "base_revision_id": project.active_revision_id,
                    "client_mutation_id": uuid4(),
                    "snapshot": {
                        "schema_version": 2,
                        "nodes": [{"id": node_id, "kind": "note", "position": {"x": 0, "y": 0}, "data": {"text": "secret-free"}}],
                        "edges": [],
                        "shots": [],
                    },
                }
            ),
        )
        project = app.state.project_service.get(project.id)
        workspace = BranchService(
            app.state.agent_proposal_service.session_factory,
            app.state.graph_service,
        ).workspace(project.id)
        selected = next(branch for branch in workspace.branches if branch.is_selected)
        control = app.state.control_config_service
        pending_profile = ModelProfileService(control).request_declaration(
            audit_project_id=project.id,
            base_config_revision_id=control.get_active().id,
            provider={"provider_key": "loopback", "display_name": "Loopback", "adapter_kind": "OPENAI_COMPATIBLE_CHAT_JSON_V1"},
            profile_key="canvas-agent-secret-witness",
            revision={
                "role": "CANVAS_AGENT", "model_id": "loopback-c3", "model_version": "1",
                "endpoint_url": "http://127.0.0.1:8765/v1/chat/completions", "locality": "LOCAL_LOOPBACK",
                "data_residency": "LOCAL_DEVICE", "allowed_content_categories": ["USER_INSTRUCTION", "SELECTED_NODE_TEXT"],
                "retention_policy": "NO_RETENTION_DECLARED", "retention_note": "", "tool_permissions": ["RUN_REQUEST_DRAFT"],
                "cost_mode": "HARD", "currency": "CNY", "cost_bound_minor": 0, "max_output_tokens": 512,
                "timeout_seconds": 5, "credential_kind": "BEARER_ENV", "fallback_model_profile_revision_id": None,
            },
            actor_id="local-control-admin",
        )
        control.approve(pending_profile.id, expected_changeset_sha256=pending_profile.changeset_sha256, actor_id="local-control-admin")
        control.apply(pending_profile.id, expected_changeset_sha256=pending_profile.changeset_sha256, actor_id="local-control-admin")
        transport = CapturingTransport(str(node_id))
        app.state.agent_proposal_service.adapter = OpenAICompatibleChatJsonAdapter(transport=transport)

        created = client.post(
            f"/api/v1/projects/{project.id}/agent-proposals",
            json={
                "base_graph_revision_id": str(graph.revision_id), "branch_id": str(project.selected_branch_id),
                "expected_branch_head_revision_id": str(graph.revision_id), "expected_branch_head_version": selected.head.version,
                "expected_selection_version": project.selection_version, "node_id": str(node_id), "scope": {"kind": "NODE"},
                "instruction": "review", "context_categories": ["USER_INSTRUCTION", "SELECTED_NODE_TEXT"],
                "new_branch_name": "secret witness",
            }, headers=headers,
        )
        proposal = created.json()
        approved = client.post(
            f"/api/v1/projects/{project.id}/agent-proposals/{proposal['id']}/approval-decisions",
            json={"decision": "APPROVED", "expected_request_sha256": proposal["run_request"]["request_sha256"]}, headers=headers,
        )
        executed = client.post(
            f"/api/v1/projects/{project.id}/agent-proposals/{proposal['id']}/execute",
            json={"kind": "execute_agent_proposal"}, headers=headers,
        )
        events = client.get(f"/api/v1/projects/{project.id}/experience-events", headers=headers)
        response_surface = created.content + approved.content + executed.content + events.content

    assert transport.authorization == f"Bearer {canary}"
    assert canary.encode() not in response_surface
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert canary.encode() not in path.read_bytes(), path
