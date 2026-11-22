from datetime import datetime, timezone
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import pytest

from ai_canvas.application.storage_lifecycle_service import StorageLifecycleService
from ai_canvas.config import Settings
from ai_canvas.contracts.artifact import VideoArtifactMetadata
from ai_canvas.contracts.media_lifecycle import CreateLargeUploadRequest
from ai_canvas.domain.media.probe import MediaProbeResult
from ai_canvas.infrastructure.db.models import (
    ArtifactRevisionRow,
    LargeUploadSessionRow,
    ProjectRow,
    StorageReservationRow,
    StoredBlobRow,
)
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


def test_large_upload_session_accepts_idempotent_part_and_rejects_conflict(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        session_factory = make_session_factory(engine)
        project_id = uuid4()
        now = datetime.now(timezone.utc)
        with session_factory.begin() as session:
            session.add(
                ProjectRow(
                    id=str(project_id),
                    name="B4 upload",
                    created_at=now,
                    updated_at=now,
                )
            )
        module = import_module("ai_canvas.application.large_upload_service")
        service_type = getattr(module, "LargeUploadService", None)
        conflict_type = getattr(module, "LargeUploadPartConflict", None)
        incomplete_type = getattr(module, "LargeUploadIncomplete", None)
        assert service_type is not None
        assert conflict_type is not None
        assert incomplete_type is not None
        settings = Settings(data_dir=tmp_path / "data")
        storage = StorageLifecycleService(
            session_factory,
            settings=settings,
            free_space_bytes=lambda: 100 * 1024**3,
        )
        service = service_type(session_factory, settings=settings, storage=storage)
        upload = service.create(
            project_id,
            CreateLargeUploadRequest(
                client_upload_id=uuid4(),
                filename="large.mp4",
                expected_byte_size=64 * 1024**2 + 1,
                requested_media_kind="video",
                declared_content_type="video/mp4",
            ),
        )
        assert upload.state == "CREATED"
        assert upload.part_size == 8 * 1024**2

        content = b"a" * upload.part_size
        digest = sha256(content).hexdigest()
        first = service.put_part(
            upload_id=upload.upload_id,
            part_number=0,
            content=content,
            content_range=f"bytes 0-{upload.part_size - 1}/{upload.expected_byte_size}",
            content_sha256=digest,
        )
        replay = service.put_part(
            upload_id=upload.upload_id,
            part_number=0,
            content=content,
            content_range=f"bytes 0-{upload.part_size - 1}/{upload.expected_byte_size}",
            content_sha256=digest,
        )
        assert first == replay
        assert first.received_byte_size == upload.part_size
        with pytest.raises(incomplete_type):
            service.complete(upload.upload_id)
        with pytest.raises(conflict_type):
            service.put_part(
                upload_id=upload.upload_id,
                part_number=0,
                content=b"b" * upload.part_size,
                content_range=f"bytes 0-{upload.part_size - 1}/{upload.expected_byte_size}",
                content_sha256=sha256(b"b" * upload.part_size).hexdigest(),
            )
    finally:
        engine.dispose()


def test_complete_promotes_all_contiguous_parts_to_one_managed_revision(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        session_factory = make_session_factory(engine)
        project_id = uuid4()
        now = datetime.now(timezone.utc)
        with session_factory.begin() as session:
            session.add(
                ProjectRow(
                    id=str(project_id),
                    name="B4 complete",
                    created_at=now,
                    updated_at=now,
                )
            )
        settings = Settings(data_dir=tmp_path / "data")
        storage = StorageLifecycleService(
            session_factory,
            settings=settings,
            free_space_bytes=lambda: 200 * 1024**3,
        )
        module = import_module("ai_canvas.application.large_upload_service")
        service_type = getattr(module, "LargeUploadService", None)
        assert service_type is not None
        service = service_type(
            session_factory,
            settings=settings,
            storage=storage,
            probe=lambda _kind, _path: MediaProbeResult(
                kind="video",
                media_type="video/mp4",
                metadata=VideoArtifactMetadata(
                    width=1920,
                    height=1080,
                    duration_seconds=1.0,
                    frames_per_second=24.0,
                ),
            ),
        )
        expected_size = 64 * 1024**2 + 1
        upload = service.create(
            project_id,
            CreateLargeUploadRequest(
                client_upload_id=uuid4(),
                filename="complete.mp4",
                expected_byte_size=expected_size,
                requested_media_kind="video",
                declared_content_type="video/mp4",
            ),
        )
        for number in range((expected_size + upload.part_size - 1) // upload.part_size):
            start = number * upload.part_size
            end = min(expected_size, start + upload.part_size) - 1
            content = bytes([number]) * (end - start + 1)
            service.put_part(
                upload_id=upload.upload_id,
                part_number=number,
                content=content,
                content_range=f"bytes {start}-{end}/{expected_size}",
                content_sha256=sha256(content).hexdigest(),
            )

        completed = service.complete(upload.upload_id)

        assert completed.state == "COMPLETED"
        assert completed.artifact_revision is not None
        assert completed.artifact_revision.byte_size == expected_size
        assert completed.artifact_revision.source_kind == "large_upload"
        with session_factory() as session:
            revision = session.get(ArtifactRevisionRow, str(completed.artifact_revision.revision_id))
            assert revision is not None
            blob = session.get(StoredBlobRow, revision.blob_sha256)
            assert blob is not None and blob.logical_ref_count == 1
        discarded = service.abort(upload.upload_id)
        assert discarded.state == "COMPLETED"
        with session_factory() as session:
            revision = session.get(ArtifactRevisionRow, str(completed.artifact_revision.revision_id))
            assert revision is not None and revision.discarded_at is not None
            blob = session.get(StoredBlobRow, revision.blob_sha256)
            assert blob is not None and blob.logical_ref_count == 0
        assert service.complete(upload.upload_id) == completed
    finally:
        engine.dispose()


def test_startup_expires_unfinished_large_upload_and_releases_reservation(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        session_factory = make_session_factory(engine)
        project_id = uuid4()
        now = datetime.now(timezone.utc)
        with session_factory.begin() as session:
            session.add(
                ProjectRow(
                    id=str(project_id),
                    name="B4 restart",
                    created_at=now,
                    updated_at=now,
                )
            )
        settings = Settings(data_dir=tmp_path / "data")
        storage = StorageLifecycleService(
            session_factory,
            settings=settings,
            free_space_bytes=lambda: 100 * 1024**3,
        )
        module = import_module("ai_canvas.application.large_upload_service")
        service_type = getattr(module, "LargeUploadService", None)
        assert service_type is not None
        service = service_type(session_factory, settings=settings, storage=storage)
        upload = service.create(
            project_id,
            CreateLargeUploadRequest(
                client_upload_id=uuid4(),
                filename="restart.mp4",
                expected_byte_size=64 * 1024**2 + 1,
                requested_media_kind="video",
            ),
        )

        service.expire_sessions_after_startup()

        with session_factory() as session:
            row = session.get(LargeUploadSessionRow, str(upload.upload_id))
            reservation = session.get(StorageReservationRow, row.reservation_id)
            assert row is not None
            assert row.state == "EXPIRED"
            assert row.failure_code == "UPLOAD_SESSION_EXPIRED"
            assert reservation is not None
            assert reservation.state == "RELEASED"
    finally:
        engine.dispose()
