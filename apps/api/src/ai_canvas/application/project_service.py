from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.errors import GraphRevisionNotFound, ProjectNotFound
from ai_canvas.application.graph_service import canonical_snapshot
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts.graph import GraphSnapshotV2, ViewportState
from ai_canvas.contracts.project import ProjectResponse
from ai_canvas.infrastructure.db.models import (
    GraphBranchHeadRow,
    GraphBranchRow,
    GraphDocumentRow,
    HandoffProjectRow,
    GraphRevisionRow,
    GraphSelectionPointerRow,
    ProjectRow,
    ProjectViewStateRow,
)
from ai_canvas.infrastructure.db.branch_repository import BranchRepository
from ai_canvas.infrastructure.db.repositories import GraphRepository, ProjectRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProjectService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        projects: ProjectRepository | None = None,
        graphs: GraphRepository | None = None,
        branches: BranchRepository | None = None,
        write_gate: WriteGate | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.projects = projects or ProjectRepository()
        self.graphs = graphs or GraphRepository()
        self.branches = branches or BranchRepository()
        self._write_gate = write_gate or WriteGate()

    def create(
        self, name: str, *, identifier: UUID | None = None,
        initialize: Callable[[Session, str], None] | None = None,
    ) -> ProjectResponse:
        name = name.strip()
        if not name or len(name) > 200:
            raise ValueError("project name must contain 1 to 200 non-whitespace characters")
        project_id = str(identifier or uuid4())
        document_id = str(uuid4())
        revision_id = str(uuid4())
        branch_id = str(uuid4())
        now = utc_now()
        snapshot_json, snapshot_hash = canonical_snapshot(GraphSnapshotV2())
        with self._write_gate:
            with self.session_factory.begin() as session:
                project = ProjectRow(id=project_id, name=name, created_at=now, updated_at=now)
                self.projects.add(session, project)
                session.flush()
                document = GraphDocumentRow(
                    id=document_id,
                    project_id=project_id,
                    active_revision_id=None,
                    created_at=now,
                )
                self.graphs.add_document(session, document)
                session.flush()
                revision = GraphRevisionRow(
                    id=revision_id,
                    graph_document_id=document_id,
                    parent_revision_id=None,
                    sequence=0,
                    client_mutation_id=str(uuid4()),
                    snapshot_json=snapshot_json,
                    snapshot_hash=snapshot_hash,
                    created_at=now,
                )
                self.graphs.add_revision(session, revision)
                session.flush()
                self.branches.create_branch_with_head(
                    session,
                    GraphBranchRow(
                        id=branch_id,
                        graph_document_id=document_id,
                        name="主线",
                        normalized_name="主线",
                        kind="MAIN",
                        forked_from_branch_id=None,
                        forked_from_revision_id=revision_id,
                        created_at=now,
                    ),
                    GraphBranchHeadRow(
                        branch_id=branch_id,
                        graph_revision_id=revision_id,
                        version=1,
                        updated_at=now,
                    ),
                )
                session.flush()
                session.add(
                    GraphSelectionPointerRow(
                        graph_document_id=document_id,
                        branch_id=branch_id,
                        graph_revision_id=revision_id,
                        version=1,
                        updated_at=now,
                    )
                )
                document.active_revision_id = revision_id
                self.graphs.add_view_state(
                    session,
                    ProjectViewStateRow(
                        project_id=project_id,
                        viewport_json=ViewportState().model_dump_json(),
                        updated_at=now,
                    ),
                )
                if initialize is not None:
                    initialize(session, project_id)
        return ProjectResponse(
            id=UUID(project_id),
            name=name,
            graph_document_id=UUID(document_id),
            active_revision_id=UUID(revision_id),
            selected_branch_id=UUID(branch_id),
            selection_version=1,
            handoff_enabled=initialize is not None,
        )

    def get(self, project_id: UUID) -> ProjectResponse:
        with self.session_factory() as session:
            project = self.projects.get(session, str(project_id))
            document = self.graphs.document_for_project(session, str(project_id))
            if project is None or document is None or document.active_revision_id is None:
                raise ProjectNotFound(str(project_id))
            revision = self.graphs.revision(session, document.active_revision_id)
            if revision is None or revision.graph_document_id != document.id:
                raise GraphRevisionNotFound(document.active_revision_id)
            selection = self.branches.selection(session, document.id)
            if selection is None or selection.graph_revision_id != document.active_revision_id:
                raise GraphRevisionNotFound(document.active_revision_id)
            return ProjectResponse(
                id=UUID(project.id),
                name=project.name,
                graph_document_id=UUID(document.id),
                active_revision_id=UUID(document.active_revision_id),
                selected_branch_id=UUID(selection.branch_id),
                selection_version=selection.version,
                handoff_enabled=session.get(HandoffProjectRow, str(project_id)) is not None,
            )

    def list(self) -> list[ProjectResponse]:
        with self.session_factory() as session:
            enabled = set(session.scalars(select(HandoffProjectRow.project_id)))
            responses: list[ProjectResponse] = []
            for project in self.projects.list_all(session):
                document = self.graphs.document_for_project(session, project.id)
                if document is None or document.active_revision_id is None:
                    continue
                revision = self.graphs.revision(session, document.active_revision_id)
                if revision is None or revision.graph_document_id != document.id:
                    continue
                selection = self.branches.selection(session, document.id)
                if selection is None or selection.graph_revision_id != document.active_revision_id:
                    continue
                responses.append(
                    ProjectResponse(
                        id=UUID(project.id),
                        name=project.name,
                        graph_document_id=UUID(document.id),
                        active_revision_id=UUID(document.active_revision_id),
                        selected_branch_id=UUID(selection.branch_id),
                        selection_version=selection.version,
                        handoff_enabled=project.id in enabled,
                    )
                )
            return responses
