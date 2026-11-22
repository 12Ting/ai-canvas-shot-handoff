from uuid import UUID

from fastapi import APIRouter, Request

from ai_canvas.contracts.branches import BranchGraphResponse, BranchWorkspaceResponse
from ai_canvas.contracts.problems import (
    GraphBranchNotFoundProblem,
    ProjectNotFoundProblem,
    RequestValidationProblem,
    StoredGraphIntegrityProblem,
)


router = APIRouter(prefix="/api/v1/projects/{project_id}/branches", tags=["branches"])


@router.get(
    "",
    response_model=BranchWorkspaceResponse,
    responses={
        404: {"model": ProjectNotFoundProblem},
        500: {"model": StoredGraphIntegrityProblem},
    },
)
def list_branches(project_id: UUID, request: Request) -> BranchWorkspaceResponse:
    return request.app.state.branch_service.workspace(project_id)


@router.get(
    "/{branch_id}/graph",
    response_model=BranchGraphResponse,
    responses={
        404: {"model": ProjectNotFoundProblem | GraphBranchNotFoundProblem},
        422: {"model": RequestValidationProblem},
        500: {"model": StoredGraphIntegrityProblem},
    },
)
def get_branch_graph(
    project_id: UUID, branch_id: UUID, request: Request
) -> BranchGraphResponse:
    return request.app.state.branch_service.graph_at_head(project_id, branch_id)
