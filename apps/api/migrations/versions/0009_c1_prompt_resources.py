from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0009_c1_prompt_resources"
down_revision: str | None = "0008_f1_control_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resources",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("origin_project_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('STYLE', 'CHARACTER', 'PROMPT', 'MEDIA')",
            name="ck_resources_kind",
        ),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 200",
            name="ck_resources_name_length",
        ),
        sa.ForeignKeyConstraint(
            ["origin_project_id"],
            ["projects.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_resources_kind_name",
        "resources",
        ["kind", "name"],
    )
    op.create_index(
        "ix_resources_origin_project_created",
        "resources",
        ["origin_project_id", "created_at"],
    )

    op.create_table(
        "resource_revisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("created_by_actor", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "sequence > 0",
            name="ck_resource_revisions_sequence_positive",
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name="ck_resource_revisions_schema_version",
        ),
        sa.CheckConstraint(
            "length(payload_sha256) = 64 "
            "AND payload_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_resource_revisions_payload_sha256",
        ),
        sa.CheckConstraint(
            "length(payload_json) > 0",
            name="ck_resource_revisions_payload_json_nonempty",
        ),
        sa.CheckConstraint(
            "length(created_by_actor) > 0",
            name="ck_resource_revisions_actor_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["resources.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "resource_id",
            "sequence",
            name="uq_resource_revisions_resource_sequence",
        ),
    )
    op.create_index(
        "ix_resource_revisions_resource_created",
        "resource_revisions",
        ["resource_id", "created_at"],
    )

    op.create_table(
        "resource_revision_tags",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("resource_revision_id", sa.String(length=36), nullable=False),
        sa.Column("normalized_tag", sa.String(length=100), nullable=False),
        sa.Column("display_tag", sa.String(length=100), nullable=False),
        sa.CheckConstraint(
            "length(normalized_tag) BETWEEN 1 AND 100",
            name="ck_resource_revision_tags_normalized_length",
        ),
        sa.CheckConstraint(
            "length(display_tag) BETWEEN 1 AND 100",
            name="ck_resource_revision_tags_display_length",
        ),
        sa.ForeignKeyConstraint(
            ["resource_revision_id"],
            ["resource_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "resource_revision_id",
            "normalized_tag",
            name="uq_resource_revision_tags_revision_tag",
        ),
    )
    op.create_index(
        "ix_resource_revision_tags_normalized_revision",
        "resource_revision_tags",
        ["normalized_tag", "resource_revision_id"],
    )

    op.create_table(
        "resource_revision_artifacts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("resource_revision_id", sa.String(length=36), nullable=False),
        sa.Column("slot", sa.String(length=100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("artifact_revision_id", sa.String(length=36), nullable=False),
        sa.CheckConstraint(
            "length(slot) BETWEEN 1 AND 100",
            name="ck_resource_revision_artifacts_slot_length",
        ),
        sa.CheckConstraint(
            "position >= 0",
            name="ck_resource_revision_artifacts_position_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["resource_revision_id"],
            ["resource_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_revision_id"],
            ["artifact_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "resource_revision_id",
            "slot",
            "position",
            name="uq_resource_revision_artifacts_revision_slot_position",
        ),
    )
    op.create_index(
        "ix_resource_revision_artifacts_artifact_revision",
        "resource_revision_artifacts",
        ["artifact_revision_id"],
    )

    for table_name in (
        "resource_revisions",
        "resource_revision_tags",
        "resource_revision_artifacts",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_no_update BEFORE UPDATE ON {table_name} "
            f"BEGIN SELECT RAISE(ABORT, '{table_name} are immutable'); END"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_no_delete BEFORE DELETE ON {table_name} "
            f"BEGIN SELECT RAISE(ABORT, '{table_name} are immutable'); END"
        )


def downgrade() -> None:
    for table_name in (
        "resource_revision_artifacts",
        "resource_revision_tags",
        "resource_revisions",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_delete")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_update")
    op.drop_index(
        "ix_resource_revision_artifacts_artifact_revision",
        table_name="resource_revision_artifacts",
    )
    op.drop_table("resource_revision_artifacts")
    op.drop_index(
        "ix_resource_revision_tags_normalized_revision",
        table_name="resource_revision_tags",
    )
    op.drop_table("resource_revision_tags")
    op.drop_index(
        "ix_resource_revisions_resource_created",
        table_name="resource_revisions",
    )
    op.drop_table("resource_revisions")
    op.drop_index("ix_resources_origin_project_created", table_name="resources")
    op.drop_index("ix_resources_kind_name", table_name="resources")
    op.drop_table("resources")
