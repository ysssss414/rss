from __future__ import annotations

import math

ABS_TOLERANCE = 1e-10
REL_TOLERANCE = 1e-12


def compare_records(expected: list[dict], actual: list[dict], *, name: str):
    """Compare every frozen field, order, null and count; tolerances are fixed."""
    if len(expected) != len(actual):
        raise AssertionError(f"{name}: row count {len(expected)} != {len(actual)}")
    for index, (left, right) in enumerate(zip(expected, actual)):
        if left.keys() != right.keys():
            raise AssertionError(f"{name}[{index}]: schema mismatch")
        for field, value in left.items():
            other = right[field]
            if isinstance(value, float) and isinstance(other, (float, int)) and not isinstance(other, bool):
                same = math.isclose(value, other, abs_tol=ABS_TOLERANCE, rel_tol=REL_TOLERANCE)
            else:
                same = value == other
            if not same:
                raise AssertionError(f"{name}[{index}].{field}: {value!r} != {other!r}")


def quality_correction(error) -> dict:
    return {"before": "LEGACY_ACCEPTED", "after": "NEW_REJECTED", "reason": error.code}
