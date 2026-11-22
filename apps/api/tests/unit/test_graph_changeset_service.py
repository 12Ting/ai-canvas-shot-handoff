import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_changeset_service import GraphChangeSetService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.contracts.graph_changesets import CreateGraphChangeSetRequest
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)
from ai_canvas.infrastructure.db.models import GraphChangeSetRow


def test_graph_change_set_request_persists_proposal_and_event(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("F1 graph proposal")
        events = EventSpineService(session_factory)
        service = GraphChangeSetService(session_factory, GraphService(session_factory), events)

        proposal = service.request(
            project.id,
            CreateGraphChangeSetRequest.model_validate(
                {
                    "kind": "graph_change_set",
                    "schema_version": 1,
                    "base_graph_revision_id": str(project.active_revision_id),
                    "client_mutation_id": str(uuid4()),
                    "operations": [
                        {
                            "op": "replace_snapshot",
                            "snapshot": {
                                "schema_version": 2,
                                "nodes": [],
                                "edges": [],
                                "shots": [],
                            },
                        }
                    ],
                }
            ),
            actor_id="local-session",
        )

        assert proposal.state == "PENDING_APPROVAL"
        assert proposal.base_graph_revision_id == project.active_revision_id
        chain = events.query(project.id, after=0, limit=10, correlation_id=proposal.correlation_id)
        assert [event.event_type for event in chain.events] == ["graph_change_set.requested"]
        assert chain.events[0].subject_id == proposal.id
    finally:
        engine.dispose()


def test_graph_change_set_approval_binds_the_exact_proposal_hash(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("F1 graph approval")
        events = EventSpineService(session_factory)
        service = GraphChangeSetService(session_factory, GraphService(session_factory), events)
        proposal = service.request(
            project.id,
            CreateGraphChangeSetRequest.model_validate(
                {
                    "kind": "graph_change_set",
                    "schema_version": 1,
                    "base_graph_revision_id": str(project.active_revision_id),
                    "client_mutation_id": str(uuid4()),
                    "operations": [
                        {
                            "op": "replace_snapshot",
                            "snapshot": {"schema_version": 2, "nodes": [], "edges": [], "shots": []},
                        }
                    ],
                }
            ),
            actor_id="local-session",
        )

        approved = service.approve(
            project.id,
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="local-session",
        )

        assert approved.state == "APPROVED"
        chain = events.query(project.id, after=0, limit=10, correlation_id=proposal.correlation_id)
        assert [event.event_type for event in chain.events] == [
            "graph_change_set.requested",
            "approval.recorded",
        ]
    finally:
        engine.dispose()


def test_approved_graph_change_set_creates_one_new_graph_revision(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("F1 graph apply")
        graph_service = GraphService(session_factory)
        events = EventSpineService(session_factory)
        service = GraphChangeSetService(session_factory, graph_service, events)
        proposal = service.request(
            project.id,
            CreateGraphChangeSetRequest.model_validate(
                {
                    "kind": "graph_change_set",
                    "schema_version": 1,
                    "base_graph_revision_id": str(project.active_revision_id),
                    "client_mutation_id": str(uuid4()),
                    "operations": [
                        {
                            "op": "replace_snapshot",
                            "snapshot": {"schema_version": 2, "nodes": [], "edges": [], "shots": []},
                        }
                    ],
                }
            ),
            actor_id="local-session",
        )
        service.approve(
            project.id,
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="local-session",
        )

        applied = service.apply(
            project.id,
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="local-session",
        )

        assert applied.state == "APPLIED"
        assert graph_service.get_active(project.id).revision_id != project.active_revision_id
        chain = events.query(project.id, after=0, limit=10, correlation_id=proposal.correlation_id)
        assert [event.event_type for event in chain.events] == [
            "graph_change_set.requested",
            "approval.recorded",
            "graph_branch.head_advanced",
            "graph_change_set.applied",
        ]
    finally:
        engine.dispose()


def test_graph_apply_rejects_content_changed_after_approval(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    try:
        project = ProjectService(session_factory).create("F1 graph integrity")
        graph_service = GraphService(session_factory)
        events = EventSpineService(session_factory)
        service = GraphChangeSetService(session_factory, graph_service, events)
        proposal = service.request(
            project.id,
            CreateGraphChangeSetRequest.model_validate(
                {
                    "kind": "graph_change_set",
                    "schema_version": 1,
                    "base_graph_revision_id": str(project.active_revision_id),
                    "client_mutation_id": str(uuid4()),
                    "operations": [
                        {
                            "op": "replace_snapshot",
                            "snapshot": {
                                "schema_version": 2,
                                "nodes": [],
                                "edges": [],
                                "shots": [],
                            },
                        }
                    ],
                }
            ),
            actor_id="local-session",
        )
        service.approve(
            project.id,
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="local-session",
        )
        with session_factory.begin() as session:
            session.execute(text("DROP TRIGGER f1_graph_change_sets_content_immutable"))
            row = session.get(GraphChangeSetRow, str(proposal.id))
            assert row is not None
            row.snapshot_json = json.dumps(
                {
                    "schema_version": 2,
                    "nodes": [
                        {
                            "id": str(uuid4()),
                            "kind": "note",
                            "position": {"x": 0, "y": 0},
                            "data": {"text": "tampered after approval"},
                        }
                    ],
                    "edges": [],
                    "shots": [],
                },
                separators=(",", ":"),
            )

        with pytest.raises(ValueError, match="integrity"):
            service.apply(
                project.id,
                proposal.id,
                expected_changeset_sha256=proposal.changeset_sha256,
                actor_id="local-session",
            )

        assert graph_service.get_active(project.id).revision_id == project.active_revision_id
        chain = events.query(project.id, after=0, limit=10, correlation_id=proposal.correlation_id)
        assert chain.events[-1].event_type == "graph_change_set.rejected"
        assert chain.events[-1].payload["reason"] == "SUBJECT_INTEGRITY_MISMATCH"
    finally:
        engine.dispose()
