from importlib import import_module
from importlib.util import find_spec


def test_large_upload_service_module_exists() -> None:
    assert find_spec("ai_canvas.application.large_upload_service") is not None


def test_large_upload_service_is_exported() -> None:
    module = import_module("ai_canvas.application.large_upload_service")

    assert hasattr(module, "LargeUploadService")


def test_fixed_part_range_helper_is_exported() -> None:
    module = import_module("ai_canvas.application.large_upload_service")

    assert hasattr(module, "expected_part_range")


def test_fixed_part_ranges_cover_exact_8_mib_windows() -> None:
    module = import_module("ai_canvas.application.large_upload_service")
    expected = getattr(module, "expected_part_range", None)

    assert expected is not None
    part_size = 8 * 1024**2
    assert expected(total_bytes=20 * 1024**2 + 3, part_size=part_size, part_number=0) == (
        0,
        part_size - 1,
    )
    assert expected(total_bytes=20 * 1024**2 + 3, part_size=part_size, part_number=2) == (
        2 * part_size,
        20 * 1024**2 + 2,
    )
