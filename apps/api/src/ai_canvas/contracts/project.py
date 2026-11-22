from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints


ProjectName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: ProjectName


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    graph_document_id: UUID
    active_revision_id: UUID
    selected_branch_id: UUID | None = None
    selection_version: int | None = None
    handoff_enabled: bool = False
