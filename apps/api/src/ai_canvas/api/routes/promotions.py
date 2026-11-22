from uuid import UUID

from fastapi import APIRouter, Request, status

from ai_canvas.api.security import F1Capability, require_f1_capability
from ai_canvas.application.errors import (
    ApprovalSubjectIntegrityError,
    PromotionStale,
)
from ai_canvas.contracts.branches import (
    PromotionApprovalRequest,
    PromotionRequest,
    PromotionResponse,
    UndoProposalRequest,
)
from ai_canvas.contracts.problems import (
    BranchHeadStaleProblem,
    GraphBranchNotFoundProblem,
    ProjectNotFoundProblem,
    PromotionNotFoundProblem,
    PromotionStaleProblem,
    PromotionUndoStaleProblem,
    SelectionPointerStaleProblem,
)


router = APIRouter(prefix="/api/v1/projects/{project_id}/promotions", tags=["promotions"])


@router.post(
    "",
    response_model=PromotionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"model": ProjectNotFoundProblem | GraphBranchNotFoundProblem},
        409: {
            "model": BranchHeadStaleProblem
            | SelectionPointerStaleProblem
            | PromotionStaleProblem
        },
    },
)
def request_promotion(
    project_id: UUID, body: PromotionRequest, request: Request
) -> PromotionResponse:
    actor = require_f1_capability(request, F1Capability.GRAPH_PROPOSE)
    return request.app.state.promotion_service.request(
        project_id, body, actor_id=actor.actor_id
    )


@router.post(
    "/{promotion_id}/approval-decisions",
    response_model=PromotionResponse,
    responses={
        404: {"model": PromotionNotFoundProblem},
        409: {"model": PromotionStaleProblem},
    },
)
def approve_promotion(
    project_id: UUID,
    promotion_id: UUID,
    body: PromotionApprovalRequest,
    request: Request,
) -> PromotionResponse:
    actor = require_f1_capability(request, F1Capability.GRAPH_APPROVE)
    try:
        return request.app.state.promotion_service.approve(
            project_id,
            promotion_id,
            expected_promotion_sha256=body.expected_promotion_sha256,
            actor_id=actor.actor_id,
            decision=body.decision,
        )
    except (ApprovalSubjectIntegrityError, PromotionStale):
        request.app.state.promotion_service.record_stale(project_id, promotion_id)
        raise


@router.post(
    "/{promotion_id}/apply",
    response_model=PromotionResponse,
    responses={
        404: {"model": PromotionNotFoundProblem},
        409: {
            "model": PromotionStaleProblem
            | BranchHeadStaleProblem
            | SelectionPointerStaleProblem
        },
    },
)
def apply_promotion(
    project_id: UUID,
    promotion_id: UUID,
    body: PromotionApprovalRequest,
    request: Request,
) -> PromotionResponse:
    actor = require_f1_capability(request, F1Capability.GRAPH_APPLY)
    try:
        return request.app.state.promotion_service.apply(
            project_id,
            promotion_id,
            expected_promotion_sha256=body.expected_promotion_sha256,
            actor_id=actor.actor_id,
        )
    except (ApprovalSubjectIntegrityError, PromotionStale):
        request.app.state.promotion_service.record_stale(project_id, promotion_id)
        raise


@router.post(
    "/{promotion_id}/undo-proposals",
    response_model=PromotionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"model": PromotionNotFoundProblem},
        409: {"model": PromotionUndoStaleProblem},
    },
)
def undo_promotion(
    project_id: UUID,
    promotion_id: UUID,
    body: UndoProposalRequest,
    request: Request,
) -> PromotionResponse:
    actor = require_f1_capability(request, F1Capability.GRAPH_PROPOSE)
    return request.app.state.promotion_service.undo_proposal(
        project_id,
        promotion_id,
        client_mutation_id=body.client_mutation_id,
        actor_id=actor.actor_id,
    )
