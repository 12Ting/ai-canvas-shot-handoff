import base64
import hashlib
import shutil
from pathlib import Path
from threading import Event, Thread
from unittest.mock import Mock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from ai_canvas.config import Settings
from ai_canvas.contracts.artifact import VideoArtifactMetadata
from ai_canvas.domain.media.probe import MediaProbeResult
from ai_canvas.infrastructure.artifacts import ArtifactStore
from ai_canvas.main import create_app


def _tiny_mp4() -> bytes:
    fixture = Path(__file__).parents[1] / "fixtures" / "tiny_mp4.b64"
    return base64.b64decode(fixture.read_text(encoding="ascii"))


def _authorized_headers(app) -> dict[str, str]:
    token = app.state.session_manager.exchange(app.state.session_manager.bootstrap_token)
    return {"Authorization": f"Bearer {token}", "Origin": "http://testserver"}


def _upload_video(client: TestClient, headers: dict[str, str], project_id: str) -> str:
    content = _tiny_mp4()
    created = client.post(
        f"/api/v1/projects/{project_id}/artifact-imports",
        json={
            "client_import_id": str(uuid4()),
            "filename": "source.mp4",
            "expected_byte_size": len(content),
            "requested_media_kind": "video",
            "declared_content_type": "video/mp4",
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
    return uploaded.json()["revision_id"]


def test_transcode_route_requires_confirmation_then_persists_one_job(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project = client.post("/api/v1/projects", json={"name": "B4 transcode"}, headers=headers)
        assert project.status_code == 201
        revision_id = _upload_video(client, headers, project.json()["id"])
        process_once = Mock(return_value=1)
        app.state.maintenance_job_coordinator.process_once = process_once
        requested = client.post(
            f"/api/v1/artifact-revisions/{revision_id}/renditions",
            json={"profile": "preview_720p"},
            headers=headers,
        )
        assert requested.status_code == 409
        token = requested.json()["confirmation_token"]
        confirmed = client.post(
            f"/api/v1/artifact-revisions/{revision_id}/renditions",
            json={"profile": "preview_720p", "confirmation_token": token},
            headers=headers,
        )
        repeated = client.post(
            f"/api/v1/artifact-revisions/{revision_id}/renditions",
            json={"profile": "preview_720p", "confirmation_token": token},
            headers=headers,
        )

    assert confirmed.status_code == 200
    assert confirmed.json()["reused"] is False
    assert repeated.status_code == 200
    assert repeated.json()["job_id"] == confirmed.json()["job_id"]
    assert repeated.json()["reused"] is True
    process_once.assert_called_once_with()


def test_confirmed_transcode_executes_into_a_ready_managed_rendition(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project = client.post("/api/v1/projects", json={"name": "B4 execute transcode"}, headers=headers)
        assert project.status_code == 201
        revision_id = _upload_video(client, headers, project.json()["id"])
        app.state.maintenance_job_coordinator.process_once = Mock(return_value=1)
        pending = client.post(
            f"/api/v1/artifact-revisions/{revision_id}/renditions",
            json={"profile": "preview_720p"},
            headers=headers,
        )
        confirmed = client.post(
            f"/api/v1/artifact-revisions/{revision_id}/renditions",
            json={"profile": "preview_720p", "confirmation_token": pending.json()["confirmation_token"]},
            headers=headers,
        )
        assert confirmed.status_code == 200

        def fake_ffmpeg(argv: list[str], *, timeout: float) -> None:
            shutil.copyfile(argv[argv.index("-i") + 1], argv[-1])

        app.state.transcode_job_service.ffmpeg_runner = fake_ffmpeg
        app.state.transcode_job_service.ffmpeg_executable = "fake-ffmpeg"
        assert app.state.transcode_job_service.execute(confirmed.json()["job_id"]) is True

        from ai_canvas.infrastructure.db.models import (
            ArtifactRenditionRow,
            MaintenanceJobRow,
            StorageReservationRow,
            StoredBlobRow,
        )

        with app.state.transcode_job_service.session_factory() as session:
            job = session.get(MaintenanceJobRow, confirmed.json()["job_id"])
            rendition = session.get(ArtifactRenditionRow, confirmed.json()["rendition_id"])
            assert job is not None and job.state == "SUCCEEDED"
            assert rendition is not None and rendition.state == "READY"
            assert rendition.blob_sha256 is not None
            blob = session.get(StoredBlobRow, rendition.blob_sha256)
            assert blob is not None and blob.logical_ref_count == 2
            reservation = session.scalar(
                select(StorageReservationRow).where(
                    StorageReservationRow.operation_type == "TRANSCODE"
                )
            )
            assert reservation is not None and reservation.state == "RELEASED"

        assert app.state.transcode_job_service.remove_rendition(
            UUID(confirmed.json()["rendition_id"])
        ) is True
        with app.state.transcode_job_service.session_factory() as session:
            rendition = session.get(ArtifactRenditionRow, confirmed.json()["rendition_id"])
            assert rendition is not None and rendition.removed_at is not None
            blob = session.get(StoredBlobRow, rendition.blob_sha256)
            assert blob is not None and blob.logical_ref_count == 1


def test_remove_rendition_during_transcode_revokes_candidate_without_resurrection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app)
        project = client.post("/api/v1/projects", json={"name": "Remove race"}, headers=headers)
        assert project.status_code == 201
        revision_id = _upload_video(client, headers, project.json()["id"])
        app.state.maintenance_job_coordinator.process_once = Mock(return_value=1)
        pending = client.post(
            f"/api/v1/artifact-revisions/{revision_id}/renditions",
            json={"profile": "preview_720p"},
            headers=headers,
        )
        confirmed = client.post(
            f"/api/v1/artifact-revisions/{revision_id}/renditions",
            json={"profile": "preview_720p", "confirmation_token": pending.json()["confirmation_token"]},
            headers=headers,
        )
        assert confirmed.status_code == 200
        ffmpeg_started = Event()
        release_ffmpeg = Event()
        promote_called = Event()
        revoke_called = Event()
        execution_results: list[bool] = []
        output = b"unique-transcode-output-after-remove"
        output_digest = hashlib.sha256(output).hexdigest()
        original_promote = ArtifactStore.promote_verified
        original_revoke = ArtifactStore.revoke_promotion

        def blocked_ffmpeg(argv: list[str], *, timeout: float) -> None:
            del timeout
            Path(argv[-1]).write_bytes(output)
            ffmpeg_started.set()
            if not release_ffmpeg.wait(5):
                raise AssertionError("transcode remove-race barrier was not released")

        def observed_promote(self, ticket, *args, **kwargs):
            promote_called.set()
            return original_promote(self, ticket, *args, **kwargs)

        def observed_revoke(self, ticket, *args, **kwargs):
            revoke_called.set()
            return original_revoke(self, ticket, *args, **kwargs)

        service = app.state.transcode_job_service
        service.ffmpeg_runner = blocked_ffmpeg
        service.ffmpeg_executable = "fake-ffmpeg"
        monkeypatch.setattr(
            service,
            "_probe_preview",
            lambda _path: MediaProbeResult(
                kind="video",
                media_type="video/mp4",
                metadata=VideoArtifactMetadata(
                    width=16,
                    height=16,
                    duration_seconds=1.0,
                    frames_per_second=24.0,
                ),
            ),
        )
        monkeypatch.setattr(ArtifactStore, "promote_verified", observed_promote)
        monkeypatch.setattr(ArtifactStore, "revoke_promotion", observed_revoke)
        execute_thread = Thread(
            target=lambda: execution_results.append(service.execute(confirmed.json()["job_id"])),
            name="transcode-remove-race",
        )
        execute_thread.start()
        try:
            assert ffmpeg_started.wait(5)
            assert service.remove_rendition(UUID(confirmed.json()["rendition_id"])) is True
            release_ffmpeg.set()
            execute_thread.join(timeout=5)
            assert not execute_thread.is_alive()
            assert execution_results == [False]
            assert not promote_called.is_set()
            assert revoke_called.is_set()
            assert not ArtifactStore(tmp_path).blob_path(output_digest).exists()
            promotion_root = ArtifactStore(tmp_path).promotion_root
            assert not promotion_root.exists() or list(promotion_root.iterdir()) == []

            from ai_canvas.infrastructure.db.models import (
                ArtifactRenditionRow,
                MaintenanceJobRow,
                StoredBlobRow,
            )

            with service.session_factory() as session:
                rendition = session.get(ArtifactRenditionRow, confirmed.json()["rendition_id"])
                job = session.get(MaintenanceJobRow, confirmed.json()["job_id"])
                assert rendition is not None
                assert rendition.state == "REMOVED"
                assert rendition.removed_at is not None
                assert rendition.blob_sha256 is None
                assert job is not None and job.state == "FAILED"
                assert job.error_code == "TRANSCODE_FAILED"
                assert session.get(StoredBlobRow, output_digest) is None
            assert service.execute(confirmed.json()["job_id"]) is False
            app.state.storage_lifecycle_service.reconcile_lifecycle_operations()
            with service.session_factory() as session:
                rendition = session.get(ArtifactRenditionRow, confirmed.json()["rendition_id"])
                assert rendition is not None
                assert rendition.state == "REMOVED"
                assert rendition.removed_at is not None
                assert rendition.blob_sha256 is None
                job = session.get(MaintenanceJobRow, confirmed.json()["job_id"])
                assert job is not None and job.state == "FAILED"
                assert job.error_code == "TRANSCODE_FAILED"
        finally:
            release_ffmpeg.set()
            execute_thread.join(timeout=5)
