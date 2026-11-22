from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_artifact_discard"
down_revision: str | None = "0002_artifact_store"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "import_sessions",
        sa.Column("discarded_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "import_sessions",
        sa.Column(
            "discard_cleanup_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_import_sessions_discard_cleanup_pending",
        "import_sessions",
        ["discard_cleanup_pending"],
    )
    op.add_column(
        "artifact_revisions",
        sa.Column("discarded_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_artifact_revisions_blob_discarded",
        "artifact_revisions",
        ["blob_sha256", "discarded_at"],
    )


def downgrade() -> None:
    # Tombstoned rows may no longer have a physical CAS object. Remove that
    # logically discarded metadata before dropping the marker columns so a
    # downgrade cannot accidentally make missing content live again.
    op.execute(
        sa.text("DELETE FROM artifact_revisions WHERE discarded_at IS NOT NULL")
    )
    op.execute(sa.text("DELETE FROM import_sessions WHERE discarded_at IS NOT NULL"))
    op.execute(
        sa.text(
            "DELETE FROM artifacts WHERE NOT EXISTS "
            "(SELECT 1 FROM artifact_revisions "
            "WHERE artifact_revisions.artifact_id = artifacts.id)"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM stored_blobs WHERE NOT EXISTS "
            "(SELECT 1 FROM artifact_revisions "
            "WHERE artifact_revisions.blob_sha256 = stored_blobs.sha256)"
        )
    )
    op.drop_index(
        "ix_artifact_revisions_blob_discarded",
        table_name="artifact_revisions",
    )
    op.drop_column("artifact_revisions", "discarded_at")
    op.drop_index(
        "ix_import_sessions_discard_cleanup_pending",
        table_name="import_sessions",
    )
    op.drop_column("import_sessions", "discard_cleanup_pending")
    op.drop_column("import_sessions", "discarded_at")
