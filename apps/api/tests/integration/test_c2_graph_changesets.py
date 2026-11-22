from pathlib import Path
from uuid import uuid4

import pytest

from ai_canvas.application.errors import BranchHeadStale, SelectionPointerStale
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_changeset_service import GraphChangeSetService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts.graph_changesets import CreateGraphChangeSetRequest
from ai_canvas.infrastructure.db.branch_repository import BranchRepository
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


def _snapshot(text: str) -> dict[str, object]:
    return {
        "schema_version": 2,
        "nodes": [
            {
                "id": "00000000-0000-4000-8000-000000000101",
                "kind": "note",
                "position": {"x": 0, "y": 0},
                "data": {"text": text},
            }
        ],
        "edges": [],
        "shots": [],
    }


def _services(tmp_path: Path):
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    factory = make_session_factory(engine)
    gate = WriteGate()
    branches = BranchRepository()
    projects = ProjectService(factory, branches=branches, write_gate=gate)
    graphs = GraphService(factory, branches=branches, write_gate=gate)
    events = EventSpineService(factory, write_gate=gate)
    changes = GraphChangeSetService(
        factory,
        graphs,
        events,
        branches=branches,
        write_gate=gate,
    )
    return engine, factory, projects, graphs, events, changes, branches


def test_direct_targets_selected_branch_and_advances_all_three_pointers(
    tmp_path: Path,
) -> None:
    engine, factory, projects, graphs, _events, changes, branches = _services(tmp_path)
    try:
        project = projects.create("Direct")
        with factory() as session:
            branch = branches.branches_for_document(
                session, str(project.graph_document_id)
            )[0]
            head = branches.head(session, branch.id)
            assert head is not None
        proposal = changes.request(
            project.id,
            CreateGraphChangeSetRequest.model_validate(
                {
                    "base_graph_revision_id": str(project.active_revision_id),
                    "client_mutation_id": str(uuid4()),
                    "operations": [{"op": "replace_snapshot", "snapshot": _snapshot("direct")}],
                    "application_target": {
                        "mode": "DIRECT",
                        "branch_id": branch.id,
                        "expected_branch_head_revision_id": head.graph_revision_id,
                        "expected_branch_head_version": head.version,
                        "expected_selection_version": 1,
                    },
                }
            ),
            actor_id="local-session",
        )
        assert proposal.application_target.mode == "DIRECT"
        assert proposal.impact.confirmation_required is False
        assert proposal.impact.items[0].projected_state == "UNMATERIALIZED"
        changes.approve(
            project.id,
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="local-session",
        )
        applied = changes.apply(
            project.id,
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="local-session",
        )
        assert str(applied.applied_branch_id) == branch.id
        assert applied.applied_graph_revision_id is not None
        assert graphs.get_active(project.id).revision_id == applied.applied_graph_revision_id
        with factory() as session:
            current_head = branches.head(session, branch.id)
            pointer = branches.selection(session, str(project.graph_document_id))
            assert current_head is not None and pointer is not None
            assert current_head.graph_revision_id == str(applied.applied_graph_revision_id)
            assert pointer.graph_revision_id == str(applied.applied_graph_revision_id)
    finally:
        engine.dispose()


def test_new_branch_creates_an_editable_experiment_without_moving_selection(
    tmp_path: Path,
) -> None:
    engine, factory, projects, graphs, _events, changes, branches = _services(tmp_path)
    try:
        project = projects.create("Experiment")
        with factory() as session:
            main = branches.branches_for_document(
                session, str(project.graph_document_id)
            )[0]
            head = branches.head(session, main.id)
            assert head is not None
        proposal = changes.request(
            project.id,
            CreateGraphChangeSetRequest.model_validate(
                {
                    "base_graph_revision_id": str(project.active_revision_id),
                    "client_mutation_id": str(uuid4()),
                    "operations": [{"op": "replace_snapshot", "snapshot": _snapshot("branch")}],
                    "application_target": {
                        "mode": "NEW_BRANCH",
                        "base_branch_id": main.id,
                        "branch_name": "探索 A",
                        "expected_branch_head_revision_id": head.graph_revision_id,
                        "expected_branch_head_version": head.version,
                        "expected_selection_version": 1,
                    },
                }
            ),
            actor_id="local-session",
        )
        changes.approve(
            project.id,
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="local-session",
        )
        applied = changes.apply(
            project.id,
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="local-session",
        )
        assert applied.created_branch_id is not None
        assert graphs.get_active(project.id).revision_id == project.active_revision_id
        with factory() as session:
            all_branches = branches.branches_for_document(
                session, str(project.graph_document_id)
            )
            pointer = branches.selection(session, str(project.graph_document_id))
            created = branches.branch(session, str(applied.created_branch_id))
            created_head = branches.head(session, str(applied.created_branch_id))
            assert len(all_branches) == 2
            assert pointer is not None and pointer.branch_id == main.id
            assert created is not None and created.kind == "EXPERIMENT"
            assert created_head is not None
            assert created_head.graph_revision_id == str(applied.applied_graph_revision_id)
    finally:
        engine.dispose()


def test_explicit_branch_target_reports_selection_and_head_conflicts_separately(
    tmp_path: Path,
) -> None:
    engine, factory, projects, _graphs, _events, changes, branches = _services(tmp_path)
    try:
        project = projects.create("Conflicts")
        with factory() as session:
            main = branches.branches_for_document(
                session, str(project.graph_document_id)
            )[0]
            head = branches.head(session, main.id)
            assert head is not None

        def request(*, head_version: int, selection_version: int):
            return changes.request(
                project.id,
                CreateGraphChangeSetRequest.model_validate(
                    {
                        "base_graph_revision_id": str(project.active_revision_id),
                        "client_mutation_id": str(uuid4()),
                        "operations": [
                            {"op": "replace_snapshot", "snapshot": _snapshot("conflict")}
                        ],
                        "application_target": {
                            "mode": "DIRECT",
                            "branch_id": main.id,
                            "expected_branch_head_revision_id": head.graph_revision_id,
                            "expected_branch_head_version": head_version,
                            "expected_selection_version": selection_version,
                        },
                    }
                ),
                actor_id="local-session",
            )

        with pytest.raises(SelectionPointerStale) as selection_error:
            request(head_version=head.version, selection_version=2)
        assert selection_error.value.current_branch_id == main.id
        assert selection_error.value.current_revision_id == head.graph_revision_id
        assert selection_error.value.current_version == 1

        with pytest.raises(BranchHeadStale) as head_error:
            request(head_version=head.version + 1, selection_version=1)
        assert head_error.value.branch_id == main.id
        assert head_error.value.current_revision_id == head.graph_revision_id
        assert head_error.value.current_version == head.version
    finally:
        engine.dispose()
