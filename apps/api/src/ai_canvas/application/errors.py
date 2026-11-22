from ai_canvas.contracts.artifact import (
    ArtifactImportConflictReason,
    ArtifactImportFailureCode,
)


class ProjectNotFound(LookupError):
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(project_id)


class GraphRevisionNotFound(LookupError):
    def __init__(self, revision_id: str) -> None:
        self.revision_id = revision_id
        super().__init__(revision_id)


class GraphBranchNotFound(LookupError):
    def __init__(self, branch_id: str) -> None:
        self.branch_id = branch_id
        super().__init__(branch_id)


class PromotionNotFound(LookupError):
    def __init__(self, promotion_id: str) -> None:
        self.promotion_id = promotion_id
        super().__init__(promotion_id)


class GraphRevisionConflict(RuntimeError):
    def __init__(self, current_revision_id: str) -> None:
        self.current_revision_id = current_revision_id
        super().__init__(f"graph base revision is stale; current={current_revision_id}")


class ResourceRevisionConflict(RuntimeError):
    def __init__(self, resource_id: str, current_revision_id: str | None) -> None:
        self.resource_id = resource_id
        self.current_revision_id = current_revision_id
        super().__init__(
            f"resource base revision is stale; resource={resource_id}; "
            f"current={current_revision_id}"
        )


class ResourceNotFound(LookupError):
    def __init__(self, resource_id: str) -> None:
        self.resource_id = resource_id
        super().__init__(resource_id)


class ResourceRevisionNotFound(LookupError):
    def __init__(self, revision_id: str) -> None:
        self.revision_id = revision_id
        super().__init__(revision_id)


class InvalidResourceReference(ValueError):
    def __init__(self, revision_id: str, reason: str) -> None:
        self.revision_id = revision_id
        self.reason = reason
        super().__init__(f"invalid resource reference ({reason}): {revision_id}")


class InvalidPromptDocument(ValueError):
    def __init__(self) -> None:
        super().__init__("prompt document failed deterministic validation")


class ClientMutationReuse(RuntimeError):
    def __init__(self, mutation_id: str) -> None:
        self.mutation_id = mutation_id
        super().__init__(f"client mutation id was reused with different content: {mutation_id}")


class StoredGraphIntegrityError(RuntimeError):
    def __init__(self, revision_id: str) -> None:
        self.revision_id = revision_id
        super().__init__(f"stored graph revision failed integrity validation: {revision_id}")


class ApprovalSubjectIntegrityError(ValueError):
    def __init__(self, subject_type: str, subject_id: str) -> None:
        self.subject_type = subject_type
        self.subject_id = subject_id
        super().__init__(f"{subject_type} approval subject integrity mismatch: {subject_id}")


class ApprovalSubjectStale(ValueError):
    pass


class ApprovalRequired(ValueError):
    pass


class StaleImpactConfirmationRequired(ValueError):
    def __init__(self, impact_sha256: str) -> None:
        self.impact_sha256 = impact_sha256
        super().__init__("stale materialization impact requires exact confirmation")


class GraphBranchNameConflict(RuntimeError):
    def __init__(self, normalized_name: str) -> None:
        self.normalized_name = normalized_name
        super().__init__(f"graph branch name already exists: {normalized_name}")


class BranchHeadStale(RuntimeError):
    def __init__(
        self, branch_id: str, current_revision_id: str, current_version: int
    ) -> None:
        self.branch_id = branch_id
        self.current_revision_id = current_revision_id
        self.current_version = current_version
        super().__init__(f"branch head is stale: {branch_id}")


class SelectionPointerStale(RuntimeError):
    def __init__(
        self,
        current_branch_id: str,
        current_revision_id: str,
        current_version: int,
    ) -> None:
        self.current_branch_id = current_branch_id
        self.current_revision_id = current_revision_id
        self.current_version = current_version
        super().__init__("selection pointer is stale")


class PromotionStale(RuntimeError):
    pass


class PromotionUndoStale(RuntimeError):
    pass


class GraphChangeSetUndoStale(RuntimeError):
    pass


class GraphChangeSetUndoUnsupported(RuntimeError):
    pass


class ChangeSetDomainMismatch(ValueError):
    def __init__(self, change_set_id: str) -> None:
        self.change_set_id = change_set_id
        super().__init__(f"change set belongs to another domain: {change_set_id}")


class GraphChangeSetBaseRevisionStale(RuntimeError):
    def __init__(self, current_revision_id: str) -> None:
        self.current_revision_id = current_revision_id
        super().__init__(
            f"graph change-set base revision is stale; current={current_revision_id}"
        )


class ConfigChangeSetBaseRevisionStale(RuntimeError):
    pass


class GraphChangeSetNotFound(LookupError):
    def __init__(self, change_set_id: str) -> None:
        self.change_set_id = change_set_id
        super().__init__(change_set_id)


class ConfigChangeSetNotFound(LookupError):
    def __init__(self, change_set_id: str) -> None:
        self.change_set_id = change_set_id
        super().__init__(change_set_id)


class ShotNotFound(LookupError):
    def __init__(self, shot_id: str) -> None:
        self.shot_id = shot_id
        super().__init__(shot_id)


class ShotSlotAssociationCursorInvalid(ValueError):
    def __init__(self) -> None:
        super().__init__("shot slot association cursor is invalid")


class ArtifactImportNotFound(LookupError):
    def __init__(self, import_id: str) -> None:
        self.import_id = import_id
        super().__init__(import_id)


class ArtifactRevisionNotFound(LookupError):
    def __init__(self, revision_id: str) -> None:
        self.revision_id = revision_id
        super().__init__(revision_id)


class ArtifactImportConflict(RuntimeError):
    def __init__(
        self,
        import_id: str,
        reason: ArtifactImportConflictReason | str,
    ) -> None:
        self.import_id = import_id
        self.reason = ArtifactImportConflictReason(reason)
        super().__init__(f"artifact import conflict ({self.reason.value}): {import_id}")


class ArtifactImportFailed(RuntimeError):
    def __init__(
        self,
        import_id: str,
        failure_code: ArtifactImportFailureCode | str,
    ) -> None:
        self.import_id = import_id
        self.failure_code = ArtifactImportFailureCode(failure_code)
        super().__init__(f"artifact import failed ({self.failure_code.value}): {import_id}")


class MediaSessionUnauthorized(PermissionError):
    def __init__(self) -> None:
        super().__init__("media session is missing, expired, or unauthorized")


class MediaResourceUnavailable(RuntimeError):
    def __init__(self, revision_id: str, health: str) -> None:
        self.revision_id = revision_id
        self.health = health
        super().__init__(f"media resource {revision_id} is unavailable: {health}")
