from importlib import import_module
from importlib.util import find_spec


def test_storage_lifecycle_domain_module_exists() -> None:
    assert find_spec("ai_canvas.domain.media.storage_lifecycle") is not None


def test_capacity_guard_is_exported() -> None:
    module = import_module("ai_canvas.domain.media.storage_lifecycle")

    assert hasattr(module, "assess_capacity")


def test_capacity_guard_requires_quota_and_125_percent_plus_10_gib() -> None:
    module = import_module("ai_canvas.domain.media.storage_lifecycle")
    assess = getattr(module, "assess_capacity", None)

    assert assess is not None
    quota_denied = assess(
        logical_used_bytes=90 * 1024**3,
        active_reservation_bytes=0,
        workspace_quota_bytes=100 * 1024**3,
        expected_added_bytes=11 * 1024**3,
        physical_free_bytes=100 * 1024**3,
    )
    physical_denied = assess(
        logical_used_bytes=10 * 1024**3,
        active_reservation_bytes=0,
        workspace_quota_bytes=100 * 1024**3,
        expected_added_bytes=8 * 1024**3,
        physical_free_bytes=19 * 1024**3,
    )
    concurrent_physical_denied = assess(
        logical_used_bytes=10 * 1024**3,
        active_reservation_bytes=5 * 1024**3,
        workspace_quota_bytes=100 * 1024**3,
        expected_added_bytes=8 * 1024**3,
        physical_free_bytes=21 * 1024**3,
    )
    allowed = assess(
        logical_used_bytes=10 * 1024**3,
        active_reservation_bytes=5 * 1024**3,
        workspace_quota_bytes=100 * 1024**3,
        expected_added_bytes=8 * 1024**3,
        physical_free_bytes=27 * 1024**3,
    )

    assert (quota_denied.allowed, quota_denied.code) == (False, "QUOTA_EXCEEDED")
    assert (physical_denied.allowed, physical_denied.code) == (
        False,
        "INSUFFICIENT_PHYSICAL_SPACE",
    )
    assert (concurrent_physical_denied.allowed, concurrent_physical_denied.code) == (
        False,
        "INSUFFICIENT_PHYSICAL_SPACE",
    )
    assert allowed.allowed is True
    assert allowed.required_physical_free_bytes == int(26.25 * 1024**3)
