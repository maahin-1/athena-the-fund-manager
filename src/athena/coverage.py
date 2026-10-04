from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from athena.contracts import Coverage


def _is_missing(available: Mapping[str, Any], name: str) -> bool:
    if name not in available:
        return True
    value = available[name]
    if value is None:
        return True
    if hasattr(value, "__len__") and len(value) == 0:
        return True
    return False


def derive_coverage(
    critical: Sequence[str],
    optional: Sequence[str],
    available: Mapping[str, Any],
) -> tuple[Coverage, list[str]]:
    missing_critical = [name for name in critical if _is_missing(available, name)]
    missing_optional = [name for name in optional if _is_missing(available, name)]
    missing = missing_critical + missing_optional
    if missing_critical:
        return Coverage.INSUFFICIENT, missing
    if missing_optional:
        return Coverage.PARTIAL, missing
    return Coverage.FULL, missing
