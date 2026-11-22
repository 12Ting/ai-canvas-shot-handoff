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


def test_graph_change_set_request_is_available_only_under_its_own_route(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Graph change-set route"},
            headers=headers,
        ).json()
        active = client.get(
            f"/api/v1/projects/{project['id']}/graph",
            headers=headers,
        ).json()

        response = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets",
            json={
                "kind": "graph_change_set",
                "schema_version": 1,
                "base_graph_revision_id": active["revision_id"],
                "client_mutation_id": str(uuid4()),
                "operations": [{"op": "replace_snapshot", "snapshot": active["snapshot"]}],
            },
            headers=headers,
        )

    assert response.status_code == 201, response.text
    assert response.json()["state"] == "PENDING_APPROVAL"
    assert response.json()["base_graph_revision_id"] == active["revision_id"]
    assert response.json()["diff"]["op"] == "replace_snapshot"
    assert response.json()["risk_summary"] == {
        "classes": ["GRAPH_WRITE", "DIRECT_BRANCH_ADVANCE"]
    }


def test_graph_change_set_route_requires_approval_before_application(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Graph approval route"},
            headers=headers,
        ).json()
        active = client.get(
            f"/api/v1/projects/{project['id']}/graph",
            headers=headers,
        ).json()
        proposal = client.post(
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

        approved = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets/{proposal['id']}/approval-decisions",
            json={"expected_changeset_sha256": proposal["changeset_sha256"]},
            headers=headers,
        )
        applied = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets/{proposal['id']}/apply",
            json={"expected_changeset_sha256": proposal["changeset_sha256"]},
            headers=headers,
        )

    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "APPROVED"
    assert applied.status_code == 200, applied.text
    assert applied.json()["state"] == "APPLIED"
    assert applied.json()["applied_graph_revision_id"] != active["revision_id"]


def test_graph_change_set_rejection_records_a_terminal_event(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post("/api/v1/projects", json={"name": "Graph rejection"}, headers=headers).json()
        active = client.get(f"/api/v1/projects/{project['id']}/graph", headers=headers).json()
        proposal = client.post(
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

        rejected = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets/{proposal['id']}/approval-decisions",
            json={
                "decision": "REJECTED",
                "expected_changeset_sha256": proposal["changeset_sha256"],
            },
            headers=headers,
        )
        events = client.get(
            f"/api/v1/projects/{project['id']}/experience-events",
            params={"correlation_id": proposal["correlation_id"]},
            headers=headers,
        ).json()["events"]

    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["state"] == "REJECTED"
    assert [event["event_type"] for event in events] == [
        "graph_change_set.requested",
        "approval.recorded",
        "graph_change_set.rejected",
    ]


def test_stale_graph_apply_records_rejected_terminal_with_stable_code(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects", json={"name": "Graph stale terminal"}, headers=headers
        ).json()
        active = client.get(f"/api/v1/projects/{project['id']}/graph", headers=headers).json()

        def proposal() -> dict:
            return client.post(
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

        stale = proposal()
        winner = proposal()
        for item in (stale, winner):
            response = client.post(
                f"/api/v1/projects/{project['id']}/graph-change-sets/{item['id']}/approval-decisions",
                json={"expected_changeset_sha256": item["changeset_sha256"]},
                headers=headers,
            )
            assert response.status_code == 200, response.text
        winner_apply = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets/{winner['id']}/apply",
            json={"expected_changeset_sha256": winner["changeset_sha256"]},
            headers=headers,
        )
        assert winner_apply.status_code == 200, winner_apply.text
        workspace = client.get(
            f"/api/v1/projects/{project['id']}/branches", headers=headers
        ).json()

        stale_apply = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets/{stale['id']}/apply",
            json={"expected_changeset_sha256": stale["changeset_sha256"]},
            headers=headers,
        )
        events = client.get(
            f"/api/v1/projects/{project['id']}/experience-events",
            params={"correlation_id": stale["correlation_id"]},
            headers=headers,
        ).json()["events"]

    assert stale_apply.status_code == 409, stale_apply.text
    assert stale_apply.json() == {
        "code": "selection_pointer_stale",
        "current_branch_id": workspace["selection"]["branch_id"],
        "current_revision_id": winner_apply.json()["applied_graph_revision_id"],
        "current_version": workspace["selection"]["version"],
    }
    assert events[-1]["event_type"] == "graph_change_set.rejected"
    assert events[-1]["payload"]["reason"] == "SELECTION_POINTER_STALE"


def test_graph_proposal_stale_and_changeset_missing_have_domain_codes(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects", json={"name": "Graph stable errors"}, headers=headers
        ).json()
        graph = client.get(f"/api/v1/projects/{project['id']}/graph", headers=headers).json()
        payload = {
            "kind": "graph_change_set",
            "schema_version": 1,
            "base_graph_revision_id": str(uuid4()),
            "client_mutation_id": str(uuid4()),
            "operations": [{"op": "replace_snapshot", "snapshot": graph["snapshot"]}],
        }
        stale = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets",
            json=payload,
            headers=headers,
        )
        missing_id = uuid4()
        missing_approval = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets/{missing_id}/approval-decisions",
            json={"expected_changeset_sha256": "a" * 64},
            headers=headers,
        )
        missing_apply = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets/{missing_id}/apply",
            json={"expected_changeset_sha256": "a" * 64},
            headers=headers,
        )

    assert stale.status_code == 409, stale.text
    assert stale.json() == {
        "code": "graph_base_revision_stale",
        "current_revision_id": graph["revision_id"],
    }
    for response in (missing_approval, missing_apply):
        assert response.status_code == 404, response.text
        assert response.json() == {
            "code": "graph_changeset_not_found",
            "change_set_id": str(missing_id),
        }


def test_graph_changeset_openapi_declares_the_stable_stale_contract(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))

    contract = app.openapi()
    graph_change_set_path = "/api/v1/projects/{project_id}/graph-change-sets"
    expected_models = {
        graph_change_set_path: {
            "GraphChangeSetBaseRevisionStaleProblem",
            "BranchHeadStaleProblem",
            "SelectionPointerStaleProblem",
            "GraphBranchNameConflictProblem",
        },
        f"{graph_change_set_path}/{{change_set_id}}/apply": {
            "GraphChangeSetBaseRevisionStaleProblem",
            "BranchHeadStaleProblem",
            "SelectionPointerStaleProblem",
        },
    }
    for path, expected in expected_models.items():
        response_schema = contract["paths"][path]["post"]["responses"]["409"][
            "content"
        ]["application/json"]["schema"]
        refs = {
            item["$ref"].rsplit("/", maxsplit=1)[-1]
            for item in response_schema["anyOf"]
        }
        assert refs == expected
    problem = contract["components"]["schemas"][
        "GraphChangeSetBaseRevisionStaleProblem"
    ]
    assert problem["properties"]["code"]["const"] == "graph_base_revision_stale"
    assert problem["properties"]["current_revision_id"]["format"] == "uuid"
    assert set(problem["required"]) == {"current_revision_id"}


def test_graph_apply_requires_approval_with_stable_code(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects", json={"name": "Graph approval required"}, headers=headers
        ).json()
        graph = client.get(f"/api/v1/projects/{project['id']}/graph", headers=headers).json()
        proposal = client.post(
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
        response = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets/{proposal['id']}/apply",
            json={"expected_changeset_sha256": proposal["changeset_sha256"]},
            headers=headers,
        )

    assert response.status_code == 409, response.text
    assert response.json() == {"code": "approval_required"}
