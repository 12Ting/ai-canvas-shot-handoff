from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, current_thread
from uuid import UUID

import pytest
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.errors import ArtifactRevisionNotFound
from ai_canvas.application.media_stream_service import MediaStreamService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.config import Settings
from ai_canvas.infrastructure.db.models import (
    ArtifactRevisionRow,
    ArtifactRow,
    MediaSessionGrantRow,
    MediaSessionRow,
    ProjectRow,
    StoredBlobRow,
)
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


class TrackingLease:
    def __init__(self) -> None:
        self.closed = Event()

    def close(self) -> None:
        self.closed.set()

    def retire(self) -> None:
        self.close()


class BlockingRetireLease(TrackingLease):
    def __init__(self) -> None:
        super().__init__()
        self.retire_started = Event()
        self.release_retire = Event()

    def close(self) -> None:
        self.retire()

    def retire(self) -> None:
        self.retire_started.set()
        if not self.release_retire.wait(timeout=2):
            raise AssertionError("test did not release lease retirement")
        self.closed.set()


def _service_with_session(
    tmp_path: Path,
    *,
    idle_expires_at: datetime,
    absolute_expires_at: datetime,
) -> tuple[MediaStreamService, MutableClock, TrackingLease, Engine]:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    token = "lease-cleanup-token"
    with session_factory.begin() as session:
        session.add(
            MediaSessionRow(
                id=token,
                subject="local",
                created_at=clock.value,
                last_used_at=clock.value,
                idle_expires_at=idle_expires_at,
                absolute_expires_at=absolute_expires_at,
                revoked_at=None,
            )
        )
    service = MediaStreamService(
        session_factory,
        settings=Settings(data_dir=tmp_path),
        now=clock,
        cleanup_interval_seconds=0.01,
    )
    lease = TrackingLease()
    service._external_leases[(token, "external-revision")] = lease  # type: ignore[assignment]
    return service, clock, lease, engine


def _add_managed_revision(
    session_factory: sessionmaker[Session],
    *,
    now: datetime,
    grant_token: str | None = "lease-cleanup-token",
) -> UUID:
    project_id = "00000000-0000-0000-0000-000000000001"
    artifact_id = "00000000-0000-0000-0000-000000000002"
    revision_id = UUID("00000000-0000-0000-0000-000000000003")
    digest = "a" * 64
    with session_factory.begin() as session:
        session.add(
            ProjectRow(
                id=project_id,
                name="lease refresh",
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add_all(
            (
                StoredBlobRow(
                    sha256=digest,
                    byte_size=1,
                    media_kind="image",
                    media_type="image/png",
                    metadata_json="{}",
                    created_at=now,
                ),
                ArtifactRow(
                    id=artifact_id,
                    origin_project_id=project_id,
                    kind="image",
                    created_at=now,
                ),
            )
        )
        session.flush()
        session.add(
            ArtifactRevisionRow(
                id=str(revision_id),
                artifact_id=artifact_id,
                sequence=1,
                blob_sha256=digest,
                storage_kind="MANAGED_BLOB",
                media_health="AVAILABLE",
                external_snapshot_id=None,
                source_kind="test",
                source_project_id=project_id,
                source_import_session_id=None,
                display_filename="refresh.png",
                discarded_at=None,
                created_at=now,
            )
        )
        if grant_token is not None:
            session.add(
                MediaSessionGrantRow(
                    media_session_id=grant_token,
                    resource_kind="artifact_revision",
                    resource_id=str(revision_id),
                    granted_at=now,
                )
            )
    return revision_id


def test_idle_expiry_actively_closes_external_lease_without_old_token_access(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    service, clock, lease, engine = _service_with_session(
        tmp_path,
        idle_expires_at=start + timedelta(seconds=5),
        absolute_expires_at=start + timedelta(seconds=50),
    )
    try:
        service.start_cleanup()
        clock.value = start + timedelta(seconds=6)

        assert lease.closed.wait(timeout=1)
        assert service._external_leases == {}
    finally:
        service.close()
        engine.dispose()


def test_absolute_expiry_actively_closes_external_lease_without_old_token_access(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    service, clock, lease, engine = _service_with_session(
        tmp_path,
        idle_expires_at=start + timedelta(seconds=50),
        absolute_expires_at=start + timedelta(seconds=5),
    )
    try:
        service.start_cleanup()
        clock.value = start + timedelta(seconds=6)

        assert lease.closed.wait(timeout=1)
        assert service._external_leases == {}
    finally:
        service.close()
        engine.dispose()


def test_revocation_actively_closes_external_lease_without_token_access(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    service, _clock, lease, engine = _service_with_session(
        tmp_path,
        idle_expires_at=start + timedelta(seconds=50),
        absolute_expires_at=start + timedelta(seconds=100),
    )
    try:
        with service.session_factory.begin() as session:
            session.get(MediaSessionRow, "lease-cleanup-token").revoked_at = start
        service.start_cleanup()

        assert lease.closed.wait(timeout=1)
        assert service._external_leases == {}
    finally:
        service.close()
        engine.dispose()


def test_stream_access_refreshes_idle_deadline_used_by_active_cleanup(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    service, clock, lease, engine = _service_with_session(
        tmp_path,
        idle_expires_at=start + timedelta(seconds=2),
        absolute_expires_at=start + timedelta(seconds=50),
    )
    service.settings = Settings(data_dir=tmp_path, media_session_idle_seconds=5)
    revision_id = _add_managed_revision(
        make_session_factory(engine),
        now=start,
    )
    try:
        service.start_cleanup()
        clock.value = start + timedelta(seconds=1)
        service.stream_resource(
            revision_id=revision_id,
            token="lease-cleanup-token",
        )

        clock.value = start + timedelta(seconds=3)
        assert not lease.closed.wait(timeout=0.1)

        clock.value = start + timedelta(seconds=7)
        assert lease.closed.wait(timeout=1)
    finally:
        service.close()
        engine.dispose()


def test_prune_removes_all_expired_sessions_and_grants_but_keeps_active_rows(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = (
        ("expired-managed", now - timedelta(seconds=1), now + timedelta(seconds=50)),
        ("expired-external", now + timedelta(seconds=50), now - timedelta(seconds=1)),
        ("active-session", now + timedelta(seconds=50), now + timedelta(seconds=100)),
    )
    with session_factory.begin() as session:
        for token, idle_expires_at, absolute_expires_at in rows:
            session.add(
                MediaSessionRow(
                    id=token,
                    subject="local",
                    created_at=now,
                    last_used_at=now,
                    idle_expires_at=idle_expires_at,
                    absolute_expires_at=absolute_expires_at,
                    revoked_at=None,
                )
            )
        session.flush()
        for index, (token, _idle_expires_at, _absolute_expires_at) in enumerate(rows):
            session.add(
                MediaSessionGrantRow(
                    media_session_id=token,
                    resource_kind="artifact_revision",
                    resource_id=f"00000000-0000-0000-0000-{index:012d}",
                    granted_at=now,
                )
            )
    service = MediaStreamService(
        session_factory,
        settings=Settings(data_dir=tmp_path),
        now=lambda: now,
    )
    external_lease = TrackingLease()
    service._external_leases[("expired-external", "external-revision")] = external_lease  # type: ignore[assignment]
    try:
        service.prune_external_leases()

        with session_factory() as session:
            assert session.get(MediaSessionRow, "expired-managed") is None
            assert session.get(MediaSessionRow, "expired-external") is None
            assert session.get(MediaSessionRow, "active-session") is not None
            remaining_grants = set(
                session.scalars(select(MediaSessionGrantRow.media_session_id))
            )
        assert remaining_grants == {"active-session"}
        assert external_lease.closed.is_set()
    finally:
        service.close()
        engine.dispose()


def test_prune_retires_slow_lease_outside_write_gate(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    service, _clock, _lease, engine = _service_with_session(
        tmp_path,
        idle_expires_at=start - timedelta(seconds=1),
        absolute_expires_at=start + timedelta(seconds=50),
    )
    blocking_lease = BlockingRetireLease()
    service._external_leases[("lease-cleanup-token", "external-revision")] = blocking_lease  # type: ignore[assignment]
    gate_acquired = Event()

    def unrelated_gate_work() -> None:
        with service._write_gate:
            gate_acquired.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            prune_future = executor.submit(service.prune_external_leases)
            assert blocking_lease.retire_started.wait(timeout=1)
            gate_future = executor.submit(unrelated_gate_work)
            try:
                assert gate_acquired.wait(timeout=0.25)
            finally:
                blocking_lease.release_retire.set()
            prune_future.result(timeout=2)
            gate_future.result(timeout=2)
    finally:
        blocking_lease.release_retire.set()
        service.close()
        engine.dispose()


def test_issue_uses_clock_after_long_preparation_for_new_deadlines(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock = MutableClock(start)
    service = MediaStreamService(
        session_factory,
        settings=Settings(
            data_dir=tmp_path,
            media_session_idle_seconds=5,
            media_session_absolute_seconds=20,
        ),
        now=clock,
    )
    revision_id = _add_managed_revision(
        session_factory,
        now=start,
        grant_token=None,
    )
    original_prepare = service._prepare_revision_for_issue

    def delayed_prepare(*args, **kwargs):
        prepared = original_prepare(*args, **kwargs)
        clock.value = start + timedelta(seconds=10)
        return prepared

    monkeypatch.setattr(service, "_prepare_revision_for_issue", delayed_prepare)
    try:
        issued = service.issue(revision_id)

        assert issued.idle_expires_at == start + timedelta(seconds=15)
        assert issued.absolute_expires_at == start + timedelta(seconds=30)
    finally:
        service.close()
        engine.dispose()


def test_issue_revalidates_managed_revision_before_grant_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    service = MediaStreamService(
        session_factory,
        settings=Settings(data_dir=tmp_path),
        now=lambda: now,
    )
    revision_id = _add_managed_revision(
        session_factory,
        now=now,
        grant_token=None,
    )
    original_prepare = service._prepare_revision_for_issue

    def discard_after_prepare(*args, **kwargs):
        prepared = original_prepare(*args, **kwargs)
        with session_factory.begin() as session:
            revision = session.get(ArtifactRevisionRow, str(revision_id))
            assert revision is not None
            revision.discarded_at = now
        return prepared

    monkeypatch.setattr(service, "_prepare_revision_for_issue", discard_after_prepare)
    try:
        with pytest.raises(ArtifactRevisionNotFound):
            service.issue(revision_id)

        with session_factory() as session:
            assert session.scalar(select(MediaSessionGrantRow)) is None
            assert session.scalar(select(MediaSessionRow)) is None
    finally:
        service.close()
        engine.dispose()


def test_reusable_session_query_filters_expired_deadlines_in_sql(tmp_path: Path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with session_factory.begin() as session:
        session.add(
            MediaSessionRow(
                id="expired-candidate",
                subject="local",
                created_at=now,
                last_used_at=now,
                idle_expires_at=now - timedelta(seconds=1),
                absolute_expires_at=now + timedelta(seconds=10),
                revoked_at=None,
            )
        )
    revision_id = _add_managed_revision(
        session_factory,
        now=now,
        grant_token=None,
    )
    service = MediaStreamService(
        session_factory,
        settings=Settings(data_dir=tmp_path),
        now=lambda: now,
    )
    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        issued = service.issue(revision_id)
        assert issued.token != "expired-candidate"
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)
        service.close()
        engine.dispose()
    candidate_queries = [
        statement
        for statement in statements
        if "from media_sessions" in statement and "order by" in statement
    ]
    assert len(candidate_queries) == 1
    assert "media_sessions.idle_expires_at >" in candidate_queries[0]
    assert "media_sessions.absolute_expires_at >" in candidate_queries[0]


def test_close_stops_cleanup_waiting_on_busy_gate_without_post_shutdown_sql(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    cleanup_gate_attempted = Event()

    class ObservableWriteGate(WriteGate):
        def __enter__(self):
            if current_thread().name == "media-external-lease-cleanup":
                cleanup_gate_attempted.set()
            return super().__enter__()

        @contextmanager
        def try_acquire(self):
            if current_thread().name == "media-external-lease-cleanup":
                cleanup_gate_attempted.set()
            with super().try_acquire() as acquired:
                yield acquired

    write_gate = ObservableWriteGate()
    service = MediaStreamService(
        make_session_factory(engine),
        settings=Settings(data_dir=tmp_path),
        write_gate=write_gate,
        cleanup_interval_seconds=0.01,
    )
    close_returned = Event()
    sql_after_close: list[str] = []

    def capture_post_close_sql(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        if close_returned.is_set():
            sql_after_close.append(statement)

    event.listen(engine, "before_cursor_execute", capture_post_close_sql)
    write_gate.__enter__()
    try:
        service.start_cleanup()
        cleanup_thread = service._cleanup_thread
        assert cleanup_thread is not None
        assert cleanup_gate_attempted.wait(timeout=1)
        with ThreadPoolExecutor(max_workers=1) as executor:
            close_future = executor.submit(service.close)
            assert close_future.result(timeout=0.5) is None
        close_returned.set()
        thread_alive_when_close_returned = cleanup_thread.is_alive()
    finally:
        write_gate.__exit__(None, None, None)
    cleanup_thread.join(timeout=1)
    try:
        assert not thread_alive_when_close_returned
        assert not cleanup_thread.is_alive()
        assert service._cleanup_thread is None
        assert sql_after_close == []
    finally:
        event.remove(engine, "before_cursor_execute", capture_post_close_sql)
        engine.dispose()
