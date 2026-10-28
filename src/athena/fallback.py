from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from athena.contracts import AllSourcesFailed


class HealthRegistry:
    """Which sources cannot be used, and why. With a `path` the list is kept on disk and shared by every process, and an entry expires after `ttl_seconds` (a model that answered 404 or
    "no credits" is skipped for hours, not asked again by each subprocess; the probe clears an entry when the model works again). Without a path it lives only in memory."""

    def __init__(self, path: Path | str | None = None, ttl_seconds: float | None = 6 * 3600, clock: Callable[[], float] = time.time) -> None:
        self._degraded: dict[str, str] = {}
        self._until: dict[str, float] = {}
        self._path = Path(path) if path else None
        self._ttl = ttl_seconds if self._path else None
        self._clock = clock
        if self._path:
            self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
        except (OSError, ValueError):
            return
        for name, row in (data if isinstance(data, dict) else {}).items():
            if isinstance(row, dict) and float(row.get("until", 0)) > self._clock():
                self._degraded[name], self._until[name] = str(row.get("reason", "")), float(row["until"])

    def _save(self) -> None:
        if not self._path:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({n: {"reason": r, "until": self._until[n]} for n, r in self._degraded.items()}, indent=1), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            pass  # remembering is a convenience; failing to remember must not fail a call

    def mark_degraded(self, name: str, reason: str) -> None:
        self._degraded[name] = reason
        if self._ttl is not None:
            self._until[name] = self._clock() + self._ttl
        self._save()

    def mark_healthy(self, name: str) -> None:
        self._degraded.pop(name, None)
        self._until.pop(name, None)
        self._save()

    def _expire(self) -> None:
        if self._ttl is None:
            return
        for name in [n for n, until in self._until.items() if until <= self._clock()]:
            self._degraded.pop(name, None)
            self._until.pop(name, None)

    def is_degraded(self, name: str) -> bool:
        self._expire()
        return name in self._degraded

    def reason(self, name: str) -> str | None:
        return self._degraded.get(name)

    def degraded(self) -> dict[str, str]:
        self._expire()
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
