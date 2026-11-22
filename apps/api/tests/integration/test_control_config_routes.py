from fastapi.testclient import TestClient
from uuid import uuid4

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


def test_control_config_change_set_is_available_only_under_control_routes(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Control config route"},
            headers=headers,
        ).json()
        active = client.get(
            "/api/v1/control/config-revision",
            headers=headers,
        )
        proposal = client.post(
            "/api/v1/control/config-change-sets",
            json={
                "kind": "config_change_set",
                "schema_version": 1,
                "audit_project_id": project["id"],
                "base_config_revision_id": active.json().get("id"),
                "operations": [{"op": "replace", "path": "/dry_run_max_tasks", "value": 2}],
            },
            headers=headers,
        )

    assert active.status_code == 200, active.text
    assert proposal.status_code == 201, proposal.text
    assert proposal.json()["state"] == "PENDING_APPROVAL"


def test_control_config_route_approval_and_apply_move_only_config_pointer(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Control config approval"},
            headers=headers,
        ).json()
        current = client.get("/api/v1/control/config-revision", headers=headers).json()
        proposal = client.post(
            "/api/v1/control/config-change-sets",
            json={
                "kind": "config_change_set",
                "schema_version": 1,
                "audit_project_id": project["id"],
                "base_config_revision_id": current["id"],
                "operations": [{"op": "replace", "path": "/dry_run_max_tasks", "value": 2}],
            },
            headers=headers,
        ).json()

        approved = client.post(
            f"/api/v1/control/config-change-sets/{proposal['id']}/approval-decisions",
            json={"expected_changeset_sha256": proposal["changeset_sha256"]},
            headers=headers,
        )
        applied = client.post(
            f"/api/v1/control/config-change-sets/{proposal['id']}/apply",
            json={"expected_changeset_sha256": proposal["changeset_sha256"]},
            headers=headers,
        )
        active = client.get("/api/v1/control/config-revision", headers=headers)

    assert approved.status_code == 200, approved.text
    assert applied.status_code == 200, applied.text
    assert applied.json()["state"] == "APPLIED"
    assert active.json()["dry_run_max_tasks"] == 2


def test_stale_config_apply_records_rejected_terminal_with_stable_code(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects", json={"name": "Config stale terminal"}, headers=headers
        ).json()
        current = client.get("/api/v1/control/config-revision", headers=headers).json()

        def proposal(value: int) -> dict:
            return client.post(
                "/api/v1/control/config-change-sets",
                json={
                    "kind": "config_change_set",
                    "schema_version": 1,
                    "audit_project_id": project["id"],
                    "base_config_revision_id": current["id"],
                    "operations": [{"op": "replace", "path": "/dry_run_max_tasks", "value": value}],
                },
                headers=headers,
            ).json()

        winner = proposal(2)
        stale = proposal(3)
        for item in (winner, stale):
            response = client.post(
                f"/api/v1/control/config-change-sets/{item['id']}/approval-decisions",
                json={"expected_changeset_sha256": item["changeset_sha256"]},
                headers=headers,
            )
            assert response.status_code == 200, response.text
        applied = client.post(
            f"/api/v1/control/config-change-sets/{winner['id']}/apply",
            json={"expected_changeset_sha256": winner["changeset_sha256"]},
            headers=headers,
        )
        assert applied.status_code == 200, applied.text

        rejected = client.post(
            f"/api/v1/control/config-change-sets/{stale['id']}/apply",
            json={"expected_changeset_sha256": stale["changeset_sha256"]},
            headers=headers,
        )
        events = client.get(
            f"/api/v1/projects/{project['id']}/experience-events",
            params={"correlation_id": stale["correlation_id"]},
            headers=headers,
        ).json()["events"]

    assert rejected.status_code == 409, rejected.text
    assert rejected.json() == {"code": "config_base_revision_stale"}
    assert events[-1]["event_type"] == "config_change_set.rejected"
    assert events[-1]["payload"]["reason"] == "BASE_REVISION_STALE"


def test_change_set_ids_cannot_cross_graph_and_config_domains(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as client:
        headers = _authorized_headers(app, client)
        project = client.post("/api/v1/projects", json={"name": "Domain IDs"}, headers=headers).json()
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

        graph_with_config_id = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets/{config_change['id']}/approval-decisions",
            json={"expected_changeset_sha256": config_change["changeset_sha256"]},
            headers=headers,
        )
        config_with_graph_id = client.post(
            f"/api/v1/control/config-change-sets/{graph_change['id']}/approval-decisions",
            json={"expected_changeset_sha256": graph_change["changeset_sha256"]},
            headers=headers,
        )

    assert graph_with_config_id.status_code == 409, graph_with_config_id.text
    assert config_with_graph_id.status_code == 409, config_with_graph_id.text
    assert graph_with_config_id.json() == {"code": "changeset_domain_mismatch"}
    assert config_with_graph_id.json() == {"code": "changeset_domain_mismatch"}


def test_config_proposal_with_stale_base_returns_stable_code(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects", json={"name": "Config stale request"}, headers=headers
        ).json()
        response = client.post(
            "/api/v1/control/config-change-sets",
            json={
                "kind": "config_change_set",
                "schema_version": 1,
                "audit_project_id": project["id"],
                "base_config_revision_id": str(uuid4()),
                "operations": [{"op": "replace", "path": "/dry_run_max_tasks", "value": 2}],
            },
            headers=headers,
        )

    assert response.status_code == 409, response.text
    assert response.json() == {"code": "config_base_revision_stale"}


def test_config_changeset_missing_and_approval_required_have_domain_codes(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects", json={"name": "Config stable errors"}, headers=headers
        ).json()
        current = client.get("/api/v1/control/config-revision", headers=headers).json()
        proposal = client.post(
            "/api/v1/control/config-change-sets",
            json={
                "kind": "config_change_set",
                "schema_version": 1,
                "audit_project_id": project["id"],
                "base_config_revision_id": current["id"],
                "operations": [{"op": "replace", "path": "/dry_run_max_tasks", "value": 2}],
            },
            headers=headers,
        ).json()
        approval_required = client.post(
            f"/api/v1/control/config-change-sets/{proposal['id']}/apply",
            json={"expected_changeset_sha256": proposal["changeset_sha256"]},
            headers=headers,
        )
        missing_id = uuid4()
        missing_approval = client.post(
            f"/api/v1/control/config-change-sets/{missing_id}/approval-decisions",
            json={"expected_changeset_sha256": "a" * 64},
            headers=headers,
        )
        missing_apply = client.post(
            f"/api/v1/control/config-change-sets/{missing_id}/apply",
            json={"expected_changeset_sha256": "a" * 64},
            headers=headers,
        )

    assert approval_required.status_code == 409, approval_required.text
    assert approval_required.json() == {"code": "approval_required"}
    for response in (missing_approval, missing_apply):
        assert response.status_code == 404, response.text
        assert response.json() == {
            "code": "config_changeset_not_found",
            "change_set_id": str(missing_id),
        }
