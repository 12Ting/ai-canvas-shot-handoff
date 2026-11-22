from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.infrastructure.db.branch_repository import BranchRepository
from ai_canvas.infrastructure.db.models import GraphRevisionRow
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


def test_head_and_selection_cas_return_current_state_when_stale(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    factory = make_session_factory(engine)
    gate = WriteGate()
    project = ProjectService(factory, write_gate=gate).create("C2")
    repository = BranchRepository()
    try:
        with factory.begin() as session:
            branch = repository.branches_for_document(
                session, str(project.graph_document_id)
            )[0]
            head = repository.head(session, branch.id)
            pointer = repository.selection(session, str(project.graph_document_id))
            assert head is not None
            assert pointer is not None
            child_id = str(uuid4())
            session.add(
                GraphRevisionRow(
                    id=child_id,
                    graph_document_id=str(project.graph_document_id),
                    parent_revision_id=head.graph_revision_id,
                    sequence=1,
                    client_mutation_id=str(uuid4()),
                    snapshot_json='{"schema_version":2,"nodes":[],"edges":[],"shots":[],"resource_bindings":[]}',
                    snapshot_hash="a" * 64,
                    created_at=datetime.now(timezone.utc),
                )
            )
            session.flush()
            stale_head = repository.advance_head(
                session,
                branch_id=branch.id,
                expected_revision_id=head.graph_revision_id,
                expected_version=0,
                new_revision_id=child_id,
            )
            stale_pointer = repository.move_selection(
                session,
                graph_document_id=str(project.graph_document_id),
                expected_branch_id=pointer.branch_id,
                expected_revision_id=pointer.graph_revision_id,
                expected_version=0,
                new_branch_id=branch.id,
                new_revision_id=child_id,
            )
        assert stale_head.applied is False
        assert stale_head.current.version == 1
        assert stale_pointer.applied is False
        assert stale_pointer.current.version == 1
    finally:
        engine.dispose()
