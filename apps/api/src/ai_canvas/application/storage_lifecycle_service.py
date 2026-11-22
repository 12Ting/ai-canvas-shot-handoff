from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from collections import defaultdict
from collections.abc import Callable, Iterable
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ai_canvas.application.write_gate import WriteGate
from ai_canvas.config import Settings
from ai_canvas.domain.media.storage_lifecycle import CapacityDecision, assess_capacity
from ai_canvas.infrastructure.artifacts import ArtifactStore
from ai_canvas.infrastructure.db.blob_lifecycle_repository import (
    BlobLifecycleRepository,
    BlobLogicalTarget,
)
from ai_canvas.infrastructure.db.models import (
    ArtifactRenditionRow,
    ArtifactRevisionRow,
    GcPlanEntryRow,
    GcPlanRow,
    GcPlanTargetRow,
    ImportSessionRow,
    QuarantineEntryRow,
    QuarantineTargetRow,
    StorageReservationRow,
    StoredBlobRow,
)
from ai_canvas.infrastructure.media.files import (
    BlobLocation,
    classify_blob_location,
    move_blob_to_quarantine,
    purge_quarantined_blob,
    restore_blob_from_quarantine,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class StorageReservation:
    id: str
    operation_type: str
    operation_id: str
    reserved_bytes: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class GcPlanEntry:
    id: str
    target_id: str
    blob_sha256: str
    target_ids: tuple[str, ...]
    reason: str
    logical_reclaim_bytes: int
    physical_reclaim_bytes: int


@dataclass(frozen=True, slots=True)
class GcPlan:
    id: str
    summary_sha256: str
    entries: tuple[GcPlanEntry, ...]


@dataclass(frozen=True, slots=True)
class StorageSummary:
    workspace_quota_bytes: int
    managed_blob_bytes: int
    staging_bytes: int
    quarantine_bytes: int
    active_reservation_bytes: int
    physical_free_bytes: int


@dataclass(frozen=True, slots=True)
class QuarantineResult:
    id: str
    target_id: str
    blob_sha256: str
    quarantine_path: str


@dataclass(frozen=True, slots=True)
class LifecycleReconciliationReport:
    recovered_operations: int
    recovery_required_ids: tuple[str, ...]
    orphan_blob_sha256s: tuple[str, ...]
    orphan_owned_bytes: int


class StorageCapacityExceeded(RuntimeError):
    def __init__(self, decision: CapacityDecision) -> None:
        if decision.code is None:
            raise ValueError("an allowed capacity decision cannot be raised")
        self.code = decision.code
        self.required_physical_free_bytes = decision.required_physical_free_bytes
        super().__init__(self.code)


class GcPlanNotFound(LookupError):
    pass


class GcPlanStale(RuntimeError):
    pass


class BlobLifecycleRecoveryRequired(RuntimeError):
    def __init__(self, quarantine_id: str) -> None:
        self.quarantine_id = quarantine_id
        super().__init__(f"blob lifecycle recovery required: {quarantine_id}")


class StorageLifecycleService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        settings: Settings,
        free_space_bytes: Callable[[], int] | None = None,
        write_gate: WriteGate | None = None,
        now: Callable[[], datetime] = utc_now,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self._free_space_bytes = free_space_bytes or self._disk_free_bytes
        self._write_gate = write_gate or WriteGate()
        self._now = now
        self._failpoint = failpoint or (lambda _name: None)
        self._blobs = BlobLifecycleRepository()

    def reserve(
        self,
        *,
        operation_type: str,
        operation_id: str,
        expected_added_bytes: int,
        ttl_seconds: float,
    ) -> StorageReservation:
        if not operation_type or not operation_id:
            raise ValueError("operation_type and operation_id are required")
        if expected_added_bytes <= 0 or ttl_seconds <= 0:
            raise ValueError("reservation bytes and TTL must be positive")
        now = self._now()
        with self._write_gate, self.session_factory.begin() as session:
            self._expire_locked(session, now)
            existing = session.scalar(
                select(StorageReservationRow).where(StorageReservationRow.operation_id == operation_id)
            )
            if existing is not None:
                return self._reservation(existing)
            decision = assess_capacity(
                logical_used_bytes=self._used_bytes(session),
                active_reservation_bytes=self._active_reservations(session),
                workspace_quota_bytes=self.settings.workspace_quota_bytes,
                expected_added_bytes=expected_added_bytes,
                physical_free_bytes=self._free_space_bytes(),
                reservation_multiplier=self.settings.media_reservation_multiplier,
                physical_free_floor_bytes=self.settings.media_physical_free_floor_bytes,
            )
            if not decision.allowed:
                raise StorageCapacityExceeded(decision)
            row = StorageReservationRow(
                id=str(uuid4()),
                operation_type=operation_type,
                operation_id=operation_id,
                reserved_bytes=expected_added_bytes,
                state="ACTIVE",
                created_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
                released_at=None,
            )
            session.add(row)
            session.flush()
            return self._reservation(row)

    def release(self, reservation_id: str) -> None:
        now = self._now()
        with self._write_gate, self.session_factory.begin() as session:
            row = session.get(StorageReservationRow, reservation_id)
            if row is None or row.state != "ACTIVE":
                return
            row.state = "RELEASED"
            row.released_at = now

    def storage_summary(self) -> StorageSummary:
        with self.session_factory() as session:
            managed_blob_bytes = self._state_bytes(session, "ACTIVE")
            quarantine_bytes = self._state_bytes(session, "QUARANTINED")
            active_reservation_bytes = self._active_reservations(session)
            transient_bytes = self._staging_bytes() + self._tracked_orphan_owned_bytes(session)
        return StorageSummary(
            workspace_quota_bytes=self.settings.workspace_quota_bytes,
            managed_blob_bytes=managed_blob_bytes,
            staging_bytes=transient_bytes,
            quarantine_bytes=quarantine_bytes,
            active_reservation_bytes=active_reservation_bytes,
            physical_free_bytes=self._free_space_bytes(),
        )

    def create_gc_plan(self, *, subject: str = "local") -> GcPlan:
        now = self._now()
        with self._write_gate, self.session_factory.begin() as session:
            self._blobs.recompute_all(session)
            entries = self._eligible_gc_entries(session)
            payload = [
                {
                    "blob_sha256": blob.sha256,
                    "targets": [{"kind": target.kind, "id": target.id} for target in targets],
                    "reason": "discarded_unreferenced_blob_group",
                    "logical_reclaim_bytes": blob.byte_size * len(targets),
                    "physical_reclaim_bytes": blob.physical_bytes,
                }
                for blob, targets in entries
            ]
            summary_sha256 = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            plan = GcPlanRow(
                id=str(uuid4()),
                summary_sha256=summary_sha256,
                logical_reclaim_bytes=sum(int(item["logical_reclaim_bytes"]) for item in payload),
                physical_reclaim_bytes=sum(int(item["physical_reclaim_bytes"]) for item in payload),
                created_by_subject=subject,
                created_at=now,
                confirmed_by_subject=None,
                confirmed_at=None,
                executed_at=None,
            )
            session.add(plan)
            session.flush()
            response_entries: list[GcPlanEntry] = []
            for (blob, targets), item in zip(entries, payload, strict=True):
                row = GcPlanEntryRow(
                    id=str(uuid4()),
                    gc_plan_id=plan.id,
                    target_kind="STORED_BLOB",
                    target_id=blob.sha256,
                    reason=str(item["reason"]),
                    logical_reclaim_bytes=int(item["logical_reclaim_bytes"]),
                    physical_reclaim_bytes=int(item["physical_reclaim_bytes"]),
                    outcome=None,
                )
                session.add(row)
                session.flush()
                for target in targets:
                    session.add(
                        GcPlanTargetRow(
                            id=str(uuid4()),
                            gc_plan_entry_id=row.id,
                            target_kind=target.kind,
                            target_id=target.id,
                        )
                    )
                response_entries.append(self._plan_entry(row, blob.sha256, targets))
            return GcPlan(plan.id, summary_sha256, tuple(response_entries))

    def confirm_gc_plan(
        self,
        plan_id: str,
        summary_sha256: str,
        *,
        subject: str = "local",
    ) -> tuple[QuarantineResult, ...]:
        now = self._now()
        prepared_ids: list[str] = []
        # Keep the shared write gate continuously from the committed intent through
        # validation, the filesystem move, and finalization. The database intent is
        # still committed before bytes move, while normal writers cannot enter the
        # prepared-to-move gap.
        with self._write_gate:
            with self.session_factory.begin() as session:
                plan = session.get(GcPlanRow, plan_id)
                if plan is None:
                    raise GcPlanNotFound(plan_id)
                if plan.summary_sha256 != summary_sha256:
                    raise GcPlanStale("GC plan summary no longer matches the user confirmation")
                if plan.executed_at is not None:
                    return ()
                plan.confirmed_by_subject = subject
                plan.confirmed_at = now
                for plan_entry in session.scalars(
                    select(GcPlanEntryRow).where(GcPlanEntryRow.gc_plan_id == plan.id)
                ):
                    blob = session.get(StoredBlobRow, plan_entry.target_id)
                    frozen = self._blobs.frozen_gc_targets(session, plan_entry.target_id)
                    planned = self._gc_targets(session, plan_entry.id)
                    if (
                        blob is None
                        or blob.state != "ACTIVE"
                        or not frozen
                        or tuple(frozen) != planned
                    ):
                        plan_entry.outcome = "SKIPPED_STALE"
                        continue
                    self._blobs.recompute_for_sha(session, blob.sha256)
                    if blob.logical_ref_count != 0:
                        plan_entry.outcome = "SKIPPED_STALE"
                        continue
                    quarantine_id = str(uuid4())
                    source = self._store().blob_path(blob.sha256)
                    destination = self._quarantine_path(quarantine_id)
                    quarantine = QuarantineEntryRow(
                        id=quarantine_id,
                        target_kind="STORED_BLOB",
                        target_id=blob.sha256,
                        original_path=str(source),
                        quarantine_path=str(destination),
                        quarantined_at=now,
                        quarantine_until=now
                        + timedelta(days=self.settings.media_quarantine_days),
                        restored_at=None,
                        purged_at=None,
                        operation_state="PREPARED_QUARANTINE",
                        operation_error=None,
                        operation_started_at=now,
                        operation_updated_at=now,
                    )
                    session.add(quarantine)
                    session.flush()
                    for target in frozen:
                        session.add(
                            QuarantineTargetRow(
                                id=str(uuid4()),
                                quarantine_entry_id=quarantine.id,
                                target_kind=target.kind,
                                target_id=target.id,
                            )
                        )
                    plan_entry.outcome = "PREPARED_QUARANTINE"
                    prepared_ids.append(quarantine.id)
                plan.executed_at = now

            results: list[QuarantineResult] = []
            for quarantine_id in prepared_ids:
                if self._execute_quarantine_intent(quarantine_id, _gate_held=True):
                    result = self._quarantine_result(quarantine_id)
                    if result is not None:
                        results.append(result)
            return tuple(results)

    def restore_quarantine(self, quarantine_id: str) -> bool:
        now = self._now()
        with self._write_gate, self.session_factory.begin() as session:
            entry = session.get(QuarantineEntryRow, quarantine_id)
            if entry is None or entry.operation_state in {"RESTORED", "PURGED"}:
                return False
            if entry.operation_state != "QUARANTINED":
                return False
            self._assert_capacity_locked(session, expected_added_bytes=0)
            entry.operation_state = "PREPARED_RESTORE"
            entry.operation_error = None
            entry.operation_started_at = now
            entry.operation_updated_at = now
        return self._execute_restore_intent(quarantine_id)

    def purge_due(self) -> int:
        now = self._now()
        prepared_ids: list[str] = []
        with self._write_gate, self.session_factory.begin() as session:
            for entry in session.scalars(
                select(QuarantineEntryRow).where(
                    QuarantineEntryRow.quarantine_until <= now,
                    QuarantineEntryRow.operation_state == "QUARANTINED",
                    QuarantineEntryRow.restored_at.is_(None),
                    QuarantineEntryRow.purged_at.is_(None),
                )
            ):
                entry.operation_state = "PREPARED_PURGE"
                entry.operation_error = None
                entry.operation_started_at = now
                entry.operation_updated_at = now
                prepared_ids.append(entry.id)
        return sum(1 for entry_id in prepared_ids if self._execute_purge_intent(entry_id))

    def reconcile_lifecycle_operations(self) -> LifecycleReconciliationReport:
        with self._write_gate, self.session_factory() as session:
            prepared = list(
                session.execute(
                    select(QuarantineEntryRow.id, QuarantineEntryRow.operation_state).where(
                        QuarantineEntryRow.operation_state.in_(
                            ("PREPARED_QUARANTINE", "PREPARED_RESTORE", "PREPARED_PURGE")
                        )
                    )
                )
            )
        recovered = 0
        for entry_id, operation_state in prepared:
            if operation_state == "PREPARED_QUARANTINE":
                recovered += int(self._execute_quarantine_intent(entry_id))
            elif operation_state == "PREPARED_RESTORE":
                recovered += int(self._execute_restore_intent(entry_id))
            else:
                recovered += int(self._execute_purge_intent(entry_id))
        with self._write_gate, self.session_factory() as session:
            recovery_required = tuple(
                session.scalars(
                    select(QuarantineEntryRow.id)
                    .where(QuarantineEntryRow.operation_state == "RECOVERY_REQUIRED")
                    .order_by(QuarantineEntryRow.id)
                )
            )
            orphan_blobs = tuple(
                blob.sha256
                for blob in session.scalars(
                    select(StoredBlobRow)
                    .where(StoredBlobRow.logical_ref_count == 0)
                    .order_by(StoredBlobRow.sha256)
                )
                if self._blobs.targets_for_sha(session, blob.sha256) == ()
            )
            orphan_owned_bytes = self._tracked_orphan_owned_bytes(session)
        return LifecycleReconciliationReport(
            recovered,
            recovery_required,
            orphan_blobs,
            orphan_owned_bytes,
        )

    def _execute_quarantine_intent(
        self,
        quarantine_id: str,
        *,
        _gate_held: bool = False,
    ) -> bool:
        gate = nullcontext() if _gate_held else self._write_gate
        with gate:
            entry, blob, cas_path, quarantine_path = self._load_intent(quarantine_id)
            if entry.operation_state != "PREPARED_QUARANTINE" or blob is None:
                return entry.operation_state == "QUARANTINED"
            if not self._paths_match(entry, cas_path, quarantine_path):
                self._mark_recovery_required_unlocked(quarantine_id, "persisted lifecycle path mismatch")
                return False
            if not self._preflight_file_action(quarantine_id, operation="QUARANTINE"):
                return False
            location = classify_blob_location(
                data_root=self.settings.data_dir,
                cas_path=cas_path,
                quarantine_path=quarantine_path,
            )
            if location is BlobLocation.CAS_ONLY:
                move_blob_to_quarantine(
                    data_root=self.settings.data_dir,
                    cas_path=cas_path,
                    quarantine_path=quarantine_path,
                )
                self._failpoint("after_quarantine_move_before_finalize")
                location = BlobLocation.QUARANTINE_ONLY
            if location is not BlobLocation.QUARANTINE_ONLY:
                self._mark_recovery_required_unlocked(
                    quarantine_id, f"quarantine:{location.value}"
                )
                return False
            now = self._now()
            with self.session_factory.begin() as session:
                stored_entry = session.get(QuarantineEntryRow, quarantine_id)
                stored_blob = (
                    session.get(StoredBlobRow, stored_entry.target_id)
                    if stored_entry is not None
                    else None
                )
                if (
                    stored_entry is None
                    or stored_blob is None
                    or stored_entry.operation_state != "PREPARED_QUARANTINE"
                ):
                    return stored_entry is not None and stored_entry.operation_state == "QUARANTINED"
                if not self._set_target_health(session, stored_entry.id, "QUARANTINED"):
                    self._mark_recovery_required_locked(
                        stored_entry,
                        "quarantine target is missing or malformed",
                    )
                    return False
                stored_blob.state = "QUARANTINED"
                stored_blob.logical_ref_count = 0
                stored_blob.physical_bytes = stored_blob.byte_size
                stored_blob.quarantine_until = stored_entry.quarantine_until
                stored_entry.operation_state = "QUARANTINED"
                stored_entry.operation_error = None
                stored_entry.operation_updated_at = now
                gc_entry = session.scalar(
                    select(GcPlanEntryRow).where(
                        GcPlanEntryRow.target_kind == "STORED_BLOB",
                        GcPlanEntryRow.target_id == stored_blob.sha256,
                        GcPlanEntryRow.outcome == "PREPARED_QUARANTINE",
                    )
                )
                if gc_entry is not None:
                    gc_entry.outcome = "QUARANTINED"
            return True

    def _execute_restore_intent(self, quarantine_id: str) -> bool:
        with self._write_gate:
            entry, blob, cas_path, quarantine_path = self._load_intent(quarantine_id)
            if entry.operation_state != "PREPARED_RESTORE" or blob is None:
                return entry.operation_state == "RESTORED"
            if not self._paths_match(entry, cas_path, quarantine_path):
                self._mark_recovery_required_unlocked(quarantine_id, "persisted lifecycle path mismatch")
                return False
            if not self._preflight_file_action(quarantine_id, operation="RESTORE"):
                return False
            location = classify_blob_location(
                data_root=self.settings.data_dir,
                cas_path=cas_path,
                quarantine_path=quarantine_path,
            )
            if location is BlobLocation.QUARANTINE_ONLY:
                restore_blob_from_quarantine(
                    data_root=self.settings.data_dir,
                    cas_path=cas_path,
                    quarantine_path=quarantine_path,
                )
                self._failpoint("after_restore_move_before_finalize")
                location = BlobLocation.CAS_ONLY
            if location is not BlobLocation.CAS_ONLY:
                self._mark_recovery_required_unlocked(quarantine_id, f"restore:{location.value}")
                return False
            now = self._now()
            with self.session_factory.begin() as session:
                stored_entry = session.get(QuarantineEntryRow, quarantine_id)
                stored_blob = (
                    session.get(StoredBlobRow, stored_entry.target_id)
                    if stored_entry is not None
                    else None
                )
                if (
                    stored_entry is None
                    or stored_blob is None
                    or stored_entry.operation_state != "PREPARED_RESTORE"
                ):
                    return stored_entry is not None and stored_entry.operation_state == "RESTORED"
                if not self._restore_targets(session, stored_entry.id, now=now):
                    self._mark_recovery_required_locked(
                        stored_entry,
                        "restore target is missing or malformed",
                    )
                    return False
                stored_blob.state = "ACTIVE"
                stored_blob.physical_bytes = stored_blob.byte_size
                stored_blob.quarantine_until = None
                self._blobs.recompute_for_sha(session, stored_blob.sha256)
                stored_entry.restored_at = now
                stored_entry.operation_state = "RESTORED"
                stored_entry.operation_error = None
                stored_entry.operation_updated_at = now
            return True

    def _execute_purge_intent(self, quarantine_id: str) -> bool:
        with self._write_gate:
            entry, blob, cas_path, quarantine_path = self._load_intent(quarantine_id)
            if entry.operation_state != "PREPARED_PURGE" or blob is None:
                return entry.operation_state == "PURGED"
            if not self._paths_match(entry, cas_path, quarantine_path):
                self._mark_recovery_required_unlocked(quarantine_id, "persisted lifecycle path mismatch")
                return False
            if not self._preflight_file_action(quarantine_id, operation="PURGE"):
                return False
            location = classify_blob_location(
                data_root=self.settings.data_dir,
                cas_path=cas_path,
                quarantine_path=quarantine_path,
            )
            if location is BlobLocation.QUARANTINE_ONLY:
                purge_quarantined_blob(
                    data_root=self.settings.data_dir,
                    cas_path=cas_path,
                    quarantine_path=quarantine_path,
                )
                self._failpoint("after_purge_unlink_before_finalize")
                location = BlobLocation.NEITHER
            if location is not BlobLocation.NEITHER:
                self._mark_recovery_required_unlocked(quarantine_id, f"purge:{location.value}")
                return False
            now = self._now()
            with self.session_factory.begin() as session:
                stored_entry = session.get(QuarantineEntryRow, quarantine_id)
                stored_blob = (
                    session.get(StoredBlobRow, stored_entry.target_id)
                    if stored_entry is not None
                    else None
                )
                if (
                    stored_entry is None
                    or stored_blob is None
                    or stored_entry.operation_state != "PREPARED_PURGE"
                ):
                    return stored_entry is not None and stored_entry.operation_state == "PURGED"
                if not self._set_target_health(session, stored_entry.id, "PURGED"):
                    self._mark_recovery_required_locked(
                        stored_entry,
                        "purge target is missing or malformed",
                    )
                    return False
                stored_blob.state = "PURGED"
                stored_blob.physical_bytes = 0
                stored_blob.logical_ref_count = 0
                stored_blob.quarantine_until = None
                stored_entry.purged_at = now
                stored_entry.operation_state = "PURGED"
                stored_entry.operation_error = None
                stored_entry.operation_updated_at = now
            return True

    def _load_intent(
        self, quarantine_id: str
    ) -> tuple[QuarantineEntryRow, StoredBlobRow | None, Path, Path]:
        with self.session_factory() as session:
            entry = session.get(QuarantineEntryRow, quarantine_id)
            if entry is None:
                raise LookupError(quarantine_id)
            blob = session.get(StoredBlobRow, entry.target_id)
            cas_path = self._store().blob_path(entry.target_id)
            quarantine_path = self._quarantine_path(entry.id)
            session.expunge(entry)
            if blob is not None:
                session.expunge(blob)
            return entry, blob, cas_path, quarantine_path

    @staticmethod
    def _paths_match(entry: QuarantineEntryRow, cas_path: Path, quarantine_path: Path) -> bool:
        return Path(entry.original_path) == cas_path and Path(entry.quarantine_path) == quarantine_path

    def _preflight_file_action(self, quarantine_id: str, *, operation: str) -> bool:
        expected_entry_state = f"PREPARED_{operation}"
        expected_blob_state = "ACTIVE" if operation == "QUARANTINE" else "QUARANTINED"
        with self.session_factory.begin() as session:
            entry = session.get(QuarantineEntryRow, quarantine_id)
            blob = (
                session.get(StoredBlobRow, entry.target_id)
                if entry is not None
                else None
            )
            if entry is None or blob is None:
                if entry is not None:
                    self._mark_recovery_required_locked(entry, "lifecycle blob is missing")
                return False
            if (
                entry.operation_state != expected_entry_state
                or entry.target_kind != "STORED_BLOB"
                or blob.state != expected_blob_state
            ):
                self._mark_recovery_required_locked(
                    entry,
                    f"{operation.lower()} lifecycle state changed before file action",
                )
                return False

            ref_count = self._blobs.logical_ref_count_for_sha(session, blob.sha256)
            # ACTIVE can faithfully persist the freshly derived count. A quarantined
            # row is constrained to zero, so an unexpected live ref is recorded on
            # the retryable intent without creating another invalid database state.
            if blob.state == "ACTIVE" or ref_count == 0:
                blob.logical_ref_count = ref_count
            if ref_count != 0:
                self._mark_retryable_intent_locked(
                    entry,
                    f"{operation.lower()} reference set became live; retry after references clear",
                )
                return False

            frozen = self._blobs.frozen_gc_targets(session, blob.sha256)
            if frozen is None:
                self._mark_retryable_intent_locked(
                    entry,
                    f"{operation.lower()} reference protection changed; retry after references clear",
                )
                return False
            targets = self._quarantine_targets(session, entry.id)
            if not targets or tuple(frozen) != targets:
                self._mark_recovery_required_locked(
                    entry,
                    f"{operation.lower()} frozen target mapping is missing or changed",
                )
                return False
            if not self._targets_are_ready_for_file_action(
                session,
                targets,
                operation=operation,
            ):
                self._mark_recovery_required_locked(
                    entry,
                    f"{operation.lower()} target tombstone or health is invalid",
                )
                return False
            entry.operation_error = None
            entry.operation_updated_at = self._now()
            return True

    @staticmethod
    def _targets_are_ready_for_file_action(
        session: Session,
        targets: tuple[BlobLogicalTarget, ...],
        *,
        operation: str,
    ) -> bool:
        expect_quarantined_health = operation != "QUARANTINE"
        for target in targets:
            if target.kind == "ARTIFACT_REVISION":
                revision = session.get(ArtifactRevisionRow, target.id)
                if revision is None or revision.discarded_at is None:
                    return False
                if expect_quarantined_health and revision.media_health != "QUARANTINED":
                    return False
            elif target.kind == "ARTIFACT_RENDITION":
                rendition = session.get(ArtifactRenditionRow, target.id)
                if rendition is None or rendition.removed_at is None:
                    return False
                if expect_quarantined_health and rendition.state != "QUARANTINED":
                    return False
            else:
                return False
        return True

    def _mark_recovery_required_unlocked(self, quarantine_id: str, error: str) -> None:
        with self.session_factory.begin() as session:
            entry = session.get(QuarantineEntryRow, quarantine_id)
            if entry is not None:
                self._mark_recovery_required_locked(entry, error)

    def _mark_recovery_required_locked(self, entry: QuarantineEntryRow, error: str) -> None:
        entry.operation_state = "RECOVERY_REQUIRED"
        entry.operation_error = error
        entry.operation_updated_at = self._now()

    def _mark_retryable_intent_locked(self, entry: QuarantineEntryRow, error: str) -> None:
        entry.operation_error = error
        entry.operation_updated_at = self._now()

    def _set_target_health(self, session: Session, quarantine_id: str, health: str) -> bool:
        targets = self._quarantine_targets(session, quarantine_id)
        resolved: list[tuple[str, ArtifactRevisionRow | ArtifactRenditionRow]] = []
        if not targets:
            return False
        for target in targets:
            if target.kind == "ARTIFACT_REVISION":
                revision = session.get(ArtifactRevisionRow, target.id)
                if revision is None:
                    return False
                resolved.append((target.kind, revision))
            elif target.kind == "ARTIFACT_RENDITION":
                rendition = session.get(ArtifactRenditionRow, target.id)
                if rendition is None:
                    return False
                resolved.append((target.kind, rendition))
            else:
                return False
        for kind, row in resolved:
            if kind == "ARTIFACT_REVISION":
                assert isinstance(row, ArtifactRevisionRow)
                row.media_health = health
            else:
                assert isinstance(row, ArtifactRenditionRow)
                row.state = health
                row.updated_at = self._now()
        return True

    def _restore_targets(self, session: Session, quarantine_id: str, *, now: datetime) -> bool:
        targets = self._quarantine_targets(session, quarantine_id)
        revisions: list[tuple[ArtifactRevisionRow, ImportSessionRow | None]] = []
        renditions: list[ArtifactRenditionRow] = []
        if not targets:
            return False
        for target in targets:
            if target.kind == "ARTIFACT_REVISION":
                revision = session.get(ArtifactRevisionRow, target.id)
                if revision is None:
                    return False
                import_row = None
                if revision.source_import_session_id is not None:
                    import_row = session.get(ImportSessionRow, revision.source_import_session_id)
                    if import_row is None:
                        return False
                revisions.append((revision, import_row))
            elif target.kind == "ARTIFACT_RENDITION":
                rendition = session.get(ArtifactRenditionRow, target.id)
                if rendition is None:
                    return False
                renditions.append(rendition)
            else:
                return False
        for revision, import_row in revisions:
            revision.discarded_at = None
            revision.media_health = "AVAILABLE"
            if import_row is not None:
                import_row.discarded_at = None
                import_row.discard_cleanup_pending = False
                import_row.updated_at = now
        for rendition in renditions:
            rendition.removed_at = None
            rendition.state = "READY"
            rendition.updated_at = now
        return True

    def _assert_capacity_locked(self, session: Session, *, expected_added_bytes: int) -> None:
        decision = assess_capacity(
            logical_used_bytes=self._used_bytes(session),
            active_reservation_bytes=self._active_reservations(session),
            workspace_quota_bytes=self.settings.workspace_quota_bytes,
            expected_added_bytes=expected_added_bytes,
            physical_free_bytes=self._free_space_bytes(),
            reservation_multiplier=self.settings.media_reservation_multiplier,
            physical_free_floor_bytes=self.settings.media_physical_free_floor_bytes,
        )
        if not decision.allowed:
            raise StorageCapacityExceeded(decision)

    def _used_bytes(self, session: Session) -> int:
        physical = int(
            session.scalar(select(func.coalesce(func.sum(StoredBlobRow.physical_bytes), 0))) or 0
        )
        return physical + self._staging_bytes() + self._tracked_orphan_owned_bytes(session)

    @staticmethod
    def _active_reservations(session: Session) -> int:
        return int(
            session.scalar(
                select(func.coalesce(func.sum(StorageReservationRow.reserved_bytes), 0)).where(
                    StorageReservationRow.state == "ACTIVE"
                )
            )
            or 0
        )

    @staticmethod
    def _state_bytes(session: Session, state: str) -> int:
        return int(
            session.scalar(
                select(func.coalesce(func.sum(StoredBlobRow.physical_bytes), 0)).where(
                    StoredBlobRow.state == state
                )
            )
            or 0
        )

    def _eligible_gc_entries(
        self, session: Session
    ) -> list[tuple[StoredBlobRow, tuple[BlobLogicalTarget, ...]]]:
        candidates = list(
            session.scalars(
            select(StoredBlobRow)
            .where(StoredBlobRow.state == "ACTIVE", StoredBlobRow.logical_ref_count == 0)
            .order_by(StoredBlobRow.sha256)
            )
        )
        frozen_by_sha = self._blobs.frozen_gc_targets_for_shas(
            session,
            [blob.sha256 for blob in candidates],
        )
        entries: list[tuple[StoredBlobRow, tuple[BlobLogicalTarget, ...]]] = []
        for blob in candidates:
            targets = frozen_by_sha[blob.sha256]
            if targets:
                entries.append((blob, targets))
        return entries

    @staticmethod
    def _plan_entry(
        row: GcPlanEntryRow,
        sha256: str,
        targets: tuple[BlobLogicalTarget, ...],
    ) -> GcPlanEntry:
        return GcPlanEntry(
            id=row.id,
            target_id=targets[0].id,
            blob_sha256=sha256,
            target_ids=tuple(target.id for target in targets),
            reason=row.reason,
            logical_reclaim_bytes=row.logical_reclaim_bytes,
            physical_reclaim_bytes=row.physical_reclaim_bytes,
        )

    @staticmethod
    def _gc_targets(session: Session, entry_id: str) -> tuple[BlobLogicalTarget, ...]:
        return tuple(
            BlobLogicalTarget(row.target_kind, row.target_id)
            for row in session.scalars(
                select(GcPlanTargetRow)
                .where(GcPlanTargetRow.gc_plan_entry_id == entry_id)
                .order_by(GcPlanTargetRow.target_kind, GcPlanTargetRow.target_id)
            )
        )

    @staticmethod
    def _quarantine_targets(
        session: Session, quarantine_id: str
    ) -> tuple[BlobLogicalTarget, ...]:
        return tuple(
            BlobLogicalTarget(row.target_kind, row.target_id)
            for row in session.scalars(
                select(QuarantineTargetRow)
                .where(QuarantineTargetRow.quarantine_entry_id == quarantine_id)
                .order_by(QuarantineTargetRow.target_kind, QuarantineTargetRow.target_id)
            )
        )

    def _quarantine_result(self, quarantine_id: str) -> QuarantineResult | None:
        with self.session_factory() as session:
            entry = session.get(QuarantineEntryRow, quarantine_id)
            if entry is None or entry.operation_state != "QUARANTINED":
                return None
            targets = self._quarantine_targets(session, entry.id)
            return QuarantineResult(
                entry.id,
                targets[0].id,
                entry.target_id,
                entry.quarantine_path,
            )

    def _expire_locked(self, session: Session, now: datetime) -> None:
        for row in session.scalars(
            select(StorageReservationRow).where(
                StorageReservationRow.state == "ACTIVE",
                StorageReservationRow.expires_at <= now,
            )
        ):
            row.state = "EXPIRED"
            row.released_at = now

    def _disk_free_bytes(self) -> int:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(self.settings.data_dir).free

    def _store(self) -> ArtifactStore:
        return ArtifactStore(
            self.settings.data_dir,
            max_bytes=self.settings.media_single_file_max_bytes,
            chunk_size=self.settings.artifact_upload_chunk_bytes,
        )

    def _quarantine_path(self, quarantine_id: str) -> Path:
        return self.settings.data_dir / "artifacts" / ".quarantine" / f"{quarantine_id}.blob"

    def _staging_bytes(self) -> int:
        artifacts = self.settings.data_dir / "artifacts"
        return self._owned_physical_bytes(
            (
                artifacts / ".staging",
                artifacts / ".promotion",
                self.settings.data_dir / "large-upload-staging",
                artifacts / ".transcode",
            )
        )

    def _tracked_orphan_owned_bytes(self, session: Session) -> int:
        blobs = list(
            session.scalars(select(StoredBlobRow).where(StoredBlobRow.physical_bytes > 0))
        )
        quarantine_entries_by_sha: dict[str, list[QuarantineEntryRow]] = defaultdict(list)
        blob_sha256s = [blob.sha256 for blob in blobs]
        if blob_sha256s:
            for entry in session.scalars(
                select(QuarantineEntryRow)
                .where(
                    QuarantineEntryRow.target_id.in_(blob_sha256s),
                    QuarantineEntryRow.operation_state.notin_(("RESTORED", "PURGED")),
                )
                .order_by(
                    QuarantineEntryRow.target_id,
                    QuarantineEntryRow.operation_updated_at.desc(),
                )
            ):
                quarantine_entries_by_sha[entry.target_id].append(entry)

        expected: set[tuple[object, ...]] = set()
        for blob in blobs:
            candidates = [self._store().blob_path(blob.sha256)]
            candidates.extend(
                self._quarantine_path(entry.id)
                for entry in quarantine_entries_by_sha[blob.sha256]
            )
            # A lifecycle intent can move the owned bytes before the database category
            # is finalized. Exclude exactly one extant owned location from the orphan
            # scan; any duplicate location remains tracked as additional physical use.
            physical_location = next(
                (candidate for candidate in candidates if self._is_owned_regular_file(candidate)),
                None,
            )
            if physical_location is not None:
                try:
                    metadata = physical_location.lstat()
                except OSError:
                    continue
                expected.add(self._physical_file_identity(physical_location, metadata))
        total = 0
        seen = set(expected)
        for _path, size, identity in self._owned_file_records(
            (
                self._store().blob_root,
                self.settings.data_dir / "artifacts" / ".quarantine",
            )
        ):
            if identity in seen:
                continue
            seen.add(identity)
            total += size
        return total

    @staticmethod
    def _physical_file_identity(
        path: Path,
        metadata: object,
    ) -> tuple[object, ...]:
        device = int(getattr(metadata, "st_dev", 0))
        inode = int(getattr(metadata, "st_ino", 0))
        if inode:
            return ("inode", device, inode)
        # Unknown identities remain path-specific so distinct allocations are
        # never collapsed merely because their byte sizes happen to match.
        return ("path", os.path.normcase(os.fspath(path.absolute())))

    @classmethod
    def _owned_file_records(
        cls,
        roots: Iterable[Path],
    ) -> list[tuple[Path, int, tuple[object, ...]]]:
        files: list[tuple[Path, int, tuple[object, ...]]] = []
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        for root in roots:
            try:
                metadata = root.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                continue
            attributes = getattr(metadata, "st_file_attributes", 0)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or attributes & reparse_flag
            ):
                continue
            pending = [root]
            while pending:
                current = pending.pop()
                try:
                    with os.scandir(current) as children:
                        for child in children:
                            try:
                                child_metadata = child.stat(follow_symlinks=False)
                                attributes = getattr(child_metadata, "st_file_attributes", 0)
                                if child.is_symlink() or attributes & reparse_flag:
                                    continue
                                if child.is_dir(follow_symlinks=False):
                                    pending.append(Path(child.path))
                                elif child.is_file(follow_symlinks=False):
                                    path = Path(child.path)
                                    # Windows DirEntry metadata can omit volume/file
                                    # identifiers even though a direct lstat exposes
                                    # them, so obtain the accounting identity directly.
                                    file_metadata = path.lstat()
                                    file_attributes = getattr(
                                        file_metadata,
                                        "st_file_attributes",
                                        0,
                                    )
                                    if (
                                        not stat.S_ISREG(file_metadata.st_mode)
                                        or stat.S_ISLNK(file_metadata.st_mode)
                                        or file_attributes & reparse_flag
                                    ):
                                        continue
                                    files.append(
                                        (
                                            path,
                                            int(file_metadata.st_size),
                                            cls._physical_file_identity(path, file_metadata),
                                        )
                                    )
                            except OSError:
                                continue
                except OSError:
                    continue
        return files

    @classmethod
    def _owned_physical_bytes(cls, roots: Iterable[Path]) -> int:
        seen: set[tuple[object, ...]] = set()
        total = 0
        for _path, size, identity in cls._owned_file_records(roots):
            if identity in seen:
                continue
            seen.add(identity)
            total += size
        return total

    @classmethod
    def _owned_files(cls, roots: Iterable[Path]) -> list[tuple[Path, int]]:
        return [
            (path, size)
            for path, size, _identity in cls._owned_file_records(roots)
        ]

    @staticmethod
    def _is_owned_regular_file(path: Path) -> bool:
        try:
            metadata = path.lstat()
        except OSError:
            return False
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        attributes = getattr(metadata, "st_file_attributes", 0)
        return (
            stat.S_ISREG(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and not attributes & reparse_flag
        )

    @classmethod
    def _owned_directory_bytes(cls, directory: Path) -> int:
        return cls._owned_physical_bytes((directory,))

    @staticmethod
    def _reservation(row: StorageReservationRow) -> StorageReservation:
        return StorageReservation(
            id=row.id,
            operation_type=row.operation_type,
            operation_id=row.operation_id,
            reserved_bytes=row.reserved_bytes,
            expires_at=row.expires_at,
        )
