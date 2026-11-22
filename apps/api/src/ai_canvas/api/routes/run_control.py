from uuid import UUID

from fastapi import APIRouter, Request, status

from ai_canvas.api.security import F1Capability, require_f1_capability
from ai_canvas.contracts.run_control import (
    CreateRunRequest,
    DryRunReceiptResponse,
    DryRunRequest,
    RunAuthorizationResponse,
    RunRequestApprovalRequest,
    RunRequestResponse,
)
from ai_canvas.contracts.problems import RequestBodyTooLargeProblem


router = APIRouter(
    prefix="/api/v1/projects/{project_id}/run-requests",
    tags=["run-control"],
)


@router.post(
    "",
    response_model=RunRequestResponse,
    status_code=status.HTTP_201_CREATED,
    responses={413: {"model": RequestBodyTooLargeProblem}},
)
def request_no_egress_run(
    project_id: UUID,
    body: CreateRunRequest,
    request: Request,
) -> RunRequestResponse:
    actor = require_f1_capability(request, F1Capability.RUN_REQUEST)
    return request.app.state.run_control_service.request(
        project_id,
        body,
        actor_id=actor.actor_id,
        actor_kind=actor.actor_kind,
    )


@router.post(
    "/{request_id}/approval-decisions",
    response_model=RunAuthorizationResponse | RunRequestResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}},
)
def approve_no_egress_run(
    project_id: UUID,
    request_id: UUID,
    body: RunRequestApprovalRequest,
    request: Request,
) -> RunAuthorizationResponse | RunRequestResponse:
    actor = require_f1_capability(request, F1Capability.RUN_APPROVE)
    return request.app.state.run_control_service.approve(
        project_id,
        request_id,
        expected_request_sha256=body.expected_request_sha256,
        actor_id=actor.actor_id,
        decision=body.decision,
    )


@router.post(
    "/{request_id}/dry-run",
    response_model=DryRunReceiptResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}},
)
def execute_no_egress_dry_run(
    project_id: UUID,
    request_id: UUID,
    body: DryRunRequest,
    request: Request,
) -> DryRunReceiptResponse:
    del body
    actor = require_f1_capability(request, F1Capability.RUN_DRY_RUN)
    return request.app.state.run_control_service.dry_run(
        project_id,
        request_id,
        actor_id=actor.actor_id,
    )
