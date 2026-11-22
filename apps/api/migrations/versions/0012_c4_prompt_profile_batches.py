from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0012_c4_prompt_profile_batches"
down_revision: str | None = "0011_c3_model_profiles_agent_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SHA_CHECK = "length({name}) = 64 AND {name} NOT GLOB '*[^0-9a-f]*'"


def _sha(column: str, constraint: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(SHA_CHECK.format(name=column), name=constraint)


def _no_update_or_delete(table: str, message: str) -> None:
    op.execute(
        f"CREATE TRIGGER c4_{table}_no_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{message} are append-only'); END"
    )
    op.execute(
        f"CREATE TRIGGER c4_{table}_no_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{message} are append-only'); END"
    )


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_model_profiles_no_update")
    op.execute("DROP TRIGGER IF EXISTS trg_model_profiles_no_delete")
    with op.batch_alter_table("model_profiles", recreate="always") as batch:
        batch.drop_constraint("ck_model_profiles_role", type_="check")
        batch.create_check_constraint(
            "ck_model_profiles_role",
            "role IN ('CANVAS_AGENT', 'PROMPT_EDITOR')",
        )
    op.execute(
        "CREATE TRIGGER trg_model_profiles_no_update BEFORE UPDATE ON model_profiles "
        "BEGIN SELECT RAISE(ABORT, 'model_profiles are append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_model_profiles_no_delete BEFORE DELETE ON model_profiles "
        "BEGIN SELECT RAISE(ABORT, 'model_profiles are append-only'); END"
    )

    for trigger in (
        "f1_graph_revisions_content_immutable",
        "f1_graph_revisions_delete_immutable",
        "f1_control_config_revisions_content_immutable",
        "f1_control_config_revisions_delete_immutable",
        "f1_run_requests_content_immutable",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    with op.batch_alter_table("run_requests", recreate="always") as batch:
        batch.drop_constraint("ck_run_requests_operation_kind", type_="check")
        batch.create_check_constraint(
            "ck_run_requests_operation_kind",
            "operation_kind IN ('DRY_RUN', 'AGENT_PROPOSAL', "
            "'PROMPT_PROFILE_EXTRACTION', 'PROMPT_BATCH_ITEM')",
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

    op.create_table(
        "prompt_profile_extractions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("branch_id", sa.String(36), nullable=False),
        sa.Column("branch_head_version", sa.Integer(), nullable=False),
        sa.Column("selection_version", sa.Integer(), nullable=False),
        sa.Column("graph_revision_id", sa.String(36), nullable=False),
        sa.Column("graph_revision_sha256", sa.String(64), nullable=False),
        sa.Column("config_revision_id", sa.String(36), nullable=False),
        sa.Column("config_revision_sha256", sa.String(64), nullable=False),
        sa.Column("model_profile_revision_id", sa.String(36), nullable=False),
        sa.Column("model_profile_revision_sha256", sa.String(64), nullable=False),
        sa.Column("source_node_id", sa.String(36), nullable=False),
        sa.Column("source_document_sha256", sa.String(64), nullable=False),
        sa.Column("provider_payload_json", sa.Text(), nullable=False),
        sa.Column("provider_payload_sha256", sa.String(64), nullable=False),
        sa.Column("run_request_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=True),
        sa.Column("output_sha256", sa.String(64), nullable=True),
        sa.Column("materialized_resource_revision_id", sa.String(36), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "branch_head_version > 0 AND selection_version > 0",
            name="ck_prompt_profile_extractions_versions",
        ),
        sa.CheckConstraint(
            "state IN ('PENDING_APPROVAL', 'PENDING_EXECUTION', 'DISPATCHING', "
            "'SUCCEEDED', 'FAILED', 'UNKNOWN_TERMINAL', 'REJECTED', "
            "'DRAFT_REJECTED', 'MATERIALIZED')",
            name="ck_prompt_profile_extractions_state",
        ),
        _sha("graph_revision_sha256", "ck_prompt_profile_extractions_graph_sha"),
        _sha("config_revision_sha256", "ck_prompt_profile_extractions_config_sha"),
        _sha("model_profile_revision_sha256", "ck_prompt_profile_extractions_model_sha"),
        _sha("source_document_sha256", "ck_prompt_profile_extractions_source_sha"),
        _sha("provider_payload_sha256", "ck_prompt_profile_extractions_payload_sha"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["branch_id"], ["graph_branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["graph_revision_id"], ["graph_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["config_revision_id"], ["control_config_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["model_profile_revision_id"],
            ["model_profile_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_request_id"], ["run_requests.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["materialized_resource_revision_id"],
            ["resource_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("run_request_id", name="uq_prompt_profile_extractions_request"),
    )
    op.create_index(
        "ix_prompt_profile_extractions_project_created",
        "prompt_profile_extractions",
        ["project_id", "created_at"],
    )

    op.create_table(
        "prompt_profile_certifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("resource_revision_id", sa.String(36), nullable=False),
        sa.Column("source_example_sha256", sa.String(64), nullable=False),
        sa.Column("extractor_schema_version", sa.Integer(), nullable=False),
        sa.Column("rule_program_version", sa.Integer(), nullable=False),
        sa.Column("rule_code_set_sha256", sa.String(64), nullable=False),
        sa.Column("profile_payload_sha256", sa.String(64), nullable=False),
        sa.Column("extraction_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "extractor_schema_version > 0 AND rule_program_version > 0",
            name="ck_prompt_profile_certifications_versions",
        ),
        _sha("source_example_sha256", "ck_prompt_profile_certifications_source_sha"),
        _sha("rule_code_set_sha256", "ck_prompt_profile_certifications_codes_sha"),
        _sha("profile_payload_sha256", "ck_prompt_profile_certifications_payload_sha"),
        sa.ForeignKeyConstraint(
            ["resource_revision_id"], ["resource_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"], ["prompt_profile_extractions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "resource_revision_id", name="uq_prompt_profile_certifications_revision"
        ),
        sa.UniqueConstraint("extraction_id", name="uq_prompt_profile_certifications_extraction"),
    )

    op.create_table(
        "prompt_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("client_mutation_id", sa.String(36), nullable=False),
        sa.Column("create_request_sha256", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("selection_mode", sa.String(16), nullable=False),
        sa.Column("selection_json", sa.Text(), nullable=False),
        sa.Column("binding_key", sa.String(64), nullable=False),
        sa.Column("base_graph_revision_id", sa.String(36), nullable=False),
        sa.Column("base_graph_revision_sha256", sa.String(64), nullable=False),
        sa.Column("branch_id", sa.String(36), nullable=False),
        sa.Column("expected_branch_head_revision_id", sa.String(36), nullable=False),
        sa.Column("expected_branch_head_version", sa.Integer(), nullable=False),
        sa.Column("expected_selection_version", sa.Integer(), nullable=False),
        sa.Column("application_target_mode", sa.String(16), nullable=False),
        sa.Column("new_branch_name", sa.String(100), nullable=True),
        sa.Column("model_profile_revision_id", sa.String(36), nullable=False),
        sa.Column("model_profile_revision_sha256", sa.String(64), nullable=False),
        sa.Column("config_revision_id", sa.String(36), nullable=False),
        sa.Column("config_revision_sha256", sa.String(64), nullable=False),
        sa.Column("item_set_sha256", sa.String(64), nullable=False),
        sa.Column("request_set_sha256", sa.String(64), nullable=False),
        sa.Column("approval_id", sa.String(36), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("final_decision_set_sha256", sa.String(64), nullable=True),
        sa.Column("graph_change_set_id", sa.String(36), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("finalized_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("selection_mode IN ('SELECTED', 'ALL')", name="ck_prompt_batches_selection"),
        sa.CheckConstraint(
            "application_target_mode IN ('DIRECT', 'NEW_BRANCH')",
            name="ck_prompt_batches_application_target",
        ),
        sa.CheckConstraint(
            "(application_target_mode = 'DIRECT' AND new_branch_name IS NULL) OR "
            "(application_target_mode = 'NEW_BRANCH' AND length(new_branch_name) BETWEEN 1 AND 100)",
            name="ck_prompt_batches_new_branch_shape",
        ),
        sa.CheckConstraint(
            "expected_branch_head_version > 0 AND expected_selection_version > 0 AND version > 0",
            name="ck_prompt_batches_versions",
        ),
        sa.CheckConstraint(
            "phase IN ('PENDING_APPROVAL', 'PENDING_EXECUTION', 'IN_PROGRESS', "
            "'REVIEW_PENDING', 'RETRY_APPROVAL_REQUIRED', 'FINALIZED', 'REJECTED')",
            name="ck_prompt_batches_phase",
        ),
        _sha("base_graph_revision_sha256", "ck_prompt_batches_graph_sha"),
        _sha("model_profile_revision_sha256", "ck_prompt_batches_model_sha"),
        _sha("config_revision_sha256", "ck_prompt_batches_config_sha"),
        _sha("item_set_sha256", "ck_prompt_batches_item_set_sha"),
        _sha("request_set_sha256", "ck_prompt_batches_request_set_sha"),
        _sha("create_request_sha256", "ck_prompt_batches_create_request_sha"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["branch_id"], ["graph_branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["base_graph_revision_id"], ["graph_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["expected_branch_head_revision_id"],
            ["graph_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_profile_revision_id"],
            ["model_profile_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["config_revision_id"], ["control_config_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["approval_decisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["graph_change_set_id"], ["graph_change_sets.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("project_id", "request_set_sha256", name="uq_prompt_batches_request_set"),
        sa.UniqueConstraint(
            "project_id", "client_mutation_id", name="uq_prompt_batches_client_mutation"
        ),
    )
    op.create_index(
        "ix_prompt_batches_project_created", "prompt_batches", ["project_id", "created_at"]
    )

    op.create_table(
        "prompt_batch_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("shot_id", sa.String(36), nullable=False),
        sa.Column("shot_node_id", sa.String(36), nullable=False),
        sa.Column("prompt_node_id", sa.String(36), nullable=False),
        sa.Column("shot_order", sa.Integer(), nullable=False),
        sa.Column("base_document_json", sa.Text(), nullable=False),
        sa.Column("base_document_sha256", sa.String(64), nullable=False),
        sa.Column("protected_manifest_json", sa.Text(), nullable=False),
        sa.Column("protected_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("editable_manifest_json", sa.Text(), nullable=False),
        sa.Column("editable_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("prompt_profile_resource_revision_id", sa.String(36), nullable=False),
        sa.Column("prompt_profile_revision_sha256", sa.String(64), nullable=False),
        sa.Column("prompt_profile_scope_kind", sa.String(16), nullable=False),
        sa.Column("prompt_profile_scope_id", sa.String(36), nullable=True),
        sa.Column("current_attempt_id", sa.String(36), nullable=True),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("shot_order >= 0", name="ck_prompt_batch_items_order"),
        sa.CheckConstraint("decision_version >= 0", name="ck_prompt_batch_items_decision_version"),
        sa.CheckConstraint(
            "prompt_profile_scope_kind IN ('PROJECT', 'SHOT', 'NODE')",
            name="ck_prompt_batch_items_profile_scope",
        ),
        _sha("base_document_sha256", "ck_prompt_batch_items_document_sha"),
        _sha("protected_manifest_sha256", "ck_prompt_batch_items_protected_sha"),
        _sha("editable_manifest_sha256", "ck_prompt_batch_items_editable_sha"),
        _sha("prompt_profile_revision_sha256", "ck_prompt_batch_items_profile_sha"),
        sa.ForeignKeyConstraint(["batch_id"], ["prompt_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["prompt_profile_resource_revision_id"],
            ["resource_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("batch_id", "shot_id", name="uq_prompt_batch_items_shot"),
        sa.UniqueConstraint("batch_id", "prompt_node_id", name="uq_prompt_batch_items_prompt_node"),
    )
    op.create_index("ix_prompt_batch_items_batch_order", "prompt_batch_items", ["batch_id", "shot_order"])

    op.create_table(
        "prompt_batch_item_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("parent_attempt_id", sa.String(36), nullable=True),
        sa.Column("run_request_id", sa.String(36), nullable=False),
        sa.Column("provider_payload_json", sa.Text(), nullable=False),
        sa.Column("provider_payload_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=True),
        sa.Column("output_sha256", sa.String(64), nullable=True),
        sa.Column("compiled_document_json", sa.Text(), nullable=True),
        sa.Column("compiled_document_sha256", sa.String(64), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("attempt_number > 0", name="ck_prompt_batch_item_attempts_number"),
        sa.CheckConstraint(
            "state IN ('PENDING_APPROVAL', 'PENDING_EXECUTION', 'DISPATCHING', "
            "'SUCCEEDED', 'FAILED', 'UNKNOWN_TERMINAL', 'REJECTED')",
            name="ck_prompt_batch_item_attempts_state",
        ),
        _sha("provider_payload_sha256", "ck_prompt_batch_item_attempts_payload_sha"),
        sa.ForeignKeyConstraint(["item_id"], ["prompt_batch_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["parent_attempt_id"], ["prompt_batch_item_attempts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["run_request_id"], ["run_requests.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("item_id", "attempt_number", name="uq_prompt_batch_item_attempt_number"),
        sa.UniqueConstraint("run_request_id", name="uq_prompt_batch_item_attempt_request"),
    )

    op.create_table(
        "prompt_batch_item_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("expected_output_sha256", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("decision_sequence", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("decision IN ('ACCEPTED', 'REJECTED')", name="ck_prompt_batch_item_decisions_value"),
        sa.CheckConstraint("decision_sequence > 0", name="ck_prompt_batch_item_decisions_sequence"),
        _sha("expected_output_sha256", "ck_prompt_batch_item_decisions_output_sha"),
        sa.ForeignKeyConstraint(["item_id"], ["prompt_batch_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["prompt_batch_item_attempts.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("item_id", "decision_sequence", name="uq_prompt_batch_item_decision_sequence"),
    )

    op.create_table(
        "c4_mutation_receipts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("client_mutation_id", sa.String(36), nullable=False),
        sa.Column("operation_kind", sa.String(64), nullable=False),
        sa.Column("subject_type", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.String(36), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        _sha("request_sha256", "ck_c4_mutation_receipts_request_sha"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "project_id", "client_mutation_id", name="uq_c4_mutation_receipts_client"
        ),
    )
    op.create_index(
        "ix_c4_mutation_receipts_subject",
        "c4_mutation_receipts",
        ["subject_type", "subject_id"],
    )

    op.execute(
        "CREATE TRIGGER c4_prompt_profile_extractions_subject_immutable BEFORE UPDATE OF "
        "project_id, correlation_id, branch_id, branch_head_version, selection_version, "
        "graph_revision_id, graph_revision_sha256, config_revision_id, config_revision_sha256, "
        "model_profile_revision_id, model_profile_revision_sha256, source_node_id, "
        "source_document_sha256, provider_payload_json, provider_payload_sha256, "
        "run_request_id, created_at ON prompt_profile_extractions "
        "BEGIN SELECT RAISE(ABORT, 'prompt profile extraction subject is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER c4_prompt_profile_extractions_no_delete BEFORE DELETE ON prompt_profile_extractions "
        "BEGIN SELECT RAISE(ABORT, 'prompt profile extractions cannot be deleted'); END"
    )
    _no_update_or_delete(
        "prompt_profile_certifications", "prompt profile certifications"
    )
    op.execute(
        "CREATE TRIGGER c4_prompt_batches_subject_immutable BEFORE UPDATE OF "
        "project_id, client_mutation_id, create_request_sha256, correlation_id, "
        "selection_mode, selection_json, binding_key, "
        "base_graph_revision_id, base_graph_revision_sha256, branch_id, "
        "expected_branch_head_revision_id, expected_branch_head_version, "
        "expected_selection_version, application_target_mode, new_branch_name, "
        "model_profile_revision_id, model_profile_revision_sha256, config_revision_id, "
        "config_revision_sha256, item_set_sha256, request_set_sha256, created_at "
        "ON prompt_batches BEGIN SELECT RAISE(ABORT, 'prompt batch subject is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER c4_prompt_batches_no_delete BEFORE DELETE ON prompt_batches "
        "BEGIN SELECT RAISE(ABORT, 'prompt batches cannot be deleted'); END"
    )
    op.execute(
        "CREATE TRIGGER c4_prompt_batch_items_subject_immutable BEFORE UPDATE OF "
        "batch_id, shot_id, shot_node_id, prompt_node_id, shot_order, base_document_json, "
        "base_document_sha256, protected_manifest_json, protected_manifest_sha256, "
        "editable_manifest_json, editable_manifest_sha256, prompt_profile_resource_revision_id, "
        "prompt_profile_revision_sha256, prompt_profile_scope_kind, prompt_profile_scope_id, "
        "created_at ON prompt_batch_items "
        "BEGIN SELECT RAISE(ABORT, 'prompt batch item subject is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER c4_prompt_batch_items_current_attempt_matches BEFORE UPDATE OF current_attempt_id "
        "ON prompt_batch_items WHEN NEW.current_attempt_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM prompt_batch_item_attempts a WHERE a.id = NEW.current_attempt_id "
        "AND a.item_id = NEW.id) BEGIN SELECT RAISE(ABORT, 'current attempt must belong to item'); END"
    )
    op.execute(
        "CREATE TRIGGER c4_prompt_batch_items_no_delete BEFORE DELETE ON prompt_batch_items "
        "BEGIN SELECT RAISE(ABORT, 'prompt batch items cannot be deleted'); END"
    )
    op.execute(
        "CREATE TRIGGER c4_prompt_batch_item_attempts_subject_immutable BEFORE UPDATE OF "
        "item_id, attempt_number, parent_attempt_id, run_request_id, provider_payload_json, "
        "provider_payload_sha256, created_at ON prompt_batch_item_attempts "
        "BEGIN SELECT RAISE(ABORT, 'prompt batch item attempt subject is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER c4_prompt_batch_item_attempts_terminal_immutable BEFORE UPDATE OF "
        "output_json, output_sha256, compiled_document_json, compiled_document_sha256, "
        "failure_code, dispatched_at, completed_at ON prompt_batch_item_attempts "
        "WHEN OLD.state IN ('SUCCEEDED', 'FAILED', 'UNKNOWN_TERMINAL', 'REJECTED') "
        "BEGIN SELECT RAISE(ABORT, 'terminal attempt is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER c4_prompt_batch_item_attempts_no_delete BEFORE DELETE ON prompt_batch_item_attempts "
        "BEGIN SELECT RAISE(ABORT, 'prompt batch item attempts cannot be deleted'); END"
    )
    _no_update_or_delete(
        "prompt_batch_item_decisions", "prompt batch item decisions"
    )
    _no_update_or_delete("c4_mutation_receipts", "C4 mutation receipts")


def downgrade() -> None:
    for table in (
        "c4_mutation_receipts",
        "prompt_batch_item_decisions",
        "prompt_batch_item_attempts",
        "prompt_batch_items",
        "prompt_batches",
        "prompt_profile_certifications",
        "prompt_profile_extractions",
    ):
        op.drop_table(table)
    op.execute("DROP TRIGGER IF EXISTS trg_model_profiles_no_update")
    op.execute("DROP TRIGGER IF EXISTS trg_model_profiles_no_delete")
    with op.batch_alter_table("model_profiles", recreate="always") as batch:
        batch.drop_constraint("ck_model_profiles_role", type_="check")
        batch.create_check_constraint("ck_model_profiles_role", "role = 'CANVAS_AGENT'")
    op.execute(
        "CREATE TRIGGER trg_model_profiles_no_update BEFORE UPDATE ON model_profiles "
        "BEGIN SELECT RAISE(ABORT, 'model_profiles are append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_model_profiles_no_delete BEFORE DELETE ON model_profiles "
        "BEGIN SELECT RAISE(ABORT, 'model_profiles are append-only'); END"
    )
