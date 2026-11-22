import os
from datetime import datetime, timedelta, timezone
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ai_canvas.config import Settings
from ai_canvas.infrastructure.artifacts import ArtifactStore
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


def test_storage_lifecycle_service_module_exists() -> None:
    assert find_spec("ai_canvas.application.storage_lifecycle_service") is not None


def test_active_reservation_blocks_concurrent_physical_overcommit(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        module = import_module("ai_canvas.application.storage_lifecycle_service")
        service_type = getattr(module, "StorageLifecycleService", None)
        error_type = getattr(module, "StorageCapacityExceeded", None)
        assert service_type is not None
        assert error_type is not None
        service = service_type(
            make_session_factory(engine),
            settings=Settings(data_dir=tmp_path / "data"),
            free_space_bytes=lambda: 21 * 1024**3,
        )

        first = service.reserve(
            operation_type="UPLOAD",
            operation_id=str(uuid4()),
            expected_added_bytes=8 * 1024**3,
            ttl_seconds=60,
        )
        assert first.reserved_bytes == 8 * 1024**3
        with pytest.raises(error_type) as captured:
            service.reserve(
                operation_type="TRANSCODE",
                operation_id=str(uuid4()),
                expected_added_bytes=8 * 1024**3,
                ttl_seconds=60,
            )
        assert captured.value.code == "INSUFFICIENT_PHYSICAL_SPACE"

        service.release(first.id)
        replacement = service.reserve(
            operation_type="TRANSCODE",
            operation_id=str(uuid4()),
            expected_added_bytes=8 * 1024**3,
            ttl_seconds=60,
        )
        assert replacement.reserved_bytes == 8 * 1024**3
    finally:
        engine.dispose()


def test_expired_reservation_no_longer_counts_against_capacity(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        module = import_module("ai_canvas.application.storage_lifecycle_service")
        current_time = [datetime(2026, 8, 18, tzinfo=timezone.utc)]
        service = module.StorageLifecycleService(
            make_session_factory(engine),
            settings=Settings(data_dir=tmp_path / "data"),
            free_space_bytes=lambda: 21 * 1024**3,
            now=lambda: current_time[0],
        )
        service.reserve(
            operation_type="UPLOAD",
            operation_id=str(uuid4()),
            expected_added_bytes=8 * 1024**3,
            ttl_seconds=1,
        )

        current_time[0] += timedelta(seconds=2)
        replacement = service.reserve(
            operation_type="TRANSCODE",
            operation_id=str(uuid4()),
            expected_added_bytes=8 * 1024**3,
            ttl_seconds=60,
        )

        assert replacement.reserved_bytes == 8 * 1024**3
    finally:
        engine.dispose()


def test_capacity_admission_counts_owned_staging_transcode_and_quarantine_bytes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        module = import_module("ai_canvas.application.storage_lifecycle_service")
        data_dir = tmp_path / "data"
        (data_dir / "artifacts" / ".staging").mkdir(parents=True)
        (data_dir / "artifacts" / ".staging" / "merge.tmp").write_bytes(b"1234")
        (data_dir / "artifacts" / ".transcode").mkdir(parents=True)
        (data_dir / "artifacts" / ".transcode" / "preview.tmp").write_bytes(b"123")
        (data_dir / "artifacts" / ".quarantine").mkdir(parents=True)
        (data_dir / "artifacts" / ".quarantine" / "retained.blob").write_bytes(b"12")
        service = module.StorageLifecycleService(
            make_session_factory(engine),
            settings=Settings(
                data_dir=data_dir,
                workspace_quota_bytes=10,
                media_physical_free_floor_bytes=0,
            ),
            free_space_bytes=lambda: 100,
        )

        with pytest.raises(module.StorageCapacityExceeded) as captured:
            service.reserve(
                operation_type="UPLOAD",
                operation_id=str(uuid4()),
                expected_added_bytes=2,
                ttl_seconds=60,
            )

        assert captured.value.code == "QUOTA_EXCEEDED"
    finally:
        engine.dispose()


def test_storage_summary_counts_private_promotion_candidate_as_staging_bytes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        module = import_module("ai_canvas.application.storage_lifecycle_service")
        data_dir = tmp_path / "data"
        store = ArtifactStore(data_dir, max_bytes=64)
        content = b"private-candidate-counted-once"
        staged = store.stage_chunks(uuid4(), [content], declared_size=len(content))
        ticket = store.prepare_promotion(staged)
        service = module.StorageLifecycleService(
            make_session_factory(engine),
            settings=Settings(data_dir=data_dir),
            free_space_bytes=lambda: 100 * 1024**3,
        )

        summary = service.storage_summary()

        assert summary.staging_bytes == len(content)
        assert summary.managed_blob_bytes + summary.staging_bytes + summary.quarantine_bytes == len(
            content
        )
        assert store.revoke_promotion(ticket) is True
    finally:
        engine.dispose()


def test_storage_summary_deduplicates_one_physical_file_linked_across_staging_roots(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        module = import_module("ai_canvas.application.storage_lifecycle_service")
        data_dir = tmp_path / "data"
        staging = data_dir / "artifacts" / ".staging"
        promotion = data_dir / "artifacts" / ".promotion"
        staging.mkdir(parents=True)
        promotion.mkdir()
        content = b"one-inode-visible-from-two-roots"
        public_path = staging / f"{uuid4()}.part"
        candidate_path = promotion / f"{uuid4().hex}.candidate"
        public_path.write_bytes(content)
        os.link(public_path, candidate_path)
        service = module.StorageLifecycleService(
            make_session_factory(engine),
            settings=Settings(data_dir=data_dir),
            free_space_bytes=lambda: 100 * 1024**3,
        )

        summary = service.storage_summary()

        assert summary.staging_bytes == len(content)
    finally:
        engine.dispose()


def test_storage_summary_counts_distinct_same_size_staging_files_separately(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        module = import_module("ai_canvas.application.storage_lifecycle_service")
        data_dir = tmp_path / "data"
        staging = data_dir / "artifacts" / ".staging"
        promotion = data_dir / "artifacts" / ".promotion"
        staging.mkdir(parents=True)
        promotion.mkdir()
        content = b"same-size-distinct-files"
        (staging / f"{uuid4()}.part").write_bytes(content)
        (promotion / f"{uuid4().hex}.candidate").write_bytes(content)
        service = module.StorageLifecycleService(
            make_session_factory(engine),
            settings=Settings(data_dir=data_dir),
            free_space_bytes=lambda: 100 * 1024**3,
        )

        summary = service.storage_summary()

        assert summary.staging_bytes == len(content) * 2
    finally:
        engine.dispose()


def test_physical_identity_fallback_never_collapses_distinct_paths_without_inode(
    tmp_path: Path,
) -> None:
    module = import_module("ai_canvas.application.storage_lifecycle_service")
    metadata = SimpleNamespace(st_dev=0, st_ino=0)
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_key = module.StorageLifecycleService._physical_file_identity(first, metadata)
    second_key = module.StorageLifecycleService._physical_file_identity(second, metadata)

    assert first_key == module.StorageLifecycleService._physical_file_identity(first, metadata)
    assert first_key != second_key
