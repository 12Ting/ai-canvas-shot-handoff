from uuid import uuid4

from fastapi.testclient import TestClient

from ai_canvas.api.security import RequestActor
from ai_canvas.config import Settings
from ai_canvas.main import create_app


def _authorized_headers(app, client: TestClient) -> dict[str, str]:
    exchanged = client.post(
        "/api/v1/session/exchange",
        headers={
            "X-Canvas-Bootstrap": app.state.session_manager.bootstrap_token,
            "Origin": "http://testserver",
        },
    )
    assert exchanged.status_code == 200
    return {
        "Authorization": f"Bearer {exchanged.json()['access_token']}",
        "Origin": "http://testserver",
    }


def test_ai_canvas_actor_can_propose_graph_but_cannot_submit_control_config(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    app.state.f1_actor_resolver = lambda _request: RequestActor.ai_canvas("assistant-1")
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "F1 domain separation"},
            headers=headers,
        ).json()
        active_graph = client.get(f"/api/v1/projects/{project['id']}/graph", headers=headers).json()
        active_config = client.get("/api/v1/control/config-revision", headers=headers).json()

        graph_proposal = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets",
            json={
                "kind": "graph_change_set",
                "schema_version": 1,
                "base_graph_revision_id": active_graph["revision_id"],
                "client_mutation_id": str(uuid4()),
                "operations": [
                    {
                        "op": "replace_snapshot",
                        "snapshot": active_graph["snapshot"],
                    }
                ],
            },
            headers=headers,
        )
        config_proposal = client.post(
            "/api/v1/control/config-change-sets",
            json={
                "kind": "config_change_set",
                "schema_version": 1,
                "audit_project_id": project["id"],
                "base_config_revision_id": active_config["id"],
                "operations": [{"op": "replace", "path": "/dry_run_max_tasks", "value": 2}],
            },
            headers=headers,
        )

    assert graph_proposal.status_code == 201, graph_proposal.text
    assert config_proposal.status_code == 403, config_proposal.text
    assert config_proposal.json() == {"code": "config_changeset_forbidden"}


def test_ai_canvas_graph_and_run_proposals_keep_ai_actor_attribution(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    app.state.f1_actor_resolver = lambda _request: RequestActor.ai_canvas("assistant-1")
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post("/api/v1/projects", json={"name": "AI attribution"}, headers=headers).json()
        active = client.get(f"/api/v1/projects/{project['id']}/graph", headers=headers).json()
        graph_proposal = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets",
            json={
                "kind": "graph_change_set",
                "schema_version": 1,
                "base_graph_revision_id": active["revision_id"],
                "client_mutation_id": str(uuid4()),
                "operations": [{"op": "replace_snapshot", "snapshot": active["snapshot"]}],
            },
            headers=headers,
        ).json()
        run_request = client.post(
            f"/api/v1/projects/{project['id']}/run-requests",
            json={"operation_kind": "DRY_RUN", "task_count": 1},
            headers=headers,
        ).json()

        app.state.f1_actor_resolver = lambda _request: RequestActor.local_human("local-session")
        graph_events = client.get(
            f"/api/v1/projects/{project['id']}/experience-events",
            params={"correlation_id": graph_proposal["correlation_id"]},
            headers=headers,
        ).json()["events"]
        run_events = client.get(
            f"/api/v1/projects/{project['id']}/experience-events",
            params={"correlation_id": run_request["correlation_id"]},
            headers=headers,
        ).json()["events"]

    assert graph_events[0]["actor_kind"] == "AI_CANVAS"
    assert graph_events[0]["actor_id"] == "assistant-1"
    assert run_events[0]["actor_kind"] == "AI_CANVAS"
    assert run_events[0]["actor_id"] == "assistant-1"


def test_f1_approval_hash_mismatches_return_one_stable_code_in_all_domains(
    tmp_path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as client:
        headers = _authorized_headers(app, client)
        project = client.post("/api/v1/projects", json={"name": "Stable stale codes"}, headers=headers).json()
        graph = client.get(f"/api/v1/projects/{project['id']}/graph", headers=headers).json()
        config = client.get("/api/v1/control/config-revision", headers=headers).json()
        graph_change = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets",
            json={
                "kind": "graph_change_set",
                "schema_version": 1,
                "base_graph_revision_id": graph["revision_id"],
                "client_mutation_id": str(uuid4()),
                "operations": [{"op": "replace_snapshot", "snapshot": graph["snapshot"]}],
            },
            headers=headers,
        ).json()
        config_change = client.post(
            "/api/v1/control/config-change-sets",
            json={
                "kind": "config_change_set",
                "schema_version": 1,
                "audit_project_id": project["id"],
                "base_config_revision_id": config["id"],
                "operations": [{"op": "replace", "path": "/dry_run_max_tasks", "value": 2}],
            },
            headers=headers,
        ).json()
        run_request = client.post(
            f"/api/v1/projects/{project['id']}/run-requests",
            json={"operation_kind": "DRY_RUN", "task_count": 1},
            headers=headers,
        ).json()
        wrong_hash = "0" * 64
        responses = [
            client.post(
                f"/api/v1/projects/{project['id']}/graph-change-sets/{graph_change['id']}/approval-decisions",
                json={"expected_changeset_sha256": wrong_hash},
                headers=headers,
            ),
            client.post(
                f"/api/v1/control/config-change-sets/{config_change['id']}/approval-decisions",
                json={"expected_changeset_sha256": wrong_hash},
                headers=headers,
            ),
            client.post(
                f"/api/v1/projects/{project['id']}/run-requests/{run_request['id']}/approval-decisions",
                json={"expected_request_sha256": wrong_hash},
                headers=headers,
            ),
        ]

    assert [response.status_code for response in responses] == [409, 409, 409]
    assert [response.json() for response in responses] == [
        {"code": "approval_subject_stale"},
        {"code": "approval_subject_stale"},
        {"code": "approval_subject_stale"},
    ]
