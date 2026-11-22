from pathlib import Path
import socket
import subprocess
from uuid import uuid4

from sqlalchemy import func, select

from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_changeset_service import GraphChangeSetService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.resource_catalog_service import ResourceCatalogService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts.graph import (
    GraphSnapshotV3,
    Point,
    PromptNodeV3,
    ResourceBinding,
    Shot,
    ShotNodeV3,
)
from ai_canvas.contracts.graph_changesets import CreateGraphChangeSetRequest
from ai_canvas.contracts.resources import (
    CharacterResourcePayload,
    CreateResourceRequest,
    CreateResourceRevisionRequest,
    MediaResourcePayload,
    PromptResourcePayload,
    StyleResourcePayload,
)
from ai_canvas.domain.prompting.document import document_from_text
from ai_canvas.infrastructure.db.models import ArtifactRevisionRow, RunRequestRow
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


def _services(database_path: Path):
    engine = create_sqlite_engine(database_path)
    factory = make_session_factory(engine)
    gate = WriteGate()
    events = EventSpineService(factory, write_gate=gate)
    graphs = GraphService(factory, write_gate=gate)
    return (
        engine,
        factory,
        ProjectService(factory, write_gate=gate),
        graphs,
        ResourceCatalogService(factory, events, write_gate=gate),
        GraphChangeSetService(factory, graphs, events, write_gate=gate),
    )


def _proposal(base_revision_id, snapshot: GraphSnapshotV3) -> CreateGraphChangeSetRequest:
    return CreateGraphChangeSetRequest.model_validate(
        {
            "base_graph_revision_id": str(base_revision_id),
            "client_mutation_id": str(uuid4()),
            "operations": [
                {"op": "replace_snapshot", "snapshot": snapshot.model_dump(mode="json")}
            ],
        }
    )


def _apply(changesets, project_id, base_revision_id, snapshot: GraphSnapshotV3):
    proposal = changesets.request(
        project_id,
        _proposal(base_revision_id, snapshot),
        actor_id="human:c1-no-execution",
    )
    changesets.approve(
        project_id,
        proposal.id,
        expected_changeset_sha256=proposal.changeset_sha256,
        actor_id="human:c1-no-execution",
    )
    return changesets.apply(
        project_id,
        proposal.id,
        expected_changeset_sha256=proposal.changeset_sha256,
        actor_id="human:c1-no-execution",
    )


def test_c1_catalog_pins_persist_across_restart_without_execution_or_media(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "c1-no-execution.sqlite3"
    run_migrations(database_path)
    engine, factory, projects, graphs, resources, changesets = _services(database_path)
    project = projects.create("C1 no execution")

    network_calls: list[tuple[object, ...]] = []
    executor_calls: list[tuple[object, ...]] = []

    def forbid_network(*args, **kwargs):
        network_calls.append(args)
        raise AssertionError("C1 metadata flow attempted network access")

    def forbid_executor(*args, **kwargs):
        executor_calls.append(args)
        raise AssertionError("C1 metadata flow attempted process execution")

    monkeypatch.setattr(socket, "create_connection", forbid_network)
    monkeypatch.setattr(subprocess, "Popen", forbid_executor)

    with factory() as session:
        before_run_requests = session.scalar(select(func.count()).select_from(RunRequestRow))
        before_artifact_revisions = session.scalar(
            select(func.count()).select_from(ArtifactRevisionRow)
        )

    style = resources.create_resource(
        project.id,
        CreateResourceRequest(
            name="Look",
            payload=StyleResourcePayload(summary="soft daylight"),
        ),
        actor_id="human:c1-no-execution",
    )
    character = resources.create_resource(
        project.id,
        CreateResourceRequest(
            name="Hero",
            payload=CharacterResourcePayload(
                canonical_name="Gwen",
                identity_anchor="short dark hair",
            ),
        ),
        actor_id="human:c1-no-execution",
    )
    prompt = resources.create_resource(
        project.id,
        CreateResourceRequest(
            name="Prompt rules",
            payload=PromptResourcePayload(
                content_order=["SUBJECT", "SCENE_ACTION"],
                detail_density="MEDIUM",
                format_rules=["one paragraph"],
            ),
        ),
        actor_id="human:c1-no-execution",
    )
    media = resources.create_resource(
        project.id,
        CreateResourceRequest(
            name="Reference usage",
            payload=MediaResourcePayload(usage_notes="composition reference only"),
        ),
        actor_id="human:c1-no-execution",
    )

    shot_id = uuid4()
    shot_node_id = uuid4()
    prompt_node_id = uuid4()
    binding_ids = [uuid4() for _ in range(4)]
    snapshot_one = GraphSnapshotV3(
        nodes=[
            ShotNodeV3(
                id=shot_node_id,
                kind="shot",
                position=Point(x=0, y=0),
                data={"shot_id": shot_id},
            ),
            PromptNodeV3(
                id=prompt_node_id,
                kind="prompt",
                position=Point(x=100, y=0),
                data={"document": document_from_text("原文🙂\n第二行")},
            ),
        ],
        shots=[Shot(id=shot_id, order=0)],
        resource_bindings=[
            ResourceBinding(
                id=binding_ids[0],
                resource_kind="STYLE",
                binding_key="style.primary",
                scope_kind="PROJECT",
                scope_id=None,
                resource_revision_id=style.latest_revision.id,
            ),
            ResourceBinding(
                id=binding_ids[1],
                resource_kind="CHARACTER",
                binding_key="character.primary",
                scope_kind="SHOT",
                scope_id=shot_id,
                resource_revision_id=character.latest_revision.id,
            ),
            ResourceBinding(
                id=binding_ids[2],
                resource_kind="PROMPT",
                binding_key="prompt.rules",
                scope_kind="NODE",
                scope_id=prompt_node_id,
                resource_revision_id=prompt.latest_revision.id,
            ),
            ResourceBinding(
                id=binding_ids[3],
                resource_kind="MEDIA",
                binding_key="media.reference",
                scope_kind="NODE",
                scope_id=shot_node_id,
                resource_revision_id=media.latest_revision.id,
            ),
        ],
    )
    base = graphs.get_active(project.id)
    first_apply = _apply(changesets, project.id, base.revision_id, snapshot_one)
    old_graph_revision_id = first_apply.applied_graph_revision_id
    assert old_graph_revision_id is not None

    style_two = resources.create_revision(
        project.id,
        style.id,
        CreateResourceRevisionRequest(
            base_revision_id=style.latest_revision.id,
            payload=StyleResourcePayload(summary="soft daylight with warm practicals"),
        ),
        actor_id="human:c1-no-execution",
    )
    snapshot_two = snapshot_one.model_copy(
        update={
            "resource_bindings": [
                snapshot_one.resource_bindings[0].model_copy(
                    update={"resource_revision_id": style_two.id}
                ),
                *snapshot_one.resource_bindings[1:],
            ]
        }
    )
    second_apply = _apply(
        changesets,
        project.id,
        old_graph_revision_id,
        snapshot_two,
    )
    new_graph_revision_id = second_apply.applied_graph_revision_id
    assert new_graph_revision_id is not None

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(RunRequestRow)) == before_run_requests
        assert (
            session.scalar(select(func.count()).select_from(ArtifactRevisionRow))
            == before_artifact_revisions
        )
    engine.dispose()

    reopened_engine, reopened_factory, _, reopened_graphs, _, _ = _services(database_path)
    try:
        old_graph = reopened_graphs.get_revision(project.id, old_graph_revision_id)
        new_graph = reopened_graphs.get_active(project.id)
        assert new_graph.revision_id == new_graph_revision_id
        old_style_binding = next(
            binding
            for binding in old_graph.snapshot.resource_bindings
            if binding.id == binding_ids[0]
        )
        new_style_binding = next(
            binding
            for binding in new_graph.snapshot.resource_bindings
            if binding.id == binding_ids[0]
        )
        assert (
            old_style_binding.resource_revision_id == style.latest_revision.id
        )
        assert new_style_binding.resource_revision_id == style_two.id
        restored_prompt = next(
            node
            for node in new_graph.snapshot.nodes
            if node.id == prompt_node_id
        )
        assert restored_prompt.data.document.rendered_text == "原文🙂\n第二行"
        with reopened_factory() as session:
            assert session.scalar(select(func.count()).select_from(RunRequestRow)) == before_run_requests
            assert (
                session.scalar(select(func.count()).select_from(ArtifactRevisionRow))
                == before_artifact_revisions
            )
    finally:
        reopened_engine.dispose()

    assert network_calls == []
    assert executor_calls == []
