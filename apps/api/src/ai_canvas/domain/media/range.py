from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ByteRange:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


class RangeNotSatisfiable(ValueError):
    def __init__(self, total_bytes: int) -> None:
        self.total_bytes = total_bytes
        super().__init__(f"bytes */{total_bytes}")


def parse_single_range(value: str | None, total_bytes: int) -> ByteRange | None:
    if total_bytes < 1:
        raise ValueError("total_bytes must be positive")
    if value is None:
        return None
    if not value.startswith("bytes=") or "," in value:
        raise RangeNotSatisfiable(total_bytes)
    spec = value.removeprefix("bytes=")
    if spec.count("-") != 1:
        raise RangeNotSatisfiable(total_bytes)
    start_text, end_text = spec.split("-", 1)
    if not start_text and not end_text:
        raise RangeNotSatisfiable(total_bytes)
    if not start_text:
        if not end_text.isdecimal():
            raise RangeNotSatisfiable(total_bytes)
        suffix_length = int(end_text)
        if suffix_length < 1:
            raise RangeNotSatisfiable(total_bytes)
        return ByteRange(max(0, total_bytes - suffix_length), total_bytes - 1)
    if not start_text.isdecimal() or (end_text and not end_text.isdecimal()):
        raise RangeNotSatisfiable(total_bytes)
    start = int(start_text)
    if start >= total_bytes:
        raise RangeNotSatisfiable(total_bytes)
    end = total_bytes - 1 if not end_text else min(total_bytes - 1, int(end_text))
    if end < start:
        raise RangeNotSatisfiable(total_bytes)
    return ByteRange(start, end)
