from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from ai_canvas.config import Settings
from ai_canvas.contracts.artifact import VideoArtifactMetadata
from ai_canvas.domain.media.probe import MediaProbeResult
from ai_canvas.main import create_app


class FakeNativeMediaPicker:
    def __init__(self, root: Path, selected_file: Path) -> None:
        self.root = root
        self.selected_file = selected_file

    def choose_directory(self) -> Path:
        return self.root

    def choose_file(self, _root: Path) -> Path:
        return self.selected_file


def _video_probe(_path: Path, _kind: str, _root: Path) -> MediaProbeResult:
    return MediaProbeResult(
        kind="video",
        media_type="video/mp4",
        metadata=VideoArtifactMetadata(
            width=1920,
            height=1080,
            duration_seconds=1.0,
            frames_per_second=24.0,
        ),
    )


def _authorized_headers(app) -> dict[str, str]:
    token = app.state.session_manager.exchange(app.state.session_manager.bootstrap_token)
    return {"Authorization": f"Bearer {token}", "Origin": "http://testserver"}


def test_external_reference_routes_use_only_native_picker_results(tmp_path: Path) -> None:
    root = tmp_path / "selected-root"
    root.mkdir()
    selected = root / "clip.mp4"
    selected.write_bytes(b"external clip")
    app = create_app(Settings(data_dir=tmp_path / "data", public_origin="http://testserver"))
    app.state.native_media_picker = FakeNativeMediaPicker(root, selected)
    with TestClient(app, base_url="http://testserver") as client:
        app.state.external_reference_service._probe = _video_probe
        headers = _authorized_headers(app)
        project = client.post("/api/v1/projects", json={"name": "B4 external API"}, headers=headers)
        assert project.status_code == 201
        grant = client.post("/api/v1/external-root-grants/select", json={}, headers=headers)
        assert grant.status_code == 200
        assert "canonical_path" not in grant.json()
        created = client.post(
            f"/api/v1/external-root-grants/{grant.json()['grant_id']}/references/select",
            json={
                "project_id": project.json()["id"],
                "requested_media_kind": "video",
            },
            headers=headers,
        )

        selected.write_bytes(b"external clip changed for reconfirmation")
        verified = client.post(
            f"/api/v1/artifact-revisions/{created.json()['revision_id']}/external-verify",
            json={},
            headers=headers,
        )
        reconfirmed = client.post(
            f"/api/v1/artifact-revisions/{created.json()['revision_id']}/external-reconfirm",
            json={"grant_id": grant.json()["grant_id"]},
            headers=headers,
        )
        copied = client.post(
            f"/api/v1/artifact-revisions/{reconfirmed.json()['revision_id']}/external-import-copy",
            json={},
            headers=headers,
        )
        from ai_canvas.infrastructure.db.models import StorageReservationRow

        with app.state.external_reference_service.session_factory() as session:
            reservation = session.scalar(
                select(StorageReservationRow).where(
                    StorageReservationRow.operation_type == "EXTERNAL_IMPORT_COPY"
                )
            )

    assert created.status_code == 200
    assert created.json()["health"] == "AVAILABLE"
    assert created.json()["storage_kind"] == "EXTERNAL_REFERENCE"
    assert verified.status_code == 200
    assert verified.json()["revision_id"] == created.json()["revision_id"]
    assert verified.json()["health"] == "DRIFTED"
    assert reconfirmed.status_code == 200
    assert reconfirmed.json()["revision_id"] != created.json()["revision_id"]
    assert reconfirmed.json()["artifact_id"] == created.json()["artifact_id"]
    assert copied.status_code == 200
    assert copied.json()["storage_kind"] == "MANAGED_BLOB"
    assert reservation is not None and reservation.state == "RELEASED"


def test_external_picker_path_escape_returns_a_stable_problem_code(tmp_path: Path) -> None:
    root = tmp_path / "selected-root"
    root.mkdir()
    selected = tmp_path / "outside.mp4"
    selected.write_bytes(b"outside")
    app = create_app(Settings(data_dir=tmp_path / "data", public_origin="http://testserver"))
    app.state.native_media_picker = FakeNativeMediaPicker(root, selected)
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as client:
        app.state.external_reference_service._probe = _video_probe
        headers = _authorized_headers(app)
        project = client.post("/api/v1/projects", json={"name": "B4 picker escape"}, headers=headers)
        grant = client.post("/api/v1/external-root-grants/select", json={}, headers=headers)
        rejected = client.post(
            f"/api/v1/external-root-grants/{grant.json()['grant_id']}/references/select",
            json={"project_id": project.json()["id"], "requested_media_kind": "video"},
            headers=headers,
        )

    assert rejected.status_code == 422
    assert rejected.json() == {"code": "external_root_violation"}
