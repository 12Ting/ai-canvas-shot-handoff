from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from ai_canvas.infrastructure.db.session import create_sqlite_engine


def _config(database_path: Path) -> Config:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def test_upgrade_from_0003_creates_b4_tables_and_revision_storage_columns(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0003_artifact_discard")

    command.upgrade(config, "head")

    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        assert {
            "large_upload_sessions",
            "large_upload_parts",
            "storage_reservations",
            "media_sessions",
            "media_session_grants",
            "external_root_grants",
            "external_snapshots",
            "artifact_renditions",
            "maintenance_jobs",
            "gc_plans",
            "gc_plan_entries",
            "quarantine_entries",
        } <= set(inspector.get_table_names())
        assert {"storage_kind", "media_health", "external_snapshot_id"} <= {
            column["name"] for column in inspector.get_columns("artifact_revisions")
        }
    finally:
        engine.dispose()


def test_legacy_0004_media_session_is_backfilled_when_upgrading_to_head(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-0004.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0004_media_lifecycle")
    revision_id = str(uuid4())
    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        assert "media_session_grants" not in inspector.get_table_names()
        assert {"resource_kind", "resource_id"} <= {
            column["name"] for column in inspector.get_columns("media_sessions")
        }
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO media_sessions "
                    "(id, subject, resource_kind, resource_id, created_at, last_used_at, "
                    "idle_expires_at, absolute_expires_at, revoked_at) "
                    "VALUES (:id, 'local', 'artifact_revision', :resource_id, :created_at, "
                    ":created_at, :idle_expires_at, :absolute_expires_at, NULL)"
                ),
                {
                    "id": "legacy-token",
                    "resource_id": revision_id,
                    "created_at": "2026-08-26 00:00:00",
                    "idle_expires_at": "2026-08-26 00:10:00",
                    "absolute_expires_at": "2026-08-26 01:00:00",
                },
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        assert "media_session_grants" in inspector.get_table_names()
        assert {"resource_kind", "resource_id"}.isdisjoint(
            column["name"] for column in inspector.get_columns("media_sessions")
        )
        with engine.connect() as connection:
            session = connection.execute(
                text("SELECT id, subject FROM media_sessions WHERE id = 'legacy-token'")
            ).one()
            grant = connection.execute(
                text(
                    "SELECT media_session_id, resource_kind, resource_id "
                    "FROM media_session_grants WHERE media_session_id = 'legacy-token'"
                )
            ).one()
        assert tuple(session) == ("legacy-token", "local")
        assert tuple(grant) == ("legacy-token", "artifact_revision", revision_id)
    finally:
        engine.dispose()


def test_0005_single_grant_downgrade_restores_legacy_media_session_columns(tmp_path: Path) -> None:
    database_path = tmp_path / "single-grant-downgrade.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0005_media_session_grants")
    revision_id = str(uuid4())
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO media_sessions "
                    "(id, subject, created_at, last_used_at, idle_expires_at, "
                    "absolute_expires_at, revoked_at) VALUES "
                    "('single-token', 'local', :created_at, :created_at, "
                    ":idle_expires_at, :absolute_expires_at, NULL)"
                ),
                {
                    "created_at": "2026-08-26 00:00:00",
                    "idle_expires_at": "2026-08-26 00:10:00",
                    "absolute_expires_at": "2026-08-26 01:00:00",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO media_session_grants "
                    "(media_session_id, resource_kind, resource_id, granted_at) "
                    "VALUES ('single-token', 'artifact_revision', :resource_id, :granted_at)"
                ),
                {"resource_id": revision_id, "granted_at": "2026-08-26 00:00:00"},
            )
    finally:
        engine.dispose()

    command.downgrade(config, "0004_media_lifecycle")

    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        assert "media_session_grants" not in inspector.get_table_names()
        assert {"resource_kind", "resource_id"} <= {
            column["name"] for column in inspector.get_columns("media_sessions")
        }
        with engine.connect() as connection:
            restored = connection.execute(
                text(
                    "SELECT resource_kind, resource_id FROM media_sessions "
                    "WHERE id = 'single-token'"
                )
            ).one()
        assert tuple(restored) == ("artifact_revision", revision_id)
    finally:
        engine.dispose()


def test_0005_downgrade_refuses_multi_grant_sessions_before_schema_changes(tmp_path: Path) -> None:
    database_path = tmp_path / "multi-grant-downgrade.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0005_media_session_grants")
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO media_sessions "
                    "(id, subject, created_at, last_used_at, idle_expires_at, "
                    "absolute_expires_at, revoked_at) VALUES "
                    "('multi-token', 'local', :created_at, :created_at, "
                    ":idle_expires_at, :absolute_expires_at, NULL)"
                ),
                {
                    "created_at": "2026-08-26 00:00:00",
                    "idle_expires_at": "2026-08-26 00:10:00",
                    "absolute_expires_at": "2026-08-26 01:00:00",
                },
            )
            for revision_id in (str(uuid4()), str(uuid4())):
                connection.execute(
                    text(
                        "INSERT INTO media_session_grants "
                        "(media_session_id, resource_kind, resource_id, granted_at) "
                        "VALUES ('multi-token', 'artifact_revision', :resource_id, :granted_at)"
                    ),
                    {"resource_id": revision_id, "granted_at": "2026-08-26 00:00:00"},
                )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="multiple resource grants"):
        command.downgrade(config, "0004_media_lifecycle")

    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        assert "media_session_grants" in inspector.get_table_names()
        assert {"resource_kind", "resource_id"}.isdisjoint(
            column["name"] for column in inspector.get_columns("media_sessions")
        )
    finally:
        engine.dispose()


def test_0005_upgrade_resumes_after_grant_backfill_before_legacy_columns_drop(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "partial-upgrade-after-backfill.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0004_media_lifecycle")
    revision_id = str(uuid4())
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO media_sessions "
                    "(id, subject, resource_kind, resource_id, created_at, last_used_at, "
                    "idle_expires_at, absolute_expires_at, revoked_at) VALUES "
                    "('partial-upgrade-token', 'local', 'artifact_revision', :resource_id, "
                    ":created_at, :created_at, :idle_expires_at, :absolute_expires_at, NULL)"
                ),
                {
                    "resource_id": revision_id,
                    "created_at": "2026-08-26 00:00:00",
                    "idle_expires_at": "2026-08-26 00:10:00",
                    "absolute_expires_at": "2026-08-26 01:00:00",
                },
            )
            connection.execute(
                text(
                    "CREATE TABLE media_session_grants ("
                    "media_session_id VARCHAR(64) NOT NULL, "
                    "resource_kind VARCHAR(32) NOT NULL, "
                    "resource_id VARCHAR(36) NOT NULL, "
                    "granted_at DATETIME NOT NULL, "
                    "PRIMARY KEY (media_session_id, resource_kind, resource_id), "
                    "FOREIGN KEY(media_session_id) REFERENCES media_sessions (id) "
                    "ON DELETE CASCADE)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_media_session_grants_resource "
                    "ON media_session_grants (resource_kind, resource_id)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO media_session_grants "
                    "(media_session_id, resource_kind, resource_id, granted_at) "
                    "SELECT id, resource_kind, resource_id, created_at FROM media_sessions"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "0005_media_session_grants")

    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        assert {"resource_kind", "resource_id"}.isdisjoint(
            column["name"] for column in inspector.get_columns("media_sessions")
        )
        with engine.connect() as connection:
            grant_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM media_session_grants "
                    "WHERE media_session_id = 'partial-upgrade-token'"
                )
            ).scalar_one()
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert grant_count == 1
        assert revision == "0005_media_session_grants"
    finally:
        engine.dispose()


def test_0005_upgrade_resumes_after_schema_changes_before_revision_stamp(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "partial-upgrade-before-stamp.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0005_media_session_grants")
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE alembic_version SET version_num = '0004_media_lifecycle'"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "0005_media_session_grants")

    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        assert "media_session_grants" in inspector.get_table_names()
        assert {"resource_kind", "resource_id"}.isdisjoint(
            column["name"] for column in inspector.get_columns("media_sessions")
        )
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert revision == "0005_media_session_grants"
    finally:
        engine.dispose()


def test_0005_downgrade_resumes_after_first_legacy_column_is_added(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "partial-downgrade-after-first-column.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0005_media_session_grants")
    revision_id = str(uuid4())
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO media_sessions "
                    "(id, subject, created_at, last_used_at, idle_expires_at, "
                    "absolute_expires_at, revoked_at) VALUES "
                    "('partial-downgrade-token', 'local', :created_at, :created_at, "
                    ":idle_expires_at, :absolute_expires_at, NULL)"
                ),
                {
                    "created_at": "2026-08-26 00:00:00",
                    "idle_expires_at": "2026-08-26 00:10:00",
                    "absolute_expires_at": "2026-08-26 01:00:00",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO media_session_grants "
                    "(media_session_id, resource_kind, resource_id, granted_at) VALUES "
                    "('partial-downgrade-token', 'artifact_revision', :resource_id, "
                    ":granted_at)"
                ),
                {
                    "resource_id": revision_id,
                    "granted_at": "2026-08-26 00:00:00",
                },
            )
            connection.execute(
                text("ALTER TABLE media_sessions ADD COLUMN resource_kind VARCHAR(32)")
            )
    finally:
        engine.dispose()

    command.downgrade(config, "0004_media_lifecycle")

    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        columns = {column["name"]: column for column in inspector.get_columns("media_sessions")}
        assert "media_session_grants" not in inspector.get_table_names()
        assert columns["resource_kind"]["nullable"] is False
        assert columns["resource_id"]["nullable"] is False
        with engine.connect() as connection:
            restored = connection.execute(
                text(
                    "SELECT resource_kind, resource_id FROM media_sessions "
                    "WHERE id = 'partial-downgrade-token'"
                )
            ).one()
        assert tuple(restored) == ("artifact_revision", revision_id)
    finally:
        engine.dispose()


def test_0005_downgrade_resumes_after_grants_drop_before_not_null_rebuild(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "partial-downgrade-before-not-null.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0005_media_session_grants")
    revision_id = str(uuid4())
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO media_sessions "
                    "(id, subject, created_at, last_used_at, idle_expires_at, "
                    "absolute_expires_at, revoked_at) VALUES "
                    "('late-downgrade-token', 'local', :created_at, :created_at, "
                    ":idle_expires_at, :absolute_expires_at, NULL)"
                ),
                {
                    "created_at": "2026-08-26 00:00:00",
                    "idle_expires_at": "2026-08-26 00:10:00",
                    "absolute_expires_at": "2026-08-26 01:00:00",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO media_session_grants "
                    "(media_session_id, resource_kind, resource_id, granted_at) VALUES "
                    "('late-downgrade-token', 'artifact_revision', :resource_id, "
                    ":granted_at)"
                ),
                {
                    "resource_id": revision_id,
                    "granted_at": "2026-08-26 00:00:00",
                },
            )
            connection.execute(
                text("ALTER TABLE media_sessions ADD COLUMN resource_kind VARCHAR(32)")
            )
            connection.execute(
                text("ALTER TABLE media_sessions ADD COLUMN resource_id VARCHAR(36)")
            )
            connection.execute(
                text(
                    "UPDATE media_sessions SET resource_kind = ("
                    "SELECT resource_kind FROM media_session_grants "
                    "WHERE media_session_id = media_sessions.id), resource_id = ("
                    "SELECT resource_id FROM media_session_grants "
                    "WHERE media_session_id = media_sessions.id)"
                )
            )
            connection.execute(text("DROP INDEX ix_media_session_grants_resource"))
            connection.execute(text("DROP TABLE media_session_grants"))
    finally:
        engine.dispose()


    command.downgrade(config, "0004_media_lifecycle")

    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        columns = {column["name"]: column for column in inspector.get_columns("media_sessions")}
        assert "media_session_grants" not in inspector.get_table_names()
        assert columns["resource_kind"]["nullable"] is False
        assert columns["resource_id"]["nullable"] is False
        with engine.connect() as connection:
            restored = connection.execute(
                text(
                    "SELECT resource_kind, resource_id FROM media_sessions "
                    "WHERE id = 'late-downgrade-token'"
                )
            ).one()
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert tuple(restored) == ("artifact_revision", revision_id)
        assert revision == "0004_media_lifecycle"
    finally:
        engine.dispose()


def _create_media_sessions_shadow(connection, *, include_legacy_resources: bool) -> None:
    legacy_columns = (
        "resource_kind VARCHAR(32) NOT NULL, resource_id VARCHAR(36) NOT NULL, "
        if include_legacy_resources
        else ""
    )
    connection.execute(
        text(
            "CREATE TABLE _alembic_tmp_media_sessions ("
            "id VARCHAR(64) NOT NULL, subject VARCHAR(255) NOT NULL, "
            f"{legacy_columns}"
            "created_at DATETIME NOT NULL, last_used_at DATETIME NOT NULL, "
            "idle_expires_at DATETIME NOT NULL, absolute_expires_at DATETIME NOT NULL, "
            "revoked_at DATETIME, PRIMARY KEY (id))"
        )
    )
    legacy_names = ", resource_kind, resource_id" if include_legacy_resources else ""
    connection.execute(
        text(
            "INSERT INTO _alembic_tmp_media_sessions "
            f"(id, subject{legacy_names}, created_at, last_used_at, idle_expires_at, "
            "absolute_expires_at, revoked_at) "
            f"SELECT id, subject{legacy_names}, created_at, last_used_at, idle_expires_at, "
            "absolute_expires_at, revoked_at FROM media_sessions"
        )
    )


@pytest.mark.parametrize("authoritative_exists", (True, False))
def test_0005_upgrade_recovers_copied_alembic_batch_shadow(
    tmp_path: Path,
    authoritative_exists: bool,
) -> None:
    database_path = tmp_path / f"upgrade-shadow-{authoritative_exists}.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0004_media_lifecycle")
    revision_id = str(uuid4())
    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.execute(
                text(
                    "INSERT INTO media_sessions "
                    "(id, subject, resource_kind, resource_id, created_at, last_used_at, "
                    "idle_expires_at, absolute_expires_at, revoked_at) VALUES "
                    "('upgrade-shadow-token', 'local', 'artifact_revision', :resource_id, "
                    ":created_at, :created_at, :idle_expires_at, :absolute_expires_at, NULL)"
                ),
                {
                    "resource_id": revision_id,
                    "created_at": "2026-08-26 00:00:00",
                    "idle_expires_at": "2026-08-26 00:10:00",
                    "absolute_expires_at": "2026-08-26 01:00:00",
                },
            )
            connection.execute(
                text(
                    "CREATE TABLE media_session_grants ("
                    "media_session_id VARCHAR(64) NOT NULL, resource_kind VARCHAR(32) NOT NULL, "
                    "resource_id VARCHAR(36) NOT NULL, granted_at DATETIME NOT NULL, "
                    "PRIMARY KEY (media_session_id, resource_kind, resource_id), "
                    "FOREIGN KEY(media_session_id) REFERENCES media_sessions (id) "
                    "ON DELETE CASCADE)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_media_session_grants_resource "
                    "ON media_session_grants (resource_kind, resource_id)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO media_session_grants "
                    "SELECT id, resource_kind, resource_id, created_at FROM media_sessions"
                )
            )
            _create_media_sessions_shadow(
                connection,
                include_legacy_resources=False,
            )
            if not authoritative_exists:
                connection.execute(text("DROP TABLE media_sessions"))
            connection.commit()
    finally:
        engine.dispose()

    command.upgrade(config, "0005_media_session_grants")

    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        assert "_alembic_tmp_media_sessions" not in inspector.get_table_names()
        assert "ix_media_sessions_expires" in {
            index["name"] for index in inspector.get_indexes("media_sessions")
        }
        assert {"resource_kind", "resource_id"}.isdisjoint(
            column["name"] for column in inspector.get_columns("media_sessions")
        )
        with engine.connect() as connection:
            session_id = connection.execute(
                text("SELECT id FROM media_sessions WHERE id = 'upgrade-shadow-token'")
            ).scalar_one()
            grant = connection.execute(
                text(
                    "SELECT resource_kind, resource_id FROM media_session_grants "
                    "WHERE media_session_id = 'upgrade-shadow-token'"
                )
            ).one()
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert session_id == "upgrade-shadow-token"
        assert tuple(grant) == ("artifact_revision", revision_id)
        assert version == "0005_media_session_grants"
    finally:
        engine.dispose()


@pytest.mark.parametrize("authoritative_exists", (True, False))
def test_0005_downgrade_recovers_copied_alembic_batch_shadow(
    tmp_path: Path,
    authoritative_exists: bool,
) -> None:
    database_path = tmp_path / f"downgrade-shadow-{authoritative_exists}.sqlite3"
    config = _config(database_path)
    command.upgrade(config, "0005_media_session_grants")
    revision_id = str(uuid4())
    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.execute(
                text(
                    "INSERT INTO media_sessions "
                    "(id, subject, created_at, last_used_at, idle_expires_at, "
                    "absolute_expires_at, revoked_at) VALUES "
                    "('downgrade-shadow-token', 'local', :created_at, :created_at, "
                    ":idle_expires_at, :absolute_expires_at, NULL)"
                ),
                {
                    "created_at": "2026-08-26 00:00:00",
                    "idle_expires_at": "2026-08-26 00:10:00",
                    "absolute_expires_at": "2026-08-26 01:00:00",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO media_session_grants "
                    "(media_session_id, resource_kind, resource_id, granted_at) VALUES "
                    "('downgrade-shadow-token', 'artifact_revision', :resource_id, "
                    ":granted_at)"
                ),
                {
                    "resource_id": revision_id,
                    "granted_at": "2026-08-26 00:00:00",
                },
            )
            connection.execute(
                text("ALTER TABLE media_sessions ADD COLUMN resource_kind VARCHAR(32)")
            )
            connection.execute(
                text("ALTER TABLE media_sessions ADD COLUMN resource_id VARCHAR(36)")
            )
            connection.execute(
                text(
                    "UPDATE media_sessions SET resource_kind = 'artifact_revision', "
                    "resource_id = :resource_id"
                ),
                {"resource_id": revision_id},
            )
            connection.execute(text("DROP INDEX ix_media_session_grants_resource"))
            connection.execute(text("DROP TABLE media_session_grants"))
            _create_media_sessions_shadow(
                connection,
                include_legacy_resources=True,
            )
            if not authoritative_exists:
                connection.execute(text("DROP TABLE media_sessions"))
            connection.commit()
    finally:
        engine.dispose()

    command.downgrade(config, "0004_media_lifecycle")

    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        assert "_alembic_tmp_media_sessions" not in inspector.get_table_names()
        assert "ix_media_sessions_expires" in {
            index["name"] for index in inspector.get_indexes("media_sessions")
        }
        columns = {column["name"]: column for column in inspector.get_columns("media_sessions")}
        assert columns["resource_kind"]["nullable"] is False
        assert columns["resource_id"]["nullable"] is False
        with engine.connect() as connection:
            restored = connection.execute(
                text(
                    "SELECT resource_kind, resource_id FROM media_sessions "
                    "WHERE id = 'downgrade-shadow-token'"
                )
            ).one()
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert tuple(restored) == ("artifact_revision", revision_id)
        assert version == "0004_media_lifecycle"
    finally:
        engine.dispose()
