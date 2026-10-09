from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from athena.agents.base import Specialist
from athena.contracts import AthenaError, Bar
from athena.orchestrator.blend import CONFLICT_MIN_CONFIDENCE, Conflict, blend_signals
from athena.resolver import Ambiguity, InstrumentResolver, Resolution
from athena.risk_overlay.apply import UNCALIBRATED_NOTE, apply_overlay
from athena.risk_overlay.checks import figures_from_bars, run_checks
from athena.risk_overlay.model import Overlay

OK = "ok"
NO_VIEW = "no_view"
NEEDS_CLARIFICATION = "needs_clarification"
NOT_BUILT = "not built yet"
RISK_OVERLAY_NOTE = "no risk profile was given, so the risk overlay applied no limits (--profile on the command line, or switch on the risk profile in the dashboard)"

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
        bars: Callable[[str], Sequence[Bar]] | None = None,
    ):
        self._resolver = resolver
        self._bars = bars  # the price history the risk overlay measures; shared with whoever else reads it
        self._specialists = dict(specialists)
        self._packet_builders = dict(packet_builders)
        self._conflict_min_confidence = conflict_min_confidence

    def analyze(self, query: str, overlay: Overlay | None = None) -> OrchestrationResult:
        resolved = self._resolver.resolve(query)
        if isinstance(resolved, Ambiguity):
            return OrchestrationResult(NEEDS_CLARIFICATION, query, None, resolved, {}, {}, None, None, None, ())
        return self.analyze_resolved(resolved, query, overlay)

    def analyze_resolved(
        self, resolution: Resolution, query: str | None = None, overlay: Overlay | None = None
    ) -> OrchestrationResult:
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
        verdict = blended.verdict
        if overlay is None:
            notes.append(RISK_OVERLAY_NOTE)
        else:
            verdict, note = self._apply_overlay(resolution, verdict, overlay)
            notes.append(note)
        return OrchestrationResult(
            status=OK if blended.participants else NO_VIEW,
            query=query if query is not None else resolution.identifier,
            resolution=resolution,
            ambiguity=None,
            specialists=outputs,
            skipped=skipped,
            conflict=blended.conflict,
            net=blended.net,
            verdict=verdict,
            notes=tuple(notes),
        )

    def _apply_overlay(
        self, resolution: Resolution, verdict: dict[str, Any], overlay: Overlay
    ) -> tuple[dict[str, Any], str]:
        """The verdict after the person's risk limits, and the note that says so. Without prices the verdict stands and the
        note says the limits could not be checked."""
        if self._bars is None:
            raise AthenaError("the risk overlay needs a price source")
        try:
            bars = self._bars(resolution.identifier)
        except AthenaError as exc:
            return verdict, f"risk limits could not be checked: {exc}"
        findings = run_checks(figures_from_bars(bars), overlay, resolution.identifier)
        return apply_overlay(verdict, findings), f"risk profile '{overlay.profile.name}' applied: {UNCALIBRATED_NOTE}"
