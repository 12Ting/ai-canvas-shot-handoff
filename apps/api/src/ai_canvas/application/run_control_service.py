import json
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.control_config_service import ControlConfigService
from ai_canvas.application.errors import (
    ApprovalRequired,
    ApprovalSubjectIntegrityError,
    ApprovalSubjectStale,
    ProjectNotFound,
)
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts.run_control import (
    AgentEgressPlanResponse,
    AgentRunRequestResponse,
    CreateRunRequest,
    DryRunReceiptResponse,
    EgressPlanResponse,
    RunAuthorizationResponse,
    RunRequestResponse,
)
from ai_canvas.contracts.experience_events import ActorKind
from ai_canvas.domain.control.canonical import canonical_json, canonical_sha256
from ai_canvas.domain.graph.snapshots import canonical_snapshot, parse_snapshot_json
from ai_canvas.domain.model_endpoints.policy import EndpointPolicy
from ai_canvas.infrastructure.db.models import (
    ApprovalDecisionRow,
    ControlConfigRevisionRow,
    DryRunReceiptRow,
    EgressPlanRow,
    GraphBranchHeadRow,
    GraphBranchRow,
    GraphDocumentRow,
    GraphRevisionRow,
    GraphSelectionPointerRow,
    ModelProfileRevisionRow,
    PromptBatchItemAttemptRow,
    PromptBatchItemRow,
    PromptBatchRow,
    RunAuthorizationRow,
    RunRequestRow,
)
from ai_canvas.infrastructure.model_providers.credentials import CredentialUnavailable


class EgressNotAllowedInF1(ValueError):
    pass


class EgressContentCategoryPolicyError(ValueError):
    pass


class DryRunTaskLimitExceeded(ValueError):
    pass


class DryRunValidationFailed(ValueError):
    pass


class RunRequestNotFound(LookupError):
    def __init__(self, request_id: UUID) -> None:
        self.request_id = request_id
        super().__init__(str(request_id))


class RunAuthorizationConsumed(ValueError):
    pass


class RunAuthorizationExpired(RunAuthorizationConsumed):
    pass


class RunAuthorizationInvalidated(RunAuthorizationConsumed):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def is_expired(expires_at: datetime, now: datetime) -> bool:
    return expires_at <= (now.replace(tzinfo=None) if expires_at.tzinfo is None else now)


class RunControlService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        events: EventSpineService,
        *,
        write_gate: WriteGate | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.events = events
        self._write_gate = write_gate or WriteGate()

    @staticmethod
    def _request_response(row: RunRequestRow, plan: EgressPlanRow) -> RunRequestResponse:
        return RunRequestResponse(
            id=UUID(row.id),
            project_id=UUID(row.project_id),
            correlation_id=UUID(row.correlation_id),
            request_sha256=row.request_sha256,
            egress_plan_sha256=plan.plan_sha256,
            egress_plan=EgressPlanResponse(
                mode="NONE",
                endpoint=None,
                external_data_classes=json.loads(plan.data_classes_json),
                task_count=plan.task_count,
                cost_bound=0,
                gpu_count=0,
                request_sha256=row.request_sha256,
                plan_sha256=plan.plan_sha256,
            ),
            state=row.state,
        )

    @staticmethod
    def _agent_request_response(
        row: RunRequestRow,
        plan: EgressPlanRow,
        profile: ModelProfileRevisionRow,
    ) -> AgentRunRequestResponse:
        declaration = json.loads(profile.declaration_json)
        return AgentRunRequestResponse(
            id=UUID(row.id),
            project_id=UUID(row.project_id),
            correlation_id=UUID(row.correlation_id),
            operation_kind=row.operation_kind,
            model_profile_revision_id=UUID(profile.id),
            branch_id=UUID(row.branch_id),
            branch_head_version=row.branch_head_version,
            selection_version=row.selection_version,
            scope_sha256=row.scope_sha256,
            request_sha256=row.request_sha256,
            egress_plan_sha256=plan.plan_sha256,
            egress_plan=AgentEgressPlanResponse(
                endpoint=plan.endpoint,
                locality=declaration["locality"],
                endpoint_identity_sha256=plan.endpoint_identity_sha256,
                external_data_classes=json.loads(plan.data_classes_json),
                gpu_count=0,
                media_execution=False,
                cost_mode=plan.cost_mode,
                currency=plan.currency,
                cost_bound_minor=plan.cost_bound_minor,
                data_residency=declaration["data_residency"],
                retention_policy=declaration["retention_policy"],
                request_sha256=row.request_sha256,
                plan_sha256=plan.plan_sha256,
                preview=json.loads(plan.preview_json),
            ),
            state=row.state,
        )

    @staticmethod
    def _authorization_response(row: RunAuthorizationRow) -> RunAuthorizationResponse:
        return RunAuthorizationResponse(id=UUID(row.id), state=row.state, expires_at=as_utc(row.expires_at))

    @staticmethod
    def _receipt_response(row: DryRunReceiptRow) -> DryRunReceiptResponse:
        return DryRunReceiptResponse(
            id=UUID(row.id),
            run_request_id=UUID(row.run_request_id),
            terminal_state=row.terminal_state,
            created_at=as_utc(row.created_at),
        )

    @staticmethod
    def _reject_egress(request: CreateRunRequest) -> None:
        if (
            request.endpoint is not None
            or request.external_data_classes
            or request.cost_bound
            or request.gpu_count
            or request.real_media
            or request.max_retries
        ):
            raise EgressNotAllowedInF1

    def request(
        self,
        project_id: UUID,
        request: CreateRunRequest,
        *,
        actor_id: str,
        actor_kind: ActorKind = "HUMAN",
    ) -> RunRequestResponse:
        self._reject_egress(request)
        with self._write_gate:
            with self.session_factory.begin() as session:
                document = session.scalar(
                    select(GraphDocumentRow).where(GraphDocumentRow.project_id == str(project_id))
                )
                if document is None or document.active_revision_id is None:
                    raise ProjectNotFound(str(project_id))
                _, config = ControlConfigService._active_in_session(session)
                graph_revision = session.get(GraphRevisionRow, document.active_revision_id)
                graph_revision_sha256 = self._graph_revision_content_sha256(graph_revision)
                config_revision_sha256 = self._config_revision_content_sha256(config)
                if graph_revision_sha256 is None:
                    raise ApprovalSubjectIntegrityError("graph_revision", document.active_revision_id)
                if config_revision_sha256 is None:
                    raise ApprovalSubjectIntegrityError("control_config_revision", config.id)
                if request.task_count > self._config_max_tasks(config.config_json):
                    raise DryRunTaskLimitExceeded
                body = request.model_dump(mode="json")
                payload_sha256 = canonical_sha256(body)
                correlation_id, request_id = uuid4(), uuid4()
                request_hash = canonical_sha256(
                    {
                        "project_id": str(project_id),
                        "graph_revision_id": document.active_revision_id,
                        "graph_revision_sha256": graph_revision_sha256,
                        "config_revision_id": config.id,
                        "config_revision_sha256": config_revision_sha256,
                        "payload_sha256": payload_sha256,
                    }
                )
                row = RunRequestRow(
                    id=str(request_id),
                    project_id=str(project_id),
                    correlation_id=str(correlation_id),
                    graph_revision_id=document.active_revision_id,
                    graph_revision_sha256=graph_revision_sha256,
                    config_revision_id=config.id,
                    config_revision_sha256=config_revision_sha256,
                    payload_sha256=payload_sha256,
                    operation_kind="DRY_RUN",
                    task_count=request.task_count,
                    request_json=canonical_json(body),
                    request_sha256=request_hash,
                    state="PENDING_APPROVAL",
                    approval_id=None,
                    created_at=utc_now(),
                )
                session.add(row)
                session.flush()
                plan_payload = {
                    "mode": "NONE",
                    "endpoint": None,
                    "data_classes": [],
                    "task_count": request.task_count,
                    "cost_bound": 0,
                    "gpu_count": 0,
                    "max_retries": 0,
                    "graph_revision_id": document.active_revision_id,
                    "graph_revision_sha256": graph_revision_sha256,
                    "config_revision_id": config.id,
                    "config_revision_sha256": config_revision_sha256,
                    "payload_sha256": payload_sha256,
                    "request_sha256": request_hash,
                }
                plan = EgressPlanRow(
                    id=str(uuid4()),
                    run_request_id=row.id,
                    graph_revision_id=document.active_revision_id,
                    graph_revision_sha256=graph_revision_sha256,
                    config_revision_id=config.id,
                    config_revision_sha256=config_revision_sha256,
                    payload_sha256=payload_sha256,
                    mode="NONE",
                    endpoint=None,
                    data_classes_json="[]",
                    task_count=request.task_count,
                    cost_bound=0,
                    gpu_count=0,
                    max_retries=0,
                    plan_sha256=canonical_sha256(plan_payload),
                    created_at=utc_now(),
                )
                session.add(plan)
                session.flush()
                self.events.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=correlation_id,
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                    subject_type="run_request",
                    subject_id=request_id,
                    event_type="run_request.requested",
                    payload={"request_sha256": request_hash},
                )
                self.events.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=correlation_id,
                    actor_kind="SYSTEM",
                    actor_id="control-plane",
                    subject_type="egress_plan",
                    subject_id=UUID(plan.id),
                    event_type="egress_plan.sealed",
                    payload={"plan_sha256": plan.plan_sha256, "mode": "NONE"},
                )
                return self._request_response(row, plan)

    def request_agent(
        self,
        project_id: UUID,
        *,
        branch_id: UUID,
        expected_head_revision_id: UUID,
        expected_head_version: int,
        expected_selection_version: int,
        scope: dict,
        provider_payload: dict,
        payload_sha256: str,
        endpoint_policy: EndpointPolicy,
        preview: dict,
        actor_id: str,
        operation_kind: str = "AGENT_PROPOSAL",
        profile_role: str = "CANVAS_AGENT",
        after_create: Callable[[Session, RunRequestRow, EgressPlanRow, ModelProfileRevisionRow], None] | None = None,
    ) -> AgentRunRequestResponse:
        allowed_operations = {
            "AGENT_PROPOSAL",
            "PROMPT_PROFILE_EXTRACTION",
            "PROMPT_BATCH_ITEM",
        }
        if operation_kind not in allowed_operations:
            raise ValueError("unsupported model operation kind")
        if profile_role not in {"CANVAS_AGENT", "PROMPT_EDITOR"}:
            raise ValueError("unsupported model profile role")
        canonical_payload_sha256 = canonical_sha256(provider_payload)
        if payload_sha256 != canonical_payload_sha256:
            raise ApprovalSubjectIntegrityError("provider_payload", payload_sha256)
        scope_sha256 = canonical_sha256(scope)
        with self._write_gate:
            with self.session_factory.begin() as session:
                document = session.scalar(
                    select(GraphDocumentRow).where(
                        GraphDocumentRow.project_id == str(project_id)
                    )
                )
                branch = session.get(GraphBranchRow, str(branch_id))
                head = session.get(GraphBranchHeadRow, str(branch_id))
                if (
                    document is None
                    or branch is None
                    or branch.graph_document_id != document.id
                    or head is None
                    or head.graph_revision_id != str(expected_head_revision_id)
                    or head.version != expected_head_version
                ):
                    raise ApprovalSubjectStale()
                selection = session.get(GraphSelectionPointerRow, document.id)
                if (
                    selection is None
                    or selection.branch_id != str(branch_id)
                    or selection.graph_revision_id != str(expected_head_revision_id)
                    or selection.version != expected_selection_version
                ):
                    raise ApprovalSubjectStale()
                _, config = ControlConfigService._active_in_session(session)
                config_body = json.loads(config.config_json)
                profile_pointer = (
                    "active_canvas_agent_profile_revision_id"
                    if profile_role == "CANVAS_AGENT"
                    else "active_prompt_editor_profile_revision_id"
                )
                profile_id = config_body.get(profile_pointer)
                profile = (
                    session.get(ModelProfileRevisionRow, profile_id)
                    if isinstance(profile_id, str)
                    else None
                )
                graph_revision = session.get(
                    GraphRevisionRow, str(expected_head_revision_id)
                )
                graph_revision_sha256 = self._graph_revision_content_sha256(
                    graph_revision
                )
                config_revision_sha256 = self._config_revision_content_sha256(config)
                if (
                    profile is None
                    or canonical_json(json.loads(profile.declaration_json))
                    != profile.declaration_json
                    or canonical_sha256(json.loads(profile.declaration_json))
                    != profile.declaration_sha256
                ):
                    raise ApprovalSubjectIntegrityError(
                        "model_profile_revision", str(profile_id)
                    )
                declaration = json.loads(profile.declaration_json)
                if (
                    graph_revision_sha256 is None
                    or config_revision_sha256 is None
                    or declaration["role"] != profile_role
                    or declaration["endpoint_url"] != endpoint_policy.canonical_url
                    or declaration["locality"] != endpoint_policy.locality
                ):
                    raise ApprovalSubjectIntegrityError("agent_run_request", str(project_id))
                contexts = preview.get("contexts")
                if not isinstance(contexts, list):
                    raise EgressContentCategoryPolicyError(
                        "egress preview must declare content categories"
                    )
                data_classes = [
                    item.get("category")
                    for item in contexts
                    if isinstance(item, dict)
                    and isinstance(item.get("category"), str)
                    and item.get("category")
                ]
                if (
                    len(data_classes) != len(contexts)
                    or len(data_classes) != len(set(data_classes))
                    or not set(data_classes)
                    <= set(declaration["allowed_content_categories"])
                ):
                    raise EgressContentCategoryPolicyError(
                        "egress content categories exceed the active model profile"
                    )
                body = {
                    "operation_kind": operation_kind,
                    "task_count": 1,
                    "model_profile_revision_id": profile.id,
                    "branch_id": str(branch_id),
                    "branch_head_version": expected_head_version,
                    "selection_version": expected_selection_version,
                    "scope": scope,
                    "provider_payload": provider_payload,
                    "endpoint_identity_sha256": endpoint_policy.identity_sha256,
                }
                request_id, correlation_id = uuid4(), uuid4()
                request_hash = canonical_sha256(
                    {
                        "project_id": str(project_id),
                        "graph_revision_id": str(expected_head_revision_id),
                        "graph_revision_sha256": graph_revision_sha256,
                        "config_revision_id": config.id,
                        "config_revision_sha256": config_revision_sha256,
                        "model_profile_revision_id": profile.id,
                        "model_profile_revision_sha256": profile.declaration_sha256,
                        "branch_id": str(branch_id),
                        "branch_head_version": expected_head_version,
                        "selection_version": expected_selection_version,
                        "scope_sha256": scope_sha256,
                        "payload_sha256": payload_sha256,
                        "endpoint_identity_sha256": endpoint_policy.identity_sha256,
                    }
                )
                row = RunRequestRow(
                    id=str(request_id),
                    project_id=str(project_id),
                    correlation_id=str(correlation_id),
                    graph_revision_id=str(expected_head_revision_id),
                    graph_revision_sha256=graph_revision_sha256,
                    config_revision_id=config.id,
                    config_revision_sha256=config_revision_sha256,
                    payload_sha256=payload_sha256,
                    operation_kind=operation_kind,
                    task_count=1,
                    request_json=canonical_json(body),
                    request_sha256=request_hash,
                    state="PENDING_APPROVAL",
                    approval_id=None,
                    model_profile_revision_id=profile.id,
                    model_profile_revision_sha256=profile.declaration_sha256,
                    branch_id=str(branch_id),
                    branch_head_version=expected_head_version,
                    selection_version=expected_selection_version,
                    scope_json=canonical_json(scope),
                    scope_sha256=scope_sha256,
                    created_at=utc_now(),
                )
                session.add(row)
                session.flush()
                plan_payload = {
                    "mode": "MODEL_PROVIDER",
                    "endpoint": endpoint_policy.canonical_url,
                    "endpoint_identity_sha256": endpoint_policy.identity_sha256,
                    "data_classes": data_classes,
                    "task_count": 1,
                    "cost_mode": declaration["cost_mode"],
                    "currency": declaration["currency"],
                    "cost_bound_minor": declaration["cost_bound_minor"],
                    "gpu_count": 0,
                    "max_retries": 0,
                    "graph_revision_id": row.graph_revision_id,
                    "graph_revision_sha256": row.graph_revision_sha256,
                    "config_revision_id": row.config_revision_id,
                    "config_revision_sha256": row.config_revision_sha256,
                    "model_profile_revision_id": profile.id,
                    "payload_sha256": payload_sha256,
                    "request_sha256": request_hash,
                    "preview": preview,
                }
                plan = EgressPlanRow(
                    id=str(uuid4()),
                    run_request_id=row.id,
                    graph_revision_id=row.graph_revision_id,
                    graph_revision_sha256=row.graph_revision_sha256,
                    config_revision_id=row.config_revision_id,
                    config_revision_sha256=row.config_revision_sha256,
                    payload_sha256=payload_sha256,
                    mode="MODEL_PROVIDER",
                    endpoint=endpoint_policy.canonical_url,
                    data_classes_json=canonical_json(data_classes),
                    task_count=1,
                    cost_bound=declaration["cost_bound_minor"] or 0,
                    gpu_count=0,
                    max_retries=0,
                    model_profile_revision_id=profile.id,
                    endpoint_identity_sha256=endpoint_policy.identity_sha256,
                    preview_json=canonical_json(preview),
                    cost_mode=declaration["cost_mode"],
                    currency=declaration["currency"],
                    cost_bound_minor=declaration["cost_bound_minor"],
                    plan_sha256=canonical_sha256(plan_payload),
                    created_at=utc_now(),
                )
                session.add(plan)
                session.flush()
                self.events.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=correlation_id,
                    actor_kind="HUMAN",
                    actor_id=actor_id,
                    subject_type="run_request",
                    subject_id=request_id,
                    event_type="run_request.requested",
                    payload={
                        "request_sha256": request_hash,
                        "operation_kind": operation_kind,
                    },
                )
                self.events.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=correlation_id,
                    actor_kind="SYSTEM",
                    actor_id="control-plane",
                    subject_type="egress_plan",
                    subject_id=UUID(plan.id),
                    event_type="egress_plan.sealed",
                    payload={
                        "plan_sha256": plan.plan_sha256,
                        "mode": "MODEL_PROVIDER",
                    },
                )
                if after_create is not None:
                    after_create(session, row, plan, profile)
                return self._agent_request_response(row, plan, profile)

    @staticmethod
    def _config_max_tasks(config_json: str) -> int:
        return int(json.loads(config_json)["dry_run_max_tasks"])

    @staticmethod
    def _graph_revision_content_sha256(revision: GraphRevisionRow | None) -> str | None:
        if revision is None:
            return None
        try:
            canonical, digest = canonical_snapshot(parse_snapshot_json(revision.snapshot_json))
        except (TypeError, ValueError):
            return None
        return digest if canonical == revision.snapshot_json and digest == revision.snapshot_hash else None

    @staticmethod
    def _config_revision_content_sha256(revision: ControlConfigRevisionRow | None) -> str | None:
        if revision is None:
            return None
        try:
            body = json.loads(revision.config_json)
            canonical = canonical_json(body)
            digest = canonical_sha256(body)
        except (TypeError, ValueError):
            return None
        return digest if canonical == revision.config_json and digest == revision.config_sha256 else None

    @staticmethod
    def _plan_content_sha256(row: RunRequestRow, plan: EgressPlanRow) -> str | None:
        try:
            return canonical_sha256(
                {
                    "mode": plan.mode,
                    "endpoint": plan.endpoint,
                    "data_classes": json.loads(plan.data_classes_json),
                    "task_count": plan.task_count,
                    "cost_bound": plan.cost_bound,
                    "gpu_count": plan.gpu_count,
                    "max_retries": plan.max_retries,
                    "graph_revision_id": plan.graph_revision_id,
                    "graph_revision_sha256": plan.graph_revision_sha256,
                    "config_revision_id": plan.config_revision_id,
                    "config_revision_sha256": plan.config_revision_sha256,
                    "payload_sha256": plan.payload_sha256,
                    "request_sha256": row.request_sha256,
                }
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _request_content_sha256(row: RunRequestRow) -> str | None:
        try:
            body = json.loads(row.request_json)
            payload_sha256 = canonical_sha256(body)
            if payload_sha256 != row.payload_sha256:
                return None
            return canonical_sha256(
                {
                    "project_id": row.project_id,
                    "graph_revision_id": row.graph_revision_id,
                    "graph_revision_sha256": row.graph_revision_sha256,
                    "config_revision_id": row.config_revision_id,
                    "config_revision_sha256": row.config_revision_sha256,
                    "payload_sha256": payload_sha256,
                }
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _agent_request_content_sha256(row: RunRequestRow) -> str | None:
        try:
            body = json.loads(row.request_json)
            if canonical_json(body) != row.request_json:
                return None
            payload_sha256 = canonical_sha256(body["provider_payload"])
            scope_sha256 = canonical_sha256(body["scope"])
            if (
                body["operation_kind"] != row.operation_kind
                or row.operation_kind
                not in {
                    "AGENT_PROPOSAL",
                    "PROMPT_PROFILE_EXTRACTION",
                    "PROMPT_BATCH_ITEM",
                }
                or body["task_count"] != 1
                or payload_sha256 != row.payload_sha256
                or scope_sha256 != row.scope_sha256
                or canonical_json(body["scope"]) != row.scope_json
            ):
                return None
            return canonical_sha256(
                {
                    "project_id": row.project_id,
                    "graph_revision_id": row.graph_revision_id,
                    "graph_revision_sha256": row.graph_revision_sha256,
                    "config_revision_id": row.config_revision_id,
                    "config_revision_sha256": row.config_revision_sha256,
                    "model_profile_revision_id": row.model_profile_revision_id,
                    "model_profile_revision_sha256": row.model_profile_revision_sha256,
                    "branch_id": row.branch_id,
                    "branch_head_version": row.branch_head_version,
                    "selection_version": row.selection_version,
                    "scope_sha256": row.scope_sha256,
                    "payload_sha256": payload_sha256,
                    "endpoint_identity_sha256": body["endpoint_identity_sha256"],
                }
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _agent_plan_content_sha256(row: RunRequestRow, plan: EgressPlanRow) -> str | None:
        try:
            return canonical_sha256(
                {
                    "mode": plan.mode,
                    "endpoint": plan.endpoint,
                    "endpoint_identity_sha256": plan.endpoint_identity_sha256,
                    "data_classes": json.loads(plan.data_classes_json),
                    "task_count": plan.task_count,
                    "cost_mode": plan.cost_mode,
                    "currency": plan.currency,
                    "cost_bound_minor": plan.cost_bound_minor,
                    "gpu_count": plan.gpu_count,
                    "max_retries": plan.max_retries,
                    "graph_revision_id": plan.graph_revision_id,
                    "graph_revision_sha256": plan.graph_revision_sha256,
                    "config_revision_id": plan.config_revision_id,
                    "config_revision_sha256": plan.config_revision_sha256,
                    "model_profile_revision_id": plan.model_profile_revision_id,
                    "payload_sha256": plan.payload_sha256,
                    "request_sha256": row.request_sha256,
                    "preview": json.loads(plan.preview_json),
                }
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _agent_bindings_current(
        session: Session,
        row: RunRequestRow,
        plan: EgressPlanRow,
    ) -> bool:
        document = session.scalar(
            select(GraphDocumentRow).where(GraphDocumentRow.project_id == row.project_id)
        )
        if document is None:
            return False
        head = session.get(GraphBranchHeadRow, row.branch_id)
        selection = session.get(GraphSelectionPointerRow, document.id)
        _, active_config = ControlConfigService._active_in_session(session)
        active_body = json.loads(active_config.config_json)
        profile = session.get(ModelProfileRevisionRow, row.model_profile_revision_id)
        graph_revision = session.get(GraphRevisionRow, row.graph_revision_id)
        config_revision = session.get(ControlConfigRevisionRow, row.config_revision_id)
        return bool(
            row.operation_kind
            in {
                "AGENT_PROPOSAL",
                "PROMPT_PROFILE_EXTRACTION",
                "PROMPT_BATCH_ITEM",
            }
            and row.task_count == 1
            and head is not None
            and head.graph_revision_id == row.graph_revision_id
            and head.version == row.branch_head_version
            and selection is not None
            and selection.branch_id == row.branch_id
            and selection.graph_revision_id == row.graph_revision_id
            and selection.version == row.selection_version
            and active_config.id == row.config_revision_id
            and profile is not None
            and active_body.get(
                "active_canvas_agent_profile_revision_id"
                if json.loads(profile.declaration_json).get("role")
                == "CANVAS_AGENT"
                else "active_prompt_editor_profile_revision_id"
            )
            == row.model_profile_revision_id
            and profile.declaration_sha256 == row.model_profile_revision_sha256
            and canonical_json(json.loads(profile.declaration_json))
            == profile.declaration_json
            and canonical_sha256(json.loads(profile.declaration_json))
            == profile.declaration_sha256
            and RunControlService._graph_revision_content_sha256(graph_revision)
            == row.graph_revision_sha256
            and RunControlService._config_revision_content_sha256(config_revision)
            == row.config_revision_sha256
            and RunControlService._agent_request_content_sha256(row)
            == row.request_sha256
            and RunControlService._agent_plan_content_sha256(row, plan)
            == plan.plan_sha256
            and plan.model_profile_revision_id == row.model_profile_revision_id
            and plan.payload_sha256 == row.payload_sha256
            and plan.mode == "MODEL_PROVIDER"
            and plan.endpoint is not None
            and plan.endpoint_identity_sha256 is not None
            and plan.task_count == 1
            and plan.gpu_count == 0
            and plan.max_retries == 0
        )

    @staticmethod
    def _dry_run_validation_error(
        row: RunRequestRow,
        plan: EgressPlanRow,
        config: ControlConfigRevisionRow,
    ) -> str | None:
        try:
            request = CreateRunRequest.model_validate_json(row.request_json)
            max_tasks = RunControlService._config_max_tasks(config.config_json)
        except (TypeError, ValueError):
            return "RUN_SCHEMA_INVALID"
        if row.operation_kind != "DRY_RUN" or request.operation_kind != "DRY_RUN":
            return "OPERATION_KIND_INVALID"
        if request.task_count != row.task_count or request.task_count != plan.task_count:
            return "TASK_COUNT_MISMATCH"
        if request.task_count > max_tasks:
            return "TASK_LIMIT_EXCEEDED"
        if (
            plan.mode != "NONE"
            or plan.endpoint is not None
            or json.loads(plan.data_classes_json) != []
            or plan.cost_bound != 0
            or plan.gpu_count != 0
            or plan.max_retries != 0
        ):
            return "EGRESS_PLAN_INVALID"
        return None

    @staticmethod
    def _approval_risk_sha256(plan: EgressPlanRow) -> str:
        return canonical_sha256(
            {
                "egress_plan_sha256": plan.plan_sha256,
                "mode": plan.mode,
                "task_count": plan.task_count,
                "cost_bound": plan.cost_bound,
                "gpu_count": plan.gpu_count,
            }
        )

    def _record_request_rejection_in_session(
        self,
        session: Session,
        row: RunRequestRow,
        *,
        reason: str,
        actor_kind: ActorKind = "SYSTEM",
        actor_id: str = "control-plane",
    ) -> None:
        row.state = "REJECTED"
        self.events.append_in_session(
            session,
            project_id=UUID(row.project_id),
            correlation_id=UUID(row.correlation_id),
            actor_kind=actor_kind,
            actor_id=actor_id,
            subject_type="run_request",
            subject_id=UUID(row.id),
            event_type="run_request.rejected",
            payload={"reason": reason},
        )

    def approve_agent(
        self,
        project_id: UUID,
        request_id: UUID,
        *,
        expected_request_sha256: str,
        actor_id: str,
        decision: str = "APPROVED",
        issue_authorization: bool = True,
    ) -> RunAuthorizationResponse | AgentRunRequestResponse:
        rejection: ApprovalSubjectIntegrityError | ApprovalSubjectStale | None = None
        response: RunAuthorizationResponse | AgentRunRequestResponse | None = None
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = session.get(RunRequestRow, str(request_id))
                if (
                    row is None
                    or row.project_id != str(project_id)
                    or row.operation_kind
                    not in {
                        "AGENT_PROPOSAL",
                        "PROMPT_PROFILE_EXTRACTION",
                        "PROMPT_BATCH_ITEM",
                    }
                ):
                    raise RunRequestNotFound(request_id)
                plan = session.scalar(
                    select(EgressPlanRow).where(EgressPlanRow.run_request_id == row.id)
                )
                profile = session.get(
                    ModelProfileRevisionRow, row.model_profile_revision_id
                )
                if (
                    row.state != "PENDING_APPROVAL"
                    or row.request_sha256 != expected_request_sha256
                ):
                    raise ApprovalSubjectStale()
                if plan is None or profile is None:
                    self._record_request_rejection_in_session(
                        session, row, reason="SUBJECT_INTEGRITY_MISMATCH"
                    )
                    rejection = ApprovalSubjectIntegrityError("run_request", row.id)
                elif not self._agent_bindings_current(session, row, plan):
                    self._record_request_rejection_in_session(
                        session, row, reason="BASE_REVISION_STALE"
                    )
                    rejection = ApprovalSubjectStale()
                else:
                    approval = ApprovalDecisionRow(
                        id=str(uuid4()),
                        project_id=row.project_id,
                        correlation_id=row.correlation_id,
                        subject_type="run_request",
                        subject_id=row.id,
                        subject_sha256=row.request_sha256,
                        risk_sha256=self._approval_risk_sha256(plan),
                        decision=decision,
                        actor_id=actor_id,
                        created_at=utc_now(),
                    )
                    session.add(approval)
                    row.approval_id = approval.id
                    self.events.append_in_session(
                        session,
                        project_id=project_id,
                        correlation_id=UUID(row.correlation_id),
                        actor_kind="HUMAN",
                        actor_id=actor_id,
                        subject_type="run_request",
                        subject_id=request_id,
                        event_type="approval.recorded",
                        payload={
                            "approval_id": approval.id,
                            "decision": decision,
                            "subject_sha256": row.request_sha256,
                        },
                    )
                    if decision == "REJECTED":
                        self._record_request_rejection_in_session(
                            session,
                            row,
                            reason="USER_REJECTED",
                            actor_kind="HUMAN",
                            actor_id=actor_id,
                        )
                        response = self._agent_request_response(row, plan, profile)
                    else:
                        row.state = "APPROVED"
                        response = (
                            self._issue_agent_authorization_in_session(
                                session, row=row, plan=plan, approval=approval
                            )
                            if issue_authorization
                            else self._agent_request_response(row, plan, profile)
                        )
        if rejection is not None:
            raise rejection
        if response is None:
            raise RuntimeError("Agent run approval produced no response")
        return response

    def _issue_agent_authorization_in_session(
        self,
        session: Session,
        *,
        row: RunRequestRow,
        plan: EgressPlanRow,
        approval: ApprovalDecisionRow,
    ) -> RunAuthorizationResponse:
        now = utc_now()
        authorization = RunAuthorizationRow(
            id=str(uuid4()),
            run_request_id=row.id,
            approval_id=approval.id,
            graph_revision_id=row.graph_revision_id,
            graph_revision_sha256=row.graph_revision_sha256,
            config_revision_id=row.config_revision_id,
            config_revision_sha256=row.config_revision_sha256,
            payload_sha256=row.payload_sha256,
            task_count=1,
            cost_bound=plan.cost_bound,
            max_retries=0,
            request_sha256=row.request_sha256,
            egress_plan_sha256=plan.plan_sha256,
            model_profile_revision_id=row.model_profile_revision_id,
            model_profile_revision_sha256=row.model_profile_revision_sha256,
            branch_id=row.branch_id,
            branch_head_version=row.branch_head_version,
            selection_version=row.selection_version,
            scope_sha256=row.scope_sha256,
            endpoint_identity_sha256=plan.endpoint_identity_sha256,
            cost_mode=plan.cost_mode,
            currency=plan.currency,
            state="APPROVED",
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
            consumed_at=None,
        )
        session.add(authorization)
        self.events.append_in_session(
            session,
            project_id=UUID(row.project_id),
            correlation_id=UUID(row.correlation_id),
            actor_kind="SYSTEM",
            actor_id="control-plane",
            subject_type="run_authorization",
            subject_id=UUID(authorization.id),
            event_type="run_authorization.issued",
            payload={
                "request_sha256": row.request_sha256,
                "egress_plan_sha256": plan.plan_sha256,
            },
        )
        return self._authorization_response(authorization)

    def issue_agent_authorization(
        self, project_id: UUID, request_id: UUID
    ) -> RunAuthorizationResponse:
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = session.get(RunRequestRow, str(request_id))
                if (
                    row is None
                    or row.project_id != str(project_id)
                    or row.operation_kind != "PROMPT_PROFILE_EXTRACTION"
                    or row.state != "APPROVED"
                ):
                    raise ApprovalRequired()
                plan = session.scalar(
                    select(EgressPlanRow).where(EgressPlanRow.run_request_id == row.id)
                )
                approval = session.get(ApprovalDecisionRow, row.approval_id)
                existing = session.scalar(
                    select(RunAuthorizationRow).where(
                        RunAuthorizationRow.run_request_id == row.id
                    )
                )
                if (
                    plan is None
                    or approval is None
                    or existing is not None
                    or approval.subject_type != "run_request"
                    or approval.subject_id != row.id
                    or approval.subject_sha256 != row.request_sha256
                    or approval.risk_sha256 != self._approval_risk_sha256(plan)
                    or approval.decision != "APPROVED"
                    or not self._agent_bindings_current(session, row, plan)
                ):
                    raise ApprovalSubjectStale()
                return self._issue_agent_authorization_in_session(
                    session, row=row, plan=plan, approval=approval
                )

    def consume_agent(
        self,
        project_id: UUID,
        request_id: UUID,
        *,
        endpoint_identity_sha256: str,
        credential_available: bool,
        actor_id: str,
    ) -> RunAuthorizationResponse:
        if not credential_available:
            raise CredentialUnavailable("model runtime credential is unavailable")
        rejection: type[RunAuthorizationConsumed] | None = None
        response: RunAuthorizationResponse | None = None
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = session.get(RunRequestRow, str(request_id))
                if (
                    row is None
                    or row.project_id != str(project_id)
                    or row.operation_kind
                    not in {
                        "AGENT_PROPOSAL",
                        "PROMPT_PROFILE_EXTRACTION",
                        "PROMPT_BATCH_ITEM",
                    }
                ):
                    raise RunRequestNotFound(request_id)
                authorization = session.scalar(
                    select(RunAuthorizationRow).where(
                        RunAuthorizationRow.run_request_id == row.id
                    )
                )
                plan = session.scalar(
                    select(EgressPlanRow).where(EgressPlanRow.run_request_id == row.id)
                )
                if authorization is None:
                    raise ApprovalRequired()
                if plan is None:
                    raise ApprovalSubjectIntegrityError("run_request", row.id)
                if authorization.state != "APPROVED":
                    raise RunAuthorizationConsumed()
                approval = session.get(ApprovalDecisionRow, authorization.approval_id)
                batch_approval_bound = False
                if row.operation_kind == "PROMPT_BATCH_ITEM" and approval is not None:
                    batch_attempt = session.scalar(
                        select(PromptBatchItemAttemptRow).where(
                            PromptBatchItemAttemptRow.run_request_id == row.id
                        )
                    )
                    batch_item = (
                        session.get(PromptBatchItemRow, batch_attempt.item_id)
                        if batch_attempt is not None
                        else None
                    )
                    batch = (
                        session.get(PromptBatchRow, batch_item.batch_id)
                        if batch_item is not None
                        else None
                    )
                    approved_subject_sha256 = (
                        batch.request_set_sha256
                        if batch is not None and approval.subject_type == "prompt_batch"
                        else approval.subject_sha256
                    )
                    batch_approval_bound = bool(
                        batch is not None
                        and batch_item is not None
                        and batch_attempt is not None
                        and batch.approval_id == approval.id
                        and batch_item.current_attempt_id == batch_attempt.id
                        and batch.phase in {"PENDING_EXECUTION", "IN_PROGRESS"}
                        and (
                            approval.subject_type == "prompt_batch"
                            or approval.subject_type.startswith("prompt_batch_retry:")
                        )
                        and approval.subject_id == batch.id
                        and approval.risk_sha256
                        == canonical_sha256(
                            {"request_set_sha256": approved_subject_sha256}
                        )
                        and approval.decision == "APPROVED"
                    )
                bound = self._agent_bindings_current(session, row, plan) and bool(
                    endpoint_identity_sha256 == plan.endpoint_identity_sha256
                    and authorization.request_sha256 == row.request_sha256
                    and authorization.egress_plan_sha256 == plan.plan_sha256
                    and authorization.model_profile_revision_id
                    == row.model_profile_revision_id
                    and authorization.model_profile_revision_sha256
                    == row.model_profile_revision_sha256
                    and authorization.branch_id == row.branch_id
                    and authorization.branch_head_version == row.branch_head_version
                    and authorization.selection_version == row.selection_version
                    and authorization.scope_sha256 == row.scope_sha256
                    and authorization.endpoint_identity_sha256
                    == plan.endpoint_identity_sha256
                    and authorization.cost_mode == plan.cost_mode
                    and authorization.currency == plan.currency
                    and approval is not None
                    and (
                        batch_approval_bound
                        or (
                            approval.subject_type == "run_request"
                            and approval.subject_id == row.id
                            and approval.subject_sha256 == row.request_sha256
                            and approval.risk_sha256
                            == self._approval_risk_sha256(plan)
                            and approval.decision == "APPROVED"
                        )
                    )
                )
                if not bound:
                    authorization.state = "INVALIDATED"
                    self.events.append_in_session(
                        session,
                        project_id=project_id,
                        correlation_id=UUID(row.correlation_id),
                        actor_kind="SYSTEM",
                        actor_id="control-plane",
                        subject_type="run_authorization",
                        subject_id=UUID(authorization.id),
                        event_type="run_authorization.invalidated",
                        payload={},
                    )
                    rejection = RunAuthorizationInvalidated
                elif is_expired(authorization.expires_at, utc_now()):
                    authorization.state = "EXPIRED"
                    self.events.append_in_session(
                        session,
                        project_id=project_id,
                        correlation_id=UUID(row.correlation_id),
                        actor_kind="SYSTEM",
                        actor_id="control-plane",
                        subject_type="run_authorization",
                        subject_id=UUID(authorization.id),
                        event_type="run_authorization.expired",
                        payload={},
                    )
                    rejection = RunAuthorizationExpired
                else:
                    consumed_at = utc_now()
                    consumed = session.execute(
                        update(RunAuthorizationRow)
                        .where(
                            RunAuthorizationRow.id == authorization.id,
                            RunAuthorizationRow.state == "APPROVED",
                            RunAuthorizationRow.expires_at > consumed_at,
                            RunAuthorizationRow.request_sha256 == row.request_sha256,
                            RunAuthorizationRow.egress_plan_sha256 == plan.plan_sha256,
                            RunAuthorizationRow.model_profile_revision_id
                            == row.model_profile_revision_id,
                            RunAuthorizationRow.model_profile_revision_sha256
                            == row.model_profile_revision_sha256,
                            RunAuthorizationRow.branch_id == row.branch_id,
                            RunAuthorizationRow.branch_head_version
                            == row.branch_head_version,
                            RunAuthorizationRow.selection_version == row.selection_version,
                            RunAuthorizationRow.scope_sha256 == row.scope_sha256,
                            RunAuthorizationRow.endpoint_identity_sha256
                            == plan.endpoint_identity_sha256,
                        )
                        .values(state="CONSUMED", consumed_at=consumed_at)
                        .execution_options(synchronize_session=False)
                    )
                    if consumed.rowcount != 1:
                        raise RunAuthorizationConsumed()
                    authorization.state = "CONSUMED"
                    authorization.consumed_at = consumed_at
                    self.events.append_in_session(
                        session,
                        project_id=project_id,
                        correlation_id=UUID(row.correlation_id),
                        actor_kind="HUMAN",
                        actor_id=actor_id,
                        subject_type="run_authorization",
                        subject_id=UUID(authorization.id),
                        event_type="run_authorization.consumed",
                        payload={},
                    )
                    response = self._authorization_response(authorization)
        if rejection is not None:
            raise rejection()
        if response is None:
            raise RunAuthorizationConsumed()
        return response

    def approve(
        self,
        project_id: UUID,
        request_id: UUID,
        *,
        expected_request_sha256: str,
        actor_id: str,
        decision: str = "APPROVED",
    ) -> RunAuthorizationResponse | RunRequestResponse:
        rejection: ApprovalSubjectIntegrityError | ApprovalSubjectStale | None = None
        response: RunAuthorizationResponse | RunRequestResponse | None = None
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = session.get(RunRequestRow, str(request_id))
                if row is None or row.project_id != str(project_id):
                    raise RunRequestNotFound(request_id)
                plan = session.scalar(
                    select(EgressPlanRow).where(EgressPlanRow.run_request_id == str(request_id))
                )
                if row.state != "PENDING_APPROVAL" or row.request_sha256 != expected_request_sha256:
                    raise ApprovalSubjectStale
                document = session.scalar(
                    select(GraphDocumentRow).where(GraphDocumentRow.project_id == str(project_id))
                )
                _, active_config = ControlConfigService._active_in_session(session)
                graph_revision = session.get(GraphRevisionRow, row.graph_revision_id)
                config_revision = session.get(ControlConfigRevisionRow, row.config_revision_id)
                graph_revision_sha256 = self._graph_revision_content_sha256(graph_revision)
                config_revision_sha256 = self._config_revision_content_sha256(config_revision)
                if plan is None:
                    self._record_request_rejection_in_session(
                        session, row, reason="SUBJECT_INTEGRITY_MISMATCH"
                    )
                    rejection = ApprovalSubjectIntegrityError("run_request", row.id)
                elif (
                    document is None
                    or document.active_revision_id != row.graph_revision_id
                    or active_config.id != row.config_revision_id
                ):
                    self._record_request_rejection_in_session(session, row, reason="BASE_REVISION_STALE")
                    rejection = ApprovalSubjectStale()
                elif (
                    graph_revision_sha256 != row.graph_revision_sha256
                    or config_revision_sha256 != row.config_revision_sha256
                    or self._request_content_sha256(row) != row.request_sha256
                    or self._plan_content_sha256(row, plan) != plan.plan_sha256
                    or plan.graph_revision_id != row.graph_revision_id
                    or plan.graph_revision_sha256 != row.graph_revision_sha256
                    or plan.config_revision_id != row.config_revision_id
                    or plan.config_revision_sha256 != row.config_revision_sha256
                    or plan.payload_sha256 != row.payload_sha256
                    or plan.task_count != row.task_count
                    or plan.mode != "NONE"
                    or plan.endpoint is not None
                    or json.loads(plan.data_classes_json) != []
                    or plan.cost_bound != 0
                    or plan.gpu_count != 0
                    or plan.max_retries != 0
                ):
                    self._record_request_rejection_in_session(
                        session, row, reason="SUBJECT_INTEGRITY_MISMATCH"
                    )
                    rejection = ApprovalSubjectIntegrityError("run_request", row.id)
                else:
                    approval = ApprovalDecisionRow(
                        id=str(uuid4()),
                        project_id=row.project_id,
                        correlation_id=row.correlation_id,
                        subject_type="run_request",
                        subject_id=row.id,
                        subject_sha256=row.request_sha256,
                        risk_sha256=self._approval_risk_sha256(plan),
                        decision=decision,
                        actor_id=actor_id,
                        created_at=utc_now(),
                    )
                    session.add(approval)
                    row.approval_id = approval.id
                    self.events.append_in_session(
                        session,
                        project_id=project_id,
                        correlation_id=UUID(row.correlation_id),
                        actor_kind="HUMAN",
                        actor_id=actor_id,
                        subject_type="run_request",
                        subject_id=request_id,
                        event_type="approval.recorded",
                        payload={
                            "approval_id": approval.id,
                            "decision": decision,
                            "subject_sha256": row.request_sha256,
                        },
                    )
                    if decision == "REJECTED":
                        self._record_request_rejection_in_session(
                            session,
                            row,
                            reason="USER_REJECTED",
                            actor_kind="HUMAN",
                            actor_id=actor_id,
                        )
                        response = self._request_response(row, plan)
                    else:
                        now = utc_now()
                        authorization = RunAuthorizationRow(
                            id=str(uuid4()),
                            run_request_id=row.id,
                            approval_id=approval.id,
                            graph_revision_id=row.graph_revision_id,
                            graph_revision_sha256=row.graph_revision_sha256,
                            config_revision_id=row.config_revision_id,
                            config_revision_sha256=row.config_revision_sha256,
                            payload_sha256=row.payload_sha256,
                            task_count=row.task_count,
                            cost_bound=plan.cost_bound,
                            max_retries=plan.max_retries,
                            request_sha256=row.request_sha256,
                            egress_plan_sha256=plan.plan_sha256,
                            state="APPROVED",
                            issued_at=now,
                            expires_at=now + timedelta(minutes=5),
                            consumed_at=None,
                        )
                        session.add(authorization)
                        row.state = "APPROVED"
                        self.events.append_in_session(
                            session,
                            project_id=project_id,
                            correlation_id=UUID(row.correlation_id),
                            actor_kind="SYSTEM",
                            actor_id="control-plane",
                            subject_type="run_authorization",
                            subject_id=UUID(authorization.id),
                            event_type="run_authorization.issued",
                            payload={
                                "request_sha256": row.request_sha256,
                                "egress_plan_sha256": plan.plan_sha256,
                            },
                        )
                        response = self._authorization_response(authorization)
        if rejection is not None:
            raise rejection
        if response is None:
            raise RuntimeError("run approval produced no response")
        return response

    def dry_run(self, project_id: UUID, request_id: UUID, *, actor_id: str) -> DryRunReceiptResponse:
        rejection: type[RunAuthorizationConsumed] | None = None
        validation_failure: str | None = None
        response: DryRunReceiptResponse | None = None
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = session.get(RunRequestRow, str(request_id))
                if row is None or row.project_id != str(project_id):
                    raise RunRequestNotFound(request_id)
                authorization = session.scalar(
                    select(RunAuthorizationRow).where(RunAuthorizationRow.run_request_id == str(request_id))
                )
                plan = session.scalar(
                    select(EgressPlanRow).where(EgressPlanRow.run_request_id == str(request_id))
                )
                if authorization is None:
                    raise ApprovalRequired
                if plan is None:
                    raise ApprovalSubjectIntegrityError("run_request", row.id)
                if authorization.state != "APPROVED":
                    raise RunAuthorizationConsumed
                document = session.scalar(
                    select(GraphDocumentRow).where(GraphDocumentRow.project_id == str(project_id))
                )
                _, active_config = ControlConfigService._active_in_session(session)
                graph_revision = session.get(GraphRevisionRow, row.graph_revision_id)
                config_revision = session.get(ControlConfigRevisionRow, row.config_revision_id)
                graph_revision_sha256 = self._graph_revision_content_sha256(graph_revision)
                config_revision_sha256 = self._config_revision_content_sha256(config_revision)
                request_content_sha256 = self._request_content_sha256(row)
                plan_content_sha256 = self._plan_content_sha256(row, plan)
                approval = session.get(ApprovalDecisionRow, authorization.approval_id)
                if (
                    document is None
                    or document.active_revision_id != row.graph_revision_id
                    or active_config.id != row.config_revision_id
                    or graph_revision_sha256 != row.graph_revision_sha256
                    or config_revision_sha256 != row.config_revision_sha256
                    or authorization.request_sha256 != row.request_sha256
                    or authorization.graph_revision_id != row.graph_revision_id
                    or authorization.graph_revision_sha256 != row.graph_revision_sha256
                    or authorization.config_revision_id != row.config_revision_id
                    or authorization.config_revision_sha256 != row.config_revision_sha256
                    or authorization.payload_sha256 != row.payload_sha256
                    or authorization.task_count != row.task_count
                    or authorization.cost_bound != plan.cost_bound
                    or authorization.max_retries != plan.max_retries
                    or row.request_sha256 != request_content_sha256
                    or authorization.egress_plan_sha256 != plan.plan_sha256
                    or plan.plan_sha256 != plan_content_sha256
                    or plan.graph_revision_id != row.graph_revision_id
                    or plan.graph_revision_sha256 != row.graph_revision_sha256
                    or plan.config_revision_id != row.config_revision_id
                    or plan.config_revision_sha256 != row.config_revision_sha256
                    or plan.payload_sha256 != row.payload_sha256
                    or approval is None
                    or approval.subject_type != "run_request"
                    or approval.subject_id != row.id
                    or approval.subject_sha256 != row.request_sha256
                    or approval.risk_sha256 != self._approval_risk_sha256(plan)
                    or approval.decision != "APPROVED"
                ):
                    authorization.state = "INVALIDATED"
                    row.state = "TERMINAL"
                    self.events.append_in_session(
                        session,
                        project_id=project_id,
                        correlation_id=UUID(row.correlation_id),
                        actor_kind="SYSTEM",
                        actor_id="control-plane",
                        subject_type="run_authorization",
                        subject_id=UUID(authorization.id),
                        event_type="run_authorization.invalidated",
                        payload={
                            "graph_revision_id": row.graph_revision_id,
                            "config_revision_id": row.config_revision_id,
                        },
                    )
                    rejection = RunAuthorizationInvalidated
                elif is_expired(authorization.expires_at, utc_now()):
                    authorization.state = "EXPIRED"
                    row.state = "TERMINAL"
                    self.events.append_in_session(
                        session,
                        project_id=project_id,
                        correlation_id=UUID(row.correlation_id),
                        actor_kind="SYSTEM",
                        actor_id="control-plane",
                        subject_type="run_authorization",
                        subject_id=UUID(authorization.id),
                        event_type="run_authorization.expired",
                        payload={},
                    )
                    rejection = RunAuthorizationExpired
                else:
                    consumed_at = utc_now()
                    consumed = session.execute(
                        update(RunAuthorizationRow)
                        .where(
                            RunAuthorizationRow.id == authorization.id,
                            RunAuthorizationRow.state == "APPROVED",
                            RunAuthorizationRow.expires_at > consumed_at,
                            RunAuthorizationRow.request_sha256 == row.request_sha256,
                            RunAuthorizationRow.egress_plan_sha256 == plan.plan_sha256,
                            RunAuthorizationRow.graph_revision_id == row.graph_revision_id,
                            RunAuthorizationRow.graph_revision_sha256 == row.graph_revision_sha256,
                            RunAuthorizationRow.config_revision_id == row.config_revision_id,
                            RunAuthorizationRow.config_revision_sha256 == row.config_revision_sha256,
                            RunAuthorizationRow.payload_sha256 == row.payload_sha256,
                            RunAuthorizationRow.task_count == row.task_count,
                            RunAuthorizationRow.cost_bound == plan.cost_bound,
                            RunAuthorizationRow.max_retries == plan.max_retries,
                        )
                        .values(state="CONSUMED", consumed_at=consumed_at)
                        .execution_options(synchronize_session=False)
                    )
                    if consumed.rowcount != 1:
                        raise RunAuthorizationConsumed
                    row.state = "TERMINAL"
                    self.events.append_in_session(
                        session,
                        project_id=project_id,
                        correlation_id=UUID(row.correlation_id),
                        actor_kind="HUMAN",
                        actor_id=actor_id,
                        subject_type="run_authorization",
                        subject_id=UUID(authorization.id),
                        event_type="run_authorization.consumed",
                        payload={},
                    )
                    validation_failure = self._dry_run_validation_error(
                        row,
                        plan,
                        config_revision,
                    )
                    receipt = DryRunReceiptRow(
                        id=str(uuid4()),
                        run_request_id=row.id,
                        run_authorization_id=authorization.id,
                        terminal_state=(
                            "DRY_RUN_VALIDATION_FAILED"
                            if validation_failure is not None
                            else "DRY_RUN_SUCCEEDED"
                        ),
                        created_at=utc_now(),
                    )
                    session.add(receipt)
                    if validation_failure is not None:
                        self.events.append_in_session(
                            session,
                            project_id=project_id,
                            correlation_id=UUID(row.correlation_id),
                            actor_kind="SYSTEM",
                            actor_id="control-plane",
                            subject_type="dry_run_receipt",
                            subject_id=UUID(receipt.id),
                            event_type="run.dry_run_failed",
                            payload={
                                "reason": validation_failure,
                                "no_egress": True,
                                "no_media": True,
                            },
                        )
                    else:
                        self.events.append_in_session(
                            session,
                            project_id=project_id,
                            correlation_id=UUID(row.correlation_id),
                            actor_kind="SYSTEM",
                            actor_id="control-plane",
                            subject_type="dry_run_receipt",
                            subject_id=UUID(receipt.id),
                            event_type="run.dry_run_succeeded",
                            payload={"no_egress": True, "no_media": True},
                        )
                        response = self._receipt_response(receipt)
        if rejection is not None:
            raise rejection
        if validation_failure is not None:
            raise DryRunValidationFailed(validation_failure)
        if response is None:
            raise RunAuthorizationConsumed
        return response
