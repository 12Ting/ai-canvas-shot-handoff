from pathlib import Path

from sqlalchemy import create_engine, inspect

from ai_canvas.infrastructure.db.session import run_migrations


C1_TABLES = {
    "resources",
    "resource_revisions",
    "resource_revision_tags",
    "resource_revision_artifacts",
}


def test_c1_upgrade_creates_resource_tables_and_constraints(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        inspector = inspect(engine)
        assert C1_TABLES <= set(inspector.get_table_names())
        assert {"resource_id", "sequence"} in {
            frozenset(item["column_names"])
            for item in inspector.get_unique_constraints("resource_revisions")
        }
        assert {"resource_revision_id", "normalized_tag"} in {
            frozenset(item["column_names"])
            for item in inspector.get_unique_constraints("resource_revision_tags")
        }
        assert {"resource_revision_id", "slot", "position"} in {
            frozenset(item["column_names"])
            for item in inspector.get_unique_constraints("resource_revision_artifacts")
        }
        artifact_foreign_keys = inspector.get_foreign_keys("resource_revision_artifacts")
        artifact_revision_key = next(
            item for item in artifact_foreign_keys if item["referred_table"] == "artifact_revisions"
        )
        assert artifact_revision_key["options"]["ondelete"] == "RESTRICT"
    finally:
        engine.dispose()
