import base64
import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from ai_canvas.config import Settings
from ai_canvas.contracts.artifact import VideoArtifactMetadata
from ai_canvas.domain.media.probe import MediaProbeResult
from ai_canvas.infrastructure.artifacts import ArtifactStore
from ai_canvas.infrastructure.media.files import SharedFileLease
from ai_canvas.main import create_app
from ai_canvas.application.media_stream_service import MediaStreamResource


def _tiny_png() -> bytes:
    fixture = Path(__file__).parents[1] / "fixtures" / "tiny_png.b64"
    return base64.b64decode(fixture.read_text(encoding="ascii"))


def _authorized_headers(app) -> dict[str, str]:
    token = app.state.session_manager.exchange(app.state.session_manager.bootstrap_token)
    return {"Authorization": f"Bearer {token}", "Origin": "http://testserver"}


def _video_probe(_path: Path, _kind: str, _root: Path) -> MediaProbeResult:
    return MediaProbeResult(
        kind="video",
        media_type="video/mp4",
        metadata=VideoArtifactMetadata(
            width=16,
            height=16,
            duration_seconds=1.0,
            frames_per_second=24.0,
        ),
    )


def _upload_image(client: TestClient, headers: dict[str, str], project_id: str) -> dict[str, object]:
    content = _tiny_png()
    created = client.post(
        f"/api/v1/projects/{project_id}/artifact-imports",
        json={
            "client_import_id": str(uuid4()),
            "filename": "stream.png",
            "expected_byte_size": len(content),
            "requested_media_kind": "image",
            "declared_content_type": "image/png",
        },
        headers=headers,
    )
    assert created.status_code == 200
    uploaded = client.put(
        f"/api/v1/projects/{project_id}/artifact-imports/{created.json()['import_id']}/content",
        content=content,
        headers={**headers, "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 200
    return uploaded.json()


def test_media_session_cookie_authorizes_one_revision_range_stream(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project = client.post("/api/v1/projects", json={"name": "B4 media stream"}, headers=headers)
        assert project.status_code == 201
        revision = _upload_image(client, headers, project.json()["id"])

        issued = client.post(
            "/api/v1/media-sessions",
            json={"revision_id": revision["revision_id"]},
            headers=headers,
        )

        assert issued.status_code == 200
        assert "ai_canvas_media_session" in issued.headers["set-cookie"]
        assert "HttpOnly" in issued.headers["set-cookie"]
        assert "SameSite=strict" in issued.headers["set-cookie"]
        stream = client.get(
            f"/api/v1/media/revisions/{revision['revision_id']}",
            headers={"Range": "bytes=2-5"},
        )
        unchanged = client.get(
            f"/api/v1/media/revisions/{revision['revision_id']}",
            headers={"If-None-Match": stream.headers["etag"]},
        )

    assert stream.status_code == 206
    assert stream.content == _tiny_png()[2:6]
    assert stream.headers["content-range"] == f"bytes 2-5/{len(_tiny_png())}"
    assert stream.headers["accept-ranges"] == "bytes"
    assert stream.headers["content-disposition"] == "inline"
    assert stream.headers["cache-control"] == "private, no-store"
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert "authorization" not in str(stream.request.url).lower()


def test_media_stream_rejects_missing_or_wrong_cookie_and_bad_range(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project = client.post("/api/v1/projects", json={"name": "B4 stream auth"}, headers=headers)
        assert project.status_code == 201
        revision = _upload_image(client, headers, project.json()["id"])
        other_revision = _upload_image(client, headers, project.json()["id"])
        stream_path = f"/api/v1/media/revisions/{revision['revision_id']}"

        missing = client.get(stream_path)
        issued = client.post(
            "/api/v1/media-sessions",
            json={"revision_id": revision["revision_id"]},
            headers=headers,
        )
        assert issued.status_code == 200
        wrong_resource = client.get(f"/api/v1/media/revisions/{other_revision['revision_id']}")
        unsatisfied = client.get(stream_path, headers={"Range": "bytes=999-1000"})

    assert missing.status_code == 401
    assert wrong_resource.status_code == 401
    assert unsatisfied.status_code == 416
    assert unsatisfied.headers["content-range"] == f"bytes */{len(_tiny_png())}"


def test_one_media_cookie_keeps_multiple_revision_grants_streamable(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project = client.post("/api/v1/projects", json={"name": "B4 additive grants"}, headers=headers)
        assert project.status_code == 201
        revision_a = _upload_image(client, headers, project.json()["id"])
        revision_b = _upload_image(client, headers, project.json()["id"])

        issued_a = client.post(
            "/api/v1/media-sessions",
            json={"revision_id": revision_a["revision_id"]},
            headers=headers,
        )
        range_a_before = client.get(
            f"/api/v1/media/revisions/{revision_a['revision_id']}",
            headers={"Range": "bytes=0-0"},
        )
        issued_b = client.post(
            "/api/v1/media-sessions",
            json={"revision_id": revision_b["revision_id"]},
            headers=headers,
        )
        range_b = client.get(
            f"/api/v1/media/revisions/{revision_b['revision_id']}",
            headers={"Range": "bytes=0-0"},
        )
        range_a_after = client.get(
            f"/api/v1/media/revisions/{revision_a['revision_id']}",
            headers={"Range": "bytes=1-1"},
        )

    assert issued_a.status_code == 200
    assert range_a_before.status_code == 206
    assert issued_b.status_code == 200
    assert range_b.status_code == 206
    assert range_a_after.status_code == 206


def test_concurrent_first_issuance_converges_on_one_session_without_lost_grants(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project = client.post("/api/v1/projects", json={"name": "B4 concurrent grants"}, headers=headers)
        assert project.status_code == 201
        revision_a = UUID(str(_upload_image(client, headers, project.json()["id"])["revision_id"]))
        revision_b = UUID(str(_upload_image(client, headers, project.json()["id"])["revision_id"]))

        with ThreadPoolExecutor(max_workers=2) as executor:
            issued = list(executor.map(app.state.media_stream_service.issue, (revision_a, revision_b)))

        assert len({item.token for item in issued}) == 1
        token = issued[0].token
        assert app.state.media_stream_service.stream_resource(
            revision_id=revision_a,
            token=token,
        ).byte_size == len(_tiny_png())
        assert app.state.media_stream_service.stream_resource(
            revision_id=revision_b,
            token=token,
        ).byte_size == len(_tiny_png())


def test_managed_one_byte_range_does_not_run_full_blob_verifier(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project = client.post("/api/v1/projects", json={"name": "B4 bounded managed range"}, headers=headers)
        assert project.status_code == 201
        revision = _upload_image(client, headers, project.json()["id"])
        issued = client.post(
            "/api/v1/media-sessions",
            json={"revision_id": revision["revision_id"]},
            headers=headers,
        )
        assert issued.status_code == 200

        verifier_calls = 0
        original_verify_blob = ArtifactStore.verify_blob

        def counted_verify_blob(store, digest: str, expected_size: int):
            nonlocal verifier_calls
            verifier_calls += 1
            return original_verify_blob(store, digest, expected_size)

        monkeypatch.setattr(ArtifactStore, "verify_blob", counted_verify_blob)
        response = client.get(
            f"/api/v1/media/revisions/{revision['revision_id']}",
            headers={"Range": "bytes=0-0"},
        )

    assert response.status_code == 206
    assert response.content == _tiny_png()[:1]
    assert verifier_calls == 0


def test_external_session_observes_once_then_one_byte_range_does_not_reobserve(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "external-root"
    root.mkdir()
    clip = root / "clip.mp4"
    clip.write_bytes(b"external immutable bytes")
    app = create_app(Settings(data_dir=tmp_path / "data", public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project = client.post("/api/v1/projects", json={"name": "B4 external range"}, headers=headers)
        assert project.status_code == 201
        service = app.state.external_reference_service
        service._probe = _video_probe
        grant = service.grant_root(root)
        created = service.create_reference(
            project_id=UUID(project.json()["id"]),
            grant_id=grant.id,
            relative_path="clip.mp4",
            requested_media_kind="video",
        )

        acquired_leases: list[SharedFileLease] = []
        original_acquire = SharedFileLease.acquire_verified

        def capture_acquire(cls, *args, **kwargs):
            lease = original_acquire(*args, **kwargs)
            acquired_leases.append(lease)
            return lease

        monkeypatch.setattr(SharedFileLease, "acquire_verified", classmethod(capture_acquire))
        issued = client.post(
            "/api/v1/media-sessions",
            json={"revision_id": str(created.revision_id)},
            headers=headers,
        )
        token = client.cookies.get("ai_canvas_media_session")
        assert token is not None
        wrong_resource = client.get(f"/api/v1/media/revisions/{uuid4()}")
        response = client.get(
            f"/api/v1/media/revisions/{created.revision_id}",
            headers={"Range": "bytes=0-0"},
        )
        retained_lease = app.state.media_stream_service._external_leases[
            (token, str(created.revision_id))
        ]
        assert retained_lease is acquired_leases[0]
        assert retained_lease._pin_count == 0

        unchanged = client.get(
            f"/api/v1/media/revisions/{created.revision_id}",
            headers={"If-None-Match": response.headers["etag"]},
        )
        assert unchanged.status_code == 304
        assert retained_lease._pin_count == 0

        unsatisfied = client.get(
            f"/api/v1/media/revisions/{created.revision_id}",
            headers={"Range": "bytes=999-1000"},
        )
        assert unsatisfied.status_code == 416
        assert retained_lease._pin_count == 0

    assert issued.status_code == 200
    assert len(acquired_leases) == 1
    assert wrong_resource.status_code == 401
    assert response.status_code == 206
    assert response.content == b"e"


def test_restart_expires_external_session_whose_shared_lease_was_lost(tmp_path: Path) -> None:
    root = tmp_path / "restart-external-root"
    root.mkdir()
    clip = root / "clip.mp4"
    clip.write_bytes(b"external restart bytes")
    settings = Settings(data_dir=tmp_path / "data", public_origin="http://testserver")
    first_app = create_app(settings)
    with TestClient(first_app, base_url="http://testserver") as client:
        headers = _authorized_headers(first_app)
        project = client.post("/api/v1/projects", json={"name": "B4 restart lease"}, headers=headers)
        service = first_app.state.external_reference_service
        service._probe = _video_probe
        grant = service.grant_root(root)
        created = service.create_reference(
            project_id=UUID(project.json()["id"]),
            grant_id=grant.id,
            relative_path="clip.mp4",
            requested_media_kind="video",
        )
        issued = client.post(
            "/api/v1/media-sessions",
            json={"revision_id": str(created.revision_id)},
            headers=headers,
        )
        assert issued.status_code == 200
        old_token = client.cookies.get("ai_canvas_media_session")
        assert old_token is not None

    restarted_app = create_app(settings)
    with TestClient(restarted_app, base_url="http://testserver") as restarted_client:
        restarted_client.cookies.set(
            "ai_canvas_media_session",
            old_token,
            path="/api/v1/media/",
        )
        response = restarted_client.get(
            f"/api/v1/media/revisions/{created.revision_id}",
            headers={"Range": "bytes=0-0"},
        )

    assert response.status_code == 401


@pytest.mark.parametrize("failure_stage", ("response_start", "first_body"))
def test_stream_response_send_failure_releases_preacquired_pin_and_retired_handle(
    tmp_path: Path,
    failure_stage: str,
    monkeypatch,
) -> None:
    source = tmp_path / "send-failure.bin"
    content = b"send failure bytes"
    source.write_bytes(content)
    metadata = source.stat()
    lease = SharedFileLease.acquire_verified(
        source,
        trusted_root=tmp_path,
        expected_size=len(content),
        expected_identity=f"{metadata.st_dev}:{metadata.st_ino}",
        expected_modified_at=datetime.fromtimestamp(metadata.st_mtime, timezone.utc),
        expected_sha256=hashlib.sha256(content).hexdigest(),
    )
    retained_handle = lease._source
    resource = MediaStreamResource(
        path=lease.path,
        byte_size=len(content),
        media_type="application/octet-stream",
        etag=hashlib.sha256(content).hexdigest(),
        trusted_root=tmp_path,
        expected_identity=lease.file_identity,
        lease=lease.pin(),
    )
    lease.retire()

    class ResourceService:
        def stream_resource(self, **_kwargs) -> MediaStreamResource:
            return resource

    request_scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "method": "GET",
        "scheme": "http",
        "path": f"/api/v1/media/revisions/{uuid4()}",
        "raw_path": b"/api/v1/media/revisions/test",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "app": SimpleNamespace(
            state=SimpleNamespace(media_stream_service=ResourceService())
        ),
    }
    from ai_canvas.api.routes import media_lifecycle

    iterators = []
    original_open_range = media_lifecycle.open_range

    def capture_open_range(*args, **kwargs):
        iterator = original_open_range(*args, **kwargs)
        iterators.append(iterator)
        return iterator

    monkeypatch.setattr(media_lifecycle, "open_range", capture_open_range)
    request = Request(request_scope)
    response = media_lifecycle.stream_media_revision(uuid4(), request)
    observed_body = False

    async def receive() -> dict[str, str]:
        return {"type": "http.disconnect"}

    async def send(message) -> None:
        nonlocal observed_body
        if failure_stage == "response_start" and message["type"] == "http.response.start":
            raise RuntimeError("injected response-start send failure")
        if message["type"] == "http.response.body" and message.get("body"):
            observed_body = True
            if failure_stage == "first_body":
                raise RuntimeError("injected first-body send failure")

    try:
        with pytest.raises(RuntimeError, match="injected"):
            asyncio.run(response(request_scope, receive, send))

        assert observed_body is (failure_stage == "first_body")
        assert lease._pin_count == 0
        assert retained_handle.closed
    finally:
        for iterator in iterators:
            iterator.close()
        resource.release()
