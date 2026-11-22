from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.errors import (
    ClientMutationReuse,
    GraphRevisionConflict,
    GraphRevisionNotFound,
    ProjectNotFound,
)
from ai_canvas.application.graph_service import GraphService, canonical_snapshot
from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts import graph as graph_contracts
from ai_canvas.contracts.graph import (
    CreateGraphRevisionRequest,
    GraphEdge,
    GraphNode,
    GraphSnapshotV1,
    GraphSnapshotV2,
    GraphSnapshotV3,
    MediaInputData,
    MediaInputNodeV2,
    Point,
    ResourceBinding,
    Shot,
    ShotNodeData,
    ShotNodeV2,
    ViewportState,
)
from ai_canvas.domain.graph.validation import GraphValidationError
from ai_canvas.infrastructure.db.models import (
    ArtifactRevisionRow,
    ArtifactRow,
    GraphDocumentRow,
    GraphRevisionRow,
    ProjectRow,
    ProjectViewStateRow,
    ResourceRevisionArtifactRow,
    ResourceRevisionRow,
    ResourceRow,
    StoredBlobRow,
)
from ai_canvas.infrastructure.db.repositories import GraphRepository
from ai_canvas.infrastructure.db.session import create_sqlite_engine, make_session_factory, run_migrations


@pytest.fixture
def services(tmp_path: Path) -> Iterator[tuple[ProjectService, GraphService]]:
    path = tmp_path / "canvas.sqlite3"
    run_migrations(path)
    engine = create_sqlite_engine(path)
    factory = make_session_factory(engine)
    write_gate = WriteGate()
    try:
        yield (
            ProjectService(factory, write_gate=write_gate),
            GraphService(factory, write_gate=write_gate),
        )
    finally:
        engine.dispose()


def _add_artifact_revision(
    factory: sessionmaker[Session],
    project_id,
    kind: str,
) -> UUID:
    revision_id = uuid4()
    artifact_id = uuid4()
    digest = hashlib.sha256(str(revision_id).encode()).hexdigest()
    metadata_json = (
        '{"kind":"image","width":1,"height":1}'
        if kind == "image"
        else (
            '{"kind":"video","width":1,"height":1,'
            '"duration_seconds":1.0,"frames_per_second":24.0}'
        )
    )
    now = datetime.now(timezone.utc)
    with factory.begin() as session:
        session.add(
            StoredBlobRow(
                sha256=digest,
                byte_size=1,
                media_kind=kind,
                media_type="image/png" if kind == "image" else "video/mp4",
                metadata_json=metadata_json,
                created_at=now,
            )
        )
        session.flush()
        session.add(
            ArtifactRow(
                id=str(artifact_id),
                origin_project_id=str(project_id),
                kind=kind,
                created_at=now,
            )
        )
        session.flush()
        session.add(
            ArtifactRevisionRow(
                id=str(revision_id),
                artifact_id=str(artifact_id),
                sequence=1,
                blob_sha256=digest,
                source_kind="fixture",
                source_project_id=str(project_id),
                source_import_session_id=None,
                display_filename="fixture",
                created_at=now,
            )
        )
    return revision_id


def _add_many_image_revisions(
    factory: sessionmaker[Session],
    project_id: UUID,
    count: int,
) -> list[UUID]:
    artifact_id = uuid4()
    digest = hashlib.sha256(str(artifact_id).encode()).hexdigest()
    now = datetime.now(timezone.utc)
    revision_ids = [uuid4() for _ in range(count)]
    with factory.begin() as session:
        session.add(
            StoredBlobRow(
                sha256=digest,
                byte_size=1,
                media_kind="image",
                media_type="image/png",
                metadata_json='{"kind":"image","width":1,"height":1}',
                created_at=now,
            )
        )
        session.flush()
        session.add(
            ArtifactRow(
                id=str(artifact_id),
                origin_project_id=str(project_id),
                kind="image",
                created_at=now,
            )
        )
        session.flush()
        session.add_all(
            [
                ArtifactRevisionRow(
                    id=str(revision_id),
                    artifact_id=str(artifact_id),
                    sequence=index + 1,
                    blob_sha256=digest,
                    source_kind="fixture",
                    source_project_id=str(project_id),
                    source_import_session_id=None,
                    display_filename=f"fixture-{index}",
                    created_at=now,
                )
                for index, revision_id in enumerate(revision_ids)
            ]
        )
    return revision_ids


def _add_resource_revision(
    graphs: GraphService,
    project_id: UUID,
    *,
    kind: str,
    archived: bool = False,
    artifact_revision_id: UUID | None = None,
) -> UUID:
    resource_id = uuid4()
    revision_id = uuid4()
    now = datetime.now(timezone.utc)
    with graphs.session_factory.begin() as session:
        session.add(
            ResourceRow(
                id=str(resource_id),
                origin_project_id=str(project_id),
                kind=kind,
                name=f"{kind} fixture",
                created_at=now,
                archived_at=now if archived else None,
            )
        )
        session.flush()
        session.add(
            ResourceRevisionRow(
                id=str(revision_id),
                resource_id=str(resource_id),
                sequence=1,
                schema_version=1,
                payload_json=f'{{"kind":"{kind}"}}',
                payload_sha256="d" * 64,
                search_text=f"{kind} fixture",
                created_by_actor="human:test",
                created_at=now,
            )
        )
        session.flush()
        if artifact_revision_id is not None:
            session.add(
                ResourceRevisionArtifactRow(
                    id=str(uuid4()),
                    resource_revision_id=str(revision_id),
                    slot="reference",
                    position=0,
                    artifact_revision_id=str(artifact_revision_id),
                )
            )
    return revision_id


def _v3_with_project_binding(resource_revision_id: UUID, kind: str) -> GraphSnapshotV3:
    return GraphSnapshotV3(
        resource_bindings=[
            ResourceBinding(
                id=uuid4(),
                resource_kind=kind,
                binding_key="main",
                scope_kind="PROJECT",
                scope_id=None,
                resource_revision_id=resource_revision_id,
            )
        ]
    )


def test_v3_resource_binding_requires_an_existing_revision_of_the_same_kind(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("C1 resource validation")
    first = graphs.get_active(project.id)
    style_revision_id = _add_resource_revision(
        graphs,
        project.id,
        kind="STYLE",
    )

    with pytest.raises(GraphValidationError, match="resource revision kind"):
        graphs.create_revision(
            project.id,
            CreateGraphRevisionRequest(
                base_revision_id=first.revision_id,
                client_mutation_id=uuid4(),
                snapshot=_v3_with_project_binding(style_revision_id, "CHARACTER"),
            ),
        )
    with pytest.raises(GraphValidationError, match="resource revision is missing"):
        graphs.create_revision(
            project.id,
            CreateGraphRevisionRequest(
                base_revision_id=first.revision_id,
                client_mutation_id=uuid4(),
                snapshot=_v3_with_project_binding(uuid4(), "STYLE"),
            ),
        )
    assert graphs.get_active(project.id).revision_id == first.revision_id


def test_v3_resource_binding_allows_an_archived_resource_revision(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("C1 archived resource")
    first = graphs.get_active(project.id)
    archived_revision_id = _add_resource_revision(
        graphs,
        project.id,
        kind="STYLE",
        archived=True,
    )

    saved = graphs.create_revision(
        project.id,
        CreateGraphRevisionRequest(
            base_revision_id=first.revision_id,
            client_mutation_id=uuid4(),
            snapshot=_v3_with_project_binding(archived_revision_id, "STYLE"),
        ),
    )

    assert saved.snapshot.resource_bindings[0].resource_revision_id == archived_revision_id


def test_v3_resource_binding_rejects_a_discarded_attached_artifact(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("C1 attached artifact")
    first = graphs.get_active(project.id)
    artifact_revision_id = _add_artifact_revision(
        graphs.session_factory,
        project.id,
        "image",
    )
    resource_revision_id = _add_resource_revision(
        graphs,
        project.id,
        kind="MEDIA",
        artifact_revision_id=artifact_revision_id,
    )
    with graphs.session_factory.begin() as session:
        artifact_revision = session.get(ArtifactRevisionRow, str(artifact_revision_id))
        assert artifact_revision is not None
        artifact_revision.discarded_at = datetime.now(timezone.utc)

    with pytest.raises(GraphValidationError, match="attached artifact revision is missing"):
        graphs.create_revision(
            project.id,
            CreateGraphRevisionRequest(
                base_revision_id=first.revision_id,
                client_mutation_id=uuid4(),
                snapshot=_v3_with_project_binding(resource_revision_id, "MEDIA"),
            ),
        )


def test_save_creates_new_revision_without_mutating_parent(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("Demo")
    first = graphs.get_active(project.id)
    snapshot = GraphSnapshotV1(
        nodes=[GraphNode(id=uuid4(), kind="note", position=Point(x=10, y=20), data={"text": "A"})]
    )

    second = graphs.create_revision(
        project.id,
        CreateGraphRevisionRequest(
            base_revision_id=first.revision_id,
            client_mutation_id=uuid4(),
            snapshot=snapshot,
        ),
    )

    assert second.revision_id != first.revision_id
    from ai_canvas.domain.graph.snapshots import normalize_to_v3

    assert graphs.get_revision(project.id, first.revision_id).snapshot.nodes == []
    assert graphs.get_active(project.id).snapshot == normalize_to_v3(snapshot)


def test_new_projects_start_with_an_empty_v2_snapshot(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("V2 project")

    active = graphs.get_active(project.id)

    assert isinstance(active.snapshot, graph_contracts.GraphSnapshotV2)
    assert active.snapshot.model_dump(mode="json") == {
        "schema_version": 2,
        "nodes": [],
        "edges": [],
        "shots": [],
    }


def test_same_mutation_is_idempotent(services: tuple[ProjectService, GraphService]) -> None:
    projects, graphs = services
    project = projects.create("Demo")
    first = graphs.get_active(project.id)
    mutation_id = uuid4()
    request = CreateGraphRevisionRequest(
        base_revision_id=first.revision_id,
        client_mutation_id=mutation_id,
        snapshot=GraphSnapshotV1(),
    )

    saved = graphs.create_revision(project.id, request)
    replayed = graphs.create_revision(project.id, request)

    assert replayed.revision_id == saved.revision_id
    assert saved.snapshot.schema_version == 3


def test_pre_upgrade_v1_mutation_replays_without_rewriting_stored_revision(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("Legacy")
    first = graphs.get_active(project.id)
    mutation_id = uuid4()
    legacy_snapshot = GraphSnapshotV1(
        nodes=[GraphNode(id=uuid4(), kind="note", position=Point(x=1, y=2), data={"text": "v1"})]
    )
    legacy_json, legacy_hash = canonical_snapshot(legacy_snapshot)
    legacy_revision_id = uuid4()
    with projects.session_factory.begin() as session:
        session.add(
            GraphRevisionRow(
                id=str(legacy_revision_id),
                graph_document_id=str(project.graph_document_id),
                parent_revision_id=str(first.revision_id),
                sequence=1,
                client_mutation_id=str(mutation_id),
                snapshot_json=legacy_json,
                snapshot_hash=legacy_hash,
                created_at=datetime.now(timezone.utc),
            )
        )
        document = session.get(GraphDocumentRow, str(project.graph_document_id))
        assert document is not None
        document.active_revision_id = str(legacy_revision_id)

    replayed = graphs.create_revision(
        project.id,
        CreateGraphRevisionRequest(
            base_revision_id=first.revision_id,
            client_mutation_id=mutation_id,
            snapshot=legacy_snapshot,
        ),
    )

    assert replayed.revision_id == legacy_revision_id
    assert isinstance(replayed.snapshot, GraphSnapshotV1)
    with projects.session_factory() as session:
        stored = session.get(GraphRevisionRow, str(legacy_revision_id))
        assert stored is not None
        assert (stored.snapshot_json, stored.snapshot_hash) == (legacy_json, legacy_hash)


def test_reads_stored_v1_revision_without_creating_or_upgrading_a_revision(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("Legacy read")
    active = graphs.get_active(project.id)
    legacy = GraphSnapshotV1(
        nodes=[GraphNode(id=uuid4(), kind="note", position=Point(x=5, y=6), data={"text": "old"})]
    )
    legacy_json, legacy_hash = canonical_snapshot(legacy)
    with projects.session_factory.begin() as session:
        row = session.get(GraphRevisionRow, str(active.revision_id))
        assert row is not None
        row.snapshot_json = legacy_json
        row.snapshot_hash = legacy_hash

    child = graphs.create_revision(
        project.id,
        CreateGraphRevisionRequest(
            base_revision_id=active.revision_id,
            client_mutation_id=uuid4(),
            snapshot=graph_contracts.GraphSnapshotV2(),
        ),
    )
    with projects.session_factory() as session:
        count_before_read = session.scalar(
            select(func.count())
            .select_from(GraphRevisionRow)
            .where(GraphRevisionRow.graph_document_id == str(project.graph_document_id))
        )

    read = graphs.get_revision(project.id, active.revision_id)

    assert read.revision_id == active.revision_id
    assert child.revision_id != active.revision_id
    assert child.snapshot.schema_version == 3
    assert read.snapshot == legacy
    assert read.snapshot.schema_version == 1
    assert read.snapshot_hash == legacy_hash
    assert canonical_snapshot(read.snapshot) == (legacy_json, legacy_hash)
    assert count_before_read == 2
    with projects.session_factory() as session:
        assert session.scalar(
            select(func.count())
            .select_from(GraphRevisionRow)
            .where(GraphRevisionRow.graph_document_id == str(project.graph_document_id))
        ) == 2


def test_stale_matching_mutation_replay_conflicts_with_current_head(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("Demo")
    first = graphs.get_active(project.id)
    mutation_a = CreateGraphRevisionRequest(
        base_revision_id=first.revision_id,
        client_mutation_id=uuid4(),
        snapshot=GraphSnapshotV1(),
    )
    second = graphs.create_revision(project.id, mutation_a)
    third = graphs.create_revision(
        project.id,
        CreateGraphRevisionRequest(
            base_revision_id=second.revision_id,
            client_mutation_id=uuid4(),
            snapshot=GraphSnapshotV1(
                nodes=[
                    GraphNode(
                        id=uuid4(),
                        kind="note",
                        position=Point(x=10, y=20),
                        data={"text": "B"},
                    )
                ]
            ),
        ),
    )

    with pytest.raises(GraphRevisionConflict) as error:
        replayed = graphs.create_revision(project.id, mutation_a)
        pytest.fail(
            f"stale mutation replay returned revision {replayed.revision_id}; "
            f"mutation A created {second.revision_id} while active is {third.revision_id}"
        )

    assert error.value.current_revision_id == str(third.revision_id)
    assert graphs.get_active(project.id).revision_id == third.revision_id


def test_saving_moves_project_to_top_of_recent_list(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    older = projects.create("Older")
    projects.create("Newer")
    first = graphs.get_active(older.id)
    graphs.create_revision(
        older.id,
        CreateGraphRevisionRequest(
            base_revision_id=first.revision_id,
            client_mutation_id=uuid4(),
            snapshot=GraphSnapshotV1(),
        ),
    )

    assert projects.list()[0].id == older.id


def test_service_rejects_blank_project_name(services: tuple[ProjectService, GraphService]) -> None:
    projects, _graphs = services
    with pytest.raises(ValueError, match="project name"):
        projects.create("   ")


def test_stale_base_is_rejected(services: tuple[ProjectService, GraphService]) -> None:
    projects, graphs = services
    project = projects.create("Demo")
    first = graphs.get_active(project.id)
    graphs.create_revision(
        project.id,
        CreateGraphRevisionRequest(
            base_revision_id=first.revision_id,
            client_mutation_id=uuid4(),
            snapshot=GraphSnapshotV1(),
        ),
    )

    with pytest.raises(GraphRevisionConflict):
        graphs.create_revision(
            project.id,
            CreateGraphRevisionRequest(
                base_revision_id=first.revision_id,
                client_mutation_id=uuid4(),
                snapshot=GraphSnapshotV1(),
            ),
        )


def test_mutation_id_cannot_be_reused_for_different_content(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("Demo")
    first = graphs.get_active(project.id)
    mutation_id = uuid4()
    graphs.create_revision(
        project.id,
        CreateGraphRevisionRequest(
            base_revision_id=first.revision_id,
            client_mutation_id=mutation_id,
            snapshot=GraphSnapshotV1(),
        ),
    )
    changed = GraphSnapshotV1(
        nodes=[GraphNode(id=uuid4(), kind="note", position=Point(x=0, y=0), data={"text": "B"})]
    )
    with pytest.raises(ClientMutationReuse):
        graphs.create_revision(
            project.id,
            CreateGraphRevisionRequest(
                base_revision_id=first.revision_id,
                client_mutation_id=mutation_id,
                snapshot=changed,
            ),
        )


def test_mutation_replay_requires_the_original_parent_even_when_content_matches(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("Demo")
    first = graphs.get_active(project.id)
    mutation_id = uuid4()
    request = CreateGraphRevisionRequest(
        base_revision_id=first.revision_id,
        client_mutation_id=mutation_id,
        snapshot=GraphSnapshotV1(),
    )
    graphs.create_revision(project.id, request)

    with pytest.raises(ClientMutationReuse):
        graphs.create_revision(
            project.id,
            CreateGraphRevisionRequest(
                base_revision_id=uuid4(),
                client_mutation_id=mutation_id,
                snapshot=GraphSnapshotV1(),
            ),
        )


def test_concurrent_tabs_get_one_save_and_one_conflict(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("Demo")
    first = graphs.get_active(project.id)
    barrier = Barrier(2)

    def save(mutation_id) -> str:
        barrier.wait()
        try:
            graphs.create_revision(
                project.id,
                CreateGraphRevisionRequest(
                    base_revision_id=first.revision_id,
                    client_mutation_id=mutation_id,
                    snapshot=GraphSnapshotV1(),
                ),
            )
            return "saved"
        except GraphRevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(save, [uuid4(), uuid4()]))
    assert sorted(outcomes) == ["conflict", "saved"]


def test_project_get_rejects_dangling_active_head(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, _graphs = services
    project = projects.create("Corrupt")
    dangling_revision_id = uuid4()
    with projects.session_factory.begin() as session:
        document = session.get(GraphDocumentRow, str(project.graph_document_id))
        assert document is not None
        document.active_revision_id = str(dangling_revision_id)

    with pytest.raises(GraphRevisionNotFound) as error:
        projects.get(project.id)

    assert error.value.revision_id == str(dangling_revision_id)


def test_project_get_rejects_cross_document_active_head(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, _graphs = services
    corrupt = projects.create("Corrupt")
    other = projects.create("Other")
    with projects.session_factory.begin() as session:
        document = session.get(GraphDocumentRow, str(corrupt.graph_document_id))
        assert document is not None
        document.active_revision_id = str(other.active_revision_id)

    with pytest.raises(GraphRevisionNotFound) as error:
        projects.get(corrupt.id)

    assert error.value.revision_id == str(other.active_revision_id)


def test_project_list_excludes_invalid_active_heads(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, _graphs = services
    dangling = projects.create("Dangling")
    cross_document = projects.create("Cross document")
    valid = projects.create("Valid")
    with projects.session_factory.begin() as session:
        dangling_document = session.get(GraphDocumentRow, str(dangling.graph_document_id))
        cross_document_row = session.get(GraphDocumentRow, str(cross_document.graph_document_id))
        assert dangling_document is not None
        assert cross_document_row is not None
        dangling_document.active_revision_id = str(uuid4())
        cross_document_row.active_revision_id = str(valid.active_revision_id)

    listed_ids = {project.id for project in projects.list()}

    assert listed_ids == {valid.id}


@pytest.mark.parametrize("corruption", ["noncanonical_json", "hash_only", "invalid_json"])
def test_get_and_replay_report_stored_revision_integrity_errors(
    services: tuple[ProjectService, GraphService],
    corruption: str,
) -> None:
    from ai_canvas.application.errors import StoredGraphIntegrityError

    projects, graphs = services
    project = projects.create("Demo")
    first = graphs.get_active(project.id)
    request = CreateGraphRevisionRequest(
        base_revision_id=first.revision_id,
        client_mutation_id=uuid4(),
        snapshot=GraphSnapshotV1(),
    )
    saved = graphs.create_revision(project.id, request)
    with projects.session_factory.begin() as session:
        revision = session.get(GraphRevisionRow, str(saved.revision_id))
        assert revision is not None
        if corruption == "noncanonical_json":
            revision.snapshot_json = json.dumps(json.loads(revision.snapshot_json), indent=2)
        elif corruption == "hash_only":
            revision.snapshot_hash = "0" * 64
        else:
            revision.snapshot_json = "{not valid json"

    with pytest.raises(StoredGraphIntegrityError) as get_error:
        graphs.get_active(project.id)
    with pytest.raises(StoredGraphIntegrityError) as replay_error:
        graphs.create_revision(project.id, request)

    assert get_error.value.revision_id == str(saved.revision_id)
    assert replay_error.value.revision_id == str(saved.revision_id)


def test_new_child_is_not_created_from_a_corrupt_active_parent(
    services: tuple[ProjectService, GraphService],
) -> None:
    from ai_canvas.application.errors import StoredGraphIntegrityError

    projects, graphs = services
    project = projects.create("Corrupt parent")
    parent = graphs.get_active(project.id)
    with projects.session_factory.begin() as session:
        revision = session.get(GraphRevisionRow, str(parent.revision_id))
        assert revision is not None
        revision.snapshot_hash = "f" * 64

    with pytest.raises(StoredGraphIntegrityError):
        graphs.create_revision(
            project.id,
            CreateGraphRevisionRequest(
                base_revision_id=parent.revision_id,
                client_mutation_id=uuid4(),
                snapshot=graph_contracts.GraphSnapshotV2(),
            ),
        )

    with projects.session_factory() as session:
        assert session.scalar(
            select(func.count())
            .select_from(GraphRevisionRow)
            .where(GraphRevisionRow.graph_document_id == str(project.graph_document_id))
        ) == 1


def test_canonical_snapshot_ignores_collection_order_without_mutating_inputs() -> None:
    source_a = GraphNode(
        id=uuid4(), kind="text_source", position=Point(x=0, y=0), data={"text": "A"}
    )
    prompt_a = GraphNode(id=uuid4(), kind="prompt", position=Point(x=1, y=0), data={"text": ""})
    source_b = GraphNode(
        id=uuid4(), kind="text_source", position=Point(x=0, y=1), data={"text": "B"}
    )
    prompt_b = GraphNode(id=uuid4(), kind="prompt", position=Point(x=1, y=1), data={"text": ""})
    edge_a = GraphEdge(
        id=uuid4(),
        source=source_a.id,
        source_port="text",
        target=prompt_a.id,
        target_port="text",
    )
    edge_b = GraphEdge(
        id=uuid4(),
        source=source_b.id,
        source_port="text",
        target=prompt_b.id,
        target_port="text",
    )
    first = GraphSnapshotV1(
        nodes=[source_a, prompt_a, source_b, prompt_b],
        edges=[edge_a, edge_b],
    )
    second = GraphSnapshotV1(
        nodes=[prompt_b, source_b, prompt_a, source_a],
        edges=[edge_b, edge_a],
    )
    first_node_order = [node.id for node in first.nodes]
    first_edge_order = [edge.id for edge in first.edges]
    second_node_order = [node.id for node in second.nodes]
    second_edge_order = [edge.id for edge in second.edges]

    assert canonical_snapshot(first) == canonical_snapshot(second)
    assert [node.id for node in first.nodes] == first_node_order
    assert [edge.id for edge in first.edges] == first_edge_order
    assert [node.id for node in second.nodes] == second_node_order
    assert [edge.id for edge in second.edges] == second_edge_order


def test_v2_canonical_snapshot_sorts_top_level_shots_but_preserves_reference_order() -> None:
    first_shot_id = uuid4()
    second_shot_id = uuid4()
    first_node_id = uuid4()
    second_node_id = uuid4()
    first_reference = uuid4()
    second_reference = uuid4()
    first_payload = {
        "schema_version": 2,
        "nodes": [
            {
                "id": first_node_id,
                "kind": "shot",
                "position": {"x": 0, "y": 0},
                "data": {"shot_id": first_shot_id},
            },
            {
                "id": second_node_id,
                "kind": "shot",
                "position": {"x": 1, "y": 1},
                "data": {"shot_id": second_shot_id},
            },
        ],
        "edges": [],
        "shots": [
            {
                "id": first_shot_id,
                "order": 1,
                "image_revision_id": None,
                "video_revision_id": None,
                "reference_revision_ids": [first_reference, second_reference],
            },
            {
                "id": second_shot_id,
                "order": 0,
                "image_revision_id": None,
                "video_revision_id": None,
                "reference_revision_ids": [],
            },
        ],
    }
    first = graph_contracts.GraphSnapshotV2.model_validate(first_payload)
    reordered = graph_contracts.GraphSnapshotV2.model_validate(
        {
            **first_payload,
            "nodes": list(reversed(first_payload["nodes"])),
            "shots": list(reversed(first_payload["shots"])),
        }
    )
    changed_reference_order_payload = {
        **first_payload,
        "shots": [dict(item) for item in first_payload["shots"]],
    }
    changed_reference_order_payload["shots"][0]["reference_revision_ids"] = [
        second_reference,
        first_reference,
    ]
    changed_reference_order = graph_contracts.GraphSnapshotV2.model_validate(
        changed_reference_order_payload
    )
    original_node_order = [node.id for node in first.nodes]
    original_shot_order = [shot.id for shot in first.shots]

    assert canonical_snapshot(first) == canonical_snapshot(reordered)
    assert canonical_snapshot(first) != canonical_snapshot(changed_reference_order)
    assert [node.id for node in first.nodes] == original_node_order
    assert [shot.id for shot in first.shots] == original_shot_order


def test_viewport_update_does_not_create_a_revision(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("Demo")
    before = graphs.get_active(project.id)
    viewport = ViewportState(x=100, y=-25, zoom=2)

    assert graphs.update_viewport(project.id, viewport) == viewport
    after = graphs.get_active(project.id)

    assert after.viewport == viewport
    assert after.revision_id == before.revision_id
    assert after.sequence == before.sequence


def test_revision_read_rejects_cross_project_ownership(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    first_project = projects.create("First")
    second_project = projects.create("Second")
    second_revision = graphs.get_active(second_project.id)

    with pytest.raises(GraphRevisionNotFound):
        graphs.get_revision(first_project.id, second_revision.revision_id)


def test_active_read_rejects_missing_project(
    services: tuple[ProjectService, GraphService],
) -> None:
    _projects, graphs = services

    with pytest.raises(ProjectNotFound):
        graphs.get_active(uuid4())


def test_project_creation_rolls_back_every_row_when_view_state_add_fails(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, _graphs = services

    class FailingGraphRepository(GraphRepository):
        def add_view_state(
            self, session: Session, view_state: ProjectViewStateRow
        ) -> None:
            super().add_view_state(session, view_state)
            raise RuntimeError("injected view state failure")

    failing_projects = ProjectService(
        projects.session_factory,
        graphs=FailingGraphRepository(),
        write_gate=WriteGate(),
    )

    with pytest.raises(RuntimeError, match="injected view state failure"):
        failing_projects.create("Rollback")

    with projects.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProjectRow)) == 0
        assert session.scalar(select(func.count()).select_from(GraphDocumentRow)) == 0
        assert session.scalar(select(func.count()).select_from(GraphRevisionRow)) == 0
        assert session.scalar(select(func.count()).select_from(ProjectViewStateRow)) == 0


def test_cas_miss_rolls_back_child_revision_and_injected_head_change(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("Demo")
    first = graphs.get_active(project.id)

    class CasMissGraphRepository(GraphRepository):
        def add_revision(self, session: Session, revision: GraphRevisionRow) -> None:
            super().add_revision(session, revision)
            document = session.get(GraphDocumentRow, revision.graph_document_id)
            assert document is not None
            document.active_revision_id = str(uuid4())
            session.flush()

    cas_miss_graphs = GraphService(
        projects.session_factory,
        repository=CasMissGraphRepository(),
        write_gate=WriteGate(),
    )
    request = CreateGraphRevisionRequest(
        base_revision_id=first.revision_id,
        client_mutation_id=uuid4(),
        snapshot=GraphSnapshotV1(),
    )

    with pytest.raises(GraphRevisionConflict):
        cas_miss_graphs.create_revision(project.id, request)

    assert graphs.get_active(project.id).revision_id == first.revision_id
    with projects.session_factory() as session:
        revision_count = session.scalar(
            select(func.count())
            .select_from(GraphRevisionRow)
            .where(GraphRevisionRow.graph_document_id == str(project.graph_document_id))
        )
        assert revision_count == 1


def test_v2_media_and_shot_references_must_exist_and_match_the_locked_kind(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("Artifact refs")
    active = graphs.get_active(project.id)
    image_revision = _add_artifact_revision(projects.session_factory, project.id, "image")
    video_revision = _add_artifact_revision(projects.session_factory, project.id, "video")
    shot_id = uuid4()

    valid = GraphSnapshotV2(
        nodes=[
            MediaInputNodeV2(
                id=uuid4(),
                kind="media_input",
                position=Point(x=0, y=0),
                data=MediaInputData(
                    artifact_revision_id=image_revision,
                    media_kind="image",
                ),
            ),
            ShotNodeV2(
                id=uuid4(),
                kind="shot",
                position=Point(x=10, y=0),
                data=ShotNodeData(shot_id=shot_id),
            ),
        ],
        shots=[
            Shot(
                id=shot_id,
                order=0,
                image_revision_id=image_revision,
                video_revision_id=video_revision,
                reference_revision_ids=[image_revision],
            )
        ],
    )
    saved = graphs.create_revision(
        project.id,
        CreateGraphRevisionRequest(
            base_revision_id=active.revision_id,
            client_mutation_id=uuid4(),
            snapshot=valid,
        ),
    )
    from ai_canvas.domain.graph.snapshots import normalize_to_v3

    assert canonical_snapshot(saved.snapshot) == canonical_snapshot(normalize_to_v3(valid))

    invalid_snapshots = (
        valid.model_copy(
            update={
                "nodes": [
                    valid.nodes[0].model_copy(
                        update={
                            "data": MediaInputData(
                                artifact_revision_id=uuid4(),
                                media_kind="image",
                            )
                        }
                    ),
                    valid.nodes[1],
                ]
            }
        ),
        valid.model_copy(
            update={
                "nodes": [
                    valid.nodes[0].model_copy(
                        update={
                            "data": MediaInputData(
                                artifact_revision_id=video_revision,
                                media_kind="image",
                            )
                        }
                    ),
                    valid.nodes[1],
                ]
            }
        ),
        valid.model_copy(
            update={
                "shots": [valid.shots[0].model_copy(update={"image_revision_id": video_revision})]
            }
        ),
        valid.model_copy(
            update={
                "shots": [valid.shots[0].model_copy(update={"video_revision_id": image_revision})]
            }
        ),
        valid.model_copy(
            update={
                "shots": [
                    valid.shots[0].model_copy(update={"reference_revision_ids": [video_revision]})
                ]
            }
        ),
    )
    for invalid in invalid_snapshots:
        current = graphs.get_active(project.id)
        with pytest.raises(GraphValidationError, match="artifact revision"):
            graphs.create_revision(
                project.id,
                CreateGraphRevisionRequest(
                    base_revision_id=current.revision_id,
                    client_mutation_id=uuid4(),
                    snapshot=invalid,
                ),
            )
        assert graphs.get_active(project.id).revision_id == current.revision_id


def test_graph_may_reference_a_workspace_revision_from_another_project(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    origin = projects.create("Origin")
    consumer = projects.create("Consumer")
    revision_id = _add_artifact_revision(projects.session_factory, origin.id, "image")
    active = graphs.get_active(consumer.id)
    snapshot = GraphSnapshotV2(
        nodes=[
            MediaInputNodeV2(
                id=uuid4(),
                kind="media_input",
                position=Point(x=0, y=0),
                data=MediaInputData(
                    artifact_revision_id=revision_id,
                    media_kind="image",
                ),
            )
        ]
    )
    saved = graphs.create_revision(
        consumer.id,
        CreateGraphRevisionRequest(
            base_revision_id=active.revision_id,
            client_mutation_id=uuid4(),
            snapshot=snapshot,
        ),
    )
    from ai_canvas.domain.graph.snapshots import normalize_to_v3

    assert saved.snapshot == normalize_to_v3(snapshot)


def test_graph_reference_validation_chunks_more_than_sqlite_variable_limit(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("Chunked refs")
    revision_ids = _add_many_image_revisions(
        projects.session_factory,
        project.id,
        1_005,
    )
    active = graphs.get_active(project.id)
    snapshot = GraphSnapshotV2(
        nodes=[
            MediaInputNodeV2(
                id=uuid4(),
                kind="media_input",
                position=Point(x=index, y=0),
                data=MediaInputData(
                    artifact_revision_id=revision_id,
                    media_kind="image",
                ),
            )
            for index, revision_id in enumerate(revision_ids)
        ]
    )

    saved = graphs.create_revision(
        project.id,
        CreateGraphRevisionRequest(
            base_revision_id=active.revision_id,
            client_mutation_id=uuid4(),
            snapshot=snapshot,
        ),
    )

    assert len(saved.snapshot.nodes) == 1_005


def test_stale_base_conflict_precedes_artifact_reference_validation(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("Precedence")
    first = graphs.get_active(project.id)
    graphs.create_revision(
        project.id,
        CreateGraphRevisionRequest(
            base_revision_id=first.revision_id,
            client_mutation_id=uuid4(),
            snapshot=GraphSnapshotV2(),
        ),
    )
    missing = GraphSnapshotV2(
        nodes=[
            MediaInputNodeV2(
                id=uuid4(),
                kind="media_input",
                position=Point(x=0, y=0),
                data=MediaInputData(artifact_revision_id=uuid4(), media_kind="image"),
            )
        ]
    )
    with pytest.raises(GraphRevisionConflict):
        graphs.create_revision(
            project.id,
            CreateGraphRevisionRequest(
                base_revision_id=first.revision_id,
                client_mutation_id=uuid4(),
                snapshot=missing,
            ),
        )


def test_idempotent_graph_replay_does_not_revalidate_or_rewrite_artifact_refs(
    services: tuple[ProjectService, GraphService],
) -> None:
    projects, graphs = services
    project = projects.create("Replay refs")
    artifact_revision_id = _add_artifact_revision(
        projects.session_factory,
        project.id,
        "image",
    )
    active = graphs.get_active(project.id)
    snapshot = GraphSnapshotV2(
        nodes=[
            MediaInputNodeV2(
                id=uuid4(),
                kind="media_input",
                position=Point(x=0, y=0),
                data=MediaInputData(
                    artifact_revision_id=artifact_revision_id,
                    media_kind="image",
                ),
            )
        ]
    )
    request = CreateGraphRevisionRequest(
        base_revision_id=active.revision_id,
        client_mutation_id=uuid4(),
        snapshot=snapshot,
    )
    saved = graphs.create_revision(project.id, request)
    with projects.session_factory.begin() as session:
        revision = session.get(ArtifactRevisionRow, str(artifact_revision_id))
        assert revision is not None
        artifact = session.get(ArtifactRow, revision.artifact_id)
        assert artifact is not None
        artifact.kind = "video"

    replayed = graphs.create_revision(project.id, request)

    assert replayed.revision_id == saved.revision_id
    assert replayed.snapshot_hash == saved.snapshot_hash
    assert canonical_snapshot(replayed.snapshot) == canonical_snapshot(saved.snapshot)
