from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0011_c3_model_profiles_agent_proposals"
down_revision: str | None = "0010_c2_graph_branches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_f1_runtime_triggers() -> None:
    for name in (
        "f1_graph_revisions_content_immutable",
        "f1_graph_revisions_delete_immutable",
        "f1_control_config_revisions_content_immutable",
        "f1_control_config_revisions_delete_immutable",
        "f1_run_requests_content_immutable",
        "f1_egress_plans_update_immutable",
        "f1_egress_plans_delete_immutable",
        "f1_run_authorizations_binding_immutable",
        "f1_run_authorizations_delete_immutable",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")


def _create_runtime_triggers() -> None:
    op.execute(
        "CREATE TRIGGER f1_graph_revisions_content_immutable BEFORE UPDATE ON graph_revisions "
        "WHEN EXISTS (SELECT 1 FROM run_requests WHERE graph_revision_id = OLD.id) "
        "BEGIN SELECT RAISE(ABORT, 'graph revisions are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_graph_revisions_delete_immutable BEFORE DELETE ON graph_revisions "
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
        "CREATE TRIGGER f1_run_requests_content_immutable BEFORE UPDATE OF "
        "project_id, correlation_id, graph_revision_id, graph_revision_sha256, "
        "config_revision_id, config_revision_sha256, payload_sha256, operation_kind, "
        "task_count, request_json, request_sha256, model_profile_revision_id, "
        "model_profile_revision_sha256, branch_id, branch_head_version, selection_version, "
        "scope_json, scope_sha256, created_at ON run_requests "
        "BEGIN SELECT RAISE(ABORT, 'run request content is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_egress_plans_update_immutable BEFORE UPDATE ON egress_plans "
        "BEGIN SELECT RAISE(ABORT, 'egress plans are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_egress_plans_delete_immutable BEFORE DELETE ON egress_plans "
        "BEGIN SELECT RAISE(ABORT, 'egress plans cannot be deleted'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_run_authorizations_binding_immutable BEFORE UPDATE OF "
        "run_request_id, approval_id, request_sha256, egress_plan_sha256, graph_revision_id, "
        "graph_revision_sha256, config_revision_id, config_revision_sha256, payload_sha256, "
        "task_count, cost_bound, max_retries, model_profile_revision_id, "
        "model_profile_revision_sha256, branch_id, branch_head_version, selection_version, "
        "scope_sha256, endpoint_identity_sha256, cost_mode, currency, issued_at, expires_at "
        "ON run_authorizations "
        "BEGIN SELECT RAISE(ABORT, 'run authorization binding is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_run_authorizations_delete_immutable BEFORE DELETE ON run_authorizations "
        "BEGIN SELECT RAISE(ABORT, 'run authorizations cannot be deleted'); END"
    )


def upgrade() -> None:
    op.create_table(
        "providers",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("adapter_kind", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "adapter_kind = 'OPENAI_COMPATIBLE_CHAT_JSON_V1'",
            name="ck_providers_adapter_kind",
        ),
        sa.UniqueConstraint("provider_key", name="uq_providers_key"),
    )
    op.create_table(
        "model_profiles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("profile_key", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("role = 'CANVAS_AGENT'", name="ck_model_profiles_role"),
        sa.UniqueConstraint("profile_key", name="uq_model_profiles_key"),
    )
    op.create_table(
        "model_profile_revisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("model_profile_id", sa.String(length=36), nullable=False),
        sa.Column("provider_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("declaration_json", sa.Text(), nullable=False),
        sa.Column("declaration_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("sequence > 0", name="ck_model_profile_revisions_sequence"),
        sa.CheckConstraint(
            "length(declaration_sha256) = 64 AND declaration_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_model_profile_revisions_sha256",
        ),
        sa.ForeignKeyConstraint(["model_profile_id"], ["model_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "model_profile_id", "sequence", name="uq_model_profile_revision_sequence"
        ),
    )

    _drop_f1_runtime_triggers()
    with op.batch_alter_table("run_requests", recreate="always") as batch:
        batch.drop_constraint("ck_run_requests_f1_dry_run", type_="check")
        batch.create_check_constraint(
            "ck_run_requests_operation_kind",
            "operation_kind IN ('DRY_RUN', 'AGENT_PROPOSAL')",
        )
        batch.add_column(sa.Column("model_profile_revision_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("model_profile_revision_sha256", sa.String(64), nullable=True))
        batch.add_column(sa.Column("branch_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("branch_head_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("selection_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("scope_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("scope_sha256", sa.String(64), nullable=True))
        batch.create_foreign_key(
            "fk_run_requests_model_profile_revision",
            "model_profile_revisions",
            ["model_profile_revision_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    with op.batch_alter_table("egress_plans", recreate="always") as batch:
        batch.drop_constraint("ck_egress_plans_f1_none", type_="check")
        batch.drop_constraint("ck_egress_plans_no_endpoint", type_="check")
        batch.drop_constraint("ck_egress_plans_no_cost_gpu", type_="check")
        batch.create_check_constraint(
            "ck_egress_plans_mode", "mode IN ('NONE', 'MODEL_PROVIDER')"
        )
        batch.add_column(sa.Column("model_profile_revision_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("endpoint_identity_sha256", sa.String(64), nullable=True))
        batch.add_column(sa.Column("preview_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("cost_mode", sa.String(16), nullable=True))
        batch.add_column(sa.Column("currency", sa.String(3), nullable=True))
        batch.add_column(sa.Column("cost_bound_minor", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_egress_plans_model_profile_revision",
            "model_profile_revisions",
            ["model_profile_revision_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    with op.batch_alter_table("run_authorizations", recreate="always") as batch:
        batch.add_column(sa.Column("model_profile_revision_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("model_profile_revision_sha256", sa.String(64), nullable=True))
        batch.add_column(sa.Column("branch_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("branch_head_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("selection_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("scope_sha256", sa.String(64), nullable=True))
        batch.add_column(sa.Column("endpoint_identity_sha256", sa.String(64), nullable=True))
        batch.add_column(sa.Column("cost_mode", sa.String(16), nullable=True))
        batch.add_column(sa.Column("currency", sa.String(3), nullable=True))
        batch.create_foreign_key(
            "fk_run_authorizations_model_profile_revision",
            "model_profile_revisions",
            ["model_profile_revision_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.create_table(
        "agent_proposals",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_request_id", sa.String(length=36), nullable=False),
        sa.Column("model_profile_revision_id", sa.String(length=36), nullable=False),
        sa.Column("branch_id", sa.String(length=36), nullable=False),
        sa.Column("branch_head_version", sa.Integer(), nullable=False),
        sa.Column("selection_version", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=36), nullable=False),
        sa.Column("scope_json", sa.Text(), nullable=False),
        sa.Column("scope_sha256", sa.String(length=64), nullable=False),
        sa.Column("provider_payload_json", sa.Text(), nullable=False),
        sa.Column("provider_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("graph_change_set_id", sa.String(length=36), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "state IN ('PENDING_APPROVAL', 'PENDING_EXECUTION', 'DISPATCHING', "
            "'SUCCEEDED', 'FAILED', 'UNKNOWN_TERMINAL', 'REJECTED')",
            name="ck_agent_proposals_state",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_request_id"], ["run_requests.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["model_profile_revision_id"], ["model_profile_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["branch_id"], ["graph_branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["graph_change_set_id"], ["graph_change_sets.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("run_request_id", name="uq_agent_proposals_run_request"),
    )
    op.create_index(
        "ix_agent_proposals_project_created", "agent_proposals", ["project_id", "created_at"]
    )

    for table in ("providers", "model_profiles", "model_profile_revisions"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} are append-only'); END"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} are append-only'); END"
        )
    op.execute(
        "CREATE TRIGGER trg_agent_proposals_subject_immutable BEFORE UPDATE OF "
        "project_id, run_request_id, model_profile_revision_id, branch_id, branch_head_version, "
        "selection_version, node_id, scope_json, scope_sha256, provider_payload_json, "
        "provider_payload_sha256, created_at ON agent_proposals "
        "BEGIN SELECT RAISE(ABORT, 'agent proposal subject is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_agent_proposals_no_delete BEFORE DELETE ON agent_proposals "
        "BEGIN SELECT RAISE(ABORT, 'agent proposals cannot be deleted'); END"
    )
    _create_runtime_triggers()


def _restore_exact_f1_runtime_tables() -> None:
    # SQLite batch recreation emits a semantically equivalent but textually
    # different schema.  The F1 downgrade witness intentionally requires the
    # exact 0007 table DDL, so use legacy rename behavior and recreate the
    # three tables in their original column/constraint order.
    op.execute("PRAGMA legacy_alter_table=ON")
    op.execute("ALTER TABLE run_authorizations RENAME TO c3_run_authorizations")
    op.execute("ALTER TABLE egress_plans RENAME TO c3_egress_plans")
    op.execute("ALTER TABLE run_requests RENAME TO c3_run_requests")
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
        sa.ForeignKeyConstraint(
            ["graph_revision_id"], ["graph_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["config_revision_id"], ["control_config_revisions.id"], ondelete="RESTRICT"
        ),
    )
    op.execute(
        "INSERT INTO run_requests (id, project_id, correlation_id, graph_revision_id, "
        "graph_revision_sha256, config_revision_id, config_revision_sha256, payload_sha256, "
        "operation_kind, task_count, request_json, request_sha256, state, approval_id, created_at) "
        "SELECT id, project_id, correlation_id, graph_revision_id, graph_revision_sha256, "
        "config_revision_id, config_revision_sha256, payload_sha256, operation_kind, task_count, "
        "request_json, request_sha256, state, approval_id, created_at FROM c3_run_requests"
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
        sa.CheckConstraint(
            "cost_bound = 0 AND gpu_count = 0", name="ck_egress_plans_no_cost_gpu"
        ),
        sa.ForeignKeyConstraint(["run_request_id"], ["run_requests.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("run_request_id", name="uq_egress_plan_request"),
    )
    op.execute(
        "INSERT INTO egress_plans (id, run_request_id, graph_revision_id, graph_revision_sha256, "
        "config_revision_id, config_revision_sha256, payload_sha256, mode, endpoint, "
        "data_classes_json, task_count, cost_bound, gpu_count, max_retries, plan_sha256, created_at) "
        "SELECT id, run_request_id, graph_revision_id, graph_revision_sha256, config_revision_id, "
        "config_revision_sha256, payload_sha256, mode, endpoint, data_classes_json, task_count, "
        "cost_bound, gpu_count, max_retries, plan_sha256, created_at FROM c3_egress_plans"
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
            "state IN ('APPROVED', 'CONSUMED', 'EXPIRED', 'INVALIDATED')",
            name="ck_run_authorizations_state",
        ),
        sa.ForeignKeyConstraint(["run_request_id"], ["run_requests.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approval_id"], ["approval_decisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["graph_revision_id"], ["graph_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["config_revision_id"], ["control_config_revisions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("run_request_id", name="uq_run_authorization_request"),
    )
    op.execute(
        "INSERT INTO run_authorizations (id, run_request_id, approval_id, graph_revision_id, "
        "graph_revision_sha256, config_revision_id, config_revision_sha256, payload_sha256, "
        "task_count, cost_bound, max_retries, request_sha256, egress_plan_sha256, state, "
        "issued_at, expires_at, consumed_at) SELECT id, run_request_id, approval_id, "
        "graph_revision_id, graph_revision_sha256, config_revision_id, config_revision_sha256, "
        "payload_sha256, task_count, cost_bound, max_retries, request_sha256, egress_plan_sha256, "
        "state, issued_at, expires_at, consumed_at FROM c3_run_authorizations"
    )
    op.execute("DROP TABLE c3_run_authorizations")
    op.execute("DROP TABLE c3_egress_plans")
    op.execute("DROP TABLE c3_run_requests")
    op.execute("PRAGMA legacy_alter_table=OFF")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_agent_proposals_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_agent_proposals_subject_immutable")
    op.drop_index("ix_agent_proposals_project_created", table_name="agent_proposals")
    op.drop_table("agent_proposals")
    _drop_f1_runtime_triggers()
    _restore_exact_f1_runtime_tables()
    for table in ("model_profile_revisions", "model_profiles", "providers"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_delete")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_update")
        op.drop_table(table)
    # Restore the exact F1 trigger shapes through their original names.
    op.execute(
        "CREATE TRIGGER f1_graph_revisions_content_immutable BEFORE UPDATE ON graph_revisions "
        "WHEN EXISTS (SELECT 1 FROM run_requests WHERE graph_revision_id = OLD.id) "
        "BEGIN SELECT RAISE(ABORT, 'graph revisions are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_graph_revisions_delete_immutable BEFORE DELETE ON graph_revisions "
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
        "CREATE TRIGGER f1_run_requests_content_immutable BEFORE UPDATE OF project_id, "
        "correlation_id, graph_revision_id, graph_revision_sha256, config_revision_id, "
        "config_revision_sha256, payload_sha256, operation_kind, task_count, request_json, "
        "request_sha256, created_at ON run_requests "
        "BEGIN SELECT RAISE(ABORT, 'run request content is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_egress_plans_update_immutable BEFORE UPDATE ON egress_plans "
        "BEGIN SELECT RAISE(ABORT, 'egress plans are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_egress_plans_delete_immutable BEFORE DELETE ON egress_plans "
        "BEGIN SELECT RAISE(ABORT, 'egress plans cannot be deleted'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_run_authorizations_binding_immutable BEFORE UPDATE OF "
        "run_request_id, approval_id, request_sha256, egress_plan_sha256, graph_revision_id, "
        "graph_revision_sha256, config_revision_id, config_revision_sha256, payload_sha256, "
        "task_count, cost_bound, max_retries, issued_at, expires_at ON run_authorizations "
        "BEGIN SELECT RAISE(ABORT, 'run authorization binding is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER f1_run_authorizations_delete_immutable BEFORE DELETE ON run_authorizations "
        "BEGIN SELECT RAISE(ABORT, 'run authorizations cannot be deleted'); END"
    )
