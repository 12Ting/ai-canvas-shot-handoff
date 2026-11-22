from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.errors import (
    GraphBranchNotFound,
    ProjectNotFound,
    StoredGraphIntegrityError,
)
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.promotion_service import as_utc, promotion_response
from ai_canvas.contracts.branches import (
    BranchGraphResponse,
    BranchHeadResponse,
    BranchSummary,
    BranchWorkspaceResponse,
    MaterializationSummary,
    SelectionPointerResponse,
)
from ai_canvas.infrastructure.db.branch_repository import BranchRepository
from ai_canvas.infrastructure.db.models import GraphBranchRow, GraphSelectionPointerRow
from ai_canvas.infrastructure.db.repositories import GraphRepository


class BranchService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        graph_service: GraphService,
        *,
        branches: BranchRepository | None = None,
        graphs: GraphRepository | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.graph_service = graph_service
        self.branches = branches or BranchRepository()
        self.graphs = graphs or GraphRepository()

    def _selection_response(
        self, pointer: GraphSelectionPointerRow
    ) -> SelectionPointerResponse:
        return SelectionPointerResponse(
            graph_document_id=UUID(pointer.graph_document_id),
            branch_id=UUID(pointer.branch_id),
            graph_revision_id=UUID(pointer.graph_revision_id),
            version=pointer.version,
        )

    def _summary(
        self,
        session: Session,
        branch: GraphBranchRow,
        pointer: GraphSelectionPointerRow,
    ) -> BranchSummary:
        head = self.branches.head(session, branch.id)
        if head is None:
            raise StoredGraphIntegrityError(branch.forked_from_revision_id)
        revision = self.graphs.revision(session, head.graph_revision_id)
        if revision is None or revision.graph_document_id != branch.graph_document_id:
            raise StoredGraphIntegrityError(head.graph_revision_id)
        return BranchSummary(
            id=UUID(branch.id),
            graph_document_id=UUID(branch.graph_document_id),
            name=branch.name,
            kind=branch.kind,
            forked_from_branch_id=(
                None
                if branch.forked_from_branch_id is None
                else UUID(branch.forked_from_branch_id)
            ),
            forked_from_revision_id=UUID(branch.forked_from_revision_id),
            head=BranchHeadResponse(
                graph_revision_id=UUID(head.graph_revision_id),
                version=head.version,
                graph_revision_sha256=revision.snapshot_hash,
            ),
            created_at=as_utc(branch.created_at),
            is_selected=pointer.branch_id == branch.id,
        )

    def workspace(self, project_id: UUID) -> BranchWorkspaceResponse:
        with self.session_factory() as session:
            document = self.graphs.document_for_project(session, str(project_id))
            if document is None:
                raise ProjectNotFound(str(project_id))
            pointer = self.branches.selection(session, document.id)
            if pointer is None or pointer.graph_revision_id != document.active_revision_id:
                raise StoredGraphIntegrityError(document.active_revision_id or document.id)
            branches = self.branches.branches_for_document(session, document.id)
            if not branches or pointer.branch_id not in {branch.id for branch in branches}:
                raise StoredGraphIntegrityError(pointer.graph_revision_id)
            latest = self.branches.effective_latest_promotion(
                session, str(project_id)
            )
            can_undo = False
            if latest is not None:
                current_head = self.branches.head(session, latest.target_branch_id)
                restore_head = self.branches.head(session, latest.source_branch_id)
                can_undo = (
                    pointer.branch_id == latest.target_branch_id
                    and pointer.graph_revision_id == latest.target_graph_revision_id
                    and pointer.version == latest.expected_selection_version + 1
                    and current_head is not None
                    and current_head.graph_revision_id
                    == latest.target_graph_revision_id
                    and current_head.version == latest.target_branch_head_version
                    and restore_head is not None
                    and restore_head.graph_revision_id
                    == latest.source_graph_revision_id
                    and restore_head.version == latest.source_branch_head_version
                )
            return BranchWorkspaceResponse(
                project_id=project_id,
                graph_document_id=UUID(document.id),
                branches=[self._summary(session, branch, pointer) for branch in branches],
                selection=self._selection_response(pointer),
                latest_promotion=(
                    promotion_response(latest) if latest is not None else None
                ),
                can_undo_latest_promotion=can_undo,
            )

    def graph_at_head(self, project_id: UUID, branch_id: UUID) -> BranchGraphResponse:
        with self.session_factory() as session:
            document = self.graphs.document_for_project(session, str(project_id))
            if document is None:
                raise ProjectNotFound(str(project_id))
            branch = self.branches.branch_for_project(
                session, str(project_id), str(branch_id)
            )
            if branch is None:
                raise GraphBranchNotFound(str(branch_id))
            pointer = self.branches.selection(session, document.id)
            if pointer is None:
                raise StoredGraphIntegrityError(document.id)
            summary = self._summary(session, branch, pointer)
            revision_id = summary.head.graph_revision_id
            rows = self.branches.materialization_rows(session, str(revision_id))
        revision = self.graph_service.get_revision(project_id, revision_id)
        state_counts = {"CURRENT": 0, "STALE": 0}
        for row in rows:
            state_counts[row.state] += 1
        return BranchGraphResponse(
            branch=summary,
            selection=self._selection_response(pointer),
            revision=revision,
            materialization=MaterializationSummary(
                unmaterialized=max(
                    0, len(revision.snapshot.nodes) - len(rows)
                ),
                current=state_counts["CURRENT"],
                stale=state_counts["STALE"],
            ),
        )
