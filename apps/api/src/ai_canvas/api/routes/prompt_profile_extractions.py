from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict

from ai_canvas.api.security import F1Capability, require_f1_capability
from ai_canvas.contracts.problems import RequestBodyTooLargeProblem
from ai_canvas.contracts.prompt_profile_extractions import (
    CreatePromptProfileExtractionRequest,
    MaterializePromptProfileRequest,
    PromptProfileExtractionResponse,
)
from ai_canvas.contracts.run_control import RunRequestApprovalRequest


class ExecutePromptProfileExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["execute_prompt_profile_extraction"] = (
        "execute_prompt_profile_extraction"
    )


router = APIRouter(
    prefix="/api/v1/projects/{project_id}/prompt-profile-extractions",
    tags=["prompt-profile-extractions"],
)


@router.post("", response_model=PromptProfileExtractionResponse, status_code=status.HTTP_201_CREATED,
    responses={413: {"model": RequestBodyTooLargeProblem}})
def create_extraction(
    project_id: UUID,
    body: CreatePromptProfileExtractionRequest,
    request: Request,
) -> PromptProfileExtractionResponse:
    actor = require_f1_capability(request, F1Capability.AGENT_REQUEST)
    return request.app.state.prompt_profile_extraction_service.request(
        project_id, body, actor_id=actor.actor_id
    )


@router.get("/{extraction_id}", response_model=PromptProfileExtractionResponse)
def get_extraction(
    project_id: UUID, extraction_id: UUID, request: Request
) -> PromptProfileExtractionResponse:
    require_f1_capability(request, F1Capability.AGENT_REQUEST)
    return request.app.state.prompt_profile_extraction_service.get(
        project_id, extraction_id
    )


@router.post("/{extraction_id}/approval-decisions", response_model=PromptProfileExtractionResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}})
def approve_extraction(
    project_id: UUID,
    extraction_id: UUID,
    body: RunRequestApprovalRequest,
    request: Request,
) -> PromptProfileExtractionResponse:
    actor = require_f1_capability(request, F1Capability.AGENT_APPROVE)
    return request.app.state.prompt_profile_extraction_service.approve(
        project_id,
        extraction_id,
        expected_request_sha256=body.expected_request_sha256,
        decision=body.decision,
        actor_id=actor.actor_id,
    )


@router.post("/{extraction_id}/execute", response_model=PromptProfileExtractionResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}})
def execute_extraction(
    project_id: UUID,
    extraction_id: UUID,
    body: ExecutePromptProfileExtractionRequest,
    request: Request,
) -> PromptProfileExtractionResponse:
    del body
    actor = require_f1_capability(request, F1Capability.AGENT_EXECUTE)
    return request.app.state.prompt_profile_extraction_service.execute(
        project_id, extraction_id, actor_id=actor.actor_id
    )


@router.post("/{extraction_id}/materialize", response_model=PromptProfileExtractionResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}})
def materialize_extraction(
    project_id: UUID,
    extraction_id: UUID,
    body: MaterializePromptProfileRequest,
    request: Request,
) -> PromptProfileExtractionResponse:
    actor = require_f1_capability(request, F1Capability.RESOURCE_WRITE)
    return request.app.state.prompt_profile_extraction_service.materialize(
        project_id, extraction_id, body, actor_id=actor.actor_id
    )
