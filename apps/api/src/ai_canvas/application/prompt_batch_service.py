import json
import socket
from datetime import datetime, timedelta, timezone
from threading import BoundedSemaphore
from typing import Callable
from uuid import UUID, uuid4

from pydantic import BaseModel, SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.control_config_service import ControlConfigService
from ai_canvas.application.errors import ClientMutationReuse
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_changeset_service import GraphChangeSetService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.run_control_service import RunControlService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts.branches import DirectApplicationTarget
from ai_canvas.contracts.graph import PromptNodeV3
from ai_canvas.contracts.graph_changesets import CreateGraphChangeSetRequest
from ai_canvas.contracts.prompt_batches import (
    CreatePromptBatchRequest,
    ExecutePromptBatchItemRequest,
    PromptBatchApprovalRequest,
    PromptBatchAcceptAllRequest,
    PromptBatchAttemptResponse,
    PromptBatchDecisionResponse,
    PromptBatchFinalizeRequest,
    PromptBatchItemDecisionRequest,
    PromptBatchItemDraftV1,
    PromptBatchItemResponse,
    PromptBatchPreflightResponse,
    PromptBatchResponse,
    PromptBatchRetryRequest,
)
from ai_canvas.contracts.prompts import PromptDocumentV1
from ai_canvas.contracts.resources import PromptResourcePayload
from ai_canvas.domain.control.canonical import canonical_json, canonical_sha256
from ai_canvas.domain.graph.snapshots import normalize_to_v3
from ai_canvas.domain.model_endpoints.policy import validate_endpoint
from ai_canvas.domain.prompt_batches.payload import (
    compile_batch_item_output,
    compile_prompt_batch_item_payload,
)
from ai_canvas.domain.prompt_batches.profile_bindings import (
    resolve_effective_prompt_profile_binding,
)
from ai_canvas.domain.prompt_batches.shot_prompt_links import (
    ShotPromptBlocker,
    resolve_shot_prompt_targets,
)
from ai_canvas.infrastructure.db.models import (
    ApprovalDecisionRow,
    C4MutationReceiptRow,
    EgressPlanRow,
    GraphBranchHeadRow,
    GraphBranchRow,
    GraphChangeSetRow,
    GraphDocumentRow,
    GraphSelectionPointerRow,
    ModelProfileRevisionRow,
    PromptBatchItemAttemptRow,
    PromptBatchItemDecisionRow,
    PromptBatchItemRow,
    PromptBatchRow,
    ResourceRevisionRow,
    ResourceRow,
    RunAuthorizationRow,
    RunRequestRow,
)
from ai_canvas.infrastructure.db.prompt_batch_repository import PromptBatchRepository
from ai_canvas.infrastructure.model_providers.credentials import resolve_runtime_credential
from ai_canvas.infrastructure.model_providers.openai_compatible import (
    ModelProviderError,
    OpenAICompatibleChatJsonAdapter,
)


class PromptBatchNotFound(LookupError):
    pass


class PromptBatchStateConflict(RuntimeError):
    pass


class PromptBatchStaleSubject(PromptBatchStateConflict):
    pass


class PromptBatchIntegrityError(PromptBatchStateConflict):
    pass


class PromptBatchPreflightError(ValueError):
    def __init__(self, blockers: list[ShotPromptBlocker]) -> None:
        self.blockers = blockers
        super().__init__("prompt batch preflight failed")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def default_resolver(host: str) -> list[str]:
    return sorted({item[4][0] for item in socket.getaddrinfo(host, None)})


class PromptBatchService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        graphs: GraphService,
        graph_changes: GraphChangeSetService,
        events: EventSpineService,
        *,
        adapter: OpenAICompatibleChatJsonAdapter | None = None,
        prompt_editor_api_key: SecretStr | None = None,
        resolver: Callable[[str], list[str]] = default_resolver,
        repository: PromptBatchRepository | None = None,
        write_gate: WriteGate | None = None,
        max_concurrency: int = 4,
    ) -> None:
        self.session_factory = session_factory
        self.graphs = graphs
        self.graph_changes = graph_changes
        self.events = events
        self.run_control = RunControlService(session_factory, events)
        self.adapter = adapter or OpenAICompatibleChatJsonAdapter()
        self.prompt_editor_api_key = prompt_editor_api_key
        self.resolver = resolver
        self.repository = repository or PromptBatchRepository()
        self._write_gate = write_gate or WriteGate()
        self._dispatch_slots = BoundedSemaphore(max_concurrency)

    @staticmethod
    def _mutation_sha256(
        operation_kind: str, subject_type: str, subject_id: UUID, request: BaseModel
    ) -> str:
        payload = request.model_dump(mode="json")
        return canonical_sha256(
            {
                "operation_kind": operation_kind,
                "subject_type": subject_type,
                "subject_id": str(subject_id),
                "request": payload,
            }
        )

    def _is_mutation_replay(
        self,
        session: Session,
        *,
        project_id: UUID,
        client_mutation_id: UUID,
        operation_kind: str,
        subject_type: str,
        subject_id: UUID,
        request_sha256: str,
    ) -> bool:
        receipt = self.repository.mutation_receipt(
            session, str(project_id), str(client_mutation_id)
        )
        if receipt is None:
            return False
        if (
            receipt.operation_kind != operation_kind
            or receipt.subject_type != subject_type
            or receipt.subject_id != str(subject_id)
            or receipt.request_sha256 != request_sha256
        ):
            raise ClientMutationReuse(str(client_mutation_id))
        return True

    def _record_mutation(
        self,
        session: Session,
        *,
        project_id: UUID,
        client_mutation_id: UUID,
        operation_kind: str,
        subject_type: str,
        subject_id: UUID,
        request_sha256: str,
    ) -> None:
        self.repository.add_mutation_receipt(
            session,
            C4MutationReceiptRow(
                id=str(uuid4()),
                project_id=str(project_id),
                client_mutation_id=str(client_mutation_id),
                operation_kind=operation_kind,
                subject_type=subject_type,
                subject_id=str(subject_id),
                request_sha256=request_sha256,
                created_at=utc_now(),
            ),
        )

    @staticmethod
    def _target(row: PromptBatchRow) -> dict[str, object]:
        common = {
            "expected_branch_head_revision_id": row.expected_branch_head_revision_id,
            "expected_branch_head_version": row.expected_branch_head_version,
            "expected_selection_version": row.expected_selection_version,
        }
        if row.application_target_mode == "DIRECT":
            return {"mode": "DIRECT", "branch_id": row.branch_id, **common}
        return {
            "mode": "NEW_BRANCH",
            "base_branch_id": row.branch_id,
            "branch_name": row.new_branch_name,
            **common,
        }

    def _response(self, session: Session, row: PromptBatchRow) -> PromptBatchResponse:
        profile = session.get(ModelProfileRevisionRow, row.model_profile_revision_id)
        if profile is None:
            raise ValueError("prompt batch profile is unavailable")
        items: list[PromptBatchItemResponse] = []
        for item in self.repository.items_for_batch(session, row.id):
            attempt_responses: list[PromptBatchAttemptResponse] = []
            for attempt in self.repository.attempts_for_item(session, item.id):
                run = session.get(RunRequestRow, attempt.run_request_id)
                plan = session.scalar(select(EgressPlanRow).where(EgressPlanRow.run_request_id == attempt.run_request_id))
                if run is None or plan is None:
                    raise ValueError("prompt batch attempt storage is incomplete")
                attempt_responses.append(PromptBatchAttemptResponse(
                    id=UUID(attempt.id), attempt_number=attempt.attempt_number,
                    parent_attempt_id=UUID(attempt.parent_attempt_id) if attempt.parent_attempt_id else None,
                    run_request=RunControlService._agent_request_response(run, plan, profile),
                    state=attempt.state,
                    output=PromptBatchItemDraftV1.model_validate_json(attempt.output_json) if attempt.output_json else None,
                    output_sha256=attempt.output_sha256,
                    compiled_document_sha256=attempt.compiled_document_sha256,
                    compiled_document=(PromptDocumentV1.model_validate_json(attempt.compiled_document_json)
                        if attempt.compiled_document_json else None),
                    failure_code=attempt.failure_code,
                    created_at=as_utc(attempt.created_at), updated_at=as_utc(attempt.updated_at),
                ))
            attempt_response = next(
                (value for value in attempt_responses if str(value.id) == item.current_attempt_id),
                None,
            )
            decisions = self.repository.decisions_for_item(session, item.id)
            decision_responses = [PromptBatchDecisionResponse(
                id=UUID(value.id), attempt_id=UUID(value.attempt_id),
                decision=value.decision, decision_sequence=value.decision_sequence,
                expected_output_sha256=value.expected_output_sha256,
                actor_id=value.actor_id, created_at=as_utc(value.created_at),
            ) for value in decisions]
            decision_response = decision_responses[-1] if decision_responses else None
            items.append(PromptBatchItemResponse(
                id=UUID(item.id), shot_id=UUID(item.shot_id), shot_node_id=UUID(item.shot_node_id),
                prompt_node_id=UUID(item.prompt_node_id), shot_order=item.shot_order,
                base_document_sha256=item.base_document_sha256,
                base_document=PromptDocumentV1.model_validate_json(item.base_document_json),
                protected_manifest_sha256=item.protected_manifest_sha256,
                protected_manifest=json.loads(item.protected_manifest_json),
                editable_manifest_sha256=item.editable_manifest_sha256,
                editable_manifest=json.loads(item.editable_manifest_json),
                prompt_profile_resource_revision_id=UUID(item.prompt_profile_resource_revision_id),
                prompt_profile_revision_sha256=item.prompt_profile_revision_sha256,
                prompt_profile_scope_kind=item.prompt_profile_scope_kind,
                prompt_profile_scope_id=UUID(item.prompt_profile_scope_id) if item.prompt_profile_scope_id else None,
                decision_version=item.decision_version, current_attempt=attempt_response,
                latest_decision=decision_response,
                attempts=attempt_responses,
                decisions=decision_responses,
            ))
        changeset = session.get(GraphChangeSetRow, row.graph_change_set_id) if row.graph_change_set_id else None
        return PromptBatchResponse(
            id=UUID(row.id), project_id=UUID(row.project_id), correlation_id=UUID(row.correlation_id),
            selection_mode=row.selection_mode, binding_key=row.binding_key,
            base_graph_revision_id=UUID(row.base_graph_revision_id),
            base_graph_revision_sha256=row.base_graph_revision_sha256,
            branch_id=UUID(row.branch_id), expected_branch_head_version=row.expected_branch_head_version,
            expected_selection_version=row.expected_selection_version, application_target=self._target(row),
            model_profile_revision_id=UUID(row.model_profile_revision_id), config_revision_id=UUID(row.config_revision_id),
            item_set_sha256=row.item_set_sha256, request_set_sha256=row.request_set_sha256,
            retry_request_set_sha256=self._retry_hash(session, row),
            version=row.version, phase=row.phase, items=items,
            graph_change_set=GraphChangeSetService._response(changeset) if changeset else None,
            final_decision_set_sha256=row.final_decision_set_sha256, failure_code=row.failure_code,
            created_at=as_utc(row.created_at), updated_at=as_utc(row.updated_at),
        )

    def _retry_hash(self, session: Session, batch: PromptBatchRow) -> str | None:
        subjects = []
        for item in self.repository.items_for_batch(session, batch.id):
            attempt = session.get(PromptBatchItemAttemptRow, item.current_attempt_id)
            if attempt is None or attempt.attempt_number == 1 or attempt.state not in {"PENDING_APPROVAL", "PENDING_EXECUTION"}:
                continue
            run = session.get(RunRequestRow, attempt.run_request_id)
            if run is not None:
                subjects.append({"item_id": item.id, "attempt_id": attempt.id,
                    "parent_attempt_id": attempt.parent_attempt_id,
                    "request_sha256": run.request_sha256})
        return canonical_sha256(subjects) if subjects else None

    def get(self, project_id: UUID, batch_id: UUID) -> PromptBatchResponse:
        with self.session_factory() as session:
            row = self.repository.batch(session, str(batch_id))
            if row is None or row.project_id != str(project_id):
                raise PromptBatchNotFound(str(batch_id))
            return self._response(session, row)

    def preflight(self, project_id: UUID, request: CreatePromptBatchRequest) -> PromptBatchPreflightResponse:
        revision = self.graphs.get_revision(project_id, request.base_graph_revision_id)
        graph = normalize_to_v3(revision.snapshot)
        selected = request.selection.shot_ids if request.selection.mode == "SELECTED" else None
        resolution = resolve_shot_prompt_targets(graph, selected_shot_ids=selected)
        prompt_nodes = {node.id: node for node in graph.nodes if isinstance(node, PromptNodeV3)}
        blockers: list[dict[str, object]] = [
            {"shot_id": value.shot_id, "code": value.code}
            for value in resolution.blockers
        ]
        if len(resolution.targets) > 100:
            blockers.extend(
                {"shot_id": value.shot_id, "code": "BATCH_SIZE_LIMIT_EXCEEDED"}
                for value in resolution.targets
            )
        with self.session_factory() as session:
            _, config = ControlConfigService._active_in_session(session)
            profile_id = json.loads(config.config_json).get("active_prompt_editor_profile_revision_id")
            profile = session.get(ModelProfileRevisionRow, profile_id)
            declaration = json.loads(profile.declaration_json) if profile is not None else {}
            profile_available = bool(
                profile is not None
                and declaration.get("role") == "PROMPT_EDITOR"
                and "PROMPT_BATCH_ITEM_DRAFT" in declaration.get("tool_permissions", [])
            )
            for subject in resolution.targets:
                if not profile_available:
                    blockers.append({
                        "shot_id": subject.shot_id,
                        "code": "ACTIVE_PROMPT_EDITOR_PROFILE_UNAVAILABLE",
                    })
                    continue
                binding = resolve_effective_prompt_profile_binding(
                    graph,
                    binding_key=request.prompt_profile_binding_key,
                    shot_id=subject.shot_id,
                    prompt_node_id=subject.prompt_node_id,
                )
                if binding is None:
                    blockers.append({
                        "shot_id": subject.shot_id,
                        "code": "PROMPT_PROFILE_BINDING_MISSING",
                    })
                    continue
                resource_revision = session.get(ResourceRevisionRow, str(binding.resource_revision_id))
                resource = session.get(ResourceRow, resource_revision.resource_id) if resource_revision else None
                certification = self.repository.certification_for_revision(
                    session, str(binding.resource_revision_id)
                )
                if (
                    resource_revision is None
                    or resource is None
                    or resource.kind != "PROMPT"
                    or certification is None
                    or certification.profile_payload_sha256 != resource_revision.payload_sha256
                ):
                    blockers.append({
                        "shot_id": subject.shot_id,
                        "code": "PROMPT_PROFILE_NOT_CERTIFIED",
                    })
                    continue
                try:
                    prompt_profile = PromptResourcePayload.model_validate_json(resource_revision.payload_json)
                    compile_prompt_batch_item_payload(
                        shot_id=subject.shot_id,
                        prompt_node_id=subject.prompt_node_id,
                        document=prompt_nodes[subject.prompt_node_id].data.document,
                        profile=prompt_profile,
                        instruction=request.instruction,
                        model_id=declaration["model_id"],
                    )
                except (ValueError, KeyError):
                    blockers.append({
                        "shot_id": subject.shot_id,
                        "code": "NO_EDITABLE_BLOCKS",
                    })
        return PromptBatchPreflightResponse(
            eligible=bool(resolution.targets) and not blockers and len(resolution.targets) <= 100,
            items=[{"shot_id": value.shot_id, "shot_node_id": value.shot_node_id,
                "prompt_node_id": value.prompt_node_id, "shot_order": value.shot_order}
                for value in resolution.targets],
            blockers=blockers,
        )

    def request(self, project_id: UUID, request: CreatePromptBatchRequest, *, actor_id: str) -> PromptBatchResponse:
        create_request_sha256 = canonical_sha256(request.model_dump(mode="json"))
        with self.session_factory() as session:
            replay = self.repository.batch_for_mutation(
                session, str(project_id), str(request.client_mutation_id)
            )
            if replay is not None:
                if replay.create_request_sha256 != create_request_sha256:
                    raise ClientMutationReuse(str(request.client_mutation_id))
                return self._response(session, replay)
        revision = self.graphs.get_revision(project_id, request.base_graph_revision_id)
        graph = normalize_to_v3(revision.snapshot)
        selected = request.selection.shot_ids if request.selection.mode == "SELECTED" else None
        resolution = resolve_shot_prompt_targets(graph, selected_shot_ids=selected)
        if resolution.blockers:
            raise PromptBatchPreflightError(list(resolution.blockers))
        if not 1 <= len(resolution.targets) <= 100:
            raise ValueError("prompt batch must contain 1 to 100 eligible shots")
        prompt_nodes = {node.id: node for node in graph.nodes if isinstance(node, PromptNodeV3)}
        batch_id, correlation_id, now = uuid4(), uuid4(), utc_now()
        target = request.application_target
        branch_id = target.branch_id if isinstance(target, DirectApplicationTarget) else target.base_branch_id
        compiled_items = []
        with self.session_factory() as session:
            document = session.scalar(select(GraphDocumentRow).where(GraphDocumentRow.project_id == str(project_id)))
            branch = session.get(GraphBranchRow, str(branch_id))
            head = session.get(GraphBranchHeadRow, str(branch_id))
            selection = session.get(GraphSelectionPointerRow, document.id) if document else None
            if (document is None or branch is None or branch.graph_document_id != document.id or head is None
                or head.graph_revision_id != str(target.expected_branch_head_revision_id)
                or head.version != target.expected_branch_head_version or selection is None
                or selection.branch_id != str(branch_id) or selection.graph_revision_id != head.graph_revision_id
                or selection.version != target.expected_selection_version
                or str(request.base_graph_revision_id) != head.graph_revision_id):
                raise PromptBatchStaleSubject("branch or selection is stale")
            _, config = ControlConfigService._active_in_session(session)
            profile_id = json.loads(config.config_json).get("active_prompt_editor_profile_revision_id")
            profile = session.get(ModelProfileRevisionRow, profile_id)
            if profile is None:
                raise ValueError("active PROMPT_EDITOR profile is unavailable")
            declaration = json.loads(profile.declaration_json)
            if declaration.get("role") != "PROMPT_EDITOR" or "PROMPT_BATCH_ITEM_DRAFT" not in declaration.get("tool_permissions", []):
                raise ValueError("active PROMPT_EDITOR cannot produce batch drafts")
            endpoint = validate_endpoint(declaration["endpoint_url"], locality=declaration["locality"],
                resolver=self.resolver if declaration["locality"] == "REMOTE_HTTPS" else None)
            for subject in resolution.targets:
                node = prompt_nodes[subject.prompt_node_id]
                binding = resolve_effective_prompt_profile_binding(graph, binding_key=request.prompt_profile_binding_key,
                    shot_id=subject.shot_id, prompt_node_id=subject.prompt_node_id)
                if binding is None:
                    raise ValueError(f"PROMPT_PROFILE_BINDING_MISSING:{subject.shot_id}")
                resource_revision = session.get(ResourceRevisionRow, str(binding.resource_revision_id))
                resource = session.get(ResourceRow, resource_revision.resource_id) if resource_revision else None
                certification = self.repository.certification_for_revision(session, str(binding.resource_revision_id))
                if (resource_revision is None or resource is None or resource.kind != "PROMPT" or certification is None
                    or certification.profile_payload_sha256 != resource_revision.payload_sha256):
                    raise ValueError(f"PROMPT_PROFILE_NOT_CERTIFIED:{subject.shot_id}")
                prompt_profile = PromptResourcePayload.model_validate_json(resource_revision.payload_json)
                compiled = compile_prompt_batch_item_payload(shot_id=subject.shot_id,
                    prompt_node_id=subject.prompt_node_id, document=node.data.document,
                    profile=prompt_profile, instruction=request.instruction, model_id=declaration["model_id"])
                compiled_items.append((subject, node, binding, resource_revision, compiled))
        item_subjects = [{
            "shot_id": str(subject.shot_id), "shot_node_id": str(subject.shot_node_id),
            "prompt_node_id": str(subject.prompt_node_id), "shot_order": subject.shot_order,
            "base_document_sha256": compiled.frozen.document_sha256,
            "protected_manifest_sha256": compiled.frozen.protected_manifest_sha256,
            "editable_manifest_sha256": compiled.frozen.editable_manifest_sha256,
            "prompt_profile_resource_revision_id": resource_revision.id,
            "prompt_profile_revision_sha256": resource_revision.payload_sha256,
            "payload_sha256": compiled.payload_sha256,
        } for subject, _, _, resource_revision, compiled in compiled_items]
        item_set_sha256 = canonical_sha256(item_subjects)
        run_values = []
        for subject, _, _, _, compiled in compiled_items:
            scope = {"kind": "PROMPT_BATCH_ITEM", "batch_id": str(batch_id), "shot_id": str(subject.shot_id),
                "prompt_node_id": str(subject.prompt_node_id), "item_set_sha256": item_set_sha256}
            scope_sha256 = canonical_sha256(scope)
            request_sha256 = canonical_sha256({"project_id": str(project_id),
                "graph_revision_id": str(request.base_graph_revision_id), "graph_revision_sha256": revision.snapshot_hash,
                "config_revision_id": config.id, "config_revision_sha256": config.config_sha256,
                "model_profile_revision_id": profile.id, "model_profile_revision_sha256": profile.declaration_sha256,
                "branch_id": str(branch_id), "branch_head_version": target.expected_branch_head_version,
                "selection_version": target.expected_selection_version, "scope_sha256": scope_sha256,
                "payload_sha256": compiled.payload_sha256, "endpoint_identity_sha256": endpoint.identity_sha256})
            run_values.append((scope, scope_sha256, request_sha256))
        request_set_sha256 = canonical_sha256({"batch_id": str(batch_id), "item_set_sha256": item_set_sha256,
            "item_request_sha256s": [value[2] for value in run_values], "task_count": len(run_values),
            "graph_revision_sha256": revision.snapshot_hash, "config_revision_sha256": config.config_sha256,
            "model_profile_revision_sha256": profile.declaration_sha256, "branch_id": str(branch_id),
            "branch_head_version": target.expected_branch_head_version, "selection_version": target.expected_selection_version,
            "endpoint_identity_sha256": endpoint.identity_sha256, "application_target": target.model_dump(mode="json")})
        with self._write_gate:
            with self.session_factory.begin() as session:
                batch = PromptBatchRow(id=str(batch_id), project_id=str(project_id),
                    client_mutation_id=str(request.client_mutation_id),
                    create_request_sha256=create_request_sha256,
                    correlation_id=str(correlation_id),
                    selection_mode=request.selection.mode, selection_json=canonical_json(request.selection.model_dump(mode="json")),
                    binding_key=request.prompt_profile_binding_key, base_graph_revision_id=str(request.base_graph_revision_id),
                    base_graph_revision_sha256=revision.snapshot_hash, branch_id=str(branch_id),
                    expected_branch_head_revision_id=str(target.expected_branch_head_revision_id),
                    expected_branch_head_version=target.expected_branch_head_version,
                    expected_selection_version=target.expected_selection_version, application_target_mode=target.mode,
                    new_branch_name=getattr(target, "branch_name", None), model_profile_revision_id=profile.id,
                    model_profile_revision_sha256=profile.declaration_sha256, config_revision_id=config.id,
                    config_revision_sha256=config.config_sha256, item_set_sha256=item_set_sha256,
                    request_set_sha256=request_set_sha256, approval_id=None, version=1, phase="PENDING_APPROVAL",
                    final_decision_set_sha256=None, graph_change_set_id=None, failure_code=None,
                    created_at=now, updated_at=now, finalized_at=None)
                self.repository.add_batch(session, batch)
                session.flush()
                for values, control in zip(compiled_items, run_values, strict=True):
                    self._add_item(session, batch, values, control, profile, declaration, endpoint, actor_id, now)
                self.events.append_in_session(session, project_id=project_id, correlation_id=correlation_id,
                    actor_kind="HUMAN", actor_id=actor_id, subject_type="prompt_batch", subject_id=batch_id,
                    event_type="prompt_batch.requested", payload={"request_set_sha256": request_set_sha256,
                    "item_set_sha256": item_set_sha256, "item_count": len(compiled_items)})
        return self.get(project_id, batch_id)

    def _add_item(self, session, batch, values, control, profile, declaration, endpoint, actor_id, now) -> None:
        subject, node, binding, resource_revision, compiled = values
        scope, scope_sha256, request_sha256 = control
        item_id, attempt_id, run_id = uuid4(), uuid4(), uuid4()
        body = {"operation_kind": "PROMPT_BATCH_ITEM", "task_count": 1,
            "model_profile_revision_id": profile.id, "branch_id": batch.branch_id,
            "branch_head_version": batch.expected_branch_head_version,
            "selection_version": batch.expected_selection_version, "scope": scope,
            "provider_payload": compiled.provider_payload, "endpoint_identity_sha256": endpoint.identity_sha256}
        run = RunRequestRow(id=str(run_id), project_id=batch.project_id, correlation_id=batch.correlation_id,
            graph_revision_id=batch.base_graph_revision_id, graph_revision_sha256=batch.base_graph_revision_sha256,
            config_revision_id=batch.config_revision_id, config_revision_sha256=batch.config_revision_sha256,
            payload_sha256=compiled.payload_sha256, operation_kind="PROMPT_BATCH_ITEM", task_count=1,
            request_json=canonical_json(body), request_sha256=request_sha256, state="PENDING_APPROVAL",
            approval_id=None, model_profile_revision_id=profile.id,
            model_profile_revision_sha256=profile.declaration_sha256, branch_id=batch.branch_id,
            branch_head_version=batch.expected_branch_head_version, selection_version=batch.expected_selection_version,
            scope_json=canonical_json(scope), scope_sha256=scope_sha256, created_at=now)
        session.add(run)
        session.flush()
        data_classes = [value["category"] for value in compiled.preview["contexts"]]
        plan_body = {"mode": "MODEL_PROVIDER", "endpoint": endpoint.canonical_url,
            "endpoint_identity_sha256": endpoint.identity_sha256, "data_classes": data_classes, "task_count": 1,
            "cost_mode": declaration["cost_mode"], "currency": declaration["currency"],
            "cost_bound_minor": declaration["cost_bound_minor"], "gpu_count": 0, "max_retries": 0,
            "graph_revision_id": run.graph_revision_id, "graph_revision_sha256": run.graph_revision_sha256,
            "config_revision_id": run.config_revision_id, "config_revision_sha256": run.config_revision_sha256,
            "model_profile_revision_id": profile.id, "payload_sha256": compiled.payload_sha256,
            "request_sha256": run.request_sha256, "preview": compiled.preview}
        plan = EgressPlanRow(id=str(uuid4()), run_request_id=run.id, graph_revision_id=run.graph_revision_id,
            graph_revision_sha256=run.graph_revision_sha256, config_revision_id=run.config_revision_id,
            config_revision_sha256=run.config_revision_sha256, payload_sha256=compiled.payload_sha256,
            mode="MODEL_PROVIDER", endpoint=endpoint.canonical_url, data_classes_json=canonical_json(data_classes),
            task_count=1, cost_bound=declaration["cost_bound_minor"] or 0, gpu_count=0, max_retries=0,
            model_profile_revision_id=profile.id, endpoint_identity_sha256=endpoint.identity_sha256,
            preview_json=canonical_json(compiled.preview), cost_mode=declaration["cost_mode"],
            currency=declaration["currency"], cost_bound_minor=declaration["cost_bound_minor"],
            plan_sha256=canonical_sha256(plan_body), created_at=now)
        session.add(plan)
        item = PromptBatchItemRow(id=str(item_id), batch_id=batch.id, shot_id=str(subject.shot_id),
            shot_node_id=str(subject.shot_node_id), prompt_node_id=str(subject.prompt_node_id), shot_order=subject.shot_order,
            base_document_json=canonical_json(node.data.document.model_dump(mode="json")),
            base_document_sha256=compiled.frozen.document_sha256,
            protected_manifest_json=canonical_json([{"block_id": str(value.block_id), "kind": value.kind,
                "locked": value.locked, "index": value.index, "text_sha256": value.text_sha256}
                for value in compiled.frozen.protected]),
            protected_manifest_sha256=compiled.frozen.protected_manifest_sha256,
            editable_manifest_json=canonical_json([{"block_id": str(value.block_id), "kind": value.kind,
                "locked": value.locked, "index": value.index, "text_sha256": value.text_sha256}
                for value in compiled.frozen.editable]),
            editable_manifest_sha256=compiled.frozen.editable_manifest_sha256,
            prompt_profile_resource_revision_id=resource_revision.id,
            prompt_profile_revision_sha256=resource_revision.payload_sha256,
            prompt_profile_scope_kind=binding.scope_kind,
            prompt_profile_scope_id=str(binding.scope_id) if binding.scope_id else None,
            current_attempt_id=None, decision_version=0, created_at=now, updated_at=now)
        self.repository.add_item(session, item)
        session.flush()
        attempt = PromptBatchItemAttemptRow(id=str(attempt_id), item_id=item.id, attempt_number=1,
            parent_attempt_id=None, run_request_id=run.id,
            provider_payload_json=compiled.canonical_bytes.decode("utf-8"),
            provider_payload_sha256=compiled.payload_sha256, state="PENDING_APPROVAL",
            output_json=None, output_sha256=None, compiled_document_json=None,
            compiled_document_sha256=None, failure_code=None, created_at=now, updated_at=now,
            dispatched_at=None, completed_at=None)
        self.repository.add_attempt(session, attempt)
        session.flush()
        item.current_attempt_id = attempt.id
        self.events.append_in_session(session, project_id=UUID(batch.project_id),
            correlation_id=UUID(batch.correlation_id), actor_kind="HUMAN", actor_id=actor_id,
            subject_type="run_request", subject_id=run_id, event_type="run_request.requested",
            payload={"request_sha256": run.request_sha256, "operation_kind": "PROMPT_BATCH_ITEM"})
        self.events.append_in_session(session, project_id=UUID(batch.project_id),
            correlation_id=UUID(batch.correlation_id), actor_kind="SYSTEM", actor_id="control-plane",
            subject_type="egress_plan", subject_id=UUID(plan.id), event_type="egress_plan.sealed",
            payload={"plan_sha256": plan.plan_sha256, "mode": plan.mode})

    def approve(self, project_id: UUID, batch_id: UUID, request: PromptBatchApprovalRequest,
        *, actor_id: str) -> PromptBatchResponse:
        with self._write_gate:
            with self.session_factory.begin() as session:
                batch = self.repository.batch(session, str(batch_id))
                if (batch is None or batch.project_id != str(project_id) or batch.phase != "PENDING_APPROVAL"
                    or batch.request_set_sha256 != request.expected_request_set_sha256):
                    raise PromptBatchStateConflict()
                approval = ApprovalDecisionRow(id=str(uuid4()), project_id=str(project_id),
                    correlation_id=batch.correlation_id, subject_type="prompt_batch", subject_id=batch.id,
                    subject_sha256=batch.request_set_sha256,
                    risk_sha256=canonical_sha256({"request_set_sha256": batch.request_set_sha256}),
                    decision=request.decision, actor_id=actor_id, created_at=utc_now())
                session.add(approval)
                batch.approval_id = approval.id
                batch.phase = "PENDING_EXECUTION" if request.decision == "APPROVED" else "REJECTED"
                batch.version += 1
                batch.updated_at = utc_now()
                for item in self.repository.items_for_batch(session, batch.id):
                    attempt = session.get(PromptBatchItemAttemptRow, item.current_attempt_id)
                    run = session.get(RunRequestRow, attempt.run_request_id) if attempt else None
                    if attempt is None or run is None or attempt.state != "PENDING_APPROVAL" or run.state != "PENDING_APPROVAL":
                        raise PromptBatchStateConflict()
                    attempt.state = "PENDING_EXECUTION" if request.decision == "APPROVED" else "REJECTED"
                    attempt.updated_at = batch.updated_at
                    attempt.completed_at = None if request.decision == "APPROVED" else batch.updated_at
                    run.state = "APPROVED" if request.decision == "APPROVED" else "REJECTED"
                    run.approval_id = approval.id
                self.events.append_in_session(session, project_id=project_id,
                    correlation_id=UUID(batch.correlation_id), actor_kind="HUMAN", actor_id=actor_id,
                    subject_type="prompt_batch", subject_id=batch_id,
                    event_type="prompt_batch.approved" if request.decision == "APPROVED" else "prompt_batch.rejected",
                    payload={"request_set_sha256": batch.request_set_sha256})
        return self.get(project_id, batch_id)

    def _refresh_phase(self, session: Session, batch: PromptBatchRow) -> None:
        states = []
        for item in self.repository.items_for_batch(session, batch.id):
            attempt = session.get(PromptBatchItemAttemptRow, item.current_attempt_id)
            if attempt:
                states.append(attempt.state)
        terminal = {"SUCCEEDED", "FAILED", "UNKNOWN_TERMINAL", "REJECTED"}
        batch.phase = "REVIEW_PENDING" if states and all(value in terminal for value in states) else "IN_PROGRESS"
        batch.updated_at = utc_now()

    def _fail(self, project_id: UUID, batch_id: UUID, item_id: UUID, attempt_id: UUID) -> PromptBatchResponse:
        with self._write_gate:
            with self.session_factory.begin() as session:
                batch = self.repository.batch(session, str(batch_id))
                attempt = self.repository.attempt(session, str(attempt_id))
                if batch is None or attempt is None:
                    raise PromptBatchNotFound(str(batch_id))
                attempt.state, attempt.failure_code = "FAILED", "AGENT_OUTPUT_OR_PROVIDER_INVALID"
                attempt.output_json = attempt.output_sha256 = None
                attempt.compiled_document_json = attempt.compiled_document_sha256 = None
                attempt.updated_at = attempt.completed_at = utc_now()
                run = session.get(RunRequestRow, attempt.run_request_id)
                if run:
                    run.state = "TERMINAL"
                self._refresh_phase(session, batch)
                self.events.append_in_session(session, project_id=project_id,
                    correlation_id=UUID(batch.correlation_id), actor_kind="SYSTEM",
                    actor_id="prompt-batch-orchestrator", subject_type="prompt_batch_item",
                    subject_id=item_id, event_type="prompt_batch_item.failed",
                    payload={"attempt_id": str(attempt_id), "code": attempt.failure_code})
        return self.get(project_id, batch_id)

    def execute_item(self, project_id: UUID, batch_id: UUID, item_id: UUID, attempt_id: UUID,
        request: ExecutePromptBatchItemRequest, *, actor_id: str) -> PromptBatchResponse:
        if request.expected_attempt_id != attempt_id:
            raise PromptBatchStateConflict()
        with self.session_factory() as session:
            batch = self.repository.batch(session, str(batch_id))
            item = self.repository.item(session, str(item_id))
            attempt = self.repository.attempt(session, str(attempt_id))
            if (batch is None or item is None or attempt is None or batch.project_id != str(project_id)
                or item.batch_id != batch.id or attempt.item_id != item.id or item.current_attempt_id != attempt.id
                or batch.phase not in {"PENDING_EXECUTION", "IN_PROGRESS"} or attempt.state != "PENDING_EXECUTION"):
                raise PromptBatchStateConflict()
            run = session.get(RunRequestRow, attempt.run_request_id)
            plan = session.scalar(select(EgressPlanRow).where(EgressPlanRow.run_request_id == attempt.run_request_id))
            profile = session.get(ModelProfileRevisionRow, batch.model_profile_revision_id)
            resource_revision = session.get(ResourceRevisionRow, item.prompt_profile_resource_revision_id)
            approval = session.get(ApprovalDecisionRow, batch.approval_id)
            approval_subject_valid = bool(
                approval is not None
                and approval.subject_id == batch.id
                and approval.decision == "APPROVED"
                and (
                    (
                        approval.subject_type == "prompt_batch"
                        and approval.subject_sha256 == batch.request_set_sha256
                    )
                    or approval.subject_type.startswith("prompt_batch_retry:")
                )
            )
            if (run is None or plan is None or profile is None or resource_revision is None or approval is None
                or not approval_subject_valid
                or run.request_sha256 != request.expected_request_sha256):
                raise PromptBatchStateConflict()
            declaration = json.loads(profile.declaration_json)
            endpoint = validate_endpoint(declaration["endpoint_url"], locality=declaration["locality"],
                resolver=self.resolver if declaration["locality"] == "REMOTE_HTTPS" else None)
            credential = resolve_runtime_credential(credential_kind=declaration["credential_kind"],
                canvas_agent_api_key=self.prompt_editor_api_key)
            document = PromptDocumentV1.model_validate_json(item.base_document_json)
            prompt_profile = PromptResourcePayload.model_validate_json(resource_revision.payload_json)
            payload = attempt.provider_payload_json.encode("utf-8")
            run_id = UUID(run.id)
        with self._dispatch_slots:
            with self._write_gate:
                with self.session_factory.begin() as session:
                    current = self.repository.attempt(session, str(attempt_id))
                    run = session.get(RunRequestRow, str(run_id))
                    plan = session.scalar(select(EgressPlanRow).where(EgressPlanRow.run_request_id == str(run_id)))
                    batch = self.repository.batch(session, str(batch_id))
                    if current is None or run is None or plan is None or batch is None or current.state != "PENDING_EXECUTION":
                        raise PromptBatchStateConflict()
                    now = utc_now()
                    session.add(RunAuthorizationRow(id=str(uuid4()), run_request_id=run.id,
                        approval_id=batch.approval_id, graph_revision_id=run.graph_revision_id,
                        graph_revision_sha256=run.graph_revision_sha256, config_revision_id=run.config_revision_id,
                        config_revision_sha256=run.config_revision_sha256, payload_sha256=run.payload_sha256,
                        task_count=1, cost_bound=plan.cost_bound, max_retries=0,
                        request_sha256=run.request_sha256, egress_plan_sha256=plan.plan_sha256,
                        model_profile_revision_id=run.model_profile_revision_id,
                        model_profile_revision_sha256=run.model_profile_revision_sha256,
                        branch_id=run.branch_id, branch_head_version=run.branch_head_version,
                        selection_version=run.selection_version, scope_sha256=run.scope_sha256,
                        endpoint_identity_sha256=plan.endpoint_identity_sha256, cost_mode=plan.cost_mode,
                        currency=plan.currency, state="APPROVED", issued_at=now,
                        expires_at=now + timedelta(minutes=5), consumed_at=None))
            self.run_control.consume_agent(project_id, run_id,
                endpoint_identity_sha256=endpoint.identity_sha256, credential_available=True, actor_id=actor_id)
            with self._write_gate:
                with self.session_factory.begin() as session:
                    current = self.repository.attempt(session, str(attempt_id))
                    batch = self.repository.batch(session, str(batch_id))
                    if current is None or batch is None or current.state != "PENDING_EXECUTION":
                        raise PromptBatchStateConflict()
                    current.state = "DISPATCHING"
                    current.dispatched_at = current.updated_at = utc_now()
                    batch.phase, batch.updated_at = "IN_PROGRESS", current.updated_at
                    self.events.append_in_session(session, project_id=project_id,
                        correlation_id=UUID(batch.correlation_id), actor_kind="SYSTEM",
                        actor_id="prompt-batch-orchestrator", subject_type="prompt_batch_item",
                        subject_id=item_id, event_type="prompt_batch_item.dispatched",
                        payload={"attempt_id": str(attempt_id)})
            try:
                result = self.adapter.send(endpoint_url=endpoint.canonical_url, canonical_payload=payload,
                    credential=credential, timeout_seconds=declaration["timeout_seconds"], max_response_bytes=512_000)
                draft = PromptBatchItemDraftV1.model_validate_json(result.output_json)
                compiled = compile_batch_item_output(document=document, profile=prompt_profile,
                    expected_shot_id=UUID(item.shot_id), expected_prompt_node_id=UUID(item.prompt_node_id), draft=draft)
            except (ModelProviderError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                return self._fail(project_id, batch_id, item_id, attempt_id)
            with self._write_gate:
                with self.session_factory.begin() as session:
                    current = self.repository.attempt(session, str(attempt_id))
                    batch = self.repository.batch(session, str(batch_id))
                    if current is None or batch is None or current.state != "DISPATCHING":
                        raise PromptBatchStateConflict()
                    current.state = "SUCCEEDED"
                    current.output_json = canonical_json(draft.model_dump(mode="json"))
                    current.output_sha256 = compiled.output_sha256
                    current.compiled_document_json = canonical_json(compiled.document.model_dump(mode="json"))
                    current.compiled_document_sha256 = compiled.document_sha256
                    current.updated_at = current.completed_at = utc_now()
                    run = session.get(RunRequestRow, current.run_request_id)
                    if run:
                        run.state = "TERMINAL"
                    self._refresh_phase(session, batch)
                    self.events.append_in_session(session, project_id=project_id,
                        correlation_id=UUID(batch.correlation_id), actor_kind="SYSTEM",
                        actor_id="prompt-batch-orchestrator", subject_type="prompt_batch_item",
                        subject_id=item_id, event_type="prompt_batch_item.succeeded",
                        payload={"attempt_id": str(attempt_id), "output_sha256": current.output_sha256,
                        "compiled_document_sha256": current.compiled_document_sha256})
        return self.get(project_id, batch_id)

    def decide_item(self, project_id: UUID, batch_id: UUID, item_id: UUID,
        request: PromptBatchItemDecisionRequest, *, actor_id: str) -> PromptBatchResponse:
        request_sha256 = self._mutation_sha256(
            "DECIDE_ITEM", "prompt_batch_item", item_id, request
        )
        with self._write_gate:
            with self.session_factory.begin() as session:
                batch = self.repository.batch(session, str(batch_id))
                if self._is_mutation_replay(
                    session, project_id=project_id,
                    client_mutation_id=request.client_mutation_id,
                    operation_kind="DECIDE_ITEM", subject_type="prompt_batch_item",
                    subject_id=item_id, request_sha256=request_sha256,
                ):
                    if batch is None or batch.project_id != str(project_id):
                        raise PromptBatchNotFound(str(batch_id))
                    return self._response(session, batch)
                item = self.repository.item(session, str(item_id))
                attempt = self.repository.attempt(session, str(request.attempt_id))
                if (batch is None or item is None or attempt is None or batch.project_id != str(project_id)
                    or batch.phase not in {"IN_PROGRESS", "REVIEW_PENDING"} or item.batch_id != batch.id
                    or item.current_attempt_id != attempt.id or attempt.state != "SUCCEEDED"
                    or attempt.output_sha256 != request.expected_output_sha256):
                    raise PromptBatchStateConflict()
                decision = PromptBatchItemDecisionRow(id=str(uuid4()), item_id=item.id, attempt_id=attempt.id,
                    expected_output_sha256=attempt.output_sha256, decision=request.decision,
                    decision_sequence=request.expected_decision_version + 1, actor_id=actor_id, created_at=utc_now())
                if not self.repository.add_decision_with_cas(session, item_id=item.id,
                    expected_version=request.expected_decision_version, decision=decision, updated_at=utc_now()):
                    raise PromptBatchStateConflict()
                batch.version += 1
                batch.updated_at = utc_now()
                self.events.append_in_session(session, project_id=project_id,
                    correlation_id=UUID(batch.correlation_id), actor_kind="HUMAN", actor_id=actor_id,
                    subject_type="prompt_batch_item", subject_id=item_id,
                    event_type="prompt_batch_item.decision_recorded",
                    payload={"attempt_id": attempt.id, "decision": request.decision,
                    "output_sha256": attempt.output_sha256})
                self._record_mutation(
                    session, project_id=project_id,
                    client_mutation_id=request.client_mutation_id,
                    operation_kind="DECIDE_ITEM", subject_type="prompt_batch_item",
                    subject_id=item_id, request_sha256=request_sha256,
                )
        return self.get(project_id, batch_id)

    def accept_all_succeeded(self, project_id: UUID, batch_id: UUID,
        request: PromptBatchAcceptAllRequest, *, actor_id: str) -> PromptBatchResponse:
        request_sha256 = self._mutation_sha256(
            "ACCEPT_ALL_SUCCEEDED", "prompt_batch", batch_id, request
        )
        with self._write_gate:
            with self.session_factory.begin() as session:
                batch = self.repository.batch(session, str(batch_id))
                if self._is_mutation_replay(
                    session, project_id=project_id,
                    client_mutation_id=request.client_mutation_id,
                    operation_kind="ACCEPT_ALL_SUCCEEDED", subject_type="prompt_batch",
                    subject_id=batch_id, request_sha256=request_sha256,
                ):
                    if batch is None or batch.project_id != str(project_id):
                        raise PromptBatchNotFound(str(batch_id))
                    return self._response(session, batch)
                if (batch is None or batch.project_id != str(project_id)
                    or batch.phase not in {"IN_PROGRESS", "REVIEW_PENDING"}
                    or batch.version != request.expected_batch_version):
                    raise PromptBatchStateConflict()
                accepted = 0
                for item in self.repository.items_for_batch(session, batch.id):
                    attempt = session.get(PromptBatchItemAttemptRow, item.current_attempt_id)
                    if attempt is None or attempt.state != "SUCCEEDED" or attempt.output_sha256 is None:
                        continue
                    latest_values = self.repository.decisions_for_item(session, item.id)
                    latest = latest_values[-1] if latest_values else None
                    if latest and latest.attempt_id == attempt.id and latest.decision == "ACCEPTED":
                        continue
                    decision = PromptBatchItemDecisionRow(id=str(uuid4()), item_id=item.id,
                        attempt_id=attempt.id, expected_output_sha256=attempt.output_sha256,
                        decision="ACCEPTED", decision_sequence=item.decision_version + 1,
                        actor_id=actor_id, created_at=utc_now())
                    if not self.repository.add_decision_with_cas(session, item_id=item.id,
                        expected_version=item.decision_version, decision=decision, updated_at=utc_now()):
                        raise PromptBatchStateConflict()
                    accepted += 1
                    self.events.append_in_session(session, project_id=project_id,
                        correlation_id=UUID(batch.correlation_id), actor_kind="HUMAN", actor_id=actor_id,
                        subject_type="prompt_batch_item", subject_id=UUID(item.id),
                        event_type="prompt_batch_item.decision_recorded",
                        payload={"attempt_id": attempt.id, "decision": "ACCEPTED",
                        "output_sha256": attempt.output_sha256})
                if accepted == 0:
                    raise PromptBatchStateConflict("no undecided succeeded items")
                batch.version += 1
                batch.updated_at = utc_now()
                self._record_mutation(
                    session, project_id=project_id,
                    client_mutation_id=request.client_mutation_id,
                    operation_kind="ACCEPT_ALL_SUCCEEDED", subject_type="prompt_batch",
                    subject_id=batch_id, request_sha256=request_sha256,
                )
        return self.get(project_id, batch_id)

    def retry_failed(self, project_id: UUID, batch_id: UUID, request: PromptBatchRetryRequest,
        *, actor_id: str) -> PromptBatchResponse:
        request_sha256 = self._mutation_sha256(
            "RETRY_FAILED", "prompt_batch", batch_id, request
        )
        with self._write_gate:
            with self.session_factory.begin() as session:
                batch = self.repository.batch(session, str(batch_id))
                if self._is_mutation_replay(
                    session, project_id=project_id,
                    client_mutation_id=request.client_mutation_id,
                    operation_kind="RETRY_FAILED", subject_type="prompt_batch",
                    subject_id=batch_id, request_sha256=request_sha256,
                ):
                    if batch is None or batch.project_id != str(project_id):
                        raise PromptBatchNotFound(str(batch_id))
                    return self._response(session, batch)
                if (batch is None or batch.project_id != str(project_id) or batch.phase != "REVIEW_PENDING"
                    or batch.version != request.expected_batch_version):
                    raise PromptBatchStateConflict()
                requested = set(request.failed_item_ids)
                items = [item for item in self.repository.items_for_batch(session, batch.id)
                    if UUID(item.id) in requested]
                if len(items) != len(requested):
                    raise PromptBatchStateConflict("retry subset contains an unknown item")
                now = utc_now()
                for item in items:
                    parent = session.get(PromptBatchItemAttemptRow, item.current_attempt_id)
                    if parent is None or parent.state != "FAILED":
                        raise PromptBatchStateConflict("only current FAILED attempts may be retried")
                    old_run = session.get(RunRequestRow, parent.run_request_id)
                    old_plan = session.scalar(select(EgressPlanRow).where(EgressPlanRow.run_request_id == parent.run_request_id))
                    if old_run is None or old_plan is None:
                        raise PromptBatchStateConflict()
                    attempt_id, run_id = uuid4(), uuid4()
                    run_body = json.loads(old_run.request_json)
                    retry_scope = dict(run_body["scope"])
                    retry_scope["retry"] = {"parent_attempt_id": parent.id,
                        "attempt_number": parent.attempt_number + 1,
                        "client_mutation_id": str(request.client_mutation_id)}
                    run_body["scope"] = retry_scope
                    retry_scope_sha256 = canonical_sha256(retry_scope)
                    run_request_sha256 = canonical_sha256({"project_id": old_run.project_id,
                        "graph_revision_id": old_run.graph_revision_id,
                        "graph_revision_sha256": old_run.graph_revision_sha256,
                        "config_revision_id": old_run.config_revision_id,
                        "config_revision_sha256": old_run.config_revision_sha256,
                        "model_profile_revision_id": old_run.model_profile_revision_id,
                        "model_profile_revision_sha256": old_run.model_profile_revision_sha256,
                        "branch_id": old_run.branch_id,
                        "branch_head_version": old_run.branch_head_version,
                        "selection_version": old_run.selection_version,
                        "scope_sha256": retry_scope_sha256,
                        "payload_sha256": old_run.payload_sha256,
                        "endpoint_identity_sha256": run_body["endpoint_identity_sha256"]})
                    run = RunRequestRow(id=str(run_id), project_id=old_run.project_id,
                        correlation_id=batch.correlation_id, graph_revision_id=old_run.graph_revision_id,
                        graph_revision_sha256=old_run.graph_revision_sha256,
                        config_revision_id=old_run.config_revision_id, config_revision_sha256=old_run.config_revision_sha256,
                        payload_sha256=old_run.payload_sha256, operation_kind=old_run.operation_kind,
                        task_count=1, request_json=canonical_json(run_body), request_sha256=run_request_sha256,
                        state="PENDING_APPROVAL", approval_id=None,
                        model_profile_revision_id=old_run.model_profile_revision_id,
                        model_profile_revision_sha256=old_run.model_profile_revision_sha256,
                        branch_id=old_run.branch_id, branch_head_version=old_run.branch_head_version,
                        selection_version=old_run.selection_version, scope_json=canonical_json(retry_scope),
                        scope_sha256=retry_scope_sha256, created_at=now)
                    session.add(run)
                    session.flush()
                    plan_body = {"mode": old_plan.mode, "endpoint": old_plan.endpoint,
                        "endpoint_identity_sha256": old_plan.endpoint_identity_sha256,
                        "data_classes": json.loads(old_plan.data_classes_json), "task_count": 1,
                        "cost_mode": old_plan.cost_mode, "currency": old_plan.currency,
                        "cost_bound_minor": old_plan.cost_bound_minor, "gpu_count": 0, "max_retries": 0,
                        "graph_revision_id": old_plan.graph_revision_id,
                        "graph_revision_sha256": old_plan.graph_revision_sha256,
                        "config_revision_id": old_plan.config_revision_id,
                        "config_revision_sha256": old_plan.config_revision_sha256,
                        "model_profile_revision_id": old_plan.model_profile_revision_id,
                        "payload_sha256": old_plan.payload_sha256, "request_sha256": run_request_sha256,
                        "preview": json.loads(old_plan.preview_json)}
                    plan = EgressPlanRow(id=str(uuid4()), run_request_id=run.id,
                        graph_revision_id=old_plan.graph_revision_id,
                        graph_revision_sha256=old_plan.graph_revision_sha256,
                        config_revision_id=old_plan.config_revision_id,
                        config_revision_sha256=old_plan.config_revision_sha256,
                        payload_sha256=old_plan.payload_sha256, mode=old_plan.mode,
                        endpoint=old_plan.endpoint, data_classes_json=old_plan.data_classes_json,
                        task_count=1, cost_bound=old_plan.cost_bound, gpu_count=0, max_retries=0,
                        model_profile_revision_id=old_plan.model_profile_revision_id,
                        endpoint_identity_sha256=old_plan.endpoint_identity_sha256,
                        preview_json=old_plan.preview_json, cost_mode=old_plan.cost_mode,
                        currency=old_plan.currency, cost_bound_minor=old_plan.cost_bound_minor,
                        plan_sha256=canonical_sha256(plan_body), created_at=now)
                    session.add(plan)
                    attempt = PromptBatchItemAttemptRow(id=str(attempt_id), item_id=item.id,
                        attempt_number=parent.attempt_number + 1, parent_attempt_id=parent.id,
                        run_request_id=run.id, provider_payload_json=parent.provider_payload_json,
                        provider_payload_sha256=parent.provider_payload_sha256, state="PENDING_APPROVAL",
                        output_json=None, output_sha256=None, compiled_document_json=None,
                        compiled_document_sha256=None, failure_code=None, created_at=now, updated_at=now,
                        dispatched_at=None, completed_at=None)
                    self.repository.add_attempt(session, attempt)
                    session.flush()
                    if not self.repository.set_current_attempt(session, item_id=item.id,
                        expected_attempt_id=parent.id, attempt_id=attempt.id, updated_at=now):
                        raise PromptBatchStateConflict()
                batch.phase = "RETRY_APPROVAL_REQUIRED"
                batch.version += 1
                batch.updated_at = now
                session.flush()
                retry_hash = self._retry_hash(session, batch)
                self.events.append_in_session(session, project_id=project_id,
                    correlation_id=UUID(batch.correlation_id), actor_kind="HUMAN", actor_id=actor_id,
                    subject_type="prompt_batch", subject_id=batch_id, event_type="prompt_batch.retry_requested",
                    payload={"retry_request_set_sha256": retry_hash, "item_count": len(items)})
                self._record_mutation(
                    session, project_id=project_id,
                    client_mutation_id=request.client_mutation_id,
                    operation_kind="RETRY_FAILED", subject_type="prompt_batch",
                    subject_id=batch_id, request_sha256=request_sha256,
                )
        return self.get(project_id, batch_id)

    def approve_retry(self, project_id: UUID, batch_id: UUID,
        request: PromptBatchApprovalRequest, *, actor_id: str) -> PromptBatchResponse:
        with self._write_gate:
            with self.session_factory.begin() as session:
                batch = self.repository.batch(session, str(batch_id))
                retry_hash = self._retry_hash(session, batch) if batch else None
                if (batch is None or batch.project_id != str(project_id)
                    or batch.phase != "RETRY_APPROVAL_REQUIRED" or retry_hash is None
                    or retry_hash != request.expected_request_set_sha256):
                    raise PromptBatchStateConflict()
                pending_attempts = [
                    attempt
                    for item in self.repository.items_for_batch(session, batch.id)
                    if (attempt := session.get(PromptBatchItemAttemptRow, item.current_attempt_id)) is not None
                    and attempt.state == "PENDING_APPROVAL"
                ]
                if not pending_attempts:
                    raise PromptBatchStateConflict()
                retry_attempt_number = max(attempt.attempt_number for attempt in pending_attempts)
                approval = ApprovalDecisionRow(id=str(uuid4()), project_id=str(project_id),
                    correlation_id=batch.correlation_id,
                    subject_type=f"prompt_batch_retry:{retry_attempt_number}", subject_id=batch.id,
                    subject_sha256=retry_hash,
                    risk_sha256=canonical_sha256({"request_set_sha256": retry_hash}),
                    decision=request.decision, actor_id=actor_id, created_at=utc_now())
                session.add(approval)
                batch.approval_id = approval.id
                batch.phase = "IN_PROGRESS" if request.decision == "APPROVED" else "REVIEW_PENDING"
                batch.version += 1
                batch.updated_at = utc_now()
                for item in self.repository.items_for_batch(session, batch.id):
                    attempt = session.get(PromptBatchItemAttemptRow, item.current_attempt_id)
                    if attempt is None or attempt.attempt_number == 1 or attempt.state != "PENDING_APPROVAL":
                        continue
                    run = session.get(RunRequestRow, attempt.run_request_id)
                    if run is None:
                        raise PromptBatchStateConflict()
                    attempt.state = "PENDING_EXECUTION" if request.decision == "APPROVED" else "REJECTED"
                    attempt.updated_at = batch.updated_at
                    attempt.completed_at = None if request.decision == "APPROVED" else batch.updated_at
                    run.state = "APPROVED" if request.decision == "APPROVED" else "REJECTED"
                    run.approval_id = approval.id
                self.events.append_in_session(session, project_id=project_id,
                    correlation_id=UUID(batch.correlation_id), actor_kind="HUMAN", actor_id=actor_id,
                    subject_type="prompt_batch", subject_id=batch_id, event_type="prompt_batch.retry_approved",
                    payload={"retry_request_set_sha256": retry_hash, "decision": request.decision})
        return self.get(project_id, batch_id)

    def finalize(self, project_id: UUID, batch_id: UUID, request: PromptBatchFinalizeRequest,
        *, actor_id: str) -> PromptBatchResponse:
        mutation_request_sha256 = self._mutation_sha256(
            "FINALIZE", "prompt_batch", batch_id, request
        )
        with self.session_factory() as session:
            batch = self.repository.batch(session, str(batch_id))
            if self._is_mutation_replay(
                session, project_id=project_id,
                client_mutation_id=request.client_mutation_id,
                operation_kind="FINALIZE", subject_type="prompt_batch",
                subject_id=batch_id, request_sha256=mutation_request_sha256,
            ):
                if batch is None or batch.project_id != str(project_id):
                    raise PromptBatchNotFound(str(batch_id))
                return self._response(session, batch)
            if (
                batch is not None
                and batch.project_id == str(project_id)
                and batch.phase == "FINALIZED"
                and batch.graph_change_set_id is not None
            ):
                changeset = session.get(GraphChangeSetRow, batch.graph_change_set_id)
                if (
                    changeset is not None
                    and changeset.client_mutation_id == str(request.client_mutation_id)
                    and request.expected_batch_version == batch.version - 1
                ):
                    return self._response(session, batch)
                if (
                    changeset is not None
                    and changeset.client_mutation_id == str(request.client_mutation_id)
                ):
                    raise ClientMutationReuse(str(request.client_mutation_id))
            if (batch is None or batch.project_id != str(project_id) or batch.phase != "REVIEW_PENDING"
                or batch.version != request.expected_batch_version or batch.graph_change_set_id is not None):
                raise PromptBatchStateConflict()
            revision = self.graphs.get_revision(project_id, UUID(batch.base_graph_revision_id))
            graph = normalize_to_v3(revision.snapshot)
            accepted = []
            decision_subjects = []
            for item in self.repository.items_for_batch(session, batch.id):
                attempt = session.get(PromptBatchItemAttemptRow, item.current_attempt_id)
                decisions = self.repository.decisions_for_item(session, item.id)
                latest = decisions[-1] if decisions else None
                decision_subjects.append({"item_id": item.id, "attempt_id": attempt.id if attempt else None,
                    "output_sha256": attempt.output_sha256 if attempt else None,
                    "decision": latest.decision if latest else None,
                    "decision_sequence": latest.decision_sequence if latest else 0})
                if (attempt is not None and latest is not None and attempt.state == "SUCCEEDED"
                    and latest.attempt_id == attempt.id and latest.expected_output_sha256 == attempt.output_sha256
                    and latest.decision == "ACCEPTED"):
                    try:
                        base_document = PromptDocumentV1.model_validate_json(
                            item.base_document_json
                        )
                        resource_revision = session.get(
                            ResourceRevisionRow,
                            item.prompt_profile_resource_revision_id,
                        )
                        if resource_revision is None:
                            raise ValueError("prompt profile revision is unavailable")
                        prompt_profile = PromptResourcePayload.model_validate_json(
                            resource_revision.payload_json
                        )
                        draft = PromptBatchItemDraftV1.model_validate_json(
                            attempt.output_json
                        )
                        recompiled = compile_batch_item_output(
                            document=base_document,
                            profile=prompt_profile,
                            expected_shot_id=UUID(item.shot_id),
                            expected_prompt_node_id=UUID(item.prompt_node_id),
                            draft=draft,
                        )
                        stored_document = PromptDocumentV1.model_validate_json(
                            attempt.compiled_document_json
                        )
                        if (
                            canonical_sha256(base_document.model_dump(mode="json"))
                            != item.base_document_sha256
                            or resource_revision.payload_sha256
                            != item.prompt_profile_revision_sha256
                            or recompiled.output_sha256 != attempt.output_sha256
                            or recompiled.document_sha256
                            != attempt.compiled_document_sha256
                            or canonical_json(stored_document.model_dump(mode="json"))
                            != canonical_json(recompiled.document.model_dump(mode="json"))
                        ):
                            raise ValueError("stored hashes do not match")
                    except (ValueError, TypeError, json.JSONDecodeError) as error:
                        raise PromptBatchIntegrityError(
                            "stored prompt batch attempt failed integrity validation"
                        ) from error
                    accepted.append((item, attempt))
            if not accepted:
                raise PromptBatchStateConflict("at least one accepted item is required")
            documents = {UUID(item.prompt_node_id): PromptDocumentV1.model_validate_json(attempt.compiled_document_json)
                for item, attempt in accepted if attempt.compiled_document_json is not None}
            nodes = [node.model_copy(update={"data": node.data.model_copy(update={"document": documents[node.id]})})
                if isinstance(node, PromptNodeV3) and node.id in documents else node for node in graph.nodes]
            proposed = graph.model_copy(update={"nodes": nodes})
            decision_set_sha256 = canonical_sha256(decision_subjects)
            application_target = self._target(batch)
            base_graph_revision_id = batch.base_graph_revision_id
            batch_correlation_id = UUID(batch.correlation_id)

        def link(session: Session, changeset: GraphChangeSetRow) -> None:
            current = self.repository.batch(session, str(batch_id))
            if (current is None or current.version != request.expected_batch_version
                or current.phase != "REVIEW_PENDING" or current.graph_change_set_id is not None):
                raise PromptBatchStateConflict()
            current.graph_change_set_id = changeset.id
            current.final_decision_set_sha256 = decision_set_sha256
            current.phase = "FINALIZED"
            current.version += 1
            current.updated_at = current.finalized_at = utc_now()
            self.events.append_in_session(session, project_id=project_id,
                correlation_id=UUID(current.correlation_id), actor_kind="HUMAN", actor_id=actor_id,
                subject_type="prompt_batch", subject_id=batch_id, event_type="prompt_batch.finalized",
                payload={"decision_set_sha256": decision_set_sha256,
                "graph_change_set_id": changeset.id, "accepted_count": len(accepted)})
            self._record_mutation(
                session, project_id=project_id,
                client_mutation_id=request.client_mutation_id,
                operation_kind="FINALIZE", subject_type="prompt_batch",
                subject_id=batch_id, request_sha256=mutation_request_sha256,
            )

        self.graph_changes.request(project_id, CreateGraphChangeSetRequest.model_validate({
            "base_graph_revision_id": base_graph_revision_id,
            "client_mutation_id": str(request.client_mutation_id),
            "operations": [{"op": "replace_snapshot", "snapshot": proposed.model_dump(mode="json")}],
            "application_target": application_target,
        }), actor_id=actor_id, after_create=link, correlation_id=batch_correlation_id)
        return self.get(project_id, batch_id)

    def reconcile_dispatching(self) -> int:
        with self._write_gate:
            with self.session_factory.begin() as session:
                attempts = list(
                    session.scalars(
                        select(PromptBatchItemAttemptRow).where(
                            PromptBatchItemAttemptRow.state == "DISPATCHING"
                        )
                    )
                )
                now = utc_now()
                affected_batches: dict[str, PromptBatchRow] = {}
                for attempt in attempts:
                    item = self.repository.item(session, attempt.item_id)
                    batch = (
                        self.repository.batch(session, item.batch_id)
                        if item is not None
                        else None
                    )
                    if item is None or batch is None:
                        raise PromptBatchStateConflict(
                            "dispatching attempt storage is incomplete"
                        )
                    attempt.state = "UNKNOWN_TERMINAL"
                    attempt.failure_code = "RESTART_DURING_DISPATCH"
                    attempt.updated_at = now
                    attempt.completed_at = now
                    run = session.get(RunRequestRow, attempt.run_request_id)
                    if run is not None:
                        run.state = "TERMINAL"
                    affected_batches[batch.id] = batch
                    self.events.append_in_session(
                        session,
                        project_id=UUID(batch.project_id),
                        correlation_id=UUID(batch.correlation_id),
                        actor_kind="SYSTEM",
                        actor_id="prompt-batch-orchestrator",
                        subject_type="prompt_batch_item",
                        subject_id=UUID(item.id),
                        event_type="prompt_batch_item.unknown_terminal",
                        payload={
                            "attempt_id": attempt.id,
                            "code": attempt.failure_code,
                        },
                    )
                for batch in affected_batches.values():
                    self._refresh_phase(session, batch)
                    batch.version += 1
                return len(attempts)
