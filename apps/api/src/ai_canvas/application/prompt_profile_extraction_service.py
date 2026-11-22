import json
import socket
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.control_config_service import ControlConfigService
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.resource_catalog_service import ResourceCatalogService
from ai_canvas.application.run_control_service import RunControlService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts.graph import PromptNodeV3
from ai_canvas.contracts.prompt_profile_extractions import (
    CreatePromptProfileExtractionRequest,
    MaterializePromptProfileRequest,
    PromptProfileDraftV1,
    PromptProfileExtractionResponse,
)
from ai_canvas.contracts.resources import (
    CreateResourceRequest,
    CreateResourceRevisionRequest,
)
from ai_canvas.domain.control.canonical import canonical_json, canonical_sha256
from ai_canvas.domain.graph.snapshots import normalize_to_v3
from ai_canvas.domain.model_endpoints.policy import validate_endpoint
from ai_canvas.domain.prompt_profiles.extraction_payload import (
    compile_prompt_profile_extraction_payload,
)
from ai_canvas.domain.prompt_profiles.rule_program import (
    compile_profile_draft,
    rule_code_set_sha256,
)
from ai_canvas.infrastructure.db.models import (
    EgressPlanRow,
    ModelProfileRevisionRow,
    PromptProfileCertificationRow,
    PromptProfileExtractionRow,
    ResourceRevisionRow,
    RunRequestRow,
)
from ai_canvas.infrastructure.db.prompt_batch_repository import PromptBatchRepository
from ai_canvas.infrastructure.model_providers.credentials import resolve_runtime_credential
from ai_canvas.infrastructure.model_providers.openai_compatible import (
    ModelProviderError,
    OpenAICompatibleChatJsonAdapter,
)


class PromptProfileExtractionNotFound(LookupError):
    pass


class PromptProfileExtractionStateConflict(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_resolver(host: str) -> list[str]:
    return sorted({item[4][0] for item in socket.getaddrinfo(host, None)})


class PromptProfileExtractionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        graphs: GraphService,
        run_control: RunControlService,
        resources: ResourceCatalogService,
        events: EventSpineService,
        *,
        adapter: OpenAICompatibleChatJsonAdapter | None = None,
        prompt_editor_api_key: SecretStr | None = None,
        resolver: Callable[[str], list[str]] = default_resolver,
        repository: PromptBatchRepository | None = None,
        write_gate: WriteGate | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.graphs = graphs
        self.run_control = run_control
        self.resources = resources
        self.events = events
        self.adapter = adapter or OpenAICompatibleChatJsonAdapter()
        self.prompt_editor_api_key = prompt_editor_api_key
        self.resolver = resolver
        self.repository = repository or PromptBatchRepository()
        self._write_gate = write_gate or WriteGate()

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )

    def _response_in_session(
        self, session: Session, row: PromptProfileExtractionRow
    ) -> PromptProfileExtractionResponse:
        run = session.get(RunRequestRow, row.run_request_id)
        plan = session.scalar(
            select(EgressPlanRow).where(EgressPlanRow.run_request_id == row.run_request_id)
        )
        profile = session.get(ModelProfileRevisionRow, row.model_profile_revision_id)
        if run is None or plan is None or profile is None:
            raise ValueError("prompt profile extraction storage is incomplete")
        output = (
            None
            if row.output_json is None
            else PromptProfileDraftV1.model_validate_json(row.output_json)
        )
        compiled_profile = (
            None
            if output is None
            else compile_profile_draft(
                output,
                source_example_sha256=row.source_document_sha256,
            )
        )
        return PromptProfileExtractionResponse(
            id=UUID(row.id),
            project_id=UUID(row.project_id),
            run_request=RunControlService._agent_request_response(run, plan, profile),
            source_node_id=UUID(row.source_node_id),
            source_document_sha256=row.source_document_sha256,
            state=row.state,
            output=output,
            output_sha256=row.output_sha256,
            compiled_profile=compiled_profile,
            materialized_resource_revision_id=(
                None
                if row.materialized_resource_revision_id is None
                else UUID(row.materialized_resource_revision_id)
            ),
            failure_code=row.failure_code,
            created_at=self._as_utc(row.created_at),
            updated_at=self._as_utc(row.updated_at),
        )

    def get(
        self, project_id: UUID, extraction_id: UUID
    ) -> PromptProfileExtractionResponse:
        with self.session_factory() as session:
            row = self.repository.extraction(session, str(extraction_id))
            if row is None or row.project_id != str(project_id):
                raise PromptProfileExtractionNotFound(str(extraction_id))
            return self._response_in_session(session, row)

    def request(
        self,
        project_id: UUID,
        request: CreatePromptProfileExtractionRequest,
        *,
        actor_id: str,
    ) -> PromptProfileExtractionResponse:
        revision = self.graphs.get_revision(project_id, request.base_graph_revision_id)
        graph = normalize_to_v3(revision.snapshot)
        source = next(
            (
                node
                for node in graph.nodes
                if node.id == request.source_node_id
                and isinstance(node, PromptNodeV3)
            ),
            None,
        )
        if source is None:
            raise ValueError("source node must be a PromptNode in the frozen graph")
        with self.session_factory() as session:
            _, config = ControlConfigService._active_in_session(session)
            profile_id = json.loads(config.config_json).get(
                "active_prompt_editor_profile_revision_id"
            )
            profile = session.get(ModelProfileRevisionRow, profile_id)
            if profile is None:
                raise ValueError("active PROMPT_EDITOR profile is unavailable")
            declaration = json.loads(profile.declaration_json)
        if "PROMPT_PROFILE_DRAFT" not in declaration["tool_permissions"]:
            raise ValueError("active PROMPT_EDITOR cannot produce profile drafts")
        compiled = compile_prompt_profile_extraction_payload(
            source_node_id=request.source_node_id,
            document=source.data.document,
            instruction=request.instruction,
            model_id=declaration["model_id"],
            max_example_characters=20_000,
        )
        endpoint = validate_endpoint(
            declaration["endpoint_url"],
            locality=declaration["locality"],
            resolver=(self.resolver if declaration["locality"] == "REMOTE_HTTPS" else None),
        )
        extraction_id = uuid4()
        now = utc_now()

        def add_extraction(
            session: Session,
            run: RunRequestRow,
            _plan: EgressPlanRow,
            profile_row: ModelProfileRevisionRow,
        ) -> None:
            row = PromptProfileExtractionRow(
                id=str(extraction_id),
                project_id=str(project_id),
                correlation_id=run.correlation_id,
                branch_id=str(request.branch_id),
                branch_head_version=request.expected_branch_head_version,
                selection_version=request.expected_selection_version,
                graph_revision_id=str(request.base_graph_revision_id),
                graph_revision_sha256=run.graph_revision_sha256,
                config_revision_id=run.config_revision_id,
                config_revision_sha256=run.config_revision_sha256,
                model_profile_revision_id=profile_row.id,
                model_profile_revision_sha256=profile_row.declaration_sha256,
                source_node_id=str(request.source_node_id),
                source_document_sha256=compiled.source_document_sha256,
                provider_payload_json=compiled.canonical_bytes.decode("utf-8"),
                provider_payload_sha256=compiled.payload_sha256,
                run_request_id=run.id,
                state="PENDING_APPROVAL",
                output_json=None,
                output_sha256=None,
                materialized_resource_revision_id=None,
                failure_code=None,
                created_at=now,
                updated_at=now,
                dispatched_at=None,
                completed_at=None,
            )
            self.repository.add_extraction(session, row)
            self.events.append_in_session(
                session,
                project_id=project_id,
                correlation_id=UUID(run.correlation_id),
                actor_kind="HUMAN",
                actor_id=actor_id,
                subject_type="prompt_profile_extraction",
                subject_id=extraction_id,
                event_type="prompt_profile_extraction.requested",
                payload={
                    "source_document_sha256": compiled.source_document_sha256,
                    "provider_payload_sha256": compiled.payload_sha256,
                    "request_sha256": run.request_sha256,
                },
            )

        self.run_control.request_agent(
            project_id,
            branch_id=request.branch_id,
            expected_head_revision_id=request.expected_branch_head_revision_id,
            expected_head_version=request.expected_branch_head_version,
            expected_selection_version=request.expected_selection_version,
            scope={
                "kind": "PROMPT_PROFILE_EXTRACTION",
                "source_node_id": str(request.source_node_id),
                "source_document_sha256": compiled.source_document_sha256,
            },
            provider_payload=compiled.provider_payload,
            payload_sha256=compiled.payload_sha256,
            endpoint_policy=endpoint,
            preview=compiled.preview,
            actor_id=actor_id,
            operation_kind="PROMPT_PROFILE_EXTRACTION",
            profile_role="PROMPT_EDITOR",
            after_create=add_extraction,
        )
        return self.get(project_id, extraction_id)

    def approve(
        self,
        project_id: UUID,
        extraction_id: UUID,
        *,
        expected_request_sha256: str,
        actor_id: str,
        decision: str = "APPROVED",
    ) -> PromptProfileExtractionResponse:
        current = self.get(project_id, extraction_id)
        self.run_control.approve_agent(
            project_id,
            current.run_request.id,
            expected_request_sha256=expected_request_sha256,
            actor_id=actor_id,
            decision=decision,
            issue_authorization=False,
        )
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = self.repository.extraction(session, str(extraction_id))
                if row is None or row.state != "PENDING_APPROVAL":
                    raise PromptProfileExtractionStateConflict()
                row.state = (
                    "PENDING_EXECUTION" if decision == "APPROVED" else "REJECTED"
                )
                row.updated_at = utc_now()
                if decision != "APPROVED":
                    row.completed_at = row.updated_at
                self.events.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=UUID(row.correlation_id),
                    actor_kind="HUMAN",
                    actor_id=actor_id,
                    subject_type="prompt_profile_extraction",
                    subject_id=extraction_id,
                    event_type=(
                        "prompt_profile_extraction.approved"
                        if decision == "APPROVED"
                        else "prompt_profile_extraction.rejected"
                    ),
                    payload={"request_sha256": expected_request_sha256},
                )
        return self.get(project_id, extraction_id)

    def _fail(self, project_id: UUID, extraction_id: UUID) -> PromptProfileExtractionResponse:
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = self.repository.extraction(session, str(extraction_id))
                if row is None:
                    raise PromptProfileExtractionNotFound(str(extraction_id))
                row.state = "FAILED"
                row.failure_code = "AGENT_OUTPUT_OR_PROVIDER_INVALID"
                row.output_json = None
                row.output_sha256 = None
                row.updated_at = utc_now()
                row.completed_at = row.updated_at
                run = session.get(RunRequestRow, row.run_request_id)
                if run is not None:
                    run.state = "TERMINAL"
                self.events.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=UUID(row.correlation_id),
                    actor_kind="SYSTEM",
                    actor_id="prompt-profile-orchestrator",
                    subject_type="prompt_profile_extraction",
                    subject_id=extraction_id,
                    event_type="prompt_profile_extraction.failed",
                    payload={"code": row.failure_code},
                )
        return self.get(project_id, extraction_id)

    def execute(
        self, project_id: UUID, extraction_id: UUID, *, actor_id: str
    ) -> PromptProfileExtractionResponse:
        with self.session_factory() as session:
            row = self.repository.extraction(session, str(extraction_id))
            if row is None or row.project_id != str(project_id):
                raise PromptProfileExtractionNotFound(str(extraction_id))
            if row.state != "PENDING_EXECUTION":
                raise PromptProfileExtractionStateConflict()
            run = session.get(RunRequestRow, row.run_request_id)
            profile = session.get(ModelProfileRevisionRow, row.model_profile_revision_id)
            if run is None or profile is None:
                raise ValueError("prompt profile extraction storage is incomplete")
            declaration = json.loads(profile.declaration_json)
            endpoint = validate_endpoint(
                declaration["endpoint_url"],
                locality=declaration["locality"],
                resolver=(self.resolver if declaration["locality"] == "REMOTE_HTTPS" else None),
            )
            credential = resolve_runtime_credential(
                credential_kind=declaration["credential_kind"],
                canvas_agent_api_key=self.prompt_editor_api_key,
            )
            payload = row.provider_payload_json.encode("utf-8")
            request_id = UUID(run.id)
        self.run_control.issue_agent_authorization(project_id, request_id)
        self.run_control.consume_agent(
            project_id,
            request_id,
            endpoint_identity_sha256=endpoint.identity_sha256,
            credential_available=True,
            actor_id=actor_id,
        )
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = self.repository.extraction(session, str(extraction_id))
                if row is None or row.state != "PENDING_EXECUTION":
                    raise PromptProfileExtractionStateConflict()
                row.state = "DISPATCHING"
                row.dispatched_at = utc_now()
                row.updated_at = row.dispatched_at
                self.events.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=UUID(row.correlation_id),
                    actor_kind="SYSTEM",
                    actor_id="prompt-profile-orchestrator",
                    subject_type="prompt_profile_extraction",
                    subject_id=extraction_id,
                    event_type="prompt_profile_extraction.dispatched",
                    payload={"provider_payload_sha256": row.provider_payload_sha256},
                )
        try:
            result = self.adapter.send(
                endpoint_url=endpoint.canonical_url,
                canonical_payload=payload,
                credential=credential,
                timeout_seconds=declaration["timeout_seconds"],
                max_response_bytes=512_000,
            )
            output = PromptProfileDraftV1.model_validate_json(result.output_json)
        except (ModelProviderError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return self._fail(project_id, extraction_id)
        output_body = output.model_dump(mode="json")
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = self.repository.extraction(session, str(extraction_id))
                if row is None or row.state != "DISPATCHING":
                    raise PromptProfileExtractionStateConflict()
                row.state = "SUCCEEDED"
                row.output_json = canonical_json(output_body)
                row.output_sha256 = canonical_sha256(output_body)
                row.updated_at = utc_now()
                row.completed_at = row.updated_at
                run = session.get(RunRequestRow, row.run_request_id)
                if run is not None:
                    run.state = "TERMINAL"
                self.events.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=UUID(row.correlation_id),
                    actor_kind="SYSTEM",
                    actor_id="prompt-profile-orchestrator",
                    subject_type="prompt_profile_extraction",
                    subject_id=extraction_id,
                    event_type="prompt_profile_extraction.succeeded",
                    payload={"output_sha256": row.output_sha256},
                )
        return self.get(project_id, extraction_id)

    def materialize(
        self,
        project_id: UUID,
        extraction_id: UUID,
        request: MaterializePromptProfileRequest,
        *,
        actor_id: str,
    ) -> PromptProfileExtractionResponse:
        current = self.get(project_id, extraction_id)
        if (
            current.state != "SUCCEEDED"
            or current.output is None
            or current.output_sha256 != request.expected_output_sha256
        ):
            raise PromptProfileExtractionStateConflict()
        payload = compile_profile_draft(
            current.output,
            source_example_sha256=current.source_document_sha256,
        )

        def certify(session: Session, revision: ResourceRevisionRow) -> None:
            row = self.repository.extraction(session, str(extraction_id))
            if (
                row is None
                or row.state != "SUCCEEDED"
                or row.output_sha256 != request.expected_output_sha256
            ):
                raise PromptProfileExtractionStateConflict()
            certification = PromptProfileCertificationRow(
                id=str(uuid4()),
                resource_revision_id=revision.id,
                source_example_sha256=row.source_document_sha256,
                extractor_schema_version=1,
                rule_program_version=1,
                rule_code_set_sha256=rule_code_set_sha256(),
                profile_payload_sha256=revision.payload_sha256,
                extraction_id=row.id,
                created_at=utc_now(),
            )
            self.repository.add_certification(session, certification)
            row.state = "MATERIALIZED"
            row.materialized_resource_revision_id = revision.id
            row.updated_at = utc_now()
            self.events.append_in_session(
                session,
                project_id=project_id,
                correlation_id=UUID(row.correlation_id),
                actor_kind="HUMAN",
                actor_id=actor_id,
                subject_type="prompt_profile_extraction",
                subject_id=extraction_id,
                event_type="prompt_profile_extraction.materialized",
                payload={
                    "resource_revision_id": revision.id,
                    "profile_payload_sha256": revision.payload_sha256,
                    "rule_code_set_sha256": certification.rule_code_set_sha256,
                },
            )

        if request.resource_id is None:
            self.resources.create_resource(
                project_id,
                CreateResourceRequest(
                    name=request.name,
                    payload=payload,
                    tags=request.tags,
                ),
                actor_id=actor_id,
                after_create=certify,
            )
        else:
            self.resources.create_revision(
                project_id,
                request.resource_id,
                CreateResourceRevisionRequest(
                    base_revision_id=request.base_resource_revision_id,
                    payload=payload,
                    tags=request.tags,
                ),
                actor_id=actor_id,
                after_create=certify,
            )
        return self.get(project_id, extraction_id)

    def reconcile_dispatching(self) -> int:
        with self._write_gate:
            with self.session_factory.begin() as session:
                rows = list(
                    session.scalars(
                        select(PromptProfileExtractionRow).where(
                            PromptProfileExtractionRow.state == "DISPATCHING"
                        )
                    )
                )
                now = utc_now()
                for row in rows:
                    row.state = "UNKNOWN_TERMINAL"
                    row.failure_code = "RESTART_DURING_DISPATCH"
                    row.updated_at = now
                    row.completed_at = now
                    run = session.get(RunRequestRow, row.run_request_id)
                    if run is not None:
                        run.state = "TERMINAL"
                    self.events.append_in_session(
                        session,
                        project_id=UUID(row.project_id),
                        correlation_id=UUID(row.correlation_id),
                        actor_kind="SYSTEM",
                        actor_id="prompt-profile-orchestrator",
                        subject_type="prompt_profile_extraction",
                        subject_id=UUID(row.id),
                        event_type="prompt_profile_extraction.unknown_terminal",
                        payload={"code": row.failure_code},
                    )
                return len(rows)
