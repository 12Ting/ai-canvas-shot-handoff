import json
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.errors import (
    ApprovalSubjectIntegrityError,
    ApprovalSubjectStale,
    ApprovalRequired,
    BranchHeadStale,
    ClientMutationReuse,
    ChangeSetDomainMismatch,
    GraphChangeSetNotFound,
    GraphChangeSetBaseRevisionStale,
    GraphChangeSetUndoStale,
    GraphChangeSetUndoUnsupported,
    GraphBranchNotFound,
    GraphBranchNameConflict,
    GraphRevisionNotFound,
    ProjectNotFound,
    SelectionPointerStale,
    StaleImpactConfirmationRequired,
    StoredGraphIntegrityError,
)
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_service import GraphService, canonical_snapshot
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts.graph import GraphSnapshotV3, PromptNodeV3, ResourceBinding
from ai_canvas.contracts.graph_changesets import (
    CreateGraphChangeSetRequest,
    GraphChangeSetDiff,
    GraphChangeSetResponse,
)
from ai_canvas.contracts.branches import (
    BranchApplicationTarget,
    DirectApplicationTarget,
    NewBranchApplicationTarget,
    StaleImpactResponse,
)
from ai_canvas.contracts.experience_events import ActorKind
from ai_canvas.domain.control.canonical import canonical_json, canonical_sha256
from ai_canvas.domain.graph.snapshots import normalize_to_v3, parse_snapshot_json
from ai_canvas.domain.graph.validation import validate_graph
from ai_canvas.domain.branching.names import normalize_branch_name
from ai_canvas.domain.branching.stale_impact import (
    SparseMaterializationState,
    compute_stale_impact,
)
from ai_canvas.infrastructure.db.branch_repository import BranchRepository
from ai_canvas.infrastructure.db.models import (
    ApprovalDecisionRow,
    ConfigChangeSetRow,
    GraphChangeSetRow,
    GraphBranchHeadRow,
    GraphBranchRow,
    GraphDocumentRow,
    NodeMaterializationStateRow,
)
from ai_canvas.infrastructure.db.repositories import GraphRepository


_TARGET_ADAPTER = TypeAdapter(BranchApplicationTarget)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class GraphChangeSetService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        graph_service: GraphService,
        events: EventSpineService,
        *,
        graphs: GraphRepository | None = None,
        branches: BranchRepository | None = None,
        write_gate: WriteGate | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.graph_service = graph_service
        self.events = events
        self.graphs = graphs or GraphRepository()
        self.branches = branches or BranchRepository()
        self._write_gate = write_gate or WriteGate()

    @staticmethod
    def _response(row: GraphChangeSetRow) -> GraphChangeSetResponse:
        if row.application_target_json is None or row.impact_json is None:
            raise ValueError("historical graph change set has no C2 branch witness")
        stored_impact = json.loads(row.impact_json)
        return GraphChangeSetResponse(
            id=UUID(row.id),
            project_id=UUID(row.project_id),
            correlation_id=UUID(row.correlation_id),
            base_graph_revision_id=UUID(row.base_graph_revision_id),
            changeset_sha256=row.changeset_sha256,
            snapshot_hash=row.snapshot_hash,
            diff=json.loads(row.diff_json),
            risk_summary=json.loads(row.risk_json),
            state=row.state,
            applied_graph_revision_id=(
                UUID(row.applied_graph_revision_id) if row.applied_graph_revision_id is not None else None
            ),
            application_target=_TARGET_ADAPTER.validate_json(row.application_target_json),
            impact=StaleImpactResponse.model_validate(
                {key: value for key, value in stored_impact.items() if key != "projected_rows"}
            ),
            created_branch_id=(UUID(row.created_branch_id) if row.created_branch_id else None),
            applied_branch_id=(UUID(row.applied_branch_id) if row.applied_branch_id else None),
            created_at=as_utc(row.created_at),
        )

    @staticmethod
    def _changeset_sha256(
        *,
        base_graph_revision_id: str,
        client_mutation_id: str,
        snapshot: dict,
        diff: dict,
        inverse_operations: list[dict],
        risk_summary: dict,
        application_target: dict,
        impact: dict,
    ) -> str:
        return canonical_sha256(
            {
                "kind": "graph_change_set",
                "schema_version": 1,
                "base_graph_revision_id": base_graph_revision_id,
                "client_mutation_id": client_mutation_id,
                "operations": [{"op": "replace_snapshot", "snapshot": snapshot}],
                "diff": diff,
                "inverse_operations": inverse_operations,
                "risk_summary": risk_summary,
                "application_target": application_target,
                "impact": impact,
            }
        )

    def _validated_subject(self, row: GraphChangeSetRow):
        try:
            snapshot = validate_graph(parse_snapshot_json(row.snapshot_json))
            snapshot_json, snapshot_hash = canonical_snapshot(snapshot)
            snapshot_payload = json.loads(snapshot_json)
            diff = json.loads(row.diff_json)
            inverse_operations = json.loads(row.inverse_operations_json)
            risk_summary = json.loads(row.risk_json)
            if row.application_target_json is None or row.impact_json is None:
                return None
            application_target = _TARGET_ADAPTER.validate_json(row.application_target_json)
            application_target_payload = application_target.model_dump(mode="json")
            impact = json.loads(row.impact_json)
            public_impact = {key: value for key, value in impact.items() if key != "projected_rows"}
            validated_impact = StaleImpactResponse.model_validate(public_impact)
            changeset_sha256 = self._changeset_sha256(
                base_graph_revision_id=row.base_graph_revision_id,
                client_mutation_id=row.client_mutation_id,
                snapshot=snapshot_payload,
                diff=diff,
                inverse_operations=inverse_operations,
                risk_summary=risk_summary,
                application_target=application_target_payload,
                impact=impact,
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if (
            row.snapshot_json != snapshot_json
            or row.snapshot_hash != snapshot_hash
            or row.changeset_sha256 != changeset_sha256
            or row.impact_sha256 != validated_impact.impact_sha256
            or row.application_target_json != canonical_json(application_target_payload)
            or row.impact_json != canonical_json(impact)
        ):
            return None
        return snapshot, risk_summary, application_target, impact

    @staticmethod
    def _binding_item(binding: ResourceBinding) -> dict:
        return {
            "binding_id": str(binding.id),
            "scope_kind": binding.scope_kind,
            "scope_id": str(binding.scope_id) if binding.scope_id is not None else None,
            "binding_key": binding.binding_key,
            "resource_kind": binding.resource_kind,
            "resource_revision_id": str(binding.resource_revision_id),
        }

    @staticmethod
    def _binding_sort_key(item: dict) -> tuple[str, str, str, str]:
        return (
            item["scope_kind"],
            item["scope_id"] or "",
            item["binding_key"],
            item["binding_id"],
        )

    @staticmethod
    def _prompt_documents(
        snapshot: GraphSnapshotV3,
    ) -> dict[str, tuple[dict, int, str]]:
        documents: dict[str, tuple[dict, int, str]] = {}
        for node in snapshot.nodes:
            if not isinstance(node, PromptNodeV3):
                continue
            payload = node.data.document.model_dump(mode="json")
            locked_blocks = [
                block.model_dump(mode="json")
                for block in node.data.document.blocks
                if block.locked
            ]
            documents[str(node.id)] = (
                payload,
                len(locked_blocks),
                canonical_sha256(locked_blocks),
            )
        return documents

    @classmethod
    def _c1_diff(
        cls,
        base_snapshot,
        proposed_snapshot: GraphSnapshotV3,
        *,
        base_hash: str,
        proposed_hash: str,
    ) -> tuple[dict, bool, bool]:
        base_v3 = normalize_to_v3(base_snapshot)
        base_bindings = {str(binding.id): binding for binding in base_v3.resource_bindings}
        proposed_bindings = {
            str(binding.id): binding for binding in proposed_snapshot.resource_bindings
        }
        added: list[dict] = []
        removed: list[dict] = []
        updated: list[dict] = []
        for binding_id in sorted(set(base_bindings) | set(proposed_bindings)):
            old = base_bindings.get(binding_id)
            new = proposed_bindings.get(binding_id)
            if old is None and new is not None:
                added.append(cls._binding_item(new))
                continue
            if old is not None and new is None:
                removed.append(cls._binding_item(old))
                continue
            assert old is not None and new is not None
            old_identity = (
                old.scope_kind,
                old.scope_id,
                old.binding_key,
                old.resource_kind,
            )
            new_identity = (
                new.scope_kind,
                new.scope_id,
                new.binding_key,
                new.resource_kind,
            )
            if old_identity != new_identity:
                removed.append(cls._binding_item(old))
                added.append(cls._binding_item(new))
            elif old.resource_revision_id != new.resource_revision_id:
                updated.append(
                    {
                        "binding_id": binding_id,
                        "scope_kind": new.scope_kind,
                        "scope_id": str(new.scope_id) if new.scope_id is not None else None,
                        "binding_key": new.binding_key,
                        "old_resource_revision_id": str(old.resource_revision_id),
                        "new_resource_revision_id": str(new.resource_revision_id),
                    }
                )
        added.sort(key=cls._binding_sort_key)
        removed.sort(key=cls._binding_sort_key)
        updated.sort(key=cls._binding_sort_key)

        base_documents = cls._prompt_documents(base_v3)
        proposed_documents = cls._prompt_documents(proposed_snapshot)
        prompt_changes: list[dict] = []
        locked_prompt_changed = False
        for node_id in sorted(set(base_documents) | set(proposed_documents)):
            old = base_documents.get(node_id)
            new = proposed_documents.get(node_id)
            old_sha = canonical_sha256(old[0]) if old is not None else None
            new_sha = canonical_sha256(new[0]) if new is not None else None
            if old_sha == new_sha:
                continue
            old_locked_count = old[1] if old is not None else 0
            new_locked_count = new[1] if new is not None else 0
            old_locked_sha = old[2] if old is not None else canonical_sha256([])
            new_locked_sha = new[2] if new is not None else canonical_sha256([])
            locked_prompt_changed = locked_prompt_changed or old_locked_sha != new_locked_sha
            prompt_changes.append(
                {
                    "node_id": node_id,
                    "old_document_sha256": old_sha,
                    "new_document_sha256": new_sha,
                    "old_locked_block_count": old_locked_count,
                    "new_locked_block_count": new_locked_count,
                }
            )

        diff = GraphChangeSetDiff(
            from_snapshot_hash=base_hash,
            to_snapshot_hash=proposed_hash,
            resource_bindings_added=added,
            resource_bindings_removed=removed,
            resource_bindings_updated=updated,
            prompt_documents_changed=prompt_changes,
        ).model_dump(mode="json")
        return diff, bool(added or removed or updated), locked_prompt_changed

    def _reject_integrity_in_session(
        self,
        session: Session,
        row: GraphChangeSetRow,
    ) -> None:
        row.state = "REJECTED"
        self.events.append_in_session(
            session,
            project_id=UUID(row.project_id),
            correlation_id=UUID(row.correlation_id),
            actor_kind="SYSTEM",
            actor_id="control-plane",
            subject_type="graph_change_set",
            subject_id=UUID(row.id),
            event_type="graph_change_set.rejected",
            payload={"reason": "SUBJECT_INTEGRITY_MISMATCH"},
        )

    def _record_rejection_in_session(
        self,
        session: Session,
        row: GraphChangeSetRow,
        *,
        actor_kind: str,
        actor_id: str,
        reason: str,
    ) -> None:
        row.state = "REJECTED"
        self.events.append_in_session(
            session,
            project_id=UUID(row.project_id),
            correlation_id=UUID(row.correlation_id),
            actor_kind=actor_kind,
            actor_id=actor_id,
            subject_type="graph_change_set",
            subject_id=UUID(row.id),
            event_type="graph_change_set.rejected",
            payload={"reason": reason},
        )

    @staticmethod
    def _impact_payload(computed, node_count: int) -> dict:
        current = sum(row.state == "CURRENT" for row in computed.projected_rows)
        stale = sum(row.state == "STALE" for row in computed.projected_rows)
        return {
            "items": [
                {
                    "node_id": str(item.node_id),
                    "impact_kind": item.impact_kind,
                    "reasons": list(item.reasons),
                    "old_state": item.old_state,
                    "projected_state": item.projected_state,
                }
                for item in computed.items
            ],
            "summary": {
                "unmaterialized": max(0, node_count - current - stale),
                "current": current,
                "stale": stale,
            },
            "impact_sha256": computed.impact_sha256,
            "confirmation_required": computed.confirmation_required,
            "projected_rows": [
                {
                    "node_id": str(row.node_id),
                    "state": row.state,
                    "last_materialized_graph_revision_id": str(
                        row.last_materialized_graph_revision_id
                    ),
                    "reasons": list(row.reasons),
                }
                for row in computed.projected_rows
            ],
        }

    def request(
        self,
        project_id: UUID,
        request: CreateGraphChangeSetRequest,
        *,
        actor_id: str,
        actor_kind: ActorKind = "HUMAN",
        after_create: Callable[[Session, GraphChangeSetRow], None] | None = None,
        correlation_id: UUID | None = None,
    ) -> GraphChangeSetResponse:
        proposed_snapshot = validate_graph(request.operations[0].snapshot)
        normalized_snapshot = normalize_to_v3(proposed_snapshot)
        snapshot_json, snapshot_hash = canonical_snapshot(normalized_snapshot)
        with self._write_gate:
            with self.session_factory.begin() as session:
                document = self.graphs.document_for_project(session, str(project_id))
                if document is None:
                    raise ProjectNotFound(str(project_id))
                selection = self.branches.selection(session, document.id)
                if (
                    selection is None
                    or document.active_revision_id != selection.graph_revision_id
                ):
                    raise GraphChangeSetBaseRevisionStale(
                        document.active_revision_id or ""
                    )
                target = request.application_target
                if target is None:
                    head = self.branches.head(session, selection.branch_id)
                    if head is None:
                        raise GraphRevisionNotFound(selection.graph_revision_id)
                    if head.graph_revision_id != str(request.base_graph_revision_id):
                        raise GraphChangeSetBaseRevisionStale(
                            head.graph_revision_id
                        )
                    target = DirectApplicationTarget(
                        branch_id=UUID(selection.branch_id),
                        expected_branch_head_revision_id=UUID(head.graph_revision_id),
                        expected_branch_head_version=head.version,
                        expected_selection_version=selection.version,
                    )
                target_branch_id = (
                    target.branch_id
                    if isinstance(target, DirectApplicationTarget)
                    else target.base_branch_id
                )
                branch = self.branches.branch_for_project(
                    session, str(project_id), str(target_branch_id)
                )
                if branch is None:
                    raise GraphBranchNotFound(str(target_branch_id))
                head = self.branches.head(session, branch.id)
                if head is None:
                    raise GraphRevisionNotFound(str(target_branch_id))
                if selection.version != target.expected_selection_version:
                    raise SelectionPointerStale(
                        selection.branch_id,
                        selection.graph_revision_id,
                        selection.version,
                    )
                if (
                    head.version != target.expected_branch_head_version
                    or head.graph_revision_id
                    != str(target.expected_branch_head_revision_id)
                    or head.graph_revision_id != str(request.base_graph_revision_id)
                ):
                    raise BranchHeadStale(
                        branch.id, head.graph_revision_id, head.version
                    )
                if isinstance(target, NewBranchApplicationTarget):
                    display_name, normalized_name = normalize_branch_name(target.branch_name)
                    if self.branches.branch_by_normalized_name(
                        session, document.id, normalized_name
                    ) is not None:
                        raise GraphBranchNameConflict(normalized_name)
                    target = target.model_copy(update={"branch_name": display_name})
                base_revision = self.graphs.revision(session, str(request.base_graph_revision_id))
                if base_revision is None or base_revision.graph_document_id != document.id:
                    raise GraphRevisionNotFound(str(request.base_graph_revision_id))
                base_snapshot = self.graph_service._load_stored_snapshot(base_revision)
                base_json, base_hash = canonical_snapshot(base_snapshot)
                self.graph_service._validate_artifact_references(
                    session,
                    normalized_snapshot,
                )
                self.graph_service._validate_resource_references(
                    session,
                    normalized_snapshot,
                )
                effective_correlation_id = correlation_id or uuid4()
                diff, resource_pin_changed, locked_prompt_changed = self._c1_diff(
                    base_snapshot,
                    normalized_snapshot,
                    base_hash=base_hash,
                    proposed_hash=snapshot_hash,
                )
                inverse_operations = [{"op": "replace_snapshot", "snapshot": json.loads(base_json)}]
                risk_classes = ["GRAPH_WRITE"]
                risk_classes.append(
                    "DIRECT_BRANCH_ADVANCE"
                    if isinstance(target, DirectApplicationTarget)
                    else "BRANCH_CREATE"
                )
                if resource_pin_changed:
                    risk_classes.append("RESOURCE_PIN_CHANGE")
                if locked_prompt_changed:
                    risk_classes.append("LOCKED_PROMPT_BLOCK_CHANGE")
                base_states = {
                    UUID(row.node_id): SparseMaterializationState(
                        state=row.state,
                        last_materialized_graph_revision_id=UUID(
                            row.last_materialized_graph_revision_id
                        ),
                        reasons=tuple(json.loads(row.reason_json)),
                    )
                    for row in self.branches.materialization_rows(
                        session, base_revision.id
                    )
                }
                computed_impact = compute_stale_impact(
                    base=base_snapshot,
                    proposed=normalized_snapshot,
                    base_states=base_states,
                )
                impact = self._impact_payload(
                    computed_impact, len(normalized_snapshot.nodes)
                )
                if computed_impact.confirmation_required:
                    risk_classes.append("MATERIALIZATION_STALE")
                risk_summary = {"classes": risk_classes}
                target_payload = target.model_dump(mode="json")
                changeset_sha256 = self._changeset_sha256(
                    base_graph_revision_id=str(request.base_graph_revision_id),
                    client_mutation_id=str(request.client_mutation_id),
                    snapshot=json.loads(snapshot_json),
                    diff=diff,
                    inverse_operations=inverse_operations,
                    risk_summary=risk_summary,
                    application_target=target_payload,
                    impact=impact,
                )
                replay = session.scalar(
                    select(GraphChangeSetRow).where(
                        GraphChangeSetRow.project_id == str(project_id),
                        GraphChangeSetRow.client_mutation_id
                        == str(request.client_mutation_id),
                    )
                )
                if replay is not None:
                    if replay.changeset_sha256 != changeset_sha256:
                        raise ClientMutationReuse(str(request.client_mutation_id))
                    return self._response(replay)
                row = GraphChangeSetRow(
                    id=str(uuid4()),
                    project_id=str(project_id),
                    correlation_id=str(effective_correlation_id),
                    base_graph_revision_id=str(request.base_graph_revision_id),
                    client_mutation_id=str(request.client_mutation_id),
                    snapshot_json=snapshot_json,
                    snapshot_hash=snapshot_hash,
                    diff_json=canonical_json(diff),
                    inverse_operations_json=canonical_json(inverse_operations),
                    risk_json=canonical_json(risk_summary),
                    changeset_sha256=changeset_sha256,
                    state="PENDING_APPROVAL",
                    approval_id=None,
                    applied_graph_revision_id=None,
                    application_target_json=canonical_json(target_payload),
                    impact_json=canonical_json(impact),
                    impact_sha256=computed_impact.impact_sha256,
                    created_branch_id=None,
                    applied_branch_id=None,
                    created_at=utc_now(),
                    applied_at=None,
                )
                session.add(row)
                session.flush()
                self.events.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=effective_correlation_id,
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                    subject_type="graph_change_set",
                    subject_id=UUID(row.id),
                    event_type="graph_change_set.requested",
                    payload={"changeset_sha256": changeset_sha256, "snapshot_hash": snapshot_hash},
                )
                if after_create is not None:
                    after_create(session, row)
                return self._response(row)

    def approve(
        self,
        project_id: UUID,
        change_set_id: UUID,
        *,
        expected_changeset_sha256: str,
        actor_id: str,
        decision: str = "APPROVED",
        confirmed_stale_impact_sha256: str | None = None,
    ) -> GraphChangeSetResponse:
        rejection: ApprovalSubjectIntegrityError | None = None
        response: GraphChangeSetResponse | None = None
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = session.get(GraphChangeSetRow, str(change_set_id))
                if row is None:
                    if session.get(ConfigChangeSetRow, str(change_set_id)) is not None:
                        raise ChangeSetDomainMismatch(str(change_set_id))
                    raise GraphChangeSetNotFound(str(change_set_id))
                if row.project_id != str(project_id):
                    raise GraphChangeSetNotFound(str(change_set_id))
                validated = self._validated_subject(row)
                if validated is None:
                    self._reject_integrity_in_session(session, row)
                    rejection = ApprovalSubjectIntegrityError("graph_change_set", row.id)
                elif row.state != "PENDING_APPROVAL" or row.changeset_sha256 != expected_changeset_sha256:
                    raise ApprovalSubjectStale
                else:
                    _, risk_summary, _, impact = validated
                    if (
                        impact["confirmation_required"]
                        and confirmed_stale_impact_sha256 != row.impact_sha256
                    ):
                        raise StaleImpactConfirmationRequired(row.impact_sha256 or "")
                    approval = ApprovalDecisionRow(
                        id=str(uuid4()),
                        project_id=str(project_id),
                        correlation_id=row.correlation_id,
                        subject_type="graph_change_set",
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
                        project_id=project_id,
                        correlation_id=UUID(row.correlation_id),
                        actor_kind="HUMAN",
                        actor_id=actor_id,
                        subject_type="graph_change_set",
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
                    response = self._response(row)
        if rejection is not None:
            raise rejection
        if response is None:
            raise RuntimeError("graph change set approval produced no response")
        return response

    def _record_stale_apply(
        self,
        project_id: UUID,
        change_set_id: UUID,
        *,
        actor_id: str,
        reason: str,
    ) -> None:
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = session.get(GraphChangeSetRow, str(change_set_id))
                if (
                    row is None
                    or row.project_id != str(project_id)
                    or row.state != "APPROVED"
                ):
                    return
                self._record_rejection_in_session(
                    session,
                    row,
                    actor_kind="HUMAN",
                    actor_id=actor_id,
                    reason=reason,
                )

    def apply(
        self,
        project_id: UUID,
        change_set_id: UUID,
        *,
        expected_changeset_sha256: str,
        actor_id: str,
    ) -> GraphChangeSetResponse:
        try:
            return self._apply(
                project_id,
                change_set_id,
                expected_changeset_sha256=expected_changeset_sha256,
                actor_id=actor_id,
            )
        except (
            BranchHeadStale,
            GraphChangeSetBaseRevisionStale,
            SelectionPointerStale,
        ) as error:
            reason = {
                BranchHeadStale: "BRANCH_HEAD_STALE",
                GraphChangeSetBaseRevisionStale: "GRAPH_BASE_REVISION_STALE",
                SelectionPointerStale: "SELECTION_POINTER_STALE",
            }[type(error)]
            self._record_stale_apply(
                project_id,
                change_set_id,
                actor_id=actor_id,
                reason=reason,
            )
            raise

    def _apply(
        self,
        project_id: UUID,
        change_set_id: UUID,
        *,
        expected_changeset_sha256: str,
        actor_id: str,
    ) -> GraphChangeSetResponse:
        rejection: ApprovalSubjectIntegrityError | GraphChangeSetBaseRevisionStale | None = None
        response: GraphChangeSetResponse | None = None
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = session.get(GraphChangeSetRow, str(change_set_id))
                if row is None:
                    if session.get(ConfigChangeSetRow, str(change_set_id)) is not None:
                        raise ChangeSetDomainMismatch(str(change_set_id))
                    raise GraphChangeSetNotFound(str(change_set_id))
                if row.project_id != str(project_id):
                    raise GraphChangeSetNotFound(str(change_set_id))
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
                        and approval.subject_type == "graph_change_set"
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
                    rejection = ApprovalSubjectIntegrityError("graph_change_set", row.id)
                else:
                    document = self.graphs.document_for_project(session, str(project_id))
                    if document is None:
                        raise ProjectNotFound(str(project_id))
                    snapshot, _, target, impact = validated
                    pointer = self.branches.selection(session, document.id)
                    target_branch_id = (
                        target.branch_id
                        if isinstance(target, DirectApplicationTarget)
                        else target.base_branch_id
                    )
                    branch = self.branches.branch_for_project(
                        session, str(project_id), str(target_branch_id)
                    )
                    head = (
                        self.branches.head(session, str(target_branch_id))
                        if branch is not None
                        else None
                    )
                    if pointer is None:
                        raise GraphChangeSetBaseRevisionStale(
                            document.active_revision_id or ""
                        )
                    if pointer.version != target.expected_selection_version:
                        raise SelectionPointerStale(
                            pointer.branch_id,
                            pointer.graph_revision_id,
                            pointer.version,
                        )
                    if branch is None:
                        raise GraphBranchNotFound(str(target_branch_id))
                    if head is None:
                        raise GraphRevisionNotFound(str(target_branch_id))
                    if (
                        head.version != target.expected_branch_head_version
                        or head.graph_revision_id
                        != str(target.expected_branch_head_revision_id)
                        or head.graph_revision_id != row.base_graph_revision_id
                    ):
                        raise BranchHeadStale(
                            branch.id,
                            head.graph_revision_id,
                            head.version,
                        )
                    revision = self.graph_service.create_branch_revision_in_session(
                        session,
                        project_id,
                        parent_revision_id=UUID(row.base_graph_revision_id),
                        client_mutation_id=UUID(row.client_mutation_id),
                        snapshot=snapshot,
                    )
                    now = utc_now()
                    session.add_all(
                        [
                            NodeMaterializationStateRow(
                                graph_revision_id=str(revision.revision_id),
                                node_id=item["node_id"],
                                state=item["state"],
                                last_materialized_graph_revision_id=item[
                                    "last_materialized_graph_revision_id"
                                ],
                                reason_json=canonical_json(item["reasons"]),
                                updated_at=now,
                            )
                            for item in impact["projected_rows"]
                        ]
                    )
                    created_branch_id: str | None = None
                    if isinstance(target, DirectApplicationTarget):
                        advanced = self.branches.advance_head(
                            session,
                            branch_id=branch.id,
                            expected_revision_id=head.graph_revision_id,
                            expected_version=head.version,
                            new_revision_id=str(revision.revision_id),
                            updated_at=now,
                        )
                        if not advanced.applied:
                            raise BranchHeadStale(
                                branch.id,
                                advanced.current.graph_revision_id,
                                advanced.current.version,
                            )
                        applied_branch_id = branch.id
                        if pointer.branch_id == branch.id:
                            previous_selected_revision_id = pointer.graph_revision_id
                            moved = self.branches.move_selection(
                                session,
                                graph_document_id=document.id,
                                expected_branch_id=pointer.branch_id,
                                expected_revision_id=pointer.graph_revision_id,
                                expected_version=pointer.version,
                                new_branch_id=branch.id,
                                new_revision_id=str(revision.revision_id),
                                updated_at=now,
                            )
                            mirrored = session.execute(
                                update(GraphDocumentRow)
                                .where(
                                    GraphDocumentRow.id == document.id,
                                    GraphDocumentRow.active_revision_id
                                    == previous_selected_revision_id,
                                )
                                .values(active_revision_id=str(revision.revision_id))
                            )
                            if not moved.applied or mirrored.rowcount != 1:
                                raise SelectionPointerStale(
                                    moved.current.branch_id,
                                    moved.current.graph_revision_id,
                                    moved.current.version,
                                )
                        self.events.append_in_session(
                            session,
                            project_id=project_id,
                            correlation_id=UUID(row.correlation_id),
                            actor_kind="HUMAN",
                            actor_id=actor_id,
                            subject_type="graph_branch",
                            subject_id=UUID(branch.id),
                            event_type="graph_branch.head_advanced",
                            payload={
                                "branch_id": branch.id,
                                "graph_revision_id": str(revision.revision_id),
                                "version": head.version + 1,
                            },
                        )
                    else:
                        display_name, normalized_name = normalize_branch_name(
                            target.branch_name
                        )
                        if self.branches.branch_by_normalized_name(
                            session, document.id, normalized_name
                        ) is not None:
                            raise GraphBranchNameConflict(normalized_name)
                        created_branch_id = str(uuid4())
                        self.branches.create_branch_with_head(
                            session,
                            GraphBranchRow(
                                id=created_branch_id,
                                graph_document_id=document.id,
                                name=display_name,
                                normalized_name=normalized_name,
                                kind="EXPERIMENT",
                                forked_from_branch_id=branch.id,
                                forked_from_revision_id=head.graph_revision_id,
                                created_at=now,
                            ),
                            GraphBranchHeadRow(
                                branch_id=created_branch_id,
                                graph_revision_id=str(revision.revision_id),
                                version=1,
                                updated_at=now,
                            ),
                        )
                        applied_branch_id = created_branch_id
                        self.events.append_in_session(
                            session,
                            project_id=project_id,
                            correlation_id=UUID(row.correlation_id),
                            actor_kind="HUMAN",
                            actor_id=actor_id,
                            subject_type="graph_branch",
                            subject_id=UUID(created_branch_id),
                            event_type="graph_branch.created",
                            payload={
                                "branch_id": created_branch_id,
                                "base_branch_id": branch.id,
                                "graph_revision_id": str(revision.revision_id),
                            },
                        )
                    if impact["confirmation_required"]:
                        self.events.append_in_session(
                            session,
                            project_id=project_id,
                            correlation_id=UUID(row.correlation_id),
                            actor_kind="SYSTEM",
                            actor_id="control-plane",
                            subject_type="graph_revision",
                            subject_id=revision.revision_id,
                            event_type="node_materialization.staled",
                            payload={
                                "impact_sha256": row.impact_sha256,
                                "stale_count": impact["summary"]["stale"],
                            },
                        )
                    row.state = "APPLIED"
                    row.applied_graph_revision_id = str(revision.revision_id)
                    row.created_branch_id = created_branch_id
                    row.applied_branch_id = applied_branch_id
                    row.applied_at = now
                    self.events.append_in_session(
                        session,
                        project_id=project_id,
                        correlation_id=UUID(row.correlation_id),
                        actor_kind="HUMAN",
                        actor_id=actor_id,
                        subject_type="graph_change_set",
                        subject_id=UUID(row.id),
                        event_type="graph_change_set.applied",
                        payload={
                            "changeset_sha256": row.changeset_sha256,
                            "graph_revision_id": str(revision.revision_id),
                            "branch_id": applied_branch_id,
                        },
                    )
                    response = self._response(row)
        if rejection is not None:
            raise rejection
        if response is None:
            raise RuntimeError("graph change set apply produced no response")
        return response

    def undo_proposal(
        self,
        project_id: UUID,
        change_set_id: UUID,
        *,
        client_mutation_id: UUID,
        actor_id: str,
    ) -> GraphChangeSetResponse:
        with self.session_factory() as session:
            original = session.get(GraphChangeSetRow, str(change_set_id))
            if original is None or original.project_id != str(project_id):
                raise GraphChangeSetNotFound(str(change_set_id))
            if (
                original.application_target_json is None
                or original.impact_json is None
                or original.impact_sha256 is None
                or original.applied_branch_id is None
            ):
                raise GraphChangeSetUndoUnsupported
            if (
                original.state != "APPLIED"
                or original.applied_graph_revision_id is None
            ):
                raise GraphChangeSetUndoStale
            target = _TARGET_ADAPTER.validate_json(original.application_target_json)
            if not isinstance(target, DirectApplicationTarget):
                raise GraphChangeSetUndoUnsupported
            head = self.branches.head(session, original.applied_branch_id)
            branch = self.branches.branch_for_project(
                session, str(project_id), original.applied_branch_id
            )
            if (
                branch is None
                or head is None
                or head.graph_revision_id != original.applied_graph_revision_id
            ):
                raise GraphChangeSetUndoStale
            pointer = self.branches.selection(session, branch.graph_document_id)
            if pointer is None:
                raise StoredGraphIntegrityError(original.applied_graph_revision_id)
            inverse = json.loads(original.inverse_operations_json)
            if (
                not isinstance(inverse, list)
                or len(inverse) != 1
                or inverse[0].get("op") != "replace_snapshot"
            ):
                raise ApprovalSubjectIntegrityError("graph_change_set", original.id)
            request = CreateGraphChangeSetRequest.model_validate(
                {
                    "base_graph_revision_id": original.applied_graph_revision_id,
                    "client_mutation_id": str(client_mutation_id),
                    "operations": inverse,
                    "application_target": {
                        "mode": "DIRECT",
                        "branch_id": branch.id,
                        "expected_branch_head_revision_id": head.graph_revision_id,
                        "expected_branch_head_version": head.version,
                        "expected_selection_version": pointer.version,
                    },
                }
            )
        return self.request(
            UUID(str(project_id)),
            request,
            actor_id=actor_id,
            actor_kind="HUMAN",
        )
