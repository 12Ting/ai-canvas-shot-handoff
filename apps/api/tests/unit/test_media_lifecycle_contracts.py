from importlib import import_module
from importlib.util import find_spec

import ai_canvas.infrastructure.db.models  # noqa: F401
from ai_canvas.config import Settings
from ai_canvas.infrastructure.db.base import Base


def test_b4_storage_defaults_preserve_confirmed_limits() -> None:
    settings = Settings()

    assert settings.media_single_file_max_bytes == 10 * 1024**3
    assert settings.workspace_quota_bytes == 100 * 1024**3
    assert settings.media_large_upload_part_bytes == 8 * 1024**2
    assert settings.media_physical_free_floor_bytes == 10 * 1024**3
    assert settings.media_reservation_multiplier == 1.25
    assert settings.media_session_idle_seconds == 10 * 60
    assert settings.media_session_absolute_seconds == 60 * 60
    assert settings.media_quarantine_days == 7


def test_b4_media_lifecycle_contract_module_exists() -> None:
    assert find_spec("ai_canvas.contracts.media_lifecycle") is not None


def test_large_upload_request_contract_is_exported() -> None:
    module = import_module("ai_canvas.contracts.media_lifecycle")

    assert hasattr(module, "CreateLargeUploadRequest")


def test_b4_media_state_contracts_are_exported() -> None:
    module = import_module("ai_canvas.contracts.media_lifecycle")

    assert all(
        hasattr(module, name)
        for name in ("StorageKind", "MediaHealth", "LargeUploadState", "MaintenanceJobState")
    )


def test_large_upload_session_response_contract_is_exported() -> None:
    module = import_module("ai_canvas.contracts.media_lifecycle")

    assert hasattr(module, "LargeUploadSessionResponse")


def test_b4_lifecycle_tables_and_revision_source_columns_exist() -> None:
    expected_tables = {
        "large_upload_sessions",
        "large_upload_parts",
        "storage_reservations",
        "media_sessions",
        "media_session_grants",
        "external_root_grants",
        "external_snapshots",
        "artifact_renditions",
        "maintenance_jobs",
        "gc_plans",
        "gc_plan_entries",
        "quarantine_entries",
    }

    assert expected_tables <= set(Base.metadata.tables)
    revision_columns = Base.metadata.tables["artifact_revisions"].c
    assert {"storage_kind", "media_health", "external_snapshot_id"} <= set(revision_columns.keys())
