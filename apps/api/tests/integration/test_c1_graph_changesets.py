from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from ai_canvas.application.errors import SelectionPointerStale
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_changeset_service import GraphChangeSetService
from ai_canvas.application.graph_service import GraphService, canonical_snapshot
from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.resource_catalog_service import ResourceCatalogService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts.graph import (
    CreateGraphRevisionRequest,
    GraphSnapshotV3,
    Point,
    PromptNodeDataV3,
    PromptNodeV3,
    ResourceBinding,
    Shot,
    ShotNodeV3,
)
from ai_canvas.contracts.graph_changesets import CreateGraphChangeSetRequest
from ai_canvas.contracts.prompts import PromptDocumentV1
from ai_canvas.contracts.resources import (
    CharacterResourcePayload,
    CreateResourceRequest,
    CreateResourceRevisionRequest,
    StyleResourcePayload,
)
from ai_canvas.domain.prompting.document import document_from_text
from ai_canvas.infrastructure.db.models import GraphChangeSetRow, GraphRevisionRow
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


@dataclass
class C1ChangeSetServices:
    projects: ProjectService
    graphs: GraphService
    events: EventSpineService
    resources: ResourceCatalogService
    changesets: GraphChangeSetService


@pytest.fixture
def c1_changesets(tmp_path: Path) -> Iterator[C1ChangeSetServices]:
    database_path = tmp_path / "changesets.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    factory = make_session_factory(engine)
    gate = WriteGate()
    events = EventSpineService(factory, write_gate=gate)
    graphs = GraphService(factory, write_gate=gate)
    try:
        yield C1ChangeSetServices(
            projects=ProjectService(factory, write_gate=gate),
            graphs=graphs,
            events=events,
            resources=ResourceCatalogService(factory, events, write_gate=gate),
            changesets=GraphChangeSetService(
                factory,
                graphs,
                events,
                write_gate=gate,
            ),
        )
    finally:
        engine.dispose()


def _proposal_request(base_revision_id, snapshot: GraphSnapshotV3) -> CreateGraphChangeSetRequest:
    return CreateGraphChangeSetRequest.model_validate(
        {
            "base_graph_revision_id": str(base_revision_id),
            "client_mutation_id": str(uuid4()),
            "operations": [
                {
                    "op": "replace_snapshot",
                    "snapshot": snapshot.model_dump(mode="json"),
                }
            ],
        }
    )


def test_c1_diff_and_apply_keep_prompt_text_out_of_review_and_events(
    c1_changesets: C1ChangeSetServices,
) -> None:
    services = c1_changesets
    project = services.projects.create("C1 changes")
    character = services.resources.create_resource(
        project.id,
        CreateResourceRequest(
            name="Hero",
            payload=CharacterResourcePayload(
                canonical_name="Gwen",
                identity_anchor="short dark hair",
            ),
        ),
        actor_id="human:test",
    )
    character_two = services.resources.create_revision(
        project.id,
        character.id,
        CreateResourceRevisionRequest(
            base_revision_id=character.latest_revision.id,
            payload=CharacterResourcePayload(
                canonical_name="Gwen",
                identity_anchor="short dark hair and blue eyes",
            ),
        ),
        actor_id="human:test",
    )
    style = services.resources.create_resource(
        project.id,
        CreateResourceRequest(
            name="Look",
            payload=StyleResourcePayload(summary="low saturation"),
        ),
        actor_id="human:test",
    )
    shot_id = uuid4()
    shot_node_id = uuid4()
    prompt_node_id = uuid4()
    binding_id = uuid4()
    removed_binding_id = uuid4()
    base_document = document_from_text("秘密原文🙂")
    base_snapshot = GraphSnapshotV3(
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
                data={"document": base_document},
            ),
        ],
        shots=[Shot(id=shot_id, order=0)],
        resource_bindings=[
            ResourceBinding(
                id=binding_id,
                resource_kind="CHARACTER",
                binding_key="character.primary",
                scope_kind="SHOT",
                scope_id=shot_id,
                resource_revision_id=character.latest_revision.id,
            ),
            ResourceBinding(
                id=removed_binding_id,
                resource_kind="STYLE",
                binding_key="style.old",
                scope_kind="PROJECT",
                scope_id=None,
                resource_revision_id=style.latest_revision.id,
            ),
        ],
    )
    initial = services.graphs.get_active(project.id)
    base = services.graphs.create_revision(
        project.id,
        CreateGraphRevisionRequest(
            base_revision_id=initial.revision_id,
            client_mutation_id=uuid4(),
            snapshot=base_snapshot,
        ),
    )
    locked_block = base_document.blocks[0].model_copy(update={"locked": True})
    locked_document = PromptDocumentV1(
        blocks=[locked_block],
        rendered_text=base_document.rendered_text,
        spans=base_document.spans,
    )
    added_binding_id = uuid4()
    proposed_snapshot = base_snapshot.model_copy(
        update={
            "nodes": [
                base_snapshot.nodes[0],
                base_snapshot.nodes[1].model_copy(
                    update={"data": PromptNodeDataV3(document=locked_document)}
                ),
            ],
            "resource_bindings": [
                base_snapshot.resource_bindings[0].model_copy(
                    update={"resource_revision_id": character_two.id}
                ),
                ResourceBinding(
                    id=added_binding_id,
                    resource_kind="STYLE",
                    binding_key="style.node",
                    scope_kind="NODE",
                    scope_id=prompt_node_id,
                    resource_revision_id=style.latest_revision.id,
                ),
            ],
        }
    )
    old_canonical = canonical_snapshot(base_snapshot)

    proposal = services.changesets.request(
        project.id,
        _proposal_request(base.revision_id, proposed_snapshot),
        actor_id="human:test",
    )
    diff = proposal.diff.model_dump(mode="json")

    assert diff["resource_bindings_updated"] == [
        {
            "binding_id": str(binding_id),
            "scope_kind": "SHOT",
            "scope_id": str(shot_id),
            "binding_key": "character.primary",
            "old_resource_revision_id": str(character.latest_revision.id),
            "new_resource_revision_id": str(character_two.id),
        }
    ]
    assert [item["binding_id"] for item in diff["resource_bindings_added"]] == [
        str(added_binding_id)
    ]
    assert [item["binding_id"] for item in diff["resource_bindings_removed"]] == [
        str(removed_binding_id)
    ]
    prompt_diff = diff["prompt_documents_changed"]
    assert prompt_diff[0]["node_id"] == str(prompt_node_id)
    assert prompt_diff[0]["old_locked_block_count"] == 0
    assert prompt_diff[0]["new_locked_block_count"] == 1
    assert "秘密原文" not in str(diff)
    assert proposal.risk_summary == {
        "classes": [
                "GRAPH_WRITE",
                "DIRECT_BRANCH_ADVANCE",
                "RESOURCE_PIN_CHANGE",
            "LOCKED_PROMPT_BLOCK_CHANGE",
        ]
    }

    services.changesets.approve(
        project.id,
        proposal.id,
        expected_changeset_sha256=proposal.changeset_sha256,
        actor_id="human:test",
    )
    applied = services.changesets.apply(
        project.id,
        proposal.id,
        expected_changeset_sha256=proposal.changeset_sha256,
        actor_id="human:test",
    )

    assert applied.state == "APPLIED"
    assert canonical_snapshot(services.graphs.get_revision(project.id, base.revision_id).snapshot) == old_canonical
    assert services.graphs.get_active(project.id).revision_id == applied.applied_graph_revision_id
    chain = services.events.query(
        project.id,
        after=0,
        limit=100,
        correlation_id=proposal.correlation_id,
    )
    assert [event.event_type for event in chain.events] == [
        "graph_change_set.requested",
        "approval.recorded",
        "graph_branch.head_advanced",
        "graph_change_set.applied",
    ]
    assert "秘密原文" not in str([event.payload for event in chain.events])


def test_missing_resource_revision_is_rejected_before_proposal_persistence(
    c1_changesets: C1ChangeSetServices,
) -> None:
    services = c1_changesets
    project = services.projects.create("Missing pin")
    active = services.graphs.get_active(project.id)
    invalid = GraphSnapshotV3(
        resource_bindings=[
            ResourceBinding(
                id=uuid4(),
                resource_kind="STYLE",
                binding_key="look",
                scope_kind="PROJECT",
                scope_id=None,
                resource_revision_id=uuid4(),
            )
        ]
    )

    with pytest.raises(ValueError, match="resource revision is missing"):
        services.changesets.request(
            project.id,
            _proposal_request(active.revision_id, invalid),
            actor_id="human:test",
        )

    with services.projects.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(GraphChangeSetRow)) == 0


def test_stale_c1_proposal_is_rejected_without_an_extra_graph_revision(
    c1_changesets: C1ChangeSetServices,
) -> None:
    services = c1_changesets
    project = services.projects.create("Stale C1")
    active = services.graphs.get_active(project.id)
    snapshot = GraphSnapshotV3()
    proposal = services.changesets.request(
        project.id,
        _proposal_request(active.revision_id, snapshot),
        actor_id="human:test",
    )
    services.changesets.approve(
        project.id,
        proposal.id,
        expected_changeset_sha256=proposal.changeset_sha256,
        actor_id="human:test",
    )
    newer = services.graphs.create_revision(
        project.id,
        CreateGraphRevisionRequest(
            base_revision_id=active.revision_id,
            client_mutation_id=uuid4(),
            snapshot=snapshot,
        ),
    )
    with services.projects.session_factory() as session:
        before = session.scalar(select(func.count()).select_from(GraphRevisionRow))

    with pytest.raises(SelectionPointerStale) as stale_error:
        services.changesets.apply(
            project.id,
            proposal.id,
            expected_changeset_sha256=proposal.changeset_sha256,
            actor_id="human:test",
        )
    assert stale_error.value.current_revision_id == str(newer.revision_id)

    with services.projects.session_factory() as session:
        after = session.scalar(select(func.count()).select_from(GraphRevisionRow))
    assert after == before
    assert services.graphs.get_active(project.id).revision_id == newer.revision_id
