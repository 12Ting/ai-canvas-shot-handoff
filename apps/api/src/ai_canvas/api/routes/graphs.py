from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from ai_canvas.contracts.graph import (
    GraphRevisionResponse,
    ViewportState,
)
from ai_canvas.contracts.problems import (
    GraphRevisionNotFoundProblem,
    ProjectNotFoundProblem,
    RequestValidationProblem,
    ShotNotFoundProblem,
    ShotSlotAssociationCursorInvalidProblem,
    StoredGraphIntegrityProblem,
)
from ai_canvas.contracts.shot_history import (
    AssociationCursor,
    ShotSlot,
    ShotSlotAssociationHistoryResponse,
)


router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["graphs"])


@router.get(
    "/graph",
    response_model=GraphRevisionResponse,
    responses={
        404: {"model": ProjectNotFoundProblem | GraphRevisionNotFoundProblem},
        422: {"model": RequestValidationProblem},
        500: {"model": StoredGraphIntegrityProblem},
    },
)
def get_graph(project_id: UUID, request: Request) -> GraphRevisionResponse:
    return request.app.state.graph_service.get_active(project_id)


@router.get(
    "/shots/{shot_id}/slots/{slot}/associations",
    response_model=ShotSlotAssociationHistoryResponse,
    responses={
        404: {
            "model": (
                ProjectNotFoundProblem
                | GraphRevisionNotFoundProblem
                | ShotNotFoundProblem
            )
        },
        422: {
            "model": (
                RequestValidationProblem
                | ShotSlotAssociationCursorInvalidProblem
            )
        },
        500: {"model": StoredGraphIntegrityProblem},
    },
)
def list_shot_slot_associations(
    project_id: UUID,
    shot_id: UUID,
    slot: ShotSlot,
    request: Request,
    anchor_revision_id: UUID,
    cursor: Annotated[AssociationCursor | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ShotSlotAssociationHistoryResponse:
    return request.app.state.graph_service.list_shot_slot_associations(
        project_id=project_id,
        shot_id=shot_id,
        slot=slot,
        anchor_revision_id=anchor_revision_id,
        cursor=cursor,
        limit=limit,
    )


@router.put(
    "/view-state",
    response_model=ViewportState,
    responses={
        404: {"model": ProjectNotFoundProblem},
        422: {"model": RequestValidationProblem},
    },
)
def update_view_state(
    project_id: UUID,
    body: ViewportState,
    request: Request,
) -> ViewportState:
    return request.app.state.graph_service.update_viewport(project_id, body)
