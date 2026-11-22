import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.errors import (
    ApprovalSubjectIntegrityError,
    ApprovalSubjectStale,
    ApprovalRequired,
    ChangeSetDomainMismatch,
    ConfigChangeSetNotFound,
    ConfigChangeSetBaseRevisionStale,
)
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts.config_changesets import (
    ConfigChangeSetResponse,
    ControlConfigRevisionResponse,
    CreateConfigChangeSetRequest,
)
from ai_canvas.domain.control.canonical import canonical_json, canonical_sha256
from ai_canvas.infrastructure.db.models import (
    ApprovalDecisionRow,
    ConfigChangeSetRow,
    ControlConfigDocumentRow,
    ControlConfigRevisionRow,
    GraphChangeSetRow,
    ModelProfileRevisionRow,
    ModelProfileRow,
    ProviderRow,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class ControlConfigService:
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
    def _revision_response(row: ControlConfigRevisionRow) -> ControlConfigRevisionResponse:
        config = json.loads(row.config_json)
        return ControlConfigRevisionResponse(
            id=UUID(row.id),
            sequence=row.sequence,
            dry_run_max_tasks=config["dry_run_max_tasks"],
            active_canvas_agent_profile_revision_id=config.get(
                "active_canvas_agent_profile_revision_id"
            ),
            active_prompt_editor_profile_revision_id=config.get(
                "active_prompt_editor_profile_revision_id"
            ),
            config_sha256=row.config_sha256,
            created_at=as_utc(row.created_at),
        )

    @staticmethod
    def _changeset_response(row: ConfigChangeSetRow) -> ConfigChangeSetResponse:
        return ConfigChangeSetResponse(
            id=UUID(row.id),
            audit_project_id=UUID(row.audit_project_id),
            correlation_id=UUID(row.correlation_id),
            base_config_revision_id=UUID(row.base_config_revision_id),
            changeset_sha256=row.changeset_sha256,
            operations=json.loads(row.operations_json),
            risk_summary=json.loads(row.risk_json),
            state=row.state,
            created_at=as_utc(row.created_at),
        )

    @staticmethod
    def _changeset_sha256(
        *,
        audit_project_id: str,
        base_config_revision_id: str,
        operations: list[dict],
        risk_summary: dict,
    ) -> str:
        return canonical_sha256(
            {
                "kind": "config_change_set",
                "schema_version": 1,
                "audit_project_id": audit_project_id,
                "base_config_revision_id": base_config_revision_id,
                "operations": operations,
                "risk_summary": risk_summary,
            }
        )

    @classmethod
    def _validated_subject(cls, row: ConfigChangeSetRow):
        try:
            operations = json.loads(row.operations_json)
            request = CreateConfigChangeSetRequest.model_validate(
                {
                    "kind": "config_change_set",
                    "schema_version": 1,
                    "audit_project_id": row.audit_project_id,
                    "base_config_revision_id": row.base_config_revision_id,
                    "operations": operations,
                }
            )
            normalized_operations = [operation.model_dump(mode="json") for operation in request.operations]
            risk_summary = json.loads(row.risk_json)
            changeset_sha256 = cls._changeset_sha256(
                audit_project_id=row.audit_project_id,
                base_config_revision_id=row.base_config_revision_id,
                operations=normalized_operations,
                risk_summary=risk_summary,
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if (
            row.operations_json != canonical_json(normalized_operations)
            or row.changeset_sha256 != changeset_sha256
        ):
            return None
        return normalized_operations, risk_summary

    def _reject_integrity_in_session(
        self,
        session: Session,
        row: ConfigChangeSetRow,
    ) -> None:
        row.state = "REJECTED"
        self.events.append_in_session(
            session,
            project_id=UUID(row.audit_project_id),
            correlation_id=UUID(row.correlation_id),
            actor_kind="SYSTEM",
            actor_id="control-plane",
            subject_type="config_change_set",
            subject_id=UUID(row.id),
            event_type="config_change_set.rejected",
            payload={"reason": "SUBJECT_INTEGRITY_MISMATCH"},
        )

    def _record_rejection_in_session(
        self,
        session: Session,
        row: ConfigChangeSetRow,
        *,
        actor_kind: str,
        actor_id: str,
        reason: str,
    ) -> None:
        row.state = "REJECTED"
        self.events.append_in_session(
            session,
            project_id=UUID(row.audit_project_id),
            correlation_id=UUID(row.correlation_id),
            actor_kind=actor_kind,
            actor_id=actor_id,
            subject_type="config_change_set",
            subject_id=UUID(row.id),
            event_type="config_change_set.rejected",
            payload={"reason": reason},
        )

    @staticmethod
    def _active_in_session(session: Session) -> tuple[ControlConfigDocumentRow, ControlConfigRevisionRow]:
        document = session.scalar(select(ControlConfigDocumentRow))
        if document is None or document.active_revision_id is None:
            raise RuntimeError("F1 control config initialization is missing")
        revision = session.get(ControlConfigRevisionRow, document.active_revision_id)
        if revision is None or revision.document_id != document.id:
            raise RuntimeError("F1 control config active revision is invalid")
        return document, revision

    def get_active(self) -> ControlConfigRevisionResponse:
        with self.session_factory() as session:
            _, revision = self._active_in_session(session)
            return self._revision_response(revision)

    def request(
        self,
        request: CreateConfigChangeSetRequest,
        *,
        actor_id: str,
    ) -> ConfigChangeSetResponse:
        with self._write_gate:
            with self.session_factory.begin() as session:
                _, active = self._active_in_session(session)
                if active.id != str(request.base_config_revision_id):
                    raise ConfigChangeSetBaseRevisionStale()
                operations = [operation.model_dump(mode="json") for operation in request.operations]
                operation = request.operations[0]
                if operation.op == "replace":
                    risk = {"classes": ["CONTROL_CONFIG_WRITE"]}
                    event_payload = {
                        "changeset_sha256": None,
                        "dry_run_max_tasks": operation.value,
                    }
                else:
                    expected_declaration_sha256 = canonical_sha256(
                        operation.revision.model_dump(mode="json")
                    )
                    if operation.declaration_sha256 != expected_declaration_sha256:
                        raise ApprovalSubjectIntegrityError(
                            "model_profile_revision", str(operation.model_profile_revision_id)
                        )
                    risk = {"classes": ["CONTROL_CONFIG_WRITE", "MODEL_EGRESS"]}
                    event_payload = {
                        "changeset_sha256": None,
                        "model_profile_revision_id": str(
                            operation.model_profile_revision_id
                        ),
                        "declaration_sha256": operation.declaration_sha256,
                    }
                changeset_sha256 = self._changeset_sha256(
                    audit_project_id=str(request.audit_project_id),
                    base_config_revision_id=str(request.base_config_revision_id),
                    operations=operations,
                    risk_summary=risk,
                )
                row = ConfigChangeSetRow(
                    id=str(uuid4()),
                    audit_project_id=str(request.audit_project_id),
                    correlation_id=str(uuid4()),
                    base_config_revision_id=str(request.base_config_revision_id),
                    operations_json=canonical_json(operations),
                    risk_json=canonical_json(risk),
                    changeset_sha256=changeset_sha256,
                    state="PENDING_APPROVAL",
                    approval_id=None,
                    applied_config_revision_id=None,
                    created_at=utc_now(),
                    applied_at=None,
                )
                session.add(row)
                session.flush()
                self.events.append_in_session(
                    session,
                    project_id=request.audit_project_id,
                    correlation_id=UUID(row.correlation_id),
                    actor_kind="HUMAN",
                    actor_id=actor_id,
                    subject_type="config_change_set",
                    subject_id=UUID(row.id),
                    event_type="config_change_set.requested",
                    payload={**event_payload, "changeset_sha256": changeset_sha256},
                )
                return self._changeset_response(row)

    def approve(
        self,
        change_set_id: UUID,
        *,
        expected_changeset_sha256: str,
        actor_id: str,
        decision: str = "APPROVED",
    ) -> ConfigChangeSetResponse:
        rejection: ApprovalSubjectIntegrityError | None = None
        response: ConfigChangeSetResponse | None = None
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = session.get(ConfigChangeSetRow, str(change_set_id))
                if row is None:
                    if session.get(GraphChangeSetRow, str(change_set_id)) is not None:
                        raise ChangeSetDomainMismatch(str(change_set_id))
                    raise ConfigChangeSetNotFound(str(change_set_id))
                validated = self._validated_subject(row)
                if validated is None:
                    self._reject_integrity_in_session(session, row)
                    rejection = ApprovalSubjectIntegrityError("config_change_set", row.id)
                elif row.state != "PENDING_APPROVAL" or row.changeset_sha256 != expected_changeset_sha256:
                    raise ApprovalSubjectStale
                else:
                    _, risk_summary = validated
                    approval = ApprovalDecisionRow(
                        id=str(uuid4()),
                        project_id=row.audit_project_id,
                        correlation_id=row.correlation_id,
                        subject_type="config_change_set",
                        subject_id=row.id,
                        subject_sha256=row.changeset_sha256,
                        risk_sha256=canonical_sha256(risk_summary),
                        decision=decision,
                        actor_id=actor_id,
                        created_at=utc_now(),
                    )
                    session.add(approval)
                    row.approval_id = approval.id
                    row.state = "APPROVED" if decision == "APPROVED" else "REJECTED"
                    self.events.append_in_session(
                        session,
                        project_id=UUID(row.audit_project_id),
                        correlation_id=UUID(row.correlation_id),
                        actor_kind="HUMAN",
                        actor_id=actor_id,
                        subject_type="config_change_set",
                        subject_id=UUID(row.id),
                        event_type="approval.recorded",
                        payload={
                            "approval_id": approval.id,
                            "decision": decision,
                            "subject_sha256": row.changeset_sha256,
                        },
                    )
                    if decision == "REJECTED":
                        self._record_rejection_in_session(
                            session,
                            row,
                            actor_kind="HUMAN",
                            actor_id=actor_id,
                            reason="USER_REJECTED",
                        )
                    response = self._changeset_response(row)
        if rejection is not None:
            raise rejection
        if response is None:
            raise RuntimeError("config change set approval produced no response")
        return response

    def apply(
        self,
        change_set_id: UUID,
        *,
        expected_changeset_sha256: str,
        actor_id: str,
    ) -> ConfigChangeSetResponse:
        rejection: ApprovalSubjectIntegrityError | ConfigChangeSetBaseRevisionStale | None = None
        response: ConfigChangeSetResponse | None = None
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = session.get(ConfigChangeSetRow, str(change_set_id))
                if row is None:
                    if session.get(GraphChangeSetRow, str(change_set_id)) is not None:
                        raise ChangeSetDomainMismatch(str(change_set_id))
                    raise ConfigChangeSetNotFound(str(change_set_id))
                if row.state == "PENDING_APPROVAL" or row.approval_id is None:
                    raise ApprovalRequired
                if row.state != "APPROVED" or row.changeset_sha256 != expected_changeset_sha256:
                    raise ApprovalSubjectStale
                validated = self._validated_subject(row)
                approval = (
                    session.get(ApprovalDecisionRow, row.approval_id) if row.approval_id is not None else None
                )
                risk_summary = validated[1] if validated is not None else None
                approval_is_bound = (
                    (
                        approval is not None
                        and approval.subject_type == "config_change_set"
                        and approval.subject_id == row.id
                        and approval.subject_sha256 == row.changeset_sha256
                        and approval.risk_sha256 == canonical_sha256(risk_summary)
                        and approval.decision == "APPROVED"
                    )
                    if risk_summary is not None
                    else False
                )
                if validated is None or not approval_is_bound:
                    self._reject_integrity_in_session(session, row)
                    rejection = ApprovalSubjectIntegrityError("config_change_set", row.id)
                else:
                    document, active = self._active_in_session(session)
                if rejection is None and active.id != row.base_config_revision_id:
                    self._record_rejection_in_session(
                        session,
                        row,
                        actor_kind="SYSTEM",
                        actor_id="control-plane",
                        reason="BASE_REVISION_STALE",
                    )
                    rejection = ConfigChangeSetBaseRevisionStale()
                if rejection is None:
                    operations = validated[0]
                    config = json.loads(active.config_json)
                    operation = operations[0]
                    if operation["op"] == "replace":
                        config["dry_run_max_tasks"] = operation["value"]
                    else:
                        provider = session.scalar(
                            select(ProviderRow).where(
                                ProviderRow.provider_key
                                == operation["provider"]["provider_key"]
                            )
                        )
                        if provider is None:
                            provider = ProviderRow(
                                id=operation["provider_id"],
                                provider_key=operation["provider"]["provider_key"],
                                adapter_kind=operation["provider"]["adapter_kind"],
                                created_at=utc_now(),
                            )
                            session.add(provider)
                            session.flush()
                        elif (
                            provider.id != operation["provider_id"]
                            or provider.adapter_kind
                            != operation["provider"]["adapter_kind"]
                        ):
                            self._reject_integrity_in_session(session, row)
                            rejection = ApprovalSubjectIntegrityError(
                                "provider", provider.id
                            )

                        profile = session.scalar(
                            select(ModelProfileRow).where(
                                ModelProfileRow.profile_key
                                == operation["profile_key"]
                            )
                        )
                        if rejection is None and profile is None:
                            profile = ModelProfileRow(
                                id=operation["model_profile_id"],
                                profile_key=operation["profile_key"],
                                role=operation["revision"]["role"],
                                created_at=utc_now(),
                            )
                            session.add(profile)
                            session.flush()
                        elif rejection is None and (
                            profile.id != operation["model_profile_id"]
                            or profile.role != operation["revision"]["role"]
                        ):
                            self._reject_integrity_in_session(session, row)
                            rejection = ApprovalSubjectIntegrityError(
                                "model_profile", profile.id
                            )

                        declaration = operation["revision"]
                        declaration_sha256 = canonical_sha256(declaration)
                        existing_revision = session.get(
                            ModelProfileRevisionRow,
                            operation["model_profile_revision_id"],
                        )
                        if rejection is None and (
                            declaration_sha256 != operation["declaration_sha256"]
                            or existing_revision is not None
                        ):
                            self._reject_integrity_in_session(session, row)
                            rejection = ApprovalSubjectIntegrityError(
                                "model_profile_revision",
                                operation["model_profile_revision_id"],
                            )
                        if rejection is None:
                            latest_sequence = session.scalar(
                                select(ModelProfileRevisionRow.sequence)
                                .where(
                                    ModelProfileRevisionRow.model_profile_id
                                    == operation["model_profile_id"]
                                )
                                .order_by(ModelProfileRevisionRow.sequence.desc())
                                .limit(1)
                            )
                            model_revision = ModelProfileRevisionRow(
                                id=operation["model_profile_revision_id"],
                                model_profile_id=operation["model_profile_id"],
                                provider_id=operation["provider_id"],
                                sequence=1
                                if latest_sequence is None
                                else latest_sequence + 1,
                                declaration_json=canonical_json(declaration),
                                declaration_sha256=declaration_sha256,
                                created_at=utc_now(),
                            )
                            session.add(model_revision)
                            active_key = (
                                "active_canvas_agent_profile_revision_id"
                                if operation["revision"]["role"] == "CANVAS_AGENT"
                                else "active_prompt_editor_profile_revision_id"
                            )
                            config[active_key] = model_revision.id
                            self.events.append_in_session(
                                session,
                                project_id=UUID(row.audit_project_id),
                                correlation_id=UUID(row.correlation_id),
                                actor_kind="HUMAN",
                                actor_id=actor_id,
                                subject_type="model_profile_revision",
                                subject_id=UUID(model_revision.id),
                                event_type="model_profile.revision_created",
                                payload={
                                    "model_profile_revision_id": model_revision.id,
                                    "declaration_sha256": declaration_sha256,
                                },
                            )
                    if rejection is not None:
                        config = None
                if rejection is None:
                    revision = ControlConfigRevisionRow(
                        id=str(uuid4()),
                        document_id=document.id,
                        parent_revision_id=active.id,
                        sequence=active.sequence + 1,
                        config_json=canonical_json(config),
                        config_sha256=canonical_sha256(config),
                        created_at=utc_now(),
                    )
                    session.add(revision)
                    session.flush()
                    document.active_revision_id = revision.id
                    row.state = "APPLIED"
                    row.applied_config_revision_id = revision.id
                    row.applied_at = utc_now()
                    self.events.append_in_session(
                        session,
                        project_id=UUID(row.audit_project_id),
                        correlation_id=UUID(row.correlation_id),
                        actor_kind="HUMAN",
                        actor_id=actor_id,
                        subject_type="config_change_set",
                        subject_id=UUID(row.id),
                        event_type="config_change_set.applied",
                        payload={"changeset_sha256": row.changeset_sha256, "config_revision_id": revision.id},
                    )
                    response = self._changeset_response(row)
        if rejection is not None:
            raise rejection
        if response is None:
            raise RuntimeError("config change set apply produced no response")
        return response
