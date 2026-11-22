"""Register directory projects without modifying existing graph revisions."""
from alembic import op
import sqlalchemy as sa

revision = "0013_shot_handoff"
down_revision = "0012_c4_prompt_profile_batches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "handoff_projects",
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("root", sa.Text, nullable=False, unique=True),
        sa.Column("document", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("handoff_projects")
