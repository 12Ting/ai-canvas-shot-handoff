from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from ai_canvas.infrastructure.db.models import MaintenanceJobRow
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


def test_coordinator_requeues_one_expired_running_job(tmp_path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        session_factory = make_session_factory(engine)
        now = datetime(2026, 8, 18, tzinfo=timezone.utc)
        job_id = str(uuid4())
        with session_factory.begin() as session:
            session.add(
                MaintenanceJobRow(
                    id=job_id,
                    job_type="TRANSCODE",
                    operation_key=f"transcode:{job_id}",
                    payload_json="{}",
                    state="RUNNING",
                    attempt=0,
                    progress=0.5,
                    error_code=None,
                    error_message=None,
                    lease_owner="old-process",
                    lease_expires_at=now - timedelta(seconds=1),
                    created_at=now - timedelta(seconds=5),
                    updated_at=now - timedelta(seconds=5),
                )
            )
        from ai_canvas.application.maintenance_job_coordinator import MaintenanceJobCoordinator

        coordinator = MaintenanceJobCoordinator(session_factory, now=lambda: now)
        assert coordinator.recover_once() == 1
        with session_factory() as session:
            job = session.get(MaintenanceJobRow, job_id)
            assert job is not None
            assert job.state == "QUEUED"
            assert job.lease_owner is None
            assert job.lease_expires_at is None
    finally:
        engine.dispose()


def test_coordinator_claims_a_queued_transcode_and_delegates_to_its_handler(tmp_path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        session_factory = make_session_factory(engine)
        now = datetime(2026, 8, 18, tzinfo=timezone.utc)
        job_id = str(uuid4())
        with session_factory.begin() as session:
            session.add(
                MaintenanceJobRow(
                    id=job_id,
                    job_type="TRANSCODE",
                    operation_key=f"transcode:{job_id}",
                    payload_json="{}",
                    state="QUEUED",
                    attempt=0,
                    progress=0.0,
                    error_code=None,
                    error_message=None,
                    lease_owner=None,
                    lease_expires_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        from ai_canvas.application.maintenance_job_coordinator import MaintenanceJobCoordinator

        executed: list[str] = []
        coordinator = MaintenanceJobCoordinator(
            session_factory,
            handlers={"TRANSCODE": lambda claimed_id: executed.append(str(claimed_id))},
            now=lambda: now,
        )

        assert coordinator.process_once() == 1
        assert executed == [job_id]
        with session_factory() as session:
            job = session.get(MaintenanceJobRow, job_id)
            assert job is not None
            assert job.state == "RUNNING"
            assert job.lease_owner is not None
            assert job.lease_expires_at is not None
    finally:
        engine.dispose()


def test_coordinator_does_not_swallow_process_interrupts(tmp_path) -> None:
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        session_factory = make_session_factory(engine)
        now = datetime(2026, 8, 18, tzinfo=timezone.utc)
        job_id = str(uuid4())
        with session_factory.begin() as session:
            session.add(
                MaintenanceJobRow(
                    id=job_id,
                    job_type="TRANSCODE",
                    operation_key=f"transcode:{job_id}",
                    payload_json="{}",
                    state="QUEUED",
                    attempt=0,
                    progress=0.0,
                    error_code=None,
                    error_message=None,
                    lease_owner=None,
                    lease_expires_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        from ai_canvas.application.maintenance_job_coordinator import MaintenanceJobCoordinator

        def interrupt(_job_id) -> None:
            raise KeyboardInterrupt

        coordinator = MaintenanceJobCoordinator(
            session_factory,
            handlers={"TRANSCODE": interrupt},
            now=lambda: now,
        )

        with pytest.raises(KeyboardInterrupt):
            coordinator.process_once()
    finally:
        engine.dispose()
