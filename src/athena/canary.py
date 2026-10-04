from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from athena.contracts import Record, StaleDataError
from athena.fallback import HealthRegistry
from athena.freshness import check_fresh

logger = logging.getLogger("athena.canary")


@dataclass(frozen=True)
class CanaryCheck:
    name: str
    dataset: str
    fetch: Callable[[], Record | None]
    required_payload_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanaryResult:
    name: str
    ok: bool
    reason: str | None


def _failure_reason(check: CanaryCheck, now: datetime) -> str | None:
    try:
        record = check.fetch()
    except Exception as exc:
        return f"fetch raised {type(exc).__name__}: {exc}"
    if record is None or not record.payload:
        return "empty response"
    missing = [key for key in check.required_payload_keys if key not in record.payload]
    if missing:
        return f"schema changed: missing keys {missing}"
    try:
        check_fresh(check.dataset, record.as_of, now)
    except StaleDataError as exc:
        return str(exc)
    return None


def run_canary(
    checks: Sequence[CanaryCheck], health: HealthRegistry, now: datetime
) -> list[CanaryResult]:
    results: list[CanaryResult] = []
    for check in checks:
        reason = _failure_reason(check, now)
        if reason is None:
            health.mark_healthy(check.name)
            results.append(CanaryResult(check.name, True, None))
        else:
            health.mark_degraded(check.name, reason)
            logger.warning("canary failed for %s: %s", check.name, reason)
            results.append(CanaryResult(check.name, False, reason))
    return results
