from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_artifact_store"
down_revision: str | None = "0001_graph_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


IMPORT_STATES = (
    "CREATED",
    "RECEIVING",
    "VALIDATING",
    "COMPLETED",
    "FAILED_RETRYABLE",
    "FAILED_FINAL",
)


def upgrade() -> None:
    op.create_table(
        "stored_blobs",
        sa.Column("sha256", sa.String(length=64), primary_key=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("media_kind", sa.String(length=16), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_stored_blobs_sha256_length",
        ),
        sa.CheckConstraint(
            "byte_size > 0",
            name="ck_stored_blobs_byte_size_positive",
        ),
        sa.CheckConstraint(
            "media_kind IN ('image', 'video')",
            name="ck_stored_blobs_media_kind",
        ),
        sa.CheckConstraint(
            "length(media_type) > 0",
            name="ck_stored_blobs_media_type_nonempty",
        ),
        sa.CheckConstraint(
            "length(metadata_json) > 0",
            name="ck_stored_blobs_metadata_json_nonempty",
        ),
    )
    op.create_index(
        "ix_stored_blobs_media_kind_created",
        "stored_blobs",
        ["media_kind", "created_at"],
    )

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("origin_project_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('image', 'video')",
            name="ck_artifacts_kind",
        ),
        sa.ForeignKeyConstraint(
            ["origin_project_id"],
            ["projects.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_artifacts_origin_project_created",
        "artifacts",
        ["origin_project_id", "created_at"],
    )

    op.create_table(
        "import_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_project_id", sa.String(length=36), nullable=False),
        sa.Column("client_import_id", sa.String(length=36), nullable=False),
        # This is deliberately application-validated instead of a database FK.
        # The referenced revision may be absent for a fresh import and making it
        # a FK would create a migration/insert cycle with artifact_revisions.
        sa.Column("replace_revision_id", sa.String(length=36), nullable=True),
        sa.Column("requested_media_kind", sa.String(length=16), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("declared_content_type", sa.String(length=255), nullable=True),
        sa.Column("expected_byte_size", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("received_byte_size", sa.Integer(), nullable=False),
        sa.Column("computed_sha256", sa.String(length=64), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(client_import_id) = 36",
            name="ck_import_sessions_client_import_id_length",
        ),
        sa.CheckConstraint(
            "replace_revision_id IS NULL OR length(replace_revision_id) = 36",
            name="ck_import_sessions_replace_revision_id_length",
        ),
        sa.CheckConstraint(
            "requested_media_kind IN ('image', 'video')",
            name="ck_import_sessions_requested_media_kind",
        ),
        sa.CheckConstraint(
            "expected_byte_size > 0",
            name="ck_import_sessions_expected_byte_size_positive",
        ),
        sa.CheckConstraint(
            "received_byte_size >= 0",
            name="ck_import_sessions_received_byte_size_nonnegative",
        ),
        sa.CheckConstraint(
            f"state IN ({', '.join(repr(state) for state in IMPORT_STATES)})",
            name="ck_import_sessions_state",
        ),
        sa.CheckConstraint(
            "computed_sha256 IS NULL OR length(computed_sha256) = 64",
            name="ck_import_sessions_computed_sha256_length",
        ),
        sa.ForeignKeyConstraint(
            ["source_project_id"],
            ["projects.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "source_project_id",
            "client_import_id",
            name="uq_import_sessions_project_client_import",
        ),
    )
    op.create_index(
        "ix_import_sessions_source_project_state_updated",
        "import_sessions",
        ["source_project_id", "state", "updated_at"],
    )

    op.create_table(
        "artifact_revisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("blob_sha256", sa.String(length=64), nullable=False),
        # Keep this open for future AI/external creation sources.
        sa.Column("source_kind", sa.String(length=64), nullable=False),
        sa.Column("source_project_id", sa.String(length=36), nullable=False),
        sa.Column("source_import_session_id", sa.String(length=36), nullable=True),
        sa.Column("display_filename", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "sequence > 0",
            name="ck_artifact_revisions_sequence_positive",
        ),
        sa.CheckConstraint(
            "length(source_kind) > 0",
            name="ck_artifact_revisions_source_kind_nonempty",
        ),
        sa.CheckConstraint(
            "length(display_filename) > 0",
            name="ck_artifact_revisions_display_filename_nonempty",
        ),
        sa.CheckConstraint(
            "source_kind != 'import' OR source_import_session_id IS NOT NULL",
            name="ck_artifact_revisions_import_provenance",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["artifacts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["blob_sha256"],
            ["stored_blobs.sha256"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_project_id"],
            ["projects.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_import_session_id"],
            ["import_sessions.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "artifact_id",
            "sequence",
            name="uq_artifact_revisions_artifact_sequence",
        ),
        sa.UniqueConstraint(
            "source_import_session_id",
            name="uq_artifact_revisions_source_import_session",
        ),
    )
    op.create_index(
        "ix_artifact_revisions_artifact_created",
        "artifact_revisions",
        ["artifact_id", "created_at"],
    )
    op.create_index(
        "ix_artifact_revisions_blob_sha256",
        "artifact_revisions",
        ["blob_sha256"],
    )
    op.create_index(
        "ix_artifact_revisions_source_project_created",
        "artifact_revisions",
        ["source_project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_artifact_revisions_source_project_created",
        table_name="artifact_revisions",
    )
    op.drop_index(
        "ix_artifact_revisions_blob_sha256",
        table_name="artifact_revisions",
    )
    op.drop_index(
        "ix_artifact_revisions_artifact_created",
        table_name="artifact_revisions",
    )
    op.drop_table("artifact_revisions")
    op.drop_index(
        "ix_import_sessions_source_project_state_updated",
        table_name="import_sessions",
    )
    op.drop_table("import_sessions")
    op.drop_index(
        "ix_artifacts_origin_project_created",
        table_name="artifacts",
    )
    op.drop_table("artifacts")
    op.drop_index(
        "ix_stored_blobs_media_kind_created",
        table_name="stored_blobs",
    )
    op.drop_table("stored_blobs")
