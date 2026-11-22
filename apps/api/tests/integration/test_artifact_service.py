from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application import artifact_service as artifact_service_module
from ai_canvas.application.artifact_service import ArtifactService
from ai_canvas.application.errors import (
    ArtifactImportConflict,
    ArtifactImportFailed,
    ArtifactImportNotFound,
)
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.project_service import ProjectService
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
from ai_canvas.infrastructure.db.models import (
    ArtifactRevisionRow,
    ArtifactRow,
    ImportSessionRow,
    GraphDocumentRow,
    GraphRevisionRow,
    StoredBlobRow,
)
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)
from ai_canvas.infrastructure.artifacts import ArtifactStore
from ai_canvas.infrastructure.artifacts import store as artifact_store_module


@dataclass
class ServiceEnvironment:
    projects: ProjectService
    graphs: GraphService
    artifacts: ArtifactService
    session_factory: sessionmaker[Session]


@pytest.fixture
def service_environment(tmp_path: Path) -> Iterator[ServiceEnvironment]:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    write_gate = WriteGate()
    settings = Settings(data_dir=tmp_path)
    try:
        yield ServiceEnvironment(
            projects=ProjectService(session_factory, write_gate=write_gate),
            graphs=GraphService(session_factory, write_gate=write_gate),
            artifacts=ArtifactService(
                session_factory,
                settings=settings,
                write_gate=write_gate,
            ),
            session_factory=session_factory,
        )
    finally:
        engine.dispose()


@contextmanager
def _service_environment(tmp_path: Path, **setting_overrides: object) -> Iterator[ServiceEnvironment]:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    write_gate = WriteGate()
    settings = Settings(data_dir=tmp_path, **setting_overrides)
    try:
        yield ServiceEnvironment(
            projects=ProjectService(session_factory, write_gate=write_gate),
            graphs=GraphService(session_factory, write_gate=write_gate),
            artifacts=ArtifactService(
                session_factory,
                settings=settings,
                write_gate=write_gate,
            ),
            session_factory=session_factory,
        )
    finally:
        engine.dispose()


def _tiny_png() -> bytes:
    fixture = Path(__file__).parents[1] / "fixtures" / "tiny_png.b64"
    return base64.b64decode(fixture.read_text(encoding="ascii"))


def _tiny_mp4() -> bytes:
    fixture = Path(__file__).parents[1] / "fixtures" / "tiny_mp4.b64"
    return base64.b64decode(fixture.read_text(encoding="ascii"))


def _declaration(
    *,
    client_import_id: UUID | None = None,
    byte_size: int | None = None,
    filename: str = "../camera.png",
    declared_content_type: str | None = "image/png",
    replace_revision_id: UUID | None = None,
) -> CreateArtifactImportRequest:
    return CreateArtifactImportRequest(
        client_import_id=client_import_id or uuid4(),
        filename=filename,
        expected_byte_size=byte_size or len(_tiny_png()),
        requested_media_kind="image",
        declared_content_type=declared_content_type,
        replace_revision_id=replace_revision_id,
    )


async def _chunks(content: bytes, *, size: int = 17) -> AsyncIterator[bytes]:
    for offset in range(0, len(content), size):
        yield content[offset : offset + size]


def _complete_import(
    environment: ServiceEnvironment,
    project_id: UUID,
    declaration: CreateArtifactImportRequest | None = None,
    content: bytes | None = None,
):
    payload = _tiny_png() if content is None else content
    request = declaration or _declaration(byte_size=len(payload))
    created = environment.artifacts.create_import(project_id, request)
    revision = asyncio.run(
        environment.artifacts.upload_content(
            project_id,
            created.import_id,
            _chunks(payload),
            content_length=len(payload),
        )
    )
    return created, revision


def _save_snapshot(
    environment: ServiceEnvironment,
    project_id: UUID,
    snapshot: GraphSnapshotV2,
):
    active = environment.graphs.get_active(project_id)
    return environment.graphs.create_revision(
        project_id,
        CreateGraphRevisionRequest(
            base_revision_id=active.revision_id,
            client_mutation_id=uuid4(),
            snapshot=snapshot,
        ),
    )


def _attach_media_revision(
    environment: ServiceEnvironment,
    project_id: UUID,
    revision_id: UUID,
) -> None:
    _save_snapshot(
        environment,
        project_id,
        GraphSnapshotV2(
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
        ),
    )


def test_create_import_is_idempotent_only_for_the_exact_declaration(
    service_environment: ServiceEnvironment,
) -> None:
    project = service_environment.projects.create("Idempotent")
    declaration = _declaration()

    created = service_environment.artifacts.create_import(project.id, declaration)
    replayed = service_environment.artifacts.create_import(project.id, declaration)

    assert replayed == created
    assert replayed.state == "CREATED"
    changed_declarations = (
        declaration.model_copy(update={"filename": "different.png"}),
        declaration.model_copy(update={"expected_byte_size": declaration.expected_byte_size + 1}),
        declaration.model_copy(update={"declared_content_type": "image/jpeg"}),
        declaration.model_copy(update={"requested_media_kind": "video"}),
        declaration.model_copy(update={"replace_revision_id": uuid4()}),
    )
    for changed in changed_declarations:
        with pytest.raises(ArtifactImportConflict) as captured:
            service_environment.artifacts.create_import(project.id, changed)
        assert captured.value.import_id == str(created.import_id)
        assert captured.value.reason.value == "declaration_mismatch"


def test_claim_query_operational_error_never_crosses_the_project_scope(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = service_environment.projects.create("Scoped owner")
    other = service_environment.projects.create("Scoped caller")
    created = service_environment.artifacts.create_import(owner.id, _declaration())
    original_lookup = service_environment.artifacts.repository.import_session
    triggered = False

    def fail_first_lookup(session, import_id):
        nonlocal triggered
        if not triggered:
            triggered = True
            raise OperationalError("claim-query", {}, RuntimeError("synthetic"))
        return original_lookup(session, import_id)

    monkeypatch.setattr(
        service_environment.artifacts.repository,
        "import_session",
        fail_first_lookup,
    )

    observed_error: Exception | None = None
    try:
        asyncio.run(
            service_environment.artifacts.upload_content(
                other.id,
                created.import_id,
                _chunks(_tiny_png()),
                content_length=len(_tiny_png()),
            )
        )
    except Exception as error:
        observed_error = error

    assert triggered
    unchanged = service_environment.artifacts.get_import(owner.id, created.import_id)
    assert unchanged.state == "CREATED"
    assert isinstance(observed_error, ArtifactImportNotFound)


def test_failed_second_claim_never_downgrades_an_existing_receiver(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service_environment.projects.create("Claim owner race")
    created = service_environment.artifacts.create_import(project.id, _declaration())

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def paused() -> AsyncIterator[bytes]:
            entered.set()
            await release.wait()
            yield _tiny_png()

        first = asyncio.create_task(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                paused(),
                content_length=len(_tiny_png()),
            )
        )
        await entered.wait()
        original_lookup = service_environment.artifacts.repository.import_session
        triggered = False

        def fail_second_claim_lookup(session, import_id):
            nonlocal triggered
            if not triggered:
                triggered = True
                raise OperationalError("second-claim-query", {}, RuntimeError("synthetic"))
            return original_lookup(session, import_id)

        monkeypatch.setattr(
            service_environment.artifacts.repository,
            "import_session",
            fail_second_claim_lookup,
        )
        second_error: Exception | None = None
        try:
            await service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(_tiny_png()),
                content_length=len(_tiny_png()),
            )
        except Exception as error:
            second_error = error
        state_during_first = service_environment.artifacts.get_import(
            project.id,
            created.import_id,
        ).state
        release.set()
        first_error: Exception | None = None
        first_revision = None
        try:
            first_revision = await first
        except Exception as error:
            first_error = error
        return triggered, state_during_first, second_error, first_error, first_revision

    triggered, state_during_first, second_error, first_error, first_revision = asyncio.run(
        scenario()
    )

    assert triggered
    assert state_during_first == "RECEIVING"
    assert isinstance(second_error, ArtifactImportConflict)
    assert second_error.reason.value == "upload_in_progress"
    assert first_error is None
    completed = service_environment.artifacts.get_import(project.id, created.import_id)
    assert completed.state == "COMPLETED"
    assert completed.artifact_revision == first_revision


def test_claim_query_operational_error_preserves_final_state_semantics(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service_environment.projects.create("Final claim state")
    created = service_environment.artifacts.create_import(project.id, _declaration())
    with service_environment.session_factory.begin() as session:
        row = session.get(ImportSessionRow, str(created.import_id))
        assert row is not None
        row.state = "FAILED_FINAL"
        row.failure_code = "size_mismatch"
    original_lookup = service_environment.artifacts.repository.import_session
    triggered = False

    def fail_first_lookup(session, import_id):
        nonlocal triggered
        if not triggered:
            triggered = True
            raise OperationalError("final-claim-query", {}, RuntimeError("synthetic"))
        return original_lookup(session, import_id)

    monkeypatch.setattr(
        service_environment.artifacts.repository,
        "import_session",
        fail_first_lookup,
    )

    with pytest.raises(ArtifactImportConflict) as captured:
        asyncio.run(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(_tiny_png()),
                content_length=len(_tiny_png()),
            )
        )

    assert triggered
    assert captured.value.reason.value == "invalid_state"
    unchanged = service_environment.artifacts.get_import(project.id, created.import_id)
    assert unchanged.state == "FAILED_FINAL"
    assert unchanged.failure_code.value == "size_mismatch"


def test_import_completes_with_sanitized_provenance_and_workspace_blob_deduplication(
    service_environment: ServiceEnvironment,
) -> None:
    first_project = service_environment.projects.create("First")
    second_project = service_environment.projects.create("Second")

    _first_session, first = _complete_import(service_environment, first_project.id)
    _second_session, second = _complete_import(service_environment, second_project.id)

    assert first.revision_id != second.revision_id
    assert first.artifact_id != second.artifact_id
    assert first.sequence == second.sequence == 1
    assert first.source_project_id == first.origin_project_id == first_project.id
    assert second.source_project_id == second.origin_project_id == second_project.id
    assert first.source_kind == second.source_kind == "import"
    assert first.display_filename == second.display_filename == "camera.png"
    assert first.media_kind == second.media_kind == "image"
    assert first.media_type == second.media_type == "image/png"
    assert first.metadata.model_dump(mode="json") == {
        "kind": "image",
        "width": 4,
        "height": 3,
    }
    with service_environment.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(StoredBlobRow)) == 1
        assert session.scalar(select(func.count()).select_from(ArtifactRow)) == 2
        assert session.scalar(select(func.count()).select_from(ArtifactRevisionRow)) == 2


def test_interrupted_upload_is_retryable_with_the_same_import_id(
    service_environment: ServiceEnvironment,
) -> None:
    project = service_environment.projects.create("Retry")
    declaration = _declaration()
    created = service_environment.artifacts.create_import(project.id, declaration)

    async def interrupted() -> AsyncIterator[bytes]:
        yield _tiny_png()[:13]
        raise OSError("synthetic receive failure")

    with pytest.raises(ArtifactImportFailed) as captured:
        asyncio.run(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                interrupted(),
                content_length=len(_tiny_png()),
            )
        )

    assert captured.value.failure_code.value == "upload_interrupted"
    failed = service_environment.artifacts.get_import(project.id, created.import_id)
    assert failed.state == "FAILED_RETRYABLE"
    assert failed.failure_code.value == "upload_interrupted"
    assert service_environment.artifacts.create_import(project.id, declaration).import_id == created.import_id

    revision = asyncio.run(
        service_environment.artifacts.upload_content(
            project.id,
            created.import_id,
            _chunks(_tiny_png()),
            content_length=len(_tiny_png()),
        )
    )
    completed = service_environment.artifacts.get_import(project.id, created.import_id)
    assert completed.state == "COMPLETED"
    assert completed.failure_code is None
    assert completed.artifact_revision == revision


def test_retryable_upload_recovers_from_a_stale_exact_part_after_unlink_failures(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service_environment.projects.create("Retry stale part")
    created = service_environment.artifacts.create_import(project.id, _declaration())
    store = service_environment.artifacts._store("image")
    exact_part = store.staging_path(created.import_id)
    original_unlink = Path.unlink
    failures_remaining = 2

    def flaky_unlink(path: Path, *args, **kwargs) -> None:
        nonlocal failures_remaining
        if path == exact_part and failures_remaining:
            failures_remaining -= 1
            raise PermissionError("synthetic temporary unlink failure")
        original_unlink(path, *args, **kwargs)

    async def interrupted() -> AsyncIterator[bytes]:
        yield _tiny_png()[:13]
        raise OSError("synthetic receive failure")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", flaky_unlink)
        with pytest.raises(ArtifactImportFailed) as initial_failure:
            asyncio.run(
                service_environment.artifacts.upload_content(
                    project.id,
                    created.import_id,
                    interrupted(),
                    content_length=len(_tiny_png()),
                )
            )
        assert initial_failure.value.failure_code.value == "upload_interrupted"
        assert exact_part.is_file()
        unrelated_part = store.staging_path(uuid4())
        unrelated_part.write_bytes(b"unrelated")

        with pytest.raises(ArtifactImportFailed) as cleanup_failure:
            asyncio.run(
                service_environment.artifacts.upload_content(
                    project.id,
                    created.import_id,
                    _chunks(_tiny_png()),
                    content_length=len(_tiny_png()),
                )
            )
        assert cleanup_failure.value.failure_code.value == "upload_interrupted"
        failed = service_environment.artifacts.get_import(project.id, created.import_id)
        assert failed.state == "FAILED_RETRYABLE"
        assert exact_part.is_file()

    revision = asyncio.run(
        service_environment.artifacts.upload_content(
            project.id,
            created.import_id,
            _chunks(_tiny_png()),
            content_length=len(_tiny_png()),
        )
    )
    completed = service_environment.artifacts.get_import(project.id, created.import_id)
    assert completed.state == "COMPLETED"
    assert completed.artifact_revision == revision
    assert not exact_part.exists()
    assert unrelated_part.read_bytes() == b"unrelated"


def test_final_media_failure_requires_a_new_client_import_id(
    service_environment: ServiceEnvironment,
) -> None:
    project = service_environment.projects.create("Final")
    corrupt = b"not-a-real-png"
    declaration = _declaration(byte_size=len(corrupt))
    created = service_environment.artifacts.create_import(project.id, declaration)

    with pytest.raises(ArtifactImportFailed) as captured:
        asyncio.run(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(corrupt),
                content_length=len(corrupt),
            )
        )

    assert captured.value.failure_code.value in {"corrupt_media", "unsupported_media"}
    failed = service_environment.artifacts.get_import(project.id, created.import_id)
    assert failed.state == "FAILED_FINAL"
    assert service_environment.artifacts.create_import(project.id, declaration).state == "FAILED_FINAL"
    with pytest.raises(ArtifactImportConflict) as invalid_state:
        asyncio.run(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(corrupt),
                content_length=len(corrupt),
            )
        )
    assert invalid_state.value.reason.value == "invalid_state"

    replacement_declaration = _declaration(client_import_id=uuid4(), byte_size=len(_tiny_png()))
    replacement_session, replacement = _complete_import(
        service_environment,
        project.id,
        replacement_declaration,
    )
    assert replacement_session.import_id != created.import_id
    assert replacement.media_type == "image/png"


def test_completed_init_and_raw_put_replays_compare_the_full_digest(
    service_environment: ServiceEnvironment,
) -> None:
    project = service_environment.projects.create("Completed replay")
    declaration = _declaration()
    created, revision = _complete_import(service_environment, project.id, declaration)

    replayed_init = service_environment.artifacts.create_import(project.id, declaration)
    assert replayed_init.state == "COMPLETED"
    assert replayed_init.artifact_revision == revision

    consumed = 0

    async def observed(content: bytes) -> AsyncIterator[bytes]:
        nonlocal consumed
        for byte in content:
            consumed += 1
            yield bytes([byte])

    replayed_revision = asyncio.run(
        service_environment.artifacts.upload_content(
            project.id,
            created.import_id,
            observed(_tiny_png()),
            content_length=len(_tiny_png()),
        )
    )
    assert replayed_revision == revision
    assert consumed == len(_tiny_png())

    changed = bytearray(_tiny_png())
    changed[-1] ^= 1
    consumed = 0
    with pytest.raises(ArtifactImportConflict) as captured:
        asyncio.run(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                observed(bytes(changed)),
                content_length=len(changed),
            )
        )
    assert captured.value.reason.value == "content_replay_mismatch"
    assert consumed == len(changed)
    assert service_environment.artifacts.get_import(project.id, created.import_id).state == "COMPLETED"

    consumed = 0
    with pytest.raises(ArtifactImportConflict) as declared_mismatch:
        asyncio.run(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                observed(_tiny_png()),
                content_length=len(_tiny_png()) - 1,
            )
        )
    assert declared_mismatch.value.reason.value == "content_replay_mismatch"
    assert consumed == len(_tiny_png())
    assert service_environment.artifacts.get_import(project.id, created.import_id).state == "COMPLETED"


def test_completed_replay_drains_an_oversized_body_before_reporting_mismatch(
    tmp_path: Path,
) -> None:
    limit = len(_tiny_png())
    with _service_environment(
        tmp_path,
        artifact_image_max_bytes=limit,
        artifact_video_max_bytes=limit,
    ) as environment:
        project = environment.projects.create("Oversized completed replay")
        created, revision = _complete_import(environment, project.id)
        replay_chunks = [b"x" * limit, b"y", b"tail-after-limit"]
        consumed: list[bytes] = []
        reached_eof = False

        async def observed() -> AsyncIterator[bytes]:
            nonlocal reached_eof
            for chunk in replay_chunks:
                consumed.append(chunk)
                yield chunk
            reached_eof = True

        with pytest.raises(ArtifactImportConflict) as captured:
            asyncio.run(
                environment.artifacts.upload_content(
                    project.id,
                    created.import_id,
                    observed(),
                    content_length=sum(len(chunk) for chunk in replay_chunks),
                )
            )

        assert captured.value.reason.value == "content_replay_mismatch"
        assert consumed == replay_chunks
        assert reached_eof is True
        completed = environment.artifacts.get_import(project.id, created.import_id)
        assert completed.state == "COMPLETED"
        assert completed.artifact_revision == revision


def test_completed_replay_receive_interruption_does_not_pollute_the_completed_session(
    service_environment: ServiceEnvironment,
) -> None:
    project = service_environment.projects.create("Completed interruption")
    created, revision = _complete_import(service_environment, project.id)

    async def interrupted() -> AsyncIterator[bytes]:
        yield _tiny_png()[:10]
        raise OSError("synthetic replay interruption")

    with pytest.raises(ArtifactImportFailed) as captured:
        asyncio.run(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                interrupted(),
                content_length=len(_tiny_png()),
            )
        )
    assert captured.value.failure_code.value == "upload_interrupted"
    completed = service_environment.artifacts.get_import(project.id, created.import_id)
    assert completed.state == "COMPLETED"
    assert completed.artifact_revision == revision


def test_completed_replay_drains_before_a_digest_database_read_failure(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service_environment.projects.create("Completed digest SQL failure")
    created, revision = _complete_import(service_environment, project.id)
    consumed = 0
    reached_eof = False

    async def observed() -> AsyncIterator[bytes]:
        nonlocal consumed, reached_eof
        for chunk in (_tiny_png()[:17], _tiny_png()[17:]):
            consumed += len(chunk)
            yield chunk
        reached_eof = True

    def fail_digest_read(_import_id):
        raise OperationalError("completed-digest", {}, RuntimeError("synthetic"))

    monkeypatch.setattr(service_environment.artifacts, "_completed_digest", fail_digest_read)

    with pytest.raises(OperationalError):
        asyncio.run(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                observed(),
                content_length=len(_tiny_png()),
            )
        )

    assert consumed == len(_tiny_png())
    assert reached_eof
    completed = service_environment.artifacts.get_import(project.id, created.import_id)
    assert completed.state == "COMPLETED"
    assert completed.artifact_revision == revision


@pytest.mark.parametrize("state", ["RECEIVING", "VALIDATING"])
def test_upload_rejects_every_live_receiver_state(
    service_environment: ServiceEnvironment,
    state: str,
) -> None:
    project = service_environment.projects.create(f"State {state}")
    created = service_environment.artifacts.create_import(project.id, _declaration())
    with service_environment.session_factory.begin() as session:
        row = session.get(ImportSessionRow, str(created.import_id))
        assert row is not None
        row.state = state

    with pytest.raises(ArtifactImportConflict) as captured:
        asyncio.run(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(_tiny_png()),
                content_length=len(_tiny_png()),
            )
        )
    assert captured.value.reason.value == "upload_in_progress"


@pytest.mark.parametrize(
    ("content_length", "payload"),
    (
        (len(_tiny_png()) - 1, _tiny_png()),
        (None, _tiny_png()[:-1]),
        (None, _tiny_png() + b"x"),
    ),
    ids=("declared-length", "streamed-short", "streamed-long"),
)
def test_declared_and_streamed_size_mismatches_are_final(
    service_environment: ServiceEnvironment,
    content_length: int | None,
    payload: bytes,
) -> None:
    project = service_environment.projects.create("Size mismatch")
    created = service_environment.artifacts.create_import(project.id, _declaration())

    with pytest.raises(ArtifactImportFailed) as captured:
        asyncio.run(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(payload),
                content_length=content_length,
            )
        )

    assert captured.value.failure_code.value == "size_mismatch"
    failed = service_environment.artifacts.get_import(project.id, created.import_id)
    assert failed.state == "FAILED_FINAL"
    assert failed.failure_code.value == "size_mismatch"


@pytest.mark.parametrize("deadline_kind", ["idle", "total"])
def test_upload_deadlines_are_retryable(tmp_path: Path, deadline_kind: str) -> None:
    settings = (
        {
            "artifact_upload_idle_timeout_seconds": 0.01,
            "artifact_upload_total_timeout_seconds": 1,
        }
        if deadline_kind == "idle"
        else {
            "artifact_upload_idle_timeout_seconds": 1,
            "artifact_upload_total_timeout_seconds": 0.02,
        }
    )
    with _service_environment(tmp_path / deadline_kind, **settings) as environment:
        project = environment.projects.create(f"{deadline_kind} deadline")
        created = environment.artifacts.create_import(project.id, _declaration())

        async def slow() -> AsyncIterator[bytes]:
            if deadline_kind == "idle":
                await asyncio.sleep(0.03)
                yield _tiny_png()
            else:
                for offset in range(0, len(_tiny_png()), 16):
                    await asyncio.sleep(0.008)
                    yield _tiny_png()[offset : offset + 16]

        with pytest.raises(ArtifactImportFailed) as captured:
            asyncio.run(
                environment.artifacts.upload_content(
                    project.id,
                    created.import_id,
                    slow(),
                    content_length=len(_tiny_png()),
                )
            )
        assert captured.value.failure_code.value == "upload_interrupted"
        assert environment.artifacts.get_import(project.id, created.import_id).state == (
            "FAILED_RETRYABLE"
        )


def test_a_second_receiver_is_rejected_while_the_first_stream_is_paused(
    service_environment: ServiceEnvironment,
) -> None:
    project = service_environment.projects.create("Concurrent receiver")
    created = service_environment.artifacts.create_import(project.id, _declaration())

    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def paused() -> AsyncIterator[bytes]:
            entered.set()
            await release.wait()
            yield _tiny_png()

        first = asyncio.create_task(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                paused(),
                content_length=len(_tiny_png()),
            )
        )
        await entered.wait()
        with pytest.raises(ArtifactImportConflict) as captured:
            await service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(_tiny_png()),
                content_length=len(_tiny_png()),
            )
        assert captured.value.reason.value == "upload_in_progress"
        release.set()
        await first

    asyncio.run(scenario())


def test_paused_stream_does_not_hold_the_shared_write_gate(
    service_environment: ServiceEnvironment,
) -> None:
    project = service_environment.projects.create("Unlocked stream")
    active = service_environment.graphs.get_active(project.id)
    created = service_environment.artifacts.create_import(project.id, _declaration())

    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def paused() -> AsyncIterator[bytes]:
            entered.set()
            await release.wait()
            yield _tiny_png()

        upload = asyncio.create_task(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                paused(),
                content_length=len(_tiny_png()),
            )
        )
        await entered.wait()
        saved = await asyncio.wait_for(
            asyncio.to_thread(
                service_environment.graphs.create_revision,
                project.id,
                CreateGraphRevisionRequest(
                    base_revision_id=active.revision_id,
                    client_mutation_id=uuid4(),
                    snapshot=GraphSnapshotV2(),
                ),
            ),
            timeout=1,
        )
        assert saved.sequence == active.sequence + 1
        release.set()
        await upload

    asyncio.run(scenario())


def test_get_import_keeps_status_and_revision_in_one_completion_snapshot(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service_environment.projects.create("Status snapshot")
    created = service_environment.artifacts.create_import(project.id, _declaration())
    promote_entered = Event()
    release_promote = Event()
    revision_lookup_entered = Event()
    release_reader = Event()
    commit_attempted = Event()
    commit_finished = Event()
    reader_result: dict[str, object] = {}
    writer_result: dict[str, object] = {}

    original_promote = ArtifactStore.promote_verified

    def paused_promote(self, promotion, **kwargs):
        promoted = original_promote(self, promotion, **kwargs)
        promote_entered.set()
        assert release_promote.wait(timeout=5)
        return promoted

    original_revision_lookup = service_environment.artifacts.repository.revision_for_import

    def paused_revision_lookup(session, import_id):
        revision_lookup_entered.set()
        assert release_reader.wait(timeout=5)
        return original_revision_lookup(session, import_id)

    original_commit = service_environment.artifacts._commit_revision

    def observed_commit(*args, **kwargs):
        commit_attempted.set()
        return original_commit(*args, **kwargs)

    def observe_commit(phase: str) -> None:
        if phase == "after_db_commit":
            commit_finished.set()

    monkeypatch.setattr(ArtifactStore, "promote_verified", paused_promote)
    monkeypatch.setattr(service_environment.artifacts, "_commit_revision", observed_commit)
    monkeypatch.setattr(service_environment.artifacts, "_failpoint", observe_commit)

    def upload() -> None:
        try:
            writer_result["revision"] = asyncio.run(
                service_environment.artifacts.upload_content(
                    project.id,
                    created.import_id,
                    _chunks(_tiny_png()),
                    content_length=len(_tiny_png()),
                )
            )
        except BaseException as error:
            writer_result["error"] = error

    writer = Thread(target=upload)
    writer.start()
    assert promote_entered.wait(timeout=2)
    monkeypatch.setattr(
        service_environment.artifacts.repository,
        "revision_for_import",
        paused_revision_lookup,
    )

    def read_status() -> None:
        try:
            reader_result["status"] = service_environment.artifacts.get_import(
                project.id,
                created.import_id,
            )
        except BaseException as error:
            reader_result["error"] = error

    reader = Thread(target=read_status)
    reader.start()
    assert not revision_lookup_entered.wait(timeout=0.25)

    try:
        release_promote.set()
        assert commit_attempted.wait(timeout=2)
        assert commit_finished.wait(timeout=2)
        assert revision_lookup_entered.wait(timeout=2)
    finally:
        release_reader.set()
        reader.join(timeout=5)
        writer.join(timeout=5)

    assert not reader.is_alive()
    assert not writer.is_alive()
    assert "error" not in reader_result
    assert "error" not in writer_result
    observed = reader_result["status"]
    assert observed.state == "COMPLETED"
    assert observed.artifact_revision == writer_result["revision"]
    assert commit_finished.is_set()
    completed = service_environment.artifacts.get_import(project.id, created.import_id)
    assert completed.state == "COMPLETED"
    assert completed.artifact_revision == writer_result["revision"]


def test_claim_waiting_for_the_shared_write_gate_does_not_block_the_event_loop(
    service_environment: ServiceEnvironment,
) -> None:
    project = service_environment.projects.create("Async claim")
    created = service_environment.artifacts.create_import(project.id, _declaration())
    gate_held = Event()
    gate_released = Event()

    def hold_gate() -> None:
        with service_environment.artifacts._write_gate:
            gate_held.set()
            time.sleep(0.3)
        gate_released.set()

    holder = Thread(target=hold_gate)
    holder.start()
    assert gate_held.wait(timeout=2)

    async def scenario() -> bool:
        upload = asyncio.create_task(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(_tiny_png()),
                content_length=len(_tiny_png()),
            )
        )
        await asyncio.sleep(0.05)
        heartbeat_before_release = not gate_released.is_set()
        await upload
        return heartbeat_before_release

    try:
        heartbeat_before_release = asyncio.run(scenario())
    finally:
        holder.join(timeout=2)

    assert heartbeat_before_release
    assert not holder.is_alive()


@pytest.mark.parametrize(
    "phase",
    [
        "mark_validating",
        "commit_revision",
        "get_revision",
        "completed_digest",
        "mark_failed",
        "discard",
    ],
)
def test_slow_sync_upload_boundaries_do_not_block_the_event_loop(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    project = service_environment.projects.create(f"Async {phase}")
    started = Event()
    finished = Event()

    def delay() -> None:
        started.set()
        time.sleep(0.3)
        finished.set()

    if phase == "completed_digest":
        created, expected_revision = _complete_import(service_environment, project.id)

        def slow_completed_digest(self, import_id):
            delay()
            with self.session_factory() as session:
                row = session.get(ImportSessionRow, str(import_id))
                return row.computed_sha256 if row is not None else None

        monkeypatch.setattr(
            ArtifactService,
            "_completed_digest",
            slow_completed_digest,
            raising=False,
        )
        payload = _tiny_png()
        content_length = len(payload)
        expected_failure = False
    elif phase == "discard":
        payload = b"not-an-image"
        declaration = _declaration(byte_size=len(payload))
        created = service_environment.artifacts.create_import(project.id, declaration)
        expected_revision = None
        original = ArtifactStore.discard

        def slow_discard(self, staged) -> None:
            delay()
            original(self, staged)

        monkeypatch.setattr(ArtifactStore, "discard", slow_discard)
        content_length = len(payload)
        expected_failure = True
    else:
        created = service_environment.artifacts.create_import(project.id, _declaration())
        expected_revision = None
        payload = _tiny_png()
        content_length = len(payload) - 1 if phase == "mark_failed" else len(payload)
        expected_failure = phase == "mark_failed"
        method_name = {
            "mark_validating": "_mark_validating",
            "commit_revision": "_commit_revision",
            "get_revision": "get_revision",
            "mark_failed": "_mark_failed",
        }[phase]
        original = getattr(ArtifactService, method_name)

        def slow_method(self, *args, **kwargs):
            delay()
            return original(self, *args, **kwargs)

        monkeypatch.setattr(ArtifactService, method_name, slow_method)

    async def heartbeat() -> bool:
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.sleep(0.05)
        return not finished.is_set()

    async def scenario():
        upload = asyncio.create_task(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(payload),
                content_length=content_length,
            )
        )
        heartbeat_before_finish = await heartbeat()
        if expected_failure:
            with pytest.raises(ArtifactImportFailed):
                await upload
            result = None
        else:
            result = await upload
        return heartbeat_before_finish, result

    heartbeat_before_finish, result = asyncio.run(scenario())

    assert heartbeat_before_finish
    if expected_revision is not None:
        assert result == expected_revision


@pytest.mark.parametrize("completed", [False, True])
def test_repeated_cancellation_during_claim_reconciles_the_persisted_state(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    completed: bool,
) -> None:
    project = service_environment.projects.create(f"Cancel claim {completed}")
    if completed:
        created, expected_revision = _complete_import(service_environment, project.id)
    else:
        created = service_environment.artifacts.create_import(project.id, _declaration())
        expected_revision = None
    claim_finished = Event()
    release_claim = Event()
    original_claim = ArtifactService._claim_upload

    def claim_then_block(self, *args, **kwargs):
        result = original_claim(self, *args, **kwargs)
        claim_finished.set()
        assert release_claim.wait(timeout=2)
        return result

    monkeypatch.setattr(ArtifactService, "_claim_upload", claim_then_block)

    async def scenario() -> bool:
        task = asyncio.create_task(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(_tiny_png()),
                content_length=len(_tiny_png()),
            )
        )
        assert await asyncio.to_thread(claim_finished.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.05)
        returned_before_claim = task.done()
        release_claim.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return returned_before_claim

    returned_before_claim = asyncio.run(scenario())

    assert not returned_before_claim
    observed = service_environment.artifacts.get_import(project.id, created.import_id)
    if completed:
        assert observed.state == "COMPLETED"
        assert observed.artifact_revision == expected_revision
    else:
        assert observed.state == "FAILED_RETRYABLE"
        assert observed.failure_code.value == "upload_interrupted"


@pytest.mark.parametrize("phase", ["probe", "promote"])
def test_repeated_cancellation_waits_for_worker_and_preserves_atomic_publication(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    project = service_environment.projects.create(f"Cancel {phase}")
    created = service_environment.artifacts.create_import(project.id, _declaration())
    worker_started = Event()
    release_worker = Event()

    if phase == "probe":
        original_probe = artifact_service_module.probe_image

        def blocking_probe(*args, **kwargs):
            worker_started.set()
            assert release_worker.wait(timeout=5)
            return original_probe(*args, **kwargs)

        monkeypatch.setattr(artifact_service_module, "probe_image", blocking_probe)
    else:
        original_promote = ArtifactStore.promote_verified

        def blocking_promote(self, promotion, **kwargs):
            worker_started.set()
            assert release_worker.wait(timeout=5)
            return original_promote(self, promotion, **kwargs)

        monkeypatch.setattr(ArtifactStore, "promote_verified", blocking_promote)

    async def scenario() -> bool:
        task = asyncio.create_task(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(_tiny_png()),
                content_length=len(_tiny_png()),
            )
        )
        assert await asyncio.to_thread(worker_started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.05)
        returned_before_worker = task.done()
        release_worker.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return returned_before_worker

    returned_before_worker = asyncio.run(scenario())

    assert not returned_before_worker
    observed = service_environment.artifacts.get_import(project.id, created.import_id)
    if phase == "probe":
        assert observed.state == "FAILED_RETRYABLE"
        assert observed.artifact_revision is None
    else:
        # Cancellation is too late once the continuous publication critical
        # section has begun: promote and its DB commit settle atomically.
        assert observed.state == "COMPLETED"
        assert observed.artifact_revision is not None
    assert not (service_environment.artifacts._store("image").staging_path(created.import_id)).exists()


def test_repeated_cancellation_waits_for_retry_cleanup_without_holding_the_write_gate(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service_environment.projects.create("Cancel retry cleanup")
    active = service_environment.graphs.get_active(project.id)
    created = service_environment.artifacts.create_import(project.id, _declaration())
    store = service_environment.artifacts._store("image")
    store.staging_root.mkdir(parents=True, exist_ok=True)
    exact_part = store.staging_path(created.import_id)
    exact_part.write_bytes(b"stale")
    with service_environment.session_factory.begin() as session:
        row = session.get(ImportSessionRow, str(created.import_id))
        assert row is not None
        row.state = "FAILED_RETRYABLE"
        row.failure_code = "upload_interrupted"

    worker_started = Event()
    release_worker = Event()
    original_discard = ArtifactStore.discard

    def blocking_discard(self, staged) -> None:
        assert staged.import_id == created.import_id
        worker_started.set()
        assert release_worker.wait(timeout=5)
        original_discard(self, staged)

    async def scenario() -> bool:
        task = asyncio.create_task(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(_tiny_png()),
                content_length=len(_tiny_png()),
            )
        )
        assert await asyncio.to_thread(worker_started.wait, 2)
        saved = await asyncio.wait_for(
            asyncio.to_thread(
                service_environment.graphs.create_revision,
                project.id,
                CreateGraphRevisionRequest(
                    base_revision_id=active.revision_id,
                    client_mutation_id=uuid4(),
                    snapshot=GraphSnapshotV2(),
                ),
            ),
            timeout=1,
        )
        assert saved.sequence == active.sequence + 1
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.05)
        returned_before_worker = task.done()
        release_worker.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return returned_before_worker

    with monkeypatch.context() as patch:
        patch.setattr(ArtifactStore, "discard", blocking_discard)
        returned_before_worker = asyncio.run(scenario())

    assert not returned_before_worker
    failed = service_environment.artifacts.get_import(project.id, created.import_id)
    assert failed.state == "FAILED_RETRYABLE"
    assert failed.failure_code.value == "upload_interrupted"
    assert not exact_part.exists()

    revision = asyncio.run(
        service_environment.artifacts.upload_content(
            project.id,
            created.import_id,
            _chunks(_tiny_png()),
            content_length=len(_tiny_png()),
        )
    )
    assert service_environment.artifacts.get_import(
        project.id,
        created.import_id,
    ).artifact_revision == revision


@pytest.mark.parametrize("phase", ["probe", "promote"])
def test_probe_stays_outside_shared_gate_but_promote_holds_publication_gate(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    project = service_environment.projects.create(f"Unlocked {phase}")
    active = service_environment.graphs.get_active(project.id)
    created = service_environment.artifacts.create_import(project.id, _declaration())
    worker_started = Event()
    release_worker = Event()
    graph_write_attempted = Event()
    graph_write_finished = Event()
    if phase == "probe":
        original_probe = artifact_service_module.probe_image

        def blocking_probe(*args, **kwargs):
            worker_started.set()
            assert release_worker.wait(timeout=5)
            return original_probe(*args, **kwargs)

        monkeypatch.setattr(artifact_service_module, "probe_image", blocking_probe)
    else:
        original_promote = ArtifactStore.promote_verified

        def blocking_promote(self, promotion, **kwargs):
            worker_started.set()
            assert release_worker.wait(timeout=5)
            return original_promote(self, promotion, **kwargs)

        monkeypatch.setattr(ArtifactStore, "promote_verified", blocking_promote)

    def save_graph_revision():
        graph_write_attempted.set()
        try:
            return service_environment.graphs.create_revision(
                project.id,
                CreateGraphRevisionRequest(
                    base_revision_id=active.revision_id,
                    client_mutation_id=uuid4(),
                    snapshot=GraphSnapshotV2(),
                ),
            )
        finally:
            graph_write_finished.set()

    async def scenario() -> None:
        upload = asyncio.create_task(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(_tiny_png()),
                content_length=len(_tiny_png()),
            )
        )
        assert await asyncio.to_thread(worker_started.wait, 2)
        graph_write = asyncio.create_task(asyncio.to_thread(save_graph_revision))
        assert await asyncio.to_thread(graph_write_attempted.wait, 2)
        if phase == "probe":
            assert await asyncio.to_thread(graph_write_finished.wait, 2)
        else:
            assert not await asyncio.to_thread(graph_write_finished.wait, 0.25)
        release_worker.set()
        saved = await asyncio.wait_for(graph_write, timeout=2)
        assert saved.sequence == active.sequence + 1
        await upload

    try:
        asyncio.run(scenario())
    finally:
        release_worker.set()


def test_full_hash_stays_outside_gate_but_verified_publish_and_commit_hold_it(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_project = service_environment.projects.create("Verified publish")
    hash_write_project = service_environment.projects.create("Hash concurrency")
    atomic_write_project = service_environment.projects.create("Atomic concurrency")
    hash_write_base = service_environment.graphs.get_active(hash_write_project.id)
    atomic_write_base = service_environment.graphs.get_active(atomic_write_project.id)
    created = service_environment.artifacts.create_import(upload_project.id, _declaration())
    hash_started = Event()
    release_hash = Event()
    atomic_started = Event()
    release_atomic = Event()
    hash_write_attempted = Event()
    hash_write_done = Event()
    atomic_write_attempted = Event()
    atomic_write_done = Event()
    errors: list[BaseException] = []
    original_file_matches = artifact_store_module._file_matches
    original_atomic_promote = artifact_store_module._atomic_promote
    promotion_root = service_environment.artifacts._store("image").promotion_root

    def blocked_file_matches(path, **kwargs):
        if path.parent == promotion_root:
            hash_started.set()
            if not release_hash.wait(5):
                raise AssertionError("hash barrier was not released")
        return original_file_matches(path, **kwargs)

    def blocked_atomic_promote(source: Path, destination: Path) -> None:
        if source.parent == promotion_root:
            atomic_started.set()
            if not release_atomic.wait(5):
                raise AssertionError("verified publish barrier was not released")
        original_atomic_promote(source, destination)

    monkeypatch.setattr(artifact_store_module, "_file_matches", blocked_file_matches)
    monkeypatch.setattr(artifact_store_module, "_atomic_promote", blocked_atomic_promote)

    def upload() -> None:
        try:
            asyncio.run(
                service_environment.artifacts.upload_content(
                    upload_project.id,
                    created.import_id,
                    _chunks(_tiny_png()),
                    content_length=len(_tiny_png()),
                )
            )
        except BaseException as error:
            errors.append(error)

    def save_graph(project_id: UUID, base_revision_id: UUID, attempted: Event, done: Event) -> None:
        attempted.set()
        try:
            service_environment.graphs.create_revision(
                project_id,
                CreateGraphRevisionRequest(
                    base_revision_id=base_revision_id,
                    client_mutation_id=uuid4(),
                    snapshot=GraphSnapshotV2(),
                ),
            )
        except BaseException as error:
            errors.append(error)
        finally:
            done.set()

    upload_thread = Thread(target=upload)
    hash_write_thread = Thread(
        target=save_graph,
        args=(
            hash_write_project.id,
            hash_write_base.revision_id,
            hash_write_attempted,
            hash_write_done,
        ),
    )
    atomic_write_thread = Thread(
        target=save_graph,
        args=(
            atomic_write_project.id,
            atomic_write_base.revision_id,
            atomic_write_attempted,
            atomic_write_done,
        ),
    )
    hash_write_completed_while_hashing = False
    atomic_write_completed_during_publish = True
    try:
        upload_thread.start()
        assert hash_started.wait(5)
        hash_write_thread.start()
        assert hash_write_attempted.wait(2)
        hash_write_completed_while_hashing = hash_write_done.wait(2)
        release_hash.set()
        assert atomic_started.wait(5)
        atomic_write_thread.start()
        assert atomic_write_attempted.wait(2)
        atomic_write_completed_during_publish = atomic_write_done.wait(0.25)
    finally:
        release_hash.set()
        release_atomic.set()
        upload_thread.join(timeout=5)
        if hash_write_thread.ident is not None:
            hash_write_thread.join(timeout=5)
        if atomic_write_thread.ident is not None:
            atomic_write_thread.join(timeout=5)

    assert not upload_thread.is_alive()
    assert not hash_write_thread.is_alive()
    assert not atomic_write_thread.is_alive()
    assert hash_write_completed_while_hashing is True
    assert atomic_write_completed_during_publish is False
    assert errors == []


def test_db_precondition_failure_revokes_private_promotion_before_cas_publish(
    service_environment: ServiceEnvironment,
) -> None:
    project = service_environment.projects.create("Publication precondition")
    created = service_environment.artifacts.create_import(project.id, _declaration())
    service_environment.artifacts._claim_upload(project.id, created.import_id)
    store = service_environment.artifacts._store("image")
    content = _tiny_png()
    staged = store.stage_chunks(
        created.import_id,
        [content],
        declared_size=len(content),
    )
    service_environment.artifacts._mark_validating(created.import_id, staged)
    probe = service_environment.artifacts._probe("image", staged)
    ticket = store.prepare_promotion(staged)
    with service_environment.session_factory.begin() as session:
        row = session.get(ImportSessionRow, str(created.import_id))
        assert row is not None
        row.state = "FAILED_RETRYABLE"

    with pytest.raises(ArtifactImportConflict):
        service_environment.artifacts._publish_revision(
            store,
            ticket,
            created.import_id,
            staged,
            probe,
        )

    assert not store.blob_path(staged.sha256).exists()
    assert not store.promotion_root.exists() or list(store.promotion_root.iterdir()) == []


def test_replacement_creates_an_immutable_next_revision_with_per_revision_provenance(
    service_environment: ServiceEnvironment,
) -> None:
    origin = service_environment.projects.create("Origin")
    _first_import, first = _complete_import(service_environment, origin.id)
    _attach_media_revision(service_environment, origin.id, first.revision_id)

    replacement_declaration = _declaration(replace_revision_id=first.revision_id)
    _replacement_import, replacement = _complete_import(
        service_environment,
        origin.id,
        replacement_declaration,
    )

    assert replacement.artifact_id == first.artifact_id
    assert replacement.revision_id != first.revision_id
    assert replacement.sequence == 2
    assert replacement.origin_project_id == first.origin_project_id == origin.id
    assert replacement.source_project_id == origin.id
    assert service_environment.artifacts.get_revision(first.revision_id) == first
    with service_environment.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(StoredBlobRow)) == 1
        assert session.scalar(select(func.count()).select_from(ArtifactRevisionRow)) == 2


def test_cross_project_replacement_is_allowed_only_after_the_active_media_node_references_it(
    service_environment: ServiceEnvironment,
) -> None:
    origin = service_environment.projects.create("Origin")
    consumer = service_environment.projects.create("Consumer")
    _import, first = _complete_import(service_environment, origin.id)

    invalid = _declaration(replace_revision_id=first.revision_id)
    with pytest.raises(ArtifactImportConflict) as captured:
        service_environment.artifacts.create_import(consumer.id, invalid)
    assert captured.value.reason.value == "invalid_replacement"
    failed = service_environment.artifacts.get_import(consumer.id, UUID(captured.value.import_id))
    assert failed.state == "FAILED_FINAL"
    assert failed.failure_code.value == "incompatible_declaration"

    _attach_media_revision(service_environment, consumer.id, first.revision_id)
    valid = _declaration(replace_revision_id=first.revision_id)
    _session, replacement = _complete_import(service_environment, consumer.id, valid)
    assert replacement.artifact_id == first.artifact_id
    assert replacement.sequence == 2
    assert replacement.origin_project_id == origin.id
    assert replacement.source_project_id == consumer.id


def test_shot_only_or_historical_reference_is_not_a_replacement_authority(
    service_environment: ServiceEnvironment,
) -> None:
    project = service_environment.projects.create("Replacement authority")
    _import, first = _complete_import(service_environment, project.id)
    shot_id = uuid4()
    _save_snapshot(
        service_environment,
        project.id,
        GraphSnapshotV2(
            nodes=[
                ShotNodeV2(
                    id=uuid4(),
                    kind="shot",
                    position=Point(x=0, y=0),
                    data=ShotNodeData(shot_id=shot_id),
                )
            ],
            shots=[Shot(id=shot_id, order=0, image_revision_id=first.revision_id)],
        ),
    )
    with pytest.raises(ArtifactImportConflict) as shot_only:
        service_environment.artifacts.create_import(
            project.id,
            _declaration(replace_revision_id=first.revision_id),
        )
    assert shot_only.value.reason.value == "invalid_replacement"

    _attach_media_revision(service_environment, project.id, first.revision_id)
    _save_snapshot(service_environment, project.id, GraphSnapshotV2())
    with pytest.raises(ArtifactImportConflict) as historical:
        service_environment.artifacts.create_import(
            project.id,
            _declaration(replace_revision_id=first.revision_id),
        )
    assert historical.value.reason.value == "invalid_replacement"


def test_replacement_authority_is_rechecked_in_the_final_commit_transaction(
    service_environment: ServiceEnvironment,
) -> None:
    project = service_environment.projects.create("Replacement recheck")
    _import, first = _complete_import(service_environment, project.id)
    _attach_media_revision(service_environment, project.id, first.revision_id)
    declaration = _declaration(replace_revision_id=first.revision_id)
    created = service_environment.artifacts.create_import(project.id, declaration)
    _save_snapshot(service_environment, project.id, GraphSnapshotV2())

    with pytest.raises(ArtifactImportConflict) as captured:
        asyncio.run(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(_tiny_png()),
                content_length=len(_tiny_png()),
            )
        )

    assert captured.value.reason.value == "invalid_replacement"
    failed = service_environment.artifacts.get_import(project.id, created.import_id)
    assert failed.state == "FAILED_FINAL"
    assert failed.failure_code.value == "incompatible_declaration"
    assert service_environment.artifacts.get_revision(first.revision_id) == first
    with service_environment.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ArtifactRevisionRow)) == 1


def test_replacement_never_trusts_a_corrupt_active_graph_snapshot(
    service_environment: ServiceEnvironment,
) -> None:
    project = service_environment.projects.create("Corrupt replacement graph")
    _import, first = _complete_import(service_environment, project.id)
    _attach_media_revision(service_environment, project.id, first.revision_id)
    with service_environment.session_factory.begin() as session:
        document = session.scalar(
            select(GraphDocumentRow).where(GraphDocumentRow.project_id == str(project.id))
        )
        assert document is not None and document.active_revision_id is not None
        active = session.get(GraphRevisionRow, document.active_revision_id)
        assert active is not None
        active.snapshot_hash = "0" * 64

    with pytest.raises(ArtifactImportConflict) as captured:
        service_environment.artifacts.create_import(
            project.id,
            _declaration(replace_revision_id=first.revision_id),
        )

    assert captured.value.reason.value == "invalid_replacement"
    failed = service_environment.artifacts.get_import(project.id, UUID(captured.value.import_id))
    assert failed.state == "FAILED_FINAL"


@pytest.mark.parametrize(
    ("payload", "requested_kind", "filename"),
    (
        (_tiny_png(), "video", "clip.mp4"),
        (_tiny_mp4(), "image", "frame.png"),
    ),
    ids=("png-declared-video", "mp4-declared-image"),
)
def test_supported_content_of_the_opposite_requested_kind_is_incompatible(
    service_environment: ServiceEnvironment,
    payload: bytes,
    requested_kind: str,
    filename: str,
) -> None:
    project = service_environment.projects.create(f"Opposite {requested_kind}")
    declaration = CreateArtifactImportRequest(
        client_import_id=uuid4(),
        filename=filename,
        expected_byte_size=len(payload),
        requested_media_kind=requested_kind,
        declared_content_type=None,
    )
    created = service_environment.artifacts.create_import(project.id, declaration)

    with pytest.raises(ArtifactImportFailed) as captured:
        asyncio.run(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(payload),
                content_length=len(payload),
            )
        )

    assert captured.value.failure_code.value == "incompatible_declaration"
    assert service_environment.artifacts.get_import(project.id, created.import_id).state == (
        "FAILED_FINAL"
    )


@pytest.mark.parametrize("declared_type", [None, "application/octet-stream", "image/png"])
def test_filename_and_declared_type_are_hints_but_probed_metadata_is_authoritative(
    service_environment: ServiceEnvironment,
    declared_type: str | None,
) -> None:
    project = service_environment.projects.create(f"Hint {declared_type}")
    declaration = _declaration(
        filename="misleading.jpeg",
        declared_content_type=declared_type,
    )
    _session, revision = _complete_import(service_environment, project.id, declaration)
    assert revision.display_filename == "misleading.jpeg"
    assert revision.media_type == "image/png"


def test_incompatible_declared_mime_is_a_final_closed_failure(
    service_environment: ServiceEnvironment,
) -> None:
    project = service_environment.projects.create("Bad MIME")
    declaration = _declaration(declared_content_type="image/jpeg")
    created = service_environment.artifacts.create_import(project.id, declaration)
    with pytest.raises(ArtifactImportFailed) as captured:
        asyncio.run(
            service_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(_tiny_png()),
                content_length=len(_tiny_png()),
            )
        )
    assert captured.value.failure_code.value == "incompatible_declaration"
    assert service_environment.artifacts.get_import(project.id, created.import_id).failure_code.value == (
        "incompatible_declaration"
    )
