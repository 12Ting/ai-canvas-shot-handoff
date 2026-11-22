from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from ai_canvas.application.branch_service import BranchService
from ai_canvas.application.control_config_service import ControlConfigService
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.model_profile_service import ModelProfileService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.prompt_profile_extraction_service import (
    PromptProfileExtractionService,
)
from ai_canvas.application.resource_catalog_service import ResourceCatalogService
from ai_canvas.application.run_control_service import RunControlService
from ai_canvas.contracts.graph import CreateGraphRevisionRequest
from ai_canvas.contracts.prompt_profile_extractions import (
    CreatePromptProfileExtractionRequest,
    MaterializePromptProfileRequest,
)
from ai_canvas.infrastructure.db.models import (
    ExperienceEventRow,
    PromptProfileCertificationRow,
    PromptProfileExtractionRow,
    RunAuthorizationRow,
    RunRequestRow,
)
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)
from ai_canvas.infrastructure.model_providers.openai_compatible import ModelProviderResult


class FakeAdapter:
    def __init__(self) -> None:
        self.calls = []

    def send(self, **kwargs):
        self.calls.append(kwargs)
        return ModelProviderResult(
            output_json=(
                b'{"kind":"prompt_profile_draft","schema_version":1,'
                b'"content_order":["COMPOSITION_CAMERA","LIGHT_COLOR_MATERIAL_STYLE"],'
                b'"detail_density":"HIGH","format_rule_codes":["SECTION_PER_BLOCK"],'
                b'"camera_rule_codes":["EXPLICIT_SHOT_SIZE"],'
                b'"positive_rule_codes":["INCLUDE_LIGHTING"],'
                b'"negative_rule_codes":["NO_TEXT_WATERMARK"],'
                b'"applicability_codes":["GENERAL_VISUAL_PROMPT"],'
                b'"summary":"Reusable structural profile"}'
            ),
            response_sha256="f" * 64,
            usage={"total_tokens": 10},
            latency_ms=3,
        )


def _activate_prompt_editor(
    project_id,
    control,
    *,
    allowed_content_categories: list[str] | None = None,
) -> None:
    pending = ModelProfileService(control).request_declaration(
        audit_project_id=project_id,
        base_config_revision_id=control.get_active().id,
        provider={
            "provider_key": "prompt-loopback",
            "display_name": "Prompt loopback",
            "adapter_kind": "OPENAI_COMPATIBLE_CHAT_JSON_V1",
        },
        profile_key="prompt-editor-default",
        revision={
            "role": "PROMPT_EDITOR",
            "model_id": "prompt-editor-v1",
            "model_version": "1",
            "endpoint_url": "http://127.0.0.1:8765/v1/chat/completions",
            "locality": "LOCAL_LOOPBACK",
            "data_residency": "LOCAL_DEVICE",
            "allowed_content_categories": allowed_content_categories
            or [
                    "USER_INSTRUCTION",
                    "PROMPT_EXAMPLE_DOCUMENT",
                    "PROMPT_TARGET_DOCUMENT",
                    "PROMPT_PROFILE_RULES",
                    "PRESERVE_CONSTRAINTS",
                ],
            "retention_policy": "NO_RETENTION_DECLARED",
            "retention_note": "",
            "tool_permissions": [
                "PROMPT_PROFILE_DRAFT",
                "PROMPT_BATCH_ITEM_DRAFT",
            ],
            "cost_mode": "HARD",
            "currency": "CNY",
            "cost_bound_minor": 0,
            "max_output_tokens": 2048,
            "timeout_seconds": 10,
            "credential_kind": "NONE",
            "fallback_model_profile_revision_id": None,
        },
        actor_id="local-control-admin",
    )
    control.approve(
        pending.id,
        expected_changeset_sha256=pending.changeset_sha256,
        actor_id="local-control-admin",
    )
    control.apply(
        pending.id,
        expected_changeset_sha256=pending.changeset_sha256,
        actor_id="local-control-admin",
    )


def test_extraction_approval_execute_and_atomic_materialization(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        session_factory = make_session_factory(engine)
        projects = ProjectService(session_factory)
        project = projects.create("C4 extraction")
        prompt_node_id = uuid4()
        block_id = uuid4()
        source_text = "Mira in a studio, amber rim light"
        graphs = GraphService(session_factory)
        graphs.create_revision(
            project.id,
            CreateGraphRevisionRequest.model_validate(
                {
                    "base_revision_id": project.active_revision_id,
                    "client_mutation_id": uuid4(),
                    "snapshot": {
                        "schema_version": 3,
                        "nodes": [
                            {
                                "id": prompt_node_id,
                                "kind": "prompt",
                                "position": {"x": 0, "y": 0},
                                "data": {
                                    "document": {
                                        "schema_version": 1,
                                        "blocks": [
                                            {
                                                "id": block_id,
                                                "kind": "FREEFORM",
                                                "text": source_text,
                                                "locked": False,
                                            }
                                        ],
                                        "rendered_text": source_text,
                                        "spans": [
                                            {
                                                "block_id": block_id,
                                                "start_utf16": 0,
                                                "end_utf16": len(source_text),
                                            }
                                        ],
                                    }
                                },
                            }
                        ],
                        "edges": [],
                        "shots": [],
                        "resource_bindings": [],
                    },
                }
            ),
        )
        project = projects.get(project.id)
        workspace = BranchService(session_factory, graphs).workspace(project.id)
        selected = next(branch for branch in workspace.branches if branch.is_selected)
        events = EventSpineService(session_factory)
        control = ControlConfigService(session_factory, events)
        _activate_prompt_editor(project.id, control)
        adapter = FakeAdapter()
        service = PromptProfileExtractionService(
            session_factory,
            graphs,
            RunControlService(session_factory, events),
            ResourceCatalogService(session_factory, events),
            events,
            adapter=adapter,
        )
        requested = service.request(
            project.id,
            CreatePromptProfileExtractionRequest(
                base_graph_revision_id=project.active_revision_id,
                branch_id=project.selected_branch_id,
                expected_branch_head_revision_id=project.active_revision_id,
                expected_branch_head_version=selected.head.version,
                expected_selection_version=project.selection_version,
                source_node_id=prompt_node_id,
                instruction="Extract reusable form only",
            ),
            actor_id="local-session",
        )
        assert requested.state == "PENDING_APPROVAL"
        assert adapter.calls == []
        assert requested.run_request.operation_kind == "PROMPT_PROFILE_EXTRACTION"
        assert requested.run_request.egress_plan.gpu_count == 0
        assert requested.run_request.egress_plan.media_execution is False
        assert requested.run_request.egress_plan.preview["contexts"][0]["content"] == (
            "Extract reusable form only"
        )
        assert requested.run_request.egress_plan.preview["contexts"][1]["content"][
            "rendered_text"
        ] == source_text
        approved = service.approve(
            project.id,
            requested.id,
            expected_request_sha256=requested.run_request.request_sha256,
            actor_id="local-session",
        )
        assert approved.state == "PENDING_EXECUTION"
        assert adapter.calls == []
        with session_factory() as session:
            assert list(session.scalars(select(RunAuthorizationRow))) == []
        succeeded = service.execute(project.id, requested.id, actor_id="local-session")
        assert succeeded.state == "SUCCEEDED"
        assert len(adapter.calls) == 1
        assert succeeded.compiled_profile is not None
        with session_factory() as session:
            authorizations = list(session.scalars(select(RunAuthorizationRow)))
            assert len(authorizations) == 1
            assert authorizations[0].state == "CONSUMED"
            assert authorizations[0].consumed_at is not None
        materialized = service.materialize(
            project.id,
            requested.id,
            MaterializePromptProfileRequest(
                expected_output_sha256=succeeded.output_sha256,
                name="Extracted prompt profile",
            ),
            actor_id="local-session",
        )
        assert materialized.state == "MATERIALIZED"
        assert materialized.materialized_resource_revision_id is not None
        with session_factory() as session:
            certification = session.scalar(select(PromptProfileCertificationRow))
            assert certification is not None
            assert certification.resource_revision_id == str(
                materialized.materialized_resource_revision_id
            )

        interrupted = service.request(
            project.id,
            CreatePromptProfileExtractionRequest(
                base_graph_revision_id=project.active_revision_id,
                branch_id=project.selected_branch_id,
                expected_branch_head_revision_id=project.active_revision_id,
                expected_branch_head_version=selected.head.version,
                expected_selection_version=project.selection_version,
                source_node_id=prompt_node_id,
                instruction="Extract a second reusable form",
            ),
            actor_id="local-session",
        )
        service.approve(
            project.id,
            interrupted.id,
            expected_request_sha256=interrupted.run_request.request_sha256,
            actor_id="local-session",
        )
        with session_factory.begin() as session:
            interrupted_row = session.get(
                PromptProfileExtractionRow, str(interrupted.id)
            )
            assert interrupted_row is not None
            interrupted_row.state = "DISPATCHING"
            run_row = session.get(RunRequestRow, interrupted_row.run_request_id)
            assert run_row is not None
            run_row.state = "APPROVED"
        assert service.reconcile_dispatching() == 1
        assert service.reconcile_dispatching() == 0
        recovered = service.get(project.id, interrupted.id)
        assert recovered.state == "UNKNOWN_TERMINAL"
        assert recovered.failure_code == "RESTART_DURING_DISPATCH"
        with session_factory() as session:
            run_row = session.get(RunRequestRow, str(interrupted.run_request.id))
            assert run_row is not None
            assert run_row.state == "TERMINAL"
            recovery_events = list(
                session.scalars(
                    select(ExperienceEventRow).where(
                        ExperienceEventRow.subject_id == str(interrupted.id),
                        ExperienceEventRow.event_type
                        == "prompt_profile_extraction.unknown_terminal",
                    )
                )
            )
            assert len(recovery_events) == 1

        _activate_prompt_editor(
            project.id,
            control,
            allowed_content_categories=["USER_INSTRUCTION"],
        )
        with session_factory() as session:
            request_ids_before = list(session.scalars(select(RunRequestRow.id)))
        with pytest.raises(ValueError, match="content categories"):
            service.request(
                project.id,
                CreatePromptProfileExtractionRequest(
                    base_graph_revision_id=project.active_revision_id,
                    branch_id=project.selected_branch_id,
                    expected_branch_head_revision_id=project.active_revision_id,
                    expected_branch_head_version=selected.head.version,
                    expected_selection_version=project.selection_version,
                    source_node_id=prompt_node_id,
                    instruction="Extract reusable form only",
                ),
                actor_id="local-session",
            )
        with session_factory() as session:
            assert list(session.scalars(select(RunRequestRow.id))) == request_ids_before
    finally:
        engine.dispose()
