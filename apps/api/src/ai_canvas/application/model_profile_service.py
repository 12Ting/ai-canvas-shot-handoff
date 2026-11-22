import json
from datetime import timezone
from uuid import UUID, uuid4

from sqlalchemy import select

from ai_canvas.application.control_config_service import ControlConfigService
from ai_canvas.contracts.config_changesets import (
    ConfigChangeSetResponse,
    CreateConfigChangeSetRequest,
)
from ai_canvas.contracts.model_profiles import (
    ModelProfileListResponse,
    ModelProfileResponse,
    ModelProfileRevisionInput,
    ModelProfileRevisionListResponse,
    ModelProfileRevisionResponse,
    ProviderInput,
)
from ai_canvas.domain.control.canonical import canonical_sha256
from ai_canvas.infrastructure.db.model_profile_repository import ModelProfileRepository
from ai_canvas.infrastructure.db.models import ModelProfileRow


class ModelProfileService:
    """Trusted controller for declaring immutable model profiles.

    This service only constructs a reviewable ConfigChangeSet.  Provider and
    profile rows are created atomically when that ConfigChangeSet is approved
    and applied by ``ControlConfigService``.
    """

    def __init__(
        self,
        control_config: ControlConfigService,
        repository: ModelProfileRepository | None = None,
    ) -> None:
        self.control_config = control_config
        self.repository = repository or ModelProfileRepository()

    def list_profiles(self) -> ModelProfileListResponse:
        with self.control_config.session_factory() as session:
            _, active = self.control_config._active_in_session(session)
            active_config = json.loads(active.config_json)
            profiles = list(
                session.scalars(
                    select(ModelProfileRow).order_by(
                        ModelProfileRow.created_at, ModelProfileRow.id
                    )
                )
            )
            return ModelProfileListResponse(
                profiles=[
                    ModelProfileResponse(
                        id=UUID(profile.id),
                        profile_key=profile.profile_key,
                        role=profile.role,
                        active_revision_id=(
                            UUID(active_id)
                            if isinstance(active_id, str)
                            and (
                                active_revision := self.repository.revision(
                                    session, active_id
                                )
                            )
                            is not None
                            and active_revision.model_profile_id == profile.id
                            else None
                        ),
                        created_at=(
                            profile.created_at.replace(tzinfo=timezone.utc)
                            if profile.created_at.tzinfo is None
                            else profile.created_at.astimezone(timezone.utc)
                        ),
                    )
                    for profile in profiles
                    for active_id in [
                        active_config.get(
                            "active_canvas_agent_profile_revision_id"
                            if profile.role == "CANVAS_AGENT"
                            else "active_prompt_editor_profile_revision_id"
                        )
                    ]
                ]
            )

    def list_revisions(
        self,
        profile_id: UUID,
        *,
        bearer_credential_available: bool,
    ) -> ModelProfileRevisionListResponse:
        with self.control_config.session_factory() as session:
            profile = self.repository.profile(session, str(profile_id))
            if profile is None:
                raise LookupError(str(profile_id))
            revisions = self.repository.revisions_for_profile(session, profile.id)
            return ModelProfileRevisionListResponse(
                profile_id=profile_id,
                revisions=[
                    ModelProfileRevisionResponse(
                        id=UUID(row.id),
                        sequence=row.sequence,
                        provider_id=UUID(row.provider_id),
                        declaration=json.loads(row.declaration_json),
                        declaration_sha256=row.declaration_sha256,
                        credential_available=(
                            True
                            if json.loads(row.declaration_json)["credential_kind"]
                            == "NONE"
                            else bearer_credential_available
                        ),
                        created_at=(
                            row.created_at.replace(tzinfo=timezone.utc)
                            if row.created_at.tzinfo is None
                            else row.created_at.astimezone(timezone.utc)
                        ),
                    )
                    for row in revisions
                ],
            )

    def request_declaration(
        self,
        *,
        audit_project_id: UUID,
        base_config_revision_id: UUID,
        provider: ProviderInput | dict,
        profile_key: str,
        revision: ModelProfileRevisionInput | dict,
        actor_id: str,
    ) -> ConfigChangeSetResponse:
        provider_input = ProviderInput.model_validate(provider)
        revision_input = ModelProfileRevisionInput.model_validate(revision)
        if actor_id == "ai_canvas":
            raise PermissionError("model profiles require a trusted control actor")
        with self.control_config.session_factory() as session:
            existing_provider = self.repository.provider_by_key(
                session, provider_input.provider_key
            )
            if (
                existing_provider is not None
                and existing_provider.adapter_kind != provider_input.adapter_kind
            ):
                raise ValueError("provider adapter is immutable")
            existing_profile = self.repository.profile_by_key(session, profile_key)
            if (
                existing_profile is not None
                and existing_profile.role != revision_input.role
            ):
                raise ValueError("model profile role is immutable")
            provider_id = (
                UUID(existing_provider.id) if existing_provider is not None else uuid4()
            )
            model_profile_id = (
                UUID(existing_profile.id) if existing_profile is not None else uuid4()
            )
        declaration = revision_input.model_dump(mode="json")
        request = CreateConfigChangeSetRequest.model_validate(
            {
                "kind": "config_change_set",
                "schema_version": 1,
                "audit_project_id": audit_project_id,
                "base_config_revision_id": base_config_revision_id,
                "operations": [
                    {
                        "op": "declare_model_profile_revision",
                        "provider_id": provider_id,
                        "provider": provider_input.model_dump(mode="json"),
                        "model_profile_id": model_profile_id,
                        "profile_key": profile_key,
                        "model_profile_revision_id": uuid4(),
                        "revision": declaration,
                        "declaration_sha256": canonical_sha256(declaration),
                        "activate": True,
                    }
                ],
            }
        )
        return self.control_config.request(request, actor_id=actor_id)
