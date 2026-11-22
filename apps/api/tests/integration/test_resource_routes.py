import json
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


def _style_body(summary: str = "低饱和") -> dict:
    return {
        "name": "电影风格",
        "payload": {"kind": "STYLE", "summary": summary},
        "tags": ["Cinematic"],
        "artifacts": [],
    }


def test_resource_routes_require_session_host_origin_and_json(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        assert client.get("/api/v1/resources").status_code == 401
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Resource security"},
            headers=headers,
        ).json()
        path = f"/api/v1/projects/{project['id']}/resources"

        invalid_origin = client.post(
            path,
            json=_style_body(),
            headers={**headers, "Origin": "http://evil.invalid"},
        )
        wrong_content_type = client.post(
            path,
            content=json.dumps(_style_body()),
            headers={**headers, "Content-Type": "text/plain"},
        )
        invalid_host = client.get(
            "/api/v1/resources",
            headers={**headers, "Host": "invalid.example"},
        )

    assert invalid_origin.status_code == 403
    assert invalid_origin.json() == {"code": "origin_denied"}
    assert wrong_content_type.status_code == 415
    assert wrong_content_type.json() == {"code": "json_required"}

    assert invalid_host.status_code == 400


def test_resource_crud_search_stale_problem_and_archive_routes(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Resource routes"},
            headers=headers,
        ).json()
        create_path = f"/api/v1/projects/{project['id']}/resources"
        created = client.post(create_path, json=_style_body(), headers=headers)

        assert created.status_code == 201, created.text
        resource = created.json()
        resource_id = resource["id"]
        revision_one = resource["latest_revision"]["id"]
        listing = client.get(
            "/api/v1/resources",
            params={"kind": "STYLE", "query": "低饱和", "tags": "cinematic"},
            headers=headers,
        )
        detail = client.get(f"/api/v1/resources/{resource_id}", headers=headers)
        revision_path = (
            f"/api/v1/projects/{project['id']}/resources/{resource_id}/revisions"
        )
        revision_body = {
            "base_revision_id": revision_one,
            "payload": {"kind": "STYLE", "summary": "高反差"},
            "tags": [],
            "artifacts": [],
        }
        revised = client.post(revision_path, json=revision_body, headers=headers)
        stale = client.post(revision_path, json=revision_body, headers=headers)
        updates = client.get(
            f"/api/v1/projects/{project['id']}/resource-updates",
            headers=headers,
        )
        archived = client.post(
            f"/api/v1/projects/{project['id']}/resources/{resource_id}/archive",
            json={},
            headers=headers,
        )
        hidden = client.get("/api/v1/resources", headers=headers)
        visible_archived = client.get(
            "/api/v1/resources",
            params={"include_archived": "true"},
            headers=headers,
        )
        missing = client.get(f"/api/v1/resources/{uuid4()}", headers=headers)

    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [resource_id]
    assert detail.status_code == 200
    assert detail.json()["revision_history"][0]["id"] == revision_one
    assert revised.status_code == 201
    assert revised.json()["sequence"] == 2
    assert stale.status_code == 409
    assert stale.json() == {
        "code": "resource_revision_conflict",
        "resource_id": resource_id,
        "current_revision_id": revised.json()["id"],
    }
    assert updates.status_code == 200
    assert updates.json() == []
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert hidden.json() == []
    assert [item["id"] for item in visible_archived.json()] == [resource_id]
    assert missing.status_code == 404
    assert missing.json() == {
        "code": "resource_not_found",
        "resource_id": missing.url.path.rsplit("/", 1)[1],
    }


def test_resource_metadata_body_limit_checks_actual_stream_bytes(tmp_path: Path) -> None:
    limit = 256
    app = create_app(
        Settings(
            data_dir=tmp_path,
            public_origin="http://testserver",
            resource_metadata_body_limit_bytes=limit,
        )
    )
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Resource limit"},
            headers=headers,
        ).json()
        body = json.dumps(_style_body("x" * 1_024)).encode()

        def chunks():
            yield body[:64]
            yield body[64:]

        response = client.post(
            f"/api/v1/projects/{project['id']}/resources",
            content=chunks(),
            headers={
                **headers,
                "Content-Type": "application/json",
                "Content-Length": "1",
            },
        )

    assert len(body) > limit
    assert response.status_code == 413
    assert response.json() == {
        "code": "request_body_too_large",
        "max_bytes": limit,
    }


def test_resource_openapi_has_only_domain_specific_change_paths() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]

    assert "/api/v1/resources" in paths
    assert "/api/v1/resources/{resource_id}" in paths
    assert "/api/v1/projects/{project_id}/resources" in paths
    assert "/api/v1/projects/{project_id}/resource-updates" in paths
    assert "/api/v1/changesets" not in paths
    assert not any(
        "config" in operation_id.lower()
        for path, methods in paths.items()
        if "resources" in path or "resource-updates" in path
        for operation in methods.values()
        if isinstance(operation, dict)
        for operation_id in [operation.get("operationId", "")]
    )
