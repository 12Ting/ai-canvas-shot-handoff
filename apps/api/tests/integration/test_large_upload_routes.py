from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from ai_canvas.config import Settings
from ai_canvas.main import create_app


def _authorized_headers(app) -> dict[str, str]:
    token = app.state.session_manager.exchange(app.state.session_manager.bootstrap_token)
    return {"Authorization": f"Bearer {token}", "Origin": "http://testserver"}


def test_create_large_upload_route_returns_fixed_part_session(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project = client.post("/api/v1/projects", json={"name": "B4 route"}, headers=headers)
        assert project.status_code == 201

        response = client.post(
            f"/api/v1/projects/{project.json()['id']}/large-uploads",
            json={
                "client_upload_id": str(uuid4()),
                "filename": "route.mp4",
                "expected_byte_size": 64 * 1024**2 + 1,
                "requested_media_kind": "video",
                "declared_content_type": "video/mp4",
            },
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["state"] == "CREATED"
        assert response.json()["part_size"] == 8 * 1024**2
        upload = response.json()
        content = b"r" * upload["part_size"]
        part = client.put(
            f"/api/v1/large-uploads/{upload['upload_id']}/parts/0",
            content=content,
            headers={
                **headers,
                "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes 0-{len(content) - 1}/{upload['expected_byte_size']}",
                "X-Content-SHA256": sha256(content).hexdigest(),
            },
        )
        assert part.status_code == 200
        assert part.json()["received_byte_size"] == len(content)


def test_large_upload_part_route_uses_stable_client_errors(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as client:
        headers = _authorized_headers(app)
        project = client.post("/api/v1/projects", json={"name": "B4 error routes"}, headers=headers)
        assert project.status_code == 201
        created = client.post(
            f"/api/v1/projects/{project.json()['id']}/large-uploads",
            json={
                "client_upload_id": str(uuid4()),
                "filename": "errors.mp4",
                "expected_byte_size": 64 * 1024**2 + 1,
                "requested_media_kind": "video",
                "declared_content_type": "video/mp4",
            },
            headers=headers,
        )
        assert created.status_code == 200
        upload_id = created.json()["upload_id"]
        missing_headers = client.put(
            f"/api/v1/large-uploads/{upload_id}/parts/0",
            content=b"anything",
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        wrong_range = client.put(
            f"/api/v1/large-uploads/{upload_id}/parts/0",
            content=b"anything",
            headers={
                **headers,
                "Content-Type": "application/octet-stream",
                "Content-Range": "bytes 0-7/8",
                "X-Content-SHA256": sha256(b"anything").hexdigest(),
            },
        )
        missing_upload = client.post(
            f"/api/v1/large-uploads/{uuid4()}/complete",
            json={},
            headers=headers,
        )

    assert missing_headers.status_code == 422
    assert missing_headers.json() == {"code": "upload_part_headers_required"}
    assert wrong_range.status_code == 422
    assert wrong_range.json()["code"] == "large_upload_protocol_error"
    assert missing_upload.status_code == 404
    assert missing_upload.json()["code"] == "large_upload_not_found"


def test_large_upload_cancel_route_aborts_session_and_releases_its_reservation(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project = client.post("/api/v1/projects", json={"name": "B4 cancel route"}, headers=headers)
        assert project.status_code == 201
        created = client.post(
            f"/api/v1/projects/{project.json()['id']}/large-uploads",
            json={
                "client_upload_id": str(uuid4()),
                "filename": "cancel.mp4",
                "expected_byte_size": 64 * 1024**2 + 1,
                "requested_media_kind": "video",
                "declared_content_type": "video/mp4",
            },
            headers=headers,
        )
        assert created.status_code == 200

        cancelled = client.delete(
            f"/api/v1/large-uploads/{created.json()['upload_id']}",
            headers={**headers, "Content-Type": "application/json"},
        )
        status = client.get(
            f"/api/v1/large-uploads/{created.json()['upload_id']}",
            headers=headers,
        )
        summary = client.get("/api/v1/storage/summary", headers=headers)

    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "ABORTED"
    assert cancelled.json()["artifact_revision"] is None
    assert status.status_code == 200
    assert status.json()["state"] == "ABORTED"
    assert summary.status_code == 200
    assert summary.json()["active_reservation_bytes"] == 0


def test_large_upload_part_rejects_declared_oversize_before_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    part_size = 4
    app = create_app(
        Settings(
            data_dir=tmp_path,
            public_origin="http://testserver",
            media_large_upload_part_bytes=part_size,
        )
    )
    received: list[bytes] = []
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)

        def put_part(**kwargs):
            received.append(kwargs["content"])
            return SimpleNamespace(upload_id=kwargs["upload_id"], part_number=0, received_byte_size=0)

        monkeypatch.setattr(app.state.large_upload_service, "put_part", put_part)
        response = client.put(
            f"/api/v1/large-uploads/{uuid4()}/parts/0",
            content=b"12345",
            headers={
                **headers,
                "Content-Type": "application/octet-stream",
                "Content-Length": "5",
                "Content-Range": "bytes 0-4/5",
                "X-Content-SHA256": sha256(b"12345").hexdigest(),
            },
        )

    assert response.status_code == 413
    assert response.json() == {"code": "request_body_too_large", "max_bytes": part_size}
    assert received == []


def test_large_upload_part_rejects_lying_streamed_oversize_before_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    part_size = 4
    app = create_app(
        Settings(
            data_dir=tmp_path,
            public_origin="http://testserver",
            media_large_upload_part_bytes=part_size,
        )
    )
    received: list[bytes] = []
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)

        def put_part(**kwargs):
            received.append(kwargs["content"])
            return SimpleNamespace(upload_id=kwargs["upload_id"], part_number=0, received_byte_size=0)

        monkeypatch.setattr(app.state.large_upload_service, "put_part", put_part)
        response = client.put(
            f"/api/v1/large-uploads/{uuid4()}/parts/0",
            content=(chunk for chunk in (b"12", b"34", b"5")),
            headers={
                **headers,
                "Content-Type": "application/octet-stream",
                "Content-Length": "1",
                "Content-Range": "bytes 0-4/5",
                "X-Content-SHA256": sha256(b"12345").hexdigest(),
            },
        )

    assert response.status_code == 413
    assert response.json() == {"code": "request_body_too_large", "max_bytes": part_size}
    assert received == []
