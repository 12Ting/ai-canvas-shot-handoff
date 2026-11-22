from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ai_canvas.api.errors import register_error_handlers
from ai_canvas.api.handoff_playback import PlaybackAccess
from ai_canvas.api.routes.artifacts import router as artifacts_router
from ai_canvas.api.routes.agent_proposals import router as agent_proposals_router
from ai_canvas.api.routes.branches import router as branches_router
from ai_canvas.api.routes.control_config import router as control_config_router
from ai_canvas.api.routes.experience_events import router as experience_events_router
from ai_canvas.api.routes.graphs import router as graphs_router
from ai_canvas.api.routes.handoff import router as handoff_router
from ai_canvas.api.routes.run_control import router as run_control_router
from ai_canvas.api.routes.graph_changesets import router as graph_changesets_router
from ai_canvas.api.routes.media_lifecycle import router as media_lifecycle_router
from ai_canvas.api.routes.model_profiles import router as model_profiles_router
from ai_canvas.api.routes.projects import router as projects_router
from ai_canvas.api.routes.promotions import router as promotions_router
from ai_canvas.api.routes.prompt_profile_extractions import (
    router as prompt_profile_extractions_router,
)
from ai_canvas.api.routes.prompt_batches import router as prompt_batches_router
from ai_canvas.api.routes.resources import router as resources_router
from ai_canvas.api.routes.session import router as session_router
from ai_canvas.api.security import (
    RequestBodyTooLarge,
    SessionManager,
    bearer_token,
    cache_bounded_request_body,
    is_artifact_content_upload,
    is_graph_changeset_body_limited,
    is_f1_control_body_limited,
    is_large_upload_part_upload,
    is_media_stream_request,
    is_resource_metadata_body_limited,
)
from ai_canvas.application.artifact_service import ArtifactService
from ai_canvas.application.agent_proposal_service import AgentProposalService
from ai_canvas.application.branch_service import BranchService
from ai_canvas.application.external_reference_service import ExternalReferenceService
from ai_canvas.application.graph_service import GraphService
from ai_canvas.application.handoff_service import HandoffService
from ai_canvas.application.graph_changeset_service import GraphChangeSetService
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.control_config_service import ControlConfigService
from ai_canvas.application.large_upload_service import LargeUploadService
from ai_canvas.application.maintenance_job_coordinator import MaintenanceJobCoordinator
from ai_canvas.application.media_stream_service import MediaStreamService
from ai_canvas.application.model_profile_service import ModelProfileService
from ai_canvas.application.project_service import ProjectService
from ai_canvas.application.promotion_service import PromotionService
from ai_canvas.application.prompt_profile_extraction_service import (
    PromptProfileExtractionService,
)
from ai_canvas.application.prompt_batch_service import PromptBatchService
from ai_canvas.application.run_control_service import RunControlService
from ai_canvas.application.resource_catalog_service import ResourceCatalogService
from ai_canvas.application.storage_lifecycle_service import StorageLifecycleService
from ai_canvas.application.transcode_job_service import TranscodeJobService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.config import AuthMode, RuntimeProfile, Settings
from ai_canvas.contracts.problems import RequestBodyTooLargeProblem
from ai_canvas.infrastructure.db.session import (
    create_sqlite_engine,
    make_session_factory,
    run_migrations,
)
from ai_canvas.infrastructure.db.branch_repository import BranchRepository
from ai_canvas.infrastructure.artifacts import ArtifactStore
from ai_canvas.infrastructure.media.native_picker import TkNativeMediaPicker
from ai_canvas.infrastructure.process_lock import DataDirectoryLock


def secure_headers(response: Response) -> Response:
    if "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
        "object-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()
    if (
        resolved.runtime_profile is RuntimeProfile.RELEASE
        and resolved.auth_mode is AuthMode.DEVELOPMENT_AUTO_SESSION
    ):
        raise ValueError(
            "development-auto-session is not allowed in the release runtime profile"
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        data_lock = DataDirectoryLock(resolved.data_dir)
        data_lock.acquire()
        engine = None
        try:
            ArtifactStore(
                resolved.data_dir,
                max_bytes=resolved.media_single_file_max_bytes,
                chunk_size=resolved.artifact_upload_chunk_bytes,
            ).reconcile_promotion_candidates()
            database_path = resolved.data_dir / "canvas.sqlite3"
            run_migrations(database_path)
            engine = create_sqlite_engine(database_path)
            session_factory = make_session_factory(engine)
            write_gate = WriteGate()
            branch_repository = BranchRepository()
            app.state.engine = engine
            app.state.project_service = ProjectService(
                session_factory,
                branches=branch_repository,
                write_gate=write_gate,
            )
            app.state.graph_service = GraphService(
                session_factory,
                branches=branch_repository,
                write_gate=write_gate,
            )
            app.state.branch_service = BranchService(
                session_factory,
                app.state.graph_service,
                branches=branch_repository,
            )
            app.state.event_spine_service = EventSpineService(
                session_factory,
                write_gate=write_gate,
            )
            app.state.resource_catalog_service = ResourceCatalogService(
                session_factory,
                app.state.event_spine_service,
                write_gate=write_gate,
            )
            app.state.graph_changeset_service = GraphChangeSetService(
                session_factory,
                app.state.graph_service,
                app.state.event_spine_service,
                branches=branch_repository,
                write_gate=write_gate,
            )
            app.state.promotion_service = PromotionService(
                session_factory,
                app.state.graph_service,
                app.state.event_spine_service,
                branches=branch_repository,
                write_gate=write_gate,
            )
            app.state.control_config_service = ControlConfigService(
                session_factory,
                app.state.event_spine_service,
                write_gate=write_gate,
            )
            app.state.run_control_service = RunControlService(
                session_factory,
                app.state.event_spine_service,
                write_gate=write_gate,
            )
            app.state.model_profile_service = ModelProfileService(
                app.state.control_config_service
            )
            app.state.agent_proposal_service = AgentProposalService(
                session_factory,
                app.state.graph_service,
                app.state.run_control_service,
                app.state.graph_changeset_service,
                app.state.event_spine_service,
                canvas_agent_api_key=resolved.canvas_agent_api_key,
                write_gate=write_gate,
            )
            app.state.agent_proposal_service.reconcile_dispatching()
            app.state.prompt_profile_extraction_service = (
                PromptProfileExtractionService(
                    session_factory,
                    app.state.graph_service,
                    app.state.run_control_service,
                    app.state.resource_catalog_service,
                    app.state.event_spine_service,
                    prompt_editor_api_key=resolved.canvas_agent_api_key,
                    write_gate=write_gate,
                )
            )
            app.state.prompt_profile_extraction_service.reconcile_dispatching()
            app.state.prompt_batch_service = PromptBatchService(
                session_factory,
                app.state.graph_service,
                app.state.graph_changeset_service,
                app.state.event_spine_service,
                prompt_editor_api_key=resolved.canvas_agent_api_key,
                write_gate=write_gate,
            )
            app.state.prompt_batch_service.reconcile_dispatching()
            app.state.artifact_service = ArtifactService(
                session_factory,
                settings=resolved,
                write_gate=write_gate,
            )
            app.state.storage_lifecycle_service = StorageLifecycleService(
                session_factory,
                settings=resolved,
                write_gate=write_gate,
            )
            # Complete or fail closed any persisted filesystem intent before
            # request handlers can observe a half-moved blob.
            app.state.storage_lifecycle_service.reconcile_lifecycle_operations()
            app.state.large_upload_service = LargeUploadService(
                session_factory,
                settings=resolved,
                storage=app.state.storage_lifecycle_service,
                write_gate=write_gate,
            )
            app.state.external_reference_service = ExternalReferenceService(
                session_factory,
                settings=resolved,
                storage=app.state.storage_lifecycle_service,
                write_gate=write_gate,
            )
            if not hasattr(app.state, "native_media_picker"):
                app.state.native_media_picker = TkNativeMediaPicker()
            app.state.handoff_service = HandoffService(
                session_factory, app.state.project_service, app.state.graph_service, write_gate,
                resolved, app.state.native_media_picker,
            )
            app.state.media_stream_service = MediaStreamService(
                session_factory,
                settings=resolved,
                write_gate=write_gate,
                external_references=app.state.external_reference_service,
            )
            app.state.media_stream_service.expire_sessions_after_startup()
            app.state.media_stream_service.start_cleanup()
            app.state.transcode_job_service = TranscodeJobService(
                session_factory,
                settings=resolved,
                external_references=app.state.external_reference_service,
                storage=app.state.storage_lifecycle_service,
                write_gate=write_gate,
            )
            app.state.maintenance_job_coordinator = MaintenanceJobCoordinator(
                session_factory,
                handlers={"TRANSCODE": app.state.transcode_job_service.execute},
                write_gate=write_gate,
            )
            app.state.artifact_service.recover_startup()
            app.state.large_upload_service.expire_sessions_after_startup()
            app.state.maintenance_job_coordinator.recover_once()
            app.state.storage_lifecycle_service.purge_due()
            app.state.handoff_service.start()
            yield
        finally:
            try:
                handoff = getattr(app.state, "handoff_service", None)
                if handoff is not None:
                    handoff.close()
                media_stream_service = getattr(app.state, "media_stream_service", None)
                if media_stream_service is not None:
                    media_stream_service.close()
                if engine is not None:
                    engine.dispose()
            finally:
                data_lock.release()

    app = FastAPI(
        title="AI Canvas Core",
        version="0.1.0",
        strict_content_type=True,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.handoff_playback = PlaybackAccess()
    app.state.session_manager = SessionManager(resolved.bootstrap_token)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=resolved.allowed_hosts)

    @app.middleware("http")
    async def local_security(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if not path.startswith("/api/"):
            return secure_headers(await call_next(request))
        allowed_origins = {resolved.public_origin, *resolved.development_origins}
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            return secure_headers(JSONResponse(status_code=403, content={"code": "origin_denied"}))
        is_mutation = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        if is_mutation and origin is None:
            return secure_headers(JSONResponse(status_code=403, content={"code": "origin_required"}))
        if is_mutation:
            media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if is_artifact_content_upload(request.method, path) or is_large_upload_part_upload(
                request.method, path
            ):
                if media_type != "application/octet-stream":
                    return secure_headers(
                        JSONResponse(
                            status_code=415,
                            content={"code": "octet_stream_required"},
                        )
                    )
            elif path != "/api/v1/session/exchange" and media_type != "application/json":
                return secure_headers(JSONResponse(status_code=415, content={"code": "json_required"}))
        if path not in {
            "/api/v1/health",
            "/api/v1/session/exchange",
            "/api/v1/session/development",
        } and not is_media_stream_request(request.method, path) and not app.state.handoff_playback.allows(request):
            try:
                app.state.session_manager.authenticate(bearer_token(request))
            except HTTPException as error:
                return secure_headers(
                    JSONResponse(status_code=error.status_code, content={"code": "unauthorized"})
                )
        if (is_mutation and (path == "/api/v1/handoff/projects" or "/handoff/" in path)) or is_graph_changeset_body_limited(
            request.method, path
        ) or is_f1_control_body_limited(request.method, path):
            try:
                await cache_bounded_request_body(
                    request,
                    resolved.graph_revision_body_limit_bytes,
                )
            except RequestBodyTooLarge as error:
                problem = RequestBodyTooLargeProblem(max_bytes=error.max_bytes)
                return secure_headers(
                    JSONResponse(
                        status_code=413,
                        content=problem.model_dump(mode="json"),
                    )
                )
        elif is_resource_metadata_body_limited(request.method, path):
            try:
                await cache_bounded_request_body(
                    request,
                    resolved.resource_metadata_body_limit_bytes,
                )
            except RequestBodyTooLarge as error:
                problem = RequestBodyTooLargeProblem(max_bytes=error.max_bytes)
                return secure_headers(
                    JSONResponse(
                        status_code=413,
                        content=problem.model_dump(mode="json"),
                    )
                )
        elif is_large_upload_part_upload(request.method, path):
            try:
                await cache_bounded_request_body(
                    request,
                    resolved.media_large_upload_part_bytes,
                )
            except RequestBodyTooLarge as error:
                problem = RequestBodyTooLargeProblem(max_bytes=error.max_bytes)
                return secure_headers(
                    JSONResponse(
                        status_code=413,
                        content=problem.model_dump(mode="json"),
                    )
                )
        return secure_headers(await call_next(request))

    @app.get("/api/v1/health")
    def health(response: Response) -> dict[str, str]:
        if resolved.instance_id is not None:
            response.headers["X-AI-Canvas-Instance"] = resolved.instance_id
        return {"name": "ai-canvas-core", "version": "0.1.0"}

    register_error_handlers(app)
    app.include_router(session_router)
    app.include_router(projects_router)
    app.include_router(handoff_router)
    app.include_router(branches_router)
    app.include_router(promotions_router)
    app.include_router(resources_router)
    app.include_router(graphs_router)
    app.include_router(graph_changesets_router)
    app.include_router(experience_events_router)
    app.include_router(control_config_router)
    app.include_router(model_profiles_router)
    app.include_router(run_control_router)
    app.include_router(agent_proposals_router)
    app.include_router(prompt_profile_extractions_router)
    app.include_router(prompt_batches_router)
    app.include_router(media_lifecycle_router)
    app.include_router(artifacts_router)

    if resolved.web_dist is not None:
        index_path = resolved.web_dist / "index.html"
        if not index_path.is_file():
            raise RuntimeError(f"AI Canvas web build is missing: {index_path}")
        app.mount("/", StaticFiles(directory=resolved.web_dist, html=True), name="web")

    return app


app = create_app()
