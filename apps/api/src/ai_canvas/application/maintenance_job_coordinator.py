from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.write_gate import WriteGate
from ai_canvas.infrastructure.db.models import MaintenanceJobRow


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class MaintenanceJobCoordinator:
    """Claim persistent maintenance jobs and hand them to closed, local handlers."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        handlers: Mapping[str, Callable[[UUID], object]] | None = None,
        write_gate: WriteGate | None = None,
        now: Callable[[], datetime] = utc_now,
        lease_seconds: float = 300,
        owner: str | None = None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("maintenance job lease must be positive")
        self.session_factory = session_factory
        self._handlers = dict(handlers or {})
        self._write_gate = write_gate or WriteGate()
        self._now = now
        self._lease_seconds = lease_seconds
        self._owner = owner or f"maintenance-{uuid4()}"

    def process_once(self) -> int:
        """Lease one queued job before invoking its handler outside the DB transaction."""

        now = self._now()
        with self._write_gate, self.session_factory.begin() as session:
            job = session.scalar(
                select(MaintenanceJobRow)
                .where(MaintenanceJobRow.state == "QUEUED")
                .order_by(MaintenanceJobRow.created_at, MaintenanceJobRow.id)
            )
            if job is None:
                return 0
            handler = self._handlers.get(job.job_type)
            if handler is None:
                job.state = "FAILED"
                job.error_code = "NO_JOB_HANDLER"
                job.error_message = "no registered handler accepts this maintenance job type"
                job.updated_at = now
                return 1
            job.state = "RUNNING"
            job.lease_owner = self._owner
            job.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            job.updated_at = now
            job_id = UUID(job.id)
        try:
            handler(job_id)
        except (LookupError, OSError, RuntimeError, SQLAlchemyError, ValueError):
            self._mark_handler_failure(job_id)
        return 1

    def recover_once(self) -> int:
        now = self._now()
        recovered = 0
        with self._write_gate, self.session_factory.begin() as session:
            running = list(
                session.scalars(select(MaintenanceJobRow).where(MaintenanceJobRow.state == "RUNNING"))
            )
            for job in running:
                if job.lease_expires_at is None or as_utc(job.lease_expires_at) > now:
                    continue
                if job.attempt < 1:
                    job.state = "QUEUED"
                    job.attempt += 1
                    job.lease_owner = None
                    job.lease_expires_at = None
                    job.updated_at = now
                    recovered += 1
                else:
                    job.state = "FAILED"
                    job.error_code = "LEASE_RECOVERY_EXHAUSTED"
                    job.error_message = "the persistent worker lease expired after its one retry"
                    job.lease_owner = None
                    job.lease_expires_at = None
                    job.updated_at = now
        return recovered

    def _mark_handler_failure(self, job_id: UUID) -> None:
        with self._write_gate, self.session_factory.begin() as session:
            job = session.get(MaintenanceJobRow, str(job_id))
            if job is None or job.state == "SUCCEEDED":
                return
            job.state = "FAILED"
            job.error_code = "MAINTENANCE_HANDLER_FAILED"
            job.error_message = "maintenance handler raised an unexpected error"
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = self._now()
