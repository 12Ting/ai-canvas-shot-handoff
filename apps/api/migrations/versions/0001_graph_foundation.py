from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_graph_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "graph_documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("active_revision_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id"),
    )
    op.create_table(
        "graph_revisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("graph_document_id", sa.String(length=36), nullable=False),
        sa.Column("parent_revision_id", sa.String(length=36), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("client_mutation_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["graph_document_id"], ["graph_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["graph_document_id", "parent_revision_id"],
            ["graph_revisions.graph_document_id", "graph_revisions.id"],
        ),
        sa.UniqueConstraint("graph_document_id", "id"),
        sa.UniqueConstraint("graph_document_id", "client_mutation_id"),
        sa.UniqueConstraint("graph_document_id", "sequence"),
    )
    op.create_index(
        "ix_graph_revisions_document_created",
        "graph_revisions",
        ["graph_document_id", "created_at"],
    )
    op.create_table(
        "project_view_states",
        sa.Column("project_id", sa.String(length=36), primary_key=True),
        sa.Column("viewport_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("project_view_states")
    op.drop_index("ix_graph_revisions_document_created", table_name="graph_revisions")
    op.drop_table("graph_revisions")
    op.drop_table("graph_documents")
    op.drop_table("projects")
