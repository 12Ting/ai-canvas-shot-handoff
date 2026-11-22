from collections.abc import Sequence
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0007_f1_event_spine"
down_revision: str | None = "0006_blob_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experience_events",
        sa.Column("stream_position", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(length=36), nullable=False, unique=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("causation_event_id", sa.String(length=36), nullable=True),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("envelope_sha256", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "actor_kind IN ('HUMAN', 'AI_CANVAS', 'SYSTEM')", name="ck_experience_events_actor_kind"
        ),
        sa.CheckConstraint("schema_version = 1", name="ck_experience_events_schema_version"),
        sa.CheckConstraint("length(payload_sha256) = 64", name="ck_experience_events_payload_sha256"),
        sa.CheckConstraint("length(envelope_sha256) = 64", name="ck_experience_events_envelope_sha256"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_experience_events_project_position",
        "experience_events",
        ["project_id", "stream_position"],
    )
    op.create_index(
        "ix_experience_events_project_correlation_position",
        "experience_events",
        ["project_id", "correlation_id", "stream_position"],
    )
    op.execute(
        "CREATE TRIGGER trg_experience_events_no_update BEFORE UPDATE ON experience_events "
        "BEGIN SELECT RAISE(ABORT, 'experience_events are append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_experience_events_no_delete BEFORE DELETE ON experience_events "
        "BEGIN SELECT RAISE(ABORT, 'experience_events are append-only'); END"
    )

    op.create_table(
        "event_consumer_cursors",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("consumer_key", sa.String(length=128), nullable=False),
        sa.Column("last_ack_stream_position", sa.Integer(), nullable=False),
        sa.Column("last_delivered_stream_position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("last_ack_stream_position >= 0", name="ck_event_cursor_position"),
        sa.CheckConstraint(
            "last_ack_stream_position <= last_delivered_stream_position",
            name="ck_event_cursor_ack_delivered",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "owner_id", "consumer_key", name="uq_event_cursor_owner"),
    )

    op.create_table(
        "graph_change_sets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("base_graph_revision_id", sa.String(length=36), nullable=False),
        sa.Column("client_mutation_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("diff_json", sa.Text(), nullable=False),
        sa.Column("inverse_operations_json", sa.Text(), nullable=False),
        sa.Column("risk_json", sa.Text(), nullable=False),
        sa.Column("changeset_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=True),
        sa.Column("applied_graph_revision_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "state IN ('PENDING_APPROVAL', 'APPROVED', 'APPLIED', 'REJECTED')",
            name="ck_graph_change_sets_state",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        # Keep the immutable audit reference after a graph-history integrity
        # probe removes a corrupt revision; application code validates the
        # active base revision before creating the proposal.
        sa.UniqueConstraint("project_id", "client_mutation_id", name="uq_graph_change_sets_client_mutation"),
    )
    op.create_index("ix_graph_change_sets_project_created", "graph_change_sets", ["project_id", "created_at"])

    op.create_table(
        "approval_decisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=36), nullable=False),
        sa.Column("subject_sha256", sa.String(length=64), nullable=False),
        sa.Column("risk_sha256", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("decision IN ('APPROVED', 'REJECTED')", name="ck_approval_decisions_decision"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("subject_type", "subject_id", name="uq_approval_decision_subject"),
    )

    op.create_table(
        "control_config_documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("active_revision_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "control_config_revisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("parent_revision_id", sa.String(length=36), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("config_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["control_config_documents.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("document_id", "sequence", name="uq_control_config_revision_sequence"),
    )
    op.create_table(
        "config_change_sets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("audit_project_id", sa.String(length=36), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("base_config_revision_id", sa.String(length=36), nullable=False),
        sa.Column("operations_json", sa.Text(), nullable=False),
        sa.Column("risk_json", sa.Text(), nullable=False),
        sa.Column("changeset_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=True),
        sa.Column("applied_config_revision_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "state IN ('PENDING_APPROVAL', 'APPROVED', 'APPLIED', 'REJECTED')",
            name="ck_config_change_sets_state",
        ),
        sa.ForeignKeyConstraint(["audit_project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["base_config_revision_id"], ["control_config_revisions.id"], ondelete="RESTRICT"
        ),
    )

    op.create_table(
        "run_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("graph_revision_id", sa.String(length=36), nullable=False),
        sa.Column("graph_revision_sha256", sa.String(length=64), nullable=False),
        sa.Column("config_revision_id", sa.String(length=36), nullable=False),
        sa.Column("config_revision_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("operation_kind", sa.String(length=32), nullable=False),
        sa.Column("task_count", sa.Integer(), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("operation_kind = 'DRY_RUN'", name="ck_run_requests_f1_dry_run"),
        sa.CheckConstraint("task_count > 0", name="ck_run_requests_task_count"),
        sa.CheckConstraint(
            "state IN ('PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'TERMINAL')",
            name="ck_run_requests_state",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["graph_revision_id"], ["graph_revisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["config_revision_id"], ["control_config_revisions.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "egress_plans",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_request_id", sa.String(length=36), nullable=False),
        sa.Column("graph_revision_id", sa.String(length=36), nullable=False),
        sa.Column("graph_revision_sha256", sa.String(length=64), nullable=False),
        sa.Column("config_revision_id", sa.String(length=36), nullable=False),
        sa.Column("config_revision_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("endpoint", sa.String(length=512), nullable=True),
        sa.Column("data_classes_json", sa.Text(), nullable=False),
        sa.Column("task_count", sa.Integer(), nullable=False),
        sa.Column("cost_bound", sa.Integer(), nullable=False),
        sa.Column("gpu_count", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("mode = 'NONE'", name="ck_egress_plans_f1_none"),
        sa.CheckConstraint("endpoint IS NULL", name="ck_egress_plans_no_endpoint"),
        sa.CheckConstraint("cost_bound = 0 AND gpu_count = 0", name="ck_egress_plans_no_cost_gpu"),
        sa.ForeignKeyConstraint(["run_request_id"], ["run_requests.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("run_request_id", name="uq_egress_plan_request"),
    )
    op.create_table(
        "run_authorizations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_request_id", sa.String(length=36), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("graph_revision_id", sa.String(length=36), nullable=False),
        sa.Column("graph_revision_sha256", sa.String(length=64), nullable=False),
        sa.Column("config_revision_id", sa.String(length=36), nullable=False),
        sa.Column("config_revision_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("task_count", sa.Integer(), nullable=False),
        sa.Column("cost_bound", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("egress_plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "state IN ('APPROVED', 'CONSUMED', 'EXPIRED', 'INVALIDATED')", name="ck_run_authorizations_state"
        ),
        sa.ForeignKeyConstraint(["run_request_id"], ["run_requests.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approval_id"], ["approval_decisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["graph_revision_id"], ["graph_revisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["config_revision_id"], ["control_config_revisions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("run_request_id", name="uq_run_authorization_request"),
    )
    op.create_table(
        "dry_run_receipts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_request_id", sa.String(length=36), nullable=False),
        sa.Column("run_authorization_id", sa.String(length=36), nullable=False),
        sa.Column("terminal_state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "terminal_state IN ('DRY_RUN_SUCCEEDED', 'DRY_RUN_VALIDATION_FAILED')",
            name="ck_dry_run_receipts_state",
        ),
        sa.ForeignKeyConstraint(["run_request_id"], ["run_requests.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_authorization_id"], ["run_authorizations.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("run_authorization_id", name="uq_dry_run_receipt_authorization"),
    )

    document_id = str(uuid4())
    revision_id = str(uuid4())
    op.execute(
        sa.text(
            "INSERT INTO control_config_documents (id, active_revision_id, created_at) "
            "VALUES (:id, :revision_id, CURRENT_TIMESTAMP)"
        ).bindparams(id=document_id, revision_id=revision_id)
    )
    op.execute(
        sa.text(
            "INSERT INTO control_config_revisions "
            "(id, document_id, parent_revision_id, sequence, config_json, config_sha256, created_at) "
            "VALUES (:id, :document_id, NULL, 1, :config_json, :config_sha256, CURRENT_TIMESTAMP)"
        ).bindparams(
            id=revision_id,
            document_id=document_id,
            config_json='{"dry_run_max_tasks":1}',
            config_sha256="57f4bbbe1ea0090646f8c03b1c2836e96387b73d49bb8fb2cc609dbc0aded000",
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    for table in ("experience_events", "graph_change_sets", "config_change_sets", "run_requests"):
        count = connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError(f"cannot downgrade F1 with persisted {table}")

    op.drop_table("dry_run_receipts")
    op.drop_table("run_authorizations")
    op.drop_table("egress_plans")
    op.drop_table("run_requests")
    op.drop_table("config_change_sets")
    op.drop_table("control_config_revisions")
    op.drop_table("control_config_documents")
    op.drop_table("approval_decisions")
    op.drop_index("ix_graph_change_sets_project_created", table_name="graph_change_sets")
    op.drop_table("graph_change_sets")
    op.drop_table("event_consumer_cursors")
    op.execute("DROP TRIGGER trg_experience_events_no_delete")
    op.execute("DROP TRIGGER trg_experience_events_no_update")
    op.drop_index("ix_experience_events_project_correlation_position", table_name="experience_events")
    op.drop_index("ix_experience_events_project_position", table_name="experience_events")
    op.drop_table("experience_events")
