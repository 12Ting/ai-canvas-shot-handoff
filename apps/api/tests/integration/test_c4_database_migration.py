from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


C3_REVISION = "0011_c3_model_profiles_agent_proposals"
C4_REVISION = "0012_c4_prompt_profile_batches"
C4_TABLES = {
    "c4_mutation_receipts",
    "prompt_profile_extractions",
    "prompt_profile_certifications",
    "prompt_batches",
    "prompt_batch_items",
    "prompt_batch_item_attempts",
    "prompt_batch_item_decisions",
}


def _alembic_config(database_path: Path) -> Config:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def test_0012_supports_fresh_upgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "fresh.sqlite3"
    command.upgrade(_alembic_config(database_path), C4_REVISION)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        inspector = inspect(engine)
        assert C4_TABLES <= set(inspector.get_table_names())
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == C4_REVISION
    finally:
        engine.dispose()


def test_0012_preserves_0011_data_and_has_required_integrity(tmp_path: Path) -> None:
    database_path = tmp_path / "upgrade.sqlite3"
    config = _alembic_config(database_path)
    command.upgrade(config, C3_REVISION)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO providers (id, provider_key, adapter_kind, created_at) "
                    "VALUES ('10000000-0000-4000-8000-000000000001', 'c3-loopback', "
                    "'OPENAI_COMPATIBLE_CHAT_JSON_V1', CURRENT_TIMESTAMP)"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, C4_REVISION)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        inspector = inspect(engine)
        assert C4_TABLES <= set(inspector.get_table_names())
        assert {"item_id", "attempt_number"} <= {
            column["name"]
            for column in inspector.get_columns("prompt_batch_item_attempts")
        }
        assert {"item_id", "decision_sequence"} <= {
            column["name"]
            for column in inspector.get_columns("prompt_batch_item_decisions")
        }
        assert any(
            set(index["column_names"]) == {"item_id", "attempt_number"}
            and index["unique"]
            for index in inspector.get_indexes("prompt_batch_item_attempts")
        ) or any(
            set(constraint["column_names"]) == {"item_id", "attempt_number"}
            for constraint in inspector.get_unique_constraints(
                "prompt_batch_item_attempts"
            )
        )
        certification_fks = inspector.get_foreign_keys("prompt_profile_certifications")
        assert any(
            fk["referred_table"] == "resource_revisions"
            and fk["options"].get("ondelete") == "RESTRICT"
            for fk in certification_fks
        )
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT provider_key FROM providers WHERE id = :id"),
                {"id": "10000000-0000-4000-8000-000000000001"},
            ) == "c3-loopback"

            trigger_names = set(
                connection.scalars(
                    text(
                        "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                        "AND name LIKE 'c4_%'"
                    )
                )
            )
            assert {
                "c4_prompt_profile_certifications_no_update",
                "c4_prompt_profile_certifications_no_delete",
                "c4_prompt_batch_items_subject_immutable",
                "c4_prompt_batch_item_decisions_no_update",
                "c4_prompt_batch_item_decisions_no_delete",
            } <= trigger_names
    finally:
        engine.dispose()


def test_0012_enforces_unique_certification_attempt_and_decision_sequences(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "constraints.sqlite3"
    command.upgrade(_alembic_config(database_path), C4_REVISION)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=OFF"))
            extraction_values = {
                "id": "20000000-0000-4000-8000-000000000001",
                "sha": "a" * 64,
            }
            connection.execute(
                text(
                    "INSERT INTO prompt_profile_extractions "
                    "(id, project_id, correlation_id, branch_id, branch_head_version, "
                    "selection_version, graph_revision_id, graph_revision_sha256, "
                    "config_revision_id, config_revision_sha256, model_profile_revision_id, "
                    "model_profile_revision_sha256, source_node_id, source_document_sha256, "
                    "provider_payload_json, provider_payload_sha256, run_request_id, state, "
                    "created_at, updated_at) VALUES "
                    "(:id, 'x', 'x', 'x', 1, 1, 'x', :sha, 'x', :sha, 'x', :sha, "
                    "'x', :sha, '{}', :sha, 'x', 'SUCCEEDED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                extraction_values,
            )
            certification_values = {
                "id": "20000000-0000-4000-8000-000000000002",
                "revision_id": "20000000-0000-4000-8000-000000000003",
                "extraction_id": extraction_values["id"],
                "sha": "b" * 64,
            }
            connection.execute(
                text(
                    "INSERT INTO prompt_profile_certifications "
                    "(id, resource_revision_id, source_example_sha256, extractor_schema_version, "
                    "rule_program_version, rule_code_set_sha256, profile_payload_sha256, "
                    "extraction_id, created_at) VALUES "
                    "(:id, :revision_id, :sha, 1, 1, :sha, :sha, :extraction_id, CURRENT_TIMESTAMP)"
                ),
                certification_values,
            )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO prompt_profile_certifications "
                        "(id, resource_revision_id, source_example_sha256, extractor_schema_version, "
                        "rule_program_version, rule_code_set_sha256, profile_payload_sha256, "
                        "extraction_id, created_at) VALUES "
                        "('duplicate', :revision_id, :sha, 1, 1, :sha, :sha, :extraction_id, CURRENT_TIMESTAMP)"
                    ),
                    certification_values,
                )
    finally:
        engine.dispose()


def test_0012_certification_and_decisions_are_append_only(tmp_path: Path) -> None:
    database_path = tmp_path / "immutable.sqlite3"
    command.upgrade(_alembic_config(database_path), C4_REVISION)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=OFF"))
            values = {"id": "30000000-0000-4000-8000-000000000001", "sha": "c" * 64}
            connection.execute(
                text(
                    "INSERT INTO prompt_profile_certifications "
                    "(id, resource_revision_id, source_example_sha256, extractor_schema_version, "
                    "rule_program_version, rule_code_set_sha256, profile_payload_sha256, "
                    "extraction_id, created_at) VALUES "
                    "(:id, 'revision', :sha, 1, 1, :sha, :sha, 'extraction', CURRENT_TIMESTAMP)"
                ),
                values,
            )
            with pytest.raises(IntegrityError, match="certifications are append-only"):
                connection.execute(
                    text(
                        "UPDATE prompt_profile_certifications SET rule_program_version = 2 "
                        "WHERE id = :id"
                    ),
                    values,
                )
            connection.execute(
                text(
                    "INSERT INTO prompt_batch_item_attempts "
                    "(id, item_id, attempt_number, run_request_id, provider_payload_json, "
                    "provider_payload_sha256, state, output_json, output_sha256, "
                    "compiled_document_json, compiled_document_sha256, created_at, updated_at, "
                    "completed_at) VALUES "
                    "('attempt', 'item', 1, 'run', '{}', :sha, 'SUCCEEDED', '{}', :sha, "
                    "'{}', :sha, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                values,
            )
            with pytest.raises(IntegrityError, match="terminal attempt is immutable"):
                connection.execute(
                    text(
                        "UPDATE prompt_batch_item_attempts SET compiled_document_json = :changed "
                        "WHERE id = 'attempt'"
                    ),
                    {"changed": '{"x":1}'},
                )
    finally:
        engine.dispose()
