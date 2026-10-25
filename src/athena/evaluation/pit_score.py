from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from athena.evaluation.outcomes import INLINE, OUTPERFORM, SIGNAL_FOR, UNDERPERFORM

SIGNALS = ("bullish", "neutral", "bearish")
HIGH_CONFIDENCE = 70


@dataclass(frozen=True)
class Call:
    """One analyst run on one case, with what happened afterwards."""

    symbol: str
    as_of: str
    horizon: int
    specialist: str
    run: int
    signal: str | None  # None when the analyst abstained or had insufficient data
    confidence: int
    label: str  # the outcome: outperform | inline | underperform

    @property
    def right(self) -> bool:
        return self.signal == SIGNAL_FOR[self.label]


@dataclass(frozen=True)
class Score:
    specialist: str
    horizon: int
    cases: int  # distinct stock-and-date cases that got a call
    abstained: int  # cases where no call was made
    accuracy: float  # share of calls matching the outcome (inline outcome counts as neutral)
    directional: float | None  # among calls that are bullish or bearish on a case that went one way or the other: share right
    directional_calls: int
    high_confidence_accuracy: float | None
    high_confidence_calls: int
    mix: dict[str, float]  # share of calls that are bullish, neutral, bearish
    outcome_mix: dict[str, float]  # share of cases that went each way; also the accuracy of always saying that signal
    agreement: float | None  # share of cases where every repeated run gave the same signal
    confusion: dict[tuple[str, str], int]  # (called signal, outcome signal) -> count


def _share(count: int, total: int) -> float | None:
    return count / total if total else None


def modal(signals: Sequence[str]) -> str:
    """The most common signal; a tie goes to neutral, the call that commits to least."""
    counts = Counter(signals)
    top = [signal for signal, n in counts.items() if n == max(counts.values())]
    return top[0] if len(top) == 1 else "neutral"


def score(calls: Iterable[Call], specialist: str, horizon: int) -> Score | None:
    """Accuracy, bias, calibration and run-to-run agreement for one analyst at one horizon. Repeated runs of one case are
    first reduced to their most common signal, so a case counts once however many times it was run."""
    rows = [c for c in calls if c.specialist == specialist and c.horizon == horizon]
    if not rows:
        return None
    by_case: dict[tuple[str, str], list[Call]] = defaultdict(list)
    for row in rows:
        by_case[(row.symbol, row.as_of)].append(row)
    decided: list[tuple[str, int, str]] = []  # (signal, confidence, outcome signal)
    abstained = 0
    agree = disagree = 0
    for runs in by_case.values():
        outcome = SIGNAL_FOR[runs[0].label]
        called = [r for r in runs if r.signal in SIGNALS]
        if not called:
            abstained += 1
            continue
        signals = [r.signal for r in called]  # type: ignore[misc]
        if len(called) > 1:
            agree, disagree = (agree + 1, disagree) if len(set(signals)) == 1 else (agree, disagree + 1)
        chosen = modal(signals)
        confidence = round(sum(r.confidence for r in called if r.signal == chosen) / sum(1 for r in called if r.signal == chosen))
        decided.append((chosen, confidence, outcome))
    n = len(decided)
    right = sum(1 for signal, _, outcome in decided if signal == outcome)
    directional = [(s, o) for s, _, o in decided if s != "neutral" and o != "neutral"]
    confident = [(s, o) for s, c, o in decided if c >= HIGH_CONFIDENCE]
    outcomes = Counter(o for _, _, o in decided)
    return Score(
        specialist=specialist,
        horizon=horizon,
        cases=n,
        abstained=abstained,
        accuracy=right / n if n else 0.0,
        directional=_share(sum(1 for s, o in directional if s == o), len(directional)),
        directional_calls=len(directional),
        high_confidence_accuracy=_share(sum(1 for s, o in confident if s == o), len(confident)),
        high_confidence_calls=len(confident),
        mix={signal: sum(1 for s, _, _ in decided if s == signal) / n if n else 0.0 for signal in SIGNALS},
        outcome_mix={signal: outcomes[signal] / n if n else 0.0 for signal in SIGNALS},
        agreement=_share(agree, agree + disagree),
        confusion=dict(Counter((s, o) for s, _, o in decided)),
    )


def format_score(result: Score) -> str:
    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.0%}"

    best = max(result.outcome_mix, key=lambda signal: result.outcome_mix[signal])
    lines = [
        f"{result.specialist}, {result.horizon}-day outcome: {result.cases} cases ({result.abstained} abstained)",
        f"  accuracy {pct(result.accuracy)}; best fixed answer (always {best}) {pct(result.outcome_mix[best])}; "
        f"when it takes a side and the stock went one way: {pct(result.directional)} of {result.directional_calls}",
        f"  confidence 70+: {pct(result.high_confidence_accuracy)} of {result.high_confidence_calls} calls; "
        f"runs agree on {pct(result.agreement)} of repeated cases",
        "  it says " + ", ".join(f"{signal} {pct(share)}" for signal, share in result.mix.items())
        + "; outcomes were " + ", ".join(f"{signal} {pct(share)}" for signal, share in result.outcome_mix.items()),
    ]
    return "\n".join(lines)


def calls_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[Call]:
    """Calls from the run log's JSON lines (one per analyst run and horizon)."""
    return [
        Call(r["symbol"], r["as_of"], int(r["horizon"]), r["specialist"], int(r.get("run", 0)), r.get("signal"), int(r.get("confidence") or 0), r["label"])
        for r in rows
        if r.get("label") in (OUTPERFORM, INLINE, UNDERPERFORM)
    ]
