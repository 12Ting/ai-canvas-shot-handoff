from pathlib import Path
import socket
import subprocess
from uuid import UUID, uuid4

from sqlalchemy import func, select

from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_changeset_service import GraphChangeSetService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.promotion_service import PromotionService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts.branches import PromotionRequest
from ai_canvas.contracts.graph_changesets import CreateGraphChangeSetRequest
from ai_canvas.infrastructure.db.branch_repository import BranchRepository
from ai_canvas.infrastructure.db.models import (
    ArtifactRevisionRow,
    ArtifactRow,
    EgressPlanRow,
    RunAuthorizationRow,
    RunRequestRow,
)
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


def test_all_c2_mutations_are_local_metadata_only(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "c2-no-execution.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    factory = make_session_factory(engine)
    gate = WriteGate()
    branches = BranchRepository()
    events = EventSpineService(factory, write_gate=gate)
    graphs = GraphService(factory, branches=branches, write_gate=gate)
    projects = ProjectService(factory, branches=branches, write_gate=gate)
    changes = GraphChangeSetService(
        factory,
        graphs,
        events,
        branches=branches,
        write_gate=gate,
    )
    promotions = PromotionService(
        factory,
        graphs,
        events,
        branches=branches,
        write_gate=gate,
    )
    project = projects.create("C2 no execution")

    def forbid_network(*_args, **_kwargs):
        raise AssertionError("C2 branch metadata attempted network access")

    def forbid_process(*_args, **_kwargs):
        raise AssertionError("C2 branch metadata attempted process execution")

    monkeypatch.setattr(socket, "create_connection", forbid_network)
    monkeypatch.setattr(subprocess, "Popen", forbid_process)

    counted_models = (
        RunRequestRow,
        EgressPlanRow,
        RunAuthorizationRow,
        ArtifactRow,
        ArtifactRevisionRow,
    )
    with factory() as session:
        before = {
            model.__name__: session.scalar(select(func.count()).select_from(model))
            for model in counted_models
        }
        main = branches.branches_for_document(
            session, str(project.graph_document_id)
        )[0]
        main_head = branches.head(session, main.id)
        pointer = branches.selection(session, str(project.graph_document_id))
        assert main_head is not None and pointer is not None

    def apply_change(request: CreateGraphChangeSetRequest):
        proposal = changes.request(
            project.id, request, actor_id="human:c2-no-execution"
        )
        changes.approve(
            project.id,
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="human:c2-no-execution",
        )
        return proposal, changes.apply(
            project.id,
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="human:c2-no-execution",
        )

    _, created = apply_change(
        CreateGraphChangeSetRequest.model_validate(
            {
                "base_graph_revision_id": main_head.graph_revision_id,
                "client_mutation_id": str(uuid4()),
                "operations": [
                    {"op": "replace_snapshot", "snapshot": _snapshot("branch")}
                ],
                "application_target": {
                    "mode": "NEW_BRANCH",
                    "base_branch_id": main.id,
                    "branch_name": "离线探索",
                    "expected_branch_head_revision_id": main_head.graph_revision_id,
                    "expected_branch_head_version": main_head.version,
                    "expected_selection_version": pointer.version,
                },
            }
        )
    )
    assert created.created_branch_id is not None

    with factory() as session:
        experiment_head = branches.head(session, str(created.created_branch_id))
        pointer = branches.selection(session, str(project.graph_document_id))
        assert experiment_head is not None and pointer is not None
    direct_proposal, direct = apply_change(
        CreateGraphChangeSetRequest.model_validate(
            {
                "base_graph_revision_id": experiment_head.graph_revision_id,
                "client_mutation_id": str(uuid4()),
                "operations": [
                    {"op": "replace_snapshot", "snapshot": _snapshot("edited")}
                ],
                "application_target": {
                    "mode": "DIRECT",
                    "branch_id": str(created.created_branch_id),
                    "expected_branch_head_revision_id": experiment_head.graph_revision_id,
                    "expected_branch_head_version": experiment_head.version,
                    "expected_selection_version": pointer.version,
                },
            }
        )
    )

    with factory() as session:
        experiment_head = branches.head(session, str(created.created_branch_id))
        pointer = branches.selection(session, str(project.graph_document_id))
        assert experiment_head is not None and pointer is not None
    promotion = promotions.request(
        project.id,
        PromotionRequest(
            client_mutation_id=uuid4(),
            target_branch_id=created.created_branch_id,
            expected_target_branch_head_revision_id=UUID(
                experiment_head.graph_revision_id
            ),
            expected_target_branch_head_version=experiment_head.version,
            expected_selection_version=pointer.version,
        ),
        actor_id="human:c2-no-execution",
    )
    promotions.approve(
        project.id,
        promotion.id,
        expected_promotion_sha256=promotion.promotion_sha256,
        actor_id="human:c2-no-execution",
        decision="APPROVED",
    )
    promotions.apply(
        project.id,
        promotion.id,
        expected_promotion_sha256=promotion.promotion_sha256,
        actor_id="human:c2-no-execution",
    )
    reverse = promotions.undo_proposal(
        project.id,
        promotion.id,
        client_mutation_id=uuid4(),
        actor_id="human:c2-no-execution",
    )
    promotions.approve(
        project.id,
        reverse.id,
        expected_promotion_sha256=reverse.promotion_sha256,
        actor_id="human:c2-no-execution",
        decision="APPROVED",
    )
    promotions.apply(
        project.id,
        reverse.id,
        expected_promotion_sha256=reverse.promotion_sha256,
        actor_id="human:c2-no-execution",
    )

    undo = changes.undo_proposal(
        project.id,
        direct_proposal.id,
        client_mutation_id=uuid4(),
        actor_id="human:c2-no-execution",
    )
    changes.approve(
        project.id,
        undo.id,
        expected_changeset_sha256=undo.changeset_sha256,
        actor_id="human:c2-no-execution",
    )
    changes.apply(
        project.id,
        undo.id,
        expected_changeset_sha256=undo.changeset_sha256,
        actor_id="human:c2-no-execution",
    )

    assert direct.applied_graph_revision_id is not None
    with factory() as session:
        after = {
            model.__name__: session.scalar(select(func.count()).select_from(model))
            for model in counted_models
        }
    engine.dispose()
    assert after == before
