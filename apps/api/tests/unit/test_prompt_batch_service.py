from pathlib import Path
import json
from uuid import uuid4

import pytest
from sqlalchemy import select, text as sql_text

from ai_canvas.application.branch_service import BranchService
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_changeset_service import GraphChangeSetService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.control_config_service import ControlConfigService
from ai_canvas.application.model_profile_service import ModelProfileService
from ai_canvas.application.prompt_profile_extraction_service import PromptProfileExtractionService
from ai_canvas.application.resource_catalog_service import ResourceCatalogService
from ai_canvas.application.run_control_service import RunControlService
from ai_canvas.application.prompt_batch_service import (
    PromptBatchPreflightError,
    PromptBatchService,
    PromptBatchStateConflict,
)
from ai_canvas.application.errors import ClientMutationReuse
from ai_canvas.contracts.graph import CreateGraphRevisionRequest
from ai_canvas.contracts.prompt_batches import (
    CreatePromptBatchRequest,
    ExecutePromptBatchItemRequest,
    PromptBatchApprovalRequest,
    PromptBatchAcceptAllRequest,
    PromptBatchFinalizeRequest,
    PromptBatchItemDecisionRequest,
    PromptBatchRetryRequest,
)
from ai_canvas.contracts.prompt_profile_extractions import (
    CreatePromptProfileExtractionRequest,
    MaterializePromptProfileRequest,
)
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)
from ai_canvas.infrastructure.db.models import (
    ExperienceEventRow,
    PromptBatchItemAttemptRow,
    PromptBatchRow,
    RunRequestRow,
)
from ai_canvas.infrastructure.model_providers.openai_compatible import ModelProviderResult


def _activate_prompt_editor(project_id, control) -> None:
    pending = ModelProfileService(control).request_declaration(
        audit_project_id=project_id,
        base_config_revision_id=control.get_active().id,
        provider={"provider_key": "prompt-loopback", "display_name": "Prompt loopback",
            "adapter_kind": "OPENAI_COMPATIBLE_CHAT_JSON_V1"},
        profile_key="prompt-editor-default",
        revision={"role": "PROMPT_EDITOR", "model_id": "prompt-editor-v1", "model_version": "1",
            "endpoint_url": "http://127.0.0.1:8765/v1/chat/completions", "locality": "LOCAL_LOOPBACK",
            "data_residency": "LOCAL_DEVICE", "allowed_content_categories": ["USER_INSTRUCTION",
                "PROMPT_EXAMPLE_DOCUMENT", "PROMPT_TARGET_DOCUMENT", "PROMPT_PROFILE_RULES", "PRESERVE_CONSTRAINTS"],
            "retention_policy": "NO_RETENTION_DECLARED", "retention_note": "",
            "tool_permissions": ["PROMPT_PROFILE_DRAFT", "PROMPT_BATCH_ITEM_DRAFT"],
            "cost_mode": "HARD", "currency": "CNY", "cost_bound_minor": 0,
            "max_output_tokens": 2048, "timeout_seconds": 10, "credential_kind": "NONE",
            "fallback_model_profile_revision_id": None}, actor_id="local-control-admin")
    control.approve(pending.id, expected_changeset_sha256=pending.changeset_sha256,
        actor_id="local-control-admin")
    control.apply(pending.id, expected_changeset_sha256=pending.changeset_sha256,
        actor_id="local-control-admin")


class ExtractionAdapter:
    def send(self, **_kwargs):
        return ModelProviderResult(output_json=(b'{"kind":"prompt_profile_draft","schema_version":1,'
            b'"content_order":["FREEFORM"],"detail_density":"HIGH",'
            b'"format_rule_codes":["SECTION_PER_BLOCK"],"camera_rule_codes":[],'
            b'"positive_rule_codes":[],"negative_rule_codes":[],'
            b'"applicability_codes":["GENERAL_VISUAL_PROMPT"],"summary":"Form only"}'),
            response_sha256="f" * 64, usage={}, latency_ms=1)


class BatchAdapter:
    def __init__(self, *, fail_first: bool = False, fail_calls: set[int] | None = None) -> None:
        self.calls = []
        self.fail_first = fail_first
        self.fail_calls = fail_calls or set()

    def send(self, **kwargs):
        self.calls.append(kwargs)
        if (self.fail_first and len(self.calls) == 1) or len(self.calls) in self.fail_calls:
            return ModelProviderResult(
                output_json=b'{"invalid":true}',
                response_sha256="d" * 64,
                usage={},
                latency_ms=1,
            )
        payload = json.loads(kwargs["canonical_payload"])
        user = payload["messages"][1]["content"]
        constraints = next(value for value in user["contexts"] if value["category"] == "PRESERVE_CONSTRAINTS")
        body = {"kind": "prompt_batch_item_draft", "schema_version": 1,
            "shot_id": user["shot_id"], "prompt_node_id": user["prompt_node_id"],
            "base_document_sha256": user["base_document_sha256"],
            "replacements": [{"block_id": block_id, "replacement_text": "improved " + block_id[:8]}
                for block_id in constraints["content"]["editable_block_ids"]], "summary": "Improved"}
        return ModelProviderResult(output_json=json.dumps(body).encode(), response_sha256="e" * 64,
            usage={}, latency_ms=1)


def test_all_selection_reports_every_unlinked_shot(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        session_factory = make_session_factory(engine)
        projects = ProjectService(session_factory)
        project = projects.create("C4 missing links")
        shot_ids = [uuid4(), uuid4()]
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
                                "id": uuid4(),
                                "kind": "shot",
                                "position": {"x": order * 100, "y": 0},
                                "data": {"shot_id": shot_id},
                            }
                            for order, shot_id in enumerate(shot_ids)
                        ],
                        "edges": [],
                        "shots": [
                            {"id": shot_id, "order": order}
                            for order, shot_id in enumerate(shot_ids)
                        ],
                        "resource_bindings": [],
                    },
                }
            ),
        )
        project = projects.get(project.id)
        workspace = BranchService(session_factory, graphs).workspace(project.id)
        selected = next(branch for branch in workspace.branches if branch.is_selected)
        events = EventSpineService(session_factory)
        service = PromptBatchService(
            session_factory,
            graphs,
            GraphChangeSetService(session_factory, graphs, events),
            events,
        )

        with pytest.raises(PromptBatchPreflightError) as error:
            service.request(
                project.id,
                CreatePromptBatchRequest(
                    base_graph_revision_id=project.active_revision_id,
                    selection={"mode": "ALL"},
                    prompt_profile_binding_key="visual-profile",
                    application_target={
                        "mode": "NEW_BRANCH",
                        "base_branch_id": project.selected_branch_id,
                        "branch_name": "C4 batch",
                        "expected_branch_head_revision_id": project.active_revision_id,
                        "expected_branch_head_version": selected.head.version,
                        "expected_selection_version": project.selection_version,
                    },
                    instruction="Improve editable prompt blocks",
                    client_mutation_id=uuid4(),
                ),
                actor_id="local-session",
            )

        assert [(item.shot_id, item.code) for item in error.value.blockers] == [
            (shot_ids[0], "SHOT_PROMPT_NOT_LINKED"),
            (shot_ids[1], "SHOT_PROMPT_NOT_LINKED"),
        ]
    finally:
        engine.dispose()


def test_batch_approval_execute_decide_and_finalize(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        session_factory = make_session_factory(engine)
        projects = ProjectService(session_factory)
        project = projects.create("C4 batch loopback")
        graphs = GraphService(session_factory)
        source_node_id, source_block_id = uuid4(), uuid4()
        source_text = "example structure"
        graphs.create_revision(project.id, CreateGraphRevisionRequest.model_validate({
            "base_revision_id": project.active_revision_id, "client_mutation_id": uuid4(),
            "snapshot": {"schema_version": 3, "nodes": [{"id": source_node_id, "kind": "prompt",
                "position": {"x": 0, "y": 0}, "data": {"document": {"schema_version": 1,
                "blocks": [{"id": source_block_id, "kind": "FREEFORM", "text": source_text, "locked": False}],
                "rendered_text": source_text, "spans": [{"block_id": source_block_id,
                "start_utf16": 0, "end_utf16": len(source_text)}]}}}], "edges": [], "shots": [],
                "resource_bindings": []}}))
        project = projects.get(project.id)
        events = EventSpineService(session_factory)
        control = ControlConfigService(session_factory, events)
        _activate_prompt_editor(project.id, control)
        workspace = BranchService(session_factory, graphs).workspace(project.id)
        selected = next(value for value in workspace.branches if value.is_selected)
        extraction_service = PromptProfileExtractionService(session_factory, graphs,
            RunControlService(session_factory, events), ResourceCatalogService(session_factory, events),
            events, adapter=ExtractionAdapter())
        extraction = extraction_service.request(project.id, CreatePromptProfileExtractionRequest(
            base_graph_revision_id=project.active_revision_id, branch_id=project.selected_branch_id,
            expected_branch_head_revision_id=project.active_revision_id,
            expected_branch_head_version=selected.head.version,
            expected_selection_version=project.selection_version, source_node_id=source_node_id,
            instruction="extract reusable form"), actor_id="local-session")
        extraction_service.approve(project.id, extraction.id,
            expected_request_sha256=extraction.run_request.request_sha256, actor_id="local-session")
        extraction = extraction_service.execute(project.id, extraction.id, actor_id="local-session")
        extraction = extraction_service.materialize(project.id, extraction.id,
            MaterializePromptProfileRequest(name="Certified profile", tags=[],
            expected_output_sha256=extraction.output_sha256), actor_id="local-session")
        profile_revision_id = extraction.materialized_resource_revision_id
        assert profile_revision_id is not None

        shot_ids = [uuid4() for _ in range(8)]
        prompt_ids = [uuid4() for _ in range(8)]
        block_ids = [uuid4() for _ in range(8)]
        shot_node_ids = [uuid4() for _ in range(8)]
        nodes = []
        edges = []
        for order in range(8):
            text = f"base {order}"
            nodes.extend([
                {"id": prompt_ids[order], "kind": "prompt", "position": {"x": order * 200, "y": 0},
                    "data": {"document": {"schema_version": 1,
                    "blocks": [{"id": block_ids[order], "kind": "FREEFORM", "text": text, "locked": False}],
                    "rendered_text": text, "spans": [{"block_id": block_ids[order], "start_utf16": 0,
                    "end_utf16": len(text)}]}}},
                {"id": shot_node_ids[order], "kind": "shot", "position": {"x": order * 200, "y": 100},
                    "data": {"shot_id": shot_ids[order]}},
            ])
            edges.append({"id": uuid4(), "source": prompt_ids[order], "source_port": "prompt_document",
                "target": shot_node_ids[order], "target_port": "prompt_document"})
        graphs.create_revision(project.id, CreateGraphRevisionRequest.model_validate({
            "base_revision_id": project.active_revision_id, "client_mutation_id": uuid4(),
            "snapshot": {"schema_version": 3, "nodes": nodes, "edges": edges,
            "shots": [{"id": shot_id, "order": order} for order, shot_id in enumerate(shot_ids)],
            "resource_bindings": [{"id": uuid4(), "resource_kind": "PROMPT", "binding_key": "visual-profile",
                "scope_kind": "PROJECT", "scope_id": None, "resource_revision_id": profile_revision_id}]}}))
        project = projects.get(project.id)
        workspace = BranchService(session_factory, graphs).workspace(project.id)
        selected = next(value for value in workspace.branches if value.is_selected)
        adapter = BatchAdapter(fail_calls={1, 9})
        service = PromptBatchService(session_factory, graphs,
            GraphChangeSetService(session_factory, graphs, events), events, adapter=adapter)
        missing_profile_preflight = service.preflight(project.id, CreatePromptBatchRequest(
            base_graph_revision_id=project.active_revision_id, selection={"mode": "ALL"},
            prompt_profile_binding_key="missing-profile", application_target={"mode": "NEW_BRANCH",
            "base_branch_id": project.selected_branch_id, "branch_name": "C4 candidate",
            "expected_branch_head_revision_id": project.active_revision_id,
            "expected_branch_head_version": selected.head.version,
            "expected_selection_version": project.selection_version}, instruction="improve form",
            client_mutation_id=uuid4()))
        assert not missing_profile_preflight.eligible
        assert [blocker.code for blocker in missing_profile_preflight.blockers] == [
            "PROMPT_PROFILE_BINDING_MISSING"
        ] * 8
        create_mutation_id = uuid4()
        batch_request = CreatePromptBatchRequest(
            base_graph_revision_id=project.active_revision_id, selection={"mode": "ALL"},
            prompt_profile_binding_key="visual-profile", application_target={"mode": "NEW_BRANCH",
            "base_branch_id": project.selected_branch_id, "branch_name": "C4 candidate",
            "expected_branch_head_revision_id": project.active_revision_id,
            "expected_branch_head_version": selected.head.version,
            "expected_selection_version": project.selection_version}, instruction="improve form",
            client_mutation_id=create_mutation_id)
        batch = service.request(project.id, batch_request, actor_id="local-session")
        assert batch.phase == "PENDING_APPROVAL"
        assert len(batch.items) == 8
        assert adapter.calls == []
        replayed_batch = service.request(
            project.id, batch_request, actor_id="local-session"
        )
        assert replayed_batch.id == batch.id
        with pytest.raises(ClientMutationReuse):
            service.request(
                project.id,
                batch_request.model_copy(update={"instruction": "different request"}),
                actor_id="local-session",
            )
        batch = service.approve(project.id, batch.id, PromptBatchApprovalRequest(
            expected_request_set_sha256=batch.request_set_sha256), actor_id="local-session")
        assert batch.phase == "PENDING_EXECUTION"
        assert adapter.calls == []
        for batch_item in batch.items:
            attempt = batch_item.current_attempt
            assert attempt is not None
            batch = service.execute_item(project.id, batch.id, batch_item.id, attempt.id,
                ExecutePromptBatchItemRequest(expected_attempt_id=attempt.id,
                expected_request_sha256=attempt.run_request.request_sha256), actor_id="local-session")
        assert batch.phase == "REVIEW_PENDING"
        assert len(adapter.calls) == 8
        failed = [
            item
            for item in batch.items
            if item.current_attempt is not None
            and item.current_attempt.state == "FAILED"
        ]
        assert len(failed) == 1
        accept_all_request = PromptBatchAcceptAllRequest(
            expected_batch_version=batch.version, client_mutation_id=uuid4()
        )
        batch = service.accept_all_succeeded(
            project.id, batch.id, accept_all_request, actor_id="local-session"
        )
        assert service.accept_all_succeeded(
            project.id, batch.id, accept_all_request, actor_id="local-session"
        ).version == batch.version
        reviewed = next(item for item in batch.items
            if item.current_attempt is not None and item.current_attempt.state == "SUCCEEDED")
        assert reviewed.current_attempt is not None
        reject_request = PromptBatchItemDecisionRequest(decision="REJECTED", attempt_id=reviewed.current_attempt.id,
            expected_output_sha256=reviewed.current_attempt.output_sha256,
            expected_decision_version=reviewed.decision_version,
            client_mutation_id=uuid4())
        batch = service.decide_item(
            project.id, batch.id, reviewed.id, reject_request, actor_id="local-session"
        )
        assert service.decide_item(
            project.id, batch.id, reviewed.id, reject_request, actor_id="local-session"
        ).version == batch.version
        with pytest.raises(ClientMutationReuse):
            service.decide_item(
                project.id, batch.id, reviewed.id,
                reject_request.model_copy(update={"decision": "ACCEPTED"}),
                actor_id="local-session",
            )
        reviewed = next(item for item in batch.items if item.id == reviewed.id)
        assert reviewed.current_attempt is not None
        batch = service.decide_item(project.id, batch.id, reviewed.id,
            PromptBatchItemDecisionRequest(decision="ACCEPTED", attempt_id=reviewed.current_attempt.id,
            expected_output_sha256=reviewed.current_attempt.output_sha256,
            expected_decision_version=reviewed.decision_version,
            client_mutation_id=uuid4()), actor_id="local-session")
        retry_request = PromptBatchRetryRequest(
                failed_item_ids=[failed[0].id],
                expected_batch_version=batch.version,
                client_mutation_id=uuid4(),
        )
        batch = service.retry_failed(
            project.id, batch.id, retry_request, actor_id="local-session"
        )
        assert service.retry_failed(
            project.id, batch.id, retry_request, actor_id="local-session"
        ).version == batch.version
        with pytest.raises(ClientMutationReuse):
            service.retry_failed(
                project.id, batch.id,
                retry_request.model_copy(
                    update={"expected_batch_version": retry_request.expected_batch_version + 1}
                ), actor_id="local-session",
            )
        assert batch.phase == "RETRY_APPROVAL_REQUIRED"
        assert batch.retry_request_set_sha256 is not None
        batch = service.approve_retry(
            project.id,
            batch.id,
            PromptBatchApprovalRequest(
                expected_request_set_sha256=batch.retry_request_set_sha256
            ),
            actor_id="local-session",
        )
        retried = next(item for item in batch.items if item.id == failed[0].id)
        assert retried.current_attempt is not None
        assert retried.current_attempt.attempt_number == 2
        batch = service.execute_item(
            project.id,
            batch.id,
            retried.id,
            retried.current_attempt.id,
            ExecutePromptBatchItemRequest(
                expected_attempt_id=retried.current_attempt.id,
                expected_request_sha256=retried.current_attempt.run_request.request_sha256,
            ),
            actor_id="local-session",
        )
        assert len(adapter.calls) == 9
        assert batch.phase == "REVIEW_PENDING"
        batch = service.retry_failed(
            project.id,
            batch.id,
            PromptBatchRetryRequest(
                failed_item_ids=[retried.id],
                expected_batch_version=batch.version,
                client_mutation_id=uuid4(),
            ),
            actor_id="local-session",
        )
        assert batch.retry_request_set_sha256 is not None
        batch = service.approve_retry(
            project.id,
            batch.id,
            PromptBatchApprovalRequest(
                expected_request_set_sha256=batch.retry_request_set_sha256
            ),
            actor_id="local-session",
        )
        retried = next(item for item in batch.items if item.id == failed[0].id)
        assert retried.current_attempt is not None
        assert retried.current_attempt.attempt_number == 3
        batch = service.execute_item(
            project.id,
            batch.id,
            retried.id,
            retried.current_attempt.id,
            ExecutePromptBatchItemRequest(
                expected_attempt_id=retried.current_attempt.id,
                expected_request_sha256=retried.current_attempt.run_request.request_sha256,
            ),
            actor_id="local-session",
        )
        assert len(adapter.calls) == 10
        batch = service.accept_all_succeeded(project.id, batch.id,
            PromptBatchAcceptAllRequest(expected_batch_version=batch.version,
            client_mutation_id=uuid4()), actor_id="local-session")
        assert all(
            item.latest_decision is not None
            and item.latest_decision.decision == "ACCEPTED"
            for item in batch.items
        )
        tampered_item = batch.items[0]
        source_item = batch.items[1]
        assert tampered_item.current_attempt is not None
        assert source_item.current_attempt is not None
        with engine.begin() as connection:
            connection.execute(
                sql_text(
                    "DROP TRIGGER c4_prompt_batch_item_attempts_terminal_immutable"
                )
            )
        with session_factory.begin() as session:
            tampered_attempt = session.get(
                PromptBatchItemAttemptRow, str(tampered_item.current_attempt.id)
            )
            source_attempt = session.get(
                PromptBatchItemAttemptRow, str(source_item.current_attempt.id)
            )
            assert tampered_attempt is not None and source_attempt is not None
            original_compiled_document_json = tampered_attempt.compiled_document_json
            tampered_attempt.compiled_document_json = source_attempt.compiled_document_json
        with pytest.raises(PromptBatchStateConflict, match="integrity"):
            service.finalize(
                project.id,
                batch.id,
                PromptBatchFinalizeRequest(
                    expected_batch_version=batch.version,
                    client_mutation_id=uuid4(),
                ),
                actor_id="local-session",
            )
        with session_factory.begin() as session:
            tampered_attempt = session.get(
                PromptBatchItemAttemptRow, str(tampered_item.current_attempt.id)
            )
            assert tampered_attempt is not None
            tampered_attempt.compiled_document_json = original_compiled_document_json

        finalize_request = PromptBatchFinalizeRequest(
            expected_batch_version=batch.version, client_mutation_id=uuid4()
        )
        batch = service.finalize(
            project.id, batch.id, finalize_request, actor_id="local-session"
        )
        assert batch.phase == "FINALIZED"
        assert batch.graph_change_set is not None
        assert batch.graph_change_set.state == "PENDING_APPROVAL"
        replayed_final = service.finalize(
            project.id, batch.id, finalize_request, actor_id="local-session"
        )
        assert replayed_final.graph_change_set is not None
        assert replayed_final.graph_change_set.id == batch.graph_change_set.id
        assert all(item.attempts for item in replayed_final.items)
        assert all(item.decisions for item in replayed_final.items)

        interrupted = service.request(project.id, CreatePromptBatchRequest(
            base_graph_revision_id=project.active_revision_id, selection={"mode": "ALL"},
            prompt_profile_binding_key="visual-profile", application_target={"mode": "NEW_BRANCH",
            "base_branch_id": project.selected_branch_id, "branch_name": "C4 recovery",
            "expected_branch_head_revision_id": project.active_revision_id,
            "expected_branch_head_version": selected.head.version,
            "expected_selection_version": project.selection_version}, instruction="recovery probe",
            client_mutation_id=uuid4()), actor_id="local-session")
        interrupted = service.approve(project.id, interrupted.id, PromptBatchApprovalRequest(
            expected_request_set_sha256=interrupted.request_set_sha256), actor_id="local-session")
        interrupted_version = interrupted.version
        interrupted_attempt_id = interrupted.items[0].current_attempt.id
        with session_factory.begin() as session:
            stored_batch = session.get(PromptBatchRow, str(interrupted.id))
            assert stored_batch is not None
            stored_batch.phase = "IN_PROGRESS"
            for index, response_item in enumerate(interrupted.items):
                attempt = session.get(
                    PromptBatchItemAttemptRow, str(response_item.current_attempt.id)
                )
                assert attempt is not None
                run = session.get(RunRequestRow, attempt.run_request_id)
                assert run is not None
                if index == 0:
                    attempt.state = "DISPATCHING"
                    run.state = "APPROVED"
                else:
                    attempt.state = "REJECTED"
                    run.state = "REJECTED"
        assert service.reconcile_dispatching() == 1
        assert service.reconcile_dispatching() == 0
        recovered = service.get(project.id, interrupted.id)
        assert recovered.phase == "REVIEW_PENDING"
        assert recovered.version == interrupted_version + 1
        assert recovered.items[0].current_attempt.state == "UNKNOWN_TERMINAL"
        with session_factory() as session:
            attempt = session.get(
                PromptBatchItemAttemptRow, str(interrupted_attempt_id)
            )
            assert attempt is not None
            assert session.get(RunRequestRow, attempt.run_request_id).state == "TERMINAL"
            recovery_events = list(session.scalars(select(ExperienceEventRow).where(
                ExperienceEventRow.subject_id == str(interrupted.items[0].id),
                ExperienceEventRow.event_type == "prompt_batch_item.unknown_terminal")))
            assert len(recovery_events) == 1
    finally:
        engine.dispose()
