"""Persist physical blob lifecycle and grouped GC targets.

Revision ID: 0006_blob_lifecycle
Revises: 0005_media_session_grants
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0006_blob_lifecycle"
down_revision: str | None = "0005_media_session_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STORED_BLOB_LEGACY_COLUMNS = {
    "sha256",
    "byte_size",
    "media_kind",
    "media_type",
    "metadata_json",
    "created_at",
}
_STORED_BLOB_FINAL_COLUMNS = _STORED_BLOB_LEGACY_COLUMNS | {
    "physical_bytes",
    "logical_ref_count",
    "state",
    "quarantine_until",
}
_STORED_BLOB_FINAL_NOT_NULL = _STORED_BLOB_FINAL_COLUMNS - {"quarantine_until"}
_STORED_BLOB_FINAL_CHECKS = {
    "ck_stored_blobs_physical_bytes_nonnegative",
    "ck_stored_blobs_logical_ref_count_nonnegative",
    "ck_stored_blobs_lifecycle_state",
    "ck_stored_blobs_lifecycle_shape",
}
_QUARANTINE_LEGACY_COLUMNS = {
    "id",
    "target_kind",
    "target_id",
    "original_path",
    "quarantine_path",
    "quarantined_at",
    "quarantine_until",
    "restored_at",
    "purged_at",
}
_QUARANTINE_FINAL_COLUMNS = _QUARANTINE_LEGACY_COLUMNS | {
    "operation_state",
    "operation_error",
    "operation_started_at",
    "operation_updated_at",
}
_QUARANTINE_FINAL_NOT_NULL = _QUARANTINE_FINAL_COLUMNS - {
    "restored_at",
    "purged_at",
    "operation_error",
}
_QUARANTINE_FINAL_CHECKS = {"ck_quarantine_entries_operation_state"}


def _tables(connection: sa.Connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names())


def _columns(connection: sa.Connection, table: str) -> dict[str, dict[str, object]]:
    return {str(column["name"]): column for column in sa.inspect(connection).get_columns(table)}


def _checks(connection: sa.Connection, table: str) -> set[str]:
    return {
        str(item["name"])
        for item in sa.inspect(connection).get_check_constraints(table)
        if item.get("name") is not None
    }


def _indexes(connection: sa.Connection, table: str) -> set[str]:
    return {
        str(item["name"])
        for item in sa.inspect(connection).get_indexes(table)
        if item.get("name") is not None
    }


def _ensure_index(
    connection: sa.Connection,
    table: str,
    name: str,
    columns: list[str],
) -> None:
    if name not in _indexes(connection, table):
        op.create_index(name, table, columns)


def _validate_batch_shadow(
    connection: sa.Connection,
    table: str,
    *,
    expected_columns: set[str],
    primary_key: list[str],
    required_not_null: set[str],
    required_checks: set[str],
) -> None:
    shadow = f"_alembic_tmp_{table}"
    tables = _tables(connection)
    if shadow not in tables:
        return
    shadow_columns = _columns(connection, shadow)
    if set(shadow_columns) != expected_columns:
        raise RuntimeError(f"cannot recover {table} batch shadow with unexpected columns")
    if sa.inspect(connection).get_pk_constraint(shadow).get("constrained_columns") != primary_key:
        raise RuntimeError(f"cannot recover {table} batch shadow without expected primary key")
    if any(bool(shadow_columns[name]["nullable"]) for name in required_not_null):
        raise RuntimeError(f"cannot recover {table} batch shadow with nullable required columns")
    if not required_checks <= _checks(connection, shadow):
        raise RuntimeError(f"cannot recover {table} batch shadow without required checks")
    for name in sorted(required_not_null):
        if connection.execute(
            sa.text(f"SELECT 1 FROM {shadow} WHERE {name} IS NULL LIMIT 1")
        ).first() is not None:
            raise RuntimeError(f"cannot recover {table} batch shadow with null required data")


def _assert_matching_batch_shadow_rows(
    connection: sa.Connection,
    table: str,
    *,
    columns: set[str],
) -> None:
    shadow = f"_alembic_tmp_{table}"
    selected_columns = ", ".join(sorted(columns))
    for source, destination in ((table, shadow), (shadow, table)):
        mismatch = connection.execute(
            sa.text(
                f"SELECT 1 FROM (SELECT {selected_columns} FROM {source} "
                f"EXCEPT SELECT {selected_columns} FROM {destination}) LIMIT 1"
            )
        ).first()
        if mismatch is not None:
            raise RuntimeError(f"cannot recover {table} batch shadow with mismatched rows")


def _recover_batch_shadow(
    connection: sa.Connection,
    table: str,
    *,
    expected_columns: set[str],
    primary_key: list[str],
    required_not_null: set[str],
    required_checks: set[str],
) -> None:
    shadow = f"_alembic_tmp_{table}"
    tables = _tables(connection)
    if shadow not in tables:
        return
    _validate_batch_shadow(
        connection,
        table,
        expected_columns=expected_columns,
        primary_key=primary_key,
        required_not_null=required_not_null,
        required_checks=required_checks,
    )
    if table in tables:
        _assert_matching_batch_shadow_rows(
            connection,
            table,
            columns=expected_columns,
        )
        op.drop_table(shadow)
        return
    op.rename_table(shadow, table)


def _preflight_upgrade_batch_shadows(connection: sa.Connection) -> None:
    _validate_batch_shadow(
        connection,
        "stored_blobs",
        expected_columns=_STORED_BLOB_FINAL_COLUMNS,
        primary_key=["sha256"],
        required_not_null=_STORED_BLOB_FINAL_NOT_NULL,
        required_checks=_STORED_BLOB_FINAL_CHECKS,
    )
    _validate_batch_shadow(
        connection,
        "quarantine_entries",
        expected_columns=_QUARANTINE_FINAL_COLUMNS,
        primary_key=["id"],
        required_not_null=_QUARANTINE_FINAL_NOT_NULL,
        required_checks=_QUARANTINE_FINAL_CHECKS,
    )


def _ensure_stored_blob_schema(connection: sa.Connection) -> None:
    _recover_batch_shadow(
        connection,
        "stored_blobs",
        expected_columns=_STORED_BLOB_FINAL_COLUMNS,
        primary_key=["sha256"],
        required_not_null=_STORED_BLOB_FINAL_NOT_NULL,
        required_checks=_STORED_BLOB_FINAL_CHECKS,
    )
    _ensure_index(
        connection,
        "stored_blobs",
        "ix_stored_blobs_media_kind_created",
        ["media_kind", "created_at"],
    )
    columns = _columns(connection, "stored_blobs")
    if "physical_bytes" not in columns:
        op.add_column("stored_blobs", sa.Column("physical_bytes", sa.Integer(), nullable=True))
    if "logical_ref_count" not in columns:
        op.add_column("stored_blobs", sa.Column("logical_ref_count", sa.Integer(), nullable=True))
    if "state" not in columns:
        op.add_column("stored_blobs", sa.Column("state", sa.String(length=16), nullable=True))
    if "quarantine_until" not in columns:
        op.add_column("stored_blobs", sa.Column("quarantine_until", sa.DateTime(), nullable=True))

    _ensure_index(
        connection,
        "artifact_renditions",
        "ix_artifact_renditions_blob_sha256",
        ["blob_sha256"],
    )

    op.execute(
        sa.text(
            "UPDATE stored_blobs SET physical_bytes = byte_size "
            "WHERE physical_bytes IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE stored_blobs SET state = 'ACTIVE' WHERE state IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE stored_blobs SET logical_ref_count = ("
            "SELECT COUNT(*) FROM artifact_revisions "
            "WHERE artifact_revisions.storage_kind = 'MANAGED_BLOB' "
            "AND artifact_revisions.blob_sha256 = stored_blobs.sha256 "
            "AND artifact_revisions.discarded_at IS NULL"
            ") + ("
            "SELECT COUNT(*) FROM artifact_renditions "
            "WHERE artifact_renditions.blob_sha256 = stored_blobs.sha256 "
            "AND artifact_renditions.removed_at IS NULL "
            "AND artifact_renditions.state IN ('READY', 'ACTIVE')"
            ") WHERE logical_ref_count IS NULL"
        )
    )

    columns = _columns(connection, "stored_blobs")
    checks = _checks(connection, "stored_blobs")
    if (
        all(not bool(columns[name]["nullable"]) for name in ("physical_bytes", "logical_ref_count", "state"))
        and _STORED_BLOB_FINAL_CHECKS <= checks
    ):
        return
    with op.batch_alter_table("stored_blobs", recreate="always") as batch_op:
        batch_op.alter_column("physical_bytes", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("logical_ref_count", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("state", existing_type=sa.String(length=16), nullable=False)
        if "ck_stored_blobs_physical_bytes_nonnegative" not in checks:
            batch_op.create_check_constraint(
                "ck_stored_blobs_physical_bytes_nonnegative", "physical_bytes >= 0"
            )
        if "ck_stored_blobs_logical_ref_count_nonnegative" not in checks:
            batch_op.create_check_constraint(
                "ck_stored_blobs_logical_ref_count_nonnegative", "logical_ref_count >= 0"
            )
        if "ck_stored_blobs_lifecycle_state" not in checks:
            batch_op.create_check_constraint(
                "ck_stored_blobs_lifecycle_state",
                "state IN ('ACTIVE', 'QUARANTINED', 'PURGED')",
            )
        if "ck_stored_blobs_lifecycle_shape" not in checks:
            batch_op.create_check_constraint(
                "ck_stored_blobs_lifecycle_shape",
                "(state = 'ACTIVE' AND physical_bytes = byte_size AND quarantine_until IS NULL) OR "
                "(state = 'QUARANTINED' AND physical_bytes = byte_size "
                "AND logical_ref_count = 0 AND quarantine_until IS NOT NULL) OR "
                "(state = 'PURGED' AND physical_bytes = 0 "
                "AND logical_ref_count = 0 AND quarantine_until IS NULL)",
            )


def _ensure_quarantine_schema(connection: sa.Connection) -> None:
    _recover_batch_shadow(
        connection,
        "quarantine_entries",
        expected_columns=_QUARANTINE_FINAL_COLUMNS,
        primary_key=["id"],
        required_not_null=_QUARANTINE_FINAL_NOT_NULL,
        required_checks=_QUARANTINE_FINAL_CHECKS,
    )
    _ensure_index(
        connection,
        "quarantine_entries",
        "ix_quarantine_entries_until",
        ["quarantine_until"],
    )
    columns = _columns(connection, "quarantine_entries")
    if "operation_state" not in columns:
        op.add_column(
            "quarantine_entries", sa.Column("operation_state", sa.String(length=32), nullable=True)
        )
    if "operation_error" not in columns:
        op.add_column("quarantine_entries", sa.Column("operation_error", sa.Text(), nullable=True))
    if "operation_started_at" not in columns:
        op.add_column(
            "quarantine_entries", sa.Column("operation_started_at", sa.DateTime(), nullable=True)
        )
    if "operation_updated_at" not in columns:
        op.add_column(
            "quarantine_entries", sa.Column("operation_updated_at", sa.DateTime(), nullable=True)
        )
    op.execute(
        sa.text(
            "UPDATE quarantine_entries SET operation_state = CASE "
            "WHEN purged_at IS NOT NULL THEN 'PURGED' "
            "WHEN restored_at IS NOT NULL THEN 'RESTORED' ELSE 'QUARANTINED' END "
            "WHERE operation_state IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE quarantine_entries SET operation_started_at = quarantined_at "
            "WHERE operation_started_at IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE quarantine_entries SET operation_updated_at = "
            "COALESCE(purged_at, restored_at, quarantined_at) "
            "WHERE operation_updated_at IS NULL"
        )
    )
    columns = _columns(connection, "quarantine_entries")
    checks = _checks(connection, "quarantine_entries")
    check_name = "ck_quarantine_entries_operation_state"
    if (
        all(
            not bool(columns[name]["nullable"])
            for name in ("operation_state", "operation_started_at", "operation_updated_at")
        )
        and check_name in checks
    ):
        return
    with op.batch_alter_table("quarantine_entries", recreate="always") as batch_op:
        batch_op.alter_column("operation_state", existing_type=sa.String(length=32), nullable=False)
        batch_op.alter_column("operation_started_at", existing_type=sa.DateTime(), nullable=False)
        batch_op.alter_column("operation_updated_at", existing_type=sa.DateTime(), nullable=False)
        if check_name not in checks:
            batch_op.create_check_constraint(
                check_name,
                "operation_state IN ('PREPARED_QUARANTINE', 'QUARANTINED', "
                "'PREPARED_RESTORE', 'RESTORED', 'PREPARED_PURGE', 'PURGED', "
                "'RECOVERY_REQUIRED')",
            )


def _ensure_target_tables(connection: sa.Connection) -> None:
    tables = _tables(connection)
    if "gc_plan_targets" not in tables:
        op.create_table(
            "gc_plan_targets",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("gc_plan_entry_id", sa.String(length=36), nullable=False),
            sa.Column("target_kind", sa.String(length=32), nullable=False),
            sa.Column("target_id", sa.String(length=64), nullable=False),
            sa.ForeignKeyConstraint(
                ["gc_plan_entry_id"], ["gc_plan_entries.id"], ondelete="CASCADE"
            ),
            sa.UniqueConstraint(
                "gc_plan_entry_id", "target_kind", "target_id", name="uq_gc_plan_targets_target"
            ),
        )
    if "ix_gc_plan_targets_entry" not in _indexes(connection, "gc_plan_targets"):
        op.create_index("ix_gc_plan_targets_entry", "gc_plan_targets", ["gc_plan_entry_id"])
    op.execute(
        sa.text(
            "INSERT INTO gc_plan_targets (id, gc_plan_entry_id, target_kind, target_id) "
            "SELECT lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-' || "
            "'4' || substr(lower(hex(randomblob(2))), 2) || '-' || "
            "substr('89ab', abs(random()) % 4 + 1, 1) || substr(lower(hex(randomblob(2))), 2) || '-' || "
            "lower(hex(randomblob(6))), id, target_kind, target_id FROM gc_plan_entries "
            "WHERE NOT EXISTS (SELECT 1 FROM gc_plan_targets "
            "WHERE gc_plan_entry_id = gc_plan_entries.id)"
        )
    )

    tables = _tables(connection)
    if "quarantine_targets" not in tables:
        op.create_table(
            "quarantine_targets",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("quarantine_entry_id", sa.String(length=36), nullable=False),
            sa.Column("target_kind", sa.String(length=32), nullable=False),
            sa.Column("target_id", sa.String(length=64), nullable=False),
            sa.ForeignKeyConstraint(
                ["quarantine_entry_id"], ["quarantine_entries.id"], ondelete="CASCADE"
            ),
            sa.UniqueConstraint(
                "quarantine_entry_id",
                "target_kind",
                "target_id",
                name="uq_quarantine_targets_target",
            ),
        )
    if "ix_quarantine_targets_entry" not in _indexes(connection, "quarantine_targets"):
        op.create_index(
            "ix_quarantine_targets_entry", "quarantine_targets", ["quarantine_entry_id"]
        )
    op.execute(
        sa.text(
            "INSERT INTO quarantine_targets (id, quarantine_entry_id, target_kind, target_id) "
            "SELECT lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-' || "
            "'4' || substr(lower(hex(randomblob(2))), 2) || '-' || "
            "substr('89ab', abs(random()) % 4 + 1, 1) || substr(lower(hex(randomblob(2))), 2) || '-' || "
            "lower(hex(randomblob(6))), id, target_kind, target_id FROM quarantine_entries "
            "WHERE NOT EXISTS (SELECT 1 FROM quarantine_targets "
            "WHERE quarantine_entry_id = quarantine_entries.id)"
        )
    )


def upgrade() -> None:
    connection = op.get_bind()
    _preflight_upgrade_batch_shadows(connection)
    _ensure_stored_blob_schema(connection)
    _ensure_quarantine_schema(connection)
    _ensure_target_tables(connection)


def _downgrade_preflight(connection: sa.Connection) -> None:
    tables = _tables(connection)
    blob_table = (
        "stored_blobs"
        if "stored_blobs" in tables
        else "_alembic_tmp_stored_blobs"
        if "_alembic_tmp_stored_blobs" in tables
        else None
    )
    if blob_table is None:
        raise RuntimeError("stored_blobs and its Alembic shadow are both missing")
    blob_columns = set(_columns(connection, blob_table))
    if {"physical_bytes", "logical_ref_count", "state", "quarantine_until"} <= blob_columns:
        unsafe_blob = connection.execute(
            sa.text(
                f"SELECT sha256 FROM {blob_table} WHERE state != 'ACTIVE' "
                "OR physical_bytes != byte_size OR quarantine_until IS NOT NULL LIMIT 1"
            )
        ).first()
        if unsafe_blob is not None:
            raise RuntimeError("cannot downgrade blob lifecycle with non-ACTIVE stored blobs")
    tables = _tables(connection)
    quarantine_table = (
        "quarantine_entries"
        if "quarantine_entries" in tables
        else "_alembic_tmp_quarantine_entries"
        if "_alembic_tmp_quarantine_entries" in tables
        else None
    )
    if quarantine_table is None:
        raise RuntimeError("quarantine_entries and its Alembic shadow are both missing")
    quarantine_columns = set(_columns(connection, quarantine_table))
    if "operation_state" in quarantine_columns:
        in_progress = connection.execute(
            sa.text(
                f"SELECT id FROM {quarantine_table} WHERE operation_state IN "
                "('PREPARED_QUARANTINE', 'PREPARED_RESTORE', 'PREPARED_PURGE', "
                "'RECOVERY_REQUIRED') LIMIT 1"
            )
        ).first()
        if in_progress is not None:
            raise RuntimeError("cannot downgrade blob lifecycle with in-progress operations")
    tables = _tables(connection)
    for mapping_table, owner_table, owner_column in (
        ("gc_plan_targets", "gc_plan_entries", "gc_plan_entry_id"),
        ("quarantine_targets", "quarantine_entries", "quarantine_entry_id"),
    ):
        if mapping_table not in tables:
            continue
        if owner_table not in tables:
            raise RuntimeError("cannot downgrade grouped blob lifecycle targets")
        nonrepresentable = connection.execute(
            sa.text(
                f"SELECT {owner_table}.id FROM {owner_table} LEFT JOIN {mapping_table} "
                f"ON {mapping_table}.{owner_column} = {owner_table}.id "
                f"GROUP BY {owner_table}.id HAVING COUNT({mapping_table}.id) != 1 LIMIT 1"
            )
        ).first()
        orphan_mapping = connection.execute(
            sa.text(
                f"SELECT {mapping_table}.id FROM {mapping_table} LEFT JOIN {owner_table} "
                f"ON {owner_table}.id = {mapping_table}.{owner_column} "
                f"WHERE {owner_table}.id IS NULL LIMIT 1"
            )
        ).first()
        if nonrepresentable is not None or orphan_mapping is not None:
            raise RuntimeError("cannot downgrade grouped blob lifecycle targets")


def _recover_downgrade_batch_shadow(
    connection: sa.Connection,
    table: str,
    *,
    legacy_columns: set[str],
    primary_key: list[str],
    index_name: str,
    index_columns: list[str],
) -> None:
    shadow = f"_alembic_tmp_{table}"
    tables = _tables(connection)
    if shadow not in tables:
        if table not in tables:
            raise RuntimeError(f"{table} and its Alembic shadow are both missing")
        return
    columns = _columns(connection, shadow)
    if set(columns) != legacy_columns:
        raise RuntimeError(f"cannot recover downgrade {table} shadow with unexpected columns")
    if sa.inspect(connection).get_pk_constraint(shadow).get("constrained_columns") != primary_key:
        raise RuntimeError(f"cannot recover downgrade {table} shadow without expected primary key")
    required = legacy_columns - {"restored_at", "purged_at"}
    if any(bool(columns[name]["nullable"]) for name in required):
        raise RuntimeError(f"cannot recover downgrade {table} shadow with nullable required columns")
    if table in tables:
        op.drop_table(shadow)
    else:
        op.rename_table(shadow, table)
    _ensure_index(connection, table, index_name, index_columns)


def _restore_legacy_targets(connection: sa.Connection) -> None:
    tables = _tables(connection)
    if "gc_plan_targets" in tables:
        op.execute(
            sa.text(
                "UPDATE gc_plan_entries SET target_kind = (SELECT target_kind FROM gc_plan_targets "
                "WHERE gc_plan_entry_id = gc_plan_entries.id), target_id = (SELECT target_id "
                "FROM gc_plan_targets WHERE gc_plan_entry_id = gc_plan_entries.id) "
                "WHERE EXISTS (SELECT 1 FROM gc_plan_targets "
                "WHERE gc_plan_entry_id = gc_plan_entries.id)"
            )
        )
        if "ix_gc_plan_targets_entry" in _indexes(connection, "gc_plan_targets"):
            op.drop_index("ix_gc_plan_targets_entry", table_name="gc_plan_targets")
        op.drop_table("gc_plan_targets")
    tables = _tables(connection)
    if "quarantine_targets" in tables:
        op.execute(
            sa.text(
                "UPDATE quarantine_entries SET target_kind = (SELECT target_kind "
                "FROM quarantine_targets WHERE quarantine_entry_id = quarantine_entries.id), "
                "target_id = (SELECT target_id FROM quarantine_targets "
                "WHERE quarantine_entry_id = quarantine_entries.id) "
                "WHERE EXISTS (SELECT 1 FROM quarantine_targets "
                "WHERE quarantine_entry_id = quarantine_entries.id)"
            )
        )
        if "ix_quarantine_targets_entry" in _indexes(connection, "quarantine_targets"):
            op.drop_index("ix_quarantine_targets_entry", table_name="quarantine_targets")
        op.drop_table("quarantine_targets")


def downgrade() -> None:
    connection = op.get_bind()
    _downgrade_preflight(connection)
    if "ix_artifact_renditions_blob_sha256" in _indexes(connection, "artifact_renditions"):
        op.drop_index(
            "ix_artifact_renditions_blob_sha256",
            table_name="artifact_renditions",
        )
    _recover_downgrade_batch_shadow(
        connection,
        "quarantine_entries",
        legacy_columns=_QUARANTINE_LEGACY_COLUMNS,
        primary_key=["id"],
        index_name="ix_quarantine_entries_until",
        index_columns=["quarantine_until"],
    )
    _recover_downgrade_batch_shadow(
        connection,
        "stored_blobs",
        legacy_columns=_STORED_BLOB_LEGACY_COLUMNS,
        primary_key=["sha256"],
        index_name="ix_stored_blobs_media_kind_created",
        index_columns=["media_kind", "created_at"],
    )
    _restore_legacy_targets(connection)

    quarantine_columns = set(_columns(connection, "quarantine_entries"))
    operation_columns = {
        "operation_state",
        "operation_error",
        "operation_started_at",
        "operation_updated_at",
    }
    if operation_columns & quarantine_columns:
        quarantine_checks = _checks(connection, "quarantine_entries")
        with op.batch_alter_table("quarantine_entries", recreate="always") as batch_op:
            if "ck_quarantine_entries_operation_state" in quarantine_checks:
                batch_op.drop_constraint(
                    "ck_quarantine_entries_operation_state",
                    type_="check",
                )
            for column_name in sorted(operation_columns & quarantine_columns, reverse=True):
                batch_op.drop_column(column_name)

    blob_columns = set(_columns(connection, "stored_blobs"))
    lifecycle_columns = {"physical_bytes", "logical_ref_count", "state", "quarantine_until"}
    if lifecycle_columns & blob_columns:
        blob_checks = _checks(connection, "stored_blobs")
        with op.batch_alter_table("stored_blobs", recreate="always") as batch_op:
            for check_name in sorted(
                {
                    "ck_stored_blobs_physical_bytes_nonnegative",
                    "ck_stored_blobs_logical_ref_count_nonnegative",
                    "ck_stored_blobs_lifecycle_state",
                    "ck_stored_blobs_lifecycle_shape",
                }
                & blob_checks
            ):
                batch_op.drop_constraint(check_name, type_="check")
            for column_name in sorted(lifecycle_columns & blob_columns, reverse=True):
                batch_op.drop_column(column_name)
