from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ai_canvas.contracts.artifact import (
    ArtifactImportConflictReason,
    ArtifactImportFailureCode,
)


class InvalidGraphProblem(BaseModel):
    code: Literal["invalid_graph"] = "invalid_graph"
    message: str


class RequestValidationProblem(BaseModel):
    code: Literal["request_validation"] = "request_validation"
    issues: list[dict[str, Any]]


class ProjectNotFoundProblem(BaseModel):
    code: Literal["project_not_found"] = "project_not_found"
    project_id: UUID


class GraphRevisionNotFoundProblem(BaseModel):
    code: Literal["graph_revision_not_found"] = "graph_revision_not_found"
    revision_id: str


class GraphBranchNotFoundProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["graph_branch_not_found"] = "graph_branch_not_found"
    branch_id: UUID


class PromotionNotFoundProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["promotion_not_found"] = "promotion_not_found"
    promotion_id: UUID


class GraphBranchNameConflictProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["graph_branch_name_conflict"] = "graph_branch_name_conflict"
    normalized_name: str


class BranchHeadStaleProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["branch_head_stale"] = "branch_head_stale"
    branch_id: UUID
    current_revision_id: UUID
    current_version: int


class SelectionPointerStaleProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["selection_pointer_stale"] = "selection_pointer_stale"
    current_branch_id: UUID
    current_revision_id: UUID
    current_version: int


class StaleImpactConfirmationRequiredProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal[
        "stale_impact_confirmation_required"
    ] = "stale_impact_confirmation_required"
    impact_sha256: str


class PromotionStaleProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["promotion_stale"] = "promotion_stale"


class PromotionUndoStaleProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["promotion_undo_stale"] = "promotion_undo_stale"


class GraphChangeSetUndoStaleProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["graph_changeset_undo_stale"] = "graph_changeset_undo_stale"


class GraphChangeSetUndoUnsupportedProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal[
        "graph_changeset_undo_unsupported"
    ] = "graph_changeset_undo_unsupported"


class StoredGraphIntegrityProblem(BaseModel):
    code: Literal["stored_graph_integrity"] = "stored_graph_integrity"
    revision_id: UUID


class ShotNotFoundProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["shot_not_found"] = "shot_not_found"
    shot_id: UUID


class ShotSlotAssociationCursorInvalidProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal[
        "shot_slot_association_cursor_invalid"
    ] = "shot_slot_association_cursor_invalid"


class RequestBodyTooLargeProblem(BaseModel):
    code: Literal["request_body_too_large"] = "request_body_too_large"
    max_bytes: int


class ArtifactImportNotFoundProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["artifact_import_not_found"] = "artifact_import_not_found"
    import_id: UUID


class ArtifactRevisionNotFoundProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["artifact_revision_not_found"] = "artifact_revision_not_found"
    revision_id: UUID


class ArtifactImportConflictProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["artifact_import_conflict"] = "artifact_import_conflict"
    import_id: UUID
    reason: ArtifactImportConflictReason


class ArtifactImportFailedProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["artifact_import_failed"] = "artifact_import_failed"
    import_id: UUID
    failure_code: ArtifactImportFailureCode


class ResourceNotFoundProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["resource_not_found"] = "resource_not_found"
    resource_id: UUID


class ResourceRevisionNotFoundProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["resource_revision_not_found"] = "resource_revision_not_found"
    revision_id: UUID


class ResourceRevisionConflictProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["resource_revision_conflict"] = "resource_revision_conflict"
    resource_id: UUID
    current_revision_id: UUID | None


class InvalidResourceReferenceProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["invalid_resource_reference"] = "invalid_resource_reference"
    revision_id: UUID
    reason: str


class InvalidPromptDocumentProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["invalid_prompt_document"] = "invalid_prompt_document"
