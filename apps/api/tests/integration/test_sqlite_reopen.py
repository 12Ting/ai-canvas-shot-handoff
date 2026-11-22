from __future__ import annotations

import base64
from collections.abc import Mapping
from datetime import datetime, timezone
from importlib import import_module, util
import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from ai_canvas.config import Settings
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)
from ai_canvas.main import create_app


MAX_B1_MEDIA_BYTES = 64 * 1024 * 1024
APPROVED_IMPORT_FAILURE_CODES = {
    "upload_interrupted",
    "probe_timeout",
    "probe_unavailable",
    "size_mismatch",
    "corrupt_media",
    "unsupported_media",
    "incompatible_declaration",
}
APPROVED_IMPORT_CONFLICT_REASONS = {
    "declaration_mismatch",
    "content_replay_mismatch",
    "upload_in_progress",
    "invalid_replacement",
    "invalid_state",
    "discard_precondition_failed",
    "result_referenced",
}
UNAPPROVED_INTERNAL_TEXT = (
    r"C:\private\media\probe-output.txt",
    "arbitrary_internal_error",
    "a" * 64,
)
SENSITIVE_ARTIFACT_FIELDS = {
    "computed_sha256",
    "blob_sha256",
    "blob_path",
    "local_path",
    "content_path",
}
POSITIVE_FLOAT_ARTIFACT_SETTINGS = (
    "artifact_upload_idle_timeout_seconds",
    "artifact_upload_total_timeout_seconds",
    "artifact_probe_metadata_timeout_seconds",
    "artifact_probe_full_scan_timeout_seconds",
    "artifact_video_max_duration_seconds",
    "artifact_video_max_fps",
    "artifact_staging_cleanup_age_seconds",
)


def _artifact_revision_payload(
    *,
    source_project_id: object,
    byte_size: int = 4,
    source_kind: str = "import",
) -> dict[str, object]:
    return {
        "revision_id": uuid4(),
        "artifact_id": uuid4(),
        "sequence": 1,
        "media_kind": "image",
        "media_type": "image/png",
        "byte_size": byte_size,
        "display_filename": "camera.png",
        "metadata": {"kind": "image", "width": 1, "height": 2},
        "source_kind": source_kind,
        "source_project_id": source_project_id,
        "origin_project_id": uuid4(),
        "created_at": datetime(2026, 7, 20, tzinfo=timezone.utc),
    }


def _completed_import_payload(
    *,
    expected_byte_size: int = 4,
    received_byte_size: int = 4,
    revision_byte_size: int = 4,
    revision_source_kind: str = "import",
) -> dict[str, object]:
    source_project_id = uuid4()
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    return {
        "import_id": uuid4(),
        "source_project_id": source_project_id,
        "client_import_id": uuid4(),
        "replace_revision_id": None,
        "requested_media_kind": "image",
        "original_filename": "../camera.png",
        "declared_content_type": "image/png",
        "expected_byte_size": expected_byte_size,
        "state": "COMPLETED",
        "received_byte_size": received_byte_size,
        "failure_code": None,
        "created_at": now,
        "updated_at": now,
        "artifact_revision": _artifact_revision_payload(
            source_project_id=source_project_id,
            byte_size=revision_byte_size,
            source_kind=revision_source_kind,
        ),
    }


def _failed_import_payload(failure_code: str) -> dict[str, object]:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    return {
        "import_id": uuid4(),
        "source_project_id": uuid4(),
        "client_import_id": uuid4(),
        "replace_revision_id": None,
        "requested_media_kind": "image",
        "original_filename": "camera.png",
        "declared_content_type": "image/png",
        "expected_byte_size": 4,
        "state": "FAILED_FINAL",
        "received_byte_size": 4,
        "failure_code": failure_code,
        "created_at": now,
        "updated_at": now,
        "artifact_revision": None,
    }


def _recursive_mapping_keys(value: object) -> set[str]:
    if isinstance(value, Mapping):
        keys = {str(key) for key in value}
        for nested in value.values():
            keys.update(_recursive_mapping_keys(nested))
        return keys
    if isinstance(value, (list, tuple)):
        keys: set[str] = set()
        for nested in value:
            keys.update(_recursive_mapping_keys(nested))
        return keys
    return set()


def open_authenticated(app: FastAPI) -> tuple[TestClient, dict[str, str]]:
    client = TestClient(app, base_url="http://testserver")
    client.__enter__()
    response = client.post(
        "/api/v1/session/exchange",
        headers={
            "X-Canvas-Bootstrap": app.state.session_manager.bootstrap_token,
            "Origin": "http://testserver",
        },
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    return client, {"Authorization": f"Bearer {token}", "Origin": "http://testserver"}


def _apply_graph_changeset(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    base_revision_id: str,
    snapshot: dict[str, object],
) -> dict[str, object]:
    proposal = client.post(
        f"/api/v1/projects/{project_id}/graph-change-sets",
        json={
            "kind": "graph_change_set",
            "schema_version": 1,
            "base_graph_revision_id": base_revision_id,
            "client_mutation_id": str(uuid4()),
            "operations": [{"op": "replace_snapshot", "snapshot": snapshot}],
        },
        headers=headers,
    )
    assert proposal.status_code == 201, proposal.text
    decision = client.post(
        f"/api/v1/projects/{project_id}/graph-change-sets/{proposal.json()['id']}/approval-decisions",
        json={"expected_changeset_sha256": proposal.json()["changeset_sha256"]},
        headers=headers,
    )
    assert decision.status_code == 200, decision.text
    applied = client.post(
        f"/api/v1/projects/{project_id}/graph-change-sets/{proposal.json()['id']}/apply",
        json={"expected_changeset_sha256": proposal.json()["changeset_sha256"]},
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    graph = client.get(f"/api/v1/projects/{project_id}/graph", headers=headers)
    assert graph.status_code == 200, graph.text
    return graph.json()


def _tiny_png() -> bytes:
    fixture = Path(__file__).parents[1] / "fixtures" / "tiny_png.b64"
    return base64.b64decode(fixture.read_text(encoding="ascii"))


def test_resource_models_and_repository_survive_sqlite_reopen(tmp_path: Path) -> None:
    models = import_module("ai_canvas.infrastructure.db.models")
    repositories = import_module("ai_canvas.infrastructure.db.repositories")
    for name in (
        "ResourceRow",
        "ResourceRevisionRow",
        "ResourceRevisionTagRow",
        "ResourceRevisionArtifactRow",
    ):
        assert hasattr(models, name), f"missing model {name}"
    assert hasattr(repositories, "ResourceRepository")

    database_path = tmp_path / "resource-reopen.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    repository = repositories.ResourceRepository()
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    project_id = str(uuid4())
    resource_id = str(uuid4())
    revision_id = str(uuid4())
    try:
        with session_factory.begin() as session:
            session.add(
                models.ProjectRow(
                    id=project_id,
                    name="Resource reopen",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
            repository.create_resource_with_revision(
                session,
                models.ResourceRow(
                    id=resource_id,
                    origin_project_id=project_id,
                    kind="STYLE",
                    name="本地风格",
                    created_at=now,
                    archived_at=None,
                ),
                models.ResourceRevisionRow(
                    id=revision_id,
                    resource_id=resource_id,
                    sequence=1,
                    schema_version=1,
                    payload_json='{"kind":"STYLE","summary":"低饱和"}',
                    payload_sha256="a" * 64,
                    search_text="本地风格 低饱和",
                    created_by_actor="human:test",
                    created_at=now,
                ),
                tags=[
                    models.ResourceRevisionTagRow(
                        id=str(uuid4()),
                        resource_revision_id=revision_id,
                        normalized_tag="cinematic",
                        display_tag="Cinematic",
                    )
                ],
                artifacts=[],
            )
    finally:
        engine.dispose()

    reopened_engine = create_sqlite_engine(database_path)
    reopened_factory = make_session_factory(reopened_engine)
    try:
        with reopened_factory() as session:
            assert repository.get_resource(session, resource_id).name == "本地风格"
            assert repository.get_revision(session, revision_id).payload_sha256 == "a" * 64
            assert repository.get_latest_revision(session, resource_id).id == revision_id
            assert [row.id for row in repository.list_revisions(session, resource_id)] == [
                revision_id
            ]
            assert [
                row.id
                for row in repository.search_latest(
                    session,
                    kind="STYLE",
                    query="低饱和",
                    normalized_tags=("cinematic",),
                    include_archived=False,
                    limit=10,
                )
            ] == [revision_id]
    finally:
        reopened_engine.dispose()


def test_resource_repository_rejects_a_stale_revision_base(tmp_path: Path) -> None:
    errors = import_module("ai_canvas.application.errors")
    models = import_module("ai_canvas.infrastructure.db.models")
    repositories = import_module("ai_canvas.infrastructure.db.repositories")
    assert hasattr(errors, "ResourceRevisionConflict")

    database_path = tmp_path / "resource-cas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    repository = repositories.ResourceRepository()
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    project_id = str(uuid4())
    resource_id = str(uuid4())
    first_revision_id = str(uuid4())
    second_revision_id = str(uuid4())
    try:
        with session_factory.begin() as session:
            session.add(
                models.ProjectRow(
                    id=project_id,
                    name="Resource CAS",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
            repository.create_resource_with_revision(
                session,
                models.ResourceRow(
                    id=resource_id,
                    origin_project_id=project_id,
                    kind="STYLE",
                    name="CAS style",
                    created_at=now,
                    archived_at=None,
                ),
                models.ResourceRevisionRow(
                    id=first_revision_id,
                    resource_id=resource_id,
                    sequence=1,
                    schema_version=1,
                    payload_json='{"kind":"STYLE","summary":"one"}',
                    payload_sha256="b" * 64,
                    search_text="CAS style one",
                    created_by_actor="human:test",
                    created_at=now,
                ),
                tags=[],
                artifacts=[],
            )

        with pytest.raises(errors.ResourceRevisionConflict) as raised:
            with session_factory.begin() as session:
                repository.append_revision(
                    session,
                    models.ResourceRevisionRow(
                        id=second_revision_id,
                        resource_id=resource_id,
                        sequence=2,
                        schema_version=1,
                        payload_json='{"kind":"STYLE","summary":"two"}',
                        payload_sha256="c" * 64,
                        search_text="CAS style two",
                        created_by_actor="human:test",
                        created_at=now,
                    ),
                    base_revision_id=str(uuid4()),
                    tags=[],
                    artifacts=[],
                )
        assert raised.value.current_revision_id == first_revision_id
        with session_factory() as session:
            assert repository.get_revision(session, second_revision_id) is None
            assert repository.get_latest_revision(session, resource_id).id == first_revision_id
    finally:
        engine.dispose()


def test_reopens_same_active_graph_after_app_restart(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, public_origin="http://testserver")
    app_a = create_app(settings)
    client_a, headers_a = open_authenticated(app_a)
    try:
        project = client_a.post(
            "/api/v1/projects", json={"name": "Restart"}, headers=headers_a
        ).json()
        saved = _apply_graph_changeset(
            client_a,
            headers_a,
            project["id"],
            project["active_revision_id"],
            {
                "schema_version": 1,
                "nodes": [
                    {
                        "id": str(uuid4()),
                        "kind": "note",
                        "position": {"x": 20, "y": 40},
                        "data": {"text": "survives restart"},
                    }
                ],
                "edges": [],
            },
        )
    finally:
        client_a.__exit__(None, None, None)

    app_b = create_app(settings)
    client_b, headers_b = open_authenticated(app_b)
    try:
        reopened = client_b.get(
            f"/api/v1/projects/{project['id']}/graph", headers=headers_b
        ).json()
    finally:
        client_b.__exit__(None, None, None)

    assert reopened["revision_id"] == saved["revision_id"]
    assert reopened["snapshot"] == saved["snapshot"]


def test_artifact_models_and_repositories_survive_sqlite_reopen(tmp_path: Path) -> None:
    models = import_module("ai_canvas.infrastructure.db.models")
    repositories = import_module("ai_canvas.infrastructure.db.repositories")
    for name in (
        "StoredBlobRow",
        "ArtifactRow",
        "ImportSessionRow",
        "ArtifactRevisionRow",
    ):
        assert hasattr(models, name), f"missing model {name}"
    assert hasattr(repositories, "ArtifactRepository")

    database_path = tmp_path / "artifact-reopen.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    repository = repositories.ArtifactRepository()
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    project_id = str(uuid4())
    client_import_id = str(uuid4())
    import_id = str(uuid4())
    artifact_id = str(uuid4())
    revision_id = str(uuid4())
    sha256 = "b" * 64
    try:
        with session_factory.begin() as session:
            session.add(
                models.ProjectRow(
                    id=project_id,
                    name="Artifact reopen",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
            repository.add_blob(
                session,
                models.StoredBlobRow(
                    sha256=sha256,
                    byte_size=4,
                    media_kind="image",
                    media_type="image/png",
                    metadata_json='{"kind":"image","width":1,"height":1}',
                    created_at=now,
                ),
            )
            repository.add_artifact(
                session,
                models.ArtifactRow(
                    id=artifact_id,
                    origin_project_id=project_id,
                    kind="image",
                    created_at=now,
                ),
            )
            repository.add_import_session(
                session,
                models.ImportSessionRow(
                    id=import_id,
                    source_project_id=project_id,
                    client_import_id=client_import_id,
                    replace_revision_id=None,
                    requested_media_kind="image",
                    original_filename="../unsafe.png",
                    declared_content_type="image/png",
                    expected_byte_size=4,
                    state="COMPLETED",
                    received_byte_size=4,
                    computed_sha256=sha256,
                    failure_code=None,
                    created_at=now,
                    updated_at=now,
                ),
            )
            session.flush()
            repository.add_revision(
                session,
                models.ArtifactRevisionRow(
                    id=revision_id,
                    artifact_id=artifact_id,
                    sequence=1,
                    blob_sha256=sha256,
                    source_kind="import",
                    source_project_id=project_id,
                    source_import_session_id=import_id,
                    display_filename="unsafe.png",
                    created_at=now,
                ),
            )
    finally:
        engine.dispose()

    reopened_engine = create_sqlite_engine(database_path)
    reopened_factory = make_session_factory(reopened_engine)
    try:
        with reopened_factory() as session:
            assert repository.blob(session, sha256).byte_size == 4
            assert repository.artifact(session, artifact_id).origin_project_id == project_id
            assert (
                repository.import_session_for_client(session, project_id, client_import_id).id
                == import_id
            )
            assert repository.revision(session, revision_id).display_filename == "unsafe.png"
            assert repository.revision_for_import(session, import_id).id == revision_id
    finally:
        reopened_engine.dispose()


def test_imported_artifact_and_graph_reference_survive_full_app_reopen(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        public_origin="http://testserver",
        bootstrap_token="artifact-reopen-bootstrap",
    )
    content = _tiny_png()
    node_id = str(uuid4())

    app_a = create_app(settings)
    client_a, headers_a = open_authenticated(app_a)
    try:
        project = client_a.post(
            "/api/v1/projects",
            json={"name": "Artifact route reopen"},
            headers=headers_a,
        ).json()
        initial_graph = client_a.get(
            f"/api/v1/projects/{project['id']}/graph",
            headers=headers_a,
        ).json()
        created_import = client_a.post(
            f"/api/v1/projects/{project['id']}/artifact-imports",
            json={
                "client_import_id": str(uuid4()),
                "filename": "../camera.png",
                "expected_byte_size": len(content),
                "requested_media_kind": "image",
                "declared_content_type": "image/png",
                "replace_revision_id": None,
            },
            headers=headers_a,
        )
        assert created_import.status_code == 200
        uploaded = client_a.put(
            (
                f"/api/v1/projects/{project['id']}/artifact-imports/"
                f"{created_import.json()['import_id']}/content"
            ),
            content=content,
            headers={**headers_a, "Content-Type": "application/octet-stream"},
        )
        assert uploaded.status_code == 200
        revision = uploaded.json()
        saved_graph = _apply_graph_changeset(
            client_a,
            headers_a,
            project["id"],
            initial_graph["revision_id"],
            {
                "schema_version": 2,
                "nodes": [
                    {
                        "id": node_id,
                        "kind": "media_input",
                        "position": {"x": 20, "y": 40},
                        "data": {
                            "artifact_revision_id": revision["revision_id"],
                            "media_kind": "image",
                        },
                    }
                ],
                "edges": [],
                "shots": [],
            },
        )
    finally:
        client_a.__exit__(None, None, None)

    assert (tmp_path / "canvas.sqlite3").is_file()
    stored_blobs = [
        candidate
        for candidate in (tmp_path / "artifacts" / "blobs" / "sha256").rglob("*")
        if candidate.is_file()
    ]
    assert len(stored_blobs) == 1
    assert stored_blobs[0].read_bytes() == content

    app_b = create_app(settings)
    client_b, headers_b = open_authenticated(app_b)
    try:
        reopened_graph = client_b.get(
            f"/api/v1/projects/{project['id']}/graph",
            headers=headers_b,
        )
        reopened_summary = client_b.get(
            f"/api/v1/artifact-revisions/{revision['revision_id']}",
            headers=headers_b,
        )
        reopened_content = client_b.get(
            f"/api/v1/artifact-revisions/{revision['revision_id']}/content",
            headers=headers_b,
        )
    finally:
        client_b.__exit__(None, None, None)

    assert reopened_graph.status_code == 200
    assert reopened_graph.json()["revision_id"] == saved_graph["revision_id"]
    assert reopened_graph.json()["snapshot"]["nodes"] == [
        {
            "id": node_id,
            "kind": "media_input",
            "position": {"x": 20.0, "y": 40.0},
            "data": {
                "artifact_revision_id": revision["revision_id"],
                "media_kind": "image",
            },
        }
    ]
    assert reopened_summary.status_code == 200
    assert reopened_summary.json() == revision
    assert reopened_content.status_code == 200
    assert reopened_content.headers["content-type"] == "image/png"
    assert reopened_content.content == content


def test_artifact_contracts_are_strict_and_bounded() -> None:
    assert util.find_spec("ai_canvas.contracts.artifact") is not None, "artifact contracts missing"
    contracts = import_module("ai_canvas.contracts.artifact")
    client_import_id = uuid4()

    image = contracts.CreateArtifactImportRequest(
        client_import_id=client_import_id,
        filename="../camera.png",
        expected_byte_size=MAX_B1_MEDIA_BYTES,
        requested_media_kind="image",
        declared_content_type="image/png",
    )
    video = contracts.CreateArtifactImportRequest(
        client_import_id=uuid4(),
        filename="clip.webm",
        expected_byte_size=MAX_B1_MEDIA_BYTES,
        requested_media_kind="video",
    )
    assert image.filename == "../camera.png"
    assert video.expected_byte_size == MAX_B1_MEDIA_BYTES

    invalid_declarations = (
        {**image.model_dump(), "unexpected": True},
        {**image.model_dump(), "filename": "x" * 513},
        {**image.model_dump(), "expected_byte_size": 0},
        {**image.model_dump(), "expected_byte_size": MAX_B1_MEDIA_BYTES + 1},
        {**image.model_dump(), "requested_media_kind": "audio"},
        {**image.model_dump(), "declared_content_type": "x" * 256},
    )
    for payload in invalid_declarations:
        with pytest.raises(ValidationError):
            contracts.CreateArtifactImportRequest.model_validate(payload)

    metadata = contracts.ImageArtifactMetadata(kind="image", width=1, height=2)
    revision = contracts.ArtifactRevisionResponse(
        revision_id=uuid4(),
        artifact_id=uuid4(),
        sequence=1,
        media_kind="image",
        media_type="image/png",
        byte_size=4,
        display_filename="camera.png",
        metadata=metadata,
        source_kind="import",
        source_project_id=uuid4(),
        origin_project_id=uuid4(),
        created_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    completed = contracts.ArtifactImportSessionResponse(
        import_id=uuid4(),
        source_project_id=revision.source_project_id,
        client_import_id=client_import_id,
        replace_revision_id=None,
        requested_media_kind="image",
        original_filename="../camera.png",
        declared_content_type="image/png",
        expected_byte_size=4,
        state="COMPLETED",
        received_byte_size=4,
        failure_code=None,
        created_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        artifact_revision=revision,
    )
    assert completed.artifact_revision == revision
    assert "computed_sha256" not in completed.model_dump()

    with pytest.raises(ValidationError):
        contracts.ArtifactRevisionResponse.model_validate(
            {**revision.model_dump(), "computed_sha256": "c" * 64}
        )
    with pytest.raises(ValidationError):
        contracts.ArtifactRevisionResponse.model_validate(
            {
                **revision.model_dump(),
                "metadata": {
                    "kind": "video",
                    "width": 1,
                    "height": 2,
                    "duration_seconds": 1,
                    "frames_per_second": 24,
                },
            }
        )
    with pytest.raises(ValidationError):
        contracts.ArtifactImportSessionResponse.model_validate(
            {**completed.model_dump(), "state": "VALIDATING", "artifact_revision": revision}
        )
    with pytest.raises(ValidationError):
        contracts.ArtifactImportSessionResponse.model_validate(
            {**completed.model_dump(), "state": "COMPLETED", "artifact_revision": None}
        )


def test_artifact_import_codes_are_closed_string_enums() -> None:
    contracts = import_module("ai_canvas.contracts.artifact")
    assert hasattr(contracts, "ArtifactImportFailureCode")
    assert hasattr(contracts, "ArtifactImportConflictReason")
    assert {item.value for item in contracts.ArtifactImportFailureCode} == (
        APPROVED_IMPORT_FAILURE_CODES
    )
    assert {item.value for item in contracts.ArtifactImportConflictReason} == (
        APPROVED_IMPORT_CONFLICT_REASONS
    )
    assert issubclass(contracts.ArtifactImportFailureCode, str)
    assert issubclass(contracts.ArtifactImportConflictReason, str)


@pytest.mark.parametrize("failure_code", UNAPPROVED_INTERNAL_TEXT)
def test_import_session_rejects_unapproved_failure_code(failure_code: str) -> None:
    contracts = import_module("ai_canvas.contracts.artifact")
    with pytest.raises(ValidationError):
        contracts.ArtifactImportSessionResponse.model_validate(
            _failed_import_payload(failure_code)
        )


@pytest.mark.parametrize("reason", UNAPPROVED_INTERNAL_TEXT)
def test_import_conflict_problem_rejects_unapproved_reason(reason: str) -> None:
    problems = import_module("ai_canvas.contracts.problems")
    with pytest.raises(ValidationError):
        problems.ArtifactImportConflictProblem(import_id=uuid4(), reason=reason)


@pytest.mark.parametrize("failure_code", UNAPPROVED_INTERNAL_TEXT)
def test_import_failed_problem_rejects_unapproved_code(failure_code: str) -> None:
    problems = import_module("ai_canvas.contracts.problems")
    with pytest.raises(ValidationError):
        problems.ArtifactImportFailedProblem(
            import_id=uuid4(),
            failure_code=failure_code,
        )


@pytest.mark.parametrize("reason", UNAPPROVED_INTERNAL_TEXT)
def test_import_conflict_exception_rejects_unapproved_reason(reason: str) -> None:
    errors = import_module("ai_canvas.application.errors")
    with pytest.raises(ValueError):
        errors.ArtifactImportConflict(str(uuid4()), reason)


@pytest.mark.parametrize("failure_code", UNAPPROVED_INTERNAL_TEXT)
def test_import_failed_exception_rejects_unapproved_code(failure_code: str) -> None:
    errors = import_module("ai_canvas.application.errors")
    with pytest.raises(ValueError):
        errors.ArtifactImportFailed(str(uuid4()), failure_code)


def test_approved_import_codes_serialize_as_safe_strings() -> None:
    contracts = import_module("ai_canvas.contracts.artifact")
    problems = import_module("ai_canvas.contracts.problems")
    errors = import_module("ai_canvas.application.errors")

    response = contracts.ArtifactImportSessionResponse.model_validate(
        _failed_import_payload("size_mismatch")
    )
    failed_problem = problems.ArtifactImportFailedProblem(
        import_id=response.import_id,
        failure_code="size_mismatch",
    )
    conflict_problem = problems.ArtifactImportConflictProblem(
        import_id=response.import_id,
        reason="content_replay_mismatch",
    )
    failed_error = errors.ArtifactImportFailed(str(response.import_id), "size_mismatch")
    conflict_error = errors.ArtifactImportConflict(
        str(response.import_id),
        "content_replay_mismatch",
    )

    assert response.model_dump(mode="json")["failure_code"] == "size_mismatch"
    assert failed_problem.model_dump(mode="json")["failure_code"] == "size_mismatch"
    assert conflict_problem.model_dump(mode="json")["reason"] == "content_replay_mismatch"
    assert (
        type(failed_error.failure_code).__name__,
        getattr(failed_error.failure_code, "value", None),
    ) == ("ArtifactImportFailureCode", "size_mismatch")
    assert (
        type(conflict_error.reason).__name__,
        getattr(conflict_error.reason, "value", None),
    ) == ("ArtifactImportConflictReason", "content_replay_mismatch")
    assert "size_mismatch" in str(failed_error)
    assert "content_replay_mismatch" in str(conflict_error)


def test_artifact_response_contracts_recursively_exclude_sensitive_fields() -> None:
    contracts = import_module("ai_canvas.contracts.artifact")
    problems = import_module("ai_canvas.contracts.problems")
    response = contracts.ArtifactImportSessionResponse.model_validate(
        _completed_import_payload()
    )
    problem = problems.ArtifactImportFailedProblem(
        import_id=response.import_id,
        failure_code="size_mismatch",
    )
    inspected_values = (
        response.model_dump(mode="json"),
        response.artifact_revision.model_dump(mode="json"),
        problem.model_dump(mode="json"),
        contracts.ArtifactImportSessionResponse.model_json_schema(),
        contracts.ArtifactRevisionResponse.model_json_schema(),
        problems.ArtifactImportConflictProblem.model_json_schema(),
        problems.ArtifactImportFailedProblem.model_json_schema(),
    )
    for value in inspected_values:
        assert SENSITIVE_ARTIFACT_FIELDS.isdisjoint(_recursive_mapping_keys(value))


def test_completed_import_requires_import_source_kind() -> None:
    contracts = import_module("ai_canvas.contracts.artifact")
    with pytest.raises(ValidationError):
        contracts.ArtifactImportSessionResponse.model_validate(
            _completed_import_payload(revision_source_kind="ai_generated")
        )


@pytest.mark.parametrize(
    "overrides",
    (
        {"received_byte_size": 3},
        {"expected_byte_size": 3},
        {"revision_byte_size": 3},
    ),
    ids=("received-size", "expected-size", "revision-size"),
)
def test_completed_import_requires_all_byte_sizes_to_match(
    overrides: dict[str, int],
) -> None:
    contracts = import_module("ai_canvas.contracts.artifact")
    with pytest.raises(ValidationError):
        contracts.ArtifactImportSessionResponse.model_validate(
            _completed_import_payload(**overrides)
        )


def test_artifact_settings_preserve_graph_limit_and_locked_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in tuple(os.environ):
        if name.upper().startswith("AI_CANVAS_"):
            monkeypatch.delenv(name)
    settings = Settings()
    assert settings.graph_revision_body_limit_bytes == 16 * 1024 * 1024
    assert settings.artifact_image_max_bytes == MAX_B1_MEDIA_BYTES
    assert settings.artifact_video_max_bytes == MAX_B1_MEDIA_BYTES
    assert settings.artifact_upload_chunk_bytes == 1024 * 1024
    assert settings.artifact_upload_idle_timeout_seconds == 30
    assert settings.artifact_upload_total_timeout_seconds == 900
    assert settings.artifact_probe_metadata_timeout_seconds == 10
    assert settings.artifact_probe_full_scan_timeout_seconds == 120
    assert settings.artifact_probe_output_limit_bytes == 256 * 1024
    assert settings.artifact_image_max_pixels == 50_000_000
    assert settings.artifact_image_max_side == 16_384
    assert settings.artifact_video_max_duration_seconds == 600
    assert settings.artifact_video_max_side == 4_096
    assert settings.artifact_video_max_fps == 120
    assert settings.artifact_staging_cleanup_age_seconds == 3_600
    assert settings.ffprobe_path is None


@pytest.mark.parametrize("field_name", POSITIVE_FLOAT_ARTIFACT_SETTINGS)
@pytest.mark.parametrize(
    "invalid_value",
    (float("nan"), float("inf"), float("-inf")),
    ids=("nan", "positive-infinity", "negative-infinity"),
)
def test_artifact_positive_float_settings_reject_non_finite_values(
    field_name: str,
    invalid_value: float,
) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field_name: invalid_value})


def test_packaged_spa_mount_does_not_shadow_api(tmp_path: Path) -> None:
    web_dist = tmp_path / "web"
    web_dist.mkdir()
    (web_dist / "index.html").write_text(
        "<main>packaged-canvas</main>", encoding="utf-8"
    )
    app = create_app(
        Settings(
            data_dir=tmp_path / "data",
            public_origin="http://testserver",
            web_dist=web_dist,
        )
    )
    with TestClient(app, base_url="http://testserver") as client:
        page = client.get("/")
        health = client.get("/api/v1/health")
    assert page.status_code == 200
    assert "packaged-canvas" in page.text
    assert "style-src 'self' 'unsafe-inline'" in page.headers[
        "content-security-policy"
    ]
    assert health.json() == {"name": "ai-canvas-core", "version": "0.1.0"}
