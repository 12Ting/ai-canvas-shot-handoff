from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event
from starlette.requests import Request

from ai_canvas.api.routes.artifacts import upload_artifact_content
from ai_canvas.application.errors import ArtifactImportFailed
from ai_canvas.config import Settings
from ai_canvas.contracts.artifact import ArtifactImportSessionResponse
from ai_canvas.infrastructure.artifacts import ArtifactStore
from ai_canvas.infrastructure.db.models import ArtifactRevisionRow, ImportSessionRow
from ai_canvas.main import create_app


def _tiny_png() -> bytes:
    fixture = Path(__file__).parents[1] / "fixtures" / "tiny_png.b64"
    return base64.b64decode(fixture.read_text(encoding="ascii"))


def _png(color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", (4, 3), color).save(output, format="PNG")
    return output.getvalue()


def _authorized_headers(app) -> dict[str, str]:
    token = app.state.session_manager.exchange(app.state.session_manager.bootstrap_token)
    return {
        "Authorization": f"Bearer {token}",
        "Origin": "http://testserver",
    }


def _create_project(client: TestClient, headers: dict[str, str], name: str = "Media") -> str:
    response = client.post("/api/v1/projects", json={"name": name}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


def _create_import(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    content: bytes | None = None,
) -> dict:
    body = _tiny_png() if content is None else content
    response = client.post(
        f"/api/v1/projects/{project_id}/artifact-imports",
        json={
            "client_import_id": str(uuid4()),
            "filename": "../camera.png",
            "expected_byte_size": len(body),
            "requested_media_kind": "image",
            "declared_content_type": "image/png",
            "replace_revision_id": None,
        },
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()


def _upload_revision(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    content: bytes | None = None,
) -> dict:
    body = _tiny_png() if content is None else content
    created = _create_import(client, headers, project_id, body)
    uploaded = client.put(
        f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}/content",
        content=body,
        headers={**headers, "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 200
    return uploaded.json()


def _assert_utc_rfc3339(value: str) -> None:
    assert value.endswith(("Z", "+00:00"))
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.utcoffset() == timedelta(0)


def test_artifact_response_contract_normalizes_timestamps_to_utc() -> None:
    response = ArtifactImportSessionResponse(
        import_id=uuid4(),
        source_project_id=uuid4(),
        client_import_id=uuid4(),
        requested_media_kind="image",
        original_filename="camera.png",
        expected_byte_size=1,
        state="CREATED",
        received_byte_size=0,
        created_at=datetime(2026, 7, 21, 8, 30),
        updated_at=datetime(
            2026,
            7,
            21,
            8,
            30,
            tzinfo=timezone(timedelta(hours=8)),
        ),
    )

    assert response.created_at == datetime(2026, 7, 21, 8, 30, tzinfo=timezone.utc)
    assert response.updated_at == datetime(2026, 7, 21, 0, 30, tzinfo=timezone.utc)


def _disconnecting_upload_request(
    app,
    *,
    initial_body: bytes,
    declared_length: int,
) -> Request:
    messages = iter(
        (
            {"type": "http.request", "body": initial_body, "more_body": True},
            {"type": "http.disconnect"},
        )
    )

    async def receive() -> dict:
        try:
            return next(messages)
        except StopIteration:
            return {"type": "http.disconnect"}

    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "PUT",
            "scheme": "http",
            "path": "/artifact-content",
            "raw_path": b"/artifact-content",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/octet-stream"),
                (b"content-length", str(declared_length).encode("ascii")),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "app": app,
        },
        receive,
    )


def test_project_import_and_workspace_revision_routes_form_an_authenticated_full_body_loop(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project_id = _create_project(client, headers)
        created = _create_import(client, headers, project_id)
        uploaded = client.put(
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}/content",
            content=_tiny_png(),
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        assert uploaded.status_code == 200
        revision = uploaded.json()

        session = client.get(
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}",
            headers=headers,
        )
        metadata = client.get(
            f"/api/v1/artifact-revisions/{revision['revision_id']}",
            headers=headers,
        )
        content = client.get(
            f"/api/v1/artifact-revisions/{revision['revision_id']}/content",
            headers={**headers, "Range": "bytes=1-3"},
        )

    assert session.status_code == 200
    assert session.json()["state"] == "COMPLETED"
    assert session.json()["artifact_revision"] == revision
    assert metadata.status_code == 200
    assert metadata.json() == revision
    assert revision["display_filename"] == "camera.png"
    assert revision["media_type"] == "image/png"
    assert revision["metadata"] == {"kind": "image", "width": 4, "height": 3}
    serialized = json.dumps((created, session.json(), revision), sort_keys=True).lower()
    for secret_name in ("path", "sha256", "blob", "bearer", "authorization"):
        assert secret_name not in serialized

    assert content.status_code == 200
    assert content.content == _tiny_png()
    assert content.headers["content-type"] == "image/png"
    assert content.headers["content-length"] == str(len(_tiny_png()))
    assert content.headers["content-disposition"] == "inline"
    assert content.headers["cache-control"] == "no-store"
    assert content.headers["x-content-type-options"] == "nosniff"
    assert content.headers["cross-origin-resource-policy"] == "same-origin"
    assert content.headers["referrer-policy"] == "no-referrer"
    assert "accept-ranges" not in content.headers
    assert "content-range" not in content.headers
    assert "etag" not in content.headers
    assert "last-modified" not in content.headers


def test_discard_import_result_route_retains_completed_content_for_gc(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project_id = _create_project(client, headers)
        created = _create_import(client, headers, project_id)
        uploaded = client.put(
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}/content",
            content=_tiny_png(),
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        assert uploaded.status_code == 200

        discarded = client.request(
            "DELETE",
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}/result",
            json={
                "client_import_id": created["client_import_id"],
                "expected_revision_id": uploaded.json()["revision_id"],
            },
            headers=headers,
        )

        assert discarded.status_code == 200
        assert discarded.json() == {
            "outcome": "discarded",
            "content_cleanup": "retained_shared",
        }
        assert client.get(
            f"/api/v1/artifact-revisions/{uploaded.json()['revision_id']}",
            headers=headers,
        ).status_code == 404


def test_discard_import_result_route_does_not_run_immediate_blob_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project_id = _create_project(client, headers)
        created = _create_import(client, headers, project_id)
        uploaded = client.put(
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}/content",
            content=_tiny_png(),
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        assert uploaded.status_code == 200

        def fail_cleanup(self, digest, expected_size):
            from ai_canvas.infrastructure.artifacts import ArtifactStorageIOError

            raise ArtifactStorageIOError

        monkeypatch.setattr(ArtifactStore, "remove_blob", fail_cleanup)
        discarded = client.request(
            "DELETE",
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}/result",
            json={
                "client_import_id": created["client_import_id"],
                "expected_revision_id": uploaded.json()["revision_id"],
            },
            headers=headers,
        )

        assert discarded.status_code == 200
        assert discarded.json() == {
            "outcome": "discarded",
            "content_cleanup": "retained_shared",
        }


def test_discard_import_result_route_uses_standard_local_mutation_security(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project_id = _create_project(client, headers)
        created = _create_import(client, headers, project_id)
        uploaded = client.put(
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}/content",
            content=_tiny_png(),
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        assert uploaded.status_code == 200
        revision_id = uploaded.json()["revision_id"]
        path = (
            f"/api/v1/projects/{project_id}/artifact-imports/"
            f"{created['import_id']}/result"
        )
        body = {
            "client_import_id": created["client_import_id"],
            "expected_revision_id": revision_id,
        }

        rejected = (
            client.request(
                "DELETE",
                path,
                json=body,
                headers={**headers, "Host": "evil.example"},
            ),
            client.request(
                "DELETE",
                path,
                json=body,
                headers={**headers, "Origin": "https://evil.example"},
            ),
            client.request(
                "DELETE",
                path,
                json=body,
                headers={"Authorization": headers["Authorization"]},
            ),
            client.request(
                "DELETE",
                path,
                json=body,
                headers={"Origin": headers["Origin"]},
            ),
            client.request(
                "DELETE",
                path,
                content=json.dumps(body),
                headers={
                    **headers,
                    "Content-Type": "text/plain",
                },
            ),
        )
        assert [response.status_code for response in rejected] == [
            400,
            403,
            403,
            401,
            415,
        ]
        assert client.get(
            f"/api/v1/artifact-revisions/{revision_id}",
            headers=headers,
        ).status_code == 200


@pytest.mark.parametrize("extra_field", ("path", "digest", "filename"))
def test_discard_route_rejects_client_supplied_storage_identity_without_deleting(
    tmp_path: Path,
    extra_field: str,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project_id = _create_project(client, headers)
        created = _create_import(client, headers, project_id)
        uploaded = client.put(
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}/content",
            content=_tiny_png(),
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        assert uploaded.status_code == 200
        revision_id = uploaded.json()["revision_id"]
        body = {
            "client_import_id": created["client_import_id"],
            "expected_revision_id": revision_id,
            extra_field: "client-controlled-value",
        }

        response = client.request(
            "DELETE",
            f"/api/v1/projects/{project_id}/artifact-imports/"
            f"{created['import_id']}/result",
            json=body,
            headers=headers,
        )

        assert response.status_code == 422
        assert client.get(
            f"/api/v1/artifact-revisions/{revision_id}",
            headers=headers,
        ).status_code == 200


def test_development_auto_session_discard_keeps_host_origin_and_json_guards(
    tmp_path: Path,
) -> None:
    origin = "http://127.0.0.1:5173"
    app = create_app(
        Settings(
            data_dir=tmp_path,
            runtime_profile="development",
            auth_mode="development-auto-session",
            public_origin="http://127.0.0.1:8765",
            development_origins=(origin,),
            allowed_hosts=("127.0.0.1",),
        )
    )
    with TestClient(
        app,
        base_url=origin,
        client=("127.0.0.1", 50000),
    ) as client:
        issued = client.post(
            "/api/v1/session/development",
            json={},
            headers={"Origin": origin, "Host": "127.0.0.1:5173"},
        )
        assert issued.status_code == 200
        headers = {
            "Authorization": f"Bearer {issued.json()['access_token']}",
            "Origin": origin,
            "Host": "127.0.0.1:5173",
        }
        project_id = _create_project(client, headers, "Development discard security")
        created = _create_import(client, headers, project_id)
        uploaded = client.put(
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}/content",
            content=_tiny_png(),
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        assert uploaded.status_code == 200
        revision_id = uploaded.json()["revision_id"]
        path = (
            f"/api/v1/projects/{project_id}/artifact-imports/"
            f"{created['import_id']}/result"
        )
        body = {
            "client_import_id": created["client_import_id"],
            "expected_revision_id": revision_id,
        }

        wrong_host = client.request(
            "DELETE",
            path,
            json=body,
            headers={**headers, "Host": "evil.example"},
        )
        wrong_origin = client.request(
            "DELETE",
            path,
            json=body,
            headers={**headers, "Origin": "http://127.0.0.1:5174"},
        )
        missing_origin = client.request(
            "DELETE",
            path,
            json=body,
            headers={
                "Authorization": headers["Authorization"],
                "Host": headers["Host"],
            },
        )
        missing_auth = client.request(
            "DELETE",
            path,
            json=body,
            headers={"Origin": origin, "Host": headers["Host"]},
        )
        wrong_type = client.request(
            "DELETE",
            path,
            content=json.dumps(body),
            headers={**headers, "Content-Type": "text/plain"},
        )

        assert [response.status_code for response in (
            wrong_host,
            wrong_origin,
            missing_origin,
            missing_auth,
            wrong_type,
        )] == [400, 403, 403, 401, 415]
        assert client.get(
            f"/api/v1/artifact-revisions/{revision_id}",
            headers=headers,
        ).status_code == 200


def test_discard_import_result_openapi_requires_owner_preconditions() -> None:
    operation = create_app().openapi()["paths"][
        "/api/v1/projects/{project_id}/artifact-imports/{import_id}/result"
    ]["delete"]

    assert operation["requestBody"]["required"] is True
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("/DiscardArtifactImportResultRequest")
    assert "200" in operation["responses"]
    assert "202" in operation["responses"]
    assert "404" in operation["responses"]
    assert "409" in operation["responses"]


def test_artifact_routes_restore_utc_on_fresh_sqlite_response_timestamps(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project_id = _create_project(client, headers)
        created = _create_import(client, headers, project_id)
        fetched = client.get(
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}",
            headers=headers,
        ).json()
        uploaded = client.put(
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}/content",
            content=_tiny_png(),
            headers={**headers, "Content-Type": "application/octet-stream"},
        ).json()
        revision = client.get(
            f"/api/v1/artifact-revisions/{uploaded['revision_id']}",
            headers=headers,
        ).json()

    for session in (created, fetched):
        _assert_utc_rfc3339(session["created_at"])
        _assert_utc_rfc3339(session["updated_at"])
    _assert_utc_rfc3339(uploaded["created_at"])
    _assert_utc_rfc3339(revision["created_at"])


def test_import_sessions_are_project_scoped_but_revisions_are_workspace_scoped(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        first_project = _create_project(client, headers, "First")
        second_project = _create_project(client, headers, "Second")
        created = _create_import(client, headers, first_project)
        uploaded = client.put(
            f"/api/v1/projects/{first_project}/artifact-imports/{created['import_id']}/content",
            content=_tiny_png(),
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        assert uploaded.status_code == 200
        revision_id = uploaded.json()["revision_id"]

        wrong_project = client.get(
            f"/api/v1/projects/{second_project}/artifact-imports/{created['import_id']}",
            headers=headers,
        )
        workspace_metadata = client.get(
            f"/api/v1/artifact-revisions/{revision_id}",
            headers=headers,
        )
        unknown_import_id = str(uuid4())
        unknown_revision_id = str(uuid4())
        missing_import = client.get(
            f"/api/v1/projects/{first_project}/artifact-imports/{unknown_import_id}",
            headers=headers,
        )
        missing_revision = client.get(
            f"/api/v1/artifact-revisions/{unknown_revision_id}",
            headers=headers,
        )
        missing_content = client.get(
            f"/api/v1/artifact-revisions/{unknown_revision_id}/content",
            headers=headers,
        )

    assert wrong_project.status_code == 404
    assert wrong_project.json() == {
        "code": "artifact_import_not_found",
        "import_id": created["import_id"],
    }
    assert workspace_metadata.status_code == 200
    assert missing_import.status_code == 404
    assert missing_import.json() == {
        "code": "artifact_import_not_found",
        "import_id": unknown_import_id,
    }
    for missing in (missing_revision, missing_content):
        assert missing.status_code == 404
        assert missing.json() == {
            "code": "artifact_revision_not_found",
            "revision_id": unknown_revision_id,
        }


def test_revision_history_returns_active_revisions_in_sequence_without_storage_metadata(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project_id = _create_project(client, headers)
        first, discarded, third, other = (
            _upload_revision(client, headers, project_id, content)
            for content in (
                _png((255, 0, 0)),
                _png((0, 255, 0)),
                _png((0, 0, 255)),
                _png((255, 255, 0)),
            )
        )
        with app.state.artifact_service.session_factory.begin() as session:
            revisions = [
                session.get(ArtifactRevisionRow, revision["revision_id"])
                for revision in (first, discarded, third)
            ]
            assert all(revisions)
            artifact_id = revisions[0].artifact_id
            revisions[1].artifact_id = artifact_id
            revisions[1].sequence = 2
            revisions[2].artifact_id = artifact_id
            revisions[2].sequence = 3
            discarded_import = session.get(ImportSessionRow, revisions[1].source_import_session_id)
            assert discarded_import is not None
            discarded_at = datetime.now(timezone.utc)
            revisions[1].discarded_at = discarded_at
            discarded_import.discarded_at = discarded_at

        response = client.get(
            f"/api/v1/artifacts/{artifact_id}/revisions",
            headers=headers,
        )

    assert response.status_code == 200
    history = response.json()
    assert [revision["sequence"] for revision in history] == [1, 3]
    assert [revision["revision_id"] for revision in history] == [
        first["revision_id"],
        third["revision_id"],
    ]
    assert other["revision_id"] not in {revision["revision_id"] for revision in history}
    serialized = json.dumps(history, sort_keys=True).lower()
    for secret_name in ("sha256", "blob", "path", "authorization", "token"):
        assert secret_name not in serialized


def test_revision_history_uses_one_query_for_multiple_revisions(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project_id = _create_project(client, headers)
        first, second, third = (
            _upload_revision(client, headers, project_id, content)
            for content in (
                _png((255, 0, 0)),
                _png((0, 255, 0)),
                _png((0, 0, 255)),
            )
        )
        with app.state.artifact_service.session_factory.begin() as session:
            revisions = [
                session.get(ArtifactRevisionRow, revision["revision_id"])
                for revision in (first, second, third)
            ]
            assert all(revisions)
            artifact_id = revisions[0].artifact_id
            for sequence, revision in enumerate(revisions, start=1):
                revision.artifact_id = artifact_id
                revision.sequence = sequence

        statements: list[str] = []

        def record_statement(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            statements.append(statement)

        event.listen(app.state.engine, "before_cursor_execute", record_statement)
        try:
            response = client.get(
                f"/api/v1/artifacts/{artifact_id}/revisions",
                headers=headers,
            )
        finally:
            event.remove(app.state.engine, "before_cursor_execute", record_statement)

    assert response.status_code == 200
    assert [revision["sequence"] for revision in response.json()] == [1, 2, 3]
    assert len(statements) == 1


def test_revision_history_returns_empty_list_for_an_unknown_artifact(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        response = client.get(
            f"/api/v1/artifacts/{uuid4()}/revisions",
            headers=_authorized_headers(app),
        )

    assert response.status_code == 200
    assert response.json() == []


def test_revision_history_requires_authentication_and_a_valid_artifact_uuid(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        unauthenticated = client.get(f"/api/v1/artifacts/{uuid4()}/revisions")
        invalid_identifier = client.get(
            "/api/v1/artifacts/not-a-uuid/revisions",
            headers=_authorized_headers(app),
        )

    assert unauthenticated.status_code == 401
    assert invalid_identifier.status_code == 422


def test_real_asgi_client_disconnect_is_retryable_with_the_same_import_id(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project_id = _create_project(client, headers)
        created = _create_import(client, headers, project_id)
        request = _disconnecting_upload_request(
            app,
            initial_body=_tiny_png()[:13],
            declared_length=len(_tiny_png()),
        )

        observed_error: Exception | None = None
        try:
            asyncio.run(
                upload_artifact_content(
                    UUID(project_id),
                    UUID(created["import_id"]),
                    request,
                )
            )
        except Exception as error:
            observed_error = error

        part = ArtifactStore(tmp_path).staging_path(UUID(created["import_id"]))
        failed = client.get(
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}",
            headers=headers,
        )
        retried = client.put(
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}/content",
            content=_tiny_png(),
            headers={**headers, "Content-Type": "application/octet-stream"},
        )

    assert not part.exists()
    assert failed.json()["state"] == "FAILED_RETRYABLE"
    assert retried.status_code == 200
    assert isinstance(observed_error, ArtifactImportFailed)
    assert observed_error.failure_code.value == "upload_interrupted"


def test_real_asgi_disconnect_during_completed_replay_preserves_completion(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project_id = _create_project(client, headers)
        created = _create_import(client, headers, project_id)
        uploaded = client.put(
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}/content",
            content=_tiny_png(),
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        assert uploaded.status_code == 200
        revision = uploaded.json()
        request = _disconnecting_upload_request(
            app,
            initial_body=_tiny_png()[:13],
            declared_length=len(_tiny_png()),
        )

        observed_error: Exception | None = None
        try:
            asyncio.run(
                upload_artifact_content(
                    UUID(project_id),
                    UUID(created["import_id"]),
                    request,
                )
            )
        except Exception as error:
            observed_error = error
        completed = client.get(
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}",
            headers=headers,
        )

    assert completed.json()["state"] == "COMPLETED"
    assert completed.json()["artifact_revision"] == revision
    assert isinstance(observed_error, ArtifactImportFailed)
    assert observed_error.failure_code.value == "upload_interrupted"


def test_artifact_openapi_has_only_the_locked_routes_and_a_binary_raw_put() -> None:
    app = create_app(Settings(data_dir=Path(".local/openapi-task4")))
    schema = app.openapi()
    artifact_paths = {
        path: operations
        for path, operations in schema["paths"].items()
        if "artifact" in path
        and path
        not in {
            "/api/v1/artifact-revisions/{revision_id}/external-reconfirm",
            "/api/v1/artifact-revisions/{revision_id}/external-verify",
            "/api/v1/artifact-revisions/{revision_id}/external-import-copy",
            "/api/v1/artifact-revisions/{revision_id}/renditions",
        }
    }
    assert set(artifact_paths) == {
        "/api/v1/projects/{project_id}/artifact-imports",
        "/api/v1/projects/{project_id}/artifact-imports/{import_id}",
        "/api/v1/projects/{project_id}/artifact-imports/{import_id}/content",
        "/api/v1/projects/{project_id}/artifact-imports/{import_id}/result",
        "/api/v1/artifacts/{artifact_id}/revisions",
        "/api/v1/artifact-revisions/{revision_id}",
        "/api/v1/artifact-revisions/{revision_id}/content",
    }
    assert {
        "/api/v1/artifact-revisions/{revision_id}/external-reconfirm",
        "/api/v1/artifact-revisions/{revision_id}/external-verify",
        "/api/v1/artifact-revisions/{revision_id}/external-import-copy",
        "/api/v1/artifact-revisions/{revision_id}/renditions",
    } <= set(schema["paths"])
    put = artifact_paths[
        "/api/v1/projects/{project_id}/artifact-imports/{import_id}/content"
    ]["put"]
    assert put["requestBody"] == {
        "required": True,
        "content": {
            "application/octet-stream": {
                "schema": {"type": "string", "format": "binary"}
            }
        },
    }
    assert "application/json" in put["responses"]["200"]["content"]


def test_route_failures_use_closed_problems_and_never_serve_corrupt_cas(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(
        app,
        base_url="http://testserver",
        raise_server_exceptions=False,
    ) as client:
        headers = _authorized_headers(app)
        project_id = _create_project(client, headers)
        corrupt = b"not-an-image"
        failed_init = client.post(
            f"/api/v1/projects/{project_id}/artifact-imports",
            json={
                "client_import_id": str(uuid4()),
                "filename": "bad.png",
                "expected_byte_size": len(corrupt),
                "requested_media_kind": "image",
                "declared_content_type": "image/png",
            },
            headers=headers,
        ).json()
        failed_upload = client.put(
            f"/api/v1/projects/{project_id}/artifact-imports/{failed_init['import_id']}/content",
            content=corrupt,
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        failed_replay = client.put(
            f"/api/v1/projects/{project_id}/artifact-imports/{failed_init['import_id']}/content",
            content=corrupt,
            headers={**headers, "Content-Type": "application/octet-stream"},
        )

        created = _create_import(client, headers, project_id)
        uploaded = client.put(
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}/content",
            content=_tiny_png(),
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        revision_id = uploaded.json()["revision_id"]
        with app.state.artifact_service.session_factory() as session:
            row = session.get(ArtifactRevisionRow, revision_id)
            assert row is not None
            digest = row.blob_sha256
        ArtifactStore(tmp_path).blob_path(digest).write_bytes(b"damaged")
        corrupt_read = client.get(
            f"/api/v1/artifact-revisions/{revision_id}/content",
            headers=headers,
        )

    assert failed_upload.status_code == 422
    assert failed_upload.json() == {
        "code": "artifact_import_failed",
        "import_id": failed_init["import_id"],
        "failure_code": "unsupported_media",
    }
    assert failed_replay.status_code == 409
    assert failed_replay.json() == {
        "code": "artifact_import_conflict",
        "import_id": failed_init["import_id"],
        "reason": "invalid_state",
    }
    assert corrupt_read.status_code == 500
    assert _tiny_png() not in corrupt_read.content
    assert digest not in corrupt_read.text
    assert str(tmp_path).lower() not in corrupt_read.text.lower()


def test_raw_upload_rejects_a_malformed_content_length_header(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project_id = _create_project(client, headers)
        created = _create_import(client, headers, project_id)

        response = client.put(
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}/content",
            content=_tiny_png(),
            headers={
                **headers,
                "Content-Type": "application/octet-stream",
                "Content-Length": "not-a-number",
            },
        )
        session = client.get(
            f"/api/v1/projects/{project_id}/artifact-imports/{created['import_id']}",
            headers=headers,
        )

    assert response.status_code == 422
    assert response.json() == {
        "code": "artifact_import_failed",
        "import_id": created["import_id"],
        "failure_code": "size_mismatch",
    }
    assert session.status_code == 200
    assert session.json()["state"] == "FAILED_FINAL"
