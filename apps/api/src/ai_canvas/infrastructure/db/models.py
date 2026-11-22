from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_canvas.infrastructure.db.base import Base


class ProjectRow(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)


class HandoffProjectRow(Base):
    __tablename__ = "handoff_projects"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    root: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    document: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class GraphDocumentRow(Base):
    __tablename__ = "graph_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True)
    # Application-level CAS owns this pointer. Deliberately omit a database FK
    # so the document/revision relationship has one acyclic creation order.
    active_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class GraphRevisionRow(Base):
    __tablename__ = "graph_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["graph_document_id", "parent_revision_id"],
            ["graph_revisions.graph_document_id", "graph_revisions.id"],
        ),
        UniqueConstraint("graph_document_id", "id"),
        UniqueConstraint("graph_document_id", "client_mutation_id"),
        UniqueConstraint("graph_document_id", "sequence"),
        Index("ix_graph_revisions_document_created", "graph_document_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    graph_document_id: Mapped[str] = mapped_column(ForeignKey("graph_documents.id", ondelete="CASCADE"))
    parent_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sequence: Mapped[int] = mapped_column(nullable=False)
    client_mutation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class GraphBranchRow(Base):
    __tablename__ = "graph_branches"
    __table_args__ = (
        CheckConstraint(
            "length(name) BETWEEN 1 AND 100",
            name="ck_graph_branches_name_length",
        ),
        CheckConstraint(
            "length(normalized_name) BETWEEN 1 AND 100",
            name="ck_graph_branches_normalized_name_length",
        ),
        CheckConstraint(
            "kind IN ('MAIN', 'EXPERIMENT')",
            name="ck_graph_branches_kind",
        ),
        CheckConstraint(
            "(kind = 'MAIN' AND forked_from_branch_id IS NULL) OR "
            "(kind = 'EXPERIMENT' AND forked_from_branch_id IS NOT NULL)",
            name="ck_graph_branches_fork_shape",
        ),
        UniqueConstraint(
            "graph_document_id",
            "normalized_name",
            name="uq_graph_branch_name",
        ),
        Index(
            "uq_graph_branches_one_main",
            "graph_document_id",
            unique=True,
            sqlite_where=text("kind = 'MAIN'"),
        ),
        Index("ix_graph_branches_document_created", "graph_document_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    graph_document_id: Mapped[str] = mapped_column(
        ForeignKey("graph_documents.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    forked_from_branch_id: Mapped[str | None] = mapped_column(
        ForeignKey("graph_branches.id", ondelete="RESTRICT"), nullable=True
    )
    forked_from_revision_id: Mapped[str] = mapped_column(
        ForeignKey("graph_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class GraphBranchHeadRow(Base):
    __tablename__ = "graph_branch_heads"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_graph_branch_heads_version_positive"),
    )
    branch_id: Mapped[str] = mapped_column(
        ForeignKey("graph_branches.id", ondelete="CASCADE"), primary_key=True
    )
    graph_revision_id: Mapped[str] = mapped_column(
        ForeignKey("graph_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)


class GraphSelectionPointerRow(Base):
    __tablename__ = "graph_selection_pointers"
    __table_args__ = (
        CheckConstraint(
            "version > 0",
            name="ck_graph_selection_pointers_version_positive",
        ),
    )
    graph_document_id: Mapped[str] = mapped_column(
        ForeignKey("graph_documents.id", ondelete="CASCADE"), primary_key=True
    )
    branch_id: Mapped[str] = mapped_column(
        ForeignKey("graph_branches.id", ondelete="RESTRICT"), nullable=False
    )
    graph_revision_id: Mapped[str] = mapped_column(
        ForeignKey("graph_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)


class GraphPromotionRow(Base):
    __tablename__ = "graph_promotions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('PROMOTE', 'UNDO_PROMOTION')",
            name="ck_graph_promotions_kind",
        ),
        CheckConstraint(
            "source_branch_head_version > 0 AND target_branch_head_version > 0 "
            "AND expected_selection_version > 0",
            name="ck_graph_promotions_versions_positive",
        ),
        CheckConstraint(
            "state IN ('PENDING_APPROVAL', 'APPROVED', 'APPLIED', 'REJECTED', 'STALE')",
            name="ck_graph_promotions_state",
        ),
        UniqueConstraint(
            "project_id",
            "client_mutation_id",
            name="uq_graph_promotions_client_mutation",
        ),
        Index("ix_graph_promotions_project_created", "project_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    client_mutation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    source_branch_id: Mapped[str] = mapped_column(
        ForeignKey("graph_branches.id", ondelete="RESTRICT"), nullable=False
    )
    source_graph_revision_id: Mapped[str] = mapped_column(
        ForeignKey("graph_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    source_branch_head_version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_branch_id: Mapped[str] = mapped_column(
        ForeignKey("graph_branches.id", ondelete="RESTRICT"), nullable=False
    )
    target_graph_revision_id: Mapped[str] = mapped_column(
        ForeignKey("graph_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    target_branch_head_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_selection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    undo_of_promotion_id: Mapped[str | None] = mapped_column(
        ForeignKey("graph_promotions.id", ondelete="RESTRICT"), nullable=True
    )
    impact_json: Mapped[str] = mapped_column(Text, nullable=False)
    impact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    promotion_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_json: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(
        ForeignKey("approval_decisions.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(nullable=True)


class NodeMaterializationStateRow(Base):
    __tablename__ = "node_materialization_states"
    __table_args__ = (
        CheckConstraint(
            "state IN ('CURRENT', 'STALE')",
            name="ck_node_materialization_states_state",
        ),
    )
    graph_revision_id: Mapped[str] = mapped_column(
        ForeignKey("graph_revisions.id", ondelete="RESTRICT"), primary_key=True
    )
    node_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    last_materialized_graph_revision_id: Mapped[str] = mapped_column(
        ForeignKey("graph_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    reason_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)


class ProjectViewStateRow(Base):
    __tablename__ = "project_view_states"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    viewport_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)


class StoredBlobRow(Base):
    __tablename__ = "stored_blobs"
    __table_args__ = (
        CheckConstraint(
            "length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_stored_blobs_sha256_length",
        ),
        CheckConstraint("byte_size > 0", name="ck_stored_blobs_byte_size_positive"),
        CheckConstraint(
            "physical_bytes >= 0",
            name="ck_stored_blobs_physical_bytes_nonnegative",
        ),
        CheckConstraint(
            "logical_ref_count >= 0",
            name="ck_stored_blobs_logical_ref_count_nonnegative",
        ),
        CheckConstraint(
            "state IN ('ACTIVE', 'QUARANTINED', 'PURGED')",
            name="ck_stored_blobs_lifecycle_state",
        ),
        CheckConstraint(
            "(state = 'ACTIVE' AND physical_bytes = byte_size AND quarantine_until IS NULL) OR "
            "(state = 'QUARANTINED' AND physical_bytes = byte_size "
            "AND logical_ref_count = 0 AND quarantine_until IS NOT NULL) OR "
            "(state = 'PURGED' AND physical_bytes = 0 "
            "AND logical_ref_count = 0 AND quarantine_until IS NULL)",
            name="ck_stored_blobs_lifecycle_shape",
        ),
        CheckConstraint(
            "media_kind IN ('image', 'video')",
            name="ck_stored_blobs_media_kind",
        ),
        CheckConstraint(
            "length(media_type) > 0",
            name="ck_stored_blobs_media_type_nonempty",
        ),
        CheckConstraint(
            "length(metadata_json) > 0",
            name="ck_stored_blobs_metadata_json_nonempty",
        ),
        Index("ix_stored_blobs_media_kind_created", "media_kind", "created_at"),
    )
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    byte_size: Mapped[int] = mapped_column(nullable=False)
    physical_bytes: Mapped[int] = mapped_column(
        nullable=False,
        default=lambda context: context.get_current_parameters()["byte_size"],
    )
    logical_ref_count: Mapped[int] = mapped_column(nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    quarantine_until: Mapped[datetime | None] = mapped_column(nullable=True)
    media_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        CheckConstraint("kind IN ('image', 'video')", name="ck_artifacts_kind"),
        Index("ix_artifacts_origin_project_created", "origin_project_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    origin_project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class ImportSessionRow(Base):
    __tablename__ = "import_sessions"
    __table_args__ = (
        CheckConstraint(
            "length(client_import_id) = 36",
            name="ck_import_sessions_client_import_id_length",
        ),
        CheckConstraint(
            "replace_revision_id IS NULL OR length(replace_revision_id) = 36",
            name="ck_import_sessions_replace_revision_id_length",
        ),
        CheckConstraint(
            "requested_media_kind IN ('image', 'video')",
            name="ck_import_sessions_requested_media_kind",
        ),
        CheckConstraint(
            "expected_byte_size > 0",
            name="ck_import_sessions_expected_byte_size_positive",
        ),
        CheckConstraint(
            "received_byte_size >= 0",
            name="ck_import_sessions_received_byte_size_nonnegative",
        ),
        CheckConstraint(
            "state IN ('CREATED', 'RECEIVING', 'VALIDATING', 'COMPLETED', "
            "'FAILED_RETRYABLE', 'FAILED_FINAL')",
            name="ck_import_sessions_state",
        ),
        CheckConstraint(
            "computed_sha256 IS NULL OR length(computed_sha256) = 64",
            name="ck_import_sessions_computed_sha256_length",
        ),
        UniqueConstraint(
            "source_project_id",
            "client_import_id",
            name="uq_import_sessions_project_client_import",
        ),
        Index(
            "ix_import_sessions_source_project_state_updated",
            "source_project_id",
            "state",
            "updated_at",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    client_import_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Deliberately no FK: this avoids a cycle with artifact_revisions and is
    # validated against the active graph/revision by the application service.
    replace_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    requested_media_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    declared_content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expected_byte_size: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    received_byte_size: Mapped[int] = mapped_column(nullable=False)
    computed_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    discarded_at: Mapped[datetime | None] = mapped_column(nullable=True)
    discard_cleanup_pending: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)


class ArtifactRevisionRow(Base):
    __tablename__ = "artifact_revisions"
    __table_args__ = (
        CheckConstraint(
            "sequence > 0",
            name="ck_artifact_revisions_sequence_positive",
        ),
        CheckConstraint(
            "length(source_kind) > 0",
            name="ck_artifact_revisions_source_kind_nonempty",
        ),
        CheckConstraint(
            "length(display_filename) > 0",
            name="ck_artifact_revisions_display_filename_nonempty",
        ),
        CheckConstraint(
            "source_kind != 'import' OR source_import_session_id IS NOT NULL",
            name="ck_artifact_revisions_import_provenance",
        ),
        CheckConstraint(
            "(storage_kind = 'MANAGED_BLOB' AND blob_sha256 IS NOT NULL "
            "AND external_snapshot_id IS NULL) OR "
            "(storage_kind = 'EXTERNAL_REFERENCE' AND blob_sha256 IS NULL "
            "AND external_snapshot_id IS NOT NULL)",
            name="ck_artifact_revisions_storage_source",
        ),
        UniqueConstraint(
            "artifact_id",
            "sequence",
            name="uq_artifact_revisions_artifact_sequence",
        ),
        UniqueConstraint(
            "source_import_session_id",
            name="uq_artifact_revisions_source_import_session",
        ),
        Index("ix_artifact_revisions_artifact_created", "artifact_id", "created_at"),
        Index("ix_artifact_revisions_blob_sha256", "blob_sha256"),
        Index(
            "ix_artifact_revisions_source_project_created",
            "source_project_id",
            "created_at",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    blob_sha256: Mapped[str | None] = mapped_column(
        ForeignKey("stored_blobs.sha256", ondelete="RESTRICT"),
        nullable=True,
    )
    storage_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="MANAGED_BLOB")
    media_health: Mapped[str] = mapped_column(String(32), nullable=False, default="AVAILABLE")
    external_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("external_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # Open string by design: later AI/external sources must not require a table split.
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_import_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("import_sessions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    display_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    discarded_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class LargeUploadSessionRow(Base):
    __tablename__ = "large_upload_sessions"
    __table_args__ = (
        CheckConstraint("expected_byte_size > 0", name="ck_large_upload_sessions_expected_positive"),
        CheckConstraint("received_byte_size >= 0", name="ck_large_upload_sessions_received_nonnegative"),
        CheckConstraint("part_size > 0", name="ck_large_upload_sessions_part_positive"),
        UniqueConstraint(
            "source_project_id",
            "client_upload_id",
            name="uq_large_upload_sessions_project_client_upload",
        ),
        Index("ix_large_upload_sessions_state_updated", "state", "updated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    client_upload_id: Mapped[str] = mapped_column(String(36), nullable=False)
    replace_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    requested_media_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    declared_content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expected_byte_size: Mapped[int] = mapped_column(nullable=False)
    received_byte_size: Mapped[int] = mapped_column(nullable=False, default=0)
    part_size: Mapped[int] = mapped_column(nullable=False)
    staging_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    computed_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reservation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)


class LargeUploadPartRow(Base):
    __tablename__ = "large_upload_parts"
    __table_args__ = (
        CheckConstraint("part_number >= 0", name="ck_large_upload_parts_number_nonnegative"),
        CheckConstraint("start_byte >= 0", name="ck_large_upload_parts_start_nonnegative"),
        CheckConstraint("end_byte >= start_byte", name="ck_large_upload_parts_end_order"),
        CheckConstraint("byte_size > 0", name="ck_large_upload_parts_size_positive"),
        UniqueConstraint("upload_session_id", "part_number", name="uq_large_upload_parts_session_number"),
        UniqueConstraint("upload_session_id", "start_byte", name="uq_large_upload_parts_session_start"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    upload_session_id: Mapped[str] = mapped_column(
        ForeignKey("large_upload_sessions.id", ondelete="CASCADE"), nullable=False
    )
    part_number: Mapped[int] = mapped_column(nullable=False)
    start_byte: Mapped[int] = mapped_column(nullable=False)
    end_byte: Mapped[int] = mapped_column(nullable=False)
    byte_size: Mapped[int] = mapped_column(nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    stored_at: Mapped[datetime] = mapped_column(nullable=False)


class StorageReservationRow(Base):
    __tablename__ = "storage_reservations"
    __table_args__ = (
        CheckConstraint("reserved_bytes > 0", name="ck_storage_reservations_bytes_positive"),
        Index("ix_storage_reservations_state_expires", "state", "expires_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    reserved_bytes: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(nullable=True)


class MediaSessionRow(Base):
    __tablename__ = "media_sessions"
    __table_args__ = (Index("ix_media_sessions_expires", "absolute_expires_at"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)


class MediaSessionGrantRow(Base):
    __tablename__ = "media_session_grants"
    __table_args__ = (Index("ix_media_session_grants_resource", "resource_kind", "resource_id"),)
    media_session_id: Mapped[str] = mapped_column(
        ForeignKey("media_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    resource_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    granted_at: Mapped[datetime] = mapped_column(nullable=False)


class ExternalRootGrantRow(Base):
    __tablename__ = "external_root_grants"
    __table_args__ = (
        UniqueConstraint("subject", "canonical_path", name="uq_external_root_grants_subject_path"),
        Index("ix_external_root_grants_subject_active", "subject", "revoked_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_path: Mapped[str] = mapped_column(Text, nullable=False)
    volume_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    root_file_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ExternalSnapshotRow(Base):
    __tablename__ = "external_snapshots"
    __table_args__ = (Index("ix_external_snapshots_grant_confirmed", "root_grant_id", "confirmed_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    root_grant_id: Mapped[str] = mapped_column(
        ForeignKey("external_root_grants.id", ondelete="RESTRICT"), nullable=False
    )
    canonical_path: Mapped[str] = mapped_column(Text, nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(nullable=False)
    modified_at: Mapped[datetime] = mapped_column(nullable=False)
    media_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(nullable=False)


class ArtifactRenditionRow(Base):
    __tablename__ = "artifact_renditions"
    __table_args__ = (
        UniqueConstraint("source_revision_id", "profile", name="uq_artifact_renditions_source_profile"),
        Index("ix_artifact_renditions_source_state", "source_revision_id", "state"),
        Index("ix_artifact_renditions_blob_sha256", "blob_sha256"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_revision_id: Mapped[str] = mapped_column(
        ForeignKey("artifact_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    profile: Mapped[str] = mapped_column(String(32), nullable=False)
    blob_sha256: Mapped[str | None] = mapped_column(
        ForeignKey("stored_blobs.sha256", ondelete="RESTRICT"), nullable=True
    )
    byte_size: Mapped[int | None] = mapped_column(nullable=True)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class MaintenanceJobRow(Base):
    __tablename__ = "maintenance_jobs"
    __table_args__ = (
        UniqueConstraint("operation_key", name="uq_maintenance_jobs_operation_key"),
        Index("ix_maintenance_jobs_state_lease", "state", "lease_expires_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    operation_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(nullable=False, default=0)
    progress: Mapped[float] = mapped_column(nullable=False, default=0.0)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)


class GcPlanRow(Base):
    __tablename__ = "gc_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    summary_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    logical_reclaim_bytes: Mapped[int] = mapped_column(nullable=False)
    physical_reclaim_bytes: Mapped[int] = mapped_column(nullable=False)
    created_by_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    confirmed_by_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class GcPlanEntryRow(Base):
    __tablename__ = "gc_plan_entries"
    __table_args__ = (
        UniqueConstraint("gc_plan_id", "target_kind", "target_id", name="uq_gc_plan_entries_target"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    gc_plan_id: Mapped[str] = mapped_column(ForeignKey("gc_plans.id", ondelete="CASCADE"), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    logical_reclaim_bytes: Mapped[int] = mapped_column(nullable=False)
    physical_reclaim_bytes: Mapped[int] = mapped_column(nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)


class GcPlanTargetRow(Base):
    __tablename__ = "gc_plan_targets"
    __table_args__ = (
        UniqueConstraint(
            "gc_plan_entry_id",
            "target_kind",
            "target_id",
            name="uq_gc_plan_targets_target",
        ),
        Index("ix_gc_plan_targets_entry", "gc_plan_entry_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    gc_plan_entry_id: Mapped[str] = mapped_column(
        ForeignKey("gc_plan_entries.id", ondelete="CASCADE"), nullable=False
    )
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)


class QuarantineEntryRow(Base):
    __tablename__ = "quarantine_entries"
    __table_args__ = (
        CheckConstraint(
            "operation_state IN ('PREPARED_QUARANTINE', 'QUARANTINED', "
            "'PREPARED_RESTORE', 'RESTORED', 'PREPARED_PURGE', 'PURGED', "
            "'RECOVERY_REQUIRED')",
            name="ck_quarantine_entries_operation_state",
        ),
        Index("ix_quarantine_entries_until", "quarantine_until"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    original_path: Mapped[str] = mapped_column(Text, nullable=False)
    quarantine_path: Mapped[str] = mapped_column(Text, nullable=False)
    quarantined_at: Mapped[datetime] = mapped_column(nullable=False)
    quarantine_until: Mapped[datetime] = mapped_column(nullable=False)
    restored_at: Mapped[datetime | None] = mapped_column(nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(nullable=True)
    operation_state: Mapped[str] = mapped_column(String(32), nullable=False)
    operation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    operation_started_at: Mapped[datetime] = mapped_column(nullable=False)
    operation_updated_at: Mapped[datetime] = mapped_column(nullable=False)


class QuarantineTargetRow(Base):
    __tablename__ = "quarantine_targets"
    __table_args__ = (
        UniqueConstraint(
            "quarantine_entry_id",
            "target_kind",
            "target_id",
            name="uq_quarantine_targets_target",
        ),
        Index("ix_quarantine_targets_entry", "quarantine_entry_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    quarantine_entry_id: Mapped[str] = mapped_column(
        ForeignKey("quarantine_entries.id", ondelete="CASCADE"), nullable=False
    )
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)


class ExperienceEventRow(Base):
    __tablename__ = "experience_events"
    __table_args__ = (
        Index("ix_experience_events_project_position", "project_id", "stream_position"),
        Index(
            "ix_experience_events_project_correlation_position",
            "project_id",
            "correlation_id",
            "stream_position",
        ),
    )

    stream_position: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    occurred_at: Mapped[datetime] = mapped_column(nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    causation_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    envelope_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class EventConsumerCursorRow(Base):
    __tablename__ = "event_consumer_cursors"
    __table_args__ = (
        UniqueConstraint("project_id", "owner_id", "consumer_key", name="uq_event_cursor_owner"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    consumer_key: Mapped[str] = mapped_column(String(128), nullable=False)
    last_ack_stream_position: Mapped[int] = mapped_column(Integer, nullable=False)
    last_delivered_stream_position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)


class GraphChangeSetRow(Base):
    __tablename__ = "graph_change_sets"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "client_mutation_id",
            name="uq_graph_change_sets_client_mutation",
        ),
        Index("ix_graph_change_sets_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Immutable audit context. This deliberately has no FK because historical
    # graph-integrity probes must be able to remove a corrupt revision while
    # retaining the change-set record that refers to it.
    base_graph_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    client_mutation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    diff_json: Mapped[str] = mapped_column(Text, nullable=False)
    inverse_operations_json: Mapped[str] = mapped_column(Text, nullable=False)
    risk_json: Mapped[str] = mapped_column(Text, nullable=False)
    changeset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    applied_graph_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    application_target_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    impact_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    impact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_branch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    applied_branch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ApprovalDecisionRow(Base):
    __tablename__ = "approval_decisions"
    __table_args__ = (UniqueConstraint("subject_type", "subject_id", name="uq_approval_decision_subject"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=True
    )
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    subject_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class ControlConfigDocumentRow(Base):
    __tablename__ = "control_config_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    active_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class ControlConfigRevisionRow(Base):
    __tablename__ = "control_config_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "sequence", name="uq_control_config_revision_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("control_config_documents.id", ondelete="RESTRICT"), nullable=False
    )
    parent_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class ConfigChangeSetRow(Base):
    __tablename__ = "config_change_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    audit_project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    base_config_revision_id: Mapped[str] = mapped_column(
        ForeignKey("control_config_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    operations_json: Mapped[str] = mapped_column(Text, nullable=False)
    risk_json: Mapped[str] = mapped_column(Text, nullable=False)
    changeset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    applied_config_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ProviderRow(Base):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    adapter_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class ModelProfileRow(Base):
    __tablename__ = "model_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class ModelProfileRevisionRow(Base):
    __tablename__ = "model_profile_revisions"
    __table_args__ = (
        UniqueConstraint(
            "model_profile_id", "sequence", name="uq_model_profile_revision_sequence"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    model_profile_id: Mapped[str] = mapped_column(
        ForeignKey("model_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    declaration_json: Mapped[str] = mapped_column(Text, nullable=False)
    declaration_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class RunRequestRow(Base):
    __tablename__ = "run_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    graph_revision_id: Mapped[str] = mapped_column(
        ForeignKey("graph_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    graph_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    config_revision_id: Mapped[str] = mapped_column(
        ForeignKey("control_config_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    config_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    task_count: Mapped[int] = mapped_column(Integer, nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    model_profile_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_profile_revisions.id", ondelete="RESTRICT"), nullable=True
    )
    model_profile_revision_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    branch_head_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selection_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scope_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class EgressPlanRow(Base):
    __tablename__ = "egress_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_request_id: Mapped[str] = mapped_column(
        ForeignKey("run_requests.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    graph_revision_id: Mapped[str] = mapped_column(
        ForeignKey("graph_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    graph_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    config_revision_id: Mapped[str] = mapped_column(
        ForeignKey("control_config_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    config_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    data_classes_json: Mapped[str] = mapped_column(Text, nullable=False)
    task_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_bound: Mapped[int] = mapped_column(Integer, nullable=False)
    gpu_count: Mapped[int] = mapped_column(Integer, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False)
    model_profile_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_profile_revisions.id", ondelete="RESTRICT"), nullable=True
    )
    endpoint_identity_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preview_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    cost_bound_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class RunAuthorizationRow(Base):
    __tablename__ = "run_authorizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_request_id: Mapped[str] = mapped_column(
        ForeignKey("run_requests.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    approval_id: Mapped[str] = mapped_column(
        ForeignKey("approval_decisions.id", ondelete="RESTRICT"), nullable=False
    )
    graph_revision_id: Mapped[str] = mapped_column(
        ForeignKey("graph_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    graph_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    config_revision_id: Mapped[str] = mapped_column(
        ForeignKey("control_config_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    config_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    task_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_bound: Mapped[int] = mapped_column(Integer, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    egress_plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model_profile_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_profile_revisions.id", ondelete="RESTRICT"), nullable=True
    )
    model_profile_revision_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    branch_head_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selection_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scope_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    endpoint_identity_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cost_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class AgentProposalRow(Base):
    __tablename__ = "agent_proposals"
    __table_args__ = (
        UniqueConstraint("run_request_id", name="uq_agent_proposals_run_request"),
        Index("ix_agent_proposals_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    run_request_id: Mapped[str] = mapped_column(
        ForeignKey("run_requests.id", ondelete="RESTRICT"), nullable=False
    )
    model_profile_revision_id: Mapped[str] = mapped_column(
        ForeignKey("model_profile_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    branch_id: Mapped[str] = mapped_column(
        ForeignKey("graph_branches.id", ondelete="RESTRICT"), nullable=False
    )
    branch_head_version: Mapped[int] = mapped_column(Integer, nullable=False)
    selection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[str] = mapped_column(String(36), nullable=False)
    scope_json: Mapped[str] = mapped_column(Text, nullable=False)
    scope_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    provider_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    graph_change_set_id: Mapped[str | None] = mapped_column(
        ForeignKey("graph_change_sets.id", ondelete="RESTRICT"), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    dispatched_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class DryRunReceiptRow(Base):
    __tablename__ = "dry_run_receipts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_request_id: Mapped[str] = mapped_column(
        ForeignKey("run_requests.id", ondelete="RESTRICT"), nullable=False
    )
    run_authorization_id: Mapped[str] = mapped_column(
        ForeignKey("run_authorizations.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    terminal_state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class ResourceRow(Base):
    __tablename__ = "resources"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('STYLE', 'CHARACTER', 'PROMPT', 'MEDIA')",
            name="ck_resources_kind",
        ),
        CheckConstraint(
            "length(name) BETWEEN 1 AND 200",
            name="ck_resources_name_length",
        ),
        Index("ix_resources_kind_name", "kind", "name"),
        Index("ix_resources_origin_project_created", "origin_project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    origin_project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ResourceRevisionRow(Base):
    __tablename__ = "resource_revisions"
    __table_args__ = (
        CheckConstraint(
            "sequence > 0",
            name="ck_resource_revisions_sequence_positive",
        ),
        CheckConstraint(
            "schema_version = 1",
            name="ck_resource_revisions_schema_version",
        ),
        CheckConstraint(
            "length(payload_sha256) = 64 "
            "AND payload_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_resource_revisions_payload_sha256",
        ),
        CheckConstraint(
            "length(payload_json) > 0",
            name="ck_resource_revisions_payload_json_nonempty",
        ),
        CheckConstraint(
            "length(created_by_actor) > 0",
            name="ck_resource_revisions_actor_nonempty",
        ),
        UniqueConstraint(
            "resource_id",
            "sequence",
            name="uq_resource_revisions_resource_sequence",
        ),
        Index("ix_resource_revisions_resource_created", "resource_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    resource_id: Mapped[str] = mapped_column(
        ForeignKey("resources.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_actor: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class ResourceRevisionTagRow(Base):
    __tablename__ = "resource_revision_tags"
    __table_args__ = (
        CheckConstraint(
            "length(normalized_tag) BETWEEN 1 AND 100",
            name="ck_resource_revision_tags_normalized_length",
        ),
        CheckConstraint(
            "length(display_tag) BETWEEN 1 AND 100",
            name="ck_resource_revision_tags_display_length",
        ),
        UniqueConstraint(
            "resource_revision_id",
            "normalized_tag",
            name="uq_resource_revision_tags_revision_tag",
        ),
        Index(
            "ix_resource_revision_tags_normalized_revision",
            "normalized_tag",
            "resource_revision_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    resource_revision_id: Mapped[str] = mapped_column(
        ForeignKey("resource_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    normalized_tag: Mapped[str] = mapped_column(String(100), nullable=False)
    display_tag: Mapped[str] = mapped_column(String(100), nullable=False)


class ResourceRevisionArtifactRow(Base):
    __tablename__ = "resource_revision_artifacts"
    __table_args__ = (
        CheckConstraint(
            "length(slot) BETWEEN 1 AND 100",
            name="ck_resource_revision_artifacts_slot_length",
        ),
        CheckConstraint(
            "position >= 0",
            name="ck_resource_revision_artifacts_position_nonnegative",
        ),
        UniqueConstraint(
            "resource_revision_id",
            "slot",
            "position",
            name="uq_resource_revision_artifacts_revision_slot_position",
        ),
        Index(
            "ix_resource_revision_artifacts_artifact_revision",
            "artifact_revision_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    resource_revision_id: Mapped[str] = mapped_column(
        ForeignKey("resource_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    slot: Mapped[str] = mapped_column(String(100), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_revision_id: Mapped[str] = mapped_column(
        ForeignKey("artifact_revisions.id", ondelete="RESTRICT"), nullable=False
    )


class PromptProfileExtractionRow(Base):
    __tablename__ = "prompt_profile_extractions"
    __table_args__ = (
        UniqueConstraint(
            "run_request_id", name="uq_prompt_profile_extractions_request"
        ),
        Index(
            "ix_prompt_profile_extractions_project_created",
            "project_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    branch_id: Mapped[str] = mapped_column(
        ForeignKey("graph_branches.id", ondelete="RESTRICT"), nullable=False
    )
    branch_head_version: Mapped[int] = mapped_column(Integer, nullable=False)
    selection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    graph_revision_id: Mapped[str] = mapped_column(
        ForeignKey("graph_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    graph_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    config_revision_id: Mapped[str] = mapped_column(
        ForeignKey("control_config_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    config_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model_profile_revision_id: Mapped[str] = mapped_column(
        ForeignKey("model_profile_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    model_profile_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_node_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    provider_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    run_request_id: Mapped[str] = mapped_column(
        ForeignKey("run_requests.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    materialized_resource_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("resource_revisions.id", ondelete="RESTRICT"), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    dispatched_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class PromptProfileCertificationRow(Base):
    __tablename__ = "prompt_profile_certifications"
    __table_args__ = (
        UniqueConstraint(
            "resource_revision_id",
            name="uq_prompt_profile_certifications_revision",
        ),
        UniqueConstraint(
            "extraction_id",
            name="uq_prompt_profile_certifications_extraction",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    resource_revision_id: Mapped[str] = mapped_column(
        ForeignKey("resource_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    source_example_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_program_version: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_code_set_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_id: Mapped[str] = mapped_column(
        ForeignKey("prompt_profile_extractions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class PromptBatchRow(Base):
    __tablename__ = "prompt_batches"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "request_set_sha256", name="uq_prompt_batches_request_set"
        ),
        UniqueConstraint(
            "project_id",
            "client_mutation_id",
            name="uq_prompt_batches_client_mutation",
        ),
        Index("ix_prompt_batches_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    client_mutation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    create_request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    selection_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    selection_json: Mapped[str] = mapped_column(Text, nullable=False)
    binding_key: Mapped[str] = mapped_column(String(64), nullable=False)
    base_graph_revision_id: Mapped[str] = mapped_column(
        ForeignKey("graph_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    base_graph_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    branch_id: Mapped[str] = mapped_column(
        ForeignKey("graph_branches.id", ondelete="RESTRICT"), nullable=False
    )
    expected_branch_head_revision_id: Mapped[str] = mapped_column(
        ForeignKey("graph_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    expected_branch_head_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_selection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    application_target_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    new_branch_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_profile_revision_id: Mapped[str] = mapped_column(
        ForeignKey("model_profile_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    model_profile_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    config_revision_id: Mapped[str] = mapped_column(
        ForeignKey("control_config_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    config_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    item_set_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_set_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(
        ForeignKey("approval_decisions.id", ondelete="RESTRICT"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    final_decision_set_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    graph_change_set_id: Mapped[str | None] = mapped_column(
        ForeignKey("graph_change_sets.id", ondelete="RESTRICT"), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(nullable=True)


class PromptBatchItemRow(Base):
    __tablename__ = "prompt_batch_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "shot_id", name="uq_prompt_batch_items_shot"),
        UniqueConstraint(
            "batch_id", "prompt_node_id", name="uq_prompt_batch_items_prompt_node"
        ),
        Index("ix_prompt_batch_items_batch_order", "batch_id", "shot_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("prompt_batches.id", ondelete="RESTRICT"), nullable=False
    )
    shot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    shot_node_id: Mapped[str] = mapped_column(String(36), nullable=False)
    prompt_node_id: Mapped[str] = mapped_column(String(36), nullable=False)
    shot_order: Mapped[int] = mapped_column(Integer, nullable=False)
    base_document_json: Mapped[str] = mapped_column(Text, nullable=False)
    base_document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    protected_manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    protected_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    editable_manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    editable_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_profile_resource_revision_id: Mapped[str] = mapped_column(
        ForeignKey("resource_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    prompt_profile_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_profile_scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_profile_scope_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decision_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)


class PromptBatchItemAttemptRow(Base):
    __tablename__ = "prompt_batch_item_attempts"
    __table_args__ = (
        UniqueConstraint(
            "item_id",
            "attempt_number",
            name="uq_prompt_batch_item_attempt_number",
        ),
        UniqueConstraint(
            "run_request_id", name="uq_prompt_batch_item_attempt_request"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("prompt_batch_items.id", ondelete="RESTRICT"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("prompt_batch_item_attempts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    run_request_id: Mapped[str] = mapped_column(
        ForeignKey("run_requests.id", ondelete="RESTRICT"), nullable=False
    )
    provider_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    provider_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    compiled_document_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    compiled_document_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    dispatched_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class PromptBatchItemDecisionRow(Base):
    __tablename__ = "prompt_batch_item_decisions"
    __table_args__ = (
        UniqueConstraint(
            "item_id",
            "decision_sequence",
            name="uq_prompt_batch_item_decision_sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("prompt_batch_items.id", ondelete="RESTRICT"), nullable=False
    )
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("prompt_batch_item_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    expected_output_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class C4MutationReceiptRow(Base):
    __tablename__ = "c4_mutation_receipts"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "client_mutation_id", name="uq_c4_mutation_receipts_client"
        ),
        Index("ix_c4_mutation_receipts_subject", "subject_type", "subject_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    client_mutation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
