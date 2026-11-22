from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.types import Receive, Scope, Send

from ai_canvas.application.media_stream_service import (
    MEDIA_SESSION_COOKIE,
    MediaStreamResource,
)
from ai_canvas.contracts.media_lifecycle import (
    ConfirmGcPlanRequest,
    CreateExternalReferenceFromPickerRequest,
    CreateLargeUploadRequest,
    CreateMediaSessionRequest,
    ExternalReferenceResponse,
    ExternalRootGrantResponse,
    GcPlanEntryResponse,
    GcPlanResponse,
    LargeUploadSessionResponse,
    ManagedCopyResponse,
    MediaSessionResponse,
    QuarantineEntryResponse,
    ReconfirmExternalReferenceFromPickerRequest,
    RenditionJobResponse,
    RequestRenditionRequest,
    StorageSummaryResponse,
)
from ai_canvas.domain.media.range import RangeNotSatisfiable, parse_single_range
from ai_canvas.infrastructure.media.files import open_range

router = APIRouter(tags=["media-lifecycle"])


class _MediaStreamingResponse(StreamingResponse):
    def __init__(
        self,
        content: Iterator[bytes],
        *,
        resource: MediaStreamResource,
        status_code: int,
        media_type: str,
        headers: dict[str, str],
    ) -> None:
        self._owned_iterator = content
        self._resource = resource
        super().__init__(
            content,
            status_code=status_code,
            media_type=media_type,
            headers=headers,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                close = getattr(self._owned_iterator, "close", None)
                if close is not None:
                    close()
            finally:
                self._resource.release()


def _relative_picker_selection(*, selected_file: Path, root: Path) -> str:
    try:
        return selected_file.resolve(strict=True).relative_to(root).as_posix()
    except (OSError, ValueError) as error:
        from ai_canvas.domain.media.external_reference import ExternalRootViolation

        raise ExternalRootViolation("native picker selected a file outside the granted root") from error


@router.post(
    "/api/v1/projects/{project_id}/large-uploads",
    response_model=LargeUploadSessionResponse,
)
def create_large_upload(
    project_id: UUID,
    body: CreateLargeUploadRequest,
    request: Request,
) -> LargeUploadSessionResponse:
    return request.app.state.large_upload_service.create(project_id, body)


@router.put("/api/v1/large-uploads/{upload_id}/parts/{part_number}")
async def put_large_upload_part(
    upload_id: UUID,
    part_number: int,
    request: Request,
) -> dict[str, object]:
    content_range = request.headers.get("content-range")
    content_sha256 = request.headers.get("x-content-sha256")
    if content_range is None or content_sha256 is None:
        return JSONResponse(
            status_code=422,
            content={"code": "upload_part_headers_required"},
        )
    receipt = request.app.state.large_upload_service.put_part(
        upload_id=upload_id,
        part_number=part_number,
        content=await request.body(),
        content_range=content_range,
        content_sha256=content_sha256,
    )
    return {
        "upload_id": str(receipt.upload_id),
        "part_number": receipt.part_number,
        "received_byte_size": receipt.received_byte_size,
    }


@router.post(
    "/api/v1/large-uploads/{upload_id}/complete",
    response_model=LargeUploadSessionResponse,
)
def complete_large_upload(upload_id: UUID, request: Request) -> LargeUploadSessionResponse:
    return request.app.state.large_upload_service.complete(upload_id)


@router.get(
    "/api/v1/large-uploads/{upload_id}",
    response_model=LargeUploadSessionResponse,
)
def get_large_upload(upload_id: UUID, request: Request) -> LargeUploadSessionResponse:
    return request.app.state.large_upload_service.get(upload_id)


@router.delete(
    "/api/v1/large-uploads/{upload_id}",
    response_model=LargeUploadSessionResponse,
)
def abort_large_upload(upload_id: UUID, request: Request) -> LargeUploadSessionResponse:
    return request.app.state.large_upload_service.abort(upload_id)


@router.post("/api/v1/media-sessions", response_model=MediaSessionResponse)
def create_media_session(
    body: CreateMediaSessionRequest,
    request: Request,
    response: Response,
) -> MediaSessionResponse:
    issued = request.app.state.media_stream_service.issue(
        body.revision_id,
        existing_token=request.cookies.get(MEDIA_SESSION_COOKIE),
    )
    response.set_cookie(
        key=MEDIA_SESSION_COOKIE,
        value=issued.token,
        max_age=request.app.state.settings.media_session_absolute_seconds,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/api/v1/media/",
    )
    return MediaSessionResponse(
        revision_id=issued.revision_id,
        idle_expires_at=issued.idle_expires_at,
        absolute_expires_at=issued.absolute_expires_at,
    )


@router.get("/api/v1/media/revisions/{revision_id}")
def stream_media_revision(revision_id: UUID, request: Request) -> Response:
    resource = request.app.state.media_stream_service.stream_resource(
        revision_id=revision_id,
        token=request.cookies.get(MEDIA_SESSION_COOKIE),
    )
    etag = f'"{resource.etag}"'
    if request.headers.get("if-none-match") == etag:
        resource.release()
        return Response(
            status_code=304,
            headers={
                "Accept-Ranges": "bytes",
                "Cache-Control": "private, no-store",
                "ETag": etag,
                "X-Content-Type-Options": "nosniff",
                "Cross-Origin-Resource-Policy": "same-origin",
                "Referrer-Policy": "no-referrer",
            },
        )
    try:
        byte_range = parse_single_range(request.headers.get("range"), resource.byte_size)
    except RangeNotSatisfiable:
        resource.release()
        return Response(
            status_code=416,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes */{resource.byte_size}",
            },
        )
    start = byte_range.start if byte_range is not None else 0
    length = byte_range.length if byte_range is not None else resource.byte_size
    end = start + length - 1
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "Content-Disposition": "inline",
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Referrer-Policy": "no-referrer",
        "Content-Length": str(length),
    }
    if byte_range is not None:
        headers["Content-Range"] = f"bytes {start}-{end}/{resource.byte_size}"
    try:
        content = open_range(
            resource.path,
            start=start,
            length=length,
            expected_size=resource.byte_size,
            trusted_root=resource.trusted_root,
            expected_identity=resource.expected_identity,
            lease=resource.lease,
        )
    except BaseException:
        resource.release()
        raise
    return _MediaStreamingResponse(
        content,
        resource=resource,
        status_code=206 if byte_range is not None else 200,
        media_type=resource.media_type,
        headers=headers,
    )


@router.post("/api/v1/external-root-grants/select", response_model=ExternalRootGrantResponse)
def select_external_root(request: Request) -> ExternalRootGrantResponse:
    result = request.app.state.external_reference_service.grant_root(
        request.app.state.native_media_picker.choose_directory()
    )
    return ExternalRootGrantResponse(grant_id=result.id, display_name=result.display_name)


@router.post(
    "/api/v1/external-root-grants/{grant_id}/references/select",
    response_model=ExternalReferenceResponse,
)
def select_external_reference(
    grant_id: UUID,
    body: CreateExternalReferenceFromPickerRequest,
    request: Request,
) -> ExternalReferenceResponse:
    grant = request.app.state.external_reference_service.get_grant(grant_id)
    selected_file = request.app.state.native_media_picker.choose_file(grant.canonical_path)
    relative_path = _relative_picker_selection(selected_file=selected_file, root=grant.canonical_path)
    result = request.app.state.external_reference_service.create_reference(
        project_id=body.project_id,
        grant_id=grant_id,
        relative_path=relative_path,
        requested_media_kind=body.requested_media_kind,
    )
    return ExternalReferenceResponse(
        revision_id=result.revision_id,
        artifact_id=result.artifact_id,
        health=result.health,
        storage_kind=result.storage_kind,
        snapshot_id=result.snapshot_id,
    )


@router.post(
    "/api/v1/artifact-revisions/{revision_id}/external-reconfirm",
    response_model=ExternalReferenceResponse,
)
def reconfirm_external_reference(
    revision_id: UUID,
    body: ReconfirmExternalReferenceFromPickerRequest,
    request: Request,
) -> ExternalReferenceResponse:
    grant = request.app.state.external_reference_service.get_grant(body.grant_id)
    selected_file = request.app.state.native_media_picker.choose_file(grant.canonical_path)
    result = request.app.state.external_reference_service.reconfirm(
        revision_id=revision_id,
        grant_id=body.grant_id,
        relative_path=_relative_picker_selection(selected_file=selected_file, root=grant.canonical_path),
    )
    return ExternalReferenceResponse(
        revision_id=result.revision_id,
        artifact_id=result.artifact_id,
        health=result.health,
        storage_kind=result.storage_kind,
        snapshot_id=result.snapshot_id,
    )


@router.post(
    "/api/v1/artifact-revisions/{revision_id}/external-verify",
    response_model=ExternalReferenceResponse,
)
def verify_external_reference(revision_id: UUID, request: Request) -> ExternalReferenceResponse:
    result = request.app.state.external_reference_service.verify(revision_id)
    return ExternalReferenceResponse(
        revision_id=result.revision_id,
        artifact_id=result.artifact_id,
        health=result.health,
        storage_kind=result.storage_kind,
        snapshot_id=result.snapshot_id,
    )


@router.post(
    "/api/v1/artifact-revisions/{revision_id}/external-import-copy",
    response_model=ManagedCopyResponse,
)
def import_external_copy(revision_id: UUID, request: Request) -> ManagedCopyResponse:
    result = request.app.state.external_reference_service.import_copy(revision_id)
    return ManagedCopyResponse(
        revision_id=result.revision_id,
        artifact_id=result.artifact_id,
        storage_kind=result.storage_kind,
    )


@router.post(
    "/api/v1/artifact-revisions/{revision_id}/renditions",
    response_model=RenditionJobResponse,
)
def request_rendition(
    revision_id: UUID,
    body: RequestRenditionRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> RenditionJobResponse:
    result = request.app.state.transcode_job_service.request(
        revision_id,
        profile=body.profile,
        confirmation_token=body.confirmation_token,
    )
    if not result.reused:
        background_tasks.add_task(request.app.state.maintenance_job_coordinator.process_once)
    return RenditionJobResponse(
        job_id=result.job_id,
        rendition_id=result.rendition_id,
        profile=result.profile.name,
        width=result.profile.width,
        height=result.profile.height,
        frames_per_second=result.profile.frames_per_second,
        estimated_output_bytes=result.profile.estimated_output_bytes,
        reused=result.reused,
    )


@router.delete("/api/v1/renditions/{rendition_id}", response_model=dict[str, bool])
def remove_rendition(rendition_id: UUID, request: Request) -> dict[str, bool]:
    return {
        "removed": request.app.state.transcode_job_service.remove_rendition(rendition_id)
    }


@router.get("/api/v1/storage/summary", response_model=StorageSummaryResponse)
def storage_summary(request: Request) -> StorageSummaryResponse:
    summary = request.app.state.storage_lifecycle_service.storage_summary()
    return StorageSummaryResponse(
        workspace_quota_bytes=summary.workspace_quota_bytes,
        managed_blob_bytes=summary.managed_blob_bytes,
        staging_bytes=summary.staging_bytes,
        quarantine_bytes=summary.quarantine_bytes,
        active_reservation_bytes=summary.active_reservation_bytes,
        physical_free_bytes=summary.physical_free_bytes,
    )


@router.post("/api/v1/gc-plans", response_model=GcPlanResponse)
def create_gc_plan(request: Request) -> GcPlanResponse:
    result = request.app.state.storage_lifecycle_service.create_gc_plan()
    return GcPlanResponse(
        plan_id=result.id,
        summary_sha256=result.summary_sha256,
        entries=[
            GcPlanEntryResponse(
                entry_id=entry.id,
                target_id=entry.target_id,
                reason=entry.reason,
                logical_reclaim_bytes=entry.logical_reclaim_bytes,
                physical_reclaim_bytes=entry.physical_reclaim_bytes,
            )
            for entry in result.entries
        ],
    )


@router.post("/api/v1/gc-plans/{plan_id}/confirm", response_model=list[QuarantineEntryResponse])
def confirm_gc_plan(
    plan_id: UUID,
    body: ConfirmGcPlanRequest,
    request: Request,
) -> list[QuarantineEntryResponse]:
    result = request.app.state.storage_lifecycle_service.confirm_gc_plan(
        str(plan_id),
        body.summary_sha256,
    )
    return [
        QuarantineEntryResponse(quarantine_id=entry.id, target_id=entry.target_id)
        for entry in result
    ]


@router.post("/api/v1/quarantine/{quarantine_id}/restore", response_model=dict[str, bool])
def restore_quarantine(quarantine_id: UUID, request: Request) -> dict[str, bool]:
    return {"restored": request.app.state.storage_lifecycle_service.restore_quarantine(str(quarantine_id))}
