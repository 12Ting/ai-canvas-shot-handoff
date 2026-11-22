from uuid import UUID

from fastapi import APIRouter, Request

from ai_canvas.api.security import F1Capability, require_f1_capability
from ai_canvas.contracts.model_profiles import (
    ModelProfileListResponse,
    ModelProfileRevisionListResponse,
)


router = APIRouter(prefix="/api/v1/control/model-profiles", tags=["model-profiles"])


@router.get("", response_model=ModelProfileListResponse)
def list_model_profiles(request: Request) -> ModelProfileListResponse:
    require_f1_capability(request, F1Capability.MODEL_PROFILE_READ)
    return request.app.state.model_profile_service.list_profiles()


@router.get("/{profile_id}/revisions", response_model=ModelProfileRevisionListResponse)
def list_model_profile_revisions(
    profile_id: UUID,
    request: Request,
) -> ModelProfileRevisionListResponse:
    require_f1_capability(request, F1Capability.MODEL_PROFILE_READ)
    secret = request.app.state.settings.canvas_agent_api_key
    return request.app.state.model_profile_service.list_revisions(
        profile_id,
        bearer_credential_available=bool(
            secret is not None and secret.get_secret_value()
        ),
    )
