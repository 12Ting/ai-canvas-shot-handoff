import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import secrets
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.errors import (
    ClientMutationReuse,
    GraphRevisionConflict,
    GraphRevisionNotFound,
    ProjectNotFound,
    ShotNotFound,
    ShotSlotAssociationCursorInvalid,
    StoredGraphIntegrityError,
)
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.contracts.artifact import (
    ArtifactMetadata,
    ArtifactRevisionResponse,
)
from ai_canvas.contracts.graph import (
    CreateGraphRevisionRequest,
    GraphRevisionResponse,
    GraphSnapshotV1,
    GraphSnapshotV2,
    GraphSnapshotV3,
    MediaInputNodeV2,
    Shot,
    ViewportState,
)
from ai_canvas.contracts.shot_history import (
    ShotSlot,
    ShotSlotAssociationAction,
    ShotSlotAssociationHistoryItem,
    ShotSlotAssociationHistoryResponse,
)
from ai_canvas.domain.graph.snapshots import (
    canonical_snapshot as _canonical_snapshot,
    normalize_to_v2,
    normalize_to_v3,
    parse_snapshot_json,
)
from ai_canvas.domain.graph.validation import GraphValidationError, validate_graph
from ai_canvas.infrastructure.db.models import (
    ArtifactRevisionRow,
    ArtifactRow,
    GraphDocumentRow,
    GraphRevisionRow,
    ProjectRow,
    ProjectViewStateRow,
    ResourceRevisionArtifactRow,
    ResourceRevisionRow,
    ResourceRow,
    StoredBlobRow,
)
from ai_canvas.infrastructure.db.branch_repository import BranchRepository
from ai_canvas.infrastructure.db.repositories import GraphRepository


_ARTIFACT_METADATA_ADAPTER = TypeAdapter(ArtifactMetadata)
_HISTORY_SCAN_LIMIT = 200
_HISTORY_QUERY_LIMIT = 2


class _ShotSlotAssociationCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[2] = 2
    project_id: UUID
    anchor_revision_id: UUID
    anchor_sequence: int = Field(ge=0)
    shot_id: UUID
    slot: ShotSlot
    next_sequence: int = Field(ge=0)
    next_revision_id: UUID
    boundary_child_revision_id: UUID | None
    event_ordinal: int = Field(ge=0, le=127)


@dataclass(frozen=True)
class _ShotSlotAssociationEvent:
    graph_revision_id: str
    graph_revision_sequence: int
    event_ordinal: int
    occurred_at: datetime
    action: ShotSlotAssociationAction
    reference_index: int | None
    artifact_revision_id: UUID | None


@dataclass(frozen=True)
class _ShotSlotAssociationPosition:
    graph_revision_id: str
    graph_revision_sequence: int
    event_ordinal: int
    boundary_child_revision_id: str | None


def _encode_history_cursor(
    cursor: _ShotSlotAssociationCursor,
    secret: bytes,
) -> str:
    payload = cursor.model_dump_json().encode("utf-8")
    encoded_payload = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    signature = hmac.new(secret, payload, hashlib.sha256).digest()
    encoded_signature = (
        base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    )
    return f"{encoded_payload}.{encoded_signature}"


def _decode_history_cursor(
    value: str,
    secret: bytes,
) -> _ShotSlotAssociationCursor:
    if len(value) > 2_048:
        raise ShotSlotAssociationCursorInvalid
    try:
        encoded_payload_text, encoded_signature_text = value.split(".")
        encoded_payload = encoded_payload_text.encode("ascii")
        encoded_signature = encoded_signature_text.encode("ascii")
        payload_padding = b"=" * (-len(encoded_payload) % 4)
        signature_padding = b"=" * (-len(encoded_signature) % 4)
        payload = base64.b64decode(
            encoded_payload + payload_padding,
            altchars=b"-_",
            validate=True,
        )
        signature = base64.b64decode(
            encoded_signature + signature_padding,
            altchars=b"-_",
            validate=True,
        )
        expected_signature = hmac.new(secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ShotSlotAssociationCursorInvalid
        cursor = _ShotSlotAssociationCursor.model_validate_json(payload)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        raise ShotSlotAssociationCursorInvalid from None
    # Requiring the canonical spelling rejects alternate encodings after the
    # HMAC has authenticated the exact payload bytes.
    if _encode_history_cursor(cursor, secret) != value:
        raise ShotSlotAssociationCursorInvalid
    return cursor


def canonical_snapshot(
    snapshot: GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3,
) -> tuple[str, str]:
    return _canonical_snapshot(snapshot)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GraphService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        repository: GraphRepository | None = None,
        branches: BranchRepository | None = None,
        write_gate: WriteGate | None = None,
        cursor_secret: str | bytes | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.repository = repository or GraphRepository()
        self.branches = branches or BranchRepository()
        if cursor_secret is None:
            resolved_cursor_secret = secrets.token_bytes(32)
        elif isinstance(cursor_secret, str):
            resolved_cursor_secret = cursor_secret.encode("utf-8")
        else:
            resolved_cursor_secret = bytes(cursor_secret)
        if not resolved_cursor_secret:
            raise ValueError("cursor_secret must not be empty")
        self._cursor_secret = resolved_cursor_secret
        # ProjectService and GraphService share this one gate in create_app so every
        # process-local writer is serialized before opening its short transaction.
        self._write_gate = write_gate or WriteGate()

    def _response(
        self,
        session: Session,
        project_id: UUID,
        document: GraphDocumentRow,
        revision: GraphRevisionRow,
        snapshot: GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3 | None = None,
    ) -> GraphRevisionResponse:
        view_row = self.repository.view_state(session, str(project_id))
        viewport = (
            ViewportState.model_validate_json(view_row.viewport_json)
            if view_row is not None
            else ViewportState()
        )
        return GraphRevisionResponse(
            revision_id=UUID(revision.id),
            graph_document_id=UUID(document.id),
            sequence=revision.sequence,
            snapshot_hash=revision.snapshot_hash,
            snapshot=(
                snapshot
                if snapshot is not None
                else self._load_stored_snapshot(revision)
            ),
            viewport=viewport,
        )

    def _load_stored_snapshot(
        self,
        revision: GraphRevisionRow,
    ) -> GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3:
        try:
            snapshot = validate_graph(parse_snapshot_json(revision.snapshot_json))
            canonical_json, canonical_hash = canonical_snapshot(snapshot)
        except (TypeError, ValueError) as error:
            raise StoredGraphIntegrityError(revision.id) from error
        if (
            canonical_json != revision.snapshot_json
            or canonical_hash != revision.snapshot_hash
        ):
            raise StoredGraphIntegrityError(revision.id)
        return snapshot

    def get_active(self, project_id: UUID) -> GraphRevisionResponse:
        with self.session_factory() as session:
            document = self.repository.document_for_project(session, str(project_id))
            if document is None:
                raise ProjectNotFound(str(project_id))
            if document.active_revision_id is None:
                raise GraphRevisionNotFound("active")
            revision = self.repository.revision(session, document.active_revision_id)
            if revision is None or revision.graph_document_id != document.id:
                raise GraphRevisionNotFound(document.active_revision_id)
            return self._response(session, project_id, document, revision)

    def get_revision(self, project_id: UUID, revision_id: UUID) -> GraphRevisionResponse:
        with self.session_factory() as session:
            document = self.repository.document_for_project(session, str(project_id))
            if document is None:
                raise ProjectNotFound(str(project_id))
            revision = self.repository.revision(session, str(revision_id))
            if revision is None or revision.graph_document_id != document.id:
                raise GraphRevisionNotFound(str(revision_id))
            return self._response(session, project_id, document, revision)

    def list_shot_slot_associations(
        self,
        project_id: UUID,
        shot_id: UUID,
        slot: ShotSlot,
        anchor_revision_id: UUID,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ShotSlotAssociationHistoryResponse:
        if not 1 <= limit <= 50:
            raise ValueError("history limit must be between 1 and 50")
        with self.session_factory() as session:
            document = self.repository.document_for_project(session, str(project_id))
            if document is None:
                raise ProjectNotFound(str(project_id))
            anchor = self.repository.revision(session, str(anchor_revision_id))
            if anchor is None or anchor.graph_document_id != document.id:
                raise GraphRevisionNotFound(str(anchor_revision_id))
            anchor_snapshot = self._load_stored_snapshot(anchor)
            anchor_has_shot = (
                self._shot_in_snapshot(anchor_snapshot, shot_id) is not None
            )
            anchor_id = anchor.id
            anchor_sequence = anchor.sequence

            if cursor is None:
                if not anchor_has_shot:
                    raise ShotNotFound(str(shot_id))
                start_sequence = anchor_sequence
                start_revision_id = anchor_id
                start_boundary_child_revision_id = None
                start_event_ordinal = 0
            else:
                decoded = _decode_history_cursor(cursor, self._cursor_secret)
                if (
                    decoded.project_id != project_id
                    or decoded.anchor_revision_id != anchor_revision_id
                    or decoded.anchor_sequence != anchor_sequence
                    or decoded.shot_id != shot_id
                    or decoded.slot != slot
                    or decoded.next_sequence > anchor_sequence
                    or (
                        decoded.boundary_child_revision_id is None
                        and (
                            decoded.next_sequence != anchor_sequence
                            or decoded.next_revision_id != anchor_revision_id
                        )
                    )
                    or (
                        decoded.boundary_child_revision_id is not None
                        and decoded.next_sequence >= anchor_sequence
                    )
                ):
                    raise ShotSlotAssociationCursorInvalid
                if not anchor_has_shot:
                    raise StoredGraphIntegrityError(anchor_id)
                start_sequence = decoded.next_sequence
                start_revision_id = str(decoded.next_revision_id)
                start_boundary_child_revision_id = (
                    str(decoded.boundary_child_revision_id)
                    if decoded.boundary_child_revision_id is not None
                    else None
                )
                start_event_ordinal = decoded.event_ordinal

            del anchor_snapshot
            session.expunge(anchor)
            del anchor
            events, next_position = self._collect_shot_slot_events(
                session=session,
                document=document,
                shot_id=shot_id,
                slot=slot,
                start_sequence=start_sequence,
                start_revision_id=start_revision_id,
                start_boundary_child_revision_id=start_boundary_child_revision_id,
                start_event_ordinal=start_event_ordinal,
                limit=limit,
            )
            items = self._history_items(session, slot, events)
            next_cursor = (
                _encode_history_cursor(
                    _ShotSlotAssociationCursor(
                        project_id=project_id,
                        anchor_revision_id=anchor_revision_id,
                        anchor_sequence=anchor_sequence,
                        shot_id=shot_id,
                        slot=slot,
                        next_sequence=next_position.graph_revision_sequence,
                        next_revision_id=UUID(next_position.graph_revision_id),
                        boundary_child_revision_id=(
                            UUID(next_position.boundary_child_revision_id)
                            if next_position.boundary_child_revision_id is not None
                            else None
                        ),
                        event_ordinal=next_position.event_ordinal,
                    ),
                    self._cursor_secret,
                )
                if next_position is not None
                else None
            )
            return ShotSlotAssociationHistoryResponse(
                project_id=project_id,
                shot_id=shot_id,
                slot=slot,
                anchor_graph_revision_id=anchor_revision_id,
                anchor_graph_sequence=anchor_sequence,
                items=items,
                next_cursor=next_cursor,
            )

    @staticmethod
    def _shot_in_snapshot(
        snapshot: GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3,
        shot_id: UUID,
    ) -> Shot | None:
        if not isinstance(snapshot, (GraphSnapshotV2, GraphSnapshotV3)):
            return None
        return next((shot for shot in snapshot.shots if shot.id == shot_id), None)

    def _collect_shot_slot_events(
        self,
        *,
        session: Session,
        document: GraphDocumentRow,
        shot_id: UUID,
        slot: ShotSlot,
        start_sequence: int,
        start_revision_id: str,
        start_boundary_child_revision_id: str | None,
        start_event_ordinal: int,
        limit: int,
    ) -> tuple[
        list[_ShotSlotAssociationEvent],
        _ShotSlotAssociationPosition | None,
    ]:
        loaded_count = 0
        event_ordinal = start_event_ordinal
        collected: list[_ShotSlotAssociationEvent] = []

        def load_exact(revision_id: str, expected_sequence: int | None = None) -> GraphRevisionRow:
            nonlocal loaded_count
            if loaded_count >= _HISTORY_SCAN_LIMIT:
                raise StoredGraphIntegrityError(revision_id)
            row = self.repository.revision(session, revision_id)
            if row is None or row.graph_document_id != document.id:
                raise StoredGraphIntegrityError(revision_id)
            if expected_sequence is not None and row.sequence != expected_sequence:
                raise StoredGraphIntegrityError(revision_id)
            loaded_count += 1
            return row

        current_boundary_child_revision_id = start_boundary_child_revision_id
        if start_boundary_child_revision_id is not None:
            boundary_child = load_exact(start_boundary_child_revision_id)
            try:
                current = load_exact(start_revision_id, start_sequence)
            except StoredGraphIntegrityError:
                raise StoredGraphIntegrityError(boundary_child.id) from None
            if boundary_child.parent_revision_id != current.id:
                raise StoredGraphIntegrityError(boundary_child.id)
            session.expunge(boundary_child)
        else:
            current = load_exact(start_revision_id, start_sequence)

        parent: GraphRevisionRow | None = None
        if current.parent_revision_id is not None:
            parent = load_exact(current.parent_revision_id)
            if parent.sequence >= current.sequence:
                raise StoredGraphIntegrityError(current.id)
        elif current.sequence != 0:
            raise StoredGraphIntegrityError(current.id)

        current_snapshot = self._load_stored_snapshot(current)
        parent_snapshot = (
            self._load_stored_snapshot(parent) if parent is not None else None
        )

        while len(collected) < limit:
            revision_events, lifecycle_boundary = self._slot_events_for_transition(
                current=current,
                current_snapshot=current_snapshot,
                parent_snapshot=parent_snapshot,
                shot_id=shot_id,
                slot=slot,
            )
            if event_ordinal > len(revision_events):
                raise ShotSlotAssociationCursorInvalid
            remaining_capacity = limit - len(collected)
            selected = revision_events[
                event_ordinal : event_ordinal + remaining_capacity
            ]
            collected.extend(selected)
            consumed_ordinal = event_ordinal + len(selected)
            if len(collected) == limit:
                if consumed_ordinal < len(revision_events):
                    return collected, _ShotSlotAssociationPosition(
                        graph_revision_id=current.id,
                        graph_revision_sequence=current.sequence,
                        event_ordinal=consumed_ordinal,
                        boundary_child_revision_id=(
                            current_boundary_child_revision_id
                        ),
                    )
                if lifecycle_boundary or parent is None:
                    return collected, None
                return collected, _ShotSlotAssociationPosition(
                    graph_revision_id=parent.id,
                    graph_revision_sequence=parent.sequence,
                    event_ordinal=0,
                    boundary_child_revision_id=current.id,
                )
            if lifecycle_boundary:
                return collected, None
            if parent is None or parent_snapshot is None:
                raise StoredGraphIntegrityError(current.id)

            previous_current = current
            previous_current_id = previous_current.id
            current = parent
            current_snapshot = parent_snapshot
            parent = None
            parent_snapshot = None
            session.expunge(previous_current)
            del previous_current
            current_boundary_child_revision_id = previous_current_id
            event_ordinal = 0

            if current.parent_revision_id is None:
                if current.sequence != 0:
                    raise StoredGraphIntegrityError(current.id)
                continue
            if loaded_count >= _HISTORY_SCAN_LIMIT:
                return collected, _ShotSlotAssociationPosition(
                    graph_revision_id=current.id,
                    graph_revision_sequence=current.sequence,
                    event_ordinal=0,
                    boundary_child_revision_id=(
                        current_boundary_child_revision_id
                    ),
                )

            parent = load_exact(current.parent_revision_id)
            if parent.sequence >= current.sequence:
                raise StoredGraphIntegrityError(current.id)
            parent_snapshot = self._load_stored_snapshot(parent)

        return collected, None

    def _slot_events_for_transition(
        self,
        *,
        current: GraphRevisionRow,
        current_snapshot: GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3,
        parent_snapshot: GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3 | None,
        shot_id: UUID,
        slot: ShotSlot,
    ) -> tuple[list[_ShotSlotAssociationEvent], bool]:
        current_shot = self._shot_in_snapshot(current_snapshot, shot_id)
        if current_shot is None:
            raise StoredGraphIntegrityError(current.id)
        parent_shot = (
            self._shot_in_snapshot(parent_snapshot, shot_id)
            if parent_snapshot is not None
            else None
        )
        lifecycle_boundary = parent_shot is None
        events: list[_ShotSlotAssociationEvent] = []

        def add_event(
            action: ShotSlotAssociationAction,
            revision_id: UUID | None,
            reference_index: int | None = None,
        ) -> None:
            events.append(
                _ShotSlotAssociationEvent(
                    graph_revision_id=current.id,
                    graph_revision_sequence=current.sequence,
                    event_ordinal=len(events),
                    occurred_at=current.created_at,
                    action=action,
                    reference_index=reference_index,
                    artifact_revision_id=revision_id,
                )
            )

        if slot in {"image", "video"}:
            attribute = (
                "image_revision_id" if slot == "image" else "video_revision_id"
            )
            current_revision_id = getattr(current_shot, attribute)
            parent_revision_id = (
                getattr(parent_shot, attribute) if parent_shot is not None else None
            )
            if current_revision_id != parent_revision_id:
                if current_revision_id is None:
                    add_event("cleared", None)
                else:
                    add_event("associated", current_revision_id)
            return events, lifecycle_boundary

        current_references = current_shot.reference_revision_ids
        parent_references = (
            parent_shot.reference_revision_ids if parent_shot is not None else []
        )
        parent_set = set(parent_references)
        current_set = set(current_references)
        for index, revision_id in enumerate(current_references):
            if revision_id not in parent_set:
                add_event("associated", revision_id, index)
        for index, revision_id in enumerate(parent_references):
            if revision_id not in current_set:
                add_event("removed", revision_id, index)
        return events, lifecycle_boundary

    def _history_items(
        self,
        session: Session,
        slot: ShotSlot,
        events: list[_ShotSlotAssociationEvent],
    ) -> list[ShotSlotAssociationHistoryItem]:
        revision_ids = list(
            dict.fromkeys(
                str(event.artifact_revision_id)
                for event in events
                if event.artifact_revision_id is not None
            )
        )
        rows_by_id = self.repository.artifact_revision_rows(
            session,
            revision_ids,
        )
        expected_kind = "video" if slot == "video" else "image"
        items: list[ShotSlotAssociationHistoryItem] = []
        for event in events:
            artifact_response: ArtifactRevisionResponse | None = None
            if event.artifact_revision_id is not None:
                rows = rows_by_id.get(str(event.artifact_revision_id))
                if rows is None:
                    raise StoredGraphIntegrityError(event.graph_revision_id)
                revision, artifact, blob = rows
                artifact_response = self._artifact_history_response(
                    revision=revision,
                    artifact=artifact,
                    blob=blob,
                    expected_kind=expected_kind,
                    graph_revision_id=event.graph_revision_id,
                )
            items.append(
                ShotSlotAssociationHistoryItem(
                    graph_revision_id=UUID(event.graph_revision_id),
                    graph_revision_sequence=event.graph_revision_sequence,
                    event_ordinal=event.event_ordinal,
                    occurred_at=event.occurred_at,
                    action=event.action,
                    reference_index=event.reference_index,
                    artifact_revision=artifact_response,
                )
            )
        return items

    @staticmethod
    def _artifact_history_response(
        *,
        revision: ArtifactRevisionRow,
        artifact: ArtifactRow,
        blob: StoredBlobRow,
        expected_kind: str,
        graph_revision_id: str,
    ) -> ArtifactRevisionResponse:
        try:
            if (
                revision.discarded_at is not None
                or artifact.kind != expected_kind
                or blob.media_kind != expected_kind
            ):
                raise ValueError("artifact kind does not match the graph slot")
            return ArtifactRevisionResponse(
                revision_id=UUID(revision.id),
                artifact_id=UUID(revision.artifact_id),
                sequence=revision.sequence,
                media_kind=artifact.kind,
                media_type=blob.media_type,
                byte_size=blob.byte_size,
                display_filename=revision.display_filename,
                metadata=_ARTIFACT_METADATA_ADAPTER.validate_json(blob.metadata_json),
                source_kind=revision.source_kind,
                source_project_id=UUID(revision.source_project_id),
                origin_project_id=UUID(artifact.origin_project_id),
                created_at=revision.created_at,
            )
        except (TypeError, ValueError) as error:
            raise StoredGraphIntegrityError(graph_revision_id) from error

    def create_revision_for_approved_change_set_in_session(
        self,
        session: Session,
        project_id: UUID,
        *,
        base_revision_id: UUID,
        client_mutation_id: UUID,
        snapshot: GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3,
    ) -> GraphRevisionResponse:
        normalized_snapshot = normalize_to_v3(validate_graph(snapshot))
        normalized_json, normalized_hash = canonical_snapshot(normalized_snapshot)
        document = self.repository.document_for_project(session, str(project_id))
        if document is None:
            raise ProjectNotFound(str(project_id))
        if document.active_revision_id != str(base_revision_id):
            raise GraphRevisionConflict(document.active_revision_id or "")
        parent = self.repository.revision(session, str(base_revision_id))
        if parent is None or parent.graph_document_id != document.id:
            raise GraphRevisionNotFound(str(base_revision_id))
        self._load_stored_snapshot(parent)
        self._validate_artifact_references(session, normalized_snapshot)
        self._validate_resource_references(session, normalized_snapshot)
        revision = GraphRevisionRow(
            id=str(uuid4()),
            graph_document_id=document.id,
            parent_revision_id=parent.id,
            sequence=self.repository.next_sequence(session, document.id),
            client_mutation_id=str(client_mutation_id),
            snapshot_json=normalized_json,
            snapshot_hash=normalized_hash,
            created_at=utc_now(),
        )
        self.repository.add_revision(session, revision)
        session.flush()
        result = session.execute(
            update(GraphDocumentRow)
            .where(
                GraphDocumentRow.id == document.id,
                GraphDocumentRow.active_revision_id == parent.id,
            )
            .values(active_revision_id=revision.id)
        )
        if result.rowcount != 1:
            session.expire(document, ["active_revision_id"])
            session.refresh(document, ["active_revision_id"])
            raise GraphRevisionConflict(document.active_revision_id or "")
        document.active_revision_id = revision.id
        pointer = self.branches.selection(session, document.id)
        if pointer is None:
            raise StoredGraphIntegrityError(parent.id)
        head = self.branches.head(session, pointer.branch_id)
        if head is None:
            raise StoredGraphIntegrityError(parent.id)
        head_result = self.branches.advance_head(
            session,
            branch_id=head.branch_id,
            expected_revision_id=parent.id,
            expected_version=head.version,
            new_revision_id=revision.id,
            updated_at=utc_now(),
        )
        pointer_result = self.branches.move_selection(
            session,
            graph_document_id=document.id,
            expected_branch_id=pointer.branch_id,
            expected_revision_id=parent.id,
            expected_version=pointer.version,
            new_branch_id=pointer.branch_id,
            new_revision_id=revision.id,
            updated_at=utc_now(),
        )
        if not head_result.applied or not pointer_result.applied:
            raise GraphRevisionConflict(pointer_result.current.graph_revision_id)
        project = session.get(ProjectRow, str(project_id))
        if project is None:
            raise ProjectNotFound(str(project_id))
        project.updated_at = utc_now()
        return self._response(session, project_id, document, revision)

    def create_branch_revision_in_session(
        self,
        session: Session,
        project_id: UUID,
        *,
        parent_revision_id: UUID,
        client_mutation_id: UUID,
        snapshot: GraphSnapshotV1 | GraphSnapshotV2 | GraphSnapshotV3,
    ) -> GraphRevisionResponse:
        normalized_snapshot = normalize_to_v3(validate_graph(snapshot))
        normalized_json, normalized_hash = canonical_snapshot(normalized_snapshot)
        document = self.repository.document_for_project(session, str(project_id))
        if document is None:
            raise ProjectNotFound(str(project_id))
        parent = self.repository.revision(session, str(parent_revision_id))
        if parent is None or parent.graph_document_id != document.id:
            raise GraphRevisionNotFound(str(parent_revision_id))
        self._load_stored_snapshot(parent)
        self._validate_artifact_references(session, normalized_snapshot)
        self._validate_resource_references(session, normalized_snapshot)
        replay = self.repository.revision_for_mutation(
            session, document.id, str(client_mutation_id)
        )
        if replay is not None:
            if (
                replay.parent_revision_id != parent.id
                or replay.snapshot_json != normalized_json
                or replay.snapshot_hash != normalized_hash
            ):
                raise ClientMutationReuse(str(client_mutation_id))
            return self._response(session, project_id, document, replay)
        revision = GraphRevisionRow(
            id=str(uuid4()),
            graph_document_id=document.id,
            parent_revision_id=parent.id,
            sequence=self.repository.next_sequence(session, document.id),
            client_mutation_id=str(client_mutation_id),
            snapshot_json=normalized_json,
            snapshot_hash=normalized_hash,
            created_at=utc_now(),
        )
        self.repository.add_revision(session, revision)
        session.flush()
        project = session.get(ProjectRow, str(project_id))
        if project is None:
            raise ProjectNotFound(str(project_id))
        project.updated_at = utc_now()
        return self._response(session, project_id, document, revision)

    def create_revision(
        self, project_id: UUID, request: CreateGraphRevisionRequest
    ) -> GraphRevisionResponse:
        requested_snapshot = validate_graph(request.snapshot)
        original_json, original_hash = canonical_snapshot(requested_snapshot)
        normalized_snapshot = normalize_to_v3(requested_snapshot)
        normalized_json, normalized_hash = canonical_snapshot(normalized_snapshot)
        legacy_normalized: tuple[str, str] | None = None
        if isinstance(requested_snapshot, (GraphSnapshotV1, GraphSnapshotV2)):
            legacy_normalized = canonical_snapshot(normalize_to_v2(requested_snapshot))
        with self._write_gate:
            with self.session_factory.begin() as session:
                document = self.repository.document_for_project(session, str(project_id))
                if document is None:
                    raise ProjectNotFound(str(project_id))
                replay = self.repository.revision_for_mutation(
                    session, document.id, str(request.client_mutation_id)
                )
                if replay is not None:
                    replay_snapshot = self._load_stored_snapshot(replay)
                    matches_original = (
                        replay.snapshot_hash == original_hash
                        and replay.snapshot_json == original_json
                    )
                    matches_normalized = (
                        replay.snapshot_hash == normalized_hash
                        and replay.snapshot_json == normalized_json
                    )
                    matches_legacy_normalized = (
                        legacy_normalized is not None
                        and replay.snapshot_json == legacy_normalized[0]
                        and replay.snapshot_hash == legacy_normalized[1]
                    )
                    if (
                        replay.parent_revision_id != str(request.base_revision_id)
                        or not (
                            matches_original
                            or matches_normalized
                            or matches_legacy_normalized
                        )
                    ):
                        raise ClientMutationReuse(str(request.client_mutation_id))
                    if document.active_revision_id != replay.id:
                        raise GraphRevisionConflict(document.active_revision_id or "")
                    return self._response(
                        session,
                        project_id,
                        document,
                        replay,
                        snapshot=replay_snapshot,
                    )
                if document.active_revision_id != str(request.base_revision_id):
                    raise GraphRevisionConflict(document.active_revision_id or "")

                parent = self.repository.revision(session, str(request.base_revision_id))
                if parent is None or parent.graph_document_id != document.id:
                    raise GraphRevisionNotFound(str(request.base_revision_id))
                self._load_stored_snapshot(parent)
                self._validate_artifact_references(session, normalized_snapshot)
                self._validate_resource_references(session, normalized_snapshot)
                revision = GraphRevisionRow(
                    id=str(uuid4()),
                    graph_document_id=document.id,
                    parent_revision_id=parent.id,
                    sequence=self.repository.next_sequence(session, document.id),
                    client_mutation_id=str(request.client_mutation_id),
                    snapshot_json=normalized_json,
                    snapshot_hash=normalized_hash,
                    created_at=utc_now(),
                )
                self.repository.add_revision(session, revision)
                session.flush()
                result = session.execute(
                    update(GraphDocumentRow)
                    .where(
                        GraphDocumentRow.id == document.id,
                        GraphDocumentRow.active_revision_id == parent.id,
                    )
                    .values(active_revision_id=revision.id)
                )
                if result.rowcount != 1:
                    session.expire(document, ["active_revision_id"])
                    session.refresh(document, ["active_revision_id"])
                    raise GraphRevisionConflict(document.active_revision_id or "")
                document.active_revision_id = revision.id
                pointer = self.branches.selection(session, document.id)
                if pointer is None:
                    raise StoredGraphIntegrityError(parent.id)
                head = self.branches.head(session, pointer.branch_id)
                if head is None:
                    raise StoredGraphIntegrityError(parent.id)
                head_result = self.branches.advance_head(
                    session,
                    branch_id=head.branch_id,
                    expected_revision_id=parent.id,
                    expected_version=head.version,
                    new_revision_id=revision.id,
                    updated_at=utc_now(),
                )
                pointer_result = self.branches.move_selection(
                    session,
                    graph_document_id=document.id,
                    expected_branch_id=pointer.branch_id,
                    expected_revision_id=parent.id,
                    expected_version=pointer.version,
                    new_branch_id=pointer.branch_id,
                    new_revision_id=revision.id,
                    updated_at=utc_now(),
                )
                if not head_result.applied or not pointer_result.applied:
                    raise GraphRevisionConflict(pointer_result.current.graph_revision_id)
                project = session.get(ProjectRow, str(project_id))
                if project is None:
                    raise ProjectNotFound(str(project_id))
                project.updated_at = utc_now()
                return self._response(session, project_id, document, revision)

    @staticmethod
    def _validate_artifact_references(
        session: Session,
        snapshot: GraphSnapshotV2 | GraphSnapshotV3,
    ) -> None:
        expected_kinds: dict[str, set[str]] = {}

        def expect(revision_id: UUID | None, kind: str) -> None:
            if revision_id is not None:
                expected_kinds.setdefault(str(revision_id), set()).add(kind)

        for node in snapshot.nodes:
            if isinstance(node, MediaInputNodeV2):
                expect(node.data.artifact_revision_id, node.data.media_kind or "image")
        for shot in snapshot.shots:
            expect(shot.image_revision_id, "image")
            expect(shot.video_revision_id, "video")
            for revision_id in shot.reference_revision_ids:
                expect(revision_id, "image")
        if not expected_kinds:
            return

        observed: dict[str, str] = {}
        revision_ids = list(expected_kinds)
        for offset in range(0, len(revision_ids), 500):
            chunk = revision_ids[offset : offset + 500]
            rows = session.execute(
                select(ArtifactRevisionRow.id, ArtifactRow.kind)
                .join(ArtifactRow, ArtifactRow.id == ArtifactRevisionRow.artifact_id)
                .where(
                    ArtifactRevisionRow.id.in_(chunk),
                    ArtifactRevisionRow.discarded_at.is_(None),
                )
            )
            observed.update((revision_id, kind) for revision_id, kind in rows)
        for revision_id, kinds in expected_kinds.items():
            actual = observed.get(revision_id)
            if actual is None:
                raise GraphValidationError(f"artifact revision is missing: {revision_id}")
            if kinds != {actual}:
                raise GraphValidationError(
                    f"artifact revision kind does not match its graph slot: {revision_id}"
                )

    @staticmethod
    def _validate_resource_references(
        session: Session,
        snapshot: GraphSnapshotV3,
    ) -> None:
        if not snapshot.resource_bindings:
            return
        expected_kinds: dict[str, set[str]] = {}
        for binding in snapshot.resource_bindings:
            expected_kinds.setdefault(str(binding.resource_revision_id), set()).add(
                binding.resource_kind
            )

        observed: dict[str, str] = {}
        revision_ids = list(expected_kinds)
        for offset in range(0, len(revision_ids), 500):
            chunk = revision_ids[offset : offset + 500]
            rows = session.execute(
                select(ResourceRevisionRow.id, ResourceRow.kind)
                .join(ResourceRow, ResourceRow.id == ResourceRevisionRow.resource_id)
                .where(ResourceRevisionRow.id.in_(chunk))
            )
            observed.update((revision_id, kind) for revision_id, kind in rows)
        for revision_id, expected in expected_kinds.items():
            actual = observed.get(revision_id)
            if actual is None:
                raise GraphValidationError(f"resource revision is missing: {revision_id}")
            if expected != {actual}:
                raise GraphValidationError(
                    f"resource revision kind does not match its graph binding: {revision_id}"
                )

        attached_rows = session.execute(
            select(
                ResourceRevisionArtifactRow.resource_revision_id,
                ResourceRevisionArtifactRow.artifact_revision_id,
                ArtifactRevisionRow.id,
                ArtifactRevisionRow.discarded_at,
            )
            .outerjoin(
                ArtifactRevisionRow,
                ArtifactRevisionRow.id
                == ResourceRevisionArtifactRow.artifact_revision_id,
            )
            .where(ResourceRevisionArtifactRow.resource_revision_id.in_(revision_ids))
        )
        for resource_revision_id, artifact_revision_id, observed_id, discarded_at in attached_rows:
            if observed_id is None or discarded_at is not None:
                raise GraphValidationError(
                    "attached artifact revision is missing or unavailable: "
                    f"{resource_revision_id}/{artifact_revision_id}"
                )

    def update_viewport(self, project_id: UUID, viewport: ViewportState) -> ViewportState:
        with self._write_gate:
            with self.session_factory.begin() as session:
                document = self.repository.document_for_project(session, str(project_id))
                if document is None:
                    raise ProjectNotFound(str(project_id))
                row = self.repository.view_state(session, str(project_id))
                if row is None:
                    row = ProjectViewStateRow(
                        project_id=str(project_id),
                        viewport_json=viewport.model_dump_json(),
                        updated_at=utc_now(),
                    )
                    self.repository.add_view_state(session, row)
                else:
                    row.viewport_json = viewport.model_dump_json()
                    row.updated_at = utc_now()
        return viewport
