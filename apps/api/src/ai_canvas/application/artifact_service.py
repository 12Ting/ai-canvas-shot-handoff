from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Callable
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, TypeVar
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.errors import (
    ArtifactImportConflict,
    ArtifactImportFailed,
    ArtifactImportNotFound,
    ArtifactRevisionNotFound,
    ProjectNotFound,
)
from ai_canvas.application.write_gate import WriteGate
from ai_canvas.config import Settings
from ai_canvas.contracts.artifact import (
    ArtifactImportFailureCode,
    ArtifactImportSessionResponse,
    ArtifactMetadata,
    ArtifactRevisionResponse,
    CreateArtifactImportRequest,
    DiscardArtifactImportResultResponse,
)
from ai_canvas.contracts.graph import GraphSnapshotV2, GraphSnapshotV3, MediaInputNodeV2
from ai_canvas.domain.graph.snapshots import canonical_snapshot, parse_snapshot_json
from ai_canvas.domain.graph.validation import validate_graph
from ai_canvas.domain.media.probe import (
    MediaProbeError,
    MediaProbeResult,
    UnsupportedMediaError,
    probe_image,
    probe_video,
)
from ai_canvas.infrastructure.artifacts import (
    ArtifactStagingConflictError,
    ArtifactStore,
    ArtifactStoreError,
    PreparedPromotion,
    StagedArtifact,
    UploadDeadline,
    next_chunk_with_deadline,
    sanitize_filename,
)
from ai_canvas.infrastructure.db.models import (
    ArtifactRevisionRow,
    ArtifactRow,
    GraphDocumentRow,
    GraphRevisionRow,
    ImportSessionRow,
    ProjectRow,
    StoredBlobRow,
)
from ai_canvas.infrastructure.db.blob_lifecycle_repository import (
    BlobLifecycleConflict,
    BlobLifecycleRepository,
)
from ai_canvas.infrastructure.db.repositories import ArtifactRepository


_METADATA_ADAPTER = TypeAdapter(ArtifactMetadata)
_T = TypeVar("_T")
_RETRYABLE_FAILURES = frozenset(
    {
        ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
        ArtifactImportFailureCode.PROBE_TIMEOUT,
        ArtifactImportFailureCode.PROBE_UNAVAILABLE,
    }
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _run_sync_settled(operation: Callable[..., _T], *args: object) -> _T:
    """Do not let task cancellation outlive a storage/probe worker."""

    worker = asyncio.create_task(asyncio.to_thread(operation, *args))
    cancellation: asyncio.CancelledError | None = None
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
        except Exception:
            break
    if worker.cancelled():
        raise asyncio.CancelledError
    worker_error = worker.exception()
    if worker_error is not None:
        raise worker_error
    if cancellation is not None:
        raise cancellation
    return worker.result()


@dataclass(frozen=True, slots=True)
class ArtifactContent:
    body: bytes
    media_type: str
    byte_size: int
    display_filename: str


class ArtifactService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        settings: Settings,
        repository: ArtifactRepository | None = None,
        write_gate: WriteGate | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.repository = repository or ArtifactRepository()
        self._write_gate = write_gate or WriteGate()
        self._failpoint = failpoint or (lambda _name: None)

    def _store(self, kind: Literal["image", "video"] | None = None) -> ArtifactStore:
        if kind == "image":
            max_bytes = self.settings.artifact_image_max_bytes
        elif kind == "video":
            max_bytes = self.settings.artifact_video_max_bytes
        else:
            max_bytes = max(
                self.settings.artifact_image_max_bytes,
                self.settings.artifact_video_max_bytes,
            )
        return ArtifactStore(
            self.settings.data_dir,
            max_bytes=max_bytes,
            chunk_size=self.settings.artifact_upload_chunk_bytes,
        )

    def create_import(
        self,
        project_id: UUID,
        request: CreateArtifactImportRequest,
    ) -> ArtifactImportSessionResponse:
        proposed_import_id = uuid4()
        invalid_replacement = False
        with self._write_gate:
            with self.session_factory.begin() as session:
                if session.get(ProjectRow, str(project_id)) is None:
                    raise ProjectNotFound(str(project_id))
                existing = self.repository.import_session_for_client(
                    session,
                    str(project_id),
                    str(request.client_import_id),
                )
                if existing is not None:
                    if not self._same_declaration(existing, request):
                        raise ArtifactImportConflict(existing.id, "declaration_mismatch")
                    import_id = UUID(existing.id)
                else:
                    if request.replace_revision_id is not None:
                        try:
                            self._validate_replacement(
                                session,
                                project_id,
                                request.replace_revision_id,
                                request.requested_media_kind,
                                proposed_import_id,
                            )
                        except ArtifactImportConflict:
                            invalid_replacement = True
                    now = utc_now()
                    row = ImportSessionRow(
                        id=str(proposed_import_id),
                        source_project_id=str(project_id),
                        client_import_id=str(request.client_import_id),
                        replace_revision_id=(
                            str(request.replace_revision_id)
                            if request.replace_revision_id is not None
                            else None
                        ),
                        requested_media_kind=request.requested_media_kind,
                        original_filename=request.filename,
                        declared_content_type=request.declared_content_type,
                        expected_byte_size=request.expected_byte_size,
                        state="FAILED_FINAL" if invalid_replacement else "CREATED",
                        received_byte_size=0,
                        computed_sha256=None,
                        failure_code=(
                            ArtifactImportFailureCode.INCOMPATIBLE_DECLARATION.value
                            if invalid_replacement
                            else None
                        ),
                        created_at=now,
                        updated_at=now,
                    )
                    self.repository.add_import_session(session, row)
                    import_id = proposed_import_id
        if invalid_replacement:
            raise ArtifactImportConflict(str(import_id), "invalid_replacement")
        return self.get_import(project_id, import_id)

    def get_import(
        self,
        project_id: UUID,
        import_id: UUID,
    ) -> ArtifactImportSessionResponse:
        with self._write_gate:
            with self.session_factory() as session:
                row = self.repository.import_session(session, str(import_id))
                if (
                    row is None
                    or row.source_project_id != str(project_id)
                    or row.discarded_at is not None
                ):
                    raise ArtifactImportNotFound(str(import_id))
                return self._import_response(session, row)

    def get_revision(self, revision_id: UUID) -> ArtifactRevisionResponse:
        with self.session_factory() as session:
            row = self.repository.revision(session, str(revision_id))
            if row is None or row.discarded_at is not None:
                raise ArtifactRevisionNotFound(str(revision_id))
            return self._revision_response(session, row)

    def list_revisions(self, artifact_id: UUID) -> list[ArtifactRevisionResponse]:
        with self.session_factory() as session:
            return [
                self._revision_response_from_rows(revision, artifact, blob)
                for revision, artifact, blob in self.repository.revisions_for_artifact(
                    session,
                    str(artifact_id),
                )
            ]

    def get_content(self, revision_id: UUID) -> ArtifactContent:
        with self.session_factory() as session:
            revision = self.repository.revision(session, str(revision_id))
            if revision is None or revision.discarded_at is not None:
                raise ArtifactRevisionNotFound(str(revision_id))
            blob = self.repository.blob(session, revision.blob_sha256)
            if blob is None:
                raise ArtifactRevisionNotFound(str(revision_id))
            digest = blob.sha256
            byte_size = blob.byte_size
            media_type = blob.media_type
            display_filename = revision.display_filename
        body = self._store().read_blob(digest, byte_size)
        return ArtifactContent(
            body=body,
            media_type=media_type,
            byte_size=byte_size,
            display_filename=display_filename,
        )

    def discard_import_result(
        self,
        project_id: UUID,
        import_id: UUID,
        *,
        client_import_id: UUID,
        expected_revision_id: UUID | None,
    ) -> DiscardArtifactImportResultResponse:
        """Tombstone one import-owned result and safely clean its local bytes."""

        with self._write_gate:
            already_discarded = self._mark_import_result_discarded_locked(
                project_id,
                import_id,
                client_import_id=client_import_id,
                expected_revision_id=expected_revision_id,
            )
            cleanup = self._cleanup_discarded_import_locked(import_id)
        return DiscardArtifactImportResultResponse(
            outcome="already_discarded" if already_discarded else "discarded",
            content_cleanup=cleanup,
        )

    def _mark_import_result_discarded_locked(
        self,
        project_id: UUID,
        import_id: UUID,
        *,
        client_import_id: UUID,
        expected_revision_id: UUID | None,
    ) -> bool:
        with self.session_factory.begin() as session:
            row = self.repository.import_session(session, str(import_id))
            if row is None or row.source_project_id != str(project_id):
                raise ArtifactImportNotFound(str(import_id))
            if row.client_import_id != str(client_import_id):
                raise ArtifactImportConflict(row.id, "discard_precondition_failed")

            revision = self.repository.revision_for_import(session, row.id)
            if revision is not None and (
                revision.source_kind != "import"
                or revision.source_project_id != row.source_project_id
                or row.source_project_id != str(project_id)
                or revision.source_import_session_id != row.id
            ):
                raise ArtifactImportConflict(row.id, "discard_precondition_failed")
            if expected_revision_id is not None and (
                revision is None or revision.id != str(expected_revision_id)
            ):
                raise ArtifactImportConflict(row.id, "discard_precondition_failed")
            if row.discarded_at is not None:
                if revision is not None and revision.discarded_at is None:
                    raise ArtifactImportConflict(row.id, "invalid_state")
                return True
            if row.state not in {"COMPLETED", "FAILED_RETRYABLE", "FAILED_FINAL"}:
                raise ArtifactImportConflict(row.id, "invalid_state")
            if row.state == "COMPLETED" and revision is None:
                raise ArtifactImportConflict(row.id, "invalid_state")
            if revision is not None:
                if revision.discarded_at is not None:
                    raise ArtifactImportConflict(row.id, "invalid_state")
                self._assert_result_is_unreferenced(session, row, revision)

            now = utc_now()
            row.discarded_at = now
            row.discard_cleanup_pending = True
            row.updated_at = now
            if revision is not None:
                revision.discarded_at = now
                assert revision.blob_sha256 is not None
                BlobLifecycleRepository().recompute_for_sha(session, revision.blob_sha256)
            session.flush()
            return False

    def _assert_result_is_unreferenced(
        self,
        session: Session,
        import_row: ImportSessionRow,
        revision: ArtifactRevisionRow,
    ) -> None:
        replacing_import = session.scalar(
            select(ImportSessionRow.id).where(
                ImportSessionRow.id != import_row.id,
                ImportSessionRow.replace_revision_id == revision.id,
                ImportSessionRow.discarded_at.is_(None),
            )
        )
        if replacing_import is not None:
            raise ArtifactImportConflict(import_row.id, "result_referenced")

        revision_id = UUID(revision.id)
        for graph_revision in session.scalars(select(GraphRevisionRow)):
            try:
                snapshot = validate_graph(parse_snapshot_json(graph_revision.snapshot_json))
                canonical_json, canonical_hash = canonical_snapshot(snapshot)
            except (TypeError, ValueError) as error:
                raise ArtifactImportConflict(import_row.id, "result_referenced") from error
            if (
                canonical_json != graph_revision.snapshot_json
                or canonical_hash != graph_revision.snapshot_hash
            ):
                raise ArtifactImportConflict(import_row.id, "result_referenced")
            if not isinstance(snapshot, (GraphSnapshotV2, GraphSnapshotV3)):
                continue
            if any(
                isinstance(node, MediaInputNodeV2)
                and node.data.artifact_revision_id == revision_id
                for node in snapshot.nodes
            ) or any(
                shot.image_revision_id == revision_id
                or shot.video_revision_id == revision_id
                or revision_id in shot.reference_revision_ids
                for shot in snapshot.shots
            ):
                raise ArtifactImportConflict(import_row.id, "result_referenced")

    def _cleanup_discarded_import_locked(
        self,
        import_id: UUID,
    ) -> Literal[
        "removed",
        "not_present",
        "retained_shared",
        "not_applicable",
        "pending",
    ]:
        store = self._store()
        with self.session_factory() as session:
            row = self.repository.import_session(session, str(import_id))
            if row is None or row.discarded_at is None:
                raise ArtifactImportNotFound(str(import_id))
            revision = self.repository.revision_for_import(session, row.id)
            digest = revision.blob_sha256 if revision is not None else row.computed_sha256
            blob = self.repository.blob(session, digest) if digest is not None else None
            expected_size = (
                blob.byte_size
                if blob is not None
                else row.received_byte_size or row.expected_byte_size
            )
            validating_import = (
                session.scalar(
                    select(ImportSessionRow.id).where(
                        ImportSessionRow.id != row.id,
                        ImportSessionRow.discarded_at.is_(None),
                        ImportSessionRow.state == "VALIDATING",
                        ImportSessionRow.computed_sha256 == digest,
                    )
                )
                if digest is not None
                else None
            )

        staged = StagedArtifact(
            import_id=import_id,
            path=store.staging_path(import_id),
            sha256="0" * 64,
            byte_size=1,
        )
        try:
            store.discard(staged)
            if digest is None:
                cleanup = "not_applicable"
            elif revision is not None:
                # Formal managed content is retained until the reviewable B4
                # GC plan moves the whole SHA group into quarantine.
                cleanup = "retained_shared"
            elif validating_import is not None:
                return "pending"
            else:
                cleanup = "removed" if store.remove_blob(digest, expected_size) else "not_present"
        except (ArtifactStoreError, OSError, ValueError):
            return "pending"

        with self.session_factory.begin() as session:
            row = self.repository.import_session(session, str(import_id))
            if row is None or row.discarded_at is None:
                raise ArtifactImportNotFound(str(import_id))
            row.discard_cleanup_pending = False
            row.updated_at = utc_now()
        return cleanup

    def recover_startup(self) -> None:
        """Recover interrupted imports, then fail closed on referenced CAS damage."""

        self._recover_pending_discards()

        with self.session_factory() as session:
            interrupted_ids = list(
                session.scalars(
                    select(ImportSessionRow.id).where(
                        ImportSessionRow.discarded_at.is_(None),
                        ImportSessionRow.state.in_(("RECEIVING", "VALIDATING")),
                    )
                )
            )
        store = self._store()
        for raw_import_id in interrupted_ids:
            import_id = UUID(raw_import_id)
            store.discard(
                StagedArtifact(
                    import_id=import_id,
                    path=store.staging_path(import_id),
                    sha256="0" * 64,
                    byte_size=1,
                )
            )
        if interrupted_ids:
            with self._write_gate:
                with self.session_factory.begin() as session:
                    rows = list(
                        session.scalars(
                            select(ImportSessionRow).where(
                                ImportSessionRow.id.in_(interrupted_ids),
                                ImportSessionRow.discarded_at.is_(None),
                                ImportSessionRow.state.in_(("RECEIVING", "VALIDATING")),
                            )
                        )
                    )
                    now = utc_now()
                    for row in rows:
                        row.state = "FAILED_RETRYABLE"
                        row.failure_code = ArtifactImportFailureCode.UPLOAD_INTERRUPTED.value
                        row.updated_at = now

        self._recover_pending_discards()

        with self.session_factory() as session:
            referenced_blobs = list(
                session.execute(
                    select(StoredBlobRow.sha256, StoredBlobRow.byte_size)
                    .join(
                        ArtifactRevisionRow,
                        ArtifactRevisionRow.blob_sha256 == StoredBlobRow.sha256,
                    )
                    .where(ArtifactRevisionRow.discarded_at.is_(None))
                    .distinct()
                )
            )
        for digest, byte_size in referenced_blobs:
            store.verify_blob(digest, byte_size)

    def _recover_pending_discards(self) -> None:
        with self.session_factory() as session:
            pending_discard_ids = list(
                session.scalars(
                    select(ImportSessionRow.id).where(
                        ImportSessionRow.discarded_at.is_not(None),
                        ImportSessionRow.discard_cleanup_pending.is_(True),
                    )
                )
            )
        for raw_import_id in pending_discard_ids:
            with self._write_gate:
                self._cleanup_discarded_import_locked(UUID(raw_import_id))

    async def upload_content(
        self,
        project_id: UUID,
        import_id: UUID,
        chunks: AsyncIterator[bytes],
        *,
        content_length: int | None,
    ) -> ArtifactRevisionResponse:
        try:
            claimed, cleanup_stale_part = await _run_sync_settled(
                self._claim_upload,
                project_id,
                import_id,
            )
        except asyncio.CancelledError as cancellation:
            await self._record_failure_settled(
                import_id,
                ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
            )
            raise cancellation
        if claimed.state == "COMPLETED":
            try:
                return await self._replay_completed_upload(claimed, chunks, content_length)
            except ArtifactStoreError as error:
                raise ArtifactImportFailed(str(import_id), error.failure_code) from error
            except OSError as error:
                raise ArtifactImportFailed(
                    str(import_id),
                    ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
                ) from error
        store = self._store(claimed.requested_media_kind)
        if cleanup_stale_part:
            await self._discard_retry_staging(store, import_id)
        if content_length is not None and content_length != claimed.expected_byte_size:
            cancellation = await self._settle_failed_upload(
                store,
                None,
                import_id,
                ArtifactImportFailureCode.SIZE_MISMATCH,
            )
            if cancellation is not None:
                raise cancellation
            raise ArtifactImportFailed(str(import_id), ArtifactImportFailureCode.SIZE_MISMATCH)

        staged: StagedArtifact | None = None
        promotion_attempted = False
        try:
            staged = await store.stage_async_chunks(
                import_id,
                chunks,
                declared_size=claimed.expected_byte_size,
                deadline=UploadDeadline(
                    idle_timeout_seconds=self.settings.artifact_upload_idle_timeout_seconds,
                    total_timeout_seconds=self.settings.artifact_upload_total_timeout_seconds,
                ),
            )
            await _run_sync_settled(self._mark_validating, import_id, staged)
            probe = await _run_sync_settled(
                self._probe,
                claimed.requested_media_kind,
                staged,
            )
            self._validate_probe_declaration(claimed, probe)
            self._failpoint("before_promote")
            promotion_attempted = True
            promotion = await _run_sync_settled(store.prepare_promotion, staged)
            ticket_owned = True
            try:
                try:
                    revision_id = await _run_sync_settled(
                        self._publish_revision,
                        store,
                        promotion,
                        import_id,
                        staged,
                        probe,
                    )
                finally:
                    # _publish_revision always promotes or revokes before it exits.
                    ticket_owned = False
            finally:
                if ticket_owned:
                    await _run_sync_settled(store.revoke_promotion, promotion)
            self._failpoint("after_commit_before_response")
            return await _run_sync_settled(self.get_revision, revision_id)
        except SQLAlchemyError as error:
            return await self._recover_sqlalchemy_failure(
                store,
                staged if not promotion_attempted else None,
                import_id,
                error,
            )
        except ArtifactStagingConflictError:
            cancellation = await self._settle_failed_upload(
                store,
                None,
                import_id,
                ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
            )
            if cancellation is not None:
                raise cancellation
            raise ArtifactImportConflict(str(import_id), "upload_in_progress") from None
        except (ArtifactStoreError, MediaProbeError) as error:
            cancellation = await self._settle_failed_upload(
                store,
                staged if not promotion_attempted else None,
                import_id,
                error.failure_code,
            )
            if cancellation is not None:
                raise cancellation
            raise ArtifactImportFailed(str(import_id), error.failure_code) from error
        except ArtifactImportFailed as error:
            cancellation = await self._settle_failed_upload(
                store,
                staged if not promotion_attempted else None,
                import_id,
                error.failure_code,
            )
            if cancellation is not None:
                raise cancellation
            raise
        except ArtifactImportConflict as error:
            if error.reason.value == "invalid_replacement":
                cancellation = await self._settle_failed_upload(
                    store,
                    staged if not promotion_attempted else None,
                    import_id,
                    ArtifactImportFailureCode.INCOMPATIBLE_DECLARATION,
                )
                if cancellation is not None:
                    raise cancellation
            raise
        except BlobLifecycleConflict:
            cancellation = await self._settle_failed_upload(
                store,
                staged if not promotion_attempted else None,
                import_id,
                ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
            )
            if cancellation is not None:
                raise cancellation
            raise
        except asyncio.CancelledError as cancellation:
            await self._settle_failed_upload(
                store,
                staged if not promotion_attempted else None,
                import_id,
                ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
            )
            raise cancellation

    def _claim_upload(
        self,
        project_id: UUID,
        import_id: UUID,
    ) -> tuple[ArtifactImportSessionResponse, bool]:
        with self._write_gate:
            scope_verified = False
            completed_observed = False
            transition_attempted = False
            try:
                with self.session_factory.begin() as session:
                    row = self.repository.import_session(session, str(import_id))
                    if row is None or row.source_project_id != str(project_id):
                        raise ArtifactImportNotFound(str(import_id))
                    scope_verified = True
                    if row.discarded_at is not None:
                        raise ArtifactImportConflict(row.id, "invalid_state")
                    if row.state == "COMPLETED":
                        completed_observed = True
                        claimed = (self._import_response(session, row), False)
                    else:
                        if row.state in {"RECEIVING", "VALIDATING"}:
                            raise ArtifactImportConflict(row.id, "upload_in_progress")
                        if row.state == "FAILED_FINAL":
                            raise ArtifactImportConflict(row.id, "invalid_state")
                        if row.state not in {"CREATED", "FAILED_RETRYABLE"}:
                            raise ArtifactImportConflict(row.id, "invalid_state")
                        cleanup_stale_part = row.state == "FAILED_RETRYABLE"
                        transition_attempted = True
                        row.state = "RECEIVING"
                        row.received_byte_size = 0
                        row.computed_sha256 = None
                        row.failure_code = None
                        row.updated_at = utc_now()
                        claimed = (self._import_response(session, row), cleanup_stale_part)
                self._failpoint("after_claim_commit")
                return claimed
            except SQLAlchemyError as error:
                try:
                    recovered = self._reconcile_claim_sqlalchemy_failure_locked(
                        project_id,
                        import_id,
                        scope_verified=scope_verified,
                        completed_observed=completed_observed,
                        transition_attempted=transition_attempted,
                        original_error=error,
                    )
                except SQLAlchemyError as recovery_error:
                    raise recovery_error from error
                if recovered is not None:
                    return recovered
                raise ArtifactImportFailed(
                    str(import_id),
                    ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
                ) from error

    def _reconcile_claim_sqlalchemy_failure_locked(
        self,
        project_id: UUID,
        import_id: UUID,
        *,
        scope_verified: bool,
        completed_observed: bool,
        transition_attempted: bool,
        original_error: SQLAlchemyError,
    ) -> tuple[ArtifactImportSessionResponse, bool] | None:
        with self.session_factory.begin() as session:
            row = self.repository.import_session(session, str(import_id))
            if row is None or row.source_project_id != str(project_id):
                raise ArtifactImportNotFound(str(import_id)) from original_error
            if row.discarded_at is not None:
                raise ArtifactImportConflict(row.id, "invalid_state") from original_error
            if row.state == "COMPLETED":
                return self._import_response(session, row), False
            if completed_observed:
                raise ArtifactImportConflict(row.id, "invalid_state") from original_error
            if not scope_verified or not transition_attempted:
                if row.state in {"RECEIVING", "VALIDATING"}:
                    raise ArtifactImportConflict(
                        row.id,
                        "upload_in_progress",
                    ) from original_error
                if row.state not in {"CREATED", "FAILED_RETRYABLE"}:
                    raise ArtifactImportConflict(row.id, "invalid_state") from original_error
                return None
            if row.state not in {
                "CREATED",
                "RECEIVING",
                "VALIDATING",
                "FAILED_RETRYABLE",
            }:
                raise ArtifactImportConflict(row.id, "invalid_state") from original_error
            row.state = "FAILED_RETRYABLE"
            row.failure_code = ArtifactImportFailureCode.UPLOAD_INTERRUPTED.value
            row.updated_at = utc_now()
        return None

    async def _discard_retry_staging(self, store: ArtifactStore, import_id: UUID) -> None:
        stale = StagedArtifact(
            import_id=import_id,
            path=store.staging_path(import_id),
            sha256="0" * 64,
            byte_size=1,
        )
        try:
            await _run_sync_settled(store.discard, stale)
        except asyncio.CancelledError as cancellation:
            await self._record_failure_settled(
                import_id,
                ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
            )
            raise cancellation
        except (ArtifactStoreError, OSError) as error:
            cancellation = await self._record_failure_settled(
                import_id,
                ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
            )
            if cancellation is not None:
                raise cancellation
            raise ArtifactImportFailed(
                str(import_id),
                ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
            ) from error

    async def _settle_failed_upload(
        self,
        store: ArtifactStore,
        staged: StagedArtifact | None,
        import_id: UUID,
        failure_code: ArtifactImportFailureCode,
    ) -> asyncio.CancelledError | None:
        cancellation: asyncio.CancelledError | None = None
        if staged is not None:
            try:
                await _run_sync_settled(store.discard, staged)
            except asyncio.CancelledError as error:
                cancellation = error
            except (ArtifactStoreError, OSError):
                pass
        state_cancellation = await self._record_failure_settled(import_id, failure_code)
        return cancellation or state_cancellation

    async def _record_failure_settled(
        self,
        import_id: UUID,
        failure_code: ArtifactImportFailureCode,
    ) -> asyncio.CancelledError | None:
        try:
            await _run_sync_settled(self._mark_failed, import_id, failure_code)
        except asyncio.CancelledError as cancellation:
            return cancellation
        except SQLAlchemyError as error:
            try:
                await _run_sync_settled(self._mark_failed, import_id, failure_code)
            except asyncio.CancelledError as cancellation:
                return cancellation
            except SQLAlchemyError as recovery_error:
                raise recovery_error from error
        return None

    async def _recover_sqlalchemy_failure(
        self,
        store: ArtifactStore,
        staged: StagedArtifact | None,
        import_id: UUID,
        error: SQLAlchemyError,
    ) -> ArtifactRevisionResponse:
        try:
            completed = await _run_sync_settled(
                self._reconcile_sqlalchemy_failure,
                import_id,
            )
        except SQLAlchemyError as recovery_error:
            raise recovery_error from error
        if completed is not None:
            return completed
        if staged is not None:
            try:
                await _run_sync_settled(store.discard, staged)
            except (ArtifactStoreError, OSError):
                pass
        raise ArtifactImportFailed(
            str(import_id),
            ArtifactImportFailureCode.UPLOAD_INTERRUPTED,
        ) from error

    def _reconcile_sqlalchemy_failure(
        self,
        import_id: UUID,
    ) -> ArtifactRevisionResponse | None:
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = self.repository.import_session(session, str(import_id))
                if row is None:
                    raise ArtifactImportNotFound(str(import_id))
                if row.discarded_at is not None:
                    raise ArtifactImportConflict(row.id, "invalid_state")
                if row.state == "COMPLETED":
                    revision = self.repository.revision_for_import(session, row.id)
                    if revision is None:
                        raise ArtifactImportConflict(row.id, "invalid_state")
                    return self._revision_response(session, revision)
                if row.state not in {
                    "CREATED",
                    "RECEIVING",
                    "VALIDATING",
                    "FAILED_RETRYABLE",
                }:
                    raise ArtifactImportConflict(row.id, "invalid_state")
                row.state = "FAILED_RETRYABLE"
                row.failure_code = ArtifactImportFailureCode.UPLOAD_INTERRUPTED.value
                row.updated_at = utc_now()
                return None

    async def _replay_completed_upload(
        self,
        completed: ArtifactImportSessionResponse,
        chunks: AsyncIterator[bytes],
        content_length: int | None,
    ) -> ArtifactRevisionResponse:
        expected_revision = completed.artifact_revision
        if expected_revision is None:
            raise ArtifactImportConflict(str(completed.import_id), "invalid_state")
        deadline = UploadDeadline(
            idle_timeout_seconds=self.settings.artifact_upload_idle_timeout_seconds,
            total_timeout_seconds=self.settings.artifact_upload_total_timeout_seconds,
        )
        digest = hashlib.sha256()
        received = 0
        iterator = chunks.__aiter__()
        while True:
            try:
                chunk = await next_chunk_with_deadline(iterator, deadline)
            except StopAsyncIteration:
                break
            received += len(chunk)
            digest.update(chunk)
        stored_digest = await _run_sync_settled(
            self._completed_digest,
            completed.import_id,
        )
        matches = (
            received == completed.expected_byte_size
            and (content_length is None or content_length == received)
            and digest.hexdigest() == stored_digest
        )
        if not matches:
            raise ArtifactImportConflict(str(completed.import_id), "content_replay_mismatch")
        return expected_revision

    def _completed_digest(self, import_id: UUID) -> str | None:
        with self.session_factory() as session:
            row = self.repository.import_session(session, str(import_id))
            return (
                row.computed_sha256
                if row is not None and row.discarded_at is None
                else None
            )

    def _probe(
        self,
        kind: Literal["image", "video"],
        staged: StagedArtifact,
    ) -> MediaProbeResult:
        store = self._store(kind)
        try:
            return self._probe_as(kind, staged, store)
        except UnsupportedMediaError as requested_error:
            opposite: Literal["image", "video"] = "video" if kind == "image" else "image"
            try:
                return self._probe_as(opposite, staged, store)
            except MediaProbeError:
                raise requested_error

    def _probe_as(
        self,
        kind: Literal["image", "video"],
        staged: StagedArtifact,
        store: ArtifactStore,
    ) -> MediaProbeResult:
        if kind == "image":
            return probe_image(
                staged.path,
                staging_root=store.staging_root,
                max_pixels=self.settings.artifact_image_max_pixels,
                max_side=self.settings.artifact_image_max_side,
            )
        return probe_video(
            staged.path,
            staging_root=store.staging_root,
            ffprobe_path=self.settings.ffprobe_path,
            metadata_timeout=self.settings.artifact_probe_metadata_timeout_seconds,
            full_scan_timeout=self.settings.artifact_probe_full_scan_timeout_seconds,
            output_limit=self.settings.artifact_probe_output_limit_bytes,
            max_duration=self.settings.artifact_video_max_duration_seconds,
            max_side=self.settings.artifact_video_max_side,
            max_fps=self.settings.artifact_video_max_fps,
        )

    def _validate_probe_declaration(
        self,
        claimed: ArtifactImportSessionResponse,
        probe: MediaProbeResult,
    ) -> None:
        declared = claimed.declared_content_type
        compatible_types = {None, "application/octet-stream", probe.media_type}
        if probe.kind != claimed.requested_media_kind or declared not in compatible_types:
            raise ArtifactImportFailed(
                str(claimed.import_id),
                ArtifactImportFailureCode.INCOMPATIBLE_DECLARATION,
            )

    def _mark_validating(self, import_id: UUID, staged: StagedArtifact) -> None:
        with self._write_gate:
            with self.session_factory.begin() as session:
                row = self.repository.import_session(session, str(import_id))
                if row is None:
                    raise ArtifactImportNotFound(str(import_id))
                if row.discarded_at is not None or row.state != "RECEIVING":
                    raise ArtifactImportConflict(row.id, "invalid_state")
                row.state = "VALIDATING"
                row.received_byte_size = staged.byte_size
                row.computed_sha256 = staged.sha256
                row.failure_code = None
                row.updated_at = utc_now()

    def _mark_failed(
        self,
        import_id: UUID,
        failure_code: ArtifactImportFailureCode,
    ) -> None:
        state = "FAILED_RETRYABLE" if failure_code in _RETRYABLE_FAILURES else "FAILED_FINAL"
        with self._write_gate:
            digest: str | None = None
            with self.session_factory.begin() as session:
                row = self.repository.import_session(session, str(import_id))
                if row is None or row.state == "COMPLETED" or row.discarded_at is not None:
                    return
                digest = row.computed_sha256
                row.state = state
                row.failure_code = failure_code.value
                row.updated_at = utc_now()
            if digest is not None:
                self._retry_pending_discards_for_digest_locked(digest)

    def _retry_pending_discards_for_digest_locked(self, digest: str) -> None:
        with self.session_factory() as session:
            import_ids = list(
                session.scalars(
                    select(ImportSessionRow.id).where(
                        ImportSessionRow.computed_sha256 == digest,
                        ImportSessionRow.discarded_at.is_not(None),
                        ImportSessionRow.discard_cleanup_pending.is_(True),
                    )
                )
            )
        for raw_import_id in import_ids:
            self._cleanup_discarded_import_locked(UUID(raw_import_id))

    def _publish_revision(
        self,
        store: ArtifactStore,
        promotion: PreparedPromotion,
        import_id: UUID,
        staged: StagedArtifact,
        probe: MediaProbeResult,
    ) -> UUID:
        with self._write_gate:
            try:
                self._assert_blob_ingest_allowed(staged.sha256)
                self._assert_revision_publication_preconditions(
                    import_id,
                    staged,
                    probe,
                )
                store.promote_verified(promotion)
                self._failpoint("after_promote")
                return self._commit_revision(
                    import_id,
                    staged,
                    probe,
                    _gate_held=True,
                )
            except BaseException as error:
                try:
                    store.revoke_promotion(promotion)
                except BaseException as cleanup_error:
                    raise cleanup_error from error
                raise

    def _assert_revision_publication_preconditions(
        self,
        import_id: UUID,
        staged: StagedArtifact,
        probe: MediaProbeResult,
    ) -> None:
        with self.session_factory() as session:
            import_row = self.repository.import_session(session, str(import_id))
            if import_row is None:
                raise ArtifactImportNotFound(str(import_id))
            if (
                import_row.discarded_at is not None
                or import_row.state != "VALIDATING"
                or import_row.computed_sha256 != staged.sha256
            ):
                raise ArtifactImportConflict(import_row.id, "invalid_state")
            if import_row.replace_revision_id is not None:
                self._validate_replacement(
                    session,
                    UUID(import_row.source_project_id),
                    UUID(import_row.replace_revision_id),
                    probe.kind,
                    import_id,
                )

    def _commit_revision(
        self,
        import_id: UUID,
        staged: StagedArtifact,
        probe: MediaProbeResult,
        *,
        _gate_held: bool = False,
    ) -> UUID:
        gate = nullcontext() if _gate_held else self._write_gate
        with gate:
            with self.session_factory() as session:
                with session.begin():
                    import_row = self.repository.import_session(session, str(import_id))
                    if import_row is None:
                        raise ArtifactImportNotFound(str(import_id))
                    if (
                        import_row.discarded_at is not None
                        or import_row.state != "VALIDATING"
                        or import_row.computed_sha256 != staged.sha256
                    ):
                        raise ArtifactImportConflict(import_row.id, "invalid_state")
                    if import_row.replace_revision_id is not None:
                        self._validate_replacement(
                            session,
                            UUID(import_row.source_project_id),
                            UUID(import_row.replace_revision_id),
                            probe.kind,
                            import_id,
                        )
                    blob = self.repository.blob(session, staged.sha256)
                    metadata_json = probe.metadata.model_dump_json()
                    if blob is None:
                        blob = StoredBlobRow(
                            sha256=staged.sha256,
                            byte_size=staged.byte_size,
                            media_kind=probe.kind,
                            media_type=probe.media_type,
                            metadata_json=metadata_json,
                            created_at=utc_now(),
                        )
                        self.repository.add_blob(session, blob)
                        session.flush()
                    else:
                        BlobLifecycleRepository().reactivate_verified_purged_blob(
                            session,
                            staged.sha256,
                            verified_new_bytes=True,
                        )
                    if (
                        blob is not None
                        and (
                        blob.byte_size != staged.byte_size
                        or blob.media_kind != probe.kind
                        or blob.media_type != probe.media_type
                        or _METADATA_ADAPTER.validate_json(blob.metadata_json) != probe.metadata
                        )
                    ):
                        raise ArtifactImportFailed(
                            str(import_id),
                            ArtifactImportFailureCode.INCOMPATIBLE_DECLARATION,
                        )

                    if import_row.replace_revision_id is None:
                        artifact = ArtifactRow(
                            id=str(uuid4()),
                            origin_project_id=import_row.source_project_id,
                            kind=probe.kind,
                            created_at=utc_now(),
                        )
                        self.repository.add_artifact(session, artifact)
                        session.flush()
                        sequence = 1
                    else:
                        replaced = self.repository.revision(session, import_row.replace_revision_id)
                        if replaced is None:
                            raise ArtifactImportConflict(import_row.id, "invalid_replacement")
                        artifact = self.repository.artifact(session, replaced.artifact_id)
                        if artifact is None or artifact.kind != probe.kind:
                            raise ArtifactImportConflict(import_row.id, "invalid_replacement")
                        sequence = int(
                            session.scalar(
                                select(func.max(ArtifactRevisionRow.sequence)).where(
                                    ArtifactRevisionRow.artifact_id == artifact.id
                                )
                            )
                            or 0
                        ) + 1

                    now = utc_now()
                    revision_id = uuid4()
                    revision = ArtifactRevisionRow(
                        id=str(revision_id),
                        artifact_id=artifact.id,
                        sequence=sequence,
                        blob_sha256=staged.sha256,
                        source_kind="import",
                        source_project_id=import_row.source_project_id,
                        source_import_session_id=import_row.id,
                        display_filename=sanitize_filename(import_row.original_filename),
                        created_at=now,
                    )
                    self.repository.add_revision(session, revision)
                    session.flush()
                    BlobLifecycleRepository().recompute_for_sha(session, staged.sha256)
                    import_row.state = "COMPLETED"
                    import_row.received_byte_size = staged.byte_size
                    import_row.failure_code = None
                    import_row.updated_at = now
                    session.flush()
                    self._failpoint("before_db_commit")
                self._failpoint("after_db_commit")
                self._retry_pending_discards_for_digest_locked(staged.sha256)
                return revision_id

    def _assert_blob_ingest_allowed(self, sha256: str) -> None:
        with self.session_factory() as session:
            BlobLifecycleRepository.assert_ingest_reusable(
                session.get(StoredBlobRow, sha256),
                verified_new_bytes=True,
            )

    @staticmethod
    def _same_declaration(
        row: ImportSessionRow,
        request: CreateArtifactImportRequest,
    ) -> bool:
        return (
            row.client_import_id == str(request.client_import_id)
            and row.original_filename == request.filename
            and row.expected_byte_size == request.expected_byte_size
            and row.requested_media_kind == request.requested_media_kind
            and row.declared_content_type == request.declared_content_type
            and row.replace_revision_id
            == (str(request.replace_revision_id) if request.replace_revision_id is not None else None)
        )

    def _validate_replacement(
        self,
        session: Session,
        project_id: UUID,
        revision_id: UUID,
        requested_kind: Literal["image", "video"],
        import_id: UUID,
    ) -> None:
        replacement = self.repository.revision(session, str(revision_id))
        if replacement is None or replacement.discarded_at is not None:
            raise ArtifactImportConflict(str(import_id), "invalid_replacement")
        artifact = self.repository.artifact(session, replacement.artifact_id)
        if artifact is None or artifact.kind != requested_kind:
            raise ArtifactImportConflict(str(import_id), "invalid_replacement")
        document = session.scalar(
            select(GraphDocumentRow).where(GraphDocumentRow.project_id == str(project_id))
        )
        if document is None or document.active_revision_id is None:
            raise ArtifactImportConflict(str(import_id), "invalid_replacement")
        graph_revision = session.get(GraphRevisionRow, document.active_revision_id)
        if graph_revision is None:
            raise ArtifactImportConflict(str(import_id), "invalid_replacement")
        try:
            snapshot = validate_graph(parse_snapshot_json(graph_revision.snapshot_json))
            canonical_json, canonical_hash = canonical_snapshot(snapshot)
        except (TypeError, ValueError) as error:
            raise ArtifactImportConflict(str(import_id), "invalid_replacement") from error
        if (
            canonical_json != graph_revision.snapshot_json
            or canonical_hash != graph_revision.snapshot_hash
        ):
            raise ArtifactImportConflict(str(import_id), "invalid_replacement")
        referenced = isinstance(snapshot, (GraphSnapshotV2, GraphSnapshotV3)) and any(
            isinstance(node, MediaInputNodeV2)
            and node.data.artifact_revision_id == revision_id
            and node.data.media_kind == requested_kind
            for node in snapshot.nodes
        )
        if not referenced:
            raise ArtifactImportConflict(str(import_id), "invalid_replacement")

    def _revision_response(
        self,
        session: Session,
        revision: ArtifactRevisionRow,
    ) -> ArtifactRevisionResponse:
        if revision.discarded_at is not None:
            raise ArtifactRevisionNotFound(revision.id)
        artifact = self.repository.artifact(session, revision.artifact_id)
        blob = self.repository.blob(session, revision.blob_sha256)
        if artifact is None or blob is None:
            raise ArtifactRevisionNotFound(revision.id)
        return self._revision_response_from_rows(revision, artifact, blob)

    @staticmethod
    def _revision_response_from_rows(
        revision: ArtifactRevisionRow,
        artifact: ArtifactRow,
        blob: StoredBlobRow,
    ) -> ArtifactRevisionResponse:
        if revision.discarded_at is not None:
            raise ArtifactRevisionNotFound(revision.id)
        return ArtifactRevisionResponse(
            revision_id=UUID(revision.id),
            artifact_id=UUID(revision.artifact_id),
            sequence=revision.sequence,
            media_kind=artifact.kind,
            media_type=blob.media_type,
            byte_size=blob.byte_size,
            display_filename=revision.display_filename,
            metadata=_METADATA_ADAPTER.validate_json(blob.metadata_json),
            source_kind=revision.source_kind,
            source_project_id=UUID(revision.source_project_id),
            origin_project_id=UUID(artifact.origin_project_id),
            created_at=revision.created_at,
        )

    def _import_response(
        self,
        session: Session,
        row: ImportSessionRow,
    ) -> ArtifactImportSessionResponse:
        if row.discarded_at is not None:
            raise ArtifactImportNotFound(row.id)
        revision = self.repository.revision_for_import(session, row.id)
        if revision is not None and revision.discarded_at is not None:
            revision = None
        return ArtifactImportSessionResponse(
            import_id=UUID(row.id),
            source_project_id=UUID(row.source_project_id),
            client_import_id=UUID(row.client_import_id),
            replace_revision_id=(UUID(row.replace_revision_id) if row.replace_revision_id else None),
            requested_media_kind=row.requested_media_kind,
            original_filename=row.original_filename,
            declared_content_type=row.declared_content_type,
            expected_byte_size=row.expected_byte_size,
            state=row.state,
            received_byte_size=row.received_byte_size,
            failure_code=row.failure_code,
            created_at=row.created_at,
            updated_at=row.updated_at,
            artifact_revision=(
                self._revision_response(session, revision) if revision is not None else None
            ),
        )
