from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict

from ai_canvas.api.security import F1Capability, require_f1_capability
from ai_canvas.contracts.problems import RequestBodyTooLargeProblem
from ai_canvas.contracts.prompt_batches import (
    CreatePromptBatchRequest,
    ExecutePromptBatchItemRequest,
    PromptBatchApprovalRequest,
    PromptBatchAcceptAllRequest,
    PromptBatchFinalizeRequest,
    PromptBatchItemDecisionRequest,
    PromptBatchPreflightResponse,
    PromptBatchResponse,
    PromptBatchRetryRequest,
)


class ExecutePromptBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["execute_prompt_batch_item"] = "execute_prompt_batch_item"


router = APIRouter(
    prefix="/api/v1/projects/{project_id}/prompt-batches",
    tags=["prompt-batches"],
)


@router.post("/preflight", response_model=PromptBatchPreflightResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}})
def preflight(project_id: UUID, body: CreatePromptBatchRequest, request: Request) -> PromptBatchPreflightResponse:
    require_f1_capability(request, F1Capability.AGENT_REQUEST)
    return request.app.state.prompt_batch_service.preflight(project_id, body)


@router.post("", response_model=PromptBatchResponse, status_code=status.HTTP_201_CREATED,
    responses={413: {"model": RequestBodyTooLargeProblem}})
def create_batch(project_id: UUID, body: CreatePromptBatchRequest, request: Request) -> PromptBatchResponse:
    actor = require_f1_capability(request, F1Capability.AGENT_REQUEST)
    return request.app.state.prompt_batch_service.request(project_id, body, actor_id=actor.actor_id)


@router.get("/{batch_id}", response_model=PromptBatchResponse)
def get_batch(project_id: UUID, batch_id: UUID, request: Request) -> PromptBatchResponse:
    require_f1_capability(request, F1Capability.AGENT_REQUEST)
    return request.app.state.prompt_batch_service.get(project_id, batch_id)


@router.post("/{batch_id}/approval", response_model=PromptBatchResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}})
def approve_batch(project_id: UUID, batch_id: UUID, body: PromptBatchApprovalRequest,
    request: Request) -> PromptBatchResponse:
    actor = require_f1_capability(request, F1Capability.AGENT_APPROVE)
    return request.app.state.prompt_batch_service.approve(project_id, batch_id, body, actor_id=actor.actor_id)


@router.post("/{batch_id}/items/{item_id}/attempts/{attempt_id}/execute", response_model=PromptBatchResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}})
def execute_item(project_id: UUID, batch_id: UUID, item_id: UUID, attempt_id: UUID,
    body: ExecutePromptBatchItemRequest, request: Request) -> PromptBatchResponse:
    actor = require_f1_capability(request, F1Capability.AGENT_EXECUTE)
    return request.app.state.prompt_batch_service.execute_item(
        project_id, batch_id, item_id, attempt_id, body, actor_id=actor.actor_id)


@router.post("/{batch_id}/items/{item_id}/decisions", response_model=PromptBatchResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}})
def decide_item(project_id: UUID, batch_id: UUID, item_id: UUID,
    body: PromptBatchItemDecisionRequest, request: Request) -> PromptBatchResponse:
    actor = require_f1_capability(request, F1Capability.AGENT_APPROVE)
    return request.app.state.prompt_batch_service.decide_item(
        project_id, batch_id, item_id, body, actor_id=actor.actor_id)


@router.post("/{batch_id}/decisions:accept-all-succeeded", response_model=PromptBatchResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}})
def accept_all_succeeded(project_id: UUID, batch_id: UUID,
    body: PromptBatchAcceptAllRequest, request: Request) -> PromptBatchResponse:
    actor = require_f1_capability(request, F1Capability.AGENT_APPROVE)
    return request.app.state.prompt_batch_service.accept_all_succeeded(
        project_id, batch_id, body, actor_id=actor.actor_id)


@router.post("/{batch_id}/retries", response_model=PromptBatchResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}})
def retry_failed(project_id: UUID, batch_id: UUID, body: PromptBatchRetryRequest,
    request: Request) -> PromptBatchResponse:
    actor = require_f1_capability(request, F1Capability.AGENT_REQUEST)
    return request.app.state.prompt_batch_service.retry_failed(
        project_id, batch_id, body, actor_id=actor.actor_id)


@router.post("/{batch_id}/retry-approval", response_model=PromptBatchResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}})
def approve_retry(project_id: UUID, batch_id: UUID, body: PromptBatchApprovalRequest,
    request: Request) -> PromptBatchResponse:
    actor = require_f1_capability(request, F1Capability.AGENT_APPROVE)
    return request.app.state.prompt_batch_service.approve_retry(
        project_id, batch_id, body, actor_id=actor.actor_id)


@router.post("/{batch_id}/finalize", response_model=PromptBatchResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}})
def finalize(project_id: UUID, batch_id: UUID, body: PromptBatchFinalizeRequest,
    request: Request) -> PromptBatchResponse:
    actor = require_f1_capability(request, F1Capability.GRAPH_PROPOSE)
    return request.app.state.prompt_batch_service.finalize(project_id, batch_id, body, actor_id=actor.actor_id)
