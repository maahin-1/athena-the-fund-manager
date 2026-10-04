from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from athena.contracts import AllSourcesFailed


class HealthRegistry:
    def __init__(self) -> None:
        self._degraded: dict[str, str] = {}

    def mark_degraded(self, name: str, reason: str) -> None:
        self._degraded[name] = reason

    def mark_healthy(self, name: str) -> None:
        self._degraded.pop(name, None)

    def is_degraded(self, name: str) -> bool:
        return name in self._degraded

    def reason(self, name: str) -> str | None:
        return self._degraded.get(name)

    def degraded(self) -> dict[str, str]:
        return dict(self._degraded)


@dataclass(frozen=True)
class ChainResult:
    value: Any
    source: str
    failures: tuple[tuple[str, str], ...]


class FallbackChain:
    def __init__(self, sources: Sequence[tuple[str, Callable[..., Any]]]) -> None:
        if not sources:
            raise ValueError("a fallback chain needs at least one source")
        self._sources = list(sources)

    def run(self, *args: Any, health: HealthRegistry | None = None, **kwargs: Any) -> ChainResult:
        failures: list[tuple[str, str]] = []
        for name, fetch in self._sources:
            if health is not None and health.is_degraded(name):
                failures.append((name, f"skipped, degraded: {health.reason(name)}"))
                continue
            try:
                value = fetch(*args, **kwargs)
            except Exception as exc:  # any source failure moves to the next source
                failures.append((name, f"{type(exc).__name__}: {exc}"))
                continue
            if value is None or (hasattr(value, "__len__") and len(value) == 0):
                failures.append((name, "returned no data"))
                continue
            return ChainResult(value, name, tuple(failures))
        detail = "; ".join(f"{name}: {reason}" for name, reason in failures)
        raise AllSourcesFailed(f"all sources failed ({detail})")
