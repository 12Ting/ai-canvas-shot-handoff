from fastapi import APIRouter, Request, status
from uuid import UUID

from ai_canvas.api.security import F1Capability, require_f1_capability
from ai_canvas.contracts.config_changesets import (
    ConfigChangeSetApprovalRequest,
    ConfigChangeSetResponse,
    ControlConfigRevisionResponse,
    CreateConfigChangeSetRequest,
)
from ai_canvas.contracts.problems import RequestBodyTooLargeProblem


router = APIRouter(prefix="/api/v1/control", tags=["control-config"])


@router.get("/config-revision", response_model=ControlConfigRevisionResponse)
def get_active_control_config(request: Request) -> ControlConfigRevisionResponse:
    return request.app.state.control_config_service.get_active()


@router.post(
    "/config-change-sets",
    response_model=ConfigChangeSetResponse,
    status_code=status.HTTP_201_CREATED,
    responses={413: {"model": RequestBodyTooLargeProblem}},
)
def request_config_change_set(
    body: CreateConfigChangeSetRequest,
    request: Request,
) -> ConfigChangeSetResponse:
    actor = require_f1_capability(request, F1Capability.CONFIG_PROPOSE)
    return request.app.state.control_config_service.request(
        body,
        actor_id=actor.actor_id,
    )


@router.post(
    "/config-change-sets/{change_set_id}/approval-decisions",
    response_model=ConfigChangeSetResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}},
)
def approve_config_change_set(
    change_set_id: UUID,
    body: ConfigChangeSetApprovalRequest,
    request: Request,
) -> ConfigChangeSetResponse:
    actor = require_f1_capability(request, F1Capability.CONFIG_APPROVE)
    return request.app.state.control_config_service.approve(
        change_set_id,
        expected_changeset_sha256=body.expected_changeset_sha256,
        actor_id=actor.actor_id,
        decision=body.decision,
    )


@router.post(
    "/config-change-sets/{change_set_id}/apply",
    response_model=ConfigChangeSetResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}},
)
def apply_config_change_set(
    change_set_id: UUID,
    body: ConfigChangeSetApprovalRequest,
    request: Request,
) -> ConfigChangeSetResponse:
    actor = require_f1_capability(request, F1Capability.CONFIG_APPLY)
    return request.app.state.control_config_service.apply(
        change_set_id,
        expected_changeset_sha256=body.expected_changeset_sha256,
        actor_id=actor.actor_id,
    )
