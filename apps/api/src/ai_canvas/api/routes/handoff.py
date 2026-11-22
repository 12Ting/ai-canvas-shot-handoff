import os
import tempfile
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from ai_canvas.application.handoff_export import export_handoff
from ai_canvas.application.handoff_frames import boundary_frame
from ai_canvas.application.handoff_service import HandoffConflict
from ai_canvas.contracts.project import CreateProjectRequest
from ai_canvas.infrastructure.artifacts.store import sanitize_filename
from ai_canvas.infrastructure.media.native_picker import DirectorySelectionCancelled


class HandoffRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def local_errors(request: Request):
            try:
                return await handler(request)
            except HandoffConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            except DirectorySelectionCancelled as exc:
                raise HTTPException(422, {"code": "directory_selection_cancelled"}) from exc
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from exc
            except (ValueError, OSError) as exc:
                raise HTTPException(422, str(exc)) from exc

        return local_errors


router = APIRouter(prefix="/api/v1", tags=["shot handoff"], route_class=HandoffRoute)
base = "/projects/{project_id}/handoff"


class HandoffAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(min_length=1, max_length=48)
    data: dict = Field(default_factory=dict)
    expected_version: int | None = Field(None, ge=0)


@router.post("/handoff/projects", status_code=201)
def create(body: CreateProjectRequest, request: Request) -> dict:
    return request.app.state.handoff_service.create(body.name)


@router.get(base)
def get(project_id: UUID, request: Request) -> dict:
    return request.app.state.handoff_service.get(str(project_id))


@router.post(base + "/bind")
def bind(project_id: UUID, request: Request) -> dict:
    return request.app.state.handoff_service.bind(str(project_id))


@router.post(base + "/rebind")
def rebind(project_id: UUID, request: Request) -> dict:
    return request.app.state.handoff_service.bind(str(project_id), relocate=True)


@router.post(base + "/actions")
def act(project_id: UUID, body: HandoffAction, request: Request) -> dict:
    return request.app.state.handoff_service.act(
        str(project_id), body.action, body.data, body.expected_version
    )


@router.get(base + "/locate")
def locate(project_id: UUID, query: Annotated[str, Query(max_length=4000)], request: Request) -> dict:
    return {"matches": request.app.state.handoff_service.locate(str(project_id), query)}


@router.get(base + "/directory")
def directory(
    project_id: UUID,
    request: Request,
    group_id: UUID | None = None,
    asset_id: UUID | None = None,
    file_id: UUID | None = None,
    location: Literal["group", "results", "refs"] = "group",
) -> dict:
    params = {
        "group_id": str(group_id) if group_id else None,
        "asset_id": str(asset_id) if asset_id else None,
        "file_id": str(file_id) if file_id else None,
        "location": location,
    }
    path = request.app.state.handoff_service.directory(str(project_id), params)
    return {"path": str(path)}


@router.get(base + "/files/{digest}")
@router.get(base + "/play/{digest}")
def file(project_id: UUID, digest: str, request: Request):
    item, path = request.app.state.handoff_service.media(str(project_id), digest)
    return FileResponse(
        path, media_type=item["mime"], filename=item["name"], content_disposition_type="inline"
    )


@router.post(base + "/play/{digest}")
def authorize_playback(project_id: UUID, digest: str, request: Request, response: Response) -> dict:
    request.app.state.handoff_service.media(str(project_id), digest)
    return request.app.state.handoff_playback.grant(request, response)


@router.get(base + "/files/{digest}/frame")
def frame(project_id: UUID, digest: str, edge: Literal["first", "last"], request: Request):
    service = request.app.state.handoff_service
    item, path = service.media(str(project_id), digest)
    if item["kind"] != "video":
        raise ValueError("请选择视频候选")
    return FileResponse(boundary_frame(service.settings, path, digest, edge), media_type="image/jpeg")


@router.post(base + "/export")
def authorize_download(
    project_id: UUID, request: Request, response: Response, issue_id: UUID | None = None
) -> dict:
    state = request.app.state.handoff_service.get(str(project_id))
    if issue_id and not any(i["id"] == str(issue_id) for i in state["issues"]):
        raise LookupError("找不到待交接的补镜意见")
    return request.app.state.handoff_playback.grant(request, response, ttl=120)


@router.get(base + "/export")
def export(project_id: UUID, request: Request, issue_id: UUID | None = None):
    service = request.app.state.handoff_service
    state = service.get(str(project_id), for_export=True)
    directory = service.settings.data_dir / "handoff-exports"
    directory.mkdir(exist_ok=True)
    fd, name = tempfile.mkstemp(dir=directory, suffix=".zip")
    os.close(fd)
    path = Path(name)
    try:
        export_handoff(state, service.files.blob_path, path, str(issue_id) if issue_id else None)
        filename = sanitize_filename(state["name"]) + ("-补镜.zip" if issue_id else "-选片.zip")
        return FileResponse(
            path,
            media_type="application/zip",
            filename=filename,
            background=BackgroundTask(path.unlink, missing_ok=True),
        )
    except BaseException:
        path.unlink(missing_ok=True)
        raise
