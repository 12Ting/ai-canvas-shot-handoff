from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

GIB = 1024**3
CapacityFailureCode = Literal["QUOTA_EXCEEDED", "INSUFFICIENT_PHYSICAL_SPACE"]


@dataclass(frozen=True, slots=True)
class CapacityDecision:
    allowed: bool
    code: CapacityFailureCode | None
    required_physical_free_bytes: int


def assess_capacity(
    *,
    logical_used_bytes: int,
    active_reservation_bytes: int,
    workspace_quota_bytes: int,
    expected_added_bytes: int,
    physical_free_bytes: int,
    reservation_multiplier: float = 1.25,
    physical_free_floor_bytes: int = 10 * GIB,
) -> CapacityDecision:
    values = (
        logical_used_bytes,
        active_reservation_bytes,
        workspace_quota_bytes,
        expected_added_bytes,
        physical_free_bytes,
        physical_free_floor_bytes,
    )
    if any(value < 0 for value in values) or reservation_multiplier < 1:
        raise ValueError("capacity inputs must be nonnegative and multiplier must be at least one")
    required_physical_free_bytes = math.ceil(
        (active_reservation_bytes + expected_added_bytes) * reservation_multiplier
    ) + physical_free_floor_bytes
    if logical_used_bytes + active_reservation_bytes + expected_added_bytes > workspace_quota_bytes:
        return CapacityDecision(False, "QUOTA_EXCEEDED", required_physical_free_bytes)
    if physical_free_bytes < required_physical_free_bytes:
        return CapacityDecision(False, "INSUFFICIENT_PHYSICAL_SPACE", required_physical_free_bytes)
    return CapacityDecision(True, None, required_physical_free_bytes)
