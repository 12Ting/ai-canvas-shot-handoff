from uuid import UUID

from fastapi import APIRouter, Request, status

from ai_canvas.contracts.problems import (
    GraphRevisionNotFoundProblem,
    ProjectNotFoundProblem,
    RequestValidationProblem,
)
from ai_canvas.contracts.project import CreateProjectRequest, ProjectResponse


router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    responses={422: {"model": RequestValidationProblem}},
)
def create_project(body: CreateProjectRequest, request: Request) -> ProjectResponse:
    return request.app.state.project_service.create(body.name)


@router.get("", response_model=list[ProjectResponse])
def list_projects(request: Request) -> list[ProjectResponse]:
    return request.app.state.project_service.list()


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    responses={
        404: {"model": ProjectNotFoundProblem | GraphRevisionNotFoundProblem},
        422: {"model": RequestValidationProblem},
    },
)
def get_project(project_id: UUID, request: Request) -> ProjectResponse:
    return request.app.state.project_service.get(project_id)
