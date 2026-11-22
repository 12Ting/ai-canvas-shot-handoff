from collections.abc import Sequence
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0010_c2_graph_branches"
down_revision: str | None = "0009_c1_prompt_resources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid() -> str:
    return str(uuid4())


def upgrade() -> None:
    op.create_table(
        "graph_branches",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("graph_document_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("normalized_name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("forked_from_branch_id", sa.String(length=36), nullable=True),
        sa.Column("forked_from_revision_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 100",
            name="ck_graph_branches_name_length",
        ),
        sa.CheckConstraint(
            "length(normalized_name) BETWEEN 1 AND 100",
            name="ck_graph_branches_normalized_name_length",
        ),
        sa.CheckConstraint(
            "kind IN ('MAIN', 'EXPERIMENT')",
            name="ck_graph_branches_kind",
        ),
        sa.CheckConstraint(
            "(kind = 'MAIN' AND forked_from_branch_id IS NULL) OR "
            "(kind = 'EXPERIMENT' AND forked_from_branch_id IS NOT NULL)",
            name="ck_graph_branches_fork_shape",
        ),
        sa.ForeignKeyConstraint(
            ["graph_document_id"],
            ["graph_documents.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["forked_from_branch_id"],
            ["graph_branches.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["forked_from_revision_id"],
            ["graph_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "graph_document_id",
            "normalized_name",
            name="uq_graph_branch_name",
        ),
    )
    op.create_index(
        "uq_graph_branches_one_main",
        "graph_branches",
        ["graph_document_id"],
        unique=True,
        sqlite_where=sa.text("kind = 'MAIN'"),
    )
    op.create_index(
        "ix_graph_branches_document_created",
        "graph_branches",
        ["graph_document_id", "created_at"],
    )

    op.create_table(
        "graph_branch_heads",
        sa.Column("branch_id", sa.String(length=36), primary_key=True),
        sa.Column("graph_revision_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_graph_branch_heads_version_positive"),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["graph_branches.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["graph_revision_id"],
            ["graph_revisions.id"],
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "graph_selection_pointers",
        sa.Column("graph_document_id", sa.String(length=36), primary_key=True),
        sa.Column("branch_id", sa.String(length=36), nullable=False),
        sa.Column("graph_revision_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "version > 0",
            name="ck_graph_selection_pointers_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["graph_document_id"],
            ["graph_documents.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["graph_branches.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["graph_revision_id"],
            ["graph_revisions.id"],
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "graph_promotions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("client_mutation_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("source_branch_id", sa.String(length=36), nullable=False),
        sa.Column("source_graph_revision_id", sa.String(length=36), nullable=False),
        sa.Column("source_branch_head_version", sa.Integer(), nullable=False),
        sa.Column("target_branch_id", sa.String(length=36), nullable=False),
        sa.Column("target_graph_revision_id", sa.String(length=36), nullable=False),
        sa.Column("target_branch_head_version", sa.Integer(), nullable=False),
        sa.Column("expected_selection_version", sa.Integer(), nullable=False),
        sa.Column("undo_of_promotion_id", sa.String(length=36), nullable=True),
        sa.Column("impact_json", sa.Text(), nullable=False),
        sa.Column("impact_sha256", sa.String(length=64), nullable=False),
        sa.Column("promotion_sha256", sa.String(length=64), nullable=False),
        sa.Column("risk_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('PROMOTE', 'UNDO_PROMOTION')",
            name="ck_graph_promotions_kind",
        ),
        sa.CheckConstraint(
            "source_branch_head_version > 0 AND target_branch_head_version > 0 "
            "AND expected_selection_version > 0",
            name="ck_graph_promotions_versions_positive",
        ),
        sa.CheckConstraint(
            "state IN ('PENDING_APPROVAL', 'APPROVED', 'APPLIED', 'REJECTED', 'STALE')",
            name="ck_graph_promotions_state",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_branch_id"], ["graph_branches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_graph_revision_id"],
            ["graph_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_branch_id"], ["graph_branches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_graph_revision_id"],
            ["graph_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["undo_of_promotion_id"],
            ["graph_promotions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["approval_decisions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "project_id",
            "client_mutation_id",
            name="uq_graph_promotions_client_mutation",
        ),
    )
    op.create_index(
        "ix_graph_promotions_project_created",
        "graph_promotions",
        ["project_id", "created_at"],
    )

    op.create_table(
        "node_materialization_states",
        sa.Column("graph_revision_id", sa.String(length=36), primary_key=True),
        sa.Column("node_id", sa.String(length=36), primary_key=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("last_materialized_graph_revision_id", sa.String(length=36), nullable=False),
        sa.Column("reason_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('CURRENT', 'STALE')",
            name="ck_node_materialization_states_state",
        ),
        sa.ForeignKeyConstraint(
            ["graph_revision_id"],
            ["graph_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_materialized_graph_revision_id"],
            ["graph_revisions.id"],
            ondelete="RESTRICT",
        ),
    )

    op.add_column(
        "graph_change_sets",
        sa.Column("application_target_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "graph_change_sets",
        sa.Column("impact_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "graph_change_sets",
        sa.Column("impact_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "graph_change_sets",
        sa.Column("created_branch_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "graph_change_sets",
        sa.Column("applied_branch_id", sa.String(length=36), nullable=True),
    )
    op.execute("DROP TRIGGER f1_graph_change_sets_content_immutable")
    op.execute(
        "CREATE TRIGGER f1_graph_change_sets_content_immutable "
        "BEFORE UPDATE OF project_id, correlation_id, base_graph_revision_id, "
        "client_mutation_id, snapshot_json, snapshot_hash, diff_json, "
        "inverse_operations_json, risk_json, changeset_sha256, created_at, "
        "application_target_json, impact_json, impact_sha256 "
        "ON graph_change_sets "
        "BEGIN SELECT RAISE(ABORT, 'graph change set content is immutable'); END"
    )

    connection = op.get_bind()
    documents = connection.execute(
        sa.text(
            "SELECT id, active_revision_id FROM graph_documents ORDER BY id"
        )
    ).mappings()
    for document in documents:
        active_revision_id = document["active_revision_id"]
        if active_revision_id is None:
            raise RuntimeError(
                f"graph document {document['id']} has no active revision"
            )
        belongs_to_document = connection.scalar(
            sa.text(
                "SELECT COUNT(*) FROM graph_revisions "
                "WHERE id = :revision_id AND graph_document_id = :document_id"
            ),
            {
                "revision_id": active_revision_id,
                "document_id": document["id"],
            },
        )
        if belongs_to_document != 1:
            raise RuntimeError(
                f"graph document {document['id']} active revision is missing or cross-document"
            )
        branch_id = _uuid()
        now = connection.scalar(sa.text("SELECT CURRENT_TIMESTAMP"))
        connection.execute(
            sa.text(
                "INSERT INTO graph_branches "
                "(id, graph_document_id, name, normalized_name, kind, "
                "forked_from_branch_id, forked_from_revision_id, created_at) "
                "VALUES (:id, :document_id, '主线', '主线', 'MAIN', NULL, :revision_id, :now)"
            ),
            {
                "id": branch_id,
                "document_id": document["id"],
                "revision_id": active_revision_id,
                "now": now,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO graph_branch_heads "
                "(branch_id, graph_revision_id, version, updated_at) "
                "VALUES (:branch_id, :revision_id, 1, :now)"
            ),
            {"branch_id": branch_id, "revision_id": active_revision_id, "now": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO graph_selection_pointers "
                "(graph_document_id, branch_id, graph_revision_id, version, updated_at) "
                "VALUES (:document_id, :branch_id, :revision_id, 1, :now)"
            ),
            {
                "document_id": document["id"],
                "branch_id": branch_id,
                "revision_id": active_revision_id,
                "now": now,
            },
        )

    op.execute(
        "CREATE TRIGGER trg_graph_promotions_no_delete BEFORE DELETE ON graph_promotions "
        "BEGIN SELECT RAISE(ABORT, 'graph_promotions are append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_graph_promotions_subject_immutable BEFORE UPDATE ON graph_promotions "
        "WHEN NEW.id IS NOT OLD.id OR NEW.project_id IS NOT OLD.project_id "
        "OR NEW.correlation_id IS NOT OLD.correlation_id "
        "OR NEW.client_mutation_id IS NOT OLD.client_mutation_id "
        "OR NEW.kind IS NOT OLD.kind OR NEW.source_branch_id IS NOT OLD.source_branch_id "
        "OR NEW.source_graph_revision_id IS NOT OLD.source_graph_revision_id "
        "OR NEW.source_branch_head_version IS NOT OLD.source_branch_head_version "
        "OR NEW.target_branch_id IS NOT OLD.target_branch_id "
        "OR NEW.target_graph_revision_id IS NOT OLD.target_graph_revision_id "
        "OR NEW.target_branch_head_version IS NOT OLD.target_branch_head_version "
        "OR NEW.expected_selection_version IS NOT OLD.expected_selection_version "
        "OR NEW.undo_of_promotion_id IS NOT OLD.undo_of_promotion_id "
        "OR NEW.impact_json IS NOT OLD.impact_json OR NEW.impact_sha256 IS NOT OLD.impact_sha256 "
        "OR NEW.promotion_sha256 IS NOT OLD.promotion_sha256 OR NEW.risk_json IS NOT OLD.risk_json "
        "OR NEW.created_at IS NOT OLD.created_at "
        "BEGIN SELECT RAISE(ABORT, 'graph_promotions subject is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_graph_promotions_state_machine BEFORE UPDATE ON graph_promotions "
        "WHEN NOT ((OLD.state = NEW.state) "
        "OR (OLD.state = 'PENDING_APPROVAL' AND NEW.state IN ('APPROVED', 'REJECTED', 'STALE')) "
        "OR (OLD.state = 'APPROVED' AND NEW.state IN ('APPLIED', 'STALE'))) "
        "BEGIN SELECT RAISE(ABORT, 'invalid graph_promotions state transition'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_graph_promotions_state_machine")
    op.execute("DROP TRIGGER IF EXISTS trg_graph_promotions_subject_immutable")
    op.execute("DROP TRIGGER IF EXISTS trg_graph_promotions_no_delete")
    op.execute("DROP TRIGGER f1_graph_change_sets_content_immutable")
    op.drop_column("graph_change_sets", "applied_branch_id")
    op.drop_column("graph_change_sets", "created_branch_id")
    op.drop_column("graph_change_sets", "impact_sha256")
    op.drop_column("graph_change_sets", "impact_json")
    op.drop_column("graph_change_sets", "application_target_json")
    op.execute(
        "CREATE TRIGGER f1_graph_change_sets_content_immutable "
        "BEFORE UPDATE OF project_id, correlation_id, base_graph_revision_id, "
        "client_mutation_id, snapshot_json, snapshot_hash, diff_json, "
        "inverse_operations_json, risk_json, changeset_sha256, created_at "
        "ON graph_change_sets "
        "BEGIN SELECT RAISE(ABORT, 'graph change set content is immutable'); END"
    )
    op.drop_table("node_materialization_states")
    op.drop_index("ix_graph_promotions_project_created", table_name="graph_promotions")
    op.drop_table("graph_promotions")
    op.drop_table("graph_selection_pointers")
    op.drop_table("graph_branch_heads")
    op.drop_index("ix_graph_branches_document_created", table_name="graph_branches")
    op.drop_index("uq_graph_branches_one_main", table_name="graph_branches")
    op.drop_table("graph_branches")
