from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


C3_REVISION = "0011_c3_model_profiles_agent_proposals"
C3_TABLES = {
    "providers",
    "model_profiles",
    "model_profile_revisions",
    "agent_proposals",
}


def _alembic_config(database_path: Path) -> Config:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def test_0011_preserves_0010_rows_and_adds_c3_storage(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    command.upgrade(_alembic_config(database_path), "0010_c2_graph_branches")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects (id, name, created_at, updated_at) VALUES "
                    "('10000000-0000-4000-8000-000000000001', 'C3 witness', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO control_config_documents (id, active_revision_id, created_at) "
                    "VALUES ('10000000-0000-4000-8000-000000000002', NULL, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO control_config_revisions "
                    "(id, document_id, parent_revision_id, sequence, config_json, config_sha256, created_at) "
                    "VALUES ('10000000-0000-4000-8000-000000000003', "
                    "'10000000-0000-4000-8000-000000000002', NULL, 1, "
                    ":config_json, :config_hash, CURRENT_TIMESTAMP)"
                ),
                {"config_json": '{"dry_run_max_tasks":8}', "config_hash": "a" * 64},
            )
            connection.execute(
                text(
                    "UPDATE control_config_documents SET active_revision_id = "
                    "'10000000-0000-4000-8000-000000000003' WHERE id = "
                    "'10000000-0000-4000-8000-000000000002'"
                )
            )
            before = connection.execute(
                text(
                    "SELECT config_json, config_sha256 FROM control_config_revisions WHERE id = "
                    "'10000000-0000-4000-8000-000000000003'"
                )
            ).one()
    finally:
        engine.dispose()

    command.upgrade(_alembic_config(database_path), C3_REVISION)

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        inspector = inspect(engine)
        assert C3_TABLES <= set(inspector.get_table_names())
        assert {
            "model_profile_revision_id",
            "model_profile_revision_sha256",
            "branch_id",
            "branch_head_version",
            "selection_version",
            "scope_json",
            "scope_sha256",
        } <= {column["name"] for column in inspector.get_columns("run_requests")}
        assert {
            "model_profile_revision_id",
            "endpoint_identity_sha256",
            "preview_json",
            "cost_mode",
            "currency",
            "cost_bound_minor",
        } <= {column["name"] for column in inspector.get_columns("egress_plans")}
        assert {
            "model_profile_revision_id",
            "model_profile_revision_sha256",
            "branch_id",
            "branch_head_version",
            "selection_version",
            "scope_sha256",
            "endpoint_identity_sha256",
            "cost_mode",
            "currency",
        } <= {column["name"] for column in inspector.get_columns("run_authorizations")}

        with engine.begin() as connection:
            after = connection.execute(
                text(
                    "SELECT config_json, config_sha256 FROM control_config_revisions WHERE id = "
                    "'10000000-0000-4000-8000-000000000003'"
                )
            ).one()
            assert after == before
            connection.execute(
                text(
                    "INSERT INTO providers "
                    "(id, provider_key, adapter_kind, created_at) VALUES "
                    "('20000000-0000-4000-8000-000000000001', 'loopback', "
                    "'OPENAI_COMPATIBLE_CHAT_JSON_V1', CURRENT_TIMESTAMP)"
                )
            )
            with pytest.raises(IntegrityError, match="providers are append-only"):
                connection.execute(
                    text("DELETE FROM providers WHERE provider_key = 'loopback'")
                )
    finally:
        engine.dispose()


def test_0011_remains_an_addressable_revision(tmp_path: Path) -> None:
    database_path = tmp_path / "c3.sqlite3"
    command.upgrade(_alembic_config(database_path), C3_REVISION)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == C3_REVISION
    finally:
        engine.dispose()
