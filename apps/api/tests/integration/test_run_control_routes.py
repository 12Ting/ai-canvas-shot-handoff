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


def test_no_egress_run_request_is_available_only_under_run_routes(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Run control route"},
            headers=headers,
        ).json()

        response = client.post(
            f"/api/v1/projects/{project['id']}/run-requests",
            json={"operation_kind": "DRY_RUN", "task_count": 1},
            headers=headers,
        )

    assert response.status_code == 201, response.text
    assert response.json()["state"] == "PENDING_APPROVAL"
    assert len(response.json()["egress_plan_sha256"]) == 64
    assert response.json()["egress_plan"] == {
        "mode": "NONE",
        "endpoint": None,
        "external_data_classes": [],
        "task_count": 1,
        "cost_bound": 0,
        "gpu_count": 0,
        "request_sha256": response.json()["request_sha256"],
        "plan_sha256": response.json()["egress_plan_sha256"],
    }


def test_no_egress_run_route_approves_and_consumes_one_authorization(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Run approval route"},
            headers=headers,
        ).json()
        run_request = client.post(
            f"/api/v1/projects/{project['id']}/run-requests",
            json={"operation_kind": "DRY_RUN", "task_count": 1},
            headers=headers,
        ).json()

        approved = client.post(
            f"/api/v1/projects/{project['id']}/run-requests/{run_request['id']}/approval-decisions",
            json={"expected_request_sha256": run_request["request_sha256"]},
            headers=headers,
        )
        receipt = client.post(
            f"/api/v1/projects/{project['id']}/run-requests/{run_request['id']}/dry-run",
            json={},
            headers=headers,
        )
        repeated = client.post(
            f"/api/v1/projects/{project['id']}/run-requests/{run_request['id']}/dry-run",
            json={},
            headers=headers,
        )

    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "APPROVED"
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["terminal_state"] == "DRY_RUN_SUCCEEDED"
    assert repeated.status_code == 409, repeated.text
    assert repeated.json() == {"code": "run_authorization_consumed"}


def test_no_egress_run_request_can_be_rejected_with_a_terminal_event(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post("/api/v1/projects", json={"name": "Run rejected"}, headers=headers).json()
        run_request = client.post(
            f"/api/v1/projects/{project['id']}/run-requests",
            json={"operation_kind": "DRY_RUN", "task_count": 1},
            headers=headers,
        ).json()

        rejected = client.post(
            f"/api/v1/projects/{project['id']}/run-requests/{run_request['id']}/approval-decisions",
            json={
                "decision": "REJECTED",
                "expected_request_sha256": run_request["request_sha256"],
            },
            headers=headers,
        )
        events = client.get(
            f"/api/v1/projects/{project['id']}/experience-events",
            params={"correlation_id": run_request["correlation_id"]},
            headers=headers,
        ).json()["events"]

    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["state"] == "REJECTED"
    assert [event["event_type"] for event in events][-2:] == [
        "approval.recorded",
        "run_request.rejected",
    ]


def test_changed_control_config_returns_an_invalidated_authorization_code(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Run invalidation code"},
            headers=headers,
        ).json()
        run_request = client.post(
            f"/api/v1/projects/{project['id']}/run-requests",
            json={"operation_kind": "DRY_RUN", "task_count": 1},
            headers=headers,
        ).json()
        client.post(
            f"/api/v1/projects/{project['id']}/run-requests/{run_request['id']}/approval-decisions",
            json={"expected_request_sha256": run_request["request_sha256"]},
            headers=headers,
        )
        config = client.get("/api/v1/control/config-revision", headers=headers).json()
        change = client.post(
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
        client.post(
            f"/api/v1/control/config-change-sets/{change['id']}/approval-decisions",
            json={"expected_changeset_sha256": change["changeset_sha256"]},
            headers=headers,
        )
        client.post(
            f"/api/v1/control/config-change-sets/{change['id']}/apply",
            json={"expected_changeset_sha256": change["changeset_sha256"]},
            headers=headers,
        )

        invalidated = client.post(
            f"/api/v1/projects/{project['id']}/run-requests/{run_request['id']}/dry-run",
            json={},
            headers=headers,
        )

    assert invalidated.status_code == 409, invalidated.text
    assert invalidated.json() == {"code": "run_authorization_invalidated"}


def test_run_route_rejects_egress_intent_before_creating_a_request(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(
        app,
        base_url="http://testserver",
        raise_server_exceptions=False,
    ) as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Egress rejection"},
            headers=headers,
        ).json()
        rejected = client.post(
            f"/api/v1/projects/{project['id']}/run-requests",
            json={
                "operation_kind": "DRY_RUN",
                "task_count": 1,
                "endpoint": "https://example.invalid",
            },
            headers=headers,
        )

    assert rejected.status_code == 422, rejected.text
    assert rejected.json() == {"code": "egress_not_allowed_in_f1"}


def test_run_route_returns_stable_code_when_task_limit_is_exceeded(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(
        app,
        base_url="http://testserver",
        raise_server_exceptions=False,
    ) as client:
        headers = _authorized_headers(app, client)
        project = client.post("/api/v1/projects", json={"name": "Run limit"}, headers=headers).json()

        rejected = client.post(
            f"/api/v1/projects/{project['id']}/run-requests",
            json={"operation_kind": "DRY_RUN", "task_count": 2},
            headers=headers,
        )

    assert rejected.status_code == 422, rejected.text
    assert rejected.json() == {"code": "dry_run_task_limit_exceeded"}


def test_run_route_returns_stable_code_for_missing_project(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(
        app,
        base_url="http://testserver",
        raise_server_exceptions=False,
    ) as client:
        headers = _authorized_headers(app, client)
        missing_project_id = uuid4()

        rejected = client.post(
            f"/api/v1/projects/{missing_project_id}/run-requests",
            json={"operation_kind": "DRY_RUN", "task_count": 1},
            headers=headers,
        )

    assert rejected.status_code == 404, rejected.text
    assert rejected.json() == {
        "code": "project_not_found",
        "project_id": str(missing_project_id),
    }


def test_dry_run_route_returns_stable_code_for_missing_request(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(
        app,
        base_url="http://testserver",
        raise_server_exceptions=False,
    ) as client:
        headers = _authorized_headers(app, client)
        project = client.post("/api/v1/projects", json={"name": "Missing run"}, headers=headers).json()
        missing_request_id = uuid4()

        rejected = client.post(
            f"/api/v1/projects/{project['id']}/run-requests/{missing_request_id}/dry-run",
            json={},
            headers=headers,
        )

    assert rejected.status_code == 404, rejected.text
    assert rejected.json() == {
        "code": "run_request_not_found",
        "request_id": str(missing_request_id),
    }


def test_run_approval_missing_and_dry_run_unapproved_have_stable_codes(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as client:
        headers = _authorized_headers(app, client)
        project = client.post("/api/v1/projects", json={"name": "Run stable errors"}, headers=headers).json()
        run_request = client.post(
            f"/api/v1/projects/{project['id']}/run-requests",
            json={"operation_kind": "DRY_RUN", "task_count": 1},
            headers=headers,
        ).json()
        unapproved = client.post(
            f"/api/v1/projects/{project['id']}/run-requests/{run_request['id']}/dry-run",
            json={},
            headers=headers,
        )
        missing_id = uuid4()
        missing = client.post(
            f"/api/v1/projects/{project['id']}/run-requests/{missing_id}/approval-decisions",
            json={"expected_request_sha256": "a" * 64},
            headers=headers,
        )

    assert unapproved.status_code == 409, unapproved.text
    assert unapproved.json() == {"code": "approval_required"}
    assert missing.status_code == 404, missing.text
    assert missing.json() == {
        "code": "run_request_not_found",
        "request_id": str(missing_id),
    }


def test_dry_run_validation_failure_returns_stable_code_and_records_terminal(
    tmp_path,
    monkeypatch,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as client:
        headers = _authorized_headers(app, client)
        project = client.post("/api/v1/projects", json={"name": "Dry-run failure"}, headers=headers).json()
        run_request = client.post(
            f"/api/v1/projects/{project['id']}/run-requests",
            json={"operation_kind": "DRY_RUN", "task_count": 1},
            headers=headers,
        ).json()
        approved = client.post(
            f"/api/v1/projects/{project['id']}/run-requests/{run_request['id']}/approval-decisions",
            json={"expected_request_sha256": run_request["request_sha256"]},
            headers=headers,
        )
        assert approved.status_code == 200, approved.text
        monkeypatch.setattr(
            app.state.run_control_service,
            "_dry_run_validation_error",
            lambda *_args: "INJECTED_VALIDATION_FAILURE",
        )

        failed = client.post(
            f"/api/v1/projects/{project['id']}/run-requests/{run_request['id']}/dry-run",
            json={},
            headers=headers,
        )
        events = client.get(
            f"/api/v1/projects/{project['id']}/experience-events",
            params={"correlation_id": run_request["correlation_id"]},
            headers=headers,
        ).json()["events"]

    assert failed.status_code == 422, failed.text
    assert failed.json() == {"code": "dry_run_validation_failed"}
    assert events[-1]["event_type"] == "run.dry_run_failed"
    assert events[-1]["payload"]["reason"] == "INJECTED_VALIDATION_FAILURE"
