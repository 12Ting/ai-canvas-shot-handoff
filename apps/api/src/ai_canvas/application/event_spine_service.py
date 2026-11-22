import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts.experience_events import (
    ActorKind,
    EventCursorResponse,
    ExperienceEventPage,
    ExperienceEventResponse,
)
from ai_canvas.domain.control.canonical import canonical_sha256
from ai_canvas.infrastructure.db.models import EventConsumerCursorRow, ExperienceEventRow


class EventCursorAckOutOfRange(ValueError):
    """Raised when a client tries to acknowledge an unseen event position."""


class EventTypeFilterInvalid(ValueError):
    """Raised when an event query contains an unknown or empty event type."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class EventSpineService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        write_gate: WriteGate | None = None,
    ) -> None:
        self.session_factory = session_factory
        self._write_gate = write_gate or WriteGate()

    @staticmethod
    def _event_response(row: ExperienceEventRow) -> ExperienceEventResponse:
        return ExperienceEventResponse(
            event_id=UUID(row.event_id),
            stream_position=row.stream_position,
            occurred_at=as_utc(row.occurred_at),
            project_id=UUID(row.project_id),
            correlation_id=UUID(row.correlation_id),
            causation_event_id=(UUID(row.causation_event_id) if row.causation_event_id else None),
            actor_kind=row.actor_kind,
            actor_id=row.actor_id,
            subject_type=row.subject_type,
            subject_id=UUID(row.subject_id),
            event_type=row.event_type,
            schema_version=row.schema_version,
            payload=json.loads(row.payload_json),
            payload_sha256=row.payload_sha256,
            envelope_sha256=row.envelope_sha256,
        )

    @staticmethod
    def _cursor_response(row: EventConsumerCursorRow) -> EventCursorResponse:
        return EventCursorResponse(
            consumer_key=row.consumer_key,
            last_ack_stream_position=row.last_ack_stream_position,
        )

    def append_in_session(
        self,
        session: Session,
        *,
        project_id: UUID,
        correlation_id: UUID,
        actor_kind: ActorKind,
        actor_id: str,
        subject_type: str,
        subject_id: UUID,
        event_type: str,
        payload: dict[str, Any],
        causation_event_id: UUID | None = None,
    ) -> ExperienceEventResponse:
        event_id = uuid4()
        occurred_at = utc_now()
        payload_sha256 = canonical_sha256(payload)
        envelope_sha256 = canonical_sha256(
            {
                "event_id": str(event_id),
                "occurred_at": occurred_at.isoformat(),
                "project_id": str(project_id),
                "correlation_id": str(correlation_id),
                "causation_event_id": (str(causation_event_id) if causation_event_id is not None else None),
                "actor_kind": actor_kind,
                "actor_id": actor_id,
                "subject_type": subject_type,
                "subject_id": str(subject_id),
                "event_type": event_type,
                "schema_version": 1,
                "payload_sha256": payload_sha256,
            }
        )
        row = ExperienceEventRow(
            event_id=str(event_id),
            occurred_at=occurred_at,
            project_id=str(project_id),
            correlation_id=str(correlation_id),
            causation_event_id=(str(causation_event_id) if causation_event_id else None),
            actor_kind=actor_kind,
            actor_id=actor_id,
            subject_type=subject_type,
            subject_id=str(subject_id),
            event_type=event_type,
            schema_version=1,
            payload_json=json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ),
            payload_sha256=payload_sha256,
            envelope_sha256=envelope_sha256,
        )
        session.add(row)
        session.flush()
        return self._event_response(row)

    def append(
        self,
        *,
        project_id: UUID,
        correlation_id: UUID,
        actor_kind: ActorKind,
        actor_id: str,
        subject_type: str,
        subject_id: UUID,
        event_type: str,
        payload: dict[str, Any],
        causation_event_id: UUID | None = None,
    ) -> ExperienceEventResponse:
        with self._write_gate:
            with self.session_factory.begin() as session:
                return self.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=correlation_id,
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    event_type=event_type,
                    payload=payload,
                    causation_event_id=causation_event_id,
                )

    def _page(
        self,
        session: Session,
        project_id: UUID,
        *,
        after: int,
        limit: int,
        correlation_id: UUID | None = None,
        event_types: frozenset[str] | None = None,
    ) -> ExperienceEventPage:
        if after < 0 or not 1 <= limit <= 100:
            raise ValueError("event query position or limit is invalid")
        query = select(ExperienceEventRow).where(
            ExperienceEventRow.project_id == str(project_id),
            ExperienceEventRow.stream_position > after,
        )
        if correlation_id is not None:
            query = query.where(ExperienceEventRow.correlation_id == str(correlation_id))
        if event_types is not None:
            query = query.where(ExperienceEventRow.event_type.in_(event_types))
        rows = list(session.scalars(query.order_by(ExperienceEventRow.stream_position).limit(limit)))
        high_watermark = session.scalar(
            select(ExperienceEventRow.stream_position)
            .where(ExperienceEventRow.project_id == str(project_id))
            .order_by(ExperienceEventRow.stream_position.desc())
            .limit(1)
        )
        return ExperienceEventPage(
            events=[self._event_response(row) for row in rows],
            next_after=(rows[-1].stream_position if rows else after),
            high_watermark=high_watermark or 0,
        )

    def query(
        self,
        project_id: UUID,
        *,
        after: int,
        limit: int,
        correlation_id: UUID | None = None,
        event_types: frozenset[str] | None = None,
    ) -> ExperienceEventPage:
        with self.session_factory() as session:
            return self._page(
                session,
                project_id,
                after=after,
                limit=limit,
                correlation_id=correlation_id,
                event_types=event_types,
            )

    def _cursor(
        self,
        session: Session,
        project_id: UUID,
        owner_id: str,
        consumer_key: str,
    ) -> EventConsumerCursorRow:
        row = session.scalar(
            select(EventConsumerCursorRow).where(
                EventConsumerCursorRow.project_id == str(project_id),
                EventConsumerCursorRow.owner_id == owner_id,
                EventConsumerCursorRow.consumer_key == consumer_key,
            )
        )
        if row is not None:
            return row
        now = utc_now()
        row = EventConsumerCursorRow(
            id=str(uuid4()),
            project_id=str(project_id),
            owner_id=owner_id,
            consumer_key=consumer_key,
            last_ack_stream_position=0,
            last_delivered_stream_position=0,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row

    def read_consumer(
        self,
        project_id: UUID,
        owner_id: str,
        consumer_key: str,
        *,
        limit: int,
    ) -> ExperienceEventPage:
        with self._write_gate:
            with self.session_factory.begin() as session:
                cursor = self._cursor(session, project_id, owner_id, consumer_key)
                page = self._page(
                    session,
                    project_id,
                    after=cursor.last_ack_stream_position,
                    limit=limit,
                )
                if page.next_after > cursor.last_delivered_stream_position:
                    cursor.last_delivered_stream_position = page.next_after
                    cursor.updated_at = utc_now()
                return page

    def ack(
        self,
        project_id: UUID,
        owner_id: str,
        consumer_key: str,
        ack_stream_position: int,
    ) -> EventCursorResponse:
        with self._write_gate:
            with self.session_factory.begin() as session:
                cursor = self._cursor(session, project_id, owner_id, consumer_key)
                if (
                    ack_stream_position < cursor.last_ack_stream_position
                    or ack_stream_position > cursor.last_delivered_stream_position
                ):
                    raise EventCursorAckOutOfRange
                if ack_stream_position > 0:
                    owned_position = session.scalar(
                        select(ExperienceEventRow.stream_position).where(
                            ExperienceEventRow.project_id == str(project_id),
                            ExperienceEventRow.stream_position == ack_stream_position,
                        )
                    )
                    if owned_position is None:
                        raise EventCursorAckOutOfRange
                cursor.last_ack_stream_position = ack_stream_position
                cursor.updated_at = utc_now()
                return self._cursor_response(cursor)
