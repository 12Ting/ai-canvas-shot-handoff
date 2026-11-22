from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.artifact_service import ArtifactService
from ai_canvas.application.errors import ArtifactImportConflict, ArtifactImportFailed
from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.config import Settings
from ai_canvas.contracts.artifact import CreateArtifactImportRequest
from ai_canvas.infrastructure.artifacts import ArtifactStore, StoredBlobIntegrityError
from ai_canvas.infrastructure.db.models import (
    ArtifactRevisionRow,
    ArtifactRow,
    ImportSessionRow,
    StoredBlobRow,
)
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)
from ai_canvas.infrastructure.process_lock import DataDirectoryLock
from ai_canvas.main import create_app


class SimulatedProcessCrash(BaseException):
    pass


@dataclass
class CrashEnvironment:
    projects: ProjectService
    artifacts: ArtifactService
    session_factory: sessionmaker[Session]
    settings: Settings


@pytest.fixture
def crash_environment(tmp_path: Path) -> Iterator[CrashEnvironment]:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    factory = make_session_factory(engine)
    gate = WriteGate()
    settings = Settings(data_dir=tmp_path)
    try:
        yield CrashEnvironment(
            projects=ProjectService(factory, write_gate=gate),
            artifacts=ArtifactService(factory, settings=settings, write_gate=gate),
            session_factory=factory,
            settings=settings,
        )
    finally:
        engine.dispose()


def _tiny_png() -> bytes:
    fixture = Path(__file__).parents[1] / "fixtures" / "tiny_png.b64"
    return base64.b64decode(fixture.read_text(encoding="ascii"))


def _declaration(client_import_id: UUID | None = None) -> CreateArtifactImportRequest:
    return CreateArtifactImportRequest(
        client_import_id=client_import_id or uuid4(),
        filename="camera.png",
        expected_byte_size=len(_tiny_png()),
        requested_media_kind="image",
        declared_content_type="image/png",
    )


async def _chunks(content: bytes) -> AsyncIterator[bytes]:
    yield content


def _row_counts(factory: sessionmaker[Session]) -> tuple[int, int, int]:
    with factory() as session:
        return (
            int(session.scalar(select(func.count()).select_from(StoredBlobRow)) or 0),
            int(session.scalar(select(func.count()).select_from(ArtifactRow)) or 0),
            int(session.scalar(select(func.count()).select_from(ArtifactRevisionRow)) or 0),
        )


def _operational_error(label: str) -> OperationalError:
    return OperationalError(label, {}, RuntimeError(label))


@pytest.mark.parametrize(
    ("failpoint", "completed", "staging_exists", "cas_exists"),
    (
        ("before_promote", False, True, False),
        ("after_promote", False, False, True),
        ("before_db_commit", False, False, True),
        ("after_db_commit", True, False, True),
        ("after_commit_before_response", True, False, True),
    ),
)
def test_crash_failpoints_preserve_promote_before_commit_invariants(
    crash_environment: CrashEnvironment,
    failpoint: str,
    completed: bool,
    staging_exists: bool,
    cas_exists: bool,
) -> None:
    project = crash_environment.projects.create(f"Crash {failpoint}")
    declaration = _declaration()
    created = crash_environment.artifacts.create_import(project.id, declaration)

    def crash_at(name: str) -> None:
        if name == failpoint:
            raise SimulatedProcessCrash(name)

    crashing = ArtifactService(
        crash_environment.session_factory,
        settings=crash_environment.settings,
        write_gate=crash_environment.artifacts._write_gate,
        failpoint=crash_at,
    )
    with pytest.raises(SimulatedProcessCrash, match=failpoint):
        asyncio.run(
            crashing.upload_content(
                project.id,
                created.import_id,
                _chunks(_tiny_png()),
                content_length=len(_tiny_png()),
            )
        )

    digest = hashlib.sha256(_tiny_png()).hexdigest()
    store = ArtifactStore(crash_environment.settings.data_dir)
    assert store.staging_path(created.import_id).exists() is staging_exists
    assert store.blob_path(digest).exists() is cas_exists
    observed = crash_environment.artifacts.get_import(project.id, created.import_id)
    if completed:
        assert observed.state == "COMPLETED"
        assert observed.artifact_revision is not None
        assert _row_counts(crash_environment.session_factory) == (1, 1, 1)
        replayed = crash_environment.artifacts.create_import(project.id, declaration)
        assert replayed.artifact_revision == observed.artifact_revision
    else:
        assert observed.state == "VALIDATING"
        assert observed.artifact_revision is None
        assert _row_counts(crash_environment.session_factory) == (0, 0, 0)


def test_startup_recovery_only_fails_interrupted_states_and_their_exact_parts(
    crash_environment: CrashEnvironment,
) -> None:
    project = crash_environment.projects.create("Recover interrupted")
    receiving = crash_environment.artifacts.create_import(project.id, _declaration())
    validating = crash_environment.artifacts.create_import(project.id, _declaration())
    untouched = crash_environment.artifacts.create_import(project.id, _declaration())
    store = ArtifactStore(crash_environment.settings.data_dir)
    store.staging_root.mkdir(parents=True)
    unrelated_id = uuid4()
    for import_id in (
        receiving.import_id,
        validating.import_id,
        untouched.import_id,
        unrelated_id,
    ):
        store.staging_path(import_id).write_bytes(b"synthetic-part")
    with crash_environment.session_factory.begin() as session:
        receiving_row = session.get(ImportSessionRow, str(receiving.import_id))
        validating_row = session.get(ImportSessionRow, str(validating.import_id))
        assert receiving_row is not None and validating_row is not None
        receiving_row.state = "RECEIVING"
        validating_row.state = "VALIDATING"
        validating_row.received_byte_size = len(_tiny_png())
        validating_row.computed_sha256 = hashlib.sha256(_tiny_png()).hexdigest()

    crash_environment.artifacts.recover_startup()

    for import_id in (receiving.import_id, validating.import_id):
        recovered = crash_environment.artifacts.get_import(project.id, import_id)
        assert recovered.state == "FAILED_RETRYABLE"
        assert recovered.failure_code.value == "upload_interrupted"
        assert not store.staging_path(import_id).exists()
    assert crash_environment.artifacts.get_import(project.id, untouched.import_id).state == "CREATED"
    assert store.staging_path(untouched.import_id).read_bytes() == b"synthetic-part"
    assert store.staging_path(unrelated_id).read_bytes() == b"synthetic-part"


def test_failed_retryable_stale_part_can_retry_after_startup_recovery(
    crash_environment: CrashEnvironment,
) -> None:
    project = crash_environment.projects.create("Restart retryable")
    created = crash_environment.artifacts.create_import(project.id, _declaration())
    store = ArtifactStore(crash_environment.settings.data_dir)
    store.staging_root.mkdir(parents=True, exist_ok=True)
    exact_part = store.staging_path(created.import_id)
    exact_part.write_bytes(b"stale-after-restart")
    unrelated_part = store.staging_path(uuid4())
    unrelated_part.write_bytes(b"unrelated")
    with crash_environment.session_factory.begin() as session:
        row = session.get(ImportSessionRow, str(created.import_id))
        assert row is not None
        row.state = "FAILED_RETRYABLE"
        row.failure_code = "upload_interrupted"

    crash_environment.artifacts.recover_startup()

    revision = asyncio.run(
        crash_environment.artifacts.upload_content(
            project.id,
            created.import_id,
            _chunks(_tiny_png()),
            content_length=len(_tiny_png()),
        )
    )
    completed = crash_environment.artifacts.get_import(project.id, created.import_id)
    assert completed.state == "COMPLETED"
    assert completed.artifact_revision == revision
    assert not exact_part.exists()
    assert unrelated_part.read_bytes() == b"unrelated"


def _complete_import(environment: CrashEnvironment):
    project = environment.projects.create("Integrity")
    declaration = _declaration()
    created = environment.artifacts.create_import(project.id, declaration)
    revision = asyncio.run(
        environment.artifacts.upload_content(
            project.id,
            created.import_id,
            _chunks(_tiny_png()),
            content_length=len(_tiny_png()),
        )
    )
    return project, created, revision


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_startup_fails_closed_for_a_db_referenced_cas_integrity_error(
    crash_environment: CrashEnvironment,
    damage: str,
) -> None:
    _project, _created, revision = _complete_import(crash_environment)
    with crash_environment.session_factory() as session:
        row = session.get(ArtifactRevisionRow, str(revision.revision_id))
        assert row is not None
        path = ArtifactStore(crash_environment.settings.data_dir).blob_path(row.blob_sha256)
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"corrupt")

    with pytest.raises(StoredBlobIntegrityError):
        crash_environment.artifacts.recover_startup()


def test_unreferenced_cas_from_a_promote_crash_does_not_block_startup(
    crash_environment: CrashEnvironment,
) -> None:
    project = crash_environment.projects.create("Orphan CAS")
    declaration = _declaration()
    created = crash_environment.artifacts.create_import(project.id, declaration)

    def crash_after_promote(name: str) -> None:
        if name == "after_promote":
            raise SimulatedProcessCrash(name)

    crashing = ArtifactService(
        crash_environment.session_factory,
        settings=crash_environment.settings,
        write_gate=crash_environment.artifacts._write_gate,
        failpoint=crash_after_promote,
    )
    with pytest.raises(SimulatedProcessCrash):
        asyncio.run(
            crashing.upload_content(
                project.id,
                created.import_id,
                _chunks(_tiny_png()),
                content_length=len(_tiny_png()),
            )
        )

    crash_environment.artifacts.recover_startup()

    recovered = crash_environment.artifacts.get_import(project.id, created.import_id)
    assert recovered.state == "FAILED_RETRYABLE"
    digest = hashlib.sha256(_tiny_png()).hexdigest()
    assert ArtifactStore(crash_environment.settings.data_dir).blob_path(digest).is_file()
    assert _row_counts(crash_environment.session_factory) == (0, 0, 0)


def test_mark_validating_commit_operational_error_becomes_retryable(
    crash_environment: CrashEnvironment,
) -> None:
    project = crash_environment.projects.create("Mark validating SQL failure")
    created = crash_environment.artifacts.create_import(project.id, _declaration())
    triggered = False

    def fail_validating_commit(session: Session) -> None:
        nonlocal triggered
        row = session.get(ImportSessionRow, str(created.import_id))
        if not triggered and row is not None and row.state == "VALIDATING":
            triggered = True
            raise _operational_error("mark-validating-commit")

    event.listen(Session, "before_commit", fail_validating_commit)
    try:
        with pytest.raises(ArtifactImportFailed) as captured:
            asyncio.run(
                crash_environment.artifacts.upload_content(
                    project.id,
                    created.import_id,
                    _chunks(_tiny_png()),
                    content_length=len(_tiny_png()),
                )
            )
    finally:
        event.remove(Session, "before_commit", fail_validating_commit)

    assert triggered
    assert captured.value.failure_code.value == "upload_interrupted"
    failed = crash_environment.artifacts.get_import(project.id, created.import_id)
    assert failed.state == "FAILED_RETRYABLE"
    assert not ArtifactStore(crash_environment.settings.data_dir).staging_path(
        created.import_id
    ).exists()

    revision = asyncio.run(
        crash_environment.artifacts.upload_content(
            project.id,
            created.import_id,
            _chunks(_tiny_png()),
            content_length=len(_tiny_png()),
        )
    )
    assert crash_environment.artifacts.get_import(
        project.id,
        created.import_id,
    ).artifact_revision == revision


@pytest.mark.parametrize("failure_mode", ["before_commit", "after_commit"])
def test_claim_operational_error_reconciles_to_failed_retryable(
    crash_environment: CrashEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    project = crash_environment.projects.create(f"Claim SQL failure {failure_mode}")
    created = crash_environment.artifacts.create_import(project.id, _declaration())
    triggered = False

    def fail_claim_commit(session: Session) -> None:
        nonlocal triggered
        row = session.get(ImportSessionRow, str(created.import_id))
        if not triggered and row is not None and row.state == "RECEIVING":
            triggered = True
            raise _operational_error("claim-before-commit")

    if failure_mode == "before_commit":
        event.listen(Session, "before_commit", fail_claim_commit)
    else:
        original_failpoint = crash_environment.artifacts._failpoint

        def fail_after_claim(name: str) -> None:
            nonlocal triggered
            if name == "after_claim_commit" and not triggered:
                triggered = True
                raise _operational_error("claim-after-commit")
            original_failpoint(name)

        monkeypatch.setattr(crash_environment.artifacts, "_failpoint", fail_after_claim)
    try:
        with pytest.raises(ArtifactImportFailed) as captured:
            asyncio.run(
                crash_environment.artifacts.upload_content(
                    project.id,
                    created.import_id,
                    _chunks(_tiny_png()),
                    content_length=len(_tiny_png()),
                )
            )
    finally:
        if failure_mode == "before_commit":
            event.remove(Session, "before_commit", fail_claim_commit)

    assert triggered
    assert captured.value.failure_code.value == "upload_interrupted"
    failed = crash_environment.artifacts.get_import(project.id, created.import_id)
    assert failed.state == "FAILED_RETRYABLE"

    revision = asyncio.run(
        crash_environment.artifacts.upload_content(
            project.id,
            created.import_id,
            _chunks(_tiny_png()),
            content_length=len(_tiny_png()),
        )
    )
    assert crash_environment.artifacts.get_import(
        project.id,
        created.import_id,
    ).artifact_revision == revision


def test_completed_claim_operational_error_still_drains_and_compares_replay(
    crash_environment: CrashEnvironment,
) -> None:
    project, created, revision = _complete_import(crash_environment)
    triggered = False
    consumed = 0
    reached_eof = False
    changed = bytearray(_tiny_png())
    changed[-1] ^= 1

    def fail_completed_claim_commit(session: Session) -> None:
        nonlocal triggered
        row = session.get(ImportSessionRow, str(created.import_id))
        if not triggered and row is not None and row.state == "COMPLETED":
            triggered = True
            raise _operational_error("completed-claim")

    async def observed() -> AsyncIterator[bytes]:
        nonlocal consumed, reached_eof
        for chunk in (bytes(changed[:17]), bytes(changed[17:])):
            consumed += len(chunk)
            yield chunk
        reached_eof = True

    event.listen(Session, "before_commit", fail_completed_claim_commit)
    try:
        with pytest.raises(ArtifactImportConflict) as captured:
            asyncio.run(
                crash_environment.artifacts.upload_content(
                    project.id,
                    created.import_id,
                    observed(),
                    content_length=len(changed),
                )
            )
    finally:
        event.remove(Session, "before_commit", fail_completed_claim_commit)

    assert triggered
    assert captured.value.reason.value == "content_replay_mismatch"
    assert consumed == len(changed)
    assert reached_eof
    completed = crash_environment.artifacts.get_import(project.id, created.import_id)
    assert completed.state == "COMPLETED"
    assert completed.artifact_revision == revision


def test_mark_failed_commit_operational_error_is_retried_in_a_fresh_session(
    crash_environment: CrashEnvironment,
) -> None:
    project = crash_environment.projects.create("Mark failed SQL failure")
    created = crash_environment.artifacts.create_import(project.id, _declaration())
    triggered = False

    def fail_mark_failed_commit(session: Session) -> None:
        nonlocal triggered
        row = session.get(ImportSessionRow, str(created.import_id))
        if not triggered and row is not None and row.state == "FAILED_RETRYABLE":
            triggered = True
            raise _operational_error("mark-failed-commit")

    async def interrupted() -> AsyncIterator[bytes]:
        yield _tiny_png()[:13]
        raise OSError("synthetic receive failure")

    event.listen(Session, "before_commit", fail_mark_failed_commit)
    try:
        with pytest.raises(ArtifactImportFailed) as captured:
            asyncio.run(
                crash_environment.artifacts.upload_content(
                    project.id,
                    created.import_id,
                    interrupted(),
                    content_length=len(_tiny_png()),
                )
            )
    finally:
        event.remove(Session, "before_commit", fail_mark_failed_commit)

    assert triggered
    assert captured.value.failure_code.value == "upload_interrupted"
    failed = crash_environment.artifacts.get_import(project.id, created.import_id)
    assert failed.state == "FAILED_RETRYABLE"
    revision = asyncio.run(
        crash_environment.artifacts.upload_content(
            project.id,
            created.import_id,
            _chunks(_tiny_png()),
            content_length=len(_tiny_png()),
        )
    )
    assert crash_environment.artifacts.get_import(
        project.id,
        created.import_id,
    ).artifact_revision == revision


def test_revision_commit_operational_error_keeps_orphan_cas_and_allows_retry(
    crash_environment: CrashEnvironment,
) -> None:
    project = crash_environment.projects.create("Revision SQL failure")
    created = crash_environment.artifacts.create_import(project.id, _declaration())
    triggered = False

    def fail_revision_commit(session: Session) -> None:
        nonlocal triggered
        row = session.get(ImportSessionRow, str(created.import_id))
        if not triggered and row is not None and row.state == "COMPLETED":
            triggered = True
            raise _operational_error("revision-commit")

    event.listen(Session, "before_commit", fail_revision_commit)
    try:
        with pytest.raises(ArtifactImportFailed) as captured:
            asyncio.run(
                crash_environment.artifacts.upload_content(
                    project.id,
                    created.import_id,
                    _chunks(_tiny_png()),
                    content_length=len(_tiny_png()),
                )
            )
    finally:
        event.remove(Session, "before_commit", fail_revision_commit)

    assert triggered
    assert captured.value.failure_code.value == "upload_interrupted"
    failed = crash_environment.artifacts.get_import(project.id, created.import_id)
    assert failed.state == "FAILED_RETRYABLE"
    digest = hashlib.sha256(_tiny_png()).hexdigest()
    assert ArtifactStore(crash_environment.settings.data_dir).blob_path(digest).is_file()
    assert _row_counts(crash_environment.session_factory) == (0, 0, 0)

    revision = asyncio.run(
        crash_environment.artifacts.upload_content(
            project.id,
            created.import_id,
            _chunks(_tiny_png()),
            content_length=len(_tiny_png()),
        )
    )
    assert crash_environment.artifacts.get_import(
        project.id,
        created.import_id,
    ).artifact_revision == revision
    assert _row_counts(crash_environment.session_factory) == (1, 1, 1)


def test_operational_error_after_actual_commit_reconciles_completed_revision(
    crash_environment: CrashEnvironment,
) -> None:
    project = crash_environment.projects.create("After commit SQL failure")
    created = crash_environment.artifacts.create_import(project.id, _declaration())
    triggered = False

    def fail_after_commit(name: str) -> None:
        nonlocal triggered
        if name == "after_db_commit" and not triggered:
            triggered = True
            raise _operational_error("after-commit")

    service = ArtifactService(
        crash_environment.session_factory,
        settings=crash_environment.settings,
        write_gate=crash_environment.artifacts._write_gate,
        failpoint=fail_after_commit,
    )
    revision = asyncio.run(
        service.upload_content(
            project.id,
            created.import_id,
            _chunks(_tiny_png()),
            content_length=len(_tiny_png()),
        )
    )

    assert triggered
    completed = service.get_import(project.id, created.import_id)
    assert completed.state == "COMPLETED"
    assert completed.artifact_revision == revision
    assert _row_counts(crash_environment.session_factory) == (1, 1, 1)


def test_recovery_database_failure_preserves_the_operational_error_chain(
    crash_environment: CrashEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = crash_environment.projects.create("Recovery SQL failure")
    created = crash_environment.artifacts.create_import(project.id, _declaration())
    initial_error = _operational_error("initial-db-failure")
    recovery_error = _operational_error("recovery-db-failure")

    def fail_mark_validating(_import_id, _staged) -> None:
        raise initial_error

    def fail_recovery(_import_id):
        raise recovery_error

    monkeypatch.setattr(crash_environment.artifacts, "_mark_validating", fail_mark_validating)
    monkeypatch.setattr(
        crash_environment.artifacts,
        "_reconcile_sqlalchemy_failure",
        fail_recovery,
        raising=False,
    )

    with pytest.raises(OperationalError) as captured:
        asyncio.run(
            crash_environment.artifacts.upload_content(
                project.id,
                created.import_id,
                _chunks(_tiny_png()),
                content_length=len(_tiny_png()),
            )
        )

    assert captured.value is recovery_error
    assert captured.value.__cause__ is initial_error
    observed = crash_environment.artifacts.get_import(project.id, created.import_id)
    assert observed.state in {"RECEIVING", "VALIDATING"}
    assert observed.artifact_revision is None


def test_failed_startup_integrity_check_releases_engine_and_data_directory_lock(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path, public_origin="http://testserver")
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        token = app.state.session_manager.exchange(app.state.session_manager.bootstrap_token)
        headers = {
            "Authorization": f"Bearer {token}",
            "Origin": "http://testserver",
        }
        project = client.post(
            "/api/v1/projects",
            json={"name": "Startup lock"},
            headers=headers,
        ).json()
        created = client.post(
            f"/api/v1/projects/{project['id']}/artifact-imports",
            json=_declaration().model_dump(mode="json"),
            headers=headers,
        ).json()
        uploaded = client.put(
            f"/api/v1/projects/{project['id']}/artifact-imports/{created['import_id']}/content",
            content=_tiny_png(),
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        assert uploaded.status_code == 200

    engine = create_sqlite_engine(tmp_path / "canvas.sqlite3")
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            revision = session.get(ArtifactRevisionRow, uploaded.json()["revision_id"])
            assert revision is not None
            blob_path = ArtifactStore(tmp_path).blob_path(revision.blob_sha256)
    finally:
        engine.dispose()
    blob_path.write_bytes(b"damaged")

    broken_app = create_app(settings)
    with pytest.raises(StoredBlobIntegrityError):
        with TestClient(broken_app, base_url="http://testserver"):
            pass

    lock = DataDirectoryLock(tmp_path)
    lock.acquire()
    lock.release()
    reopened = create_sqlite_engine(tmp_path / "canvas.sqlite3")
    reopened.dispose()
