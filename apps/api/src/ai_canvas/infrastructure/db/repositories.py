from datetime import datetime

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from ai_canvas.application.errors import ResourceRevisionConflict
from ai_canvas.infrastructure.db.models import (
    ArtifactRevisionRow,
    ArtifactRow,
    GraphDocumentRow,
    GraphRevisionRow,
    ImportSessionRow,
    ProjectRow,
    ProjectViewStateRow,
    ResourceRevisionArtifactRow,
    ResourceRevisionRow,
    ResourceRevisionTagRow,
    ResourceRow,
    StoredBlobRow,
)


class ProjectRepository:
    def add(self, session: Session, project: ProjectRow) -> None:
        session.add(project)

    def get(self, session: Session, project_id: str) -> ProjectRow | None:
        return session.get(ProjectRow, project_id)

    def list_all(self, session: Session) -> list[ProjectRow]:
        return list(session.scalars(select(ProjectRow).order_by(ProjectRow.updated_at.desc())))


class GraphRepository:
    def add_document(self, session: Session, document: GraphDocumentRow) -> None:
        session.add(document)

    def document(self, session: Session, document_id: str) -> GraphDocumentRow | None:
        return session.get(GraphDocumentRow, document_id)

    def document_for_project(self, session: Session, project_id: str) -> GraphDocumentRow | None:
        return session.scalar(select(GraphDocumentRow).where(GraphDocumentRow.project_id == project_id))

    def revision(self, session: Session, revision_id: str) -> GraphRevisionRow | None:
        return session.get(GraphRevisionRow, revision_id)

    def revision_for_mutation(
        self, session: Session, graph_document_id: str, mutation_id: str
    ) -> GraphRevisionRow | None:
        return session.scalar(
            select(GraphRevisionRow).where(
                GraphRevisionRow.graph_document_id == graph_document_id,
                GraphRevisionRow.client_mutation_id == mutation_id,
            )
        )

    def add_revision(self, session: Session, revision: GraphRevisionRow) -> None:
        session.add(revision)

    def next_sequence(self, session: Session, graph_document_id: str) -> int:
        current = session.scalar(
            select(func.max(GraphRevisionRow.sequence)).where(
                GraphRevisionRow.graph_document_id == graph_document_id
            )
        )
        return 0 if current is None else current + 1

    def ancestor_revisions(
        self,
        session: Session,
        graph_document_id: str,
        start_revision_id: str,
        *,
        limit: int,
    ) -> list[GraphRevisionRow]:
        if not 1 <= limit <= 10_000:
            raise ValueError("graph ancestor query limit must be between 1 and 10,000")
        result: list[GraphRevisionRow] = []
        visited: set[str] = set()
        revision_id: str | None = start_revision_id
        while revision_id is not None and len(result) < limit:
            if revision_id in visited:
                raise ValueError("graph revision ancestry contains a cycle")
            visited.add(revision_id)
            revision = self.revision(session, revision_id)
            if revision is None or revision.graph_document_id != graph_document_id:
                raise ValueError("graph revision ancestry is missing or cross-document")
            result.append(revision)
            revision_id = revision.parent_revision_id
        return result

    def revisions_descending(
        self,
        session: Session,
        graph_document_id: str,
        at_or_before_sequence: int,
        limit: int,
    ) -> list[GraphRevisionRow]:
        if not 1 <= limit <= 2:
            raise ValueError("graph revision history query limit must be between 1 and 2")
        return list(
            session.scalars(
                select(GraphRevisionRow)
                .where(
                    GraphRevisionRow.graph_document_id == graph_document_id,
                    GraphRevisionRow.sequence <= at_or_before_sequence,
                )
                .order_by(GraphRevisionRow.sequence.desc())
                .limit(limit)
            )
        )

    def artifact_revision_rows(
        self,
        session: Session,
        revision_ids: list[str],
    ) -> dict[str, tuple[ArtifactRevisionRow, ArtifactRow, StoredBlobRow]]:
        if not revision_ids:
            return {}
        rows = session.execute(
            select(ArtifactRevisionRow, ArtifactRow, StoredBlobRow)
            .join(ArtifactRow, ArtifactRevisionRow.artifact_id == ArtifactRow.id)
            .join(StoredBlobRow, ArtifactRevisionRow.blob_sha256 == StoredBlobRow.sha256)
            .where(
                ArtifactRevisionRow.id.in_(revision_ids),
                ArtifactRevisionRow.discarded_at.is_(None),
            )
        ).tuples()
        return {revision.id: (revision, artifact, blob) for revision, artifact, blob in rows}

    def view_state(self, session: Session, project_id: str) -> ProjectViewStateRow | None:
        return session.get(ProjectViewStateRow, project_id)

    def add_view_state(self, session: Session, view_state: ProjectViewStateRow) -> None:
        session.add(view_state)


class ArtifactRepository:
    def add_blob(self, session: Session, blob: StoredBlobRow) -> None:
        session.add(blob)

    def blob(self, session: Session, sha256: str) -> StoredBlobRow | None:
        return session.get(StoredBlobRow, sha256)

    def add_artifact(self, session: Session, artifact: ArtifactRow) -> None:
        session.add(artifact)

    def artifact(self, session: Session, artifact_id: str) -> ArtifactRow | None:
        return session.get(ArtifactRow, artifact_id)

    def add_import_session(self, session: Session, import_session: ImportSessionRow) -> None:
        session.add(import_session)

    def import_session(self, session: Session, import_id: str) -> ImportSessionRow | None:
        return session.get(ImportSessionRow, import_id)

    def import_session_for_client(
        self,
        session: Session,
        source_project_id: str,
        client_import_id: str,
    ) -> ImportSessionRow | None:
        return session.scalar(
            select(ImportSessionRow).where(
                ImportSessionRow.source_project_id == source_project_id,
                ImportSessionRow.client_import_id == client_import_id,
            )
        )

    def add_revision(self, session: Session, revision: ArtifactRevisionRow) -> None:
        session.add(revision)

    def revision(self, session: Session, revision_id: str) -> ArtifactRevisionRow | None:
        return session.get(ArtifactRevisionRow, revision_id)

    def revision_for_import(
        self,
        session: Session,
        import_id: str,
    ) -> ArtifactRevisionRow | None:
        return session.scalar(
            select(ArtifactRevisionRow).where(
                ArtifactRevisionRow.source_import_session_id == import_id
            )
        )

    def revisions_for_artifact(
        self,
        session: Session,
        artifact_id: str,
    ) -> list[tuple[ArtifactRevisionRow, ArtifactRow, StoredBlobRow]]:
        return list(
            session.execute(
                select(ArtifactRevisionRow, ArtifactRow, StoredBlobRow)
                .join(ArtifactRow, ArtifactRevisionRow.artifact_id == ArtifactRow.id)
                .join(StoredBlobRow, ArtifactRevisionRow.blob_sha256 == StoredBlobRow.sha256)
                .where(
                    ArtifactRevisionRow.artifact_id == artifact_id,
                    ArtifactRevisionRow.discarded_at.is_(None),
                )
                .order_by(ArtifactRevisionRow.sequence)
            ).tuples()
        )


class ResourceRepository:
    def get_resource(self, session: Session, resource_id: str) -> ResourceRow | None:
        return session.get(ResourceRow, resource_id)

    def get_revision(
        self,
        session: Session,
        revision_id: str,
    ) -> ResourceRevisionRow | None:
        return session.get(ResourceRevisionRow, revision_id)

    def get_latest_revision(
        self,
        session: Session,
        resource_id: str,
    ) -> ResourceRevisionRow | None:
        return session.scalar(
            select(ResourceRevisionRow)
            .where(ResourceRevisionRow.resource_id == resource_id)
            .order_by(ResourceRevisionRow.sequence.desc())
            .limit(1)
        )

    def create_resource_with_revision(
        self,
        session: Session,
        resource: ResourceRow,
        revision: ResourceRevisionRow,
        *,
        tags: list[ResourceRevisionTagRow],
        artifacts: list[ResourceRevisionArtifactRow],
    ) -> None:
        if revision.resource_id != resource.id or revision.sequence != 1:
            raise ValueError("the initial resource revision must belong to the resource at sequence 1")
        self._validate_children(revision.id, tags=tags, artifacts=artifacts)
        session.add(resource)
        session.flush()
        session.add(revision)
        session.flush()
        session.add_all(tags)
        session.add_all(artifacts)

    def append_revision(
        self,
        session: Session,
        revision: ResourceRevisionRow,
        *,
        base_revision_id: str,
        tags: list[ResourceRevisionTagRow],
        artifacts: list[ResourceRevisionArtifactRow],
    ) -> None:
        current = self.get_latest_revision(session, revision.resource_id)
        if current is None or current.id != base_revision_id:
            raise ResourceRevisionConflict(
                revision.resource_id,
                None if current is None else current.id,
            )
        if revision.sequence != current.sequence + 1:
            raise ValueError("the next resource revision sequence must be contiguous")
        self._validate_children(revision.id, tags=tags, artifacts=artifacts)
        session.add(revision)
        session.flush()
        session.add_all(tags)
        session.add_all(artifacts)

    def list_revisions(
        self,
        session: Session,
        resource_id: str,
    ) -> list[ResourceRevisionRow]:
        return list(
            session.scalars(
                select(ResourceRevisionRow)
                .where(ResourceRevisionRow.resource_id == resource_id)
                .order_by(ResourceRevisionRow.sequence)
            )
        )

    def search_latest(
        self,
        session: Session,
        *,
        kind: str | None,
        query: str,
        normalized_tags: tuple[str, ...],
        include_archived: bool,
        limit: int,
    ) -> list[ResourceRevisionRow]:
        if not 1 <= limit <= 100:
            raise ValueError("resource search limit must be between 1 and 100")
        latest_sequences = (
            select(
                ResourceRevisionRow.resource_id.label("resource_id"),
                func.max(ResourceRevisionRow.sequence).label("sequence"),
            )
            .group_by(ResourceRevisionRow.resource_id)
            .subquery()
        )
        statement = (
            select(ResourceRevisionRow)
            .join(ResourceRow, ResourceRevisionRow.resource_id == ResourceRow.id)
            .join(
                latest_sequences,
                (ResourceRevisionRow.resource_id == latest_sequences.c.resource_id)
                & (ResourceRevisionRow.sequence == latest_sequences.c.sequence),
            )
        )
        if kind is not None:
            statement = statement.where(ResourceRow.kind == kind)
        if query:
            statement = statement.where(ResourceRevisionRow.search_text.contains(query))
        if not include_archived:
            statement = statement.where(ResourceRow.archived_at.is_(None))
        for normalized_tag in dict.fromkeys(normalized_tags):
            statement = statement.where(
                exists().where(
                    ResourceRevisionTagRow.resource_revision_id == ResourceRevisionRow.id,
                    ResourceRevisionTagRow.normalized_tag == normalized_tag,
                )
            )
        return list(
            session.scalars(
                statement.order_by(ResourceRevisionRow.created_at.desc()).limit(limit)
            )
        )

    def archive(
        self,
        session: Session,
        resource_id: str,
        archived_at: datetime,
    ) -> ResourceRow | None:
        resource = self.get_resource(session, resource_id)
        if resource is not None:
            resource.archived_at = archived_at
        return resource

    @staticmethod
    def _validate_children(
        revision_id: str,
        *,
        tags: list[ResourceRevisionTagRow],
        artifacts: list[ResourceRevisionArtifactRow],
    ) -> None:
        if any(tag.resource_revision_id != revision_id for tag in tags):
            raise ValueError("resource revision tag belongs to another revision")
        if any(artifact.resource_revision_id != revision_id for artifact in artifacts):
            raise ValueError("resource revision artifact belongs to another revision")
