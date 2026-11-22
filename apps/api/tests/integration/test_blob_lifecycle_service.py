from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
from threading import Event, Thread, current_thread
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from ai_canvas.application.artifact_service import ArtifactService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.config import Settings
from ai_canvas.contracts.artifact import CreateArtifactImportRequest
from ai_canvas.infrastructure.artifacts import ArtifactStore
from ai_canvas.infrastructure.db.models import (
    ArtifactRenditionRow,
    ArtifactRevisionRow,
    ArtifactRow,
    ImportSessionRow,
    MaintenanceJobRow,
    ProjectRow,
    QuarantineEntryRow,
    QuarantineTargetRow,
    StoredBlobRow,
)
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)
from ai_canvas.main import create_app


NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)


def _tiny_png() -> bytes:
    fixture = Path(__file__).parents[1] / "fixtures" / "tiny_png.b64"
    return base64.b64decode(fixture.read_text(encoding="ascii"))


async def _chunks(content: bytes):
    yield content


def _blob_repository_type():
    module = import_module("ai_canvas.infrastructure.db.blob_lifecycle_repository")
    repository_type = getattr(module, "BlobLifecycleRepository", None)
    assert repository_type is not None
    return repository_type


def _seed_group(
    tmp_path: Path,
    *,
    discarded: tuple[bool, bool],
    with_import_sessions: bool = False,
    content: bytes | None = None,
    media_kind: str = "image",
    media_type: str = "image/png",
    metadata_json: str = '{"kind":"image","width":1,"height":1}',
):
    data_dir = tmp_path / "data"
    database_path = data_dir / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    store = ArtifactStore(data_dir)
    content = content or b"one physical blob shared by two logical revisions"
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    store.promote(staged)
    project_id = str(uuid4())
    revision_ids = (str(uuid4()), str(uuid4()))
    import_ids = (str(uuid4()), str(uuid4()))
    with session_factory.begin() as session:
        session.add(ProjectRow(id=project_id, name="blob group", created_at=NOW, updated_at=NOW))
        session.add(
            StoredBlobRow(
                sha256=staged.sha256,
                byte_size=len(content),
                media_kind=media_kind,
                media_type=media_type,
                metadata_json=metadata_json,
                created_at=NOW,
            )
        )
        session.flush()
        for offset, revision_id in enumerate(revision_ids):
            artifact_id = str(uuid4())
            import_id = import_ids[offset]
            session.add(
                ArtifactRow(
                    id=artifact_id,
                    origin_project_id=project_id,
                    kind=media_kind,
                    created_at=NOW,
                )
            )
            if with_import_sessions:
                session.add(
                    ImportSessionRow(
                        id=import_id,
                        source_project_id=project_id,
                        client_import_id=str(uuid4()),
                        replace_revision_id=None,
                        requested_media_kind=media_kind,
                        original_filename=f"shared-{offset}.png",
                        declared_content_type="image/png",
                        expected_byte_size=len(content),
                        state="COMPLETED",
                        received_byte_size=len(content),
                        computed_sha256=staged.sha256,
                        failure_code=None,
                        discarded_at=NOW if discarded[offset] else None,
                        discard_cleanup_pending=discarded[offset],
                        created_at=NOW,
                        updated_at=NOW,
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
                    source_kind="import" if with_import_sessions else "fixture",
                    source_project_id=project_id,
                    source_import_session_id=import_id if with_import_sessions else None,
                    display_filename=f"shared-{offset}.png",
                    discarded_at=NOW if discarded[offset] else None,
                    created_at=NOW,
                )
            )
        session.flush()
        _blob_repository_type()().recompute_all(session)
    return engine, session_factory, data_dir, staged.sha256, content, revision_ids, import_ids


def _service(session_factory, data_dir: Path, **kwargs):
    module = import_module("ai_canvas.application.storage_lifecycle_service")
    return module.StorageLifecycleService(
        session_factory,
        settings=Settings(
            data_dir=data_dir,
            media_physical_free_floor_bytes=kwargs.pop("physical_floor", 0),
            workspace_quota_bytes=kwargs.pop("quota", 1024**3),
        ),
        free_space_bytes=kwargs.pop("free_space_bytes", lambda: 1024**3),
        now=kwargs.pop("now", lambda: NOW),
        **kwargs,
    )


def test_shared_sha_counts_once_physically_and_twice_logically(tmp_path: Path) -> None:
    engine, session_factory, data_dir, digest, content, _revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(False, False),
    )
    try:
        with session_factory() as session:
            blob = session.get(StoredBlobRow, digest)
            assert blob is not None
            assert blob.physical_bytes == len(content)
            assert blob.logical_ref_count == 2
            assert blob.state == "ACTIVE"
        summary = _service(session_factory, data_dir).storage_summary()
        assert summary.managed_blob_bytes == len(content)
        assert summary.quarantine_bytes == 0
    finally:
        engine.dispose()


def test_recompute_all_uses_constant_select_count_for_many_blobs(tmp_path: Path) -> None:
    engine, session_factory, _data_dir, _digest, _content, _revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(False, False),
    )
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    try:
        with session_factory.begin() as session:
            for offset in range(24):
                digest = f"{offset + 1:064x}"
                session.add(
                    StoredBlobRow(
                        sha256=digest,
                        byte_size=1,
                        media_kind="image",
                        media_type="image/png",
                        metadata_json='{"kind":"image","width":1,"height":1}',
                        created_at=NOW,
                    )
                )
        event.listen(engine, "before_cursor_execute", capture)
        try:
            with session_factory.begin() as session:
                counts = _blob_repository_type()().recompute_all(session)
        finally:
            event.remove(engine, "before_cursor_execute", capture)

        assert len(counts) == 25
        assert len(statements) <= 2
    finally:
        engine.dispose()


def test_gc_plan_uses_constant_select_count_for_many_zero_ref_blobs(tmp_path: Path) -> None:
    engine, session_factory, data_dir, _digest, _content, _revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(False, False),
    )
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    try:
        with session_factory.begin() as session:
            for offset in range(24):
                session.add(
                    StoredBlobRow(
                        sha256=f"{offset + 1:064x}",
                        byte_size=1,
                        media_kind="image",
                        media_type="image/png",
                        metadata_json='{"kind":"image","width":1,"height":1}',
                        created_at=NOW,
                    )
                )

        event.listen(engine, "before_cursor_execute", capture)
        try:
            plan = _service(session_factory, data_dir).create_gc_plan()
        finally:
            event.remove(engine, "before_cursor_execute", capture)

        assert plan.entries == ()
        assert len(statements) <= 5
    finally:
        engine.dispose()


def test_storage_summary_uses_constant_select_count_for_many_owned_blobs(tmp_path: Path) -> None:
    engine, session_factory, data_dir, _digest, _content, _revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(False, False),
    )
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    try:
        with session_factory.begin() as session:
            for offset in range(24):
                session.add(
                    StoredBlobRow(
                        sha256=f"{offset + 1:064x}",
                        byte_size=1,
                        media_kind="image",
                        media_type="image/png",
                        metadata_json='{"kind":"image","width":1,"height":1}',
                        created_at=NOW,
                    )
                )

        event.listen(engine, "before_cursor_execute", capture)
        try:
            _service(session_factory, data_dir).storage_summary()
        finally:
            event.remove(engine, "before_cursor_execute", capture)

        assert len(statements) <= 6
    finally:
        engine.dispose()


def test_gc_groups_two_discarded_targets_and_live_shared_ref_blocks(tmp_path: Path) -> None:
    engine, session_factory, data_dir, digest, _content, revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, False),
    )
    try:
        service = _service(session_factory, data_dir)
        assert service.create_gc_plan().entries == ()
        with session_factory.begin() as session:
            second = session.get(ArtifactRevisionRow, revision_ids[1])
            assert second is not None
            second.discarded_at = NOW
            _blob_repository_type()().recompute_for_sha(session, digest)

        plan = service.create_gc_plan()
        assert len(plan.entries) == 1
        assert plan.entries[0].blob_sha256 == digest
        assert set(plan.entries[0].target_ids) == set(revision_ids)
        assert plan.entries[0].physical_reclaim_bytes > 0
    finally:
        engine.dispose()


def test_gc_confirmation_freezes_removed_rendition_with_revision_group(tmp_path: Path) -> None:
    engine, session_factory, data_dir, digest, content, revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
    )
    rendition_id = str(uuid4())
    try:
        with session_factory.begin() as session:
            session.add(
                ArtifactRenditionRow(
                    id=rendition_id,
                    source_revision_id=revision_ids[0],
                    profile="proxy_720p",
                    blob_sha256=digest,
                    byte_size=len(content),
                    media_type="image/png",
                    metadata_json='{"kind":"image","width":1,"height":1}',
                    state="REMOVED",
                    job_id=None,
                    created_at=NOW,
                    updated_at=NOW,
                    removed_at=NOW,
                )
            )
            _blob_repository_type()().recompute_for_sha(session, digest)

        service = _service(session_factory, data_dir)
        plan = service.create_gc_plan()
        assert len(plan.entries) == 1
        assert set(plan.entries[0].target_ids) == {*revision_ids, rendition_id}
        quarantines = service.confirm_gc_plan(plan.id, plan.summary_sha256)
        assert len(quarantines) == 1
        with session_factory() as session:
            rendition = session.get(ArtifactRenditionRow, rendition_id)
            assert rendition is not None and rendition.state == "QUARANTINED"
    finally:
        engine.dispose()


def test_gc_confirmation_holds_write_gate_from_prepared_intent_through_move(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, session_factory, data_dir, digest, _content, revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
    )
    gate = WriteGate()
    service = _service(session_factory, data_dir, write_gate=gate)
    entered_execute = Event()
    allow_execute = Event()
    writer_attempted = Event()
    writer_done = Event()
    confirm_results: list[object] = []
    confirm_errors: list[BaseException] = []
    writer_revision_ids: list[str] = []
    writer_errors: list[BaseException] = []
    original_execute = service._execute_quarantine_intent

    def pause_before_execute(quarantine_id: str, *args, **kwargs) -> bool:
        entered_execute.set()
        if not allow_execute.wait(5):
            raise AssertionError("quarantine execute barrier was not released")
        return original_execute(quarantine_id, *args, **kwargs)

    monkeypatch.setattr(service, "_execute_quarantine_intent", pause_before_execute)
    plan = service.create_gc_plan()

    def confirm() -> None:
        try:
            confirm_results.extend(service.confirm_gc_plan(plan.id, plan.summary_sha256))
        except BaseException as error:
            confirm_errors.append(error)

    def commit_same_sha_revision() -> None:
        writer_attempted.set()
        try:
            with gate, session_factory.begin() as session:
                blob = session.get(StoredBlobRow, digest)
                _blob_repository_type()().reactivate_verified_purged_blob(
                    session,
                    digest,
                    verified_new_bytes=True,
                )
                source = session.get(ArtifactRevisionRow, revision_ids[0])
                assert blob is not None and source is not None
                artifact_id = str(uuid4())
                revision_id = str(uuid4())
                session.add(
                    ArtifactRow(
                        id=artifact_id,
                        origin_project_id=source.source_project_id,
                        kind="image",
                        created_at=NOW,
                    )
                )
                session.flush()
                session.add(
                    ArtifactRevisionRow(
                        id=revision_id,
                        artifact_id=artifact_id,
                        sequence=1,
                        blob_sha256=digest,
                        storage_kind="MANAGED_BLOB",
                        media_health="AVAILABLE",
                        external_snapshot_id=None,
                        source_kind="concurrent_import",
                        source_project_id=source.source_project_id,
                        source_import_session_id=None,
                        display_filename="concurrent.png",
                        discarded_at=None,
                        created_at=NOW,
                    )
                )
                session.flush()
                _blob_repository_type()().recompute_for_sha(session, digest)
                writer_revision_ids.append(revision_id)
        except BaseException as error:
            writer_errors.append(error)
        finally:
            writer_done.set()

    confirm_thread = Thread(target=confirm, name="gc-confirm")
    writer_thread = Thread(target=commit_same_sha_revision, name="same-sha-import")
    try:
        confirm_thread.start()
        assert entered_execute.wait(5)
        writer_thread.start()
        assert writer_attempted.wait(5)
        writer_completed_before_execute = writer_done.wait(0.25)
        allow_execute.set()
        confirm_thread.join(timeout=5)
        writer_thread.join(timeout=5)

        assert not confirm_thread.is_alive()
        assert not writer_thread.is_alive()
        assert writer_completed_before_execute is False
        assert not confirm_errors
        assert len(confirm_results) == 1
        assert not writer_revision_ids
        assert len(writer_errors) == 1
        assert getattr(writer_errors[0], "code", None) == "BLOB_QUARANTINED"
        with session_factory() as session:
            blob = session.get(StoredBlobRow, digest)
            live = list(
                session.scalars(
                    select(ArtifactRevisionRow).where(
                        ArtifactRevisionRow.blob_sha256 == digest,
                        ArtifactRevisionRow.discarded_at.is_(None),
                    )
                )
            )
            assert blob is not None and (blob.state, blob.logical_ref_count) == (
                "QUARANTINED",
                0,
            )
            assert live == []
    finally:
        allow_execute.set()
        confirm_thread.join(timeout=5)
        if writer_thread.ident is not None:
            writer_thread.join(timeout=5)
        engine.dispose()


def test_real_artifact_writer_cannot_promote_during_quarantine_move_finalize_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    content = _tiny_png()
    engine, session_factory, data_dir, digest, _content, _revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
        content=content,
    )
    gate = WriteGate()
    gc_moved = Event()
    allow_gc_finalize = Event()
    writer_before_promote = Event()
    allow_writer = Event()
    writer_promote_called = Event()
    writer_gate_attempted = Event()
    observe_writer_gate = Event()
    writer_revoke_called = Event()
    writer_done = Event()
    gc_results: list[object] = []
    gc_errors: list[BaseException] = []
    writer_errors: list[BaseException] = []

    def gc_failpoint(name: str) -> None:
        if name == "after_quarantine_move_before_finalize":
            gc_moved.set()
            if not allow_gc_finalize.wait(15):
                raise AssertionError("GC finalize barrier was not released")

    def writer_failpoint(name: str) -> None:
        if name == "before_promote":
            writer_before_promote.set()
            if not allow_writer.wait(5):
                raise AssertionError("artifact writer barrier was not released")

    original_promote = ArtifactStore.promote_verified
    original_revoke = ArtifactStore.revoke_promotion
    original_gate_enter = WriteGate.__enter__

    def observed_promote(self, promotion, *args, **kwargs):
        writer_promote_called.set()
        return original_promote(self, promotion, *args, **kwargs)

    def observed_revoke(self, promotion, *args, **kwargs):
        writer_revoke_called.set()
        return original_revoke(self, promotion, *args, **kwargs)

    def observed_gate_enter(self):
        # upload_content runs its synchronous publication in an asyncio worker.
        # Once armed, GC already owns the gate, so the next enter is the writer.
        if self is gate and observe_writer_gate.is_set():
            writer_gate_attempted.set()
        return original_gate_enter(self)

    monkeypatch.setattr(ArtifactStore, "promote_verified", observed_promote)
    monkeypatch.setattr(ArtifactStore, "revoke_promotion", observed_revoke)
    monkeypatch.setattr(WriteGate, "__enter__", observed_gate_enter)
    lifecycle = _service(
        session_factory,
        data_dir,
        write_gate=gate,
        failpoint=gc_failpoint,
    )
    artifacts = ArtifactService(
        session_factory,
        settings=Settings(data_dir=data_dir),
        write_gate=gate,
        failpoint=writer_failpoint,
    )
    plan = lifecycle.create_gc_plan()
    with session_factory() as session:
        project_id = UUID(session.scalar(select(ProjectRow.id)))
    import_session = artifacts.create_import(
        project_id,
        CreateArtifactImportRequest(
            client_import_id=uuid4(),
            filename="same-sha.png",
            expected_byte_size=len(content),
            requested_media_kind="image",
            declared_content_type="image/png",
        ),
    )

    def upload_same_sha() -> None:
        try:
            asyncio.run(
                artifacts.upload_content(
                    project_id,
                    import_session.import_id,
                    _chunks(content),
                    content_length=len(content),
                )
            )
        except BaseException as error:
            writer_errors.append(error)
        finally:
            writer_done.set()

    def confirm_gc() -> None:
        try:
            gc_results.extend(lifecycle.confirm_gc_plan(plan.id, plan.summary_sha256))
        except BaseException as error:
            gc_errors.append(error)

    writer_thread = Thread(target=upload_same_sha, name="real-artifact-writer")
    gc_thread = Thread(target=confirm_gc, name="gc-move-finalize")
    try:
        writer_thread.start()
        assert writer_before_promote.wait(5)
        gc_thread.start()
        assert gc_moved.wait(5)
        observe_writer_gate.set()
        allow_writer.set()
        assert writer_gate_attempted.wait(5)
        assert not writer_promote_called.is_set()
        replacement = b"new public staging path not owned by the prepared ticket"
        rebuilt_staging_path = ArtifactStore(data_dir).staging_path(import_session.import_id)
        rebuilt_staging_path.write_bytes(replacement)
        allow_gc_finalize.set()
        gc_thread.join(timeout=5)
        writer_thread.join(timeout=5)

        assert not gc_thread.is_alive()
        assert not writer_thread.is_alive()
        assert writer_revoke_called.is_set()
        assert not gc_errors
        assert len(gc_results) == 1
        assert len(writer_errors) == 1
        assert getattr(writer_errors[0], "code", None) == "BLOB_QUARANTINED"
        quarantine_path = Path(gc_results[0].quarantine_path)
        assert quarantine_path.read_bytes() == content
        assert rebuilt_staging_path.read_bytes() == replacement
        promotion_root = ArtifactStore(data_dir).promotion_root
        assert not promotion_root.exists() or list(promotion_root.iterdir()) == []
        assert not ArtifactStore(data_dir).blob_path(digest).exists()
        report = lifecycle.reconcile_lifecycle_operations()
        assert report.recovery_required_ids == ()
    finally:
        allow_writer.set()
        allow_gc_finalize.set()
        if gc_thread.ident is not None:
            gc_thread.join(timeout=5)
        if writer_thread.ident is not None:
            writer_thread.join(timeout=5)
        engine.dispose()


def _exercise_real_writer_during_gc_move_finalize(
    *,
    session_factory,
    data_dir: Path,
    digest: str,
    content: bytes,
    gate: WriteGate,
    writer_ready: Event,
    allow_writer: Event,
    writer,
    monkeypatch,
):
    gc_moved = Event()
    allow_gc_finalize = Event()
    promote_called = Event()
    writer_gate_attempted = Event()
    observe_writer_gate = Event()
    revoke_called = Event()
    writer_values: list[object] = []
    writer_errors: list[BaseException] = []
    gc_results: list[object] = []
    gc_errors: list[BaseException] = []

    def gc_failpoint(name: str) -> None:
        if name == "after_quarantine_move_before_finalize":
            gc_moved.set()
            if not allow_gc_finalize.wait(15):
                raise AssertionError("GC finalize barrier was not released")

    original_promote = ArtifactStore.promote_verified
    original_revoke = ArtifactStore.revoke_promotion
    original_gate_enter = WriteGate.__enter__

    def observed_promote(self, promotion, *args, **kwargs):
        promote_called.set()
        return original_promote(self, promotion, *args, **kwargs)

    def observed_revoke(self, promotion, *args, **kwargs):
        revoke_called.set()
        return original_revoke(self, promotion, *args, **kwargs)

    def observed_gate_enter(self):
        if (
            self is gate
            and observe_writer_gate.is_set()
            and current_thread().name == "real-ingest-writer"
        ):
            writer_gate_attempted.set()
        return original_gate_enter(self)

    monkeypatch.setattr(ArtifactStore, "promote_verified", observed_promote)
    monkeypatch.setattr(ArtifactStore, "revoke_promotion", observed_revoke)
    monkeypatch.setattr(WriteGate, "__enter__", observed_gate_enter)
    lifecycle = _service(
        session_factory,
        data_dir,
        write_gate=gate,
        failpoint=gc_failpoint,
    )
    plan = lifecycle.create_gc_plan()

    def run_writer() -> None:
        try:
            writer_values.append(writer())
        except BaseException as error:
            writer_errors.append(error)

    def confirm_gc() -> None:
        try:
            gc_results.extend(lifecycle.confirm_gc_plan(plan.id, plan.summary_sha256))
        except BaseException as error:
            gc_errors.append(error)

    writer_thread = Thread(target=run_writer, name="real-ingest-writer")
    gc_thread = Thread(target=confirm_gc, name="gc-move-finalize")
    try:
        writer_thread.start()
        assert writer_ready.wait(5)
        gc_thread.start()
        assert gc_moved.wait(5)
        observe_writer_gate.set()
        allow_writer.set()
        assert writer_gate_attempted.wait(5)
        assert not promote_called.is_set()
        allow_gc_finalize.set()
        gc_thread.join(timeout=5)
        writer_thread.join(timeout=5)

        assert not gc_thread.is_alive()
        assert not writer_thread.is_alive()
        assert not promote_called.is_set()
        assert revoke_called.is_set()
        assert not gc_errors
        assert len(gc_results) == 1
        quarantine_path = Path(gc_results[0].quarantine_path)
        assert quarantine_path.read_bytes() == content
        promotion_root = ArtifactStore(data_dir).promotion_root
        assert not promotion_root.exists() or list(promotion_root.iterdir()) == []
        assert not ArtifactStore(data_dir).blob_path(digest).exists()
        report = lifecycle.reconcile_lifecycle_operations()
        assert report.recovery_required_ids == ()
        return writer_values, writer_errors
    finally:
        allow_writer.set()
        allow_gc_finalize.set()
        if gc_thread.ident is not None:
            gc_thread.join(timeout=5)
        if writer_thread.ident is not None:
            writer_thread.join(timeout=5)


def test_real_large_upload_writer_cannot_promote_during_gc_finalize(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from hashlib import sha256

    from ai_canvas.application import large_upload_service as large_upload_module
    from ai_canvas.application.large_upload_service import LargeUploadService
    from ai_canvas.contracts.artifact import ImageArtifactMetadata
    from ai_canvas.contracts.media_lifecycle import CreateLargeUploadRequest
    from ai_canvas.domain.media.probe import MediaProbeResult

    content = b"large upload bytes sharing the quarantined digest"
    engine, session_factory, data_dir, digest, _content, _revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
        content=content,
    )
    gate = WriteGate()
    writer_ready = Event()
    allow_writer = Event()

    def blocked_probe(_kind, _path):
        writer_ready.set()
        if not allow_writer.wait(5):
            raise AssertionError("large upload writer barrier was not released")
        return MediaProbeResult(
            kind="image",
            media_type="image/png",
            metadata=ImageArtifactMetadata(width=1, height=1),
        )

    monkeypatch.setattr(large_upload_module, "B1_MEDIA_MAX_BYTES", 0)
    settings = Settings(data_dir=data_dir)
    storage = _service(session_factory, data_dir, write_gate=gate)
    service = LargeUploadService(
        session_factory,
        settings=settings,
        storage=storage,
        write_gate=gate,
        probe=blocked_probe,
    )
    with session_factory() as session:
        project_id = UUID(session.scalar(select(ProjectRow.id)))
    upload = service.create(
        project_id,
        CreateLargeUploadRequest(
            client_upload_id=uuid4(),
            filename="same-sha.png",
            expected_byte_size=len(content),
            requested_media_kind="image",
            declared_content_type="image/png",
        ),
    )
    service.put_part(
        upload_id=upload.upload_id,
        part_number=0,
        content=content,
        content_range=f"bytes 0-{len(content) - 1}/{len(content)}",
        content_sha256=sha256(content).hexdigest(),
    )
    try:
        values, errors = _exercise_real_writer_during_gc_move_finalize(
            session_factory=session_factory,
            data_dir=data_dir,
            digest=digest,
            content=content,
            gate=gate,
            writer_ready=writer_ready,
            allow_writer=allow_writer,
            writer=lambda: service.complete(upload.upload_id),
            monkeypatch=monkeypatch,
        )
        assert values == []
        assert len(errors) == 1
        assert getattr(errors[0], "code", None) == "BLOB_QUARANTINED"
    finally:
        engine.dispose()


def test_real_external_copy_writer_cannot_promote_during_gc_finalize(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_canvas.application.external_reference_service import ExternalReferenceService
    from ai_canvas.contracts.artifact import ImageArtifactMetadata
    from ai_canvas.domain.media.probe import MediaProbeResult

    content = b"external copy bytes sharing the quarantined digest"
    engine, session_factory, data_dir, digest, _content, _revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
        content=content,
    )
    gate = WriteGate()
    writer_ready = Event()
    allow_writer = Event()

    def image_probe(_path, _kind, _root):
        return MediaProbeResult(
            kind="image",
            media_type="image/png",
            metadata=ImageArtifactMetadata(width=1, height=1),
        )

    root = tmp_path / "external-root"
    root.mkdir()
    source = root / "same-sha.png"
    source.write_bytes(content)
    service = ExternalReferenceService(
        session_factory,
        settings=Settings(data_dir=data_dir),
        write_gate=gate,
        probe=image_probe,
    )
    with session_factory() as session:
        project_id = UUID(session.scalar(select(ProjectRow.id)))
    grant = service.grant_root(root)
    external = service.create_reference(
        project_id=project_id,
        grant_id=grant.id,
        relative_path=source.name,
        requested_media_kind="image",
    )
    original_stream_source = service.stream_source
    stream_calls = 0

    def blocked_second_stream_source(*args, **kwargs):
        nonlocal stream_calls
        result = original_stream_source(*args, **kwargs)
        stream_calls += 1
        if stream_calls == 2:
            writer_ready.set()
            if not allow_writer.wait(5):
                raise AssertionError("external copy writer barrier was not released")
        return result

    monkeypatch.setattr(service, "stream_source", blocked_second_stream_source)
    try:
        values, errors = _exercise_real_writer_during_gc_move_finalize(
            session_factory=session_factory,
            data_dir=data_dir,
            digest=digest,
            content=content,
            gate=gate,
            writer_ready=writer_ready,
            allow_writer=allow_writer,
            writer=lambda: service.import_copy(external.revision_id),
            monkeypatch=monkeypatch,
        )
        assert values == []
        assert len(errors) == 1
        assert getattr(errors[0], "code", None) == "BLOB_QUARANTINED"
    finally:
        engine.dispose()


def test_real_transcode_writer_cannot_promote_during_gc_finalize(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_canvas.application.transcode_job_service import (
        TranscodeConfirmationRequired,
        TranscodeJobService,
    )
    from ai_canvas.contracts.artifact import VideoArtifactMetadata
    from ai_canvas.domain.media.probe import MediaProbeResult

    content = b"transcode output bytes sharing the quarantined digest"
    metadata_json = VideoArtifactMetadata(
        width=16,
        height=16,
        duration_seconds=1.0,
        frames_per_second=24.0,
    ).model_dump_json()
    engine, session_factory, data_dir, digest, _content, _revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
        content=content,
        media_kind="video",
        media_type="video/mp4",
        metadata_json=metadata_json,
    )
    gate = WriteGate()
    writer_ready = Event()
    allow_writer = Event()
    source_content = b"different active source video bytes"
    source_store = ArtifactStore(data_dir)
    source_staged = source_store.stage_chunks(
        uuid4(),
        [source_content],
        declared_size=len(source_content),
    )
    source_store.promote(source_staged)
    with session_factory.begin() as session:
        project_id = session.scalar(select(ProjectRow.id))
        source_artifact = ArtifactRow(
            id=str(uuid4()),
            origin_project_id=project_id,
            kind="video",
            created_at=NOW,
        )
        session.add(source_artifact)
        session.add(
            StoredBlobRow(
                sha256=source_staged.sha256,
                byte_size=len(source_content),
                media_kind="video",
                media_type="video/mp4",
                metadata_json=metadata_json,
                created_at=NOW,
            )
        )
        session.flush()
        source_revision = ArtifactRevisionRow(
            id=str(uuid4()),
            artifact_id=source_artifact.id,
            sequence=1,
            blob_sha256=source_staged.sha256,
            storage_kind="MANAGED_BLOB",
            media_health="AVAILABLE",
            external_snapshot_id=None,
            source_kind="transcode_source_fixture",
            source_project_id=project_id,
            source_import_session_id=None,
            display_filename="source.mp4",
            discarded_at=None,
            created_at=NOW,
        )
        session.add(source_revision)
        session.flush()
        _blob_repository_type()().recompute_for_sha(session, source_staged.sha256)
        source_revision_id = UUID(source_revision.id)

    service = TranscodeJobService(
        session_factory,
        settings=Settings(data_dir=data_dir),
        write_gate=gate,
    )
    with pytest.raises(TranscodeConfirmationRequired) as confirmation:
        service.request(source_revision_id, profile="preview_720p", confirmation_token=None)
    job = service.request(
        source_revision_id,
        profile="preview_720p",
        confirmation_token=confirmation.value.confirmation_token,
    )

    def blocked_ffmpeg(argv: list[str], *, timeout: float) -> None:
        del timeout
        writer_ready.set()
        if not allow_writer.wait(5):
            raise AssertionError("transcode writer barrier was not released")
        Path(argv[-1]).write_bytes(content)

    service.ffmpeg_executable = "fake-ffmpeg"
    service.ffmpeg_runner = blocked_ffmpeg
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
    try:
        values, errors = _exercise_real_writer_during_gc_move_finalize(
            session_factory=session_factory,
            data_dir=data_dir,
            digest=digest,
            content=content,
            gate=gate,
            writer_ready=writer_ready,
            allow_writer=allow_writer,
            writer=lambda: service.execute(job.job_id),
            monkeypatch=monkeypatch,
        )
        assert errors == []
        assert values == [False]
        with session_factory() as session:
            failed_job = session.get(MaintenanceJobRow, str(job.job_id))
            assert failed_job is not None
            assert failed_job.error_code == "BLOB_QUARANTINED"
    finally:
        engine.dispose()


def test_prepared_quarantine_rechecks_late_live_ref_before_move(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, session_factory, data_dir, digest, content, revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
    )
    try:
        service = _service(session_factory, data_dir)
        original_execute = service._execute_quarantine_intent
        monkeypatch.setattr(service, "_execute_quarantine_intent", lambda *_args, **_kwargs: False)
        plan = service.create_gc_plan()
        assert service.confirm_gc_plan(plan.id, plan.summary_sha256) == ()
        monkeypatch.setattr(service, "_execute_quarantine_intent", original_execute)

        with session_factory.begin() as session:
            source = session.get(ArtifactRevisionRow, revision_ids[0])
            assert source is not None
            artifact_id = str(uuid4())
            session.add(
                ArtifactRow(
                    id=artifact_id,
                    origin_project_id=source.source_project_id,
                    kind="image",
                    created_at=NOW,
                )
            )
            session.flush()
            session.add(
                ArtifactRevisionRow(
                    id=str(uuid4()),
                    artifact_id=artifact_id,
                    sequence=1,
                    blob_sha256=digest,
                    storage_kind="MANAGED_BLOB",
                    media_health="AVAILABLE",
                    external_snapshot_id=None,
                    source_kind="late_fixture",
                    source_project_id=source.source_project_id,
                    source_import_session_id=None,
                    display_filename="late.png",
                    discarded_at=None,
                    created_at=NOW,
                )
            )
            session.flush()
            _blob_repository_type()().recompute_for_sha(session, digest)

        report = service.reconcile_lifecycle_operations()
        assert report.recovered_operations == 0
        cas_path = ArtifactStore(data_dir).blob_path(digest)
        assert cas_path.read_bytes() == content
        with session_factory() as session:
            blob = session.get(StoredBlobRow, digest)
            audit = session.scalar(select(QuarantineEntryRow))
            assert blob is not None and (blob.state, blob.logical_ref_count) == ("ACTIVE", 1)
            assert audit is not None and audit.operation_state == "PREPARED_QUARANTINE"
            assert audit.operation_error is not None and "reference" in audit.operation_error
    finally:
        engine.dispose()


def test_quarantine_changes_category_without_changing_total(tmp_path: Path) -> None:
    engine, session_factory, data_dir, digest, content, _revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
    )
    try:
        service = _service(session_factory, data_dir)
        before = service.storage_summary()
        plan = service.create_gc_plan()
        quarantines = service.confirm_gc_plan(plan.id, plan.summary_sha256)
        assert len(quarantines) == 1
        after = service.storage_summary()

        before_used = before.managed_blob_bytes + before.quarantine_bytes + before.staging_bytes
        after_used = after.managed_blob_bytes + after.quarantine_bytes + after.staging_bytes
        assert before_used == after_used == len(content)
        assert after.managed_blob_bytes == 0
        assert after.quarantine_bytes == len(content)
        with session_factory() as session:
            blob = session.get(StoredBlobRow, digest)
            assert blob is not None
            assert (blob.state, blob.physical_bytes, blob.logical_ref_count) == (
                "QUARANTINED",
                len(content),
                0,
            )
    finally:
        engine.dispose()


def test_restore_capacity_rejection_is_mutation_free_then_success_clears_all_tombstones(
    tmp_path: Path,
) -> None:
    engine, session_factory, data_dir, digest, content, revision_ids, import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
        with_import_sessions=True,
    )
    try:
        service = _service(session_factory, data_dir)
        plan = service.create_gc_plan()
        quarantine = service.confirm_gc_plan(plan.id, plan.summary_sha256)[0]
        quarantine_path = Path(quarantine.quarantine_path)
        cas_path = ArtifactStore(data_dir).blob_path(digest)
        assert quarantine_path.is_file() and not cas_path.exists()

        blocked = _service(
            session_factory,
            data_dir,
            free_space_bytes=lambda: 0,
            physical_floor=1,
        )
        module = import_module("ai_canvas.application.storage_lifecycle_service")
        with pytest.raises(module.StorageCapacityExceeded):
            blocked.restore_quarantine(quarantine.id)
        assert quarantine_path.is_file() and not cas_path.exists()
        with session_factory() as session:
            blob = session.get(StoredBlobRow, digest)
            audit = session.get(QuarantineEntryRow, quarantine.id)
            assert blob is not None and blob.state == "QUARANTINED"
            assert audit is not None and audit.operation_state == "QUARANTINED"
            assert all(
                session.get(ArtifactRevisionRow, revision_id).discarded_at is not None
                for revision_id in revision_ids
            )

        assert service.restore_quarantine(quarantine.id) is True
        assert cas_path.read_bytes() == content
        with session_factory() as session:
            blob = session.get(StoredBlobRow, digest)
            assert blob is not None
            assert (blob.state, blob.logical_ref_count) == ("ACTIVE", 2)
            for revision_id, import_id in zip(revision_ids, import_ids, strict=True):
                revision = session.get(ArtifactRevisionRow, revision_id)
                import_row = session.get(ImportSessionRow, import_id)
                assert revision is not None and revision.discarded_at is None
                assert revision.media_health == "AVAILABLE"
                assert import_row is not None and import_row.discarded_at is None
                assert import_row.discard_cleanup_pending is False
        assert ArtifactService(session_factory, settings=Settings(data_dir=data_dir)).get_content(
            UUID(revision_ids[0])
        ).body == content
        assert service.create_gc_plan().entries == ()
    finally:
        engine.dispose()


def test_purge_zeroes_physical_bytes_and_future_reservation_ignores_old_blob(
    tmp_path: Path,
) -> None:
    engine, session_factory, data_dir, digest, content, _revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
    )
    try:
        current = [NOW]
        service = _service(session_factory, data_dir, now=lambda: current[0], quota=len(content))
        plan = service.create_gc_plan()
        quarantine = service.confirm_gc_plan(plan.id, plan.summary_sha256)[0]
        current[0] += timedelta(days=8)
        assert service.purge_due() == 1
        with session_factory() as session:
            blob = session.get(StoredBlobRow, digest)
            assert blob is not None
            assert (blob.state, blob.physical_bytes, blob.logical_ref_count) == ("PURGED", 0, 0)
        reservation = service.reserve(
            operation_type="UPLOAD",
            operation_id=str(uuid4()),
            expected_added_bytes=len(content),
            ttl_seconds=60,
        )
        assert reservation.reserved_bytes == len(content)
        assert not Path(quarantine.quarantine_path).exists()
    finally:
        engine.dispose()


def test_purge_refuses_new_live_same_sha_revision_before_unlink(tmp_path: Path) -> None:
    engine, session_factory, data_dir, digest, content, revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
    )
    try:
        service = _service(session_factory, data_dir)
        plan = service.create_gc_plan()
        quarantine = service.confirm_gc_plan(plan.id, plan.summary_sha256)[0]
        quarantine_path = Path(quarantine.quarantine_path)
        live_revision_id = str(uuid4())
        with session_factory.begin() as session:
            source = session.get(ArtifactRevisionRow, revision_ids[0])
            audit = session.get(QuarantineEntryRow, quarantine.id)
            assert source is not None and audit is not None
            artifact_id = str(uuid4())
            session.add(
                ArtifactRow(
                    id=artifact_id,
                    origin_project_id=source.source_project_id,
                    kind="image",
                    created_at=NOW,
                )
            )
            session.flush()
            session.add(
                ArtifactRevisionRow(
                    id=live_revision_id,
                    artifact_id=artifact_id,
                    sequence=1,
                    blob_sha256=digest,
                    storage_kind="MANAGED_BLOB",
                    media_health="AVAILABLE",
                    external_snapshot_id=None,
                    source_kind="late_fixture",
                    source_project_id=source.source_project_id,
                    source_import_session_id=None,
                    display_filename="late.png",
                    discarded_at=None,
                    created_at=NOW,
                )
            )
            audit.quarantine_until = NOW - timedelta(seconds=1)

        assert service.purge_due() == 0
        assert quarantine_path.read_bytes() == content
        with session_factory() as session:
            blob = session.get(StoredBlobRow, digest)
            audit = session.get(QuarantineEntryRow, quarantine.id)
            live = session.get(ArtifactRevisionRow, live_revision_id)
            assert blob is not None and (blob.state, blob.physical_bytes) == (
                "QUARANTINED",
                len(content),
            )
            assert audit is not None and audit.operation_state == "PREPARED_PURGE"
            assert audit.operation_error is not None and "reference" in audit.operation_error
            assert live is not None and live.media_health == "AVAILABLE"
    finally:
        engine.dispose()


def test_purge_validates_all_frozen_targets_before_unlink(tmp_path: Path) -> None:
    engine, session_factory, data_dir, digest, content, _revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
    )
    try:
        service = _service(session_factory, data_dir)
        plan = service.create_gc_plan()
        quarantine = service.confirm_gc_plan(plan.id, plan.summary_sha256)[0]
        quarantine_path = Path(quarantine.quarantine_path)
        with session_factory.begin() as session:
            audit = session.get(QuarantineEntryRow, quarantine.id)
            frozen = session.scalar(
                select(QuarantineTargetRow)
                .where(QuarantineTargetRow.quarantine_entry_id == quarantine.id)
                .order_by(QuarantineTargetRow.target_id)
            )
            assert audit is not None and frozen is not None
            audit.quarantine_until = NOW - timedelta(seconds=1)
            frozen.target_id = str(UUID(int=0))

        assert service.purge_due() == 0
        assert quarantine_path.read_bytes() == content
        with session_factory() as session:
            blob = session.get(StoredBlobRow, digest)
            audit = session.get(QuarantineEntryRow, quarantine.id)
            assert blob is not None and (blob.state, blob.physical_bytes) == (
                "QUARANTINED",
                len(content),
            )
            assert audit is not None and audit.operation_state == "RECOVERY_REQUIRED"
    finally:
        engine.dispose()


def test_restart_reconciles_move_and_unlink_failures_without_duplicate_accounting(
    tmp_path: Path,
) -> None:
    engine, session_factory, data_dir, digest, content, _revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
    )
    try:
        def fail_after_move(name: str) -> None:
            if name == "after_quarantine_move_before_finalize":
                raise RuntimeError("injected after move")

        crashing = _service(session_factory, data_dir, failpoint=fail_after_move)
        plan = crashing.create_gc_plan()
        with pytest.raises(RuntimeError, match="injected after move"):
            crashing.confirm_gc_plan(plan.id, plan.summary_sha256)
        with session_factory() as session:
            audit = session.scalar(select(QuarantineEntryRow))
            blob = session.get(StoredBlobRow, digest)
            assert audit is not None and audit.operation_state == "PREPARED_QUARANTINE"
            assert blob is not None and (blob.state, blob.physical_bytes) == (
                "ACTIVE",
                len(content),
            )
            quarantine_id = audit.id
            quarantine_path = Path(audit.quarantine_path)
        assert quarantine_path.is_file()
        crashed_summary = crashing.storage_summary()
        assert (
            crashed_summary.managed_blob_bytes
            + crashed_summary.quarantine_bytes
            + crashed_summary.staging_bytes
            == len(content)
        )

        restarted = _service(session_factory, data_dir)
        report = restarted.reconcile_lifecycle_operations()
        assert report.recovered_operations == 1
        summary = restarted.storage_summary()
        assert summary.managed_blob_bytes + summary.quarantine_bytes + summary.staging_bytes == len(
            content
        )

        with session_factory.begin() as session:
            audit = session.get(QuarantineEntryRow, quarantine_id)
            assert audit is not None
            audit.quarantine_until = NOW - timedelta(seconds=1)

        def fail_after_unlink(name: str) -> None:
            if name == "after_purge_unlink_before_finalize":
                raise RuntimeError("injected after unlink")

        purge_crash = _service(session_factory, data_dir, failpoint=fail_after_unlink)
        with pytest.raises(RuntimeError, match="injected after unlink"):
            purge_crash.purge_due()
        assert not quarantine_path.exists()
        with session_factory() as session:
            audit = session.get(QuarantineEntryRow, quarantine_id)
            blob = session.get(StoredBlobRow, digest)
            assert audit is not None and audit.operation_state == "PREPARED_PURGE"
            assert blob is not None and blob.physical_bytes == len(content)

        final_report = restarted.reconcile_lifecycle_operations()
        assert final_report.recovered_operations == 1
        with session_factory() as session:
            blob = session.get(StoredBlobRow, digest)
            audit = session.get(QuarantineEntryRow, quarantine_id)
            assert blob is not None and (blob.state, blob.physical_bytes) == ("PURGED", 0)
            assert audit is not None and audit.operation_state == "PURGED"
    finally:
        engine.dispose()


def test_restart_reconciles_restore_move_before_finalize(tmp_path: Path) -> None:
    engine, session_factory, data_dir, digest, content, _revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
    )
    try:
        service = _service(session_factory, data_dir)
        plan = service.create_gc_plan()
        quarantine = service.confirm_gc_plan(plan.id, plan.summary_sha256)[0]
        quarantine_path = Path(quarantine.quarantine_path)
        cas_path = ArtifactStore(data_dir).blob_path(digest)

        def fail_after_restore_move(name: str) -> None:
            if name == "after_restore_move_before_finalize":
                raise RuntimeError("injected after restore move")

        crashing = _service(session_factory, data_dir, failpoint=fail_after_restore_move)
        with pytest.raises(RuntimeError, match="injected after restore move"):
            crashing.restore_quarantine(quarantine.id)
        assert cas_path.read_bytes() == content
        assert not quarantine_path.exists()
        with session_factory() as session:
            audit = session.get(QuarantineEntryRow, quarantine.id)
            blob = session.get(StoredBlobRow, digest)
            assert audit is not None and audit.operation_state == "PREPARED_RESTORE"
            assert blob is not None and blob.state == "QUARANTINED"

        report = service.reconcile_lifecycle_operations()
        assert report.recovered_operations == 1
        with session_factory() as session:
            audit = session.get(QuarantineEntryRow, quarantine.id)
            blob = session.get(StoredBlobRow, digest)
            assert audit is not None and audit.operation_state == "RESTORED"
            assert blob is not None and (blob.state, blob.logical_ref_count) == ("ACTIVE", 2)
    finally:
        engine.dispose()


@pytest.mark.parametrize("location", ("BOTH", "NEITHER"))
def test_quarantine_recovery_fails_closed_for_ambiguous_file_location(
    tmp_path: Path,
    location: str,
) -> None:
    engine, session_factory, data_dir, digest, content, revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
    )
    try:
        def fail_after_move(name: str) -> None:
            if name == "after_quarantine_move_before_finalize":
                raise RuntimeError("injected after quarantine move")

        crashing = _service(session_factory, data_dir, failpoint=fail_after_move)
        plan = crashing.create_gc_plan()
        with pytest.raises(RuntimeError, match="injected after quarantine move"):
            crashing.confirm_gc_plan(plan.id, plan.summary_sha256)
        with session_factory() as session:
            audit = session.scalar(select(QuarantineEntryRow))
            assert audit is not None
            quarantine_id = audit.id
            quarantine_path = Path(audit.quarantine_path)
        cas_path = ArtifactStore(data_dir).blob_path(digest)
        if location == "BOTH":
            cas_path.parent.mkdir(parents=True, exist_ok=True)
            cas_path.write_bytes(content)
        else:
            quarantine_path.unlink()

        report = _service(session_factory, data_dir).reconcile_lifecycle_operations()
        assert report.recovered_operations == 0
        assert report.recovery_required_ids == (quarantine_id,)
        with session_factory() as session:
            audit = session.get(QuarantineEntryRow, quarantine_id)
            blob = session.get(StoredBlobRow, digest)
            assert audit is not None and audit.operation_state == "RECOVERY_REQUIRED"
            assert blob is not None and (blob.state, blob.physical_bytes) == (
                "ACTIVE",
                len(content),
            )
            assert all(
                session.get(ArtifactRevisionRow, revision_id).media_health == "AVAILABLE"
                for revision_id in revision_ids
            )
    finally:
        engine.dispose()


def test_recovery_fails_closed_before_partial_finalize_for_missing_frozen_target(
    tmp_path: Path,
) -> None:
    engine, session_factory, data_dir, digest, content, revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
    )
    try:
        def fail_after_move(name: str) -> None:
            if name == "after_quarantine_move_before_finalize":
                raise RuntimeError("injected after move")

        crashing = _service(session_factory, data_dir, failpoint=fail_after_move)
        plan = crashing.create_gc_plan()
        with pytest.raises(RuntimeError, match="injected after move"):
            crashing.confirm_gc_plan(plan.id, plan.summary_sha256)
        with session_factory.begin() as session:
            audit = session.scalar(select(QuarantineEntryRow))
            assert audit is not None
            frozen = session.scalar(
                select(QuarantineTargetRow)
                .where(QuarantineTargetRow.quarantine_entry_id == audit.id)
                .order_by(QuarantineTargetRow.target_id)
            )
            assert frozen is not None
            frozen.target_id = str(UUID(int=0))
            quarantine_id = audit.id
            quarantine_path = Path(audit.quarantine_path)

        report = _service(session_factory, data_dir).reconcile_lifecycle_operations()
        assert report.recovered_operations == 0
        assert report.recovery_required_ids == (quarantine_id,)
        assert quarantine_path.read_bytes() == content
        with session_factory() as session:
            blob = session.get(StoredBlobRow, digest)
            audit = session.get(QuarantineEntryRow, quarantine_id)
            assert blob is not None and (blob.state, blob.physical_bytes) == (
                "ACTIVE",
                len(content),
            )
            assert audit is not None and audit.operation_state == "RECOVERY_REQUIRED"
            assert all(
                session.get(ArtifactRevisionRow, revision_id).media_health == "AVAILABLE"
                for revision_id in revision_ids
            )
    finally:
        engine.dispose()


def test_quarantined_digest_reuse_is_rejected_and_purged_requires_verified_bytes(
    tmp_path: Path,
) -> None:
    engine, session_factory, data_dir, digest, content, _revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
    )
    try:
        service = _service(session_factory, data_dir)
        plan = service.create_gc_plan()
        quarantine = service.confirm_gc_plan(plan.id, plan.summary_sha256)[0]
        repository = _blob_repository_type()()
        module = import_module("ai_canvas.infrastructure.db.blob_lifecycle_repository")
        with session_factory() as session:
            blob = session.get(StoredBlobRow, digest)
            with pytest.raises(module.BlobLifecycleConflict) as captured:
                repository.assert_ingest_reusable(blob, verified_new_bytes=True)
            assert captured.value.code == "BLOB_QUARANTINED"

        with session_factory.begin() as session:
            audit = session.get(QuarantineEntryRow, quarantine.id)
            assert audit is not None
            audit.quarantine_until = NOW - timedelta(seconds=1)
        assert service.purge_due() == 1
        with session_factory.begin() as session:
            blob = session.get(StoredBlobRow, digest)
            with pytest.raises(module.BlobLifecycleConflict) as captured:
                repository.reactivate_verified_purged_blob(
                    session,
                    digest,
                    verified_new_bytes=False,
                )
            assert captured.value.code == "BLOB_PURGED"

        store = ArtifactStore(data_dir)
        staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
        store.promote(staged)
        with session_factory.begin() as session:
            repository.reactivate_verified_purged_blob(
                session,
                digest,
                verified_new_bytes=True,
            )
        with session_factory() as session:
            blob = session.get(StoredBlobRow, digest)
            assert blob is not None and (blob.state, blob.physical_bytes) == (
                "ACTIVE",
                len(content),
            )
    finally:
        engine.dispose()


def test_application_startup_reconciles_prepared_quarantine_before_serving(
    tmp_path: Path,
) -> None:
    engine, session_factory, data_dir, digest, _content, _revision_ids, _import_ids = _seed_group(
        tmp_path,
        discarded=(True, True),
    )

    def fail_after_move(name: str) -> None:
        if name == "after_quarantine_move_before_finalize":
            raise RuntimeError("restart boundary")

    startup_now = datetime.now(timezone.utc)
    crashing = _service(
        session_factory,
        data_dir,
        failpoint=fail_after_move,
        now=lambda: startup_now,
    )
    plan = crashing.create_gc_plan()
    with pytest.raises(RuntimeError, match="restart boundary"):
        crashing.confirm_gc_plan(plan.id, plan.summary_sha256)
    engine.dispose()

    app = create_app(Settings(data_dir=data_dir, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver"):
        with make_session_factory(app.state.engine)() as session:
            blob = session.get(StoredBlobRow, digest)
            audit = session.scalar(select(QuarantineEntryRow))
            assert blob is not None and blob.state == "QUARANTINED"
            assert audit is not None and audit.operation_state == "QUARANTINED"


def test_application_startup_reconciles_abandoned_promotion_candidate_before_serving(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    store = ArtifactStore(data_dir)
    store._prepare_promotion_root()
    candidate = store.promotion_root / f"{uuid4().hex}.candidate"
    candidate.write_bytes(b"candidate-left-after-process-crash")

    app = create_app(Settings(data_dir=data_dir, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver"):
        assert not candidate.exists()


def test_orphan_blob_without_logical_target_is_reported_and_not_deleted(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    database_path = data_dir / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    content = b"tracked physical orphan"
    store = ArtifactStore(data_dir)
    staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
    store.promote(staged)
    try:
        with session_factory.begin() as session:
            session.add(
                StoredBlobRow(
                    sha256=staged.sha256,
                    byte_size=len(content),
                    media_kind="image",
                    media_type="image/png",
                    metadata_json='{"kind":"image","width":1,"height":1}',
                    created_at=NOW,
                )
            )
        service = _service(session_factory, data_dir)
        report = service.reconcile_lifecycle_operations()
        assert report.orphan_blob_sha256s == (staged.sha256,)
        assert report.orphan_owned_bytes == 0
        assert store.blob_path(staged.sha256).read_bytes() == content
        with session_factory() as session:
            blob = session.get(StoredBlobRow, staged.sha256)
            assert blob is not None and (blob.state, blob.physical_bytes) == (
                "ACTIVE",
                len(content),
            )
        summary = service.storage_summary()
        assert summary.managed_blob_bytes == len(content)
        assert summary.staging_bytes == 0
    finally:
        engine.dispose()
