import math

import pytest

from ai_canvas.domain.control.canonical import (
    CanonicalJsonError,
    canonical_json,
    canonical_sha256,
)


def test_canonical_json_sorts_mapping_keys_without_whitespace() -> None:
    value = {"later": ["x", 2], "first": {"z": True, "a": None}}

    assert canonical_json(value) == '{"first":{"a":null,"z":true},"later":["x",2]}'
    assert canonical_sha256(value) == canonical_sha256(
        {"first": {"a": None, "z": True}, "later": ["x", 2]}
    )


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_canonical_json_rejects_nonfinite_number(value: float) -> None:
    with pytest.raises(CanonicalJsonError):
        canonical_json({"value": value})
