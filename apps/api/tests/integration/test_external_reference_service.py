import io
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from ai_canvas.application.errors import MediaResourceUnavailable
from ai_canvas.application.external_reference_service import ExternalReferenceService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.config import Settings
from ai_canvas.contracts.artifact import VideoArtifactMetadata
from ai_canvas.domain.media.probe import MediaProbeResult
from ai_canvas.infrastructure.db.models import ArtifactRevisionRow, ProjectRow, StoredBlobRow
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)
from ai_canvas.infrastructure.media.files import SharedFileLease


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


def _create_external_reference_for_stream(
    tmp_path: Path,
    *,
    write_gate: WriteGate,
):
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    project_id = uuid4()
    now = datetime.now(timezone.utc)
    with session_factory.begin() as session:
        session.add(
            ProjectRow(
                id=str(project_id),
                name="external stream race",
                created_at=now,
                updated_at=now,
            )
        )
    root = tmp_path / "granted-root"
    root.mkdir()
    clip = root / "clip.mp4"
    clip.write_bytes(b"immutable external stream bytes")
    service = ExternalReferenceService(
        session_factory,
        settings=Settings(data_dir=tmp_path / "data"),
        write_gate=write_gate,
        probe=_video_probe,
    )
    grant = service.grant_root(root)
    created = service.create_reference(
        project_id=project_id,
        grant_id=grant.id,
        relative_path="clip.mp4",
        requested_media_kind="video",
    )
    return engine, session_factory, service, created


def test_changed_external_file_marks_immutable_old_revision_drifted(tmp_path: Path) -> None:
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
                    name="B4 external reference",
                    created_at=now,
                    updated_at=now,
                )
            )
        root = tmp_path / "granted-root"
        root.mkdir()
        clip = root / "clip.mp4"
        clip.write_bytes(b"first immutable observation")
        from ai_canvas.application.external_reference_service import ExternalReferenceService

        service = ExternalReferenceService(
            session_factory,
            settings=Settings(data_dir=tmp_path / "data"),
            probe=_video_probe,
        )
        grant = service.grant_root(root)
        created = service.create_reference(
            project_id=project_id,
            grant_id=grant.id,
            relative_path="clip.mp4",
            requested_media_kind="video",
        )
        assert created.health == "AVAILABLE"
        assert created.storage_kind == "EXTERNAL_REFERENCE"

        clip.write_bytes(b"changed external bytes")
        verified = service.verify(created.revision_id)

        assert verified.revision_id == created.revision_id
        assert verified.health == "DRIFTED"
        clip.write_bytes(b"reconfirmed external bytes")
        reconfirmed = service.reconfirm(
            revision_id=created.revision_id,
            grant_id=grant.id,
            relative_path="clip.mp4",
        )

        assert reconfirmed.revision_id != created.revision_id
        assert reconfirmed.artifact_id == created.artifact_id
        assert reconfirmed.health == "AVAILABLE"
        copied = service.import_copy(reconfirmed.revision_id)

        assert copied.revision_id != reconfirmed.revision_id
        assert copied.artifact_id == created.artifact_id
        assert copied.storage_kind == "MANAGED_BLOB"
        with session_factory() as session:
            revision = session.get(ArtifactRevisionRow, str(created.revision_id))
            assert revision is not None
            assert revision.storage_kind == "EXTERNAL_REFERENCE"
            assert revision.media_health == "DRIFTED"
            assert revision.blob_sha256 is None
            copied_revision = session.get(ArtifactRevisionRow, str(copied.revision_id))
            assert copied_revision is not None
            assert copied_revision.storage_kind == "MANAGED_BLOB"
            assert copied_revision.blob_sha256 is not None
            copied_blob = session.get(StoredBlobRow, copied_revision.blob_sha256)
            assert copied_blob is not None and copied_blob.logical_ref_count == 1
    finally:
        engine.dispose()


def test_hash_read_failure_closes_handle_and_persists_external_health_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_canvas.application.external_reference_service import ExternalReferenceService
    from ai_canvas.infrastructure.media import files

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
                    name="external read failure",
                    created_at=now,
                    updated_at=now,
                )
            )
        root = tmp_path / "granted-root"
        root.mkdir()
        clip = root / "clip.mp4"
        clip.write_bytes(b"immutable external bytes")
        service = ExternalReferenceService(
            session_factory,
            settings=Settings(data_dir=tmp_path / "data"),
            probe=_video_probe,
        )
        grant = service.grant_root(root)
        created = service.create_reference(
            project_id=project_id,
            grant_id=grant.id,
            relative_path="clip.mp4",
            requested_media_kind="video",
        )

        class FailingReadHandle(io.BytesIO):
            def read(self, _size: int = -1) -> bytes:
                raise OSError("injected hash read failure")

        failing_handle = FailingReadHandle(clip.read_bytes())

        def open_failing_handle(*_args, **_kwargs):
            return clip.absolute(), failing_handle, clip.stat()

        monkeypatch.setattr(files, "_open_validated_handle", open_failing_handle)

        with pytest.raises(MediaResourceUnavailable) as failure:
            service.acquire_stream_lease(created.revision_id)

        assert failure.value.health == "UNAVAILABLE"
        assert failing_handle.closed
        with session_factory() as session:
            revision = session.get(ArtifactRevisionRow, str(created.revision_id))
            assert revision is not None
            assert revision.media_health == "UNAVAILABLE"
    finally:
        engine.dispose()


def test_external_hash_does_not_hold_global_write_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    write_gate = WriteGate()
    engine, session_factory, service, created = _create_external_reference_for_stream(
        tmp_path,
        write_gate=write_gate,
    )
    hash_started = Event()
    release_hash = Event()
    write_completed = Event()
    acquired_leases: list[SharedFileLease] = []
    original_acquire = SharedFileLease.acquire_verified

    def blocked_acquire(cls, *args, **kwargs):
        hash_started.set()
        if not release_hash.wait(timeout=2):
            raise AssertionError("test did not release external hash")
        lease = original_acquire(*args, **kwargs)
        acquired_leases.append(lease)
        return lease

    monkeypatch.setattr(SharedFileLease, "acquire_verified", classmethod(blocked_acquire))

    def unrelated_write() -> None:
        with write_gate, session_factory.begin() as session:
            now = datetime.now(timezone.utc)
            session.add(
                ProjectRow(
                    id=str(uuid4()),
                    name="unrelated write",
                    created_at=now,
                    updated_at=now,
                )
            )
        write_completed.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            acquire_future = executor.submit(service.acquire_stream_lease, created.revision_id)
            assert hash_started.wait(timeout=1)
            write_future = executor.submit(unrelated_write)
            try:
                assert write_completed.wait(timeout=2)
            finally:
                release_hash.set()
            source = acquire_future.result(timeout=2)
            write_future.result(timeout=2)
            source.lease.close()
    finally:
        release_hash.set()
        for lease in acquired_leases:
            lease.close()
        engine.dispose()


def test_external_discard_during_hash_closes_stale_lease_and_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    write_gate = WriteGate()
    engine, session_factory, service, created = _create_external_reference_for_stream(
        tmp_path,
        write_gate=write_gate,
    )
    hash_started = Event()
    release_hash = Event()
    mutation_completed = Event()
    acquired_leases: list[SharedFileLease] = []
    original_acquire = SharedFileLease.acquire_verified

    def blocked_acquire(cls, *args, **kwargs):
        hash_started.set()
        if not release_hash.wait(timeout=2):
            raise AssertionError("test did not release external hash")
        lease = original_acquire(*args, **kwargs)
        acquired_leases.append(lease)
        return lease

    monkeypatch.setattr(SharedFileLease, "acquire_verified", classmethod(blocked_acquire))

    def discard_revision() -> None:
        with write_gate, session_factory.begin() as session:
            revision = session.get(ArtifactRevisionRow, str(created.revision_id))
            assert revision is not None
            revision.discarded_at = datetime.now(timezone.utc)
        mutation_completed.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            acquire_future = executor.submit(service.acquire_stream_lease, created.revision_id)
            assert hash_started.wait(timeout=1)
            mutation_future = executor.submit(discard_revision)
            try:
                assert mutation_completed.wait(timeout=0.25)
            finally:
                release_hash.set()
            mutation_future.result(timeout=2)
            with pytest.raises(MediaResourceUnavailable):
                acquire_future.result(timeout=2)
        assert len(acquired_leases) == 1
        assert acquired_leases[0]._source.closed
    finally:
        release_hash.set()
        for lease in acquired_leases:
            lease.close()
        engine.dispose()
