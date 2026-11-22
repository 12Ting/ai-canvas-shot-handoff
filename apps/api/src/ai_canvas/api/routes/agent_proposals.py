from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict

from ai_canvas.api.security import F1Capability, require_f1_capability
from ai_canvas.contracts.agent_proposals import (
    AgentProposalResponse,
    CreateAgentProposalRequest,
)
from ai_canvas.contracts.run_control import RunRequestApprovalRequest
from ai_canvas.contracts.problems import RequestBodyTooLargeProblem


class ExecuteAgentProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["execute_agent_proposal"] = "execute_agent_proposal"


router = APIRouter(
    prefix="/api/v1/projects/{project_id}/agent-proposals",
    tags=["agent-proposals"],
)


@router.post(
    "",
    response_model=AgentProposalResponse,
    status_code=status.HTTP_201_CREATED,
    responses={413: {"model": RequestBodyTooLargeProblem}},
)
def request_agent_proposal(
    project_id: UUID,
    body: CreateAgentProposalRequest,
    request: Request,
) -> AgentProposalResponse:
    actor = require_f1_capability(request, F1Capability.AGENT_REQUEST)
    return request.app.state.agent_proposal_service.request(
        project_id,
        body,
        actor_id=actor.actor_id,
    )


@router.get("/{proposal_id}", response_model=AgentProposalResponse)
def get_agent_proposal(
    project_id: UUID,
    proposal_id: UUID,
    request: Request,
) -> AgentProposalResponse:
    require_f1_capability(request, F1Capability.AGENT_REQUEST)
    return request.app.state.agent_proposal_service.get(project_id, proposal_id)


@router.post(
    "/{proposal_id}/approval-decisions",
    response_model=AgentProposalResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}},
)
def approve_agent_proposal(
    project_id: UUID,
    proposal_id: UUID,
    body: RunRequestApprovalRequest,
    request: Request,
) -> AgentProposalResponse:
    actor = require_f1_capability(request, F1Capability.AGENT_APPROVE)
    return request.app.state.agent_proposal_service.approve(
        project_id,
        proposal_id,
        expected_request_sha256=body.expected_request_sha256,
        actor_id=actor.actor_id,
        decision=body.decision,
    )


@router.post(
    "/{proposal_id}/execute",
    response_model=AgentProposalResponse,
    responses={413: {"model": RequestBodyTooLargeProblem}},
)
def execute_agent_proposal(
    project_id: UUID,
    proposal_id: UUID,
    body: ExecuteAgentProposalRequest,
    request: Request,
) -> AgentProposalResponse:
    del body
    actor = require_f1_capability(request, F1Capability.AGENT_EXECUTE)
    return request.app.state.agent_proposal_service.execute(
        project_id,
        proposal_id,
        actor_id=actor.actor_id,
    )
