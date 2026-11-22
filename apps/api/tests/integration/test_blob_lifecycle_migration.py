from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from ai_canvas.infrastructure.db.session import create_sqlite_engine


BLOB_LIFECYCLE_REVISION = "0006_blob_lifecycle"


def _config(database_path: Path) -> Config:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def _seed_shared_blob(database_path: Path) -> tuple[str, tuple[str, str]]:
    digest = "a" * 64
    project_id = str(uuid4())
    artifact_ids = (str(uuid4()), str(uuid4()))
    revision_ids = (str(uuid4()), str(uuid4()))
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects (id, name, created_at, updated_at) "
                    "VALUES (:id, 'migration', :now, :now)"
                ),
                {"id": project_id, "now": "2026-08-26 00:00:00"},
            )
            connection.execute(
                text(
                    "INSERT INTO stored_blobs "
                    "(sha256, byte_size, media_kind, media_type, metadata_json, created_at) "
                    "VALUES (:sha256, 7, 'image', 'image/png', '{}', :now)"
                ),
                {"sha256": digest, "now": "2026-08-26 00:00:00"},
            )
            for artifact_id in artifact_ids:
                connection.execute(
                    text(
                        "INSERT INTO artifacts (id, origin_project_id, kind, created_at) "
                        "VALUES (:id, :project_id, 'image', :now)"
                    ),
                    {
                        "id": artifact_id,
                        "project_id": project_id,
                        "now": "2026-08-26 00:00:00",
                    },
                )
            for artifact_id, revision_id in zip(artifact_ids, revision_ids, strict=True):
                connection.execute(
                    text(
                        "INSERT INTO artifact_revisions "
                        "(id, artifact_id, sequence, blob_sha256, storage_kind, media_health, "
                        "external_snapshot_id, source_kind, source_project_id, "
                        "source_import_session_id, display_filename, discarded_at, created_at) "
                        "VALUES (:id, :artifact_id, 1, :sha256, 'MANAGED_BLOB', 'AVAILABLE', "
                        "NULL, 'fixture', :project_id, NULL, 'shared.png', NULL, :now)"
                    ),
                    {
                        "id": revision_id,
                        "artifact_id": artifact_id,
                        "sha256": digest,
                        "project_id": project_id,
                        "now": "2026-08-26 00:00:00",
                    },
                )
    finally:
        engine.dispose()
    return digest, revision_ids


def test_0006_backfills_shared_logical_refs_and_physical_state(tmp_path: Path) -> None:
    database_path = tmp_path / "shared.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0005_media_session_grants")
    digest, _revision_ids = _seed_shared_blob(database_path)

    command.upgrade(config, BLOB_LIFECYCLE_REVISION)

    engine = create_sqlite_engine(database_path)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("stored_blobs")}
        assert {"physical_bytes", "logical_ref_count", "state", "quarantine_until"} <= columns
        with engine.connect() as connection:
            lifecycle = connection.execute(
                text(
                    "SELECT physical_bytes, logical_ref_count, state, quarantine_until "
                    "FROM stored_blobs WHERE sha256 = :sha256"
                ),
                {"sha256": digest},
            ).one()
        assert tuple(lifecycle) == (7, 2, "ACTIVE", None)
    finally:
        engine.dispose()


def test_0006_backfills_mixed_revision_and_rendition_refs(tmp_path: Path) -> None:
    database_path = tmp_path / "mixed-refs.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0005_media_session_grants")
    digest, revision_ids = _seed_shared_blob(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            now = "2026-08-26 00:00:00"
            connection.execute(
                text("UPDATE artifact_revisions SET discarded_at = :now WHERE id = :id"),
                {"id": revision_ids[1], "now": now},
            )
            for profile, state, removed_at in (
                ("proxy_720p", "READY", None),
                ("preview_low", "REMOVED", now),
            ):
                connection.execute(
                    text(
                        "INSERT INTO artifact_renditions "
                        "(id, source_revision_id, profile, blob_sha256, byte_size, media_type, "
                        "metadata_json, state, job_id, created_at, updated_at, removed_at) "
                        "VALUES (:id, :revision_id, :profile, :sha256, 7, 'image/png', '{}', "
                        ":state, NULL, :now, :now, :removed_at)"
                    ),
                    {
                        "id": str(uuid4()),
                        "revision_id": revision_ids[0],
                        "profile": profile,
                        "sha256": digest,
                        "state": state,
                        "now": now,
                        "removed_at": removed_at,
                    },
                )
    finally:
        engine.dispose()

    command.upgrade(config, BLOB_LIFECYCLE_REVISION)

    engine = create_sqlite_engine(database_path)
    try:
        assert "ix_artifact_renditions_blob_sha256" in {
            index["name"] for index in inspect(engine).get_indexes("artifact_renditions")
        }
        with engine.connect() as connection:
            count = connection.execute(
                text("SELECT logical_ref_count FROM stored_blobs WHERE sha256 = :sha256"),
                {"sha256": digest},
            ).scalar_one()
        assert count == 2
    finally:
        engine.dispose()


def test_0006_downgrade_preflight_refuses_non_active_state_before_ddl(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "unsafe-downgrade.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0005_media_session_grants")
    digest, _revision_ids = _seed_shared_blob(database_path)
    command.upgrade(config, BLOB_LIFECYCLE_REVISION)
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE artifact_revisions SET discarded_at = :now "
                    "WHERE blob_sha256 = :sha256"
                ),
                {"sha256": digest, "now": "2026-08-26 00:01:00"},
            )
            connection.execute(
                text(
                    "UPDATE stored_blobs SET logical_ref_count = 0, state = 'QUARANTINED', "
                    "quarantine_until = :until WHERE sha256 = :sha256"
                ),
                {"sha256": digest, "until": "2026-09-02 00:01:00"},
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="non-ACTIVE"):
        command.downgrade(config, "0005_media_session_grants")

    engine = create_sqlite_engine(database_path)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("stored_blobs")}
        assert {"physical_bytes", "logical_ref_count", "state", "quarantine_until"} <= columns
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0006_blob_lifecycle"
            )
    finally:
        engine.dispose()


def test_0006_safe_downgrade_preserves_active_blob_and_shared_revisions(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "safe-downgrade.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0005_media_session_grants")
    digest, revision_ids = _seed_shared_blob(database_path)
    command.upgrade(config, BLOB_LIFECYCLE_REVISION)

    command.downgrade(config, "0005_media_session_grants")

    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        columns = {column["name"] for column in inspector.get_columns("stored_blobs")}
        assert not {
            "physical_bytes",
            "logical_ref_count",
            "state",
            "quarantine_until",
        } & columns
        assert "ix_artifact_renditions_blob_sha256" not in {
            index["name"] for index in inspector.get_indexes("artifact_renditions")
        }
        assert "gc_plan_targets" not in inspector.get_table_names()
        assert "quarantine_targets" not in inspector.get_table_names()
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT byte_size FROM stored_blobs WHERE sha256 = :sha256"),
                {"sha256": digest},
            ).scalar_one() == 7
            preserved = connection.execute(
                text(
                    "SELECT id FROM artifact_revisions WHERE blob_sha256 = :sha256 "
                    "ORDER BY id"
                ),
                {"sha256": digest},
            ).scalars().all()
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert preserved == sorted(revision_ids)
        assert version == "0005_media_session_grants"
    finally:
        engine.dispose()


def test_0006_downgrade_to_0005_preserves_external_revision(tmp_path: Path) -> None:
    database_path = tmp_path / "external-safe-downgrade.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0005_media_session_grants")
    _digest, revision_ids = _seed_shared_blob(database_path)
    external_revision_id = str(uuid4())
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            project_id = connection.execute(
                text("SELECT source_project_id FROM artifact_revisions WHERE id = :id"),
                {"id": revision_ids[0]},
            ).scalar_one()
            grant_id = str(uuid4())
            snapshot_id = str(uuid4())
            artifact_id = str(uuid4())
            now = "2026-08-26 00:00:00"
            connection.execute(
                text(
                    "INSERT INTO external_root_grants "
                    "(id, subject, display_name, canonical_path, volume_identity, "
                    "root_file_identity, created_at, revoked_at) VALUES "
                    "(:id, 'local', 'external', 'D:/external', 'volume', 'root', :now, NULL)"
                ),
                {"id": grant_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO external_snapshots "
                    "(id, root_grant_id, canonical_path, relative_path, file_identity, sha256, "
                    "byte_size, modified_at, media_kind, media_type, metadata_json, "
                    "confirmed_by_subject, confirmed_at) VALUES "
                    "(:id, :grant_id, 'D:/external/image.png', 'image.png', 'file', :sha256, "
                    "9, :now, 'image', 'image/png', '{}', 'local', :now)"
                ),
                {"id": snapshot_id, "grant_id": grant_id, "sha256": "c" * 64, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO artifacts (id, origin_project_id, kind, created_at) "
                    "VALUES (:id, :project_id, 'image', :now)"
                ),
                {"id": artifact_id, "project_id": project_id, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO artifact_revisions "
                    "(id, artifact_id, sequence, blob_sha256, storage_kind, media_health, "
                    "external_snapshot_id, source_kind, source_project_id, "
                    "source_import_session_id, display_filename, discarded_at, created_at) "
                    "VALUES (:id, :artifact_id, 1, NULL, 'EXTERNAL_REFERENCE', 'AVAILABLE', "
                    ":snapshot_id, 'external_reference', :project_id, NULL, 'image.png', "
                    "NULL, :now)"
                ),
                {
                    "id": external_revision_id,
                    "artifact_id": artifact_id,
                    "snapshot_id": snapshot_id,
                    "project_id": project_id,
                    "now": now,
                },
            )
    finally:
        engine.dispose()

    command.upgrade(config, BLOB_LIFECYCLE_REVISION)
    command.downgrade(config, "0005_media_session_grants")

    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            preserved = connection.execute(
                text(
                    "SELECT storage_kind, external_snapshot_id FROM artifact_revisions "
                    "WHERE id = :id"
                ),
                {"id": external_revision_id},
            ).one()
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert tuple(preserved) == ("EXTERNAL_REFERENCE", preserved.external_snapshot_id)
        assert preserved.external_snapshot_id is not None
        assert version == "0005_media_session_grants"
    finally:
        engine.dispose()


def test_0006_downgrade_preflight_refuses_owner_without_normalized_target(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "missing-normalized-target.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0005_media_session_grants")
    digest, _revision_ids = _seed_shared_blob(database_path)
    command.upgrade(config, BLOB_LIFECYCLE_REVISION)
    plan_id = str(uuid4())
    entry_id = str(uuid4())
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO gc_plans "
                    "(id, summary_sha256, logical_reclaim_bytes, physical_reclaim_bytes, "
                    "created_by_subject, created_at, confirmed_by_subject, confirmed_at, "
                    "executed_at) VALUES (:id, :summary, 7, 7, 'fixture', :now, NULL, NULL, NULL)"
                ),
                {"id": plan_id, "summary": "b" * 64, "now": "2026-08-26 00:00:00"},
            )
            connection.execute(
                text(
                    "INSERT INTO gc_plan_entries "
                    "(id, gc_plan_id, target_kind, target_id, reason, logical_reclaim_bytes, "
                    "physical_reclaim_bytes, outcome) VALUES "
                    "(:id, :plan_id, 'STORED_BLOB', :sha256, 'fixture', 7, 7, NULL)"
                ),
                {"id": entry_id, "plan_id": plan_id, "sha256": digest},
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="grouped blob lifecycle targets"):
        command.downgrade(config, "0005_media_session_grants")

    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        assert "gc_plan_targets" in inspector.get_table_names()
        assert "physical_bytes" in {
            column["name"] for column in inspector.get_columns("stored_blobs")
        }
    finally:
        engine.dispose()


def test_0006_upgrade_is_resumable_when_schema_finished_before_stamp(tmp_path: Path) -> None:
    database_path = tmp_path / "resume-upgrade.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0005_media_session_grants")
    digest, _revision_ids = _seed_shared_blob(database_path)
    command.upgrade(config, BLOB_LIFECYCLE_REVISION)
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = '0005_media_session_grants'")
            )
    finally:
        engine.dispose()

    command.upgrade(config, BLOB_LIFECYCLE_REVISION)

    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            lifecycle = connection.execute(
                text(
                    "SELECT physical_bytes, logical_ref_count, state "
                    "FROM stored_blobs WHERE sha256 = :sha256"
                ),
                {"sha256": digest},
            ).one()
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert tuple(lifecycle) == (7, 2, "ACTIVE")
        assert version == "0006_blob_lifecycle"
    finally:
        engine.dispose()


def test_0006_upgrade_refuses_complete_shadow_without_primary_key_before_ddl(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "unsafe-upgrade-shadow.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0005_media_session_grants")
    digest, _revision_ids = _seed_shared_blob(database_path)
    command.upgrade(config, BLOB_LIFECYCLE_REVISION)
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = '0005_media_session_grants'")
            )
            connection.execute(
                text(
                    "CREATE TABLE _alembic_tmp_stored_blobs ("
                    "sha256 VARCHAR(64) NOT NULL, "
                    "byte_size INTEGER NOT NULL, media_kind VARCHAR(16) NOT NULL, "
                    "media_type VARCHAR(255) NOT NULL, metadata_json TEXT NOT NULL, "
                    "created_at DATETIME NOT NULL, physical_bytes INTEGER NOT NULL, "
                    "logical_ref_count INTEGER NOT NULL, state VARCHAR(16) NOT NULL, "
                    "quarantine_until DATETIME, "
                    "CONSTRAINT ck_stored_blobs_physical_bytes_nonnegative "
                    "CHECK (physical_bytes >= 0), "
                    "CONSTRAINT ck_stored_blobs_logical_ref_count_nonnegative "
                    "CHECK (logical_ref_count >= 0), "
                    "CONSTRAINT ck_stored_blobs_lifecycle_state "
                    "CHECK (state IN ('ACTIVE', 'QUARANTINED', 'PURGED')), "
                    "CONSTRAINT ck_stored_blobs_lifecycle_shape CHECK ("
                    "(state = 'ACTIVE' AND physical_bytes = byte_size AND quarantine_until IS NULL) OR "
                    "(state = 'QUARANTINED' AND physical_bytes = byte_size "
                    "AND logical_ref_count = 0 AND quarantine_until IS NOT NULL) OR "
                    "(state = 'PURGED' AND physical_bytes = 0 "
                    "AND logical_ref_count = 0 AND quarantine_until IS NULL))"
                    ")"
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="primary key"):
        command.upgrade(config, BLOB_LIFECYCLE_REVISION)

    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        assert "_alembic_tmp_stored_blobs" in inspector.get_table_names()
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT physical_bytes FROM stored_blobs WHERE sha256 = :sha256"),
                {"sha256": digest},
            ).scalar_one() == 7
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "0005_media_session_grants"
            )
    finally:
        engine.dispose()


def test_0006_upgrade_recovers_verified_stored_blob_batch_shadow(tmp_path: Path) -> None:
    database_path = tmp_path / "resume-upgrade-shadow.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0005_media_session_grants")
    digest, _revision_ids = _seed_shared_blob(database_path)
    command.upgrade(config, BLOB_LIFECYCLE_REVISION)
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE stored_blobs RENAME TO _alembic_tmp_stored_blobs")
            )
            connection.execute(
                text("UPDATE alembic_version SET version_num = '0005_media_session_grants'")
            )
    finally:
        engine.dispose()

    command.upgrade(config, BLOB_LIFECYCLE_REVISION)

    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        assert "_alembic_tmp_stored_blobs" not in inspector.get_table_names()
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT physical_bytes FROM stored_blobs WHERE sha256 = :sha256"),
                {"sha256": digest},
            ).scalar_one() == 7
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "0006_blob_lifecycle"
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("authoritative_exists", (True, False))
def test_0006_downgrade_recovers_copied_stored_blob_batch_shadow(
    tmp_path: Path,
    authoritative_exists: bool,
) -> None:
    database_path = tmp_path / f"resume-downgrade-shadow-{authoritative_exists}.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0005_media_session_grants")
    digest, revision_ids = _seed_shared_blob(database_path)
    command.upgrade(config, BLOB_LIFECYCLE_REVISION)
    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.execute(
                text(
                    "CREATE TABLE _alembic_tmp_stored_blobs ("
                    "sha256 VARCHAR(64) NOT NULL PRIMARY KEY, "
                    "byte_size INTEGER NOT NULL, media_kind VARCHAR(16) NOT NULL, "
                    "media_type VARCHAR(255) NOT NULL, metadata_json TEXT NOT NULL, "
                    "created_at DATETIME NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO _alembic_tmp_stored_blobs "
                    "(sha256, byte_size, media_kind, media_type, metadata_json, created_at) "
                    "SELECT sha256, byte_size, media_kind, media_type, metadata_json, created_at "
                    "FROM stored_blobs"
                )
            )
            if not authoritative_exists:
                connection.execute(text("DROP TABLE stored_blobs"))
            connection.commit()
    finally:
        engine.dispose()

    command.downgrade(config, "0005_media_session_grants")

    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        assert "_alembic_tmp_stored_blobs" not in inspector.get_table_names()
        assert "ix_stored_blobs_media_kind_created" in {
            index["name"] for index in inspector.get_indexes("stored_blobs")
        }
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT byte_size FROM stored_blobs WHERE sha256 = :sha256"),
                {"sha256": digest},
            ).scalar_one() == 7
            preserved = connection.execute(
                text(
                    "SELECT id FROM artifact_revisions WHERE blob_sha256 = :sha256 "
                    "ORDER BY id"
                ),
                {"sha256": digest},
            ).scalars().all()
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert preserved == sorted(revision_ids)
        assert version == "0005_media_session_grants"
    finally:
        engine.dispose()
