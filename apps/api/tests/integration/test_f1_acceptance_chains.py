from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

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


def _approved_graph_change(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
) -> dict:
    active = client.get(
        f"/api/v1/projects/{project_id}/graph", headers=headers
    ).json()
    proposal = client.post(
        f"/api/v1/projects/{project_id}/graph-change-sets",
        json={
            "kind": "graph_change_set",
            "schema_version": 1,
            "base_graph_revision_id": active["revision_id"],
            "client_mutation_id": str(uuid4()),
            "operations": [
                {"op": "replace_snapshot", "snapshot": active["snapshot"]}
            ],
        },
        headers=headers,
    )
    assert proposal.status_code == 201, proposal.text
    change = proposal.json()
    approved = client.post(
        f"/api/v1/projects/{project_id}/graph-change-sets/{change['id']}/approval-decisions",
        json={"expected_changeset_sha256": change["changeset_sha256"]},
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["created_at"].endswith("Z")
    applied = client.post(
        f"/api/v1/projects/{project_id}/graph-change-sets/{change['id']}/apply",
        json={"expected_changeset_sha256": change["changeset_sha256"]},
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["created_at"].endswith("Z")
    assert applied.json()["applied_graph_revision_id"] != active["revision_id"]
    return change


def _approved_config_change(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
) -> dict:
    current = client.get("/api/v1/control/config-revision", headers=headers)
    assert current.status_code == 200, current.text
    assert current.json()["created_at"].endswith("Z")
    proposal = client.post(
        "/api/v1/control/config-change-sets",
        json={
            "kind": "config_change_set",
            "schema_version": 1,
            "audit_project_id": project_id,
            "base_config_revision_id": current.json()["id"],
            "operations": [
                {"op": "replace", "path": "/dry_run_max_tasks", "value": 2}
            ],
        },
        headers=headers,
    )
    assert proposal.status_code == 201, proposal.text
    change = proposal.json()
    assert change["created_at"].endswith("Z")
    approved = client.post(
        f"/api/v1/control/config-change-sets/{change['id']}/approval-decisions",
        json={"expected_changeset_sha256": change["changeset_sha256"]},
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["created_at"].endswith("Z")
    applied = client.post(
        f"/api/v1/control/config-change-sets/{change['id']}/apply",
        json={"expected_changeset_sha256": change["changeset_sha256"]},
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["created_at"].endswith("Z")
    return change


def _approved_no_egress_dry_run(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
) -> dict:
    requested = client.post(
        f"/api/v1/projects/{project_id}/run-requests",
        json={"operation_kind": "DRY_RUN", "task_count": 1},
        headers=headers,
    )
    assert requested.status_code == 201, requested.text
    run_request = requested.json()
    approved = client.post(
        f"/api/v1/projects/{project_id}/run-requests/{run_request['id']}/approval-decisions",
        json={"expected_request_sha256": run_request["request_sha256"]},
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["expires_at"].endswith("Z")
    receipt = client.post(
        f"/api/v1/projects/{project_id}/run-requests/{run_request['id']}/dry-run",
        json={},
        headers=headers,
    )
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["terminal_state"] == "DRY_RUN_SUCCEEDED"
    assert receipt.json()["created_at"].endswith("Z")
    repeated = client.post(
        f"/api/v1/projects/{project_id}/run-requests/{run_request['id']}/dry-run",
        json={},
        headers=headers,
    )
    assert repeated.status_code == 409, repeated.text
    assert repeated.json() == {"code": "run_authorization_consumed"}
    return run_request


def _event_types_after_restart(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    correlation_id: str,
) -> list[str]:
    response = client.get(
        f"/api/v1/projects/{project_id}/experience-events",
        params={"after": 0, "limit": 100, "correlation_id": correlation_id},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    events = response.json()["events"]
    assert all(event["occurred_at"].endswith("Z") for event in events)
    return [event["event_type"] for event in events]


def test_f1_three_approved_chains_persist_across_restart(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, public_origin="http://testserver")
    first_app = create_app(settings)
    with TestClient(first_app, base_url="http://testserver") as client:
        headers = _authorized_headers(first_app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "F1 accepted chain witness"},
            headers=headers,
        )
        assert project.status_code == 201, project.text
        project_id = project.json()["id"]
        graph = _approved_graph_change(client, headers, project_id)
        config = _approved_config_change(client, headers, project_id)
        run = _approved_no_egress_dry_run(client, headers, project_id)

    restarted_app = create_app(settings)
    with TestClient(restarted_app, base_url="http://testserver") as client:
        headers = _authorized_headers(restarted_app, client)
        assert _event_types_after_restart(
            client, headers, project_id, graph["correlation_id"]
        ) == [
            "graph_change_set.requested",
            "approval.recorded",
            "graph_branch.head_advanced",
            "graph_change_set.applied",
        ]
        assert _event_types_after_restart(
            client, headers, project_id, config["correlation_id"]
        ) == [
            "config_change_set.requested",
            "approval.recorded",
            "config_change_set.applied",
        ]
        assert _event_types_after_restart(
            client, headers, project_id, run["correlation_id"]
        ) == [
            "run_request.requested",
            "egress_plan.sealed",
            "approval.recorded",
            "run_authorization.issued",
            "run_authorization.consumed",
            "run.dry_run_succeeded",
        ]
