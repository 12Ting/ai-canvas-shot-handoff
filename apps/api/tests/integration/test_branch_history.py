from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.infrastructure.db.models import GraphRevisionRow
from ai_canvas.infrastructure.db.repositories import GraphRepository
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


def test_revision_sequence_is_document_global_and_ancestry_excludes_siblings(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    factory = make_session_factory(engine)
    project = ProjectService(factory, write_gate=WriteGate()).create("History")
    repository = GraphRepository()
    try:
        with factory.begin() as session:
            document = repository.document_for_project(session, str(project.id))
            assert document is not None
            parent_id = document.active_revision_id
            assert parent_id is not None
            first_id = str(uuid4())
            second_id = str(uuid4())
            for revision_id, sequence in ((first_id, 1), (second_id, 2)):
                repository.add_revision(
                    session,
                    GraphRevisionRow(
                        id=revision_id,
                        graph_document_id=document.id,
                        parent_revision_id=parent_id,
                        sequence=sequence,
                        client_mutation_id=str(uuid4()),
                        snapshot_json='{"edges":[],"nodes":[],"resource_bindings":[],"schema_version":3,"shots":[]}',
                        snapshot_hash="a" * 64,
                        created_at=datetime.now(timezone.utc),
                    ),
                )
            session.flush()
            assert repository.next_sequence(session, document.id) == 3
            ancestors = repository.ancestor_revisions(
                session, document.id, second_id, limit=10
            )
        assert [row.id for row in ancestors] == [second_id, parent_id]
        assert first_id not in {row.id for row in ancestors}
    finally:
        engine.dispose()
