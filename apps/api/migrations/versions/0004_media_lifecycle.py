from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_media_lifecycle"
down_revision: str | None = "0003_artifact_discard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_root_grants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=512), nullable=False),
        sa.Column("canonical_path", sa.Text(), nullable=False),
        sa.Column("volume_identity", sa.String(length=255), nullable=False),
        sa.Column("root_file_identity", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("subject", "canonical_path", name="uq_external_root_grants_subject_path"),
    )
    op.create_index("ix_external_root_grants_subject_active", "external_root_grants", ["subject", "revoked_at"])
    op.create_table(
        "external_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("root_grant_id", sa.String(length=36), nullable=False),
        sa.Column("canonical_path", sa.Text(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("file_identity", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("modified_at", sa.DateTime(), nullable=False),
        sa.Column("media_kind", sa.String(length=16), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("confirmed_by_subject", sa.String(length=255), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["root_grant_id"], ["external_root_grants.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_external_snapshots_grant_confirmed", "external_snapshots", ["root_grant_id", "confirmed_at"])

    with op.batch_alter_table("artifact_revisions", recreate="always") as batch:
        batch.alter_column("blob_sha256", existing_type=sa.String(length=64), nullable=True)
        batch.add_column(sa.Column("storage_kind", sa.String(length=32), nullable=False, server_default="MANAGED_BLOB"))
        batch.add_column(sa.Column("media_health", sa.String(length=32), nullable=False, server_default="AVAILABLE"))
        batch.add_column(sa.Column("external_snapshot_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_artifact_revisions_external_snapshot",
            "external_snapshots",
            ["external_snapshot_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_artifact_revisions_storage_source",
            "(storage_kind = 'MANAGED_BLOB' AND blob_sha256 IS NOT NULL AND external_snapshot_id IS NULL) "
            "OR (storage_kind = 'EXTERNAL_REFERENCE' AND blob_sha256 IS NULL AND external_snapshot_id IS NOT NULL)",
        )
    op.create_index("ix_artifact_revisions_external_snapshot", "artifact_revisions", ["external_snapshot_id"])

    op.create_table(
        "storage_reservations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("reserved_bytes", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("reserved_bytes > 0", name="ck_storage_reservations_bytes_positive"),
        sa.UniqueConstraint("operation_id", name="uq_storage_reservations_operation_id"),
    )
    op.create_index("ix_storage_reservations_state_expires", "storage_reservations", ["state", "expires_at"])
    op.create_table(
        "large_upload_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_project_id", sa.String(length=36), nullable=False),
        sa.Column("client_upload_id", sa.String(length=36), nullable=False),
        sa.Column("replace_revision_id", sa.String(length=36), nullable=True),
        sa.Column("requested_media_kind", sa.String(length=16), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("declared_content_type", sa.String(length=255), nullable=True),
        sa.Column("expected_byte_size", sa.Integer(), nullable=False),
        sa.Column("received_byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("part_size", sa.Integer(), nullable=False),
        sa.Column("staging_key", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("computed_sha256", sa.String(length=64), nullable=True),
        sa.Column("artifact_revision_id", sa.String(length=36), nullable=True),
        sa.Column("reservation_id", sa.String(length=36), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("expected_byte_size > 0", name="ck_large_upload_sessions_expected_positive"),
        sa.CheckConstraint("received_byte_size >= 0", name="ck_large_upload_sessions_received_nonnegative"),
        sa.CheckConstraint("part_size > 0", name="ck_large_upload_sessions_part_positive"),
        sa.ForeignKeyConstraint(["source_project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("source_project_id", "client_upload_id", name="uq_large_upload_sessions_project_client_upload"),
        sa.UniqueConstraint("staging_key", name="uq_large_upload_sessions_staging_key"),
    )
    op.create_index("ix_large_upload_sessions_state_updated", "large_upload_sessions", ["state", "updated_at"])
    op.create_table(
        "large_upload_parts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("upload_session_id", sa.String(length=36), nullable=False),
        sa.Column("part_number", sa.Integer(), nullable=False),
        sa.Column("start_byte", sa.Integer(), nullable=False),
        sa.Column("end_byte", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("stored_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("part_number >= 0", name="ck_large_upload_parts_number_nonnegative"),
        sa.CheckConstraint("start_byte >= 0", name="ck_large_upload_parts_start_nonnegative"),
        sa.CheckConstraint("end_byte >= start_byte", name="ck_large_upload_parts_end_order"),
        sa.CheckConstraint("byte_size > 0", name="ck_large_upload_parts_size_positive"),
        sa.ForeignKeyConstraint(["upload_session_id"], ["large_upload_sessions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("upload_session_id", "part_number", name="uq_large_upload_parts_session_number"),
        sa.UniqueConstraint("upload_session_id", "start_byte", name="uq_large_upload_parts_session_start"),
    )
    op.create_table(
        "media_sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("resource_kind", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_media_sessions_expires", "media_sessions", ["absolute_expires_at"])
    op.create_table(
        "maintenance_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("operation_key", sa.String(length=255), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("operation_key", name="uq_maintenance_jobs_operation_key"),
    )
    op.create_index("ix_maintenance_jobs_state_lease", "maintenance_jobs", ["state", "lease_expires_at"])
    op.create_table(
        "artifact_renditions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_revision_id", sa.String(length=36), nullable=False),
        sa.Column("profile", sa.String(length=32), nullable=False),
        sa.Column("blob_sha256", sa.String(length=64), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["source_revision_id"], ["artifact_revisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["blob_sha256"], ["stored_blobs.sha256"], ondelete="RESTRICT"),
        sa.UniqueConstraint("source_revision_id", "profile", name="uq_artifact_renditions_source_profile"),
    )
    op.create_index("ix_artifact_renditions_source_state", "artifact_renditions", ["source_revision_id", "state"])
    op.create_table(
        "gc_plans",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("summary_sha256", sa.String(length=64), nullable=False),
        sa.Column("logical_reclaim_bytes", sa.Integer(), nullable=False),
        sa.Column("physical_reclaim_bytes", sa.Integer(), nullable=False),
        sa.Column("created_by_subject", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("confirmed_by_subject", sa.String(length=255), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "gc_plan_entries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("gc_plan_id", sa.String(length=36), nullable=False),
        sa.Column("target_kind", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("logical_reclaim_bytes", sa.Integer(), nullable=False),
        sa.Column("physical_reclaim_bytes", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["gc_plan_id"], ["gc_plans.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("gc_plan_id", "target_kind", "target_id", name="uq_gc_plan_entries_target"),
    )
    op.create_table(
        "quarantine_entries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("target_kind", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("original_path", sa.Text(), nullable=False),
        sa.Column("quarantine_path", sa.Text(), nullable=False),
        sa.Column("quarantined_at", sa.DateTime(), nullable=False),
        sa.Column("quarantine_until", sa.DateTime(), nullable=False),
        sa.Column("restored_at", sa.DateTime(), nullable=True),
        sa.Column("purged_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_quarantine_entries_until", "quarantine_entries", ["quarantine_until"])


def downgrade() -> None:
    op.drop_index("ix_quarantine_entries_until", table_name="quarantine_entries")
    op.drop_table("quarantine_entries")
    op.drop_table("gc_plan_entries")
    op.drop_table("gc_plans")
    op.drop_index("ix_artifact_renditions_source_state", table_name="artifact_renditions")
    op.drop_table("artifact_renditions")
    op.drop_index("ix_maintenance_jobs_state_lease", table_name="maintenance_jobs")
    op.drop_table("maintenance_jobs")
    op.drop_index("ix_media_sessions_expires", table_name="media_sessions")
    op.drop_table("media_sessions")
    op.drop_table("large_upload_parts")
    op.drop_index("ix_large_upload_sessions_state_updated", table_name="large_upload_sessions")
    op.drop_table("large_upload_sessions")
    op.drop_index("ix_storage_reservations_state_expires", table_name="storage_reservations")
    op.drop_table("storage_reservations")
    op.drop_index("ix_artifact_revisions_external_snapshot", table_name="artifact_revisions")
    with op.batch_alter_table("artifact_revisions", recreate="always") as batch:
        batch.drop_constraint("ck_artifact_revisions_storage_source", type_="check")
        batch.drop_constraint("fk_artifact_revisions_external_snapshot", type_="foreignkey")
        batch.drop_column("external_snapshot_id")
        batch.drop_column("media_health")
        batch.drop_column("storage_kind")
        batch.alter_column("blob_sha256", existing_type=sa.String(length=64), nullable=False)
    op.drop_index("ix_external_snapshots_grant_confirmed", table_name="external_snapshots")
    op.drop_table("external_snapshots")
    op.drop_index("ix_external_root_grants_subject_active", table_name="external_root_grants")
    op.drop_table("external_root_grants")
