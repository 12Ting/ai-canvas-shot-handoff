from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from threading import Barrier, Event, Thread
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.artifact_service import ArtifactService
from ai_canvas.application.errors import (
    ArtifactImportConflict,
    ArtifactImportNotFound,
    ArtifactRevisionNotFound,
)
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.storage_lifecycle_service import StorageLifecycleService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.config import Settings
from ai_canvas.contracts.artifact import CreateArtifactImportRequest
from ai_canvas.contracts.graph import (
    CreateGraphRevisionRequest,
    GraphSnapshotV2,
    MediaInputData,
    MediaInputNodeV2,
    Point,
    Shot,
    ShotNodeData,
    ShotNodeV2,
)
from ai_canvas.domain.graph.snapshots import normalize_to_v3
from ai_canvas.domain.graph.validation import GraphValidationError
from ai_canvas.infrastructure.artifacts import ArtifactStorageIOError
from ai_canvas.infrastructure.db.models import (
    ArtifactRevisionRow,
    GraphRevisionRow,
    ImportSessionRow,
    StoredBlobRow,
)
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


@dataclass
class DiscardEnvironment:
    projects: ProjectService
    graphs: GraphService
    artifacts: ArtifactService
    session_factory: sessionmaker[Session]
    settings: Settings
    database_path: Path
    engine: Engine
    write_gate: WriteGate


@dataclass
class ThreadCall:
    thread: Thread | None = None
    started: Event = field(default_factory=Event)
    done: Event = field(default_factory=Event)
    value: object | None = None
    error: BaseException | None = None


@pytest.fixture
def discard_environment(tmp_path: Path) -> Iterator[DiscardEnvironment]:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    write_gate = WriteGate()
    settings = Settings(data_dir=tmp_path)
    try:
        yield DiscardEnvironment(
            projects=ProjectService(session_factory, write_gate=write_gate),
            graphs=GraphService(session_factory, write_gate=write_gate),
            artifacts=ArtifactService(
                session_factory,
                settings=settings,
                write_gate=write_gate,
            ),
            session_factory=session_factory,
            settings=settings,
            database_path=database_path,
            engine=engine,
            write_gate=write_gate,
        )
    finally:
        engine.dispose()


def _fixture(name: str) -> bytes:
    path = Path(__file__).parents[1] / "fixtures" / name
    return base64.b64decode(path.read_text(encoding="ascii"))


async def _chunks(content: bytes) -> AsyncIterator[bytes]:
    yield content


def _png(color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", (4, 3), color).save(output, format="PNG")
    return output.getvalue()


def _media_snapshot(revision_id: UUID) -> GraphSnapshotV2:
    return GraphSnapshotV2(
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


def _start_thread(callback: Callable[[], object]) -> ThreadCall:
    call = ThreadCall()

    def run() -> None:
        call.started.set()
        try:
            call.value = callback()
        except BaseException as error:
            call.error = error
        finally:
            call.done.set()

    call.thread = Thread(target=run, daemon=True)
    call.thread.start()
    return call


def _join_thread(call: ThreadCall, *, timeout: float = 5.0) -> None:
    assert call.thread is not None
    call.thread.join(timeout=timeout)
    assert not call.thread.is_alive(), "worker thread did not terminate"
    assert call.done.is_set()


def _complete(
    environment: DiscardEnvironment,
    project_id: UUID,
    *,
    payload: bytes | None = None,
    requested_kind: str = "image",
    replace_revision_id: UUID | None = None,
):
    content = payload or _fixture("tiny_png.b64")
    declaration = CreateArtifactImportRequest(
        client_import_id=uuid4(),
        filename="clip.mp4" if requested_kind == "video" else "frame.png",
        expected_byte_size=len(content),
        requested_media_kind=requested_kind,
        declared_content_type="video/mp4" if requested_kind == "video" else "image/png",
        replace_revision_id=replace_revision_id,
    )
    created = environment.artifacts.create_import(project_id, declaration)
    revision = asyncio.run(
        environment.artifacts.upload_content(
            project_id,
            created.import_id,
            _chunks(content),
            content_length=len(content),
        )
    )
    return created, revision, declaration


def _save_snapshot(
    environment: DiscardEnvironment,
    project_id: UUID,
    snapshot: GraphSnapshotV2,
) -> None:
    active = environment.graphs.get_active(project_id)
    environment.graphs.create_revision(
        project_id,
        CreateGraphRevisionRequest(
            base_revision_id=active.revision_id,
            client_mutation_id=uuid4(),
            snapshot=snapshot,
        ),
    )


def _discard(
    environment: DiscardEnvironment,
    project_id: UUID,
    import_id: UUID,
    client_import_id: UUID,
    revision_id: UUID | None,
):
    return environment.artifacts.discard_import_result(
        project_id,
        import_id,
        client_import_id=client_import_id,
        expected_revision_id=revision_id,
    )


def test_completed_unshared_result_is_tombstoned_and_its_blob_is_retained_for_gc(
    discard_environment: DiscardEnvironment,
) -> None:
    project = discard_environment.projects.create("Discard")
    created, revision, declaration = _complete(discard_environment, project.id)
    digest = hashlib.sha256(_fixture("tiny_png.b64")).hexdigest()
    blob_path = discard_environment.artifacts._store().blob_path(digest)
    assert blob_path.is_file()

    result = _discard(
        discard_environment,
        project.id,
        created.import_id,
        declaration.client_import_id,
        revision.revision_id,
    )

    assert result.outcome == "discarded"
    assert result.content_cleanup == "retained_shared"
    assert blob_path.is_file()
    with pytest.raises(ArtifactImportNotFound):
        discard_environment.artifacts.get_import(project.id, created.import_id)
    with pytest.raises(ArtifactRevisionNotFound):
        discard_environment.artifacts.get_revision(revision.revision_id)
    with pytest.raises(ArtifactRevisionNotFound):
        discard_environment.artifacts.get_content(revision.revision_id)

    replay = _discard(
        discard_environment,
        project.id,
        created.import_id,
        declaration.client_import_id,
        revision.revision_id,
    )
    assert replay.outcome == "already_discarded"
    assert replay.content_cleanup == "retained_shared"


def test_shared_blob_is_retained_for_another_live_revision(
    discard_environment: DiscardEnvironment,
) -> None:
    first_project = discard_environment.projects.create("First")
    second_project = discard_environment.projects.create("Second")
    first_import, first_revision, first_declaration = _complete(
        discard_environment,
        first_project.id,
    )
    _second_import, second_revision, _second_declaration = _complete(
        discard_environment,
        second_project.id,
    )

    result = _discard(
        discard_environment,
        first_project.id,
        first_import.import_id,
        first_declaration.client_import_id,
        first_revision.revision_id,
    )

    assert result.content_cleanup == "retained_shared"
    assert discard_environment.artifacts.get_content(second_revision.revision_id).body == (
        _fixture("tiny_png.b64")
    )


def test_last_discarded_revision_retains_physical_blob_for_confirmed_gc(
    discard_environment: DiscardEnvironment,
) -> None:
    project = discard_environment.projects.create("Formal GC")
    created, revision, declaration = _complete(discard_environment, project.id)
    digest = hashlib.sha256(_fixture("tiny_png.b64")).hexdigest()
    blob_path = discard_environment.artifacts._store().blob_path(digest)

    result = _discard(
        discard_environment,
        project.id,
        created.import_id,
        declaration.client_import_id,
        revision.revision_id,
    )

    assert result.content_cleanup == "retained_shared"
    assert blob_path.is_file()
    with discard_environment.session_factory() as session:
        blob = session.get(StoredBlobRow, digest)
        assert blob is not None and blob.logical_ref_count == 0
    plan = StorageLifecycleService(
        discard_environment.session_factory,
        settings=discard_environment.settings,
        free_space_bytes=lambda: 100 * 1024**3,
    ).create_gc_plan()
    assert len(plan.entries) == 1
    assert plan.entries[0].target_ids == (str(revision.revision_id),)


def test_discarding_a_replacement_preserves_the_old_revision_and_artifact(
    discard_environment: DiscardEnvironment,
) -> None:
    project = discard_environment.projects.create("Replacement")
    _first_import, first_revision, _first_declaration = _complete(
        discard_environment,
        project.id,
    )
    _save_snapshot(
        discard_environment,
        project.id,
        GraphSnapshotV2(
            nodes=[
                MediaInputNodeV2(
                    id=uuid4(),
                    kind="media_input",
                    position=Point(x=0, y=0),
                    data=MediaInputData(
                        artifact_revision_id=first_revision.revision_id,
                        media_kind="image",
                    ),
                )
            ]
        ),
    )
    replacement_import, replacement, replacement_declaration = _complete(
        discard_environment,
        project.id,
        replace_revision_id=first_revision.revision_id,
    )

    result = _discard(
        discard_environment,
        project.id,
        replacement_import.import_id,
        replacement_declaration.client_import_id,
        replacement.revision_id,
    )

    assert result.content_cleanup == "retained_shared"
    assert replacement.artifact_id == first_revision.artifact_id
    assert discard_environment.artifacts.get_revision(first_revision.revision_id) == first_revision
    assert discard_environment.artifacts.get_content(first_revision.revision_id).body == (
        _fixture("tiny_png.b64")
    )


@pytest.mark.parametrize(
    "reference_slot",
    ("media", "shot_image", "shot_video", "shot_reference"),
)
def test_any_active_or_historical_graph_reference_blocks_discard(
    discard_environment: DiscardEnvironment,
    reference_slot: str,
) -> None:
    project = discard_environment.projects.create(reference_slot)
    requested_kind = "video" if reference_slot == "shot_video" else "image"
    payload = (
        _fixture("tiny_mp4.b64")
        if requested_kind == "video"
        else _fixture("tiny_png.b64")
    )
    created, revision, declaration = _complete(
        discard_environment,
        project.id,
        payload=payload,
        requested_kind=requested_kind,
    )
    shot_id = uuid4()
    snapshot = GraphSnapshotV2()
    if reference_slot == "media":
        snapshot = GraphSnapshotV2(
            nodes=[
                MediaInputNodeV2(
                    id=uuid4(),
                    kind="media_input",
                    position=Point(x=0, y=0),
                    data=MediaInputData(
                        artifact_revision_id=revision.revision_id,
                        media_kind=requested_kind,
                    ),
                )
            ]
        )
    else:
        shot = Shot(id=shot_id, order=0)
        if reference_slot == "shot_image":
            shot = shot.model_copy(update={"image_revision_id": revision.revision_id})
        elif reference_slot == "shot_video":
            shot = shot.model_copy(update={"video_revision_id": revision.revision_id})
        else:
            shot = shot.model_copy(update={"reference_revision_ids": [revision.revision_id]})
        snapshot = GraphSnapshotV2(
            nodes=[
                ShotNodeV2(
                    id=uuid4(),
                    kind="shot",
                    position=Point(x=0, y=0),
                    data=ShotNodeData(shot_id=shot_id),
                )
            ],
            shots=[shot],
        )
    _save_snapshot(discard_environment, project.id, snapshot)
    _save_snapshot(discard_environment, project.id, GraphSnapshotV2())

    with pytest.raises(ArtifactImportConflict) as captured:
        _discard(
            discard_environment,
            project.id,
            created.import_id,
            declaration.client_import_id,
            revision.revision_id,
        )

    assert captured.value.reason.value == "result_referenced"
    assert discard_environment.artifacts.get_revision(revision.revision_id) == revision


def test_other_projects_historical_graph_reference_blocks_discard_without_mutation(
    discard_environment: DiscardEnvironment,
) -> None:
    owner = discard_environment.projects.create("Artifact owner")
    referencing = discard_environment.projects.create("Historical reference owner")
    created, revision, declaration = _complete(discard_environment, owner.id)
    payload = _fixture("tiny_png.b64")
    blob_path = discard_environment.artifacts._store().blob_path(
        hashlib.sha256(payload).hexdigest()
    )
    _save_snapshot(
        discard_environment,
        referencing.id,
        _media_snapshot(revision.revision_id),
    )
    _save_snapshot(discard_environment, referencing.id, GraphSnapshotV2())
    assert (
        discard_environment.graphs.get_active(referencing.id).snapshot
        == normalize_to_v3(GraphSnapshotV2())
    )

    with pytest.raises(ArtifactImportConflict) as captured:
        _discard(
            discard_environment,
            owner.id,
            created.import_id,
            declaration.client_import_id,
            revision.revision_id,
        )

    assert captured.value.reason.value == "result_referenced"
    assert blob_path.read_bytes() == payload
    assert discard_environment.artifacts.get_revision(revision.revision_id) == revision
    assert discard_environment.artifacts.get_content(revision.revision_id).body == payload
    with discard_environment.session_factory() as session:
        import_row = session.get(ImportSessionRow, str(created.import_id))
        revision_row = session.get(ArtifactRevisionRow, str(revision.revision_id))
        assert import_row is not None
        assert import_row.discarded_at is None
        assert import_row.discard_cleanup_pending is False
        assert revision_row is not None
        assert revision_row.discarded_at is None


def test_scope_identity_revision_and_terminal_state_are_checked_before_discard(
    discard_environment: DiscardEnvironment,
) -> None:
    owner = discard_environment.projects.create("Owner")
    other = discard_environment.projects.create("Other")
    created, revision, declaration = _complete(discard_environment, owner.id)

    with pytest.raises(ArtifactImportNotFound):
        _discard(
            discard_environment,
            other.id,
            created.import_id,
            declaration.client_import_id,
            revision.revision_id,
        )
    for client_id, revision_id in (
        (uuid4(), revision.revision_id),
        (declaration.client_import_id, uuid4()),
    ):
        with pytest.raises(ArtifactImportConflict) as captured:
            _discard(
                discard_environment,
                owner.id,
                created.import_id,
                client_id,
                revision_id,
            )
        assert captured.value.reason.value == "discard_precondition_failed"
    assert discard_environment.artifacts.get_revision(revision.revision_id) == revision

    pending = discard_environment.artifacts.create_import(
        owner.id,
        CreateArtifactImportRequest(
            client_import_id=uuid4(),
            filename="pending.png",
            expected_byte_size=1,
            requested_media_kind="image",
        ),
    )
    with pytest.raises(ArtifactImportConflict) as captured:
        _discard(
            discard_environment,
            owner.id,
            pending.import_id,
            pending.client_import_id,
            None,
        )
    assert captured.value.reason.value == "invalid_state"


@pytest.mark.parametrize(
    "provenance_tamper",
    ("external_source_kind", "cross_project_source"),
)
def test_discard_fails_closed_when_revision_provenance_is_tampered(
    discard_environment: DiscardEnvironment,
    provenance_tamper: str,
) -> None:
    owner = discard_environment.projects.create("Provenance owner")
    other = discard_environment.projects.create("Provenance other")
    created, revision, declaration = _complete(discard_environment, owner.id)
    blob_path = discard_environment.artifacts._store().blob_path(
        hashlib.sha256(_fixture("tiny_png.b64")).hexdigest()
    )

    with discard_environment.session_factory.begin() as session:
        revision_row = session.get(ArtifactRevisionRow, str(revision.revision_id))
        assert revision_row is not None
        if provenance_tamper == "external_source_kind":
            revision_row.source_kind = "external"
        else:
            revision_row.source_project_id = str(other.id)

    with pytest.raises(ArtifactImportConflict) as captured:
        _discard(
            discard_environment,
            owner.id,
            created.import_id,
            declaration.client_import_id,
            revision.revision_id,
        )

    assert captured.value.reason.value == "discard_precondition_failed"
    assert blob_path.is_file()
    with discard_environment.session_factory() as session:
        import_row = session.get(ImportSessionRow, str(created.import_id))
        revision_row = session.get(ArtifactRevisionRow, str(revision.revision_id))
        assert import_row is not None and import_row.discarded_at is None
        assert revision_row is not None and revision_row.discarded_at is None


def test_failed_import_discards_exact_staging_and_promoted_orphan(
    discard_environment: DiscardEnvironment,
) -> None:
    project = discard_environment.projects.create("Failed")
    content = b"failed-import-content"
    declaration = CreateArtifactImportRequest(
        client_import_id=uuid4(),
        filename="failed.png",
        expected_byte_size=len(content),
        requested_media_kind="image",
    )
    created = discard_environment.artifacts.create_import(project.id, declaration)
    store = discard_environment.artifacts._store("image")
    staged = store.stage_chunks(
        created.import_id,
        [content],
        declared_size=len(content),
    )
    promoted = store.promote(staged)
    store.staging_root.mkdir(parents=True, exist_ok=True)
    staging_path = store.staging_path(created.import_id)
    staging_path.write_bytes(content)
    neighbour = store.staging_path(uuid4())
    neighbour.write_bytes(b"keep")
    with discard_environment.session_factory.begin() as session:
        row = session.get(ImportSessionRow, str(created.import_id))
        assert row is not None
        row.state = "FAILED_RETRYABLE"
        row.received_byte_size = len(content)
        row.computed_sha256 = promoted.sha256
        row.failure_code = "upload_interrupted"

    result = _discard(
        discard_environment,
        project.id,
        created.import_id,
        declaration.client_import_id,
        None,
    )

    assert result.outcome == "discarded"
    assert result.content_cleanup == "removed"
    assert not promoted.path.exists()
    assert not staging_path.exists()
    assert neighbour.read_bytes() == b"keep"


def test_corrupt_persisted_digest_stays_pending_without_path_interpretation(
    discard_environment: DiscardEnvironment,
) -> None:
    project = discard_environment.projects.create("Corrupt digest")
    declaration = CreateArtifactImportRequest(
        client_import_id=uuid4(),
        filename="failed.png",
        expected_byte_size=1,
        requested_media_kind="image",
    )
    created = discard_environment.artifacts.create_import(project.id, declaration)
    sentinel = discard_environment.settings.data_dir / "sentinel"
    sentinel.write_bytes(b"keep")
    with discard_environment.session_factory.begin() as session:
        row = session.get(ImportSessionRow, str(created.import_id))
        assert row is not None
        row.state = "FAILED_RETRYABLE"
        row.received_byte_size = 1
        row.computed_sha256 = "g" * 64
        row.failure_code = "upload_interrupted"

    result = _discard(
        discard_environment,
        project.id,
        created.import_id,
        declaration.client_import_id,
        None,
    )

    assert result.content_cleanup == "pending"
    assert sentinel.read_bytes() == b"keep"


def test_completed_discard_does_not_unlink_through_import_cleanup(
    discard_environment: DiscardEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = discard_environment.projects.create("Recovery")
    created, revision, declaration = _complete(discard_environment, project.id)
    store = discard_environment.artifacts._store()
    blob_path = store.blob_path(hashlib.sha256(_fixture("tiny_png.b64")).hexdigest())
    def fail_remove(self, digest, expected_size):
        raise ArtifactStorageIOError

    monkeypatch.setattr(type(store), "remove_blob", fail_remove)
    result = _discard(
        discard_environment,
        project.id,
        created.import_id,
        declaration.client_import_id,
        revision.revision_id,
    )

    assert result.outcome == "discarded"
    assert result.content_cleanup == "retained_shared"
    assert blob_path.exists()
    with discard_environment.session_factory() as session:
        row = session.get(ImportSessionRow, str(created.import_id))
        assert row is not None
        assert row.discarded_at is not None
        assert row.discard_cleanup_pending is False

    restarted = ArtifactService(
        discard_environment.session_factory,
        settings=discard_environment.settings,
        write_gate=WriteGate(),
    )
    restarted.recover_startup()
    assert blob_path.exists()
    with discard_environment.session_factory() as session:
        row = session.get(ImportSessionRow, str(created.import_id))
        assert row is not None
        assert row.discard_cleanup_pending is False


def test_matching_validating_import_keeps_cleanup_pending_then_orphan_is_recovered(
    discard_environment: DiscardEnvironment,
) -> None:
    project = discard_environment.projects.create("Race")
    created, revision, declaration = _complete(discard_environment, project.id)
    digest = hashlib.sha256(_fixture("tiny_png.b64")).hexdigest()
    second = discard_environment.artifacts.create_import(
        project.id,
        CreateArtifactImportRequest(
            client_import_id=uuid4(),
            filename="same.png",
            expected_byte_size=len(_fixture("tiny_png.b64")),
            requested_media_kind="image",
            declared_content_type="image/png",
        ),
    )
    with discard_environment.session_factory.begin() as session:
        row = session.get(ImportSessionRow, str(second.import_id))
        assert row is not None
        row.state = "VALIDATING"
        row.received_byte_size = len(_fixture("tiny_png.b64"))
        row.computed_sha256 = digest

    result = _discard(
        discard_environment,
        project.id,
        created.import_id,
        declaration.client_import_id,
        revision.revision_id,
    )

    assert result.content_cleanup == "retained_shared"
    assert discard_environment.artifacts._store().blob_path(digest).is_file()
    with discard_environment.session_factory.begin() as session:
        row = session.get(ImportSessionRow, str(second.import_id))
        assert row is not None
        row.state = "FAILED_RETRYABLE"
        row.failure_code = "upload_interrupted"

    discard_environment.artifacts.recover_startup()

    assert discard_environment.artifacts._store().blob_path(digest).is_file()
    with discard_environment.session_factory() as session:
        row = session.get(ImportSessionRow, str(created.import_id))
        assert row is not None
        assert row.discard_cleanup_pending is False


def test_graph_save_rejects_a_discarded_revision(
    discard_environment: DiscardEnvironment,
) -> None:
    project = discard_environment.projects.create("Graph")
    created, revision, declaration = _complete(discard_environment, project.id)
    _discard(
        discard_environment,
        project.id,
        created.import_id,
        declaration.client_import_id,
        revision.revision_id,
    )

    active = discard_environment.graphs.get_active(project.id)
    with pytest.raises(Exception, match="artifact revision is missing"):
        discard_environment.graphs.create_revision(
            project.id,
            CreateGraphRevisionRequest(
                base_revision_id=active.revision_id,
                client_mutation_id=uuid4(),
                snapshot=GraphSnapshotV2(
                    nodes=[
                        MediaInputNodeV2(
                            id=uuid4(),
                            kind="media_input",
                            position=Point(x=0, y=0),
                            data=MediaInputData(
                                artifact_revision_id=revision.revision_id,
                                media_kind="image",
                            ),
                        )
                    ]
                ),
            ),
        )


def test_tombstoned_client_import_id_cannot_be_recreated(
    discard_environment: DiscardEnvironment,
) -> None:
    project = discard_environment.projects.create("No resurrection")
    created, revision, declaration = _complete(discard_environment, project.id)
    _discard(
        discard_environment,
        project.id,
        created.import_id,
        declaration.client_import_id,
        revision.revision_id,
    )

    with pytest.raises(ArtifactImportNotFound):
        discard_environment.artifacts.create_import(project.id, declaration)
    with discard_environment.session_factory() as session:
        rows = list(
            session.scalars(
                select(ImportSessionRow).where(
                    ImportSessionRow.client_import_id
                    == str(declaration.client_import_id)
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].discarded_at is not None


def test_corrupt_persisted_graph_history_fails_closed_before_tombstoning(
    discard_environment: DiscardEnvironment,
) -> None:
    project = discard_environment.projects.create("Corrupt history")
    created, revision, declaration = _complete(discard_environment, project.id)
    with discard_environment.session_factory.begin() as session:
        graph_revision = session.scalar(select(GraphRevisionRow))
        assert graph_revision is not None
        graph_revision.snapshot_json = "{not-json"

    with pytest.raises(ArtifactImportConflict) as captured:
        _discard(
            discard_environment,
            project.id,
            created.import_id,
            declaration.client_import_id,
            revision.revision_id,
        )

    assert captured.value.reason.value == "result_referenced"
    with discard_environment.session_factory() as session:
        import_row = session.get(ImportSessionRow, str(created.import_id))
        revision_row = session.get(ArtifactRevisionRow, str(revision.revision_id))
        assert import_row is not None and import_row.discarded_at is None
        assert revision_row is not None and revision_row.discarded_at is None


def test_other_import_replacement_reference_blocks_discard(
    discard_environment: DiscardEnvironment,
) -> None:
    project = discard_environment.projects.create("Replacement reference")
    created, revision, declaration = _complete(discard_environment, project.id)
    _save_snapshot(
        discard_environment,
        project.id,
        GraphSnapshotV2(
            nodes=[
                MediaInputNodeV2(
                    id=uuid4(),
                    kind="media_input",
                    position=Point(x=0, y=0),
                    data=MediaInputData(
                        artifact_revision_id=revision.revision_id,
                        media_kind="image",
                    ),
                )
            ]
        ),
    )
    replacement = discard_environment.artifacts.create_import(
        project.id,
        CreateArtifactImportRequest(
            client_import_id=uuid4(),
            filename="replacement.png",
            expected_byte_size=len(_fixture("tiny_png.b64")),
            requested_media_kind="image",
            replace_revision_id=revision.revision_id,
        ),
    )
    assert replacement.replace_revision_id == revision.revision_id

    with pytest.raises(ArtifactImportConflict) as captured:
        _discard(
            discard_environment,
            project.id,
            created.import_id,
            declaration.client_import_id,
            revision.revision_id,
        )

    assert captured.value.reason.value == "result_referenced"
    with discard_environment.session_factory() as session:
        stored = session.scalar(
            select(ArtifactRevisionRow).where(
                ArtifactRevisionRow.id == str(revision.revision_id)
            )
        )
        assert stored is not None
        assert stored.discarded_at is None


def test_discarded_result_stays_absent_after_real_sqlite_reopen(
    discard_environment: DiscardEnvironment,
) -> None:
    project = discard_environment.projects.create("Reopen")
    created, revision, declaration = _complete(discard_environment, project.id)
    digest = hashlib.sha256(_fixture("tiny_png.b64")).hexdigest()
    blob_path = discard_environment.artifacts._store().blob_path(digest)

    first = _discard(
        discard_environment,
        project.id,
        created.import_id,
        declaration.client_import_id,
        revision.revision_id,
    )
    assert first.outcome == "discarded"
    assert first.content_cleanup == "retained_shared"
    assert blob_path.is_file()

    discard_environment.engine.dispose()
    reopened_engine = create_sqlite_engine(discard_environment.database_path)
    reopened = ArtifactService(
        make_session_factory(reopened_engine),
        settings=discard_environment.settings,
        write_gate=WriteGate(),
    )
    try:
        with pytest.raises(ArtifactImportNotFound):
            reopened.get_import(project.id, created.import_id)
        with pytest.raises(ArtifactRevisionNotFound):
            reopened.get_revision(revision.revision_id)
        with pytest.raises(ArtifactRevisionNotFound):
            reopened.get_content(revision.revision_id)
        assert blob_path.is_file()

        replay = reopened.discard_import_result(
            project.id,
            created.import_id,
            client_import_id=declaration.client_import_id,
            expected_revision_id=revision.revision_id,
        )
        assert replay.outcome == "already_discarded"
        assert replay.content_cleanup == "retained_shared"
    finally:
        reopened_engine.dispose()


def test_completed_result_can_be_discarded_without_a_frontend_revision_precondition(
    discard_environment: DiscardEnvironment,
) -> None:
    project = discard_environment.projects.create("Legacy frontend")
    created, revision, declaration = _complete(discard_environment, project.id)
    blob_path = discard_environment.artifacts._store().blob_path(
        hashlib.sha256(_fixture("tiny_png.b64")).hexdigest()
    )

    result = _discard(
        discard_environment,
        project.id,
        created.import_id,
        declaration.client_import_id,
        None,
    )

    assert result.outcome == "discarded"
    assert result.content_cleanup == "retained_shared"
    assert blob_path.is_file()
    with pytest.raises(ArtifactRevisionNotFound):
        discard_environment.artifacts.get_revision(revision.revision_id)


@pytest.mark.parametrize("state", ("CREATED", "RECEIVING", "VALIDATING"))
def test_nonterminal_import_state_is_rejected_without_discarding(
    discard_environment: DiscardEnvironment,
    state: str,
) -> None:
    project = discard_environment.projects.create(f"Reject {state}")
    declaration = CreateArtifactImportRequest(
        client_import_id=uuid4(),
        filename="pending.png",
        expected_byte_size=len(_fixture("tiny_png.b64")),
        requested_media_kind="image",
        declared_content_type="image/png",
    )
    created = discard_environment.artifacts.create_import(project.id, declaration)
    with discard_environment.session_factory.begin() as session:
        row = session.get(ImportSessionRow, str(created.import_id))
        assert row is not None
        row.state = state

    with pytest.raises(ArtifactImportConflict) as captured:
        _discard(
            discard_environment,
            project.id,
            created.import_id,
            declaration.client_import_id,
            None,
        )

    assert captured.value.reason.value == "invalid_state"
    with discard_environment.session_factory() as session:
        row = session.get(ImportSessionRow, str(created.import_id))
        assert row is not None
        assert row.state == state
        assert row.discarded_at is None


def test_discarding_a_different_digest_replacement_only_removes_the_new_blob(
    discard_environment: DiscardEnvironment,
) -> None:
    project = discard_environment.projects.create("Different replacement")
    old_payload = _png((191, 43, 61))
    new_payload = _png((29, 91, 173))
    _old_import, old_revision, _old_declaration = _complete(
        discard_environment,
        project.id,
        payload=old_payload,
    )
    _save_snapshot(
        discard_environment,
        project.id,
        _media_snapshot(old_revision.revision_id),
    )
    replacement_import, replacement, replacement_declaration = _complete(
        discard_environment,
        project.id,
        payload=new_payload,
        replace_revision_id=old_revision.revision_id,
    )
    old_blob = discard_environment.artifacts._store().blob_path(
        hashlib.sha256(old_payload).hexdigest()
    )
    new_blob = discard_environment.artifacts._store().blob_path(
        hashlib.sha256(new_payload).hexdigest()
    )
    assert old_blob.is_file()
    assert new_blob.is_file()
    assert old_blob != new_blob

    result = _discard(
        discard_environment,
        project.id,
        replacement_import.import_id,
        replacement_declaration.client_import_id,
        replacement.revision_id,
    )

    assert result.outcome == "discarded"
    assert result.content_cleanup == "retained_shared"
    assert old_blob.is_file()
    assert new_blob.is_file()
    assert discard_environment.artifacts.get_content(old_revision.revision_id).body == (
        old_payload
    )
    with pytest.raises(ArtifactRevisionNotFound):
        discard_environment.artifacts.get_revision(replacement.revision_id)


def test_graph_save_wins_the_gate_and_discard_then_fails_as_referenced(
    discard_environment: DiscardEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = discard_environment.projects.create("Graph wins")
    created, revision, declaration = _complete(discard_environment, project.id)
    base = discard_environment.graphs.get_active(project.id)
    snapshot = _media_snapshot(revision.revision_id)
    request = CreateGraphRevisionRequest(
        base_revision_id=base.revision_id,
        client_mutation_id=uuid4(),
        snapshot=snapshot,
    )
    validation_holds_gate = Event()
    release_validation = Event()
    real_validate = GraphService._validate_artifact_references

    def paused_validation(session: Session, candidate: GraphSnapshotV2) -> None:
        real_validate(session, candidate)
        validation_holds_gate.set()
        if not release_validation.wait(timeout=5):
            raise TimeoutError("graph validation race was not released")

    monkeypatch.setattr(
        GraphService,
        "_validate_artifact_references",
        staticmethod(paused_validation),
    )
    save_call = _start_thread(
        lambda: discard_environment.graphs.create_revision(project.id, request)
    )
    try:
        assert validation_holds_gate.wait(timeout=5)
        discard_call = _start_thread(
            lambda: _discard(
                discard_environment,
                project.id,
                created.import_id,
                declaration.client_import_id,
                revision.revision_id,
            )
        )
        assert discard_call.started.wait(timeout=1)
        assert not discard_call.done.wait(timeout=0.1)
    finally:
        release_validation.set()

    _join_thread(save_call)
    _join_thread(discard_call)
    assert save_call.error is None
    assert isinstance(discard_call.error, ArtifactImportConflict)
    assert discard_call.error.reason.value == "result_referenced"
    assert discard_environment.graphs.get_active(project.id).snapshot == normalize_to_v3(
        snapshot
    )
    assert discard_environment.artifacts.get_content(revision.revision_id).body == (
        _fixture("tiny_png.b64")
    )


def test_discard_wins_the_gate_and_graph_save_then_fails_without_dangling_reference(
    discard_environment: DiscardEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = discard_environment.projects.create("Discard wins")
    created, revision, declaration = _complete(discard_environment, project.id)
    base = discard_environment.graphs.get_active(project.id)
    request = CreateGraphRevisionRequest(
        base_revision_id=base.revision_id,
        client_mutation_id=uuid4(),
        snapshot=_media_snapshot(revision.revision_id),
    )
    discard_holds_gate = Event()
    release_discard = Event()
    real_reference_check = ArtifactService._assert_result_is_unreferenced

    def paused_reference_check(
        service: ArtifactService,
        session: Session,
        import_row: ImportSessionRow,
        revision_row: ArtifactRevisionRow,
    ) -> None:
        real_reference_check(service, session, import_row, revision_row)
        discard_holds_gate.set()
        if not release_discard.wait(timeout=5):
            raise TimeoutError("discard race was not released")

    monkeypatch.setattr(
        ArtifactService,
        "_assert_result_is_unreferenced",
        paused_reference_check,
    )
    discard_call = _start_thread(
        lambda: _discard(
            discard_environment,
            project.id,
            created.import_id,
            declaration.client_import_id,
            revision.revision_id,
        )
    )
    try:
        assert discard_holds_gate.wait(timeout=5)
        save_call = _start_thread(
            lambda: discard_environment.graphs.create_revision(project.id, request)
        )
        assert save_call.started.wait(timeout=1)
        assert not save_call.done.wait(timeout=0.1)
    finally:
        release_discard.set()

    _join_thread(discard_call)
    _join_thread(save_call)
    assert discard_call.error is None
    assert getattr(discard_call.value, "outcome") == "discarded"
    assert isinstance(save_call.error, GraphValidationError)
    assert "artifact revision is missing" in str(save_call.error)
    assert discard_environment.graphs.get_active(project.id).revision_id == base.revision_id
    with pytest.raises(ArtifactRevisionNotFound):
        discard_environment.artifacts.get_revision(revision.revision_id)
    blob_path = discard_environment.artifacts._store().blob_path(
        hashlib.sha256(_fixture("tiny_png.b64")).hexdigest()
    )
    assert blob_path.is_file()


def test_same_digest_upload_promoted_before_discard_keeps_new_revision_readable(
    discard_environment: DiscardEnvironment,
) -> None:
    project = discard_environment.projects.create("Upload wins")
    first_import, first_revision, first_declaration = _complete(
        discard_environment,
        project.id,
    )
    payload = _fixture("tiny_png.b64")
    second_declaration = CreateArtifactImportRequest(
        client_import_id=uuid4(),
        filename="same.png",
        expected_byte_size=len(payload),
        requested_media_kind="image",
        declared_content_type="image/png",
    )
    second_import = discard_environment.artifacts.create_import(
        project.id,
        second_declaration,
    )
    promoted = Event()
    release_commit = Event()

    def pause_after_promote(name: str) -> None:
        if name != "after_promote":
            return
        promoted.set()
        if not release_commit.wait(timeout=5):
            raise TimeoutError("same-digest upload race was not released")

    uploading = ArtifactService(
        discard_environment.session_factory,
        settings=discard_environment.settings,
        write_gate=discard_environment.write_gate,
        failpoint=pause_after_promote,
    )
    upload_call = _start_thread(
        lambda: asyncio.run(
            uploading.upload_content(
                project.id,
                second_import.import_id,
                _chunks(payload),
                content_length=len(payload),
            )
        )
    )
    discard_call: ThreadCall | None = None
    try:
        assert promoted.wait(timeout=5)
        discard_call = _start_thread(
            lambda: _discard(
                discard_environment,
                project.id,
                first_import.import_id,
                first_declaration.client_import_id,
                first_revision.revision_id,
            )
        )
        assert discard_call.started.wait(timeout=2)
        assert not discard_call.done.wait(timeout=0.25)
    finally:
        release_commit.set()

    _join_thread(upload_call)
    assert discard_call is not None
    _join_thread(discard_call)
    assert upload_call.error is None
    assert discard_call.error is None
    discarded = discard_call.value
    assert getattr(discarded, "outcome") == "discarded"
    assert getattr(discarded, "content_cleanup") == "retained_shared"
    assert discard_environment.artifacts._store().blob_path(
        hashlib.sha256(payload).hexdigest()
    ).is_file()
    uploaded_revision_id = getattr(upload_call.value, "revision_id")
    assert discard_environment.artifacts.get_content(uploaded_revision_id).body == payload
    with pytest.raises(ArtifactRevisionNotFound):
        discard_environment.artifacts.get_revision(first_revision.revision_id)
    with discard_environment.session_factory() as session:
        discarded_row = session.get(ImportSessionRow, str(first_import.import_id))
        assert discarded_row is not None
        assert discarded_row.discard_cleanup_pending is False


def test_discard_before_same_digest_upload_allows_new_content_to_be_promoted(
    discard_environment: DiscardEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = discard_environment.projects.create("Discard before upload")
    first_import, first_revision, first_declaration = _complete(
        discard_environment,
        project.id,
    )
    payload = _fixture("tiny_png.b64")
    second_declaration = CreateArtifactImportRequest(
        client_import_id=uuid4(),
        filename="same.png",
        expected_byte_size=len(payload),
        requested_media_kind="image",
        declared_content_type="image/png",
    )
    second_import = discard_environment.artifacts.create_import(
        project.id,
        second_declaration,
    )
    blob_path = discard_environment.artifacts._store().blob_path(
        hashlib.sha256(payload).hexdigest()
    )
    discard_holds_gate = Event()
    release_discard = Event()
    upload_claim_committed = Event()
    real_reference_check = ArtifactService._assert_result_is_unreferenced

    def paused_reference_check(
        service: ArtifactService,
        session: Session,
        import_row: ImportSessionRow,
        revision_row: ArtifactRevisionRow,
    ) -> None:
        real_reference_check(service, session, import_row, revision_row)
        discard_holds_gate.set()
        if not release_discard.wait(timeout=5):
            raise TimeoutError("discard-first upload race was not released")

    def observe_upload(name: str) -> None:
        if name == "after_claim_commit":
            upload_claim_committed.set()

    monkeypatch.setattr(
        ArtifactService,
        "_assert_result_is_unreferenced",
        paused_reference_check,
    )
    uploading = ArtifactService(
        discard_environment.session_factory,
        settings=discard_environment.settings,
        write_gate=discard_environment.write_gate,
        failpoint=observe_upload,
    )
    discard_call = _start_thread(
        lambda: _discard(
            discard_environment,
            project.id,
            first_import.import_id,
            first_declaration.client_import_id,
            first_revision.revision_id,
        )
    )
    upload_call: ThreadCall | None = None
    try:
        assert discard_holds_gate.wait(timeout=5)
        upload_call = _start_thread(
            lambda: asyncio.run(
                uploading.upload_content(
                    project.id,
                    second_import.import_id,
                    _chunks(payload),
                    content_length=len(payload),
                )
            )
        )
        assert upload_call.started.wait(timeout=1)
        assert not upload_claim_committed.is_set()
        assert not upload_call.done.is_set()
    finally:
        release_discard.set()

    _join_thread(discard_call)
    assert upload_call is not None
    _join_thread(upload_call)
    assert discard_call.error is None
    assert upload_call.error is None
    assert getattr(discard_call.value, "content_cleanup") == "retained_shared"
    assert upload_claim_committed.is_set()
    new_revision_id = getattr(upload_call.value, "revision_id")
    assert blob_path.is_file()
    assert discard_environment.artifacts.get_content(new_revision_id).body == payload
    with pytest.raises(ArtifactRevisionNotFound):
        discard_environment.artifacts.get_revision(first_revision.revision_id)


def test_two_concurrent_discards_are_idempotent_and_leave_one_file_fact(
    discard_environment: DiscardEnvironment,
) -> None:
    project = discard_environment.projects.create("Concurrent replay")
    created, revision, declaration = _complete(discard_environment, project.id)
    blob_path = discard_environment.artifacts._store().blob_path(
        hashlib.sha256(_fixture("tiny_png.b64")).hexdigest()
    )
    start = Barrier(3)

    def discard_once() -> object:
        start.wait(timeout=5)
        return _discard(
            discard_environment,
            project.id,
            created.import_id,
            declaration.client_import_id,
            revision.revision_id,
        )

    first_call = _start_thread(discard_once)
    second_call = _start_thread(discard_once)
    start.wait(timeout=5)
    _join_thread(first_call)
    _join_thread(second_call)

    assert first_call.error is None
    assert second_call.error is None
    responses = (first_call.value, second_call.value)
    assert sorted(getattr(response, "outcome") for response in responses) == [
        "already_discarded",
        "discarded",
    ]
    assert [getattr(response, "content_cleanup") for response in responses] == [
        "retained_shared",
        "retained_shared",
    ]
    assert blob_path.is_file()
    with pytest.raises(ArtifactRevisionNotFound):
        discard_environment.artifacts.get_content(revision.revision_id)
