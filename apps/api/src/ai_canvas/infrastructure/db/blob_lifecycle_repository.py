from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select, union_all
from sqlalchemy.orm import Session

from ai_canvas.contracts.graph import GraphSnapshotV2, GraphSnapshotV3, MediaInputNodeV2
from ai_canvas.domain.graph.snapshots import canonical_snapshot, parse_snapshot_json
from ai_canvas.domain.graph.validation import validate_graph
from ai_canvas.infrastructure.db.models import (
    ArtifactRenditionRow,
    ArtifactRevisionRow,
    GraphRevisionRow,
    ImportSessionRow,
    LargeUploadSessionRow,
    MaintenanceJobRow,
    StoredBlobRow,
)


@dataclass(frozen=True, slots=True)
class BlobLogicalTarget:
    kind: str
    id: str


class BlobLifecycleConflict(RuntimeError):
    def __init__(self, sha256: str, state: str) -> None:
        self.sha256 = sha256
        self.state = state
        self.code = f"BLOB_{state}"
        super().__init__(self.code)


class BlobLifecycleRepository:
    """Derive StoredBlob reference counts and GC protection from authoritative rows."""

    _RENDITION_REF_STATES = ("READY", "ACTIVE")

    def logical_ref_count_for_sha(self, session: Session, sha256: str) -> int:
        revision_refs = int(
            session.scalar(
                select(func.count(ArtifactRevisionRow.id)).where(
                    ArtifactRevisionRow.storage_kind == "MANAGED_BLOB",
                    ArtifactRevisionRow.blob_sha256 == sha256,
                    ArtifactRevisionRow.discarded_at.is_(None),
                )
            )
            or 0
        )
        rendition_refs = int(
            session.scalar(
                select(func.count(ArtifactRenditionRow.id)).where(
                    ArtifactRenditionRow.blob_sha256 == sha256,
                    ArtifactRenditionRow.removed_at.is_(None),
                    ArtifactRenditionRow.state.in_(self._RENDITION_REF_STATES),
                )
            )
            or 0
        )
        return revision_refs + rendition_refs

    def recompute_for_sha(self, session: Session, sha256: str) -> int:
        blob = session.get(StoredBlobRow, sha256)
        if blob is None:
            return 0
        count = self.logical_ref_count_for_sha(session, sha256)
        blob.logical_ref_count = count
        return count

    def recompute_all(self, session: Session) -> dict[str, int]:
        revision_refs = select(ArtifactRevisionRow.blob_sha256.label("sha256")).where(
            ArtifactRevisionRow.storage_kind == "MANAGED_BLOB",
            ArtifactRevisionRow.blob_sha256.is_not(None),
            ArtifactRevisionRow.discarded_at.is_(None),
        )
        rendition_refs = select(ArtifactRenditionRow.blob_sha256.label("sha256")).where(
            ArtifactRenditionRow.blob_sha256.is_not(None),
            ArtifactRenditionRow.removed_at.is_(None),
            ArtifactRenditionRow.state.in_(self._RENDITION_REF_STATES),
        )
        references = union_all(revision_refs, rendition_refs).subquery()
        counts = (
            select(references.c.sha256, func.count().label("logical_ref_count"))
            .group_by(references.c.sha256)
            .subquery()
        )
        result: dict[str, int] = {}
        for blob, count in session.execute(
            select(
                StoredBlobRow,
                func.coalesce(counts.c.logical_ref_count, 0),
            ).outerjoin(counts, counts.c.sha256 == StoredBlobRow.sha256)
        ):
            logical_ref_count = int(count)
            blob.logical_ref_count = logical_ref_count
            result[blob.sha256] = logical_ref_count
        return result

    def targets_for_sha(self, session: Session, sha256: str) -> tuple[BlobLogicalTarget, ...]:
        targets = [
            BlobLogicalTarget("ARTIFACT_REVISION", row.id)
            for row in session.scalars(
                select(ArtifactRevisionRow)
                .where(
                    ArtifactRevisionRow.storage_kind == "MANAGED_BLOB",
                    ArtifactRevisionRow.blob_sha256 == sha256,
                )
                .order_by(ArtifactRevisionRow.id)
            )
        ]
        targets.extend(
            BlobLogicalTarget("ARTIFACT_RENDITION", row.id)
            for row in session.scalars(
                select(ArtifactRenditionRow)
                .where(ArtifactRenditionRow.blob_sha256 == sha256)
                .order_by(ArtifactRenditionRow.id)
            )
        )
        return tuple(sorted(targets, key=lambda item: (item.kind, item.id)))

    def frozen_gc_targets(
        self,
        session: Session,
        sha256: str,
    ) -> tuple[BlobLogicalTarget, ...] | None:
        """Return all tombstoned logical targets, or None when any target protects bytes."""

        return self.frozen_gc_targets_for_shas(session, (sha256,))[sha256]

    def frozen_gc_targets_for_shas(
        self,
        session: Session,
        sha256s: tuple[str, ...] | list[str],
    ) -> dict[str, tuple[BlobLogicalTarget, ...] | None]:
        """Batch the tombstone and formal-reference checks for several blob groups.

        GC planning can have many zero-ref rows.  The protection rules are global
        (a malformed graph or maintenance payload blocks collection), so evaluating
        the same rows once per digest turned a single plan into an N+1 query fan-out.
        Keep the single-digest API above, while deriving the exact same result for a
        set of SHA-256 values in a bounded number of database reads.
        """

        unique_sha256s = tuple(dict.fromkeys(sha256s))
        if not unique_sha256s:
            return {}

        revisions_by_sha: dict[str, list[ArtifactRevisionRow]] = defaultdict(list)
        for revision in session.scalars(
            select(ArtifactRevisionRow).where(
                ArtifactRevisionRow.storage_kind == "MANAGED_BLOB",
                ArtifactRevisionRow.blob_sha256.in_(unique_sha256s),
            )
        ):
            assert revision.blob_sha256 is not None
            revisions_by_sha[revision.blob_sha256].append(revision)

        renditions_by_sha: dict[str, list[ArtifactRenditionRow]] = defaultdict(list)
        for rendition in session.scalars(
            select(ArtifactRenditionRow).where(
                ArtifactRenditionRow.blob_sha256.in_(unique_sha256s)
            )
        ):
            assert rendition.blob_sha256 is not None
            renditions_by_sha[rendition.blob_sha256].append(rendition)

        has_targets = any(revisions_by_sha.values()) or any(renditions_by_sha.values())
        protected_revision_ids: set[str] | None = set()
        if has_targets:
            candidate_revision_ids = {
                revision.id
                for revisions in revisions_by_sha.values()
                for revision in revisions
            }
            protected_revision_ids = self._formal_protected_revision_ids(
                session,
                candidate_revision_ids,
            )

        result: dict[str, tuple[BlobLogicalTarget, ...] | None] = {}
        for sha256 in unique_sha256s:
            revisions = revisions_by_sha[sha256]
            renditions = renditions_by_sha[sha256]
            if not revisions and not renditions:
                result[sha256] = ()
                continue
            if any(revision.discarded_at is None for revision in revisions):
                result[sha256] = None
                continue
            if any(rendition.removed_at is None for rendition in renditions):
                result[sha256] = None
                continue
            if protected_revision_ids is None:
                result[sha256] = None
                continue
            if {revision.id for revision in revisions} & protected_revision_ids:
                result[sha256] = None
                continue
            targets = [
                BlobLogicalTarget("ARTIFACT_REVISION", revision.id)
                for revision in revisions
            ]
            targets.extend(
                BlobLogicalTarget("ARTIFACT_RENDITION", rendition.id)
                for rendition in renditions
            )
            result[sha256] = tuple(sorted(targets, key=lambda item: (item.kind, item.id)))
        return result

    @staticmethod
    def assert_ingest_reusable(
        blob: StoredBlobRow | None,
        *,
        verified_new_bytes: bool,
    ) -> None:
        if blob is None or blob.state == "ACTIVE":
            return
        if blob.state == "QUARANTINED" or not verified_new_bytes:
            raise BlobLifecycleConflict(blob.sha256, blob.state)

    def reactivate_verified_purged_blob(
        self,
        session: Session,
        sha256: str,
        *,
        verified_new_bytes: bool,
    ) -> StoredBlobRow | None:
        blob = session.get(StoredBlobRow, sha256)
        self.assert_ingest_reusable(blob, verified_new_bytes=verified_new_bytes)
        if blob is not None and blob.state == "PURGED":
            blob.state = "ACTIVE"
            blob.physical_bytes = blob.byte_size
            blob.quarantine_until = None
        return blob

    @staticmethod
    def _formal_protected_revision_ids(
        session: Session,
        revision_ids: set[str],
    ) -> set[str] | None:
        """Return candidate revisions held by formal references, or None on corruption."""

        protected: set[str] = set()
        for graph_revision in session.scalars(select(GraphRevisionRow)):
            try:
                snapshot = validate_graph(parse_snapshot_json(graph_revision.snapshot_json))
                canonical_json, canonical_hash = canonical_snapshot(snapshot)
            except (TypeError, ValueError):
                return None
            if (
                canonical_json != graph_revision.snapshot_json
                or canonical_hash != graph_revision.snapshot_hash
            ):
                return None
            if not isinstance(snapshot, (GraphSnapshotV2, GraphSnapshotV3)):
                continue
            referenced = {
                str(node.data.artifact_revision_id)
                for node in snapshot.nodes
                if isinstance(node, MediaInputNodeV2)
                and node.data.artifact_revision_id is not None
            }
            for shot in snapshot.shots:
                if shot.image_revision_id is not None:
                    referenced.add(str(shot.image_revision_id))
                if shot.video_revision_id is not None:
                    referenced.add(str(shot.video_revision_id))
                referenced.update(str(item) for item in shot.reference_revision_ids)
            protected.update(referenced & revision_ids)

        if revision_ids:
            protected.update(
                session.scalars(
                    select(ImportSessionRow.replace_revision_id).where(
                ImportSessionRow.replace_revision_id.in_(revision_ids),
                ImportSessionRow.discarded_at.is_(None),
                    )
                )
            )
            protected.update(
                session.scalars(
                    select(LargeUploadSessionRow.replace_revision_id).where(
                LargeUploadSessionRow.replace_revision_id.in_(revision_ids),
                LargeUploadSessionRow.state.in_(("CREATED", "RECEIVING", "FINALIZING")),
                    )
                )
            )
        for job in session.scalars(
            select(MaintenanceJobRow).where(MaintenanceJobRow.state.in_(("QUEUED", "RUNNING")))
        ):
            try:
                payload = json.loads(job.payload_json)
            except (TypeError, ValueError):
                return None
            if not isinstance(payload, dict):
                return None
            referenced = {
                str(value)
                for key, value in payload.items()
                if key.endswith("revision_id") and value is not None
            }
            protected.update(referenced & revision_ids)
        return protected
