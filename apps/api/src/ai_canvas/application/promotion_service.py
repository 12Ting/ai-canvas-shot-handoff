import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.errors import (
    ApprovalRequired,
    ApprovalSubjectIntegrityError,
    ClientMutationReuse,
    GraphBranchNotFound,
    ProjectNotFound,
    PromotionNotFound,
    PromotionStale,
    PromotionUndoStale,
    StoredGraphIntegrityError,
)
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts.branches import (
    MaterializationSummary,
    PromotionRequest,
    PromotionResponse,
)
from ai_canvas.domain.control.canonical import canonical_json, canonical_sha256
from ai_canvas.infrastructure.db.branch_repository import BranchRepository
from ai_canvas.infrastructure.db.models import (
    ApprovalDecisionRow,
    GraphDocumentRow,
    GraphPromotionRow,
)
from ai_canvas.infrastructure.db.repositories import GraphRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def promotion_response(row: GraphPromotionRow) -> PromotionResponse:
    return PromotionResponse(
        id=UUID(row.id),
        project_id=UUID(row.project_id),
        correlation_id=UUID(row.correlation_id),
        client_mutation_id=UUID(row.client_mutation_id),
        kind=row.kind,
        source_branch_id=UUID(row.source_branch_id),
        source_graph_revision_id=UUID(row.source_graph_revision_id),
        source_branch_head_version=row.source_branch_head_version,
        target_branch_id=UUID(row.target_branch_id),
        target_graph_revision_id=UUID(row.target_graph_revision_id),
        target_branch_head_version=row.target_branch_head_version,
        expected_selection_version=row.expected_selection_version,
        undo_of_promotion_id=(
            UUID(row.undo_of_promotion_id) if row.undo_of_promotion_id else None
        ),
        impact=json.loads(row.impact_json),
        impact_sha256=row.impact_sha256,
        promotion_sha256=row.promotion_sha256,
        risk_summary=json.loads(row.risk_json),
        state=row.state,
        approval_id=UUID(row.approval_id) if row.approval_id else None,
        created_at=as_utc(row.created_at),
        applied_at=as_utc(row.applied_at) if row.applied_at else None,
    )


class PromotionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        graph_service: GraphService,
        events: EventSpineService,
        *,
        branches: BranchRepository | None = None,
        graphs: GraphRepository | None = None,
        write_gate: WriteGate | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.graph_service = graph_service
        self.events = events
        self.branches = branches or BranchRepository()
        self.graphs = graphs or GraphRepository()
        self._write_gate = write_gate or WriteGate()

    @staticmethod
    def _promotion_sha256(row_payload: dict) -> str:
        return canonical_sha256(row_payload)

    @staticmethod
    def _response(row: GraphPromotionRow) -> PromotionResponse:
        return promotion_response(row)

    @staticmethod
    def _subject_payload(row: GraphPromotionRow) -> dict:
        return {
            "kind": row.kind,
            "project_id": row.project_id,
            "client_mutation_id": row.client_mutation_id,
            "source_branch_id": row.source_branch_id,
            "source_graph_revision_id": row.source_graph_revision_id,
            "source_branch_head_version": row.source_branch_head_version,
            "target_branch_id": row.target_branch_id,
            "target_graph_revision_id": row.target_graph_revision_id,
            "target_branch_head_version": row.target_branch_head_version,
            "expected_selection_version": row.expected_selection_version,
            "undo_of_promotion_id": row.undo_of_promotion_id,
            "impact": json.loads(row.impact_json),
            "risk_summary": json.loads(row.risk_json),
        }

    def _validated(self, row: GraphPromotionRow) -> tuple[dict, dict] | None:
        try:
            impact = json.loads(row.impact_json)
            risk = json.loads(row.risk_json)
            payload = self._subject_payload(row)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if (
            row.impact_json != canonical_json(impact)
            or row.risk_json != canonical_json(risk)
            or row.impact_sha256 != canonical_sha256(impact)
            or row.promotion_sha256 != self._promotion_sha256(payload)
        ):
            return None
        return impact, risk

    def record_stale(self, project_id: UUID, promotion_id: UUID) -> None:
        """Persist STALE after a failed transaction has rolled back its partial work."""
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = self.branches.promotion(session, str(promotion_id))
                if row is None or row.project_id != str(project_id):
                    return
                if row.state in {"PENDING_APPROVAL", "APPROVED"}:
                    row.state = "STALE"

    def request(
        self,
        project_id: UUID,
        request: PromotionRequest,
        *,
        actor_id: str,
    ) -> PromotionResponse:
        with self._write_gate:
            with self.session_factory.begin() as session:
                document = self.graphs.document_for_project(session, str(project_id))
                if document is None:
                    raise ProjectNotFound(str(project_id))
                pointer = self.branches.selection(session, document.id)
                target = self.branches.branch_for_project(
                    session, str(project_id), str(request.target_branch_id)
                )
                if target is None:
                    raise GraphBranchNotFound(str(request.target_branch_id))
                target_head = self.branches.head(session, target.id)
                if pointer is None or target_head is None:
                    raise StoredGraphIntegrityError(document.id)
                source_head = self.branches.head(session, pointer.branch_id)
                if source_head is None:
                    raise StoredGraphIntegrityError(pointer.graph_revision_id)
                if (
                    pointer.version != request.expected_selection_version
                    or target_head.version
                    != request.expected_target_branch_head_version
                    or target_head.graph_revision_id
                    != str(request.expected_target_branch_head_revision_id)
                    or pointer.branch_id == target.id
                ):
                    raise PromotionStale
                replay = self.branches.promotion_for_mutation(
                    session, str(project_id), str(request.client_mutation_id)
                )
                target_revision = self.graphs.revision(
                    session, target_head.graph_revision_id
                )
                source_revision = self.graphs.revision(
                    session, pointer.graph_revision_id
                )
                if target_revision is None or source_revision is None:
                    raise StoredGraphIntegrityError(target_head.graph_revision_id)
                target_snapshot = self.graph_service._load_stored_snapshot(target_revision)
                materialization_rows = self.branches.materialization_rows(
                    session, target_head.graph_revision_id
                )
                current = sum(row.state == "CURRENT" for row in materialization_rows)
                stale = sum(row.state == "STALE" for row in materialization_rows)
                materialization = MaterializationSummary(
                    unmaterialized=max(
                        0, len(target_snapshot.nodes) - current - stale
                    ),
                    current=current,
                    stale=stale,
                )
                impact = {
                    "source_branch_id": pointer.branch_id,
                    "source_graph_revision_id": pointer.graph_revision_id,
                    "source_graph_revision_sha256": source_revision.snapshot_hash,
                    "target_branch_id": target.id,
                    "target_graph_revision_id": target_head.graph_revision_id,
                    "target_graph_revision_sha256": target_revision.snapshot_hash,
                    "materialization": materialization.model_dump(mode="json"),
                }
                risk = {"classes": ["SELECTION_PROMOTION"]}
                now = utc_now()
                row = GraphPromotionRow(
                    id=str(uuid4()),
                    project_id=str(project_id),
                    correlation_id=str(uuid4()),
                    client_mutation_id=str(request.client_mutation_id),
                    kind="PROMOTE",
                    source_branch_id=pointer.branch_id,
                    source_graph_revision_id=pointer.graph_revision_id,
                    source_branch_head_version=source_head.version,
                    target_branch_id=target.id,
                    target_graph_revision_id=target_head.graph_revision_id,
                    target_branch_head_version=target_head.version,
                    expected_selection_version=pointer.version,
                    undo_of_promotion_id=None,
                    impact_json=canonical_json(impact),
                    impact_sha256=canonical_sha256(impact),
                    promotion_sha256="0" * 64,
                    risk_json=canonical_json(risk),
                    state="PENDING_APPROVAL",
                    approval_id=None,
                    created_at=now,
                    applied_at=None,
                )
                row.promotion_sha256 = self._promotion_sha256(
                    self._subject_payload(row)
                )
                if replay is not None:
                    if replay.promotion_sha256 != row.promotion_sha256:
                        raise ClientMutationReuse(str(request.client_mutation_id))
                    return self._response(replay)
                self.branches.add_promotion(session, row)
                session.flush()
                self.events.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=UUID(row.correlation_id),
                    actor_kind="HUMAN",
                    actor_id=actor_id,
                    subject_type="selection_promotion",
                    subject_id=UUID(row.id),
                    event_type="selection_promotion.requested",
                    payload={
                        "promotion_sha256": row.promotion_sha256,
                        "target_branch_id": row.target_branch_id,
                    },
                )
                return self._response(row)

    def approve(
        self,
        project_id: UUID,
        promotion_id: UUID,
        *,
        expected_promotion_sha256: str,
        actor_id: str,
        decision: str,
    ) -> PromotionResponse:
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = self.branches.promotion(session, str(promotion_id))
                if row is None or row.project_id != str(project_id):
                    raise PromotionNotFound(str(promotion_id))
                validated = self._validated(row)
                if validated is None:
                    row.state = "STALE"
                    raise ApprovalSubjectIntegrityError("selection_promotion", row.id)
                if (
                    row.state != "PENDING_APPROVAL"
                    or row.promotion_sha256 != expected_promotion_sha256
                ):
                    raise PromotionStale
                _, risk = validated
                approval = ApprovalDecisionRow(
                    id=str(uuid4()),
                    project_id=str(project_id),
                    correlation_id=row.correlation_id,
                    subject_type="selection_promotion",
                    subject_id=row.id,
                    subject_sha256=row.promotion_sha256,
                    risk_sha256=canonical_sha256(risk),
                    decision=decision,
                    actor_id=actor_id,
                    created_at=utc_now(),
                )
                session.add(approval)
                row.approval_id = approval.id
                row.state = "APPROVED" if decision == "APPROVED" else "REJECTED"
                self.events.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=UUID(row.correlation_id),
                    actor_kind="HUMAN",
                    actor_id=actor_id,
                    subject_type="selection_promotion",
                    subject_id=UUID(row.id),
                    event_type="approval.recorded",
                    payload={
                        "approval_id": approval.id,
                        "decision": decision,
                        "subject_sha256": row.promotion_sha256,
                    },
                )
                if decision == "REJECTED":
                    self.events.append_in_session(
                        session,
                        project_id=project_id,
                        correlation_id=UUID(row.correlation_id),
                        actor_kind="HUMAN",
                        actor_id=actor_id,
                        subject_type="selection_promotion",
                        subject_id=UUID(row.id),
                        event_type="selection_promotion.rejected",
                        payload={"reason": "USER_REJECTED"},
                    )
                return self._response(row)

    def undo_proposal(
        self,
        project_id: UUID,
        promotion_id: UUID,
        *,
        client_mutation_id: UUID,
        actor_id: str,
    ) -> PromotionResponse:
        with self._write_gate:
            with self.session_factory.begin() as session:
                original = self.branches.promotion(session, str(promotion_id))
                latest = self.branches.effective_latest_promotion(
                    session, str(project_id)
                )
                if (
                    original is None
                    or original.project_id != str(project_id)
                ):
                    raise PromotionNotFound(str(promotion_id))
                if (
                    original.state != "APPLIED"
                    or latest is None
                    or latest.id != original.id
                ):
                    raise PromotionUndoStale
                document = self.graphs.document_for_project(session, str(project_id))
                pointer = (
                    self.branches.selection(session, document.id)
                    if document is not None
                    else None
                )
                source_head = self.branches.head(session, original.target_branch_id)
                target_head = self.branches.head(session, original.source_branch_id)
                if (
                    document is None
                    or pointer is None
                    or source_head is None
                    or target_head is None
                    or pointer.branch_id != original.target_branch_id
                    or pointer.graph_revision_id != original.target_graph_revision_id
                    or source_head.graph_revision_id != original.target_graph_revision_id
                    or target_head.graph_revision_id != original.source_graph_revision_id
                    or target_head.version != original.source_branch_head_version
                ):
                    raise PromotionUndoStale
                target_revision = self.graphs.revision(
                    session, target_head.graph_revision_id
                )
                source_revision = self.graphs.revision(
                    session, source_head.graph_revision_id
                )
                if target_revision is None or source_revision is None:
                    raise StoredGraphIntegrityError(target_head.graph_revision_id)
                target_snapshot = self.graph_service._load_stored_snapshot(target_revision)
                rows = self.branches.materialization_rows(
                    session, target_head.graph_revision_id
                )
                current = sum(row.state == "CURRENT" for row in rows)
                stale = sum(row.state == "STALE" for row in rows)
                impact = {
                    "source_branch_id": pointer.branch_id,
                    "source_graph_revision_id": pointer.graph_revision_id,
                    "source_graph_revision_sha256": source_revision.snapshot_hash,
                    "target_branch_id": original.source_branch_id,
                    "target_graph_revision_id": original.source_graph_revision_id,
                    "target_graph_revision_sha256": target_revision.snapshot_hash,
                    "materialization": MaterializationSummary(
                        unmaterialized=max(
                            0, len(target_snapshot.nodes) - current - stale
                        ),
                        current=current,
                        stale=stale,
                    ).model_dump(mode="json"),
                }
                risk = {"classes": ["SELECTION_PROMOTION_UNDO"]}
                row = GraphPromotionRow(
                    id=str(uuid4()),
                    project_id=str(project_id),
                    correlation_id=str(uuid4()),
                    client_mutation_id=str(client_mutation_id),
                    kind="UNDO_PROMOTION",
                    source_branch_id=pointer.branch_id,
                    source_graph_revision_id=pointer.graph_revision_id,
                    source_branch_head_version=source_head.version,
                    target_branch_id=original.source_branch_id,
                    target_graph_revision_id=original.source_graph_revision_id,
                    target_branch_head_version=target_head.version,
                    expected_selection_version=pointer.version,
                    undo_of_promotion_id=original.id,
                    impact_json=canonical_json(impact),
                    impact_sha256=canonical_sha256(impact),
                    promotion_sha256="0" * 64,
                    risk_json=canonical_json(risk),
                    state="PENDING_APPROVAL",
                    approval_id=None,
                    created_at=utc_now(),
                    applied_at=None,
                )
                row.promotion_sha256 = self._promotion_sha256(
                    self._subject_payload(row)
                )
                replay = self.branches.promotion_for_mutation(
                    session, str(project_id), str(client_mutation_id)
                )
                if replay is not None:
                    if replay.promotion_sha256 != row.promotion_sha256:
                        raise ClientMutationReuse(str(client_mutation_id))
                    return self._response(replay)
                self.branches.add_promotion(session, row)
                session.flush()
                self.events.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=UUID(row.correlation_id),
                    actor_kind="HUMAN",
                    actor_id=actor_id,
                    subject_type="selection_promotion",
                    subject_id=UUID(row.id),
                    event_type="selection_promotion.requested",
                    payload={
                        "promotion_sha256": row.promotion_sha256,
                        "undo_of_promotion_id": original.id,
                    },
                )
                return self._response(row)

    def apply(
        self,
        project_id: UUID,
        promotion_id: UUID,
        *,
        expected_promotion_sha256: str,
        actor_id: str,
    ) -> PromotionResponse:
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = self.branches.promotion(session, str(promotion_id))
                if row is None or row.project_id != str(project_id):
                    raise PromotionNotFound(str(promotion_id))
                if row.state == "APPLIED" and row.promotion_sha256 == expected_promotion_sha256:
                    return self._response(row)
                if row.state == "PENDING_APPROVAL" or row.approval_id is None:
                    raise ApprovalRequired
                if row.state != "APPROVED" or row.promotion_sha256 != expected_promotion_sha256:
                    raise PromotionStale
                validated = self._validated(row)
                approval = session.get(ApprovalDecisionRow, row.approval_id)
                if (
                    validated is None
                    or approval is None
                    or approval.subject_type != "selection_promotion"
                    or approval.subject_id != row.id
                    or approval.subject_sha256 != row.promotion_sha256
                    or approval.risk_sha256 != canonical_sha256(validated[1])
                    or approval.decision != "APPROVED"
                ):
                    row.state = "STALE"
                    raise ApprovalSubjectIntegrityError("selection_promotion", row.id)
                document = self.graphs.document_for_project(session, str(project_id))
                target_head = self.branches.head(session, row.target_branch_id)
                pointer = (
                    self.branches.selection(session, document.id)
                    if document is not None
                    else None
                )
                if (
                    document is None
                    or pointer is None
                    or target_head is None
                    or pointer.branch_id != row.source_branch_id
                    or pointer.graph_revision_id != row.source_graph_revision_id
                    or pointer.version != row.expected_selection_version
                    or target_head.graph_revision_id != row.target_graph_revision_id
                    or target_head.version != row.target_branch_head_version
                ):
                    row.state = "STALE"
                    raise PromotionStale
                old_revision_id = pointer.graph_revision_id
                now = utc_now()
                moved = self.branches.move_selection(
                    session,
                    graph_document_id=document.id,
                    expected_branch_id=pointer.branch_id,
                    expected_revision_id=pointer.graph_revision_id,
                    expected_version=pointer.version,
                    new_branch_id=row.target_branch_id,
                    new_revision_id=row.target_graph_revision_id,
                    updated_at=now,
                )
                mirrored = session.execute(
                    update(GraphDocumentRow)
                    .where(
                        GraphDocumentRow.id == document.id,
                        GraphDocumentRow.active_revision_id == old_revision_id,
                    )
                    .values(active_revision_id=row.target_graph_revision_id)
                )
                if not moved.applied or mirrored.rowcount != 1:
                    row.state = "STALE"
                    raise PromotionStale
                row.state = "APPLIED"
                row.applied_at = now
                self.events.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=UUID(row.correlation_id),
                    actor_kind="HUMAN",
                    actor_id=actor_id,
                    subject_type="selection_promotion",
                    subject_id=UUID(row.id),
                    event_type=(
                        "selection_promotion.undone"
                        if row.kind == "UNDO_PROMOTION"
                        else "selection_promotion.applied"
                    ),
                    payload={
                        "source_branch_id": row.source_branch_id,
                        "target_branch_id": row.target_branch_id,
                        "selection_version": row.expected_selection_version + 1,
                    },
                )
                return self._response(row)
