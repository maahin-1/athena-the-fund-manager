from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from athena.agents.base import Specialist
from athena.contracts import AthenaError
from athena.orchestrator.blend import CONFLICT_MIN_CONFIDENCE, Conflict, blend_signals
from athena.resolver import Ambiguity, InstrumentResolver, Resolution

OK = "ok"
NO_VIEW = "no_view"
NEEDS_CLARIFICATION = "needs_clarification"
NOT_BUILT = "not built yet"
RISK_OVERLAY_NOTE = "the risk overlay (TRD 2.7) is not built, so no position or concentration limits were applied"

PacketBuilder = Callable[[Resolution], dict[str, Any]]


@dataclass(frozen=True)
class OrchestrationResult:
    status: str  # OK, NO_VIEW (nobody had enough data) or NEEDS_CLARIFICATION (the input matched several instruments)
    query: str
    resolution: Resolution | None
    ambiguity: Ambiguity | None
    specialists: dict[str, dict[str, Any]]  # name -> specialist output contract (TRD section 3)
    skipped: dict[str, str]  # routed specialist name -> why it did not run
    conflict: Conflict | None
    net: float | None
    verdict: dict[str, Any] | None  # judge verdict contract (TRD section 3)
    notes: tuple[str, ...]


class Orchestrator:
    """Resolve -> route -> run the specialists that exist -> blend (TRD 2.1, skeleton).

    The debate path (TRD 2.15) is not built: a conflict is detected and reported, the verdict is the blend with
    conviction capped, and `resolution_path` is always "blend". A specialist that is routed but not registered,
    or that fails with an AthenaError, is listed in `skipped` and the rest still run."""

    def __init__(
        self,
        resolver: InstrumentResolver,
        specialists: Mapping[str, Specialist],
        packet_builders: Mapping[str, PacketBuilder],
        conflict_min_confidence: int = CONFLICT_MIN_CONFIDENCE,
    ):
        self._resolver = resolver
        self._specialists = dict(specialists)
        self._packet_builders = dict(packet_builders)
        self._conflict_min_confidence = conflict_min_confidence

    def analyze(self, query: str) -> OrchestrationResult:
        resolved = self._resolver.resolve(query)
        if isinstance(resolved, Ambiguity):
            return OrchestrationResult(NEEDS_CLARIFICATION, query, None, resolved, {}, {}, None, None, None, ())
        return self.analyze_resolved(resolved, query)

    def analyze_resolved(self, resolution: Resolution, query: str | None = None) -> OrchestrationResult:
        outputs: dict[str, dict[str, Any]] = {}
        skipped: dict[str, str] = {}
        for name in resolution.routed_specialists:
            if name not in self._specialists or name not in self._packet_builders:
                skipped[name] = NOT_BUILT
                continue
            try:
                packet = self._packet_builders[name](resolution)
                outputs[name] = self._specialists[name].analyze(packet)
            except AthenaError as exc:
                skipped[name] = f"{type(exc).__name__}: {exc}"
        blended = blend_signals(outputs, self._conflict_min_confidence)
        notes: list[str] = []
        if len(outputs) < len(resolution.routed_specialists):
            notes.append(
                f"only {len(outputs)} of {len(resolution.routed_specialists)} routed specialists ran, "
                "so the verdict reflects just those"
            )
        notes.append(RISK_OVERLAY_NOTE)
        return OrchestrationResult(
            status=OK if blended.participants else NO_VIEW,
            query=query if query is not None else resolution.identifier,
            resolution=resolution,
            ambiguity=None,
            specialists=outputs,
            skipped=skipped,
            conflict=blended.conflict,
            net=blended.net,
            verdict=blended.verdict,
            notes=tuple(notes),
        )
