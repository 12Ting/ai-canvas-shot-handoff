from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from ai_canvas.application.event_spine_service import (
    EventCursorAckOutOfRange,
    EventSpineService,
)
from ai_canvas.infrastructure.db.models import ProjectRow
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)


@pytest.fixture
def event_service(tmp_path: Path):
    database_path = tmp_path / "canvas.sqlite3"
    run_migrations(database_path)
    engine = create_sqlite_engine(database_path)
    session_factory = make_session_factory(engine)
    project_id = uuid4()
    with session_factory.begin() as session:
        session.add(
            ProjectRow(
                id=str(project_id),
                name="F1 event test",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
    service = EventSpineService(session_factory)
    try:
        yield service, project_id
    finally:
        engine.dispose()


def test_append_query_and_explicit_cursor_ack_are_separate(event_service) -> None:
    service, project_id = event_service
    correlation_id = uuid4()
    first = service.append(
        project_id=project_id,
        correlation_id=correlation_id,
        actor_kind="HUMAN",
        actor_id="local-session",
        subject_type="graph_change_set",
        subject_id=uuid4(),
        event_type="graph_change_set.requested",
        payload={"changeset_sha256": "a" * 64},
    )
    second = service.append(
        project_id=project_id,
        correlation_id=correlation_id,
        actor_kind="HUMAN",
        actor_id="local-session",
        subject_type="graph_change_set",
        subject_id=uuid4(),
        event_type="approval.recorded",
        payload={"decision": "APPROVED"},
        causation_event_id=first.event_id,
    )

    page = service.read_consumer(project_id, "local-session", "timeline", limit=1)
    assert [event.event_id for event in page.events] == [first.event_id]
    assert page.next_after == first.stream_position

    with pytest.raises(EventCursorAckOutOfRange):
        service.ack(project_id, "local-session", "timeline", second.stream_position)

    acknowledged = service.ack(project_id, "local-session", "timeline", first.stream_position)
    assert acknowledged.last_ack_stream_position == first.stream_position

    after_ack = service.read_consumer(project_id, "local-session", "timeline", limit=10)
    assert [event.event_id for event in after_ack.events] == [second.event_id]
