from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ai_canvas.api.security import F1Capability, F1CapabilityForbidden
from ai_canvas.application.run_control_service import (
    DryRunValidationFailed,
    DryRunTaskLimitExceeded,
    EgressContentCategoryPolicyError,
    EgressNotAllowedInF1,
    RunAuthorizationConsumed,
    RunAuthorizationExpired,
    RunAuthorizationInvalidated,
    RunRequestNotFound,
)
from ai_canvas.application.agent_proposal_service import (
    AgentProposalNotFound,
    AgentProposalStateConflict,
    ModelProfileUnavailable,
)
from ai_canvas.application.prompt_profile_extraction_service import (
    PromptProfileExtractionNotFound,
    PromptProfileExtractionStateConflict,
)
from ai_canvas.application.prompt_batch_service import (
    PromptBatchIntegrityError,
    PromptBatchNotFound,
    PromptBatchPreflightError,
    PromptBatchStaleSubject,
    PromptBatchStateConflict,
)
from ai_canvas.application.event_spine_service import (
    EventCursorAckOutOfRange,
    EventTypeFilterInvalid,
)
from ai_canvas.application.errors import (
    ApprovalRequired,
    ApprovalSubjectIntegrityError,
    ApprovalSubjectStale,
    ArtifactImportConflict,
    ArtifactImportFailed,
    ArtifactImportNotFound,
    ArtifactRevisionNotFound,
    BranchHeadStale,
    ChangeSetDomainMismatch,
    ClientMutationReuse,
    ConfigChangeSetBaseRevisionStale,
    ConfigChangeSetNotFound,
    GraphChangeSetBaseRevisionStale,
    GraphChangeSetNotFound,
    GraphChangeSetUndoStale,
    GraphChangeSetUndoUnsupported,
    GraphBranchNotFound,
    GraphBranchNameConflict,
    GraphRevisionConflict,
    GraphRevisionNotFound,
    InvalidPromptDocument,
    InvalidResourceReference,
    MediaResourceUnavailable,
    MediaSessionUnauthorized,
    ProjectNotFound,
    PromotionNotFound,
    PromotionStale,
    PromotionUndoStale,
    ResourceNotFound,
    ResourceRevisionConflict,
    ResourceRevisionNotFound,
    SelectionPointerStale,
    ShotNotFound,
    ShotSlotAssociationCursorInvalid,
    StaleImpactConfirmationRequired,
    StoredGraphIntegrityError,
)
from ai_canvas.application.large_upload_service import (
    LargeUploadIncomplete,
    LargeUploadNotFound,
    LargeUploadPartConflict,
    LargeUploadProtocolError,
)
from ai_canvas.application.storage_lifecycle_service import (
    GcPlanNotFound,
    GcPlanStale,
    StorageCapacityExceeded,
)
from ai_canvas.infrastructure.db.blob_lifecycle_repository import BlobLifecycleConflict
from ai_canvas.application.transcode_job_service import TranscodeConfirmationRequired
from ai_canvas.contracts.problems import (
    ArtifactImportConflictProblem,
    ArtifactImportFailedProblem,
    ArtifactImportNotFoundProblem,
    ArtifactRevisionNotFoundProblem,
    BranchHeadStaleProblem,
    GraphBranchNotFoundProblem,
    GraphBranchNameConflictProblem,
    GraphRevisionNotFoundProblem,
    InvalidPromptDocumentProblem,
    InvalidGraphProblem,
    InvalidResourceReferenceProblem,
    ProjectNotFoundProblem,
    PromotionNotFoundProblem,
    RequestValidationProblem,
    ResourceNotFoundProblem,
    ResourceRevisionConflictProblem,
    ResourceRevisionNotFoundProblem,
    SelectionPointerStaleProblem,
    ShotNotFoundProblem,
    ShotSlotAssociationCursorInvalidProblem,
    StaleImpactConfirmationRequiredProblem,
    StoredGraphIntegrityProblem,
)
from ai_canvas.domain.graph.validation import GraphValidationError
from ai_canvas.domain.agent.scope import AgentScopeError
from ai_canvas.domain.model_endpoints.policy import EndpointPolicyError
from ai_canvas.infrastructure.model_providers.credentials import CredentialUnavailable
from ai_canvas.domain.media.external_reference import ExternalRootUnavailable, ExternalRootViolation


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PromptBatchIntegrityError)
    async def prompt_batch_integrity_error(
        _request: Request, _error: PromptBatchIntegrityError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "prompt_batch_integrity_error"},
        )

    @app.exception_handler(PromptBatchStaleSubject)
    async def prompt_batch_stale_subject(
        _request: Request, _error: PromptBatchStaleSubject
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "prompt_batch_stale_subject"},
        )

    @app.exception_handler(EgressContentCategoryPolicyError)
    async def egress_content_category_policy_error(
        _request: Request, _error: EgressContentCategoryPolicyError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"code": "egress_content_category_not_allowed"},
        )

    @app.exception_handler(PromptBatchNotFound)
    async def prompt_batch_not_found(
        _request: Request, _error: PromptBatchNotFound
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"code": "prompt_batch_not_found"},
        )

    @app.exception_handler(PromptBatchStateConflict)
    async def prompt_batch_state_conflict(
        _request: Request, _error: PromptBatchStateConflict
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "prompt_batch_state_conflict"},
        )

    @app.exception_handler(PromptBatchPreflightError)
    async def prompt_batch_preflight_error(
        _request: Request, error: PromptBatchPreflightError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "code": "prompt_batch_preflight_failed",
                "blockers": [
                    {"shot_id": str(item.shot_id), "code": item.code}
                    for item in error.blockers
                ],
            },
        )

    @app.exception_handler(PromptProfileExtractionNotFound)
    async def prompt_profile_extraction_not_found(
        _request: Request, _error: PromptProfileExtractionNotFound
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"code": "prompt_profile_extraction_not_found"},
        )

    @app.exception_handler(PromptProfileExtractionStateConflict)
    async def prompt_profile_extraction_state_conflict(
        _request: Request, _error: PromptProfileExtractionStateConflict
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "prompt_profile_extraction_state_conflict"},
        )

    @app.exception_handler(AgentProposalNotFound)
    async def agent_proposal_not_found(
        _request: Request, _error: AgentProposalNotFound
    ) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"code": "agent_proposal_not_found"})

    @app.exception_handler(AgentProposalStateConflict)
    async def agent_proposal_state_conflict(
        _request: Request, _error: AgentProposalStateConflict
    ) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"code": "agent_proposal_state_conflict"})

    @app.exception_handler(ModelProfileUnavailable)
    async def model_profile_unavailable(
        _request: Request, _error: ModelProfileUnavailable
    ) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"code": "model_profile_unavailable"})

    @app.exception_handler(CredentialUnavailable)
    async def credential_unavailable(
        _request: Request, _error: CredentialUnavailable
    ) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"code": "model_credential_unavailable"})

    @app.exception_handler(EndpointPolicyError)
    async def endpoint_policy_invalid(
        _request: Request, _error: EndpointPolicyError
    ) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"code": "model_endpoint_policy_invalid"})

    @app.exception_handler(AgentScopeError)
    async def agent_scope_invalid(
        _request: Request, _error: AgentScopeError
    ) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"code": "agent_scope_invalid"})

    @app.exception_handler(SelectionPointerStale)
    async def selection_pointer_stale(
        _request: Request,
        error: SelectionPointerStale,
    ) -> JSONResponse:
        problem = SelectionPointerStaleProblem(
            current_branch_id=error.current_branch_id,
            current_revision_id=error.current_revision_id,
            current_version=error.current_version,
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(BranchHeadStale)
    async def branch_head_stale(
        _request: Request,
        error: BranchHeadStale,
    ) -> JSONResponse:
        problem = BranchHeadStaleProblem(
            branch_id=error.branch_id,
            current_revision_id=error.current_revision_id,
            current_version=error.current_version,
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(GraphChangeSetUndoUnsupported)
    async def graph_changeset_undo_unsupported(
        _request: Request,
        _error: GraphChangeSetUndoUnsupported,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "graph_changeset_undo_unsupported"},
        )

    @app.exception_handler(GraphChangeSetUndoStale)
    async def graph_changeset_undo_stale(
        _request: Request,
        _error: GraphChangeSetUndoStale,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "graph_changeset_undo_stale"},
        )

    @app.exception_handler(PromotionUndoStale)
    async def promotion_undo_stale(
        _request: Request,
        _error: PromotionUndoStale,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "promotion_undo_stale"},
        )

    @app.exception_handler(PromotionStale)
    async def promotion_stale(
        _request: Request,
        _error: PromotionStale,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "promotion_stale"},
        )

    @app.exception_handler(StaleImpactConfirmationRequired)
    async def stale_impact_confirmation_required(
        _request: Request,
        error: StaleImpactConfirmationRequired,
    ) -> JSONResponse:
        problem = StaleImpactConfirmationRequiredProblem(
            impact_sha256=error.impact_sha256
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(GraphBranchNameConflict)
    async def graph_branch_name_conflict(
        _request: Request,
        error: GraphBranchNameConflict,
    ) -> JSONResponse:
        problem = GraphBranchNameConflictProblem(
            normalized_name=error.normalized_name
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(PromotionNotFound)
    async def promotion_not_found(
        _request: Request,
        error: PromotionNotFound,
    ) -> JSONResponse:
        problem = PromotionNotFoundProblem(promotion_id=error.promotion_id)
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(GraphBranchNotFound)
    async def graph_branch_not_found(
        _request: Request,
        error: GraphBranchNotFound,
    ) -> JSONResponse:
        problem = GraphBranchNotFoundProblem(branch_id=error.branch_id)
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(ResourceNotFound)
    async def resource_not_found(
        _request: Request,
        error: ResourceNotFound,
    ) -> JSONResponse:
        problem = ResourceNotFoundProblem(resource_id=error.resource_id)
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(ResourceRevisionNotFound)
    async def resource_revision_not_found(
        _request: Request,
        error: ResourceRevisionNotFound,
    ) -> JSONResponse:
        problem = ResourceRevisionNotFoundProblem(revision_id=error.revision_id)
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(ResourceRevisionConflict)
    async def resource_revision_conflict(
        _request: Request,
        error: ResourceRevisionConflict,
    ) -> JSONResponse:
        problem = ResourceRevisionConflictProblem(
            resource_id=error.resource_id,
            current_revision_id=error.current_revision_id,
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(InvalidResourceReference)
    async def invalid_resource_reference(
        _request: Request,
        error: InvalidResourceReference,
    ) -> JSONResponse:
        problem = InvalidResourceReferenceProblem(
            revision_id=error.revision_id,
            reason=error.reason,
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(InvalidPromptDocument)
    async def invalid_prompt_document(
        _request: Request,
        _error: InvalidPromptDocument,
    ) -> JSONResponse:
        problem = InvalidPromptDocumentProblem()
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(ApprovalSubjectStale)
    async def approval_subject_stale(
        _request: Request,
        _error: ApprovalSubjectStale,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "approval_subject_stale"},
        )

    @app.exception_handler(ApprovalRequired)
    async def approval_required(
        _request: Request,
        _error: ApprovalRequired,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "approval_required"},
        )

    @app.exception_handler(EventTypeFilterInvalid)
    async def event_type_filter_invalid(
        _request: Request,
        _error: EventTypeFilterInvalid,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"code": "event_type_filter_invalid"},
        )

    @app.exception_handler(ApprovalSubjectIntegrityError)
    async def approval_subject_integrity_error(
        _request: Request,
        _error: ApprovalSubjectIntegrityError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "approval_subject_integrity_mismatch"},
        )

    @app.exception_handler(ChangeSetDomainMismatch)
    async def changeset_domain_mismatch(
        _request: Request,
        _error: ChangeSetDomainMismatch,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "changeset_domain_mismatch"},
        )

    @app.exception_handler(GraphChangeSetBaseRevisionStale)
    async def graph_changeset_base_revision_stale(
        _request: Request,
        error: GraphChangeSetBaseRevisionStale,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": "graph_base_revision_stale",
                "current_revision_id": error.current_revision_id,
            },
        )

    @app.exception_handler(ConfigChangeSetBaseRevisionStale)
    async def config_changeset_base_revision_stale(
        _request: Request,
        _error: ConfigChangeSetBaseRevisionStale,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "config_base_revision_stale"},
        )

    @app.exception_handler(GraphChangeSetNotFound)
    async def graph_changeset_not_found(
        _request: Request,
        error: GraphChangeSetNotFound,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "code": "graph_changeset_not_found",
                "change_set_id": error.change_set_id,
            },
        )

    @app.exception_handler(ConfigChangeSetNotFound)
    async def config_changeset_not_found(
        _request: Request,
        error: ConfigChangeSetNotFound,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "code": "config_changeset_not_found",
                "change_set_id": error.change_set_id,
            },
        )

    @app.exception_handler(F1CapabilityForbidden)
    async def f1_capability_forbidden(
        _request: Request,
        error: F1CapabilityForbidden,
    ) -> JSONResponse:
        code = (
            "config_changeset_forbidden"
            if error.capability
            in {
                F1Capability.CONFIG_PROPOSE,
                F1Capability.CONFIG_APPROVE,
                F1Capability.CONFIG_APPLY,
            }
            else "f1_capability_forbidden"
        )
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"code": code},
        )

    @app.exception_handler(EgressNotAllowedInF1)
    async def egress_not_allowed_in_f1(
        _request: Request,
        _error: EgressNotAllowedInF1,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"code": "egress_not_allowed_in_f1"},
        )

    @app.exception_handler(DryRunTaskLimitExceeded)
    async def dry_run_task_limit_exceeded(
        _request: Request,
        _error: DryRunTaskLimitExceeded,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"code": "dry_run_task_limit_exceeded"},
        )

    @app.exception_handler(DryRunValidationFailed)
    async def dry_run_validation_failed(
        _request: Request,
        _error: DryRunValidationFailed,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"code": "dry_run_validation_failed"},
        )

    @app.exception_handler(RunRequestNotFound)
    async def run_request_not_found(
        _request: Request,
        error: RunRequestNotFound,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "code": "run_request_not_found",
                "request_id": str(error.request_id),
            },
        )

    @app.exception_handler(RunAuthorizationConsumed)
    async def run_authorization_consumed(
        _request: Request,
        _error: RunAuthorizationConsumed,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "run_authorization_consumed"},
        )

    @app.exception_handler(RunAuthorizationExpired)
    async def run_authorization_expired(
        _request: Request,
        _error: RunAuthorizationExpired,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "run_authorization_expired"},
        )

    @app.exception_handler(RunAuthorizationInvalidated)
    async def run_authorization_invalidated(
        _request: Request,
        _error: RunAuthorizationInvalidated,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "run_authorization_invalidated"},
        )

    @app.exception_handler(EventCursorAckOutOfRange)
    async def event_cursor_ack_out_of_range(
        _request: Request,
        _error: EventCursorAckOutOfRange,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "event_cursor_ack_out_of_range"},
        )

    @app.exception_handler(StorageCapacityExceeded)
    async def storage_capacity_exceeded(
        _request: Request,
        error: StorageCapacityExceeded,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": error.code.lower(),
                "required_physical_free_bytes": error.required_physical_free_bytes,
            },
        )

    @app.exception_handler(BlobLifecycleConflict)
    async def blob_lifecycle_conflict(
        _request: Request,
        error: BlobLifecycleConflict,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": error.code.lower(),
                "sha256": error.sha256,
                "state": error.state,
            },
        )

    @app.exception_handler(GcPlanNotFound)
    async def gc_plan_not_found(
        _request: Request,
        _error: GcPlanNotFound,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"code": "gc_plan_not_found"},
        )

    @app.exception_handler(GcPlanStale)
    async def gc_plan_stale(
        _request: Request,
        _error: GcPlanStale,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "gc_plan_stale"},
        )

    @app.exception_handler(ExternalRootViolation)
    async def external_root_violation(
        _request: Request,
        _error: ExternalRootViolation,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"code": "external_root_violation"},
        )

    @app.exception_handler(ExternalRootUnavailable)
    async def external_root_unavailable(
        _request: Request,
        _error: ExternalRootUnavailable,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "external_root_unavailable"},
        )

    @app.exception_handler(TranscodeConfirmationRequired)
    async def transcode_confirmation_required(
        _request: Request,
        error: TranscodeConfirmationRequired,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": "transcode_confirmation_required",
                "confirmation_token": error.confirmation_token,
                "profile": error.profile,
                "width": error.width,
                "height": error.height,
                "estimated_output_bytes": error.estimated_output_bytes,
            },
        )

    @app.exception_handler(LargeUploadNotFound)
    async def large_upload_not_found(
        _request: Request,
        error: LargeUploadNotFound,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"code": "large_upload_not_found", "upload_id": error.upload_id},
        )

    @app.exception_handler(LargeUploadProtocolError)
    async def large_upload_protocol_error(
        _request: Request,
        _error: LargeUploadProtocolError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"code": "large_upload_protocol_error"},
        )

    @app.exception_handler(LargeUploadPartConflict)
    async def large_upload_part_conflict(
        _request: Request,
        _error: LargeUploadPartConflict,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "large_upload_part_conflict"},
        )

    @app.exception_handler(LargeUploadIncomplete)
    async def large_upload_incomplete(
        _request: Request,
        _error: LargeUploadIncomplete,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "large_upload_incomplete"},
        )

    @app.exception_handler(ArtifactImportNotFound)
    async def artifact_import_not_found(
        _request: Request,
        error: ArtifactImportNotFound,
    ) -> JSONResponse:
        problem = ArtifactImportNotFoundProblem(import_id=error.import_id)
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=jsonable_encoder(problem))

    @app.exception_handler(MediaSessionUnauthorized)
    async def media_session_unauthorized(
        _request: Request,
        _error: MediaSessionUnauthorized,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"code": "media_session_unauthorized"},
        )

    @app.exception_handler(MediaResourceUnavailable)
    async def media_resource_unavailable(
        _request: Request,
        error: MediaResourceUnavailable,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": "media_resource_unavailable",
                "revision_id": error.revision_id,
                "health": error.health,
            },
        )

    @app.exception_handler(ArtifactRevisionNotFound)
    async def artifact_revision_not_found(
        _request: Request,
        error: ArtifactRevisionNotFound,
    ) -> JSONResponse:
        problem = ArtifactRevisionNotFoundProblem(revision_id=error.revision_id)
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=jsonable_encoder(problem))

    @app.exception_handler(ArtifactImportConflict)
    async def artifact_import_conflict(
        _request: Request,
        error: ArtifactImportConflict,
    ) -> JSONResponse:
        problem = ArtifactImportConflictProblem(import_id=error.import_id, reason=error.reason)
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=jsonable_encoder(problem))

    @app.exception_handler(ArtifactImportFailed)
    async def artifact_import_failed(
        _request: Request,
        error: ArtifactImportFailed,
    ) -> JSONResponse:
        problem = ArtifactImportFailedProblem(
            import_id=error.import_id,
            failure_code=error.failure_code,
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(StoredGraphIntegrityError)
    async def stored_graph_integrity(
        _request: Request,
        error: StoredGraphIntegrityError,
    ) -> JSONResponse:
        problem = StoredGraphIntegrityProblem(revision_id=error.revision_id)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(ShotNotFound)
    async def shot_not_found(
        _request: Request,
        error: ShotNotFound,
    ) -> JSONResponse:
        problem = ShotNotFoundProblem(shot_id=error.shot_id)
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(ShotSlotAssociationCursorInvalid)
    async def shot_slot_association_cursor_invalid(
        _request: Request,
        _error: ShotSlotAssociationCursorInvalid,
    ) -> JSONResponse:
        problem = ShotSlotAssociationCursorInvalidProblem()
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(GraphRevisionConflict)
    async def graph_revision_conflict(
        _request: Request,
        error: GraphRevisionConflict,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": "graph_revision_conflict",
                "current_revision_id": error.current_revision_id,
            },
        )

    @app.exception_handler(ClientMutationReuse)
    async def client_mutation_reuse(
        _request: Request,
        error: ClientMutationReuse,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": "client_mutation_reuse",
                "client_mutation_id": error.mutation_id,
            },
        )

    @app.exception_handler(GraphValidationError)
    async def invalid_graph(
        _request: Request,
        error: GraphValidationError,
    ) -> JSONResponse:
        problem = InvalidGraphProblem(message=str(error))
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation(
        _request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        problem = RequestValidationProblem(issues=jsonable_encoder(error.errors()))
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(ProjectNotFound)
    async def project_not_found(
        _request: Request,
        error: ProjectNotFound,
    ) -> JSONResponse:
        problem = ProjectNotFoundProblem(project_id=error.project_id)
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=jsonable_encoder(problem),
        )

    @app.exception_handler(GraphRevisionNotFound)
    async def graph_revision_not_found(
        _request: Request,
        error: GraphRevisionNotFound,
    ) -> JSONResponse:
        problem = GraphRevisionNotFoundProblem(revision_id=error.revision_id)
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=jsonable_encoder(problem),
        )
