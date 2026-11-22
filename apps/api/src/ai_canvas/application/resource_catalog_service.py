from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.errors import (
    GraphRevisionNotFound,
    InvalidResourceReference,
    ProjectNotFound,
    ResourceNotFound,
    ResourceRevisionNotFound,
    StoredGraphIntegrityError,
)
from ai_canvas.application.event_spine_service import EventSpineService
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts.experience_events import ActorKind
from ai_canvas.contracts.graph import GraphSnapshotV3
from ai_canvas.contracts.resources import (
    CreateResourceRequest,
    CreateResourceRevisionRequest,
    MediaResourcePayload,
    ResourceArtifactReference,
    ResourceDetailResponse,
    ResourcePayload,
    ResourceResponse,
    ResourceRevisionResponse,
    ResourceRevisionSummary,
    ResourceUpdateItem,
)
from ai_canvas.domain.graph.snapshots import canonical_snapshot, parse_snapshot_json
from ai_canvas.domain.graph.validation import validate_graph
from ai_canvas.domain.resources.canonical import (
    canonical_artifact_references,
    canonical_resource_payload,
    normalize_resource_tags,
    resource_search_text,
)
from ai_canvas.infrastructure.db.models import (
    ArtifactRevisionRow,
    ArtifactRow,
    GraphRevisionRow,
    ProjectRow,
    ResourceRevisionArtifactRow,
    ResourceRevisionRow,
    ResourceRevisionTagRow,
    ResourceRow,
)
from ai_canvas.infrastructure.db.repositories import GraphRepository, ResourceRepository


_PAYLOAD_ADAPTER = TypeAdapter(ResourcePayload)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ResourceCatalogService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        events: EventSpineService,
        *,
        resources: ResourceRepository | None = None,
        graphs: GraphRepository | None = None,
        write_gate: WriteGate | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.events = events
        self.resources = resources or ResourceRepository()
        self.graphs = graphs or GraphRepository()
        self._write_gate = write_gate or WriteGate()

    @staticmethod
    def _require_actor(actor_id: str) -> None:
        if not actor_id or len(actor_id) > 128:
            raise ValueError("actor_id must contain 1 to 128 characters")

    @staticmethod
    def _require_project(session: Session, project_id: UUID) -> None:
        if session.get(ProjectRow, str(project_id)) is None:
            raise ProjectNotFound(str(project_id))

    def _revision_response(
        self,
        session: Session,
        revision: ResourceRevisionRow,
    ) -> ResourceRevisionResponse:
        tags = list(
            session.scalars(
                select(ResourceRevisionTagRow)
                .where(ResourceRevisionTagRow.resource_revision_id == revision.id)
                .order_by(ResourceRevisionTagRow.normalized_tag)
            )
        )
        artifacts = list(
            session.scalars(
                select(ResourceRevisionArtifactRow)
                .where(ResourceRevisionArtifactRow.resource_revision_id == revision.id)
                .order_by(
                    ResourceRevisionArtifactRow.slot,
                    ResourceRevisionArtifactRow.position,
                    ResourceRevisionArtifactRow.artifact_revision_id,
                )
            )
        )
        return ResourceRevisionResponse(
            id=UUID(revision.id),
            resource_id=UUID(revision.resource_id),
            sequence=revision.sequence,
            schema_version=revision.schema_version,
            payload=_PAYLOAD_ADAPTER.validate_json(revision.payload_json),
            payload_sha256=revision.payload_sha256,
            tags=[
                {
                    "display_tag": tag.display_tag,
                    "normalized_tag": tag.normalized_tag,
                }
                for tag in tags
            ],
            artifacts=[
                ResourceArtifactReference(
                    slot=artifact.slot,
                    position=artifact.position,
                    artifact_revision_id=UUID(artifact.artifact_revision_id),
                )
                for artifact in artifacts
            ],
            created_by_actor=revision.created_by_actor,
            created_at=revision.created_at,
        )

    def _resource_response(
        self,
        session: Session,
        resource: ResourceRow,
        revision: ResourceRevisionRow | None = None,
    ) -> ResourceResponse:
        latest = revision or self.resources.get_latest_revision(session, resource.id)
        if latest is None:
            raise ResourceRevisionNotFound(resource.id)
        return ResourceResponse(
            id=UUID(resource.id),
            origin_project_id=UUID(resource.origin_project_id),
            kind=resource.kind,
            name=resource.name,
            created_at=resource.created_at,
            archived_at=resource.archived_at,
            latest_revision=self._revision_response(session, latest),
        )

    @staticmethod
    def _validate_artifact_references(
        session: Session,
        payload: ResourcePayload,
        artifacts: list[ResourceArtifactReference],
    ) -> None:
        if not artifacts:
            return
        revision_ids = [str(item.artifact_revision_id) for item in artifacts]
        observed = {
            revision_id: (kind, discarded_at)
            for revision_id, kind, discarded_at in session.execute(
                select(
                    ArtifactRevisionRow.id,
                    ArtifactRow.kind,
                    ArtifactRevisionRow.discarded_at,
                )
                .join(ArtifactRow, ArtifactRow.id == ArtifactRevisionRow.artifact_id)
                .where(ArtifactRevisionRow.id.in_(revision_ids))
            )
        }
        expected_kind = (
            payload.expected_media_kind
            if isinstance(payload, MediaResourcePayload)
            else None
        )
        for revision_id in revision_ids:
            observed_item = observed.get(revision_id)
            if observed_item is None or observed_item[1] is not None:
                raise InvalidResourceReference(revision_id, "artifact_revision_unavailable")
            if expected_kind is not None and observed_item[0] != expected_kind:
                raise InvalidResourceReference(revision_id, "artifact_media_kind_mismatch")

    @staticmethod
    def _child_rows(
        revision_id: UUID,
        tags,
        artifacts: list[ResourceArtifactReference],
    ) -> tuple[list[ResourceRevisionTagRow], list[ResourceRevisionArtifactRow]]:
        return (
            [
                ResourceRevisionTagRow(
                    id=str(uuid4()),
                    resource_revision_id=str(revision_id),
                    normalized_tag=tag.normalized_tag,
                    display_tag=tag.display_tag,
                )
                for tag in tags
            ],
            [
                ResourceRevisionArtifactRow(
                    id=str(uuid4()),
                    resource_revision_id=str(revision_id),
                    slot=artifact.slot,
                    position=artifact.position,
                    artifact_revision_id=str(artifact.artifact_revision_id),
                )
                for artifact in artifacts
            ],
        )

    def create_resource(
        self,
        project_id: UUID,
        request: CreateResourceRequest,
        *,
        actor_id: str,
        actor_kind: ActorKind = "HUMAN",
        after_create: Callable[[Session, ResourceRevisionRow], None] | None = None,
    ) -> ResourceResponse:
        self._require_actor(actor_id)
        name = request.name.strip()
        if not name:
            raise ValueError("resource name must contain non-whitespace characters")
        tags = normalize_resource_tags(request.tags)
        artifacts = canonical_artifact_references(request.artifacts)
        payload_json, payload_sha256 = canonical_resource_payload(request.payload)
        resource_id = uuid4()
        revision_id = uuid4()
        correlation_id = uuid4()
        now = utc_now()
        with self._write_gate:
            with self.session_factory.begin() as session:
                self._require_project(session, project_id)
                self._validate_artifact_references(session, request.payload, artifacts)
                resource = ResourceRow(
                    id=str(resource_id),
                    origin_project_id=str(project_id),
                    kind=request.payload.kind,
                    name=name,
                    created_at=now,
                    archived_at=None,
                )
                revision = ResourceRevisionRow(
                    id=str(revision_id),
                    resource_id=str(resource_id),
                    sequence=1,
                    schema_version=1,
                    payload_json=payload_json,
                    payload_sha256=payload_sha256,
                    search_text=resource_search_text(name, tags, request.payload),
                    created_by_actor=actor_id,
                    created_at=now,
                )
                tag_rows, artifact_rows = self._child_rows(
                    revision_id,
                    tags,
                    artifacts,
                )
                self.resources.create_resource_with_revision(
                    session,
                    resource,
                    revision,
                    tags=tag_rows,
                    artifacts=artifact_rows,
                )
                if after_create is not None:
                    after_create(session, revision)
                self.events.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=correlation_id,
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                    subject_type="resource",
                    subject_id=resource_id,
                    event_type="resource.created",
                    payload={
                        "resource_id": str(resource_id),
                        "resource_revision_id": str(revision_id),
                        "kind": request.payload.kind,
                        "sequence": 1,
                        "payload_sha256": payload_sha256,
                    },
                )
                return self._resource_response(session, resource, revision)

    def create_revision(
        self,
        project_id: UUID,
        resource_id: UUID,
        request: CreateResourceRevisionRequest,
        *,
        actor_id: str,
        actor_kind: ActorKind = "HUMAN",
        after_create: Callable[[Session, ResourceRevisionRow], None] | None = None,
    ) -> ResourceRevisionResponse:
        self._require_actor(actor_id)
        tags = normalize_resource_tags(request.tags)
        artifacts = canonical_artifact_references(request.artifacts)
        payload_json, payload_sha256 = canonical_resource_payload(request.payload)
        revision_id = uuid4()
        correlation_id = uuid4()
        now = utc_now()
        with self._write_gate:
            with self.session_factory.begin() as session:
                self._require_project(session, project_id)
                resource = self.resources.get_resource(session, str(resource_id))
                if resource is None:
                    raise ResourceNotFound(str(resource_id))
                if request.payload.kind != resource.kind:
                    raise InvalidResourceReference(
                        str(request.base_revision_id),
                        "resource_kind_mismatch",
                    )
                self._validate_artifact_references(session, request.payload, artifacts)
                current = self.resources.get_latest_revision(session, str(resource_id))
                next_sequence = 1 if current is None else current.sequence + 1
                revision = ResourceRevisionRow(
                    id=str(revision_id),
                    resource_id=str(resource_id),
                    sequence=next_sequence,
                    schema_version=1,
                    payload_json=payload_json,
                    payload_sha256=payload_sha256,
                    search_text=resource_search_text(resource.name, tags, request.payload),
                    created_by_actor=actor_id,
                    created_at=now,
                )
                tag_rows, artifact_rows = self._child_rows(
                    revision_id,
                    tags,
                    artifacts,
                )
                self.resources.append_revision(
                    session,
                    revision,
                    base_revision_id=str(request.base_revision_id),
                    tags=tag_rows,
                    artifacts=artifact_rows,
                )
                if after_create is not None:
                    after_create(session, revision)
                self.events.append_in_session(
                    session,
                    project_id=project_id,
                    correlation_id=correlation_id,
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                    subject_type="resource_revision",
                    subject_id=revision_id,
                    event_type="resource.revision_created",
                    payload={
                        "resource_id": str(resource_id),
                        "resource_revision_id": str(revision_id),
                        "kind": resource.kind,
                        "sequence": next_sequence,
                        "payload_sha256": payload_sha256,
                    },
                )
                return self._revision_response(session, revision)

    def get_resource(self, resource_id: UUID) -> ResourceDetailResponse:
        with self.session_factory() as session:
            resource = self.resources.get_resource(session, str(resource_id))
            if resource is None:
                raise ResourceNotFound(str(resource_id))
            revisions = self.resources.list_revisions(session, str(resource_id))
            if not revisions:
                raise ResourceRevisionNotFound(str(resource_id))
            latest = revisions[-1]
            response = self._resource_response(session, resource, latest)
            return ResourceDetailResponse(
                **response.model_dump(mode="python"),
                revision_history=[
                    ResourceRevisionSummary(
                        id=UUID(revision.id),
                        sequence=revision.sequence,
                        payload_sha256=revision.payload_sha256,
                        created_at=revision.created_at,
                    )
                    for revision in reversed(revisions)
                ],
            )

    def get_revision(self, revision_id: UUID) -> ResourceRevisionResponse:
        with self.session_factory() as session:
            revision = self.resources.get_revision(session, str(revision_id))
            if revision is None:
                raise ResourceRevisionNotFound(str(revision_id))
            return self._revision_response(session, revision)

    def list_resources(
        self,
        *,
        kind: str | None = None,
        query: str = "",
        tags: tuple[str, ...] = (),
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[ResourceResponse]:
        normalized_tags = tuple(
            tag.normalized_tag for tag in normalize_resource_tags(list(tags))
        )
        with self.session_factory() as session:
            revisions = self.resources.search_latest(
                session,
                kind=kind,
                query=query.strip().casefold(),
                normalized_tags=normalized_tags,
                include_archived=include_archived,
                limit=limit,
            )
            responses: list[ResourceResponse] = []
            for revision in revisions:
                resource = self.resources.get_resource(session, revision.resource_id)
                if resource is None:
                    raise ResourceNotFound(revision.resource_id)
                responses.append(self._resource_response(session, resource, revision))
            return responses

    def archive_resource(
        self,
        project_id: UUID,
        resource_id: UUID,
        *,
        actor_id: str,
        actor_kind: ActorKind = "HUMAN",
    ) -> ResourceResponse:
        self._require_actor(actor_id)
        with self._write_gate:
            with self.session_factory.begin() as session:
                self._require_project(session, project_id)
                resource = self.resources.get_resource(session, str(resource_id))
                if resource is None:
                    raise ResourceNotFound(str(resource_id))
                if resource.archived_at is None:
                    resource.archived_at = utc_now()
                    latest = self.resources.get_latest_revision(session, resource.id)
                    if latest is None:
                        raise ResourceRevisionNotFound(resource.id)
                    self.events.append_in_session(
                        session,
                        project_id=project_id,
                        correlation_id=uuid4(),
                        actor_kind=actor_kind,
                        actor_id=actor_id,
                        subject_type="resource",
                        subject_id=resource_id,
                        event_type="resource.archived",
                        payload={
                            "resource_id": str(resource_id),
                            "resource_revision_id": latest.id,
                            "kind": resource.kind,
                            "sequence": latest.sequence,
                            "payload_sha256": latest.payload_sha256,
                        },
                    )
                return self._resource_response(session, resource)

    def list_project_updates(self, project_id: UUID) -> list[ResourceUpdateItem]:
        with self.session_factory() as session:
            self._require_project(session, project_id)
            document = self.graphs.document_for_project(session, str(project_id))
            if document is None or document.active_revision_id is None:
                raise GraphRevisionNotFound("active")
            graph_revision = session.get(GraphRevisionRow, document.active_revision_id)
            if graph_revision is None or graph_revision.graph_document_id != document.id:
                raise GraphRevisionNotFound(document.active_revision_id)
            try:
                snapshot = validate_graph(parse_snapshot_json(graph_revision.snapshot_json))
                stored_json, stored_hash = canonical_snapshot(snapshot)
            except (TypeError, ValueError) as error:
                raise StoredGraphIntegrityError(graph_revision.id) from error
            if (
                stored_json != graph_revision.snapshot_json
                or stored_hash != graph_revision.snapshot_hash
            ):
                raise StoredGraphIntegrityError(graph_revision.id)
            if not isinstance(snapshot, GraphSnapshotV3):
                return []

            updates: list[ResourceUpdateItem] = []
            for binding in snapshot.resource_bindings:
                pinned = self.resources.get_revision(
                    session,
                    str(binding.resource_revision_id),
                )
                if pinned is None:
                    raise StoredGraphIntegrityError(graph_revision.id)
                resource = self.resources.get_resource(session, pinned.resource_id)
                latest = self.resources.get_latest_revision(session, pinned.resource_id)
                if (
                    resource is None
                    or latest is None
                    or resource.kind != binding.resource_kind
                ):
                    raise StoredGraphIntegrityError(graph_revision.id)
                if latest.sequence > pinned.sequence:
                    updates.append(
                        ResourceUpdateItem(
                            binding_id=binding.id,
                            binding_key=binding.binding_key,
                            scope_kind=binding.scope_kind,
                            scope_id=binding.scope_id,
                            resource_id=UUID(resource.id),
                            resource_kind=resource.kind,
                            pinned_revision_id=UUID(pinned.id),
                            latest_revision_id=UUID(latest.id),
                            pinned_sequence=pinned.sequence,
                            latest_sequence=latest.sequence,
                        )
                    )
            return sorted(
                updates,
                key=lambda item: (
                    item.scope_kind,
                    str(item.scope_id or ""),
                    item.binding_key,
                    str(item.binding_id),
                ),
            )
