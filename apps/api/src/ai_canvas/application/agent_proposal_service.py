import json
import socket
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID, uuid4

from pydantic import SecretStr, TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.control_config_service import ControlConfigService
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_changeset_service import GraphChangeSetService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.run_control_service import RunControlService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts.agent_proposals import (
    AgentGraphChangeSetDraftV1,
    AgentOutputDraft,
    AgentProposalResponse,
    CreateAgentProposalRequest,
)
from ai_canvas.contracts.graph_changesets import CreateGraphChangeSetRequest
from ai_canvas.domain.agent.payload import compile_agent_payload
from ai_canvas.domain.agent.scope import compile_graph_draft
from ai_canvas.domain.control.canonical import canonical_json, canonical_sha256
from ai_canvas.domain.graph.snapshots import normalize_to_v3
from ai_canvas.domain.model_endpoints.policy import validate_endpoint
from ai_canvas.infrastructure.db.model_profile_repository import AgentProposalRepository
from ai_canvas.infrastructure.db.models import (
    AgentProposalRow,
    EgressPlanRow,
    GraphChangeSetRow,
    ModelProfileRevisionRow,
    RunRequestRow,
)
from ai_canvas.infrastructure.model_providers.credentials import resolve_runtime_credential
from ai_canvas.infrastructure.model_providers.openai_compatible import (
    ModelProviderError,
    OpenAICompatibleChatJsonAdapter,
)


_output_adapter = TypeAdapter(AgentOutputDraft)


class AgentProposalNotFound(LookupError):
    pass


class AgentProposalStateConflict(RuntimeError):
    pass


class ModelProfileUnavailable(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def default_resolver(host: str) -> list[str]:
    return sorted({item[4][0] for item in socket.getaddrinfo(host, None)})


class AgentProposalService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        graphs: GraphService,
        run_control: RunControlService,
        graph_changes: GraphChangeSetService,
        events: EventSpineService,
        *,
        adapter: OpenAICompatibleChatJsonAdapter | None = None,
        canvas_agent_api_key: SecretStr | None = None,
        resolver: Callable[[str], list[str]] = default_resolver,
        repository: AgentProposalRepository | None = None,
        write_gate: WriteGate | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.graphs = graphs
        self.run_control = run_control
        self.graph_changes = graph_changes
        self.events = events
        self.adapter = adapter or OpenAICompatibleChatJsonAdapter()
        self.canvas_agent_api_key = canvas_agent_api_key
        self.resolver = resolver
        self.repository = repository or AgentProposalRepository()
        self._write_gate = write_gate or WriteGate()

    def _response_in_session(
        self, session: Session, row: AgentProposalRow
    ) -> AgentProposalResponse:
        run = session.get(RunRequestRow, row.run_request_id)
        plan = session.scalar(
            select(EgressPlanRow).where(EgressPlanRow.run_request_id == row.run_request_id)
        )
        profile = session.get(ModelProfileRevisionRow, row.model_profile_revision_id)
        if run is None or plan is None or profile is None:
            raise ValueError("Agent proposal storage is incomplete")
        output = (
            None
            if row.output_json is None
            else _output_adapter.validate_json(row.output_json)
        )
        graph_change_set = (
            None
            if row.graph_change_set_id is None
            else session.get(GraphChangeSetRow, row.graph_change_set_id)
        )
        return AgentProposalResponse(
            id=UUID(row.id),
            project_id=UUID(row.project_id),
            run_request=RunControlService._agent_request_response(run, plan, profile),
            state=row.state,
            output=output,
            graph_change_set=(
                None
                if graph_change_set is None
                else GraphChangeSetService._response(graph_change_set)
            ),
            failure_code=row.failure_code,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
        )

    def get(self, project_id: UUID, proposal_id: UUID) -> AgentProposalResponse:
        with self.session_factory() as session:
            row = self.repository.get(session, str(proposal_id))
            if row is None or row.project_id != str(project_id):
                raise AgentProposalNotFound(str(proposal_id))
            return self._response_in_session(session, row)

    def reconcile_dispatching(self) -> int:
        reconciled = 0
        with self._write_gate:
            with self.session_factory.begin() as session:
                for row in self.repository.dispatching(session):
                    run = session.get(RunRequestRow, row.run_request_id)
                    row.state = "UNKNOWN_TERMINAL"
                    row.failure_code = "DISPATCH_OUTCOME_UNKNOWN_AFTER_RESTART"
                    row.updated_at = utc_now()
                    row.completed_at = row.updated_at
                    if run is not None:
                        run.state = "TERMINAL"
                        self.events.append_in_session(
                            session,
                            project_id=UUID(row.project_id),
                            correlation_id=UUID(run.correlation_id),
                            actor_kind="SYSTEM",
                            actor_id="startup-reconciler",
                            subject_type="agent_proposal",
                            subject_id=UUID(row.id),
                            event_type="agent_proposal.unknown_terminal",
                            payload={"reason": "DISPATCH_OUTCOME_UNKNOWN_AFTER_RESTART"},
                        )
                    reconciled += 1
        return reconciled

    def request(
        self,
        project_id: UUID,
        request: CreateAgentProposalRequest,
        *,
        actor_id: str,
    ) -> AgentProposalResponse:
        revision = self.graphs.get_revision(project_id, request.base_graph_revision_id)
        graph = normalize_to_v3(revision.snapshot)
        with self.session_factory() as session:
            _, config = ControlConfigService._active_in_session(session)
            profile_id = json.loads(config.config_json).get(
                "active_canvas_agent_profile_revision_id"
            )
            profile = session.get(ModelProfileRevisionRow, profile_id)
            if profile is None:
                raise ModelProfileUnavailable()
            declaration = json.loads(profile.declaration_json)
        compiled = compile_agent_payload(
            graph=graph,
            node_id=request.node_id,
            scope=request.scope,
            instruction=request.instruction,
            model_id=declaration["model_id"],
            requested_categories=list(request.context_categories),
            allowed_categories=declaration["allowed_content_categories"],
        )
        endpoint = validate_endpoint(
            declaration["endpoint_url"],
            locality=declaration["locality"],
            resolver=(self.resolver if declaration["locality"] == "REMOTE_HTTPS" else None),
        )
        now = utc_now()
        proposal_id = uuid4()

        def add_proposal(
            session: Session,
            run_row: RunRequestRow,
            _plan: EgressPlanRow,
            profile_row: ModelProfileRevisionRow,
        ) -> None:
            row = AgentProposalRow(
                id=str(proposal_id),
                project_id=str(project_id),
                run_request_id=run_row.id,
                model_profile_revision_id=profile_row.id,
                branch_id=str(request.branch_id),
                branch_head_version=request.expected_branch_head_version,
                selection_version=request.expected_selection_version,
                node_id=str(request.node_id),
                scope_json=canonical_json(
                    {
                        "scope": request.scope.model_dump(mode="json"),
                        "node_id": str(request.node_id),
                        "new_branch_name": request.new_branch_name,
                    }
                ),
                scope_sha256=canonical_sha256(request.scope.model_dump(mode="json")),
                provider_payload_json=compiled.canonical_bytes.decode("utf-8"),
                provider_payload_sha256=compiled.payload_sha256,
                state="PENDING_APPROVAL",
                output_json=None,
                output_sha256=None,
                graph_change_set_id=None,
                failure_code=None,
                created_at=now,
                updated_at=now,
                dispatched_at=None,
                completed_at=None,
            )
            self.repository.add(session, row)
            session.flush()

        self.run_control.request_agent(
            project_id,
            branch_id=request.branch_id,
            expected_head_revision_id=request.expected_branch_head_revision_id,
            expected_head_version=request.expected_branch_head_version,
            expected_selection_version=request.expected_selection_version,
            scope=request.scope.model_dump(mode="json"),
            provider_payload=compiled.provider_payload,
            payload_sha256=compiled.payload_sha256,
            endpoint_policy=endpoint,
            preview=compiled.preview,
            actor_id=actor_id,
            after_create=add_proposal,
        )
        return self.get(project_id, proposal_id)

    def approve(
        self,
        project_id: UUID,
        proposal_id: UUID,
        *,
        expected_request_sha256: str,
        actor_id: str,
        decision: str = "APPROVED",
    ) -> AgentProposalResponse:
        with self.session_factory() as session:
            row = self.repository.get(session, str(proposal_id))
            if row is None or row.project_id != str(project_id):
                raise AgentProposalNotFound(str(proposal_id))
            request_id = UUID(row.run_request_id)
        self.run_control.approve_agent(
            project_id,
            request_id,
            expected_request_sha256=expected_request_sha256,
            actor_id=actor_id,
            decision=decision,
        )
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = self.repository.get(session, str(proposal_id))
                if row is None:
                    raise AgentProposalNotFound(str(proposal_id))
                row.state = "PENDING_EXECUTION" if decision == "APPROVED" else "REJECTED"
                row.updated_at = utc_now()
                if decision != "APPROVED":
                    row.completed_at = row.updated_at
                return self._response_in_session(session, row)

    def _fail(self, proposal_id: UUID, code: str) -> AgentProposalResponse:
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = self.repository.get(session, str(proposal_id))
                if row is None:
                    raise AgentProposalNotFound(str(proposal_id))
                row.state = "FAILED"
                row.failure_code = code
                row.output_json = None
                row.output_sha256 = None
                row.updated_at = utc_now()
                row.completed_at = row.updated_at
                self.events.append_in_session(
                    session,
                    project_id=UUID(row.project_id),
                    correlation_id=UUID(
                        session.get(RunRequestRow, row.run_request_id).correlation_id
                    ),
                    actor_kind="SYSTEM",
                    actor_id="agent-orchestrator",
                    subject_type="agent_proposal",
                    subject_id=UUID(row.id),
                    event_type="agent_proposal.failed",
                    payload={"code": code},
                )
                return self._response_in_session(session, row)

    def _succeed_in_session(
        self,
        session: Session,
        row: AgentProposalRow,
        run: RunRequestRow,
        output: AgentOutputDraft,
        *,
        graph_change_set_id: str | None,
    ) -> None:
        if row.state != "DISPATCHING":
            raise ValueError("Agent proposal state changed during dispatch")
        row.state = "SUCCEEDED"
        row.output_json = canonical_json(output.model_dump(mode="json"))
        row.output_sha256 = canonical_sha256(output.model_dump(mode="json"))
        row.graph_change_set_id = graph_change_set_id
        row.updated_at = utc_now()
        row.completed_at = row.updated_at
        run.state = "TERMINAL"
        self.events.append_in_session(
            session,
            project_id=UUID(row.project_id),
            correlation_id=UUID(run.correlation_id),
            actor_kind="SYSTEM",
            actor_id="agent-orchestrator",
            subject_type="agent_proposal",
            subject_id=UUID(row.id),
            event_type="agent_proposal.succeeded",
            payload={
                "output_sha256": row.output_sha256,
                "graph_change_set_id": graph_change_set_id,
            },
        )

    def execute(
        self,
        project_id: UUID,
        proposal_id: UUID,
        *,
        actor_id: str,
    ) -> AgentProposalResponse:
        with self.session_factory() as session:
            row = self.repository.get(session, str(proposal_id))
            if row is None or row.project_id != str(project_id):
                raise AgentProposalNotFound(str(proposal_id))
            if row.state != "PENDING_EXECUTION":
                raise AgentProposalStateConflict()
            run = session.get(RunRequestRow, row.run_request_id)
            plan = session.scalar(
                select(EgressPlanRow).where(EgressPlanRow.run_request_id == row.run_request_id)
            )
            profile = session.get(ModelProfileRevisionRow, row.model_profile_revision_id)
            if run is None or plan is None or profile is None:
                raise ValueError("Agent proposal storage is incomplete")
            declaration = json.loads(profile.declaration_json)
            request_id = UUID(run.id)
            endpoint = validate_endpoint(
                declaration["endpoint_url"],
                locality=declaration["locality"],
                resolver=(self.resolver if declaration["locality"] == "REMOTE_HTTPS" else None),
            )
            credential = resolve_runtime_credential(
                credential_kind=declaration["credential_kind"],
                canvas_agent_api_key=self.canvas_agent_api_key,
            )
            canonical_payload = row.provider_payload_json.encode("utf-8")
            scope_payload = json.loads(row.scope_json)
        self.run_control.consume_agent(
            project_id,
            request_id,
            endpoint_identity_sha256=endpoint.identity_sha256,
            credential_available=True,
            actor_id=actor_id,
        )
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = self.repository.get(session, str(proposal_id))
                if row is None or row.state != "PENDING_EXECUTION":
                    raise AgentProposalStateConflict()
                row.state = "DISPATCHING"
                row.dispatched_at = utc_now()
                row.updated_at = row.dispatched_at
                run = session.get(RunRequestRow, row.run_request_id)
                self.events.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=UUID(run.correlation_id),
                    actor_kind="SYSTEM",
                    actor_id="agent-orchestrator",
                    subject_type="agent_proposal",
                    subject_id=proposal_id,
                    event_type="agent_proposal.dispatched",
                    payload={"payload_sha256": row.provider_payload_sha256},
                )
        try:
            provider_result = self.adapter.send(
                endpoint_url=endpoint.canonical_url,
                canonical_payload=canonical_payload,
                credential=credential,
                timeout_seconds=declaration["timeout_seconds"],
                max_response_bytes=512_000,
            )
            output = _output_adapter.validate_json(provider_result.output_json)
            if isinstance(output, AgentGraphChangeSetDraftV1):
                base = self.graphs.get_revision(project_id, UUID(run.graph_revision_id))
                compiled_graph = compile_graph_draft(
                    normalize_to_v3(base.snapshot),
                    node_id=UUID(scope_payload["node_id"]),
                    scope=scope_payload["scope"],
                    draft=output,
                )
                def finish_with_graph(
                    session: Session,
                    graph_row: GraphChangeSetRow,
                ) -> None:
                    proposal_row = self.repository.get(session, str(proposal_id))
                    run_row = session.get(RunRequestRow, str(request_id))
                    if proposal_row is None or run_row is None:
                        raise ValueError("Agent proposal storage is incomplete")
                    self._succeed_in_session(
                        session,
                        proposal_row,
                        run_row,
                        output,
                        graph_change_set_id=graph_row.id,
                    )

                self.graph_changes.request(
                    project_id,
                    CreateGraphChangeSetRequest.model_validate(
                        {
                            "base_graph_revision_id": run.graph_revision_id,
                            "client_mutation_id": str(uuid4()),
                            "operations": [
                                {
                                    "op": "replace_snapshot",
                                    "snapshot": compiled_graph.model_dump(mode="json"),
                                }
                            ],
                            "application_target": {
                                "mode": "NEW_BRANCH",
                                "base_branch_id": run.branch_id,
                                "branch_name": scope_payload["new_branch_name"],
                                "expected_branch_head_revision_id": run.graph_revision_id,
                                "expected_branch_head_version": run.branch_head_version,
                                "expected_selection_version": run.selection_version,
                            },
                        }
                    ),
                    actor_id="canvas-agent",
                    actor_kind="AI_CANVAS",
                    after_create=finish_with_graph,
                )
                return self.get(project_id, proposal_id)
        except (ModelProviderError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return self._fail(proposal_id, "AGENT_OUTPUT_OR_PROVIDER_INVALID")

        with self._write_gate:
            with self.session_factory.begin() as session:
                row = self.repository.get(session, str(proposal_id))
                if row is None or row.state != "DISPATCHING":
                    raise ValueError("Agent proposal state changed during dispatch")
                run = session.get(RunRequestRow, row.run_request_id)
                if run is None:
                    raise ValueError("Agent proposal storage is incomplete")
                self._succeed_in_session(
                    session, row, run, output, graph_change_set_id=None
                )
                return self._response_in_session(session, row)
