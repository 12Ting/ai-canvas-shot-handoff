from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Request, Response
from starlette.requests import ClientDisconnect

from ai_canvas.contracts.artifact import (
    ArtifactImportSessionResponse,
    ArtifactRevisionResponse,
    CreateArtifactImportRequest,
    DiscardArtifactImportResultRequest,
    DiscardArtifactImportResultResponse,
)
from ai_canvas.contracts.problems import (
    ArtifactImportConflictProblem,
    ArtifactImportFailedProblem,
    ArtifactImportNotFoundProblem,
    ArtifactRevisionNotFoundProblem,
    ProjectNotFoundProblem,
    RequestValidationProblem,
)


router = APIRouter(tags=["artifacts"])


async def _artifact_content_chunks(request: Request) -> AsyncIterator[bytes]:
    try:
        async for chunk in request.stream():
            yield chunk
    except ClientDisconnect as error:
        raise OSError("artifact upload was interrupted") from error


@router.post(
    "/api/v1/projects/{project_id}/artifact-imports",
    response_model=ArtifactImportSessionResponse,
    responses={
        404: {"model": ProjectNotFoundProblem},
        409: {"model": ArtifactImportConflictProblem},
        422: {"model": RequestValidationProblem},
    },
)
def create_artifact_import(
    project_id: UUID,
    body: CreateArtifactImportRequest,
    request: Request,
) -> ArtifactImportSessionResponse:
    return request.app.state.artifact_service.create_import(project_id, body)


@router.get(
    "/api/v1/projects/{project_id}/artifact-imports/{import_id}",
    response_model=ArtifactImportSessionResponse,
    responses={
        404: {"model": ArtifactImportNotFoundProblem},
        422: {"model": RequestValidationProblem},
    },
)
def get_artifact_import(
    project_id: UUID,
    import_id: UUID,
    request: Request,
) -> ArtifactImportSessionResponse:
    return request.app.state.artifact_service.get_import(project_id, import_id)


@router.delete(
    "/api/v1/projects/{project_id}/artifact-imports/{import_id}/result",
    response_model=DiscardArtifactImportResultResponse,
    responses={
        202: {"model": DiscardArtifactImportResultResponse},
        404: {"model": ArtifactImportNotFoundProblem},
        409: {"model": ArtifactImportConflictProblem},
        422: {"model": RequestValidationProblem},
    },
)
def discard_artifact_import_result(
    project_id: UUID,
    import_id: UUID,
    body: DiscardArtifactImportResultRequest,
    request: Request,
    response: Response,
) -> DiscardArtifactImportResultResponse:
    result = request.app.state.artifact_service.discard_import_result(
        project_id,
        import_id,
        client_import_id=body.client_import_id,
        expected_revision_id=body.expected_revision_id,
    )
    if result.content_cleanup == "pending":
        response.status_code = 202
    return result


@router.put(
    "/api/v1/projects/{project_id}/artifact-imports/{import_id}/content",
    response_model=ArtifactRevisionResponse,
    responses={
        404: {"model": ArtifactImportNotFoundProblem},
        409: {"model": ArtifactImportConflictProblem},
        422: {"model": ArtifactImportFailedProblem | RequestValidationProblem},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        }
    },
)
async def upload_artifact_content(
    project_id: UUID,
    import_id: UUID,
    request: Request,
) -> ArtifactRevisionResponse:
    raw_length = request.headers.get("content-length")
    try:
        content_length = int(raw_length) if raw_length is not None else None
    except ValueError:
        content_length = -1
    return await request.app.state.artifact_service.upload_content(
        project_id,
        import_id,
        _artifact_content_chunks(request),
        content_length=content_length,
    )


@router.get(
    "/api/v1/artifacts/{artifact_id}/revisions",
    response_model=list[ArtifactRevisionResponse],
    responses={422: {"model": RequestValidationProblem}},
)
def list_artifact_revisions(
    artifact_id: UUID,
    request: Request,
) -> list[ArtifactRevisionResponse]:
    return request.app.state.artifact_service.list_revisions(artifact_id)


@router.get(
    "/api/v1/artifact-revisions/{revision_id}",
    response_model=ArtifactRevisionResponse,
    responses={
        404: {"model": ArtifactRevisionNotFoundProblem},
        422: {"model": RequestValidationProblem},
    },
)
def get_artifact_revision(
    revision_id: UUID,
    request: Request,
) -> ArtifactRevisionResponse:
    return request.app.state.artifact_service.get_revision(revision_id)


@router.get(
    "/api/v1/artifact-revisions/{revision_id}/content",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            }
        },
        404: {"model": ArtifactRevisionNotFoundProblem},
        422: {"model": RequestValidationProblem},
    },
)
def get_artifact_revision_content(revision_id: UUID, request: Request) -> Response:
    content = request.app.state.artifact_service.get_content(revision_id)
    return Response(
        content=content.body,
        media_type=content.media_type,
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Referrer-Policy": "no-referrer",
        },
    )
