from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from ai_canvas.config import Settings
from ai_canvas.infrastructure.artifacts import ArtifactStore
from ai_canvas.infrastructure.db.blob_lifecycle_repository import BlobLifecycleRepository
from ai_canvas.infrastructure.db.models import ArtifactRevisionRow, ArtifactRow, ProjectRow, StoredBlobRow
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


def test_gc_plan_requires_matching_confirmation_then_supports_restore(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    database_path = data_dir / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        session_factory = make_session_factory(engine)
        now = datetime.now(timezone.utc)
        content = b"reclaimable media bytes"
        store = ArtifactStore(data_dir)
        staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
        store.promote(staged)
        project_id = str(uuid4())
        artifact_id = str(uuid4())
        revision_id = str(uuid4())
        with session_factory.begin() as session:
            session.add(ProjectRow(id=project_id, name="GC", created_at=now, updated_at=now))
            session.flush()
            session.add(
                StoredBlobRow(
                    sha256=staged.sha256,
                    byte_size=len(content),
                    media_kind="image",
                    media_type="image/png",
                    metadata_json='{"kind":"image","width":1,"height":1}',
                    created_at=now,
                )
            )
            session.add(
                ArtifactRow(
                    id=artifact_id,
                    origin_project_id=project_id,
                    kind="image",
                    created_at=now,
                )
            )
            session.flush()
            session.add(
                ArtifactRevisionRow(
                    id=revision_id,
                    artifact_id=artifact_id,
                    sequence=1,
                    blob_sha256=staged.sha256,
                    storage_kind="MANAGED_BLOB",
                    media_health="AVAILABLE",
                    external_snapshot_id=None,
                    source_kind="gc_fixture",
                    source_project_id=project_id,
                    source_import_session_id=None,
                    display_filename="discarded.png",
                    discarded_at=now,
                    created_at=now,
                )
            )
        from ai_canvas.application.storage_lifecycle_service import StorageLifecycleService

        service = StorageLifecycleService(
            session_factory,
            settings=Settings(data_dir=data_dir),
            free_space_bytes=lambda: 100 * 1024**3,
        )
        plan = service.create_gc_plan()
        assert len(plan.entries) == 1
        assert plan.entries[0].target_id == revision_id

        quarantine = service.confirm_gc_plan(plan.id, plan.summary_sha256)
        assert len(quarantine) == 1
        assert not store.blob_path(staged.sha256).exists()
        restored = service.restore_quarantine(quarantine[0].id)
        assert restored is True
        assert store.blob_path(staged.sha256).read_bytes() == content
        with session_factory() as session:
            revision = session.get(ArtifactRevisionRow, revision_id)
            assert revision is not None
            assert revision.media_health == "AVAILABLE"

        second_plan = service.create_gc_plan()
        assert second_plan.entries == ()
        with session_factory.begin() as session:
            revision = session.get(ArtifactRevisionRow, revision_id)
            assert revision is not None
            revision.discarded_at = datetime.now(timezone.utc)
            BlobLifecycleRepository().recompute_for_sha(session, staged.sha256)

        rediscarded_plan = service.create_gc_plan()
        second_quarantine = service.confirm_gc_plan(
            rediscarded_plan.id,
            rediscarded_plan.summary_sha256,
        )
        assert len(second_quarantine) == 1
        with session_factory.begin() as session:
            from ai_canvas.infrastructure.db.models import QuarantineEntryRow

            entry = session.get(QuarantineEntryRow, second_quarantine[0].id)
            assert entry is not None
            entry.quarantine_until = datetime.now(timezone.utc) - timedelta(seconds=1)

        assert service.purge_due() == 1
        assert service.restore_quarantine(second_quarantine[0].id) is False
        with session_factory() as session:
            revision = session.get(ArtifactRevisionRow, revision_id)
            assert revision is not None
            assert revision.media_health == "PURGED"
    finally:
        engine.dispose()
