from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from ai_canvas.contracts.graph import GraphSnapshotV1
from ai_canvas.domain.graph.snapshots import canonical_snapshot
from ai_canvas.infrastructure.db.session import create_sqlite_engine, run_migrations


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
DOCUMENT_ID = "22222222-2222-4222-8222-222222222222"
GRAPH_REVISION_ID = "33333333-3333-4333-8333-333333333333"
MUTATION_ID = "44444444-4444-4444-8444-444444444444"
IMPORT_ID = "55555555-5555-4555-8555-555555555555"
CLIENT_IMPORT_ID = "66666666-6666-4666-8666-666666666666"
ARTIFACT_ID = "77777777-7777-4777-8777-777777777777"
ARTIFACT_REVISION_ID = "88888888-8888-4888-8888-888888888888"
BLOB_SHA256 = "a" * 64
NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)
FOUNDATION_ROW_QUERIES = {
    "projects": "SELECT id, name, created_at, updated_at FROM projects ORDER BY id",
    "graph_documents": (
        "SELECT id, project_id, active_revision_id, created_at "
        "FROM graph_documents ORDER BY id"
    ),
    "graph_revisions": (
        "SELECT id, graph_document_id, parent_revision_id, sequence, client_mutation_id, "
        "snapshot_json, snapshot_hash, created_at FROM graph_revisions "
        "ORDER BY graph_document_id, sequence, id"
    ),
    "project_view_states": (
        "SELECT project_id, viewport_json, updated_at FROM project_view_states "
        "ORDER BY project_id"
    ),
}


def _alembic_config(database_path: Path) -> Config:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def _fixed_v1_snapshot() -> tuple[str, str]:
    fixture_path = Path(__file__).parents[1] / "fixtures" / "graph_snapshot_v1.json"
    snapshot_json, snapshot_hash = canonical_snapshot(
        GraphSnapshotV1.model_validate_json(fixture_path.read_text(encoding="utf-8"))
    )
    assert snapshot_hash == "d6d67b276ed51f289005bb259ed2e3f906e5ef9dc2a78bcfeb77816935656381"
    return snapshot_json, snapshot_hash


def _insert_foundation_row(database_path: Path) -> tuple[str, str]:
    snapshot_json, snapshot_hash = _fixed_v1_snapshot()
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects (id, name, created_at, updated_at) "
                    "VALUES (:id, :name, :created_at, :updated_at)"
                ),
                {
                    "id": PROJECT_ID,
                    "name": "Fixed V1",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO graph_documents (id, project_id, active_revision_id, created_at) "
                    "VALUES (:id, :project_id, NULL, :created_at)"
                ),
                {"id": DOCUMENT_ID, "project_id": PROJECT_ID, "created_at": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO graph_revisions "
                    "(id, graph_document_id, parent_revision_id, sequence, client_mutation_id, "
                    "snapshot_json, snapshot_hash, created_at) VALUES "
                    "(:id, :document_id, NULL, 0, :mutation_id, :snapshot_json, :snapshot_hash, "
                    ":created_at)"
                ),
                {
                    "id": GRAPH_REVISION_ID,
                    "document_id": DOCUMENT_ID,
                    "mutation_id": MUTATION_ID,
                    "snapshot_json": snapshot_json,
                    "snapshot_hash": snapshot_hash,
                    "created_at": NOW,
                },
            )
            connection.execute(
                text(
                    "UPDATE graph_documents SET active_revision_id = :revision_id WHERE id = :id"
                ),
                {"revision_id": GRAPH_REVISION_ID, "id": DOCUMENT_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO project_view_states (project_id, viewport_json, updated_at) "
                    "VALUES (:project_id, :viewport_json, :updated_at)"
                ),
                {
                    "project_id": PROJECT_ID,
                    "viewport_json": '{"x":0,"y":0,"zoom":1}',
                    "updated_at": NOW,
                },
            )
    finally:
        engine.dispose()
    return snapshot_json, snapshot_hash


def _foreign_key_signature(item: dict[str, object]) -> tuple[tuple[str, ...], str, tuple[str, ...], str | None]:
    options = item.get("options") or {}
    assert isinstance(options, dict)
    return (
        tuple(item["constrained_columns"]),
        str(item["referred_table"]),
        tuple(item["referred_columns"]),
        options.get("ondelete"),
    )


def _normalized_sql(sqltext: object) -> str:
    return " ".join(str(sqltext).split())


def _check_signatures(items: list[dict[str, object]]) -> dict[str, str]:
    signatures: dict[str, str] = {}
    for item in items:
        name = item["name"]
        assert isinstance(name, str)
        signatures[name] = _normalized_sql(item["sqltext"])
    return signatures


def _index_signatures(
    items: list[dict[str, object]],
) -> dict[str, tuple[tuple[str, ...], bool]]:
    signatures: dict[str, tuple[tuple[str, ...], bool]] = {}
    for item in items:
        name = item["name"]
        assert isinstance(name, str)
        signatures[name] = (tuple(item["column_names"]), bool(item["unique"]))
    return signatures


def _foundation_rows(database_path: Path) -> dict[str, list[tuple[object, ...]]]:
    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            return {
                table_name: [tuple(row) for row in connection.execute(text(query)).all()]
                for table_name, query in FOUNDATION_ROW_QUERIES.items()
            }
    finally:
        engine.dispose()


def test_migration_creates_exact_artifact_schema_and_safe_pragmas(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "canvas.sqlite3"
    assert not database_path.parent.exists()

    run_migrations(database_path)

    assert database_path.parent.is_dir()
    assert database_path.is_file()
    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == {
            "agent_proposals",
            "alembic_version",
            "approval_decisions",
            "artifact_revisions",
            "artifact_renditions",
            "artifacts",
            "c4_mutation_receipts",
            "config_change_sets",
            "control_config_documents",
            "control_config_revisions",
            "dry_run_receipts",
            "egress_plans",
            "event_consumer_cursors",
            "experience_events",
            "external_root_grants",
            "external_snapshots",
            "gc_plan_entries",
            "gc_plan_targets",
            "gc_plans",
                "graph_change_sets",
                "graph_branch_heads",
                "graph_branches",
                "graph_documents",
                "graph_promotions",
                "graph_revisions",
                "graph_selection_pointers",
            "handoff_projects",
            "import_sessions",
            "large_upload_parts",
            "large_upload_sessions",
            "maintenance_jobs",
            "media_session_grants",
                "media_sessions",
                "model_profile_revisions",
                "model_profiles",
                "node_materialization_states",
                "project_view_states",
                "projects",
                "providers",
                "prompt_profile_extractions",
                "prompt_profile_certifications",
                "prompt_batches",
                "prompt_batch_items",
                "prompt_batch_item_attempts",
                "prompt_batch_item_decisions",
                "quarantine_entries",
            "quarantine_targets",
            "resource_revision_artifacts",
            "resource_revision_tags",
            "resource_revisions",
            "resources",
            "run_authorizations",
            "run_requests",
            "storage_reservations",
            "stored_blobs",
        }
        assert {column["name"] for column in inspector.get_columns("stored_blobs")} == {
            "sha256",
            "byte_size",
            "media_kind",
            "media_type",
            "metadata_json",
            "created_at",
            "physical_bytes",
            "logical_ref_count",
            "state",
            "quarantine_until",
        }
        assert {column["name"] for column in inspector.get_columns("artifacts")} == {
            "id",
            "origin_project_id",
            "kind",
            "created_at",
        }
        assert {column["name"] for column in inspector.get_columns("import_sessions")} == {
            "id",
            "source_project_id",
            "client_import_id",
            "replace_revision_id",
            "requested_media_kind",
            "original_filename",
            "declared_content_type",
            "expected_byte_size",
            "state",
            "received_byte_size",
            "computed_sha256",
            "failure_code",
            "discarded_at",
            "discard_cleanup_pending",
            "created_at",
            "updated_at",
        }
        assert {column["name"] for column in inspector.get_columns("artifact_revisions")} == {
            "id",
            "artifact_id",
            "sequence",
            "blob_sha256",
            "storage_kind",
            "media_health",
            "external_snapshot_id",
            "source_kind",
            "source_project_id",
            "source_import_session_id",
            "display_filename",
            "discarded_at",
            "created_at",
        }

        assert {_foreign_key_signature(item) for item in inspector.get_foreign_keys("artifacts")} == {
            (("origin_project_id",), "projects", ("id",), "RESTRICT")
        }
        assert {
            _foreign_key_signature(item) for item in inspector.get_foreign_keys("import_sessions")
        } == {(("source_project_id",), "projects", ("id",), "RESTRICT")}
        assert {
            _foreign_key_signature(item) for item in inspector.get_foreign_keys("artifact_revisions")
        } == {
            (("artifact_id",), "artifacts", ("id",), "RESTRICT"),
            (("blob_sha256",), "stored_blobs", ("sha256",), "RESTRICT"),
            (("external_snapshot_id",), "external_snapshots", ("id",), "RESTRICT"),
            (("source_project_id",), "projects", ("id",), "RESTRICT"),
            (("source_import_session_id",), "import_sessions", ("id",), "RESTRICT"),
        }
        assert {
            _foreign_key_signature(item) for item in inspector.get_foreign_keys("graph_revisions")
        } == {
            (("graph_document_id",), "graph_documents", ("id",), "CASCADE"),
            (
                ("graph_document_id", "parent_revision_id"),
                "graph_revisions",
                ("graph_document_id", "id"),
                None,
            ),
        }

        assert _check_signatures(inspector.get_check_constraints("stored_blobs")) == {
            "ck_stored_blobs_byte_size_positive": "byte_size > 0",
            "ck_stored_blobs_physical_bytes_nonnegative": "physical_bytes >= 0",
            "ck_stored_blobs_logical_ref_count_nonnegative": "logical_ref_count >= 0",
            "ck_stored_blobs_lifecycle_state": (
                "state IN ('ACTIVE', 'QUARANTINED', 'PURGED')"
            ),
            "ck_stored_blobs_lifecycle_shape": (
                "(state = 'ACTIVE' AND physical_bytes = byte_size AND "
                "quarantine_until IS NULL) OR (state = 'QUARANTINED' AND "
                "physical_bytes = byte_size AND logical_ref_count = 0 AND "
                "quarantine_until IS NOT NULL) OR (state = 'PURGED' AND "
                "physical_bytes = 0 AND logical_ref_count = 0 AND "
                "quarantine_until IS NULL)"
            ),
            "ck_stored_blobs_media_kind": "media_kind IN ('image', 'video')",
            "ck_stored_blobs_media_type_nonempty": "length(media_type) > 0",
            "ck_stored_blobs_metadata_json_nonempty": "length(metadata_json) > 0",
            "ck_stored_blobs_sha256_length": (
                "length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'"
            ),
        }
        assert _check_signatures(inspector.get_check_constraints("artifacts")) == {
            "ck_artifacts_kind": "kind IN ('image', 'video')"
        }
        assert _check_signatures(inspector.get_check_constraints("import_sessions")) == {
            "ck_import_sessions_client_import_id_length": "length(client_import_id) = 36",
            "ck_import_sessions_computed_sha256_length": (
                "computed_sha256 IS NULL OR length(computed_sha256) = 64"
            ),
            "ck_import_sessions_expected_byte_size_positive": "expected_byte_size > 0",
            "ck_import_sessions_received_byte_size_nonnegative": "received_byte_size >= 0",
            "ck_import_sessions_replace_revision_id_length": (
                "replace_revision_id IS NULL OR length(replace_revision_id) = 36"
            ),
            "ck_import_sessions_requested_media_kind": (
                "requested_media_kind IN ('image', 'video')"
            ),
            "ck_import_sessions_state": (
                "state IN ('CREATED', 'RECEIVING', 'VALIDATING', 'COMPLETED', "
                "'FAILED_RETRYABLE', 'FAILED_FINAL')"
            ),
        }
        assert _check_signatures(inspector.get_check_constraints("artifact_revisions")) == {
            "ck_artifact_revisions_display_filename_nonempty": (
                "length(display_filename) > 0"
            ),
            "ck_artifact_revisions_import_provenance": (
                "source_kind != 'import' OR source_import_session_id IS NOT NULL"
            ),
            "ck_artifact_revisions_sequence_positive": "sequence > 0",
            "ck_artifact_revisions_source_kind_nonempty": "length(source_kind) > 0",
            "ck_artifact_revisions_storage_source": (
                "(storage_kind = 'MANAGED_BLOB' AND blob_sha256 IS NOT NULL AND "
                "external_snapshot_id IS NULL) OR (storage_kind = 'EXTERNAL_REFERENCE' "
                "AND blob_sha256 IS NULL AND external_snapshot_id IS NOT NULL)"
            ),
        }

        assert _index_signatures(inspector.get_indexes("stored_blobs")) == {
            "ix_stored_blobs_media_kind_created": (("media_kind", "created_at"), False)
        }
        assert _index_signatures(inspector.get_indexes("artifacts")) == {
            "ix_artifacts_origin_project_created": (("origin_project_id", "created_at"), False)
        }
        assert _index_signatures(inspector.get_indexes("import_sessions")) == {
            "ix_import_sessions_discard_cleanup_pending": (
                ("discard_cleanup_pending",),
                False,
            ),
            "ix_import_sessions_source_project_state_updated": (
                ("source_project_id", "state", "updated_at"),
                False,
            )
        }
        assert _index_signatures(inspector.get_indexes("artifact_revisions")) == {
            "ix_artifact_revisions_artifact_created": (("artifact_id", "created_at"), False),
            "ix_artifact_revisions_blob_discarded": (
                ("blob_sha256", "discarded_at"),
                False,
            ),
            "ix_artifact_revisions_blob_sha256": (("blob_sha256",), False),
            "ix_artifact_revisions_external_snapshot": (("external_snapshot_id",), False),
            "ix_artifact_revisions_source_project_created": (
                ("source_project_id", "created_at"),
                False,
            ),
        }
        assert {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("import_sessions")
        } == {("source_project_id", "client_import_id")}
        assert {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("artifact_revisions")
        } == {
            ("artifact_id", "sequence"),
            ("source_import_session_id",),
        }
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
            assert connection.execute(text("PRAGMA busy_timeout")).scalar_one() == 5000
            assert connection.execute(text("PRAGMA journal_mode")).scalar_one().lower() == "delete"
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO stored_blobs "
                        "(sha256, byte_size, media_kind, media_type, metadata_json, created_at, "
                        "physical_bytes, logical_ref_count, state, quarantine_until) "
                        "VALUES (:sha256, 1, 'image', 'image/png', '{}', :created_at, "
                        "1, 0, 'ACTIVE', NULL)"
                    ),
                    {"sha256": "g" * 64, "created_at": NOW},
                )
    finally:
        engine.dispose()


def test_upgrade_from_real_0001_preserves_fixed_v1_bytes(tmp_path: Path) -> None:
    database_path = tmp_path / "upgrade" / "canvas.sqlite3"
    database_path.parent.mkdir()
    config = _alembic_config(database_path)
    command.upgrade(config, "0001_graph_foundation")
    expected_snapshot = _insert_foundation_row(database_path)

    command.upgrade(config, "head")

    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            actual_snapshot = connection.execute(
                text(
                    "SELECT snapshot_json, snapshot_hash FROM graph_revisions WHERE id = :id"
                ),
                {"id": GRAPH_REVISION_ID},
            ).one()
    finally:
        engine.dispose()
    assert tuple(actual_snapshot) == expected_snapshot


def test_upgrade_from_0002_defaults_live_rows_and_downgrade_does_not_resurrect_tombstones(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "artifact-discard-upgrade" / "canvas.sqlite3"
    database_path.parent.mkdir()
    config = _alembic_config(database_path)
    command.upgrade(config, "0002_artifact_store")
    _insert_foundation_row(database_path)
    expected_foundation_rows = _foundation_rows(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO stored_blobs "
                    "(sha256, byte_size, media_kind, media_type, metadata_json, created_at) "
                    "VALUES (:sha256, 3, 'image', 'image/png', '{}', :created_at)"
                ),
                {"sha256": BLOB_SHA256, "created_at": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO artifacts (id, origin_project_id, kind, created_at) "
                    "VALUES (:id, :project_id, 'image', :created_at)"
                ),
                {"id": ARTIFACT_ID, "project_id": PROJECT_ID, "created_at": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO import_sessions "
                    "(id, source_project_id, client_import_id, requested_media_kind, "
                    "original_filename, expected_byte_size, state, received_byte_size, "
                    "computed_sha256, created_at, updated_at) VALUES "
                    "(:id, :project_id, :client_id, 'image', 'fixed.png', 3, 'COMPLETED', "
                    "3, :sha256, :created_at, :updated_at)"
                ),
                {
                    "id": IMPORT_ID,
                    "project_id": PROJECT_ID,
                    "client_id": CLIENT_IMPORT_ID,
                    "sha256": BLOB_SHA256,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO artifact_revisions "
                    "(id, artifact_id, sequence, blob_sha256, source_kind, source_project_id, "
                    "source_import_session_id, display_filename, created_at) VALUES "
                    "(:id, :artifact_id, 1, :sha256, 'import', :project_id, :import_id, "
                    "'fixed.png', :created_at)"
                ),
                {
                    "id": ARTIFACT_REVISION_ID,
                    "artifact_id": ARTIFACT_ID,
                    "sha256": BLOB_SHA256,
                    "project_id": PROJECT_ID,
                    "import_id": IMPORT_ID,
                    "created_at": NOW,
                },
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            defaults = connection.execute(
                text(
                    "SELECT discarded_at, discard_cleanup_pending "
                    "FROM import_sessions WHERE id = :id"
                ),
                {"id": IMPORT_ID},
            ).one()
            revision_default = connection.execute(
                text(
                    "SELECT discarded_at FROM artifact_revisions WHERE id = :id"
                ),
                {"id": ARTIFACT_REVISION_ID},
            ).scalar_one()
            assert tuple(defaults) == (None, 0)
            assert revision_default is None
            connection.execute(
                text(
                    "UPDATE import_sessions SET discarded_at = :now, "
                    "discard_cleanup_pending = 1 WHERE id = :id"
                ),
                {"now": NOW, "id": IMPORT_ID},
            )
            connection.execute(
                text(
                    "UPDATE artifact_revisions SET discarded_at = :now WHERE id = :id"
                ),
                {"now": NOW, "id": ARTIFACT_REVISION_ID},
            )
    finally:
        engine.dispose()

    command.downgrade(config, "0002_artifact_store")
    engine = create_sqlite_engine(database_path)
    try:
        inspector = inspect(engine)
        assert "discarded_at" not in {
            column["name"] for column in inspector.get_columns("import_sessions")
        }
        assert "discarded_at" not in {
            column["name"] for column in inspector.get_columns("artifact_revisions")
        }
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM artifact_revisions")
            ).scalar_one() == 0
            assert connection.execute(text("SELECT count(*) FROM artifacts")).scalar_one() == 0
            assert connection.execute(text("SELECT count(*) FROM stored_blobs")).scalar_one() == 0
            assert connection.execute(text("SELECT count(*) FROM import_sessions")).scalar_one() == 0
    finally:
        engine.dispose()
    assert _foundation_rows(database_path) == expected_foundation_rows


def test_downgrade_to_0001_keeps_foundation_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "downgrade" / "canvas.sqlite3"
    database_path.parent.mkdir()
    config = _alembic_config(database_path)
    command.upgrade(config, "0001_graph_foundation")
    _insert_foundation_row(database_path)
    expected_foundation_rows = _foundation_rows(database_path)
    command.upgrade(config, "head")

    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO stored_blobs "
                    "(sha256, byte_size, media_kind, media_type, metadata_json, created_at, "
                    "physical_bytes, logical_ref_count, state, quarantine_until) "
                    "VALUES (:sha256, 3, 'image', 'image/png', :metadata_json, :created_at, "
                    "3, 1, 'ACTIVE', NULL)"
                ),
                {"sha256": BLOB_SHA256, "metadata_json": "{}", "created_at": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO artifacts (id, origin_project_id, kind, created_at) "
                    "VALUES (:id, :project_id, 'image', :created_at)"
                ),
                {"id": ARTIFACT_ID, "project_id": PROJECT_ID, "created_at": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO import_sessions "
                    "(id, source_project_id, client_import_id, replace_revision_id, "
                    "requested_media_kind, original_filename, declared_content_type, "
                    "expected_byte_size, state, received_byte_size, computed_sha256, "
                    "failure_code, created_at, updated_at) VALUES "
                    "(:id, :project_id, :client_id, NULL, 'image', 'fixed.png', 'image/png', "
                    "3, 'COMPLETED', 3, :sha256, NULL, :created_at, :updated_at)"
                ),
                {
                    "id": IMPORT_ID,
                    "project_id": PROJECT_ID,
                    "client_id": CLIENT_IMPORT_ID,
                    "sha256": BLOB_SHA256,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO artifact_revisions "
                    "(id, artifact_id, sequence, blob_sha256, source_kind, source_project_id, "
                    "source_import_session_id, display_filename, created_at) VALUES "
                    "(:id, :artifact_id, 1, :sha256, 'import', :project_id, :import_id, "
                    "'fixed.png', :created_at)"
                ),
                {
                    "id": ARTIFACT_REVISION_ID,
                    "artifact_id": ARTIFACT_ID,
                    "sha256": BLOB_SHA256,
                    "project_id": PROJECT_ID,
                    "import_id": IMPORT_ID,
                    "created_at": NOW,
                },
            )
    finally:
        engine.dispose()

    command.downgrade(config, "0001_graph_foundation")

    engine = create_sqlite_engine(database_path)
    try:
        assert set(inspect(engine).get_table_names()) == {
            "alembic_version",
            "graph_documents",
            "graph_revisions",
            "project_view_states",
            "projects",
        }
    finally:
        engine.dispose()
    assert _foundation_rows(database_path) == expected_foundation_rows


def test_project_provenance_is_restricted_not_cascade_deleted(tmp_path: Path) -> None:
    database_path = tmp_path / "provenance.sqlite3"
    run_migrations(database_path)
    _insert_foundation_row(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO stored_blobs "
                    "(sha256, byte_size, media_kind, media_type, metadata_json, created_at, "
                    "physical_bytes, logical_ref_count, state, quarantine_until) "
                    "VALUES (:sha256, 3, 'image', 'image/png', '{}', :created_at, "
                    "3, 1, 'ACTIVE', NULL)"
                ),
                {"sha256": BLOB_SHA256, "created_at": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO artifacts (id, origin_project_id, kind, created_at) "
                    "VALUES (:id, :project_id, 'image', :created_at)"
                ),
                {"id": ARTIFACT_ID, "project_id": PROJECT_ID, "created_at": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO import_sessions "
                    "(id, source_project_id, client_import_id, requested_media_kind, "
                    "original_filename, expected_byte_size, state, received_byte_size, "
                    "created_at, updated_at) VALUES "
                    "(:id, :project_id, :client_id, 'image', 'fixed.png', 3, 'COMPLETED', 3, "
                    ":created_at, :updated_at)"
                ),
                {
                    "id": IMPORT_ID,
                    "project_id": PROJECT_ID,
                    "client_id": CLIENT_IMPORT_ID,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO artifact_revisions "
                    "(id, artifact_id, sequence, blob_sha256, source_kind, source_project_id, "
                    "source_import_session_id, display_filename, created_at) VALUES "
                    "(:id, :artifact_id, 1, :sha256, 'import', :project_id, :import_id, "
                    "'fixed.png', :created_at)"
                ),
                {
                    "id": ARTIFACT_REVISION_ID,
                    "artifact_id": ARTIFACT_ID,
                    "sha256": BLOB_SHA256,
                    "project_id": PROJECT_ID,
                    "import_id": IMPORT_ID,
                    "created_at": NOW,
                },
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM projects WHERE id = :id"), {"id": PROJECT_ID}
                )

        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM artifacts")).scalar_one() == 1
            assert connection.execute(
                text("SELECT count(*) FROM artifact_revisions")
            ).scalar_one() == 1
            assert connection.execute(text("SELECT count(*) FROM import_sessions")).scalar_one() == 1
    finally:
        engine.dispose()
