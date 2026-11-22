from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

F1_REVISION = "0008_f1_control_integrity"
C1_TABLES = {
    "resources",
    "resource_revisions",
    "resource_revision_tags",
    "resource_revision_artifacts",
}
F1_TABLES = {
    "experience_events",
    "event_consumer_cursors",
    "graph_change_sets",
    "approval_decisions",
    "control_config_documents",
    "control_config_revisions",
    "config_change_sets",
    "run_requests",
    "egress_plans",
    "run_authorizations",
    "dry_run_receipts",
}


def _alembic_config(database_path: Path) -> Config:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def _run_migrations_to(database_path: Path, revision: str) -> None:
    command.upgrade(_alembic_config(database_path), revision)


def _schema_signature(database_path: Path) -> list[tuple[str, str, str, str | None]]:
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.connect() as connection:
            return [
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT type, name, tbl_name, sql FROM sqlite_master "
                        "WHERE name NOT LIKE 'sqlite_%' AND name != 'alembic_version' "
                        "ORDER BY type, name"
                    )
                )
            ]
    finally:
        engine.dispose()


def test_f1_upgrade_creates_append_only_event_spine_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"

    _run_migrations_to(database_path, F1_REVISION)

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        assert F1_TABLES <= set(inspect(engine).get_table_names())
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects (id, name, created_at, updated_at) "
                    "VALUES ('00000000-0000-4000-8000-000000000001', 'F1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO experience_events "
                    "(event_id, occurred_at, project_id, correlation_id, causation_event_id, "
                    "actor_kind, actor_id, subject_type, subject_id, event_type, schema_version, "
                    "payload_json, payload_sha256, envelope_sha256) VALUES "
                    "('00000000-0000-4000-8000-000000000002', CURRENT_TIMESTAMP, "
                    "'00000000-0000-4000-8000-000000000001', "
                    "'00000000-0000-4000-8000-000000000003', NULL, 'HUMAN', 'test', "
                    "'graph_change_set', '00000000-0000-4000-8000-000000000004', "
                    "'graph_change_set.requested', 1, '{}', :payload_hash, :envelope_hash)"
                ),
                {"payload_hash": "a" * 64, "envelope_hash": "b" * 64},
            )
            with pytest.raises(IntegrityError, match="experience_events"):
                connection.execute(
                    text("UPDATE experience_events SET event_type = 'x' WHERE stream_position = 1")
                )
            with pytest.raises(IntegrityError, match="experience_events"):
                connection.execute(text("DELETE FROM experience_events WHERE stream_position = 1"))
    finally:
        engine.dispose()


def test_graph_change_set_audit_rows_do_not_block_graph_history_integrity_checks(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"

    _run_migrations_to(database_path, F1_REVISION)

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        foreign_key_columns = {
            foreign_key["constrained_columns"][0]
            for foreign_key in inspect(engine).get_foreign_keys("graph_change_sets")
        }
        assert "base_graph_revision_id" not in foreign_key_columns
        assert "project_id" in foreign_key_columns
    finally:
        engine.dispose()


def test_f1_approved_subjects_and_decisions_have_database_immutability_guards(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"

    _run_migrations_to(database_path, F1_REVISION)

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.connect() as connection:
            trigger_names = set(
                connection.scalars(
                    text(
                        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'f1_%_immutable'"
                    )
                )
            )
        assert {
            "f1_graph_change_sets_content_immutable",
            "f1_graph_change_sets_delete_immutable",
            "f1_config_change_sets_content_immutable",
            "f1_config_change_sets_delete_immutable",
            "f1_approval_decisions_update_immutable",
            "f1_approval_decisions_delete_immutable",
            "f1_run_requests_content_immutable",
            "f1_egress_plans_update_immutable",
            "f1_egress_plans_delete_immutable",
            "f1_run_authorizations_binding_immutable",
            "f1_run_authorizations_delete_immutable",
            "f1_dry_run_receipts_update_immutable",
            "f1_dry_run_receipts_delete_immutable",
            "f1_graph_revisions_content_immutable",
            "f1_graph_revisions_delete_immutable",
            "f1_control_config_revisions_content_immutable",
            "f1_control_config_revisions_delete_immutable",
        } <= trigger_names
    finally:
        engine.dispose()


def test_final_f1_revision_does_not_contain_c1_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"

    _run_migrations_to(database_path, F1_REVISION)

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert F1_TABLES <= tables
        assert C1_TABLES.isdisjoint(tables)
    finally:
        engine.dispose()


def test_downgrade_from_head_restores_the_exact_0007_schema(tmp_path: Path) -> None:
    direct_path = tmp_path / "direct-0007.sqlite3"
    downgraded_path = tmp_path / "head-then-0007.sqlite3"

    command.upgrade(_alembic_config(direct_path), "0007_f1_event_spine")
    command.upgrade(_alembic_config(downgraded_path), "head")
    command.downgrade(_alembic_config(downgraded_path), "0007_f1_event_spine")

    direct_schema = [entry for entry in _schema_signature(direct_path) if entry[2] in F1_TABLES]
    downgraded_schema = [entry for entry in _schema_signature(downgraded_path) if entry[2] in F1_TABLES]
    assert downgraded_schema == direct_schema
