from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from ai_canvas.api.security import F1Capability, require_f1_capability
from ai_canvas.contracts.graph import ResourceKind
from ai_canvas.contracts.problems import (
    InvalidResourceReferenceProblem,
    ProjectNotFoundProblem,
    RequestBodyTooLargeProblem,
    ResourceNotFoundProblem,
    ResourceRevisionConflictProblem,
    ResourceRevisionNotFoundProblem,
    StoredGraphIntegrityProblem,
)
from ai_canvas.contracts.resources import (
    ArchiveResourceRequest,
    CreateResourceRequest,
    CreateResourceRevisionRequest,
    ResourceDetailResponse,
    ResourceResponse,
    ResourceRevisionResponse,
    ResourceUpdateItem,
)


router = APIRouter(tags=["resources"])


@router.post(
    "/api/v1/projects/{project_id}/resources",
    response_model=ResourceResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"model": ProjectNotFoundProblem | ResourceRevisionNotFoundProblem},
        413: {"model": RequestBodyTooLargeProblem},
        422: {"model": InvalidResourceReferenceProblem},
    },
)
def create_resource(
    project_id: UUID,
    body: CreateResourceRequest,
    request: Request,
) -> ResourceResponse:
    actor = require_f1_capability(request, F1Capability.RESOURCE_WRITE)
    return request.app.state.resource_catalog_service.create_resource(
        project_id,
        body,
        actor_id=actor.actor_id,
        actor_kind=actor.actor_kind,
    )


@router.get("/api/v1/resources", response_model=list[ResourceResponse])
def list_resources(
    request: Request,
    kind: ResourceKind | None = None,
    query: str = "",
    tags: Annotated[list[str] | None, Query(max_length=64)] = None,
    include_archived: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
) -> list[ResourceResponse]:
    return request.app.state.resource_catalog_service.list_resources(
        kind=kind,
        query=query,
        tags=tuple(tags or ()),
        include_archived=include_archived,
        limit=limit,
    )


@router.get(
    "/api/v1/resources/{resource_id}",
    response_model=ResourceDetailResponse,
    responses={
        404: {"model": ResourceNotFoundProblem | ResourceRevisionNotFoundProblem},
    },
)
def get_resource(resource_id: UUID, request: Request) -> ResourceDetailResponse:
    return request.app.state.resource_catalog_service.get_resource(resource_id)


@router.post(
    "/api/v1/projects/{project_id}/resources/{resource_id}/revisions",
    response_model=ResourceRevisionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"model": ProjectNotFoundProblem | ResourceNotFoundProblem},
        409: {"model": ResourceRevisionConflictProblem},
        413: {"model": RequestBodyTooLargeProblem},
        422: {"model": InvalidResourceReferenceProblem},
    },
)
def create_resource_revision(
    project_id: UUID,
    resource_id: UUID,
    body: CreateResourceRevisionRequest,
    request: Request,
) -> ResourceRevisionResponse:
    actor = require_f1_capability(request, F1Capability.RESOURCE_WRITE)
    return request.app.state.resource_catalog_service.create_revision(
        project_id,
        resource_id,
        body,
        actor_id=actor.actor_id,
        actor_kind=actor.actor_kind,
    )


@router.post(
    "/api/v1/projects/{project_id}/resources/{resource_id}/archive",
    response_model=ResourceResponse,
    responses={
        404: {"model": ProjectNotFoundProblem | ResourceNotFoundProblem},
        413: {"model": RequestBodyTooLargeProblem},
    },
)
def archive_resource(
    project_id: UUID,
    resource_id: UUID,
    _body: ArchiveResourceRequest,
    request: Request,
) -> ResourceResponse:
    actor = require_f1_capability(request, F1Capability.RESOURCE_WRITE)
    return request.app.state.resource_catalog_service.archive_resource(
        project_id,
        resource_id,
        actor_id=actor.actor_id,
        actor_kind=actor.actor_kind,
    )


@router.get(
    "/api/v1/projects/{project_id}/resource-updates",
    response_model=list[ResourceUpdateItem],
    responses={
        404: {"model": ProjectNotFoundProblem},
        500: {"model": StoredGraphIntegrityProblem},
    },
)
def list_resource_updates(project_id: UUID, request: Request) -> list[ResourceUpdateItem]:
    return request.app.state.resource_catalog_service.list_project_updates(project_id)
