from typing import Annotated, get_args
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request

from ai_canvas.api.security import F1Capability, require_f1_capability
from ai_canvas.contracts.experience_events import (
    EventCursorAckRequest,
    EventCursorResponse,
    ExperienceEventPage,
    ExperienceEventType,
)
from ai_canvas.application.event_spine_service import EventTypeFilterInvalid


router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["experience-events"])


@router.get("/experience-events", response_model=ExperienceEventPage)
def query_experience_events(
    project_id: UUID,
    request: Request,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    correlation_id: UUID | None = None,
    types: str | None = None,
) -> ExperienceEventPage:
    require_f1_capability(request, F1Capability.EVENTS_READ)
    event_types: frozenset[str] | None = None
    if types is not None:
        requested_types = [value.strip() for value in types.split(",")]
        known_types = frozenset(get_args(ExperienceEventType))
        if not requested_types or any(not value or value not in known_types for value in requested_types):
            raise EventTypeFilterInvalid
        event_types = frozenset(requested_types)
    return request.app.state.event_spine_service.query(
        project_id,
        after=after,
        limit=limit,
        correlation_id=correlation_id,
        event_types=event_types,
    )


@router.get(
    "/experience-event-cursors/{consumer_key}/events",
    response_model=ExperienceEventPage,
)
def read_experience_event_cursor(
    project_id: UUID,
    consumer_key: Annotated[str, Path(min_length=1, max_length=128)],
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ExperienceEventPage:
    actor = require_f1_capability(request, F1Capability.EVENTS_READ)
    return request.app.state.event_spine_service.read_consumer(
        project_id,
        actor.actor_id,
        consumer_key,
        limit=limit,
    )


@router.put(
    "/experience-event-cursors/{consumer_key}",
    response_model=EventCursorResponse,
)
def acknowledge_experience_event_cursor(
    project_id: UUID,
    consumer_key: Annotated[str, Path(min_length=1, max_length=128)],
    body: EventCursorAckRequest,
    request: Request,
) -> EventCursorResponse:
    actor = require_f1_capability(request, F1Capability.EVENTS_CURSOR_ACK)
    return request.app.state.event_spine_service.ack(
        project_id,
        actor.actor_id,
        consumer_key,
        body.ack_stream_position,
    )
