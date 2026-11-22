from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ai_canvas.api.security import SessionManager
from ai_canvas.config import Settings
from ai_canvas.main import create_app


def test_session_manager_rejects_non_ascii_bootstrap_with_unauthorized() -> None:
    manager = SessionManager()

    with pytest.raises(HTTPException) as error:
        manager.exchange("é")

    assert error.value.status_code == 401
    assert manager.exchange(manager.bootstrap_token)


def test_session_manager_rejects_non_ascii_bearer_with_unauthorized() -> None:
    manager = SessionManager()
    manager.exchange(manager.bootstrap_token)

    with pytest.raises(HTTPException) as error:
        manager.authenticate("é")

    assert error.value.status_code == 401


def test_exchange_rejects_obs_text_bootstrap_header(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        malformed = client.post(
            "/api/v1/session/exchange",
            headers=[
                (b"X-Canvas-Bootstrap", b"\xff"),
                (b"Origin", b"http://testserver"),
                (b"Host", b"testserver"),
            ],
        )
        valid = client.post(
            "/api/v1/session/exchange",
            headers={
                "X-Canvas-Bootstrap": app.state.session_manager.bootstrap_token,
                "Origin": "http://testserver",
                "Host": "testserver",
            },
        )

    assert malformed.status_code == 401
    assert valid.status_code == 200


def test_api_rejects_obs_text_bearer_header(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    app.state.session_manager.exchange(app.state.session_manager.bootstrap_token)
    with TestClient(app, base_url="http://testserver") as client:
        response = client.get(
            "/api/v1/projects",
            headers=[
                (b"Authorization", b"Bearer \xff"),
                (b"Origin", b"http://testserver"),
                (b"Host", b"testserver"),
            ],
        )

    assert response.status_code == 401
    assert response.json() == {"code": "unauthorized"}


def test_bootstrap_is_one_time_and_mutations_require_bearer(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    bootstrap = app.state.session_manager.bootstrap_token
    with TestClient(app, base_url="http://testserver") as client:
        missing_header = client.post(
            "/api/v1/session/exchange",
            headers={"Origin": "http://testserver"},
        )
        assert missing_header.status_code == 422
        assert missing_header.json()["code"] == "request_validation"
        exchange = client.post(
            "/api/v1/session/exchange",
            headers={"X-Canvas-Bootstrap": bootstrap, "Origin": "http://testserver"},
        )
        assert exchange.status_code == 200
        token = exchange.json()["access_token"]
        assert (
            client.post(
                "/api/v1/session/exchange",
                headers={"X-Canvas-Bootstrap": bootstrap, "Origin": "http://testserver"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/projects",
                json={"name": "Denied"},
                headers={"Origin": "http://testserver"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/projects",
                json={"name": "Allowed"},
                headers={"Authorization": f"Bearer {token}", "Origin": "http://testserver"},
            ).status_code
            == 201
        )


def test_rejects_wrong_origin_and_non_json_mutation(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    token = app.state.session_manager.exchange(app.state.session_manager.bootstrap_token)
    with TestClient(app, base_url="http://testserver") as client:
        wrong_origin = client.post(
            "/api/v1/projects",
            json={"name": "Blocked"},
            headers={"Authorization": f"Bearer {token}", "Origin": "https://evil.example"},
        )
        wrong_type = client.post(
            "/api/v1/projects",
            content="name=Blocked",
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "http://testserver",
                "Content-Type": "text/plain",
            },
        )
        missing_origin = client.post(
            "/api/v1/projects",
            json={"name": "Blocked"},
            headers={"Authorization": f"Bearer {token}"},
        )
        wrong_host = client.get("/api/v1/health", headers={"Host": "evil.example"})
    assert wrong_origin.status_code == 403
    assert wrong_type.status_code == 415
    assert missing_origin.status_code == 403
    assert missing_origin.json() == {"code": "origin_required"}
    assert wrong_host.status_code == 400
    assert "access-control-allow-origin" not in wrong_origin.headers


def test_concurrent_bootstrap_exchange_has_one_winner(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    manager = app.state.session_manager
    bootstrap = manager.bootstrap_token
    barrier = Barrier(2)

    def exchange() -> str:
        barrier.wait()
        try:
            manager.exchange(bootstrap)
            return "accepted"
        except HTTPException:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: exchange(), range(2)))
    assert sorted(outcomes) == ["accepted", "rejected"]


def _development_auto_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        runtime_profile="development",
        auth_mode="development-auto-session",
        public_origin="http://127.0.0.1:8765",
        development_origins=("http://127.0.0.1:5173",),
        allowed_hosts=("127.0.0.1",),
    )


def _development_session_headers(
    *,
    origin: str = "http://127.0.0.1:5173",
    host: str = "127.0.0.1:5173",
) -> dict[str, str]:
    return {"Origin": origin, "Host": host}


def test_development_session_is_disabled_in_normal_session_mode(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        response = client.post(
            "/api/v1/session/development",
            json={},
            headers={"Origin": "http://testserver", "Host": "testserver"},
        )

    assert response.status_code == 403
    assert response.json() == {"code": "development_session_disabled"}
    assert response.headers["cache-control"] == "no-store"


def test_development_session_403_openapi_lists_only_exact_boundary_codes(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))

    contract = app.openapi()
    response_schema = contract["paths"]["/api/v1/session/development"]["post"]["responses"]["403"]["content"][
        "application/json"
    ]["schema"]
    assert response_schema == {"$ref": "#/components/schemas/DevelopmentSessionForbidden"}
    code_schema = contract["components"]["schemas"]["DevelopmentSessionForbidden"]["properties"]["code"]
    assert code_schema["type"] == "string"
    assert set(code_schema["enum"]) == {
        "development_session_disabled",
        "origin_required",
        "origin_denied",
        "loopback_peer_required",
        "literal_host_required",
    }


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "127.0.0.1:5173", "127.0.0.1:8765"],
)
def test_development_session_issues_token_for_literal_loopback_request(
    tmp_path: Path,
    host: str,
) -> None:
    app = create_app(_development_auto_settings(tmp_path))
    with TestClient(
        app,
        base_url="http://127.0.0.1:5173",
        client=("127.0.0.1", 50000),
    ) as client:
        response = client.post(
            "/api/v1/session/development",
            json={},
            headers=_development_session_headers(host=host),
        )

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_development_session_accepts_ipv4_mapped_loopback_peer(tmp_path: Path) -> None:
    app = create_app(_development_auto_settings(tmp_path))
    with TestClient(
        app,
        base_url="http://127.0.0.1:5173",
        client=("::ffff:127.0.0.1", 50000),
    ) as client:
        response = client.post(
            "/api/v1/session/development",
            json={},
            headers=_development_session_headers(),
        )

    assert response.status_code == 200
    assert response.json()["access_token"]


def test_development_session_matches_browser_origins_after_root_slash_normalization(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        runtime_profile="development",
        auth_mode="development-auto-session",
        public_origin="http://127.0.0.1:8765/",
        development_origins=("http://127.0.0.1:5173/",),
        allowed_hosts=("127.0.0.1",),
    )
    app = create_app(settings)
    with TestClient(
        app,
        base_url="http://127.0.0.1:5173",
        client=("127.0.0.1", 50000),
    ) as client:
        vite_response = client.post(
            "/api/v1/session/development",
            json={},
            headers=_development_session_headers(),
        )
        api_response = client.post(
            "/api/v1/session/development",
            json={},
            headers=_development_session_headers(
                origin="http://127.0.0.1:8765",
                host="127.0.0.1:8765",
            ),
        )

    assert vite_response.status_code == 200
    assert api_response.status_code == 200
    assert vite_response.json()["access_token"] == api_response.json()["access_token"]


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",
        "http://127.0.0.1:5173/",
        "http://127.0.0.1:5174",
        "https://127.0.0.1:5173",
    ],
)
def test_development_session_rejects_wrong_origin(tmp_path: Path, origin: str) -> None:
    app = create_app(_development_auto_settings(tmp_path))
    with TestClient(
        app,
        base_url="http://127.0.0.1:5173",
        client=("127.0.0.1", 50000),
    ) as client:
        response = client.post(
            "/api/v1/session/development",
            json={},
            headers=_development_session_headers(origin=origin),
        )

    assert response.status_code == 403
    assert response.json() == {"code": "origin_denied"}


def test_development_session_rejects_missing_origin(tmp_path: Path) -> None:
    app = create_app(_development_auto_settings(tmp_path))
    with TestClient(
        app,
        base_url="http://127.0.0.1:5173",
        client=("127.0.0.1", 50000),
    ) as client:
        response = client.post(
            "/api/v1/session/development",
            json={},
            headers={"Host": "127.0.0.1:5173"},
        )

    assert response.status_code == 403
    assert response.json() == {"code": "origin_required"}


@pytest.mark.parametrize(
    "host",
    [
        "localhost:5173",
        "0.0.0.0:5173",
        "127.0.0.2:5173",
        "*:5173",
        "127.0.0.1:not-a-port",
        "127.0.0.1:0",
        "127.0.0.1:65536",
    ],
)
def test_development_session_rejects_non_literal_or_invalid_host(
    tmp_path: Path,
    host: str,
) -> None:
    app = create_app(_development_auto_settings(tmp_path))
    with TestClient(
        app,
        base_url="http://127.0.0.1:5173",
        client=("127.0.0.1", 50000),
    ) as client:
        response = client.post(
            "/api/v1/session/development",
            json={},
            headers=_development_session_headers(host=host),
        )

    assert response.status_code in {400, 403}


def test_development_session_rejects_excessively_long_numeric_host_port(
    tmp_path: Path,
) -> None:
    app = create_app(_development_auto_settings(tmp_path))
    with TestClient(
        app,
        base_url="http://127.0.0.1:5173",
        client=("127.0.0.1", 50000),
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            "/api/v1/session/development",
            json={},
            headers=_development_session_headers(host=f"127.0.0.1:{'9' * 5_000}"),
        )

    assert response.status_code == 403
    assert response.json() == {"code": "literal_host_required"}
    assert "access_token" not in response.json()


def test_development_session_rejects_non_json_body(tmp_path: Path) -> None:
    app = create_app(_development_auto_settings(tmp_path))
    with TestClient(
        app,
        base_url="http://127.0.0.1:5173",
        client=("127.0.0.1", 50000),
    ) as client:
        response = client.post(
            "/api/v1/session/development",
            content="{}",
            headers={
                **_development_session_headers(),
                "Content-Type": "text/plain",
            },
        )

    assert response.status_code == 415
    assert response.json() == {"code": "json_required"}


def test_development_session_rejects_non_empty_request_object(tmp_path: Path) -> None:
    app = create_app(_development_auto_settings(tmp_path))
    with TestClient(
        app,
        base_url="http://127.0.0.1:5173",
        client=("127.0.0.1", 50000),
    ) as client:
        response = client.post(
            "/api/v1/session/development",
            json={"bootstrap": "unexpected"},
            headers=_development_session_headers(),
        )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "peer",
    ["::1", "127.0.0.2", "192.0.2.10"],
)
def test_development_session_rejects_non_literal_loopback_peer(
    tmp_path: Path,
    peer: str,
) -> None:
    app = create_app(_development_auto_settings(tmp_path))
    with TestClient(
        app,
        base_url="http://127.0.0.1:5173",
        client=(peer, 50000),
    ) as client:
        response = client.post(
            "/api/v1/session/development",
            json={},
            headers=_development_session_headers(),
        )

    assert response.status_code == 403
    assert response.json() == {"code": "loopback_peer_required"}


def test_session_status_requires_bearer_and_attests_bootstrap_session(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    bootstrap = app.state.session_manager.bootstrap_token
    with TestClient(app, base_url="http://testserver") as client:
        missing = client.get("/api/v1/session/status")
        invalid = client.get(
            "/api/v1/session/status",
            headers={"Authorization": "Bearer invalid"},
        )
        exchange = client.post(
            "/api/v1/session/exchange",
            headers={
                "X-Canvas-Bootstrap": bootstrap,
                "Origin": "http://testserver",
            },
        )
        status_response = client.get(
            "/api/v1/session/status",
            headers={"Authorization": f"Bearer {exchange.json()['access_token']}"},
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert status_response.status_code == 200
    assert status_response.json() == {"mode": "session"}
    assert status_response.headers["cache-control"] == "no-store"


def test_development_token_attests_mode_and_protects_data_routes(tmp_path: Path) -> None:
    app = create_app(_development_auto_settings(tmp_path))
    with TestClient(
        app,
        base_url="http://127.0.0.1:5173",
        client=("127.0.0.1", 50000),
    ) as client:
        issue = client.post(
            "/api/v1/session/development",
            json={},
            headers=_development_session_headers(),
        )
        token = issue.json()["access_token"]
        missing = client.get("/api/v1/projects")
        authorized = client.get(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
        )
        status_response = client.get(
            "/api/v1/session/status",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert missing.status_code == 401
    assert missing.json() == {"code": "unauthorized"}
    assert authorized.status_code == 200
    assert authorized.json() == []
    assert status_response.status_code == 200
    assert status_response.json() == {"mode": "development-auto-session"}


def test_development_session_issuance_is_stable_when_serial() -> None:
    manager = SessionManager()

    first = manager.issue_development_session()
    second = manager.issue_development_session()

    assert first == second
    manager.authenticate(first)
    manager.authenticate(second)


def test_bootstrap_exchange_does_not_rotate_an_issued_development_token() -> None:
    manager = SessionManager()
    issued = manager.issue_development_session()

    exchanged = manager.exchange(manager.bootstrap_token)

    assert exchanged == issued
    manager.authenticate(issued)
    with pytest.raises(HTTPException):
        manager.exchange(manager.bootstrap_token)


def test_development_session_issuance_is_atomic_when_concurrent() -> None:
    manager = SessionManager()
    barrier = Barrier(8)

    def issue() -> str:
        barrier.wait()
        return manager.issue_development_session()

    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(lambda _index: issue(), range(8)))

    assert len(set(tokens)) == 1
    for token in tokens:
        manager.authenticate(token)


def _create_authenticated_project(
    client: TestClient,
    app,
) -> tuple[dict[str, str], dict[str, str]]:
    token = app.state.session_manager.exchange(app.state.session_manager.bootstrap_token)
    headers = {
        "Authorization": f"Bearer {token}",
        "Origin": "http://testserver",
    }
    response = client.post(
        "/api/v1/projects",
        json={"name": "Body limit"},
        headers=headers,
    )
    assert response.status_code == 201
    return headers, response.json()


def _graph_changeset_payload(base_revision_id: str, *, text: str = "") -> dict[str, object]:
    nodes = []
    if text:
        nodes.append(
            {
                "id": str(uuid4()),
                "kind": "note",
                "position": {"x": 0, "y": 0},
                "data": {"text": text},
            }
        )
    return {
        "kind": "graph_change_set",
        "schema_version": 1,
        "base_graph_revision_id": base_revision_id,
        "client_mutation_id": str(uuid4()),
        "operations": [
            {
                "op": "replace_snapshot",
                "snapshot": {
                    "schema_version": 2,
                    "nodes": nodes,
                    "edges": [],
                    "shots": [],
                },
            }
        ],
    }


def test_graph_changeset_body_limit_scope_is_exact() -> None:
    from ai_canvas.api.security import is_graph_changeset_body_limited

    project_id = uuid4()
    path = f"/api/v1/projects/{project_id}/graph-change-sets"

    assert is_graph_changeset_body_limited("POST", path)
    assert not is_graph_changeset_body_limited("PUT", path)
    assert not is_graph_changeset_body_limited("POST", f"{path}/content")
    assert not is_graph_changeset_body_limited("POST", "/api/v1/projects/not-a-uuid/graph-change-sets")


def test_f1_control_body_limit_scope_includes_config_and_run_mutations() -> None:
    from ai_canvas.api.security import is_f1_control_body_limited

    project_id = uuid4()
    request_id = uuid4()
    assert is_f1_control_body_limited("POST", "/api/v1/control/config-change-sets")
    assert is_f1_control_body_limited(
        "POST", f"/api/v1/control/config-change-sets/{request_id}/approval-decisions"
    )
    assert is_f1_control_body_limited("POST", f"/api/v1/projects/{project_id}/run-requests")
    assert is_f1_control_body_limited(
        "POST", f"/api/v1/projects/{project_id}/run-requests/{request_id}/dry-run"
    )
    assert is_f1_control_body_limited(
        "POST",
        f"/api/v1/control/config-change-sets/{request_id.hex}/approval-decisions",
    )
    assert is_f1_control_body_limited("POST", f"/api/v1/projects/{project_id.hex}/run-requests")
    assert is_f1_control_body_limited(
        "POST",
        f"/api/v1/projects/{project_id.hex}/run-requests/{request_id.hex}/dry-run",
    )
    assert not is_f1_control_body_limited("GET", "/api/v1/control/config-revision")
    assert not is_f1_control_body_limited("POST", "/api/v1/projects/not-a-uuid/run-requests")


def test_f1_control_body_limit_scope_includes_every_c4_mutation() -> None:
    from ai_canvas.api.security import is_f1_control_body_limited

    project_id = uuid4()
    batch_id = uuid4()
    item_id = uuid4()
    attempt_id = uuid4()
    extraction_id = uuid4()
    paths = [
        f"/api/v1/projects/{project_id}/prompt-profile-extractions",
        f"/api/v1/projects/{project_id}/prompt-profile-extractions/{extraction_id}/approval-decisions",
        f"/api/v1/projects/{project_id}/prompt-profile-extractions/{extraction_id}/execute",
        f"/api/v1/projects/{project_id}/prompt-profile-extractions/{extraction_id}/materialize",
        f"/api/v1/projects/{project_id}/prompt-batches/preflight",
        f"/api/v1/projects/{project_id}/prompt-batches",
        f"/api/v1/projects/{project_id}/prompt-batches/{batch_id}/approval",
        f"/api/v1/projects/{project_id}/prompt-batches/{batch_id}/items/{item_id}/attempts/{attempt_id}/execute",
        f"/api/v1/projects/{project_id}/prompt-batches/{batch_id}/items/{item_id}/decisions",
        f"/api/v1/projects/{project_id}/prompt-batches/{batch_id}/decisions:accept-all-succeeded",
        f"/api/v1/projects/{project_id}/prompt-batches/{batch_id}/retries",
        f"/api/v1/projects/{project_id}/prompt-batches/{batch_id}/retry-approval",
        f"/api/v1/projects/{project_id}/prompt-batches/{batch_id}/finalize",
    ]
    assert all(is_f1_control_body_limited("POST", path) for path in paths)
    assert not is_f1_control_body_limited("GET", paths[0])
    assert not is_f1_control_body_limited(
        "POST", f"/api/v1/projects/not-a-uuid/prompt-batches/{batch_id}/finalize"
    )
    assert not is_f1_control_body_limited(
        "POST", f"/api/v1/projects/{project_id}/prompt-batches/{batch_id}/unknown"
    )


def test_resource_metadata_body_limit_scope_is_exact() -> None:
    from ai_canvas.api.security import is_resource_metadata_body_limited

    project_id = uuid4()
    resource_id = uuid4()
    assert is_resource_metadata_body_limited(
        "POST", f"/api/v1/projects/{project_id}/resources"
    )
    assert is_resource_metadata_body_limited(
        "POST",
        f"/api/v1/projects/{project_id}/resources/{resource_id}/revisions",
    )
    assert is_resource_metadata_body_limited(
        "POST",
        f"/api/v1/projects/{project_id}/resources/{resource_id}/archive",
    )
    assert not is_resource_metadata_body_limited(
        "GET", f"/api/v1/projects/{project_id}/resources"
    )
    assert not is_resource_metadata_body_limited(
        "POST", f"/api/v1/projects/{project_id.hex}/resources"
    )
    assert not is_resource_metadata_body_limited(
        "POST", f"/api/v1/projects/{project_id}/resources/{resource_id}/delete"
    )


@pytest.mark.parametrize("target", ["config", "run"])
def test_config_and_run_json_bodies_are_bounded_before_validation(
    tmp_path: Path,
    target: str,
) -> None:
    limit = 128
    app = create_app(
        Settings(
            data_dir=tmp_path,
            public_origin="http://testserver",
            graph_revision_body_limit_bytes=limit,
        )
    )
    with TestClient(app, base_url="http://testserver") as client:
        headers, project = _create_authenticated_project(client, app)
        if target == "config":
            config = client.get("/api/v1/control/config-revision", headers=headers).json()
            path = "/api/v1/control/config-change-sets"
            payload = {
                "kind": "config_change_set",
                "schema_version": 1,
                "audit_project_id": project["id"],
                "base_config_revision_id": config["id"],
                "operations": [{"op": "replace", "path": "/dry_run_max_tasks", "value": 2}],
            }
        else:
            path = f"/api/v1/projects/{project['id']}/run-requests"
            payload = {
                "operation_kind": "DRY_RUN",
                "task_count": 1,
                "padding": "x" * 256,
            }
        body = json.dumps(payload).encode()
        assert len(body) > limit
        response = client.post(
            path,
            content=body,
            headers={**headers, "Content-Type": "application/json"},
        )

    assert response.status_code == 413, response.text
    assert response.json() == {
        "code": "request_body_too_large",
        "max_bytes": limit,
    }


def test_run_json_body_limit_cannot_be_bypassed_with_uuid_hex_path(tmp_path: Path) -> None:
    limit = 128
    app = create_app(
        Settings(
            data_dir=tmp_path,
            public_origin="http://testserver",
            graph_revision_body_limit_bytes=limit,
        )
    )
    with TestClient(app, base_url="http://testserver") as client:
        headers, project = _create_authenticated_project(client, app)
        body = json.dumps(
            {
                "operation_kind": "DRY_RUN",
                "task_count": 1,
                "padding": "x" * 256,
            }
        ).encode()
        response = client.post(
            f"/api/v1/projects/{project['id'].replace('-', '')}/run-requests",
            content=body,
            headers={**headers, "Content-Type": "application/json"},
        )

    assert len(body) > limit
    assert response.status_code == 413, response.text
    assert response.json() == {
        "code": "request_body_too_large",
        "max_bytes": limit,
    }


def test_graph_changeset_oversized_content_length_rejects_before_handler(tmp_path: Path) -> None:
    limit = 512
    app = create_app(
        Settings(
            data_dir=tmp_path,
            public_origin="http://testserver",
            graph_revision_body_limit_bytes=limit,
        )
    )
    with TestClient(app, base_url="http://testserver") as client:
        headers, project = _create_authenticated_project(client, app)
        active = client.get(f"/api/v1/projects/{project['id']}/graph", headers=headers).json()
        body = json.dumps(_graph_changeset_payload(active["revision_id"])).encode()
        response = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets",
            content=body,
            headers={
                **headers,
                "Content-Type": "application/json",
                "Content-Length": str(limit + 1),
            },
        )

    assert len(body) < limit
    assert response.status_code == 413
    assert response.json() == {
        "code": "request_body_too_large",
        "max_bytes": limit,
    }


@pytest.mark.parametrize("declared_length", [None, "1"])
def test_graph_changeset_actual_body_limit_cannot_be_bypassed_by_length_header(
    tmp_path: Path,
    declared_length: str | None,
) -> None:
    limit = 512
    app = create_app(
        Settings(
            data_dir=tmp_path,
            public_origin="http://testserver",
            graph_revision_body_limit_bytes=limit,
        )
    )
    with TestClient(app, base_url="http://testserver") as client:
        headers, project = _create_authenticated_project(client, app)
        active = client.get(f"/api/v1/projects/{project['id']}/graph", headers=headers).json()
        body = json.dumps(_graph_changeset_payload(active["revision_id"], text="x" * 1_024)).encode()
        request_headers = {**headers, "Content-Type": "application/json"}
        if declared_length is not None:
            request_headers["Content-Length"] = declared_length

        def chunks():
            yield body[:128]
            yield body[128:]

        response = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets",
            content=chunks(),
            headers=request_headers,
        )

    assert len(body) > limit
    assert response.status_code == 413
    assert response.json() == {
        "code": "request_body_too_large",
        "max_bytes": limit,
    }


def test_normal_graph_changeset_body_and_openapi_contract_are_declared(tmp_path: Path) -> None:
    limit = 512
    app = create_app(
        Settings(
            data_dir=tmp_path,
            public_origin="http://testserver",
            graph_revision_body_limit_bytes=limit,
        )
    )
    with TestClient(app, base_url="http://testserver") as client:
        headers, project = _create_authenticated_project(client, app)
        active = client.get(f"/api/v1/projects/{project['id']}/graph", headers=headers).json()
        response = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets",
            json=_graph_changeset_payload(active["revision_id"]),
            headers=headers,
        )

    assert response.status_code == 201
    assert response.json()["state"] == "PENDING_APPROVAL"
    responses = app.openapi()["paths"]["/api/v1/projects/{project_id}/graph-change-sets"]["post"]["responses"]
    assert "413" in responses


def test_artifact_raw_upload_exception_scope_is_exact() -> None:
    from ai_canvas.api.security import is_artifact_content_upload

    project_id = uuid4()
    import_id = uuid4()
    path = f"/api/v1/projects/{project_id}/artifact-imports/{import_id}/content"
    assert is_artifact_content_upload("PUT", path)
    assert is_artifact_content_upload(
        "put",
        path.upper()
        .replace("/API/V1/PROJECTS", "/api/v1/projects")
        .replace("/ARTIFACT-IMPORTS", "/artifact-imports")
        .replace("/CONTENT", "/content"),
    )
    assert not is_artifact_content_upload("POST", path)
    assert not is_artifact_content_upload("PATCH", path)
    assert not is_artifact_content_upload("PUT", f"{path}/")
    assert not is_artifact_content_upload("PUT", f"{path}/extra")
    assert not is_artifact_content_upload(
        "PUT",
        f"/api/v1/projects/not-a-uuid/artifact-imports/{import_id}/content",
    )
    assert not is_artifact_content_upload(
        "PUT",
        f"/api/v1/projects/{project_id}/artifact-imports/not-a-uuid/content",
    )


def test_only_exact_artifact_put_accepts_octet_stream_and_keeps_all_local_guards(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    token = app.state.session_manager.exchange(app.state.session_manager.bootstrap_token)
    headers = {
        "Authorization": f"Bearer {token}",
        "Origin": "http://testserver",
    }
    fixture = Path(__file__).parents[1] / "fixtures" / "tiny_png.b64"
    content = __import__("base64").b64decode(fixture.read_text(encoding="ascii"))
    with TestClient(app, base_url="http://testserver") as client:
        project = client.post(
            "/api/v1/projects",
            json={"name": "Raw policy"},
            headers=headers,
        ).json()
        created = client.post(
            f"/api/v1/projects/{project['id']}/artifact-imports",
            json={
                "client_import_id": str(uuid4()),
                "filename": "camera.png",
                "expected_byte_size": len(content),
                "requested_media_kind": "image",
                "declared_content_type": "image/png",
            },
            headers=headers,
        ).json()
        path = f"/api/v1/projects/{project['id']}/artifact-imports/{created['import_id']}/content"
        wrong_type = client.put(
            path,
            content=content,
            headers={**headers, "Content-Type": "application/json"},
        )
        missing_origin = client.put(
            path,
            content=content,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
            },
        )
        wrong_origin = client.put(
            path,
            content=content,
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "https://evil.example",
                "Content-Type": "application/octet-stream",
            },
        )
        missing_bearer = client.put(
            path,
            content=content,
            headers={
                "Origin": "http://testserver",
                "Content-Type": "application/octet-stream",
            },
        )
        approximate = (
            client.post(path, content=content, headers={**headers, "Content-Type": "text/plain"}),
            client.put(
                f"{path}/",
                content=content,
                headers={**headers, "Content-Type": "application/octet-stream"},
                follow_redirects=False,
            ),
            client.put(
                f"{path}/extra",
                content=content,
                headers={**headers, "Content-Type": "application/octet-stream"},
            ),
            client.put(
                path.replace(created["import_id"], "not-a-uuid"),
                content=content,
                headers={**headers, "Content-Type": "application/octet-stream"},
            ),
        )
        accepted = client.put(
            path,
            content=content,
            headers={
                **headers,
                "Content-Type": "application/octet-stream; charset=binary",
            },
        )
        wrong_host = client.get("/api/v1/health", headers={"Host": "evil.example"})

    assert wrong_type.status_code == 415
    assert wrong_type.json() == {"code": "octet_stream_required"}
    assert missing_origin.status_code == 403
    assert missing_origin.json() == {"code": "origin_required"}
    assert wrong_origin.status_code == 403
    assert wrong_origin.json() == {"code": "origin_denied"}
    assert missing_bearer.status_code == 401
    assert missing_bearer.json() == {"code": "unauthorized"}
    for response in approximate:
        assert response.status_code == 415
        assert response.json() == {"code": "json_required"}
    assert accepted.status_code == 200
    assert wrong_host.status_code == 400


def test_locked_csp_contains_media_and_mutation_boundaries(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        response = client.get("/api/v1/health")

    directives = {
        directive.strip()
        for directive in response.headers["content-security-policy"].split(";")
        if directive.strip()
    }
    assert directives == {
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "connect-src 'self'",
        "object-src 'none'",
        "form-action 'self'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
