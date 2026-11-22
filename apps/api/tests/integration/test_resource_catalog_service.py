from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ai_canvas.application.errors import (
    InvalidResourceReference,
    ResourceRevisionConflict,
)
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.resource_catalog_service import ResourceCatalogService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts.graph import (
    CreateGraphRevisionRequest,
    GraphSnapshotV3,
    ResourceBinding,
)
from ai_canvas.contracts.resources import (
    CreateResourceRequest,
    CreateResourceRevisionRequest,
    MediaResourcePayload,
    ResourceArtifactReference,
    StyleResourcePayload,
)
from ai_canvas.infrastructure.db.models import (
    ArtifactRevisionRow,
    ArtifactRow,
    StoredBlobRow,
)
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


@dataclass
class CatalogServices:
    projects: ProjectService
    graphs: GraphService
    events: EventSpineService
    resources: ResourceCatalogService
    project_id: UUID


@pytest.fixture
def c1_services(tmp_path: Path) -> Iterator[CatalogServices]:
    database_path = tmp_path / "catalog.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    factory = make_session_factory(engine)
    write_gate = WriteGate()
    projects = ProjectService(factory, write_gate=write_gate)
    graphs = GraphService(factory, write_gate=write_gate)
    events = EventSpineService(factory, write_gate=write_gate)
    resources = ResourceCatalogService(
        factory,
        events,
        write_gate=write_gate,
    )
    project = projects.create("C1 catalog")
    try:
        yield CatalogServices(
            projects=projects,
            graphs=graphs,
            events=events,
            resources=resources,
            project_id=project.id,
        )
    finally:
        engine.dispose()


def _style_request(summary: str = "低饱和电影风格") -> CreateResourceRequest:
    return CreateResourceRequest(
        name="电影风格",
        payload=StyleResourcePayload(summary=summary),
        tags=[" Cinematic ", "低饱和"],
    )


def _seed_artifact_revision(c1_services: CatalogServices, kind: str) -> UUID:
    factory = c1_services.projects.session_factory
    revision_id = uuid4()
    artifact_id = uuid4()
    digest = hashlib.sha256(str(revision_id).encode()).hexdigest()
    now = datetime.now(timezone.utc)
    metadata_json = (
        '{"kind":"image","width":1,"height":1}'
        if kind == "image"
        else (
            '{"kind":"video","width":1,"height":1,'
            '"duration_seconds":1.0,"frames_per_second":24.0}'
        )
    )
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
                origin_project_id=str(c1_services.project_id),
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
                source_project_id=str(c1_services.project_id),
                source_import_session_id=None,
                display_filename="fixture",
                created_at=now,
            )
        )
    return revision_id


def test_create_is_atomic_searchable_and_appends_a_redacted_event(
    c1_services: CatalogServices,
) -> None:
    created = c1_services.resources.create_resource(
        c1_services.project_id,
        _style_request(),
        actor_id="human:test",
    )

    assert created.kind == "STYLE"
    assert created.latest_revision.sequence == 1
    assert [tag.normalized_tag for tag in created.latest_revision.tags] == [
        "cinematic",
        "低饱和",
    ]
    assert [item.id for item in c1_services.resources.list_resources(
        kind="STYLE", query="低饱和", tags=("CINEMATIC",)
    )] == [created.id]
    page = c1_services.events.query(
        c1_services.project_id,
        after=0,
        limit=10,
        event_types=frozenset({"resource.created"}),
    )
    assert [event.event_type for event in page.events] == ["resource.created"]
    assert page.events[0].payload["resource_revision_id"] == str(
        created.latest_revision.id
    )
    assert "低饱和电影风格" not in str(page.events[0].payload)


def test_new_revision_does_not_move_existing_graph_pin(
    c1_services: CatalogServices,
) -> None:
    resource = c1_services.resources.create_resource(
        c1_services.project_id,
        _style_request(),
        actor_id="human:test",
    )
    active = c1_services.graphs.get_active(c1_services.project_id)
    binding_id = uuid4()
    pinned = c1_services.graphs.create_revision(
        c1_services.project_id,
        CreateGraphRevisionRequest(
            base_revision_id=active.revision_id,
            client_mutation_id=uuid4(),
            snapshot=GraphSnapshotV3(
                resource_bindings=[
                    ResourceBinding(
                        id=binding_id,
                        resource_kind="STYLE",
                        binding_key="look",
                        scope_kind="PROJECT",
                        scope_id=None,
                        resource_revision_id=resource.latest_revision.id,
                    )
                ]
            ),
        ),
    )
    newer = c1_services.resources.create_revision(
        c1_services.project_id,
        resource.id,
        CreateResourceRevisionRequest(
            base_revision_id=resource.latest_revision.id,
            payload=StyleResourcePayload(summary="高反差电影风格"),
            tags=["cinematic"],
        ),
        actor_id="human:test",
    )

    active_after = c1_services.graphs.get_active(c1_services.project_id)
    assert active_after.revision_id == pinned.revision_id
    assert (
        active_after.snapshot.resource_bindings[0].resource_revision_id
        == resource.latest_revision.id
    )
    updates = c1_services.resources.list_project_updates(c1_services.project_id)
    assert [(item.binding_id, item.latest_revision_id) for item in updates] == [
        (binding_id, newer.id)
    ]


def test_stale_revision_base_is_rejected_without_creating_an_event(
    c1_services: CatalogServices,
) -> None:
    resource = c1_services.resources.create_resource(
        c1_services.project_id,
        _style_request(),
        actor_id="human:test",
    )
    first = resource.latest_revision
    second = c1_services.resources.create_revision(
        c1_services.project_id,
        resource.id,
        CreateResourceRevisionRequest(
            base_revision_id=first.id,
            payload=StyleResourcePayload(summary="第二版"),
        ),
        actor_id="human:test",
    )
    before = c1_services.events.query(c1_services.project_id, after=0, limit=100)

    with pytest.raises(ResourceRevisionConflict) as raised:
        c1_services.resources.create_revision(
            c1_services.project_id,
            resource.id,
            CreateResourceRevisionRequest(
                base_revision_id=first.id,
                payload=StyleResourcePayload(summary="过期写入"),
            ),
            actor_id="human:test",
        )

    assert raised.value.current_revision_id == str(second.id)
    detail = c1_services.resources.get_resource(resource.id)
    assert [revision.id for revision in detail.revision_history] == [second.id, first.id]
    after = c1_services.events.query(c1_services.project_id, after=0, limit=100)
    assert after.high_watermark == before.high_watermark


def test_archive_hides_search_but_preserves_existing_graph_pins(
    c1_services: CatalogServices,
) -> None:
    resource = c1_services.resources.create_resource(
        c1_services.project_id,
        _style_request(),
        actor_id="human:test",
    )
    active = c1_services.graphs.get_active(c1_services.project_id)
    pinned = c1_services.graphs.create_revision(
        c1_services.project_id,
        CreateGraphRevisionRequest(
            base_revision_id=active.revision_id,
            client_mutation_id=uuid4(),
            snapshot=GraphSnapshotV3(
                resource_bindings=[
                    ResourceBinding(
                        id=uuid4(),
                        resource_kind="STYLE",
                        binding_key="look",
                        scope_kind="PROJECT",
                        scope_id=None,
                        resource_revision_id=resource.latest_revision.id,
                    )
                ]
            ),
        ),
    )

    archived = c1_services.resources.archive_resource(
        c1_services.project_id,
        resource.id,
        actor_id="human:test",
    )
    c1_services.resources.archive_resource(
        c1_services.project_id,
        resource.id,
        actor_id="human:test",
    )

    assert archived.archived_at is not None
    assert c1_services.resources.list_resources(query="电影风格") == []
    assert [item.id for item in c1_services.resources.list_resources(
        query="电影风格", include_archived=True
    )] == [resource.id]
    assert c1_services.graphs.get_active(c1_services.project_id).revision_id == pinned.revision_id
    events = c1_services.events.query(
        c1_services.project_id,
        after=0,
        limit=100,
        event_types=frozenset({"resource.archived"}),
    )
    assert len(events.events) == 1


def test_media_resource_rejects_an_artifact_of_the_wrong_kind_atomically(
    c1_services: CatalogServices,
) -> None:
    image_revision_id = _seed_artifact_revision(c1_services, "image")

    with pytest.raises(InvalidResourceReference) as raised:
        c1_services.resources.create_resource(
            c1_services.project_id,
            CreateResourceRequest(
                name="视频参考",
                payload=MediaResourcePayload(
                    usage_notes="只允许视频",
                    expected_media_kind="video",
                ),
                artifacts=[
                    ResourceArtifactReference(
                        slot="reference",
                        position=0,
                        artifact_revision_id=image_revision_id,
                    )
                ],
            ),
            actor_id="human:test",
        )

    assert raised.value.reason == "artifact_media_kind_mismatch"
    assert c1_services.resources.list_resources(include_archived=True) == []
    assert c1_services.events.query(
        c1_services.project_id, after=0, limit=100
    ).high_watermark == 0


def test_event_failure_rolls_back_the_resource_and_revision(
    c1_services: CatalogServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_event(*_args, **_kwargs):
        raise RuntimeError("event append failed")

    monkeypatch.setattr(c1_services.events, "append_in_session", fail_event)

    with pytest.raises(RuntimeError, match="event append failed"):
        c1_services.resources.create_resource(
            c1_services.project_id,
            _style_request(),
            actor_id="human:test",
        )

    assert c1_services.resources.list_resources(include_archived=True) == []
