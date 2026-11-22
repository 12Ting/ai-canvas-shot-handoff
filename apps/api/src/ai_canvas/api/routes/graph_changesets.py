from uuid import UUID

from fastapi import APIRouter, Request, status

from ai_canvas.api.security import F1Capability, require_f1_capability
from ai_canvas.contracts.graph_changesets import (
    CreateGraphChangeSetRequest,
    GraphChangeSetApprovalRequest,
    GraphChangeSetBaseRevisionStaleProblem,
    GraphChangeSetResponse,
)
from ai_canvas.contracts.problems import (
    BranchHeadStaleProblem,
    GraphBranchNameConflictProblem,
    GraphBranchNotFoundProblem,
    GraphChangeSetUndoStaleProblem,
    GraphChangeSetUndoUnsupportedProblem,
    GraphRevisionNotFoundProblem,
    InvalidGraphProblem,
    ProjectNotFoundProblem,
    RequestBodyTooLargeProblem,
    SelectionPointerStaleProblem,
    StaleImpactConfirmationRequiredProblem,
    StoredGraphIntegrityProblem,
)
from ai_canvas.contracts.branches import UndoProposalRequest


router = APIRouter(
    prefix="/api/v1/projects/{project_id}/graph-change-sets",
    tags=["graph-change-sets"],
)


@router.post(
    "",
    response_model=GraphChangeSetResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"model": ProjectNotFoundProblem | GraphRevisionNotFoundProblem},
        409: {
            "model": GraphChangeSetBaseRevisionStaleProblem
            | BranchHeadStaleProblem
            | SelectionPointerStaleProblem
            | GraphBranchNameConflictProblem
        },
        413: {"model": RequestBodyTooLargeProblem},
        422: {"model": InvalidGraphProblem},
        500: {"model": StoredGraphIntegrityProblem},
    },
)
def request_graph_change_set(
    project_id: UUID,
    body: CreateGraphChangeSetRequest,
    request: Request,
) -> GraphChangeSetResponse:
    actor = require_f1_capability(request, F1Capability.GRAPH_PROPOSE)
    return request.app.state.graph_changeset_service.request(
        project_id,
        body,
        actor_id=actor.actor_id,
        actor_kind=actor.actor_kind,
    )


@router.post(
    "/{change_set_id}/approval-decisions",
    response_model=GraphChangeSetResponse,
    responses={422: {"model": StaleImpactConfirmationRequiredProblem}},
)
def approve_graph_change_set(
    project_id: UUID,
    change_set_id: UUID,
    body: GraphChangeSetApprovalRequest,
    request: Request,
) -> GraphChangeSetResponse:
    actor = require_f1_capability(request, F1Capability.GRAPH_APPROVE)
    return request.app.state.graph_changeset_service.approve(
        project_id,
        change_set_id,
        expected_changeset_sha256=body.expected_changeset_sha256,
        actor_id=actor.actor_id,
        decision=body.decision,
        confirmed_stale_impact_sha256=body.confirmed_stale_impact_sha256,
    )


@router.post(
    "/{change_set_id}/apply",
    response_model=GraphChangeSetResponse,
    responses={
        409: {
            "model": GraphChangeSetBaseRevisionStaleProblem
            | BranchHeadStaleProblem
            | SelectionPointerStaleProblem
        }
    },
)
def apply_graph_change_set(
    project_id: UUID,
    change_set_id: UUID,
    body: GraphChangeSetApprovalRequest,
    request: Request,
) -> GraphChangeSetResponse:
    actor = require_f1_capability(request, F1Capability.GRAPH_APPLY)
    return request.app.state.graph_changeset_service.apply(
        project_id,
        change_set_id,
        expected_changeset_sha256=body.expected_changeset_sha256,
        actor_id=actor.actor_id,
    )


@router.post(
    "/{change_set_id}/undo-proposals",
    response_model=GraphChangeSetResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"model": GraphBranchNotFoundProblem},
        409: {
            "model": GraphChangeSetUndoStaleProblem
            | GraphChangeSetUndoUnsupportedProblem
        },
    },
)
def undo_graph_change_set(
    project_id: UUID,
    change_set_id: UUID,
    body: UndoProposalRequest,
    request: Request,
) -> GraphChangeSetResponse:
    actor = require_f1_capability(request, F1Capability.GRAPH_PROPOSE)
    return request.app.state.graph_changeset_service.undo_proposal(
        project_id,
        change_set_id,
        client_mutation_id=body.client_mutation_id,
        actor_id=actor.actor_id,
    )
