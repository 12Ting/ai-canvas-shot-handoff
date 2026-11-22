from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


C2_REVISION = "0010_c2_graph_branches"
C2_TABLES = {
    "graph_branches",
    "graph_branch_heads",
    "graph_selection_pointers",
    "graph_promotions",
    "node_materialization_states",
}

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
DOCUMENT_ID = "00000000-0000-4000-8000-000000000002"
REVISION_ID = "00000000-0000-4000-8000-000000000003"
CHANGE_SET_ID = "00000000-0000-4000-8000-000000000004"
MUTATION_ID = "00000000-0000-4000-8000-000000000005"
CORRELATION_ID = "00000000-0000-4000-8000-000000000006"


def _alembic_config(database_path: Path) -> Config:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def _seed_0009(database_path: Path) -> tuple[str, str, str]:
    command.upgrade(_alembic_config(database_path), "0009_c1_prompt_resources")
    snapshot_json = '{"schema_version":2,"nodes":[],"edges":[]}'
    snapshot_hash = "a" * 64
    changeset_hash = "b" * 64
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects (id, name, created_at, updated_at) VALUES "
                    "(:id, 'Existing project', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": PROJECT_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO graph_documents (id, project_id, active_revision_id, created_at) "
                    "VALUES (:id, :project_id, NULL, CURRENT_TIMESTAMP)"
                ),
                {"id": DOCUMENT_ID, "project_id": PROJECT_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO graph_revisions "
                    "(id, graph_document_id, parent_revision_id, sequence, client_mutation_id, "
                    "snapshot_json, snapshot_hash, created_at) VALUES "
                    "(:id, :document_id, NULL, 0, :mutation_id, :snapshot_json, "
                    ":snapshot_hash, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": REVISION_ID,
                    "document_id": DOCUMENT_ID,
                    "mutation_id": MUTATION_ID,
                    "snapshot_json": snapshot_json,
                    "snapshot_hash": snapshot_hash,
                },
            )
            connection.execute(
                text(
                    "UPDATE graph_documents SET active_revision_id = :revision_id WHERE id = :id"
                ),
                {"revision_id": REVISION_ID, "id": DOCUMENT_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO graph_change_sets "
                    "(id, project_id, correlation_id, base_graph_revision_id, client_mutation_id, "
                    "snapshot_json, snapshot_hash, diff_json, inverse_operations_json, risk_json, "
                    "changeset_sha256, state, approval_id, applied_graph_revision_id, created_at, "
                    "applied_at) VALUES "
                    "(:id, :project_id, :correlation_id, :revision_id, :mutation_id, "
                    ":snapshot_json, :snapshot_hash, '[]', '[]', '{}', :changeset_hash, "
                    "'PENDING_APPROVAL', NULL, NULL, CURRENT_TIMESTAMP, NULL)"
                ),
                {
                    "id": CHANGE_SET_ID,
                    "project_id": PROJECT_ID,
                    "correlation_id": CORRELATION_ID,
                    "revision_id": REVISION_ID,
                    "mutation_id": "00000000-0000-4000-8000-000000000007",
                    "snapshot_json": snapshot_json,
                    "snapshot_hash": snapshot_hash,
                    "changeset_hash": changeset_hash,
                },
            )
    finally:
        engine.dispose()
    return snapshot_json, snapshot_hash, changeset_hash


def test_0010_backfills_main_branch_without_rewriting_graph(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    snapshot_json, snapshot_hash, changeset_hash = _seed_0009(database_path)

    command.upgrade(_alembic_config(database_path), C2_REVISION)

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        inspector = inspect(engine)
        assert C2_TABLES <= set(inspector.get_table_names())
        change_set_columns = {
            column["name"]: column for column in inspector.get_columns("graph_change_sets")
        }
        assert {
            "application_target_json",
            "impact_json",
            "impact_sha256",
            "created_branch_id",
            "applied_branch_id",
        } <= change_set_columns.keys()
        assert all(
            change_set_columns[column]["nullable"]
            for column in (
                "application_target_json",
                "impact_json",
                "impact_sha256",
                "created_branch_id",
                "applied_branch_id",
            )
        )

        with engine.connect() as connection:
            branch = connection.execute(text("SELECT * FROM graph_branches")).mappings().one()
            head = connection.execute(text("SELECT * FROM graph_branch_heads")).mappings().one()
            pointer = connection.execute(
                text("SELECT * FROM graph_selection_pointers")
            ).mappings().one()
            revision = connection.execute(
                text("SELECT snapshot_json, snapshot_hash FROM graph_revisions WHERE id = :id"),
                {"id": REVISION_ID},
            ).mappings().one()
            change_set = connection.execute(
                text(
                    "SELECT changeset_sha256, application_target_json, impact_json, "
                    "impact_sha256, created_branch_id, applied_branch_id "
                    "FROM graph_change_sets WHERE id = :id"
                ),
                {"id": CHANGE_SET_ID},
            ).mappings().one()

        assert branch["graph_document_id"] == DOCUMENT_ID
        assert branch["name"] == "主线"
        assert branch["normalized_name"] == "主线"
        assert branch["kind"] == "MAIN"
        assert branch["forked_from_branch_id"] is None
        assert branch["forked_from_revision_id"] == REVISION_ID
        assert head["branch_id"] == branch["id"]
        assert head["graph_revision_id"] == REVISION_ID
        assert head["version"] == 1
        assert pointer["graph_document_id"] == DOCUMENT_ID
        assert pointer["branch_id"] == branch["id"]
        assert pointer["graph_revision_id"] == REVISION_ID
        assert pointer["version"] == 1
        assert revision == {"snapshot_json": snapshot_json, "snapshot_hash": snapshot_hash}
        assert change_set["changeset_sha256"] == changeset_hash
        assert all(
            change_set[column] is None
            for column in (
                "application_target_json",
                "impact_json",
                "impact_sha256",
                "created_branch_id",
                "applied_branch_id",
            )
        )
    finally:
        engine.dispose()


def test_0010_rejects_a_document_with_a_cross_document_active_revision(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    _seed_0009(database_path)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects (id, name, created_at, updated_at) VALUES "
                    "('00000000-0000-4000-8000-000000000010', 'Corrupt', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO graph_documents (id, project_id, active_revision_id, created_at) "
                    "VALUES ('00000000-0000-4000-8000-000000000011', "
                    "'00000000-0000-4000-8000-000000000010', :foreign_revision, "
                    "CURRENT_TIMESTAMP)"
                ),
                {"foreign_revision": REVISION_ID},
            )
    finally:
        engine.dispose()

    try:
        command.upgrade(_alembic_config(database_path), C2_REVISION)
    except Exception as error:  # Alembic wraps the migration's fail-closed error.
        assert "active revision" in str(error).lower()
    else:
        raise AssertionError("corrupt graph document unexpectedly migrated")
