from collections.abc import Sequence

from alembic import op


revision: str = "0008_f1_control_integrity"
down_revision: str | None = "0007_f1_event_spine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TRIGGER f1_graph_revisions_content_immutable "
        "BEFORE UPDATE ON graph_revisions "
        "WHEN EXISTS (SELECT 1 FROM run_requests WHERE graph_revision_id = OLD.id) "
        "BEGIN SELECT RAISE(ABORT, 'graph revisions are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_graph_revisions_delete_immutable "
        "BEFORE DELETE ON graph_revisions "
        "WHEN EXISTS (SELECT 1 FROM run_requests WHERE graph_revision_id = OLD.id) "
        "BEGIN SELECT RAISE(ABORT, 'graph revisions cannot be deleted'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_control_config_revisions_content_immutable "
        "BEFORE UPDATE ON control_config_revisions "
        "WHEN EXISTS (SELECT 1 FROM run_requests WHERE config_revision_id = OLD.id) "
        "BEGIN SELECT RAISE(ABORT, 'control config revisions are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_control_config_revisions_delete_immutable "
        "BEFORE DELETE ON control_config_revisions "
        "WHEN EXISTS (SELECT 1 FROM run_requests WHERE config_revision_id = OLD.id) "
        "BEGIN SELECT RAISE(ABORT, 'control config revisions cannot be deleted'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_graph_change_sets_content_immutable "
        "BEFORE UPDATE OF project_id, correlation_id, base_graph_revision_id, "
        "client_mutation_id, snapshot_json, snapshot_hash, diff_json, "
        "inverse_operations_json, risk_json, changeset_sha256, created_at "
        "ON graph_change_sets "
        "BEGIN SELECT RAISE(ABORT, 'graph change set content is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_graph_change_sets_delete_immutable "
        "BEFORE DELETE ON graph_change_sets "
        "BEGIN SELECT RAISE(ABORT, 'graph change sets cannot be deleted'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_config_change_sets_content_immutable "
        "BEFORE UPDATE OF audit_project_id, correlation_id, base_config_revision_id, "
        "operations_json, risk_json, changeset_sha256, created_at "
        "ON config_change_sets "
        "BEGIN SELECT RAISE(ABORT, 'config change set content is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_config_change_sets_delete_immutable "
        "BEFORE DELETE ON config_change_sets "
        "BEGIN SELECT RAISE(ABORT, 'config change sets cannot be deleted'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_approval_decisions_update_immutable "
        "BEFORE UPDATE ON approval_decisions "
        "BEGIN SELECT RAISE(ABORT, 'approval decisions are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_approval_decisions_delete_immutable "
        "BEFORE DELETE ON approval_decisions "
        "BEGIN SELECT RAISE(ABORT, 'approval decisions cannot be deleted'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_run_requests_content_immutable "
        "BEFORE UPDATE OF project_id, correlation_id, graph_revision_id, "
        "graph_revision_sha256, config_revision_id, config_revision_sha256, "
        "payload_sha256, operation_kind, task_count, request_json, request_sha256, "
        "created_at ON run_requests "
        "BEGIN SELECT RAISE(ABORT, 'run request content is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_egress_plans_update_immutable "
        "BEFORE UPDATE ON egress_plans "
        "BEGIN SELECT RAISE(ABORT, 'egress plans are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_egress_plans_delete_immutable "
        "BEFORE DELETE ON egress_plans "
        "BEGIN SELECT RAISE(ABORT, 'egress plans cannot be deleted'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_run_authorizations_binding_immutable "
        "BEFORE UPDATE OF run_request_id, approval_id, request_sha256, "
        "egress_plan_sha256, graph_revision_id, graph_revision_sha256, "
        "config_revision_id, config_revision_sha256, payload_sha256, task_count, "
        "cost_bound, max_retries, issued_at, expires_at ON run_authorizations "
        "BEGIN SELECT RAISE(ABORT, 'run authorization binding is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_run_authorizations_delete_immutable "
        "BEFORE DELETE ON run_authorizations "
        "BEGIN SELECT RAISE(ABORT, 'run authorizations cannot be deleted'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_dry_run_receipts_update_immutable "
        "BEFORE UPDATE ON dry_run_receipts "
        "BEGIN SELECT RAISE(ABORT, 'dry-run receipts are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_dry_run_receipts_delete_immutable "
        "BEFORE DELETE ON dry_run_receipts "
        "BEGIN SELECT RAISE(ABORT, 'dry-run receipts cannot be deleted'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS f1_dry_run_receipts_delete_immutable")
    op.execute("DROP TRIGGER IF EXISTS f1_dry_run_receipts_update_immutable")
    op.execute("DROP TRIGGER IF EXISTS f1_run_authorizations_delete_immutable")
    op.execute("DROP TRIGGER IF EXISTS f1_run_authorizations_binding_immutable")
    op.execute("DROP TRIGGER IF EXISTS f1_egress_plans_delete_immutable")
    op.execute("DROP TRIGGER IF EXISTS f1_egress_plans_update_immutable")
    op.execute("DROP TRIGGER IF EXISTS f1_run_requests_content_immutable")
    op.execute("DROP TRIGGER IF EXISTS f1_approval_decisions_delete_immutable")
    op.execute("DROP TRIGGER IF EXISTS f1_approval_decisions_update_immutable")
    op.execute("DROP TRIGGER IF EXISTS f1_config_change_sets_delete_immutable")
    op.execute("DROP TRIGGER IF EXISTS f1_config_change_sets_content_immutable")
    op.execute("DROP TRIGGER IF EXISTS f1_graph_change_sets_delete_immutable")
    op.execute("DROP TRIGGER IF EXISTS f1_graph_change_sets_content_immutable")
    op.execute("DROP TRIGGER IF EXISTS f1_control_config_revisions_delete_immutable")
    op.execute("DROP TRIGGER IF EXISTS f1_control_config_revisions_content_immutable")
    op.execute("DROP TRIGGER IF EXISTS f1_graph_revisions_delete_immutable")
    op.execute("DROP TRIGGER IF EXISTS f1_graph_revisions_content_immutable")
