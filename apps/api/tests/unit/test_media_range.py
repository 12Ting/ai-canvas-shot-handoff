from importlib import import_module
from importlib.util import find_spec


def test_media_range_domain_module_exists() -> None:
    assert find_spec("ai_canvas.domain.media.range") is not None


def test_single_range_parser_is_exported() -> None:
    module = import_module("ai_canvas.domain.media.range")

    assert hasattr(module, "parse_single_range")


def test_single_range_parser_accepts_bounded_open_and_suffix_ranges() -> None:
    module = import_module("ai_canvas.domain.media.range")
    parse = module.parse_single_range

    assert (parse("bytes=2-4", 6).start, parse("bytes=2-4", 6).end) == (2, 4)
    assert (parse("bytes=2-", 6).start, parse("bytes=2-", 6).end) == (2, 5)
    assert (parse("bytes=-3", 6).start, parse("bytes=-3", 6).end) == (3, 5)


def test_single_range_parser_rejects_multi_and_unsatisfied_values() -> None:
    module = import_module("ai_canvas.domain.media.range")

    for value in ("bytes=0-1,3-4", "bytes=6-", "bytes=4-2", "units=0-1"):
        try:
            module.parse_single_range(value, 6)
        except module.RangeNotSatisfiable as error:
            assert error.total_bytes == 6
        else:
            raise AssertionError(f"{value} must be rejected")
