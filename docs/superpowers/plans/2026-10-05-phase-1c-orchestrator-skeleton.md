# Phase 1c — Orchestrator Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Athena answer a question end to end: take a ticker, ISIN or name, resolve it, route it to the specialists that exist, run them on a real model, blend their views into a verdict in the judge-verdict contract, and print a report. The debate path stays stubbed (a conflict is detected and reported, never argued out).

**Architecture:** `athena.orchestrator.blend` is pure arithmetic: a conviction-weighted blend of specialist outputs (the `blend_signals` idea from ai-hedge-fund) plus conflict detection per TRD §2.15. `Orchestrator` composes the existing pieces: `InstrumentResolver` (Phase 0c) -> `ROUTING` names -> per-specialist packet builders and `Specialist` objects -> `blend_signals`. A routed specialist that is not built, or that fails with an `AthenaError`, is listed as skipped and the rest still run; any other exception is a bug and propagates. `athena.cli` wires real sources (NSE lists and holidays, jugaad-data then Yahoo, the Plan 1b model router) behind `python -m athena.cli SBIN`.

**Tech Stack:** Python >= 3.11, pytest. No new dependencies.

**Spec:** `TRD.md` §2.1 (orchestrator), §2.15 (arbitration: trigger and conviction cap), §3 (judge verdict contract, routing table), §9 roadmap item 1 ("orchestrator skeleton: classification + simple blend, debate stubbed"); `PRD.md` FR-1, FR-2.

**Plan series:** 0a-0e, 1a, 1b (done) -> **1c (this plan)** -> 1d (remaining equity specialists in ratio-only mode, dashboard, backtest) -> Phase 2+ (debt, mutual funds, ETF analyst, full arbitration, risk overlay, live brokers).

**Suggested models:** Sonnet at medium effort, inline execution (the four tasks are sequential and small). Prototyped end to end in a scratch copy first: 347 offline tests passed, four deliberate mutations (conflict cap off, insufficient specialists not excluded, failures not caught, partial-coverage cap off) were each caught by the matching tests, and the live tests ran on real data and real models.

## Verified findings (5 Oct 2026)

Live run through the real resolver, jugaad-data prices and the Plan 1b model router (a stock, an ETF, and an ambiguous name):

| Query | Result |
| --- | --- |
| `SBIN` | equity, exact match; Quant/Technical said neutral 35 (daily and weekly trends down, monthly mixed, so not aligned); verdict **Hold, conviction 35**; valuation, moat_quality and earnings_intelligence listed as "not built yet" |
| `NIFTYBEES` | etf, exact match; Quant/Technical said bearish 80 (all three timeframes down, price below the lower Bollinger Band); verdict **Sell, conviction 80**; etf_analyst listed as "not built yet" |
| `SBI` | needs clarification (several instruments match); nothing is run and no model is called |

- **One specialist can still produce a strong verdict.** The ETF Sell above rests on a single technical view. The orchestrator therefore adds a note, `only 1 of 2 routed specialists ran, so the verdict reflects just those`, and always notes that the risk overlay is not built. Neither limitation is hidden.
- **Blend formula (all constants in `blend.py`, all tested):** each participating specialist weighs `confidence/100 x coverage weight` (full 1.0, partial 0.7); `net = sum(sign x weight) / sum(weight)` in [-1, 1]; verdict bands are Buy at net >= 0.6, Overweight >= 0.2, Hold above -0.2, Underweight above -0.6, else Sell; conviction is the mean confidence times `|net|` for a directional call and times `(1 - |net|)` for a Hold (a Hold's conviction measures how balanced the evidence is); it is capped at 70 if any participant has partial coverage, and at 40 if specialists conflict. These numbers are a starting point chosen for plausibility, not calibrated; calibrating them needs the golden sets.
- **Conflict (TRD §2.15):** opposite signals with both confidences >= 60; neutral never conflicts and an `insufficient` specialist never takes part. Because the debate is not built, a conflict yields a capped blend with `key_risks` saying so, and `resolution_path` stays `blend`.
- **Cost of a run:** about 14 s for three queries including loading both NSE master lists and the holiday calendar into a fresh in-memory store each time. Caching that store is deferred.
- **Not built here:** the debate and judge, the risk overlay, the other specialists, persistence of results, and any use of the `confirm` path for ambiguous names from the command line.

## Global Constraints

- No network in the default test run; the live test is opt-in via `--live` and uses the keys in `.env` (read by the code at runtime; the assistant cannot read `.env`).
- Every verdict must pass `validate_judge_verdict` and every specialist output `validate_specialist_output` (TRD §3, locked contract); `resolution_path` is `"blend"` in this plan.
- The blend is deterministic code: no model is called to produce a verdict or `key_risks`.
- Only `AthenaError` subclasses are treated as "this specialist could not run"; everything else propagates so bugs are not hidden.
- Output always ends with the disclaimer "A stylized analytical framework, not financial advice; not a registered investment adviser." (TRD §8).
- Commit with the GitHub no-reply identity: `git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit ...`; `git push` after each task.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/athena/orchestrator/blend.py` | `Conflict`, `BlendResult`, `participants`, `detect_conflict`, `blend_signals`, blend constants |
| `src/athena/orchestrator/orchestrator.py` | `OrchestrationResult`, `Orchestrator`, status constants |
| `src/athena/orchestrator/builders.py` | `history_fetcher`, `technical_packet_builder` |
| `src/athena/orchestrator/report.py` | `format_result`, `DISCLAIMER` |
| `src/athena/cli.py` | `build_orchestrator`, `live_orchestrator`, `main` (`python -m athena.cli`) |
| `tests/test_orchestrator_blend.py`, `test_orchestrator.py`, `test_orchestrator_builders.py`, `test_orchestrator_report.py`, `test_cli.py`, `tests/live/test_live_orchestrator.py` | one test module per source module, plus the live check |

---

### Task 1: Blend and conflict detection

**Files:**
- Create: `src/athena/orchestrator/__init__.py` (empty file), `src/athena/orchestrator/blend.py`
- Test: `tests/test_orchestrator_blend.py`

**Interfaces:**
- Consumes: `validate_judge_verdict` from `athena.evaluation.schema` (tests only).
- Produces (`athena.orchestrator.blend`): constants `SIGNAL_SIGN`, `COVERAGE_WEIGHT`, `COVERAGE_CONVICTION_CAP`, `CONFLICT_MIN_CONFIDENCE = 60`, `CONFLICT_CONVICTION_CAP = 40`, `BUY_AT = 0.6`, `OVERWEIGHT_AT = 0.2`, `UNDERWEIGHT_ABOVE = -0.6`, `NO_VIEW_RISK`; `@dataclass(frozen=True) Conflict(bullish: tuple[str, ...], bearish: tuple[str, ...])`; `@dataclass(frozen=True) BlendResult(verdict: dict, net: float, participants: tuple[str, ...], conflict: Conflict | None)`; `participants(outputs) -> dict` (drops `insufficient`); `detect_conflict(outputs, min_confidence=60) -> Conflict | None`; `blend_signals(outputs, min_conflict_confidence=60) -> BlendResult`. `outputs` maps a specialist name to a specialist-contract dict.

- [ ] **Step 1: Write the failing tests `tests/test_orchestrator_blend.py`**

```python
import pytest

from athena.evaluation.schema import validate_judge_verdict
from athena.orchestrator.blend import (
    CONFLICT_CONVICTION_CAP,
    NO_VIEW_RISK,
    Conflict,
    _verdict_for,
    blend_signals,
    detect_conflict,
)


def out(signal, confidence, coverage="full", missing=()):
    return {"signal": signal, "confidence": confidence, "reasoning": "x", "data_coverage": coverage, "missing": list(missing)}


def blend(outputs, **kwargs):
    result = blend_signals(outputs, **kwargs)
    assert validate_judge_verdict(result.verdict) == [], result.verdict
    assert result.verdict["resolution_path"] == "blend"
    return result


@pytest.mark.parametrize(
    "net, expected",
    [(1.0, "Buy"), (0.6, "Buy"), (0.59, "Overweight"), (0.2, "Overweight"), (0.19, "Hold"), (0.0, "Hold"),
     (-0.19, "Hold"), (-0.2, "Underweight"), (-0.59, "Underweight"), (-0.6, "Sell"), (-1.0, "Sell")],
)
def test_verdict_bands(net, expected):
    assert _verdict_for(net) == expected


def test_one_confident_bullish_specialist_is_a_buy_at_its_own_confidence():
    result = blend({"a": out("bullish", 80)})
    assert (result.verdict["verdict"], result.verdict["conviction"], result.net) == ("Buy", 80, 1.0)
    assert result.verdict["key_risks"] == [] and result.participants == ("a",)


def test_one_neutral_specialist_is_a_hold_at_its_own_confidence():
    result = blend({"a": out("neutral", 35)})
    assert (result.verdict["verdict"], result.verdict["conviction"], result.net) == ("Hold", 35, 0.0)


def test_one_bearish_specialist_is_a_sell():
    assert blend({"a": out("bearish", 70)}).verdict["verdict"] == "Sell"


def test_a_neutral_specialist_dilutes_a_bullish_one():
    result = blend({"a": out("bullish", 100), "b": out("neutral", 100)})
    assert result.net == 0.5 and result.verdict["verdict"] == "Overweight"
    assert result.verdict["conviction"] == 50 and result.conflict is None


def test_partial_coverage_caps_conviction_and_is_named_as_a_risk():
    result = blend({"a": out("bullish", 90, "partial", ["trend_monthly", "sma_200"])})
    assert result.verdict["verdict"] == "Buy" and result.verdict["conviction"] == 70
    assert result.verdict["key_risks"] == ["a: partial data, missing trend_monthly, sma_200"]


def test_partial_coverage_counts_for_less_than_full_when_specialists_disagree():
    result = blend({"a": out("bullish", 100, "full"), "b": out("bearish", 100, "partial", ["x"])})
    assert result.net == pytest.approx(0.1765, abs=1e-4)


def test_an_insufficient_specialist_does_not_take_part_but_is_reported():
    result = blend({"a": out("bullish", 80), "b": out("neutral", 0, "insufficient", ["nav"])})
    assert result.participants == ("a",) and result.verdict["verdict"] == "Buy"
    assert result.verdict["key_risks"] == ["b: not enough data to form a view (missing nav)"]


def test_nobody_with_enough_data_is_a_zero_conviction_hold():
    result = blend({"a": out("neutral", 0, "insufficient", ["nav"])})
    assert result.verdict["verdict"] == "Hold" and result.verdict["conviction"] == 0 and result.participants == ()
    assert result.verdict["key_risks"][0] == NO_VIEW_RISK
    assert blend({}).verdict["key_risks"] == [NO_VIEW_RISK]


def test_opposite_confident_signals_are_a_conflict_and_cap_conviction():
    result = blend({"a": out("bullish", 80), "b": out("bearish", 80)})
    assert result.conflict == Conflict(("a",), ("b",))
    assert result.verdict["verdict"] == "Hold" and result.verdict["conviction"] == CONFLICT_CONVICTION_CAP
    assert "specialists disagree" in result.verdict["key_risks"][0]


def test_a_conflict_needs_both_sides_at_or_above_the_confidence_threshold():
    assert detect_conflict({"a": out("bullish", 80), "b": out("bearish", 59)}) is None
    assert detect_conflict({"a": out("bullish", 80), "b": out("bearish", 60)}) is not None
    assert detect_conflict({"a": out("bullish", 80), "b": out("bearish", 59)}, min_confidence=50) is not None


def test_neutral_never_conflicts_and_insufficient_never_takes_part():
    assert detect_conflict({"a": out("bullish", 90), "b": out("neutral", 90)}) is None
    assert detect_conflict({"a": out("bullish", 90), "b": out("bearish", 90, "insufficient", ["x"])}) is None


def test_conviction_stays_within_zero_and_one_hundred():
    for confidence in (0, 1, 50, 100):
        for signal in ("bullish", "bearish", "neutral"):
            conviction = blend({"a": out(signal, confidence)}).verdict["conviction"]
            assert 0 <= conviction <= 100


def test_a_zero_confidence_participant_gives_a_hold_without_dividing_by_zero():
    result = blend({"a": out("bullish", 0)})
    assert result.net == 0.0 and result.verdict["verdict"] == "Hold"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_orchestrator_blend.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.orchestrator'`.

- [ ] **Step 3: Create the empty `src/athena/orchestrator/__init__.py` and write `src/athena/orchestrator/blend.py`**

```python
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SIGNAL_SIGN = {"bullish": 1, "bearish": -1, "neutral": 0}
COVERAGE_WEIGHT = {"full": 1.0, "partial": 0.7}  # how much a specialist's confidence counts
COVERAGE_CONVICTION_CAP = {"full": 100, "partial": 70}  # a verdict resting on partial data is not fully convinced
CONFLICT_MIN_CONFIDENCE = 60  # TRD 2.15 default
CONFLICT_CONVICTION_CAP = 40  # while the debate step does not exist, a contested blend stays tentative
BUY_AT = 0.6  # net score at or above: Buy
OVERWEIGHT_AT = 0.2  # at or above: Overweight; above -OVERWEIGHT_AT: Hold
UNDERWEIGHT_ABOVE = -0.6  # above: Underweight; at or below: Sell
NO_VIEW_RISK = "no specialist had enough data to form a view"


@dataclass(frozen=True)
class Conflict:
    """Specialists on opposite sides with both confidences at or above the threshold (TRD 2.15)."""

    bullish: tuple[str, ...]
    bearish: tuple[str, ...]


@dataclass(frozen=True)
class BlendResult:
    verdict: dict[str, Any]  # the judge verdict contract (TRD section 3)
    net: float  # -1 (all bearish) .. +1 (all bullish), confidence- and coverage-weighted
    participants: tuple[str, ...]
    conflict: Conflict | None


def participants(outputs: Mapping[str, Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Specialists that formed a view; one reporting `insufficient` coverage does not take part."""
    return {name: out for name, out in outputs.items() if out["data_coverage"] != "insufficient"}


def detect_conflict(
    outputs: Mapping[str, Mapping[str, Any]], min_confidence: int = CONFLICT_MIN_CONFIDENCE
) -> Conflict | None:
    taking_part = participants(outputs)
    bullish = tuple(n for n, o in taking_part.items() if o["signal"] == "bullish" and o["confidence"] >= min_confidence)
    bearish = tuple(n for n, o in taking_part.items() if o["signal"] == "bearish" and o["confidence"] >= min_confidence)
    return Conflict(bullish, bearish) if bullish and bearish else None


def _verdict_for(net: float) -> str:
    if net >= BUY_AT:
        return "Buy"
    if net >= OVERWEIGHT_AT:
        return "Overweight"
    if net > -OVERWEIGHT_AT:
        return "Hold"
    if net > UNDERWEIGHT_ABOVE:
        return "Underweight"
    return "Sell"


def _key_risks(outputs: Mapping[str, Mapping[str, Any]], conflict: Conflict | None) -> list[str]:
    risks: list[str] = []
    for name, out in outputs.items():
        if out["data_coverage"] == "partial":
            risks.append(f"{name}: partial data, missing {', '.join(out['missing'])}")
        elif out["data_coverage"] == "insufficient":
            risks.append(f"{name}: not enough data to form a view (missing {', '.join(out['missing'])})")
    if conflict:
        risks.append(
            f"specialists disagree (bullish: {', '.join(conflict.bullish)}; bearish: {', '.join(conflict.bearish)}); "
            "the debate step is not built, so this is a blend with capped conviction"
        )
    return risks


def blend_signals(
    outputs: Mapping[str, Mapping[str, Any]], min_conflict_confidence: int = CONFLICT_MIN_CONFIDENCE
) -> BlendResult:
    """Conviction-weighted blend of specialist outputs into a judge-contract verdict (resolution_path 'blend').

    net = sum(sign * confidence * coverage weight) / sum(confidence * coverage weight). The verdict comes from
    net; conviction is the mean confidence times |net| for a directional call, or times (1 - |net|) for a Hold
    (so a Hold measures how balanced the evidence is), capped by the weakest participant's coverage and, if
    specialists conflict, by CONFLICT_CONVICTION_CAP."""
    taking_part = participants(outputs)
    conflict = detect_conflict(outputs, min_conflict_confidence)
    if not taking_part:
        risks = [NO_VIEW_RISK] + _key_risks(outputs, None)
        verdict = {"verdict": "Hold", "conviction": 0, "key_risks": risks, "resolution_path": "blend"}
        return BlendResult(verdict, 0.0, (), None)

    weights = {n: o["confidence"] / 100 * COVERAGE_WEIGHT[o["data_coverage"]] for n, o in taking_part.items()}
    total = sum(weights.values())
    net = sum(SIGNAL_SIGN[taking_part[n]["signal"]] * w for n, w in weights.items()) / total if total else 0.0
    verdict = _verdict_for(net)
    mean_confidence = sum(o["confidence"] for o in taking_part.values()) / len(taking_part)
    strength = 1 - abs(net) if verdict == "Hold" else abs(net)
    cap = min(COVERAGE_CONVICTION_CAP[o["data_coverage"]] for o in taking_part.values())
    if conflict:
        cap = min(cap, CONFLICT_CONVICTION_CAP)
    conviction = max(0, min(round(mean_confidence * strength), cap))
    return BlendResult(
        {"verdict": verdict, "conviction": conviction, "key_risks": _key_risks(outputs, conflict), "resolution_path": "blend"},
        round(net, 4),
        tuple(taking_part),
        conflict,
    )
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_orchestrator_blend.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `24 passed`, then `323 passed, 41 skipped`.

- [ ] **Step 5: Mutation check (do not commit these edits)**

In `blend.py`, replace `cap = min(cap, CONFLICT_CONVICTION_CAP)` with `pass` and run `.venv/Scripts/python -m pytest tests/test_orchestrator_blend.py -q`: expect `test_opposite_confident_signals_are_a_conflict_and_cap_conviction` to FAIL. Undo it. Then change `COVERAGE_CONVICTION_CAP = {"full": 100, "partial": 70}` to use `100` for partial: expect `test_partial_coverage_caps_conviction_and_is_named_as_a_risk` to FAIL. Undo it and confirm `git diff` is empty for `blend.py`.

- [ ] **Step 6: Commit and push**

```bash
git add src/athena/orchestrator tests/test_orchestrator_blend.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add conviction-weighted blend and conflict detection"
git push
```

---

### Task 2: Orchestrator

**Files:**
- Create: `src/athena/orchestrator/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `blend_signals`, `CONFLICT_MIN_CONFIDENCE`, `Conflict` (Task 1); `Resolution`, `Ambiguity`, `InstrumentResolver` from `athena.resolver` (`resolve(query) -> Resolution | Ambiguity`; `Resolution.routed_specialists` is a tuple of names from `athena.routing.ROUTING`); `Specialist` from `athena.agents.base` (`analyze(packet) -> dict`); `AthenaError` from `athena.contracts`.
- Produces (`athena.orchestrator.orchestrator`): constants `OK = "ok"`, `NO_VIEW = "no_view"`, `NEEDS_CLARIFICATION = "needs_clarification"`, `NOT_BUILT = "not built yet"`, `RISK_OVERLAY_NOTE`; alias `PacketBuilder = Callable[[Resolution], dict]`; `@dataclass(frozen=True) OrchestrationResult(status, query, resolution, ambiguity, specialists, skipped, conflict, net, verdict, notes)`; `Orchestrator(resolver, specialists: Mapping[str, Specialist], packet_builders: Mapping[str, PacketBuilder], conflict_min_confidence=60)` with `analyze(query) -> OrchestrationResult` and `analyze_resolved(resolution, query=None) -> OrchestrationResult`.

- [ ] **Step 1: Write the failing tests `tests/test_orchestrator.py`**

```python
import pytest

from athena.agents.base import SpecialistError
from athena.contracts import AllSourcesFailed, InsufficientData
from athena.evaluation.schema import validate_judge_verdict, validate_specialist_output
from athena.orchestrator.orchestrator import (
    NEEDS_CLARIFICATION,
    NO_VIEW,
    NOT_BUILT,
    OK,
    Orchestrator,
)
from athena.resolver import Ambiguity, Candidate, Resolution


def resolution(asset_class="equity", routed=("quant_technical", "valuation")):
    return Resolution(asset_class, "ticker", "SBIN", "State Bank of India", "INE062A01020", "exact", 1.0, (), tuple(routed))


class FakeResolver:
    def __init__(self, result):
        self.result = result
        self.queries = []

    def resolve(self, query):
        self.queries.append(query)
        return self.result


class FakeSpecialist:
    def __init__(self, output=None, error=None):
        self.output, self.error, self.packets = output, error, []

    def analyze(self, packet):
        self.packets.append(packet)
        if self.error:
            raise self.error
        return self.output


def out(signal="bullish", confidence=80, coverage="full", missing=()):
    return {"signal": signal, "confidence": confidence, "reasoning": "r", "data_coverage": coverage, "missing": list(missing)}


def build(specialists, builders=None, resolved=None):
    builders = builders if builders is not None else {name: (lambda res, n=name: {"built_for": n, "id": res.identifier}) for name in specialists}
    return Orchestrator(FakeResolver(resolved or resolution()), specialists, builders)


def test_a_resolved_query_runs_the_registered_specialists_and_blends():
    quant = FakeSpecialist(out("bullish", 80))
    result = build({"quant_technical": quant}).analyze("sbin")
    assert result.status == OK and result.query == "sbin"
    assert result.specialists == {"quant_technical": out("bullish", 80)}
    assert quant.packets == [{"built_for": "quant_technical", "id": "SBIN"}]
    assert result.verdict["verdict"] == "Buy" and validate_judge_verdict(result.verdict) == []
    assert validate_specialist_output(result.specialists["quant_technical"]) == []


def test_routed_specialists_that_are_not_built_are_listed_not_hidden():
    result = build({"quant_technical": FakeSpecialist(out())}).analyze("sbin")
    assert result.skipped == {"valuation": NOT_BUILT}


def test_a_specialist_with_no_packet_builder_counts_as_not_built():
    result = build({"quant_technical": FakeSpecialist(out())}, builders={}).analyze("sbin")
    assert result.skipped["quant_technical"] == NOT_BUILT and result.status == NO_VIEW


def test_a_data_failure_in_one_specialist_is_recorded_and_the_others_still_run():
    def failing_builder(res):
        raise InsufficientData("no price history")

    good = FakeSpecialist(out("bearish", 70))
    orchestrator = Orchestrator(
        FakeResolver(resolution(routed=("quant_technical", "valuation"))),
        {"quant_technical": FakeSpecialist(out()), "valuation": good},
        {"quant_technical": failing_builder, "valuation": lambda res: {"metrics": {}}},
    )
    result = orchestrator.analyze("sbin")
    assert result.skipped == {"quant_technical": "InsufficientData: no price history"}
    assert list(result.specialists) == ["valuation"] and result.verdict["verdict"] == "Sell"


def test_model_failures_are_recorded_as_skipped_specialists():
    for error in (SpecialistError("no valid output"), AllSourcesFailed("every model failed")):
        result = build({"quant_technical": FakeSpecialist(error=error)}).analyze("sbin")
        assert type(error).__name__ in result.skipped["quant_technical"]


def test_when_nothing_ran_the_result_is_a_zero_conviction_hold_marked_no_view():
    result = build({"quant_technical": FakeSpecialist(error=SpecialistError("x"))}).analyze("sbin")
    assert result.status == NO_VIEW
    assert (result.verdict["verdict"], result.verdict["conviction"]) == ("Hold", 0)


def test_an_insufficient_specialist_is_shown_but_gives_no_view():
    result = build({"quant_technical": FakeSpecialist(out("neutral", 0, "insufficient", ["rsi_14"]))}).analyze("sbin")
    assert result.status == NO_VIEW and "quant_technical" in result.specialists


def test_a_conflict_between_specialists_is_reported_and_conviction_is_capped():
    orchestrator = Orchestrator(
        FakeResolver(resolution(routed=("quant_technical", "valuation"))),
        {"quant_technical": FakeSpecialist(out("bullish", 85)), "valuation": FakeSpecialist(out("bearish", 85))},
        {"quant_technical": lambda r: {}, "valuation": lambda r: {}},
    )
    result = orchestrator.analyze("sbin")
    assert result.conflict is not None and result.verdict["conviction"] <= 40
    assert result.verdict["resolution_path"] == "blend"


def test_an_ambiguous_query_asks_for_clarification_and_runs_nothing():
    ambiguity = Ambiguity("sbi", (Candidate("equity", "SBIN", "State Bank of India", 0.8),), "several matches")
    quant = FakeSpecialist(out())
    orchestrator = Orchestrator(FakeResolver(ambiguity), {"quant_technical": quant}, {"quant_technical": lambda r: {}})
    result = orchestrator.analyze("sbi")
    assert result.status == NEEDS_CLARIFICATION and result.ambiguity is ambiguity
    assert result.verdict is None and result.specialists == {} and quant.packets == []


def test_a_bug_in_a_specialist_is_not_swallowed():
    orchestrator = build({"quant_technical": FakeSpecialist(error=RuntimeError("bug"))})
    with pytest.raises(RuntimeError):
        orchestrator.analyze("sbin")


def test_analyze_resolved_skips_the_resolver_and_always_notes_the_missing_risk_overlay():
    orchestrator = build({"quant_technical": FakeSpecialist(out())})
    result = orchestrator.analyze_resolved(resolution())
    assert orchestrator._resolver.queries == [] and result.query == "SBIN"
    assert any("risk overlay" in note for note in result.notes)


def test_the_result_says_how_many_of_the_routed_specialists_actually_ran():
    result = build({"quant_technical": FakeSpecialist(out())}).analyze("sbin")
    assert result.notes[0] == "only 1 of 2 routed specialists ran, so the verdict reflects just those"


def test_no_coverage_note_when_every_routed_specialist_ran():
    orchestrator = Orchestrator(
        FakeResolver(resolution(routed=("quant_technical",))), {"quant_technical": FakeSpecialist(out())}, {"quant_technical": lambda r: {}}
    )
    notes = orchestrator.analyze("sbin").notes
    assert not any("routed specialists ran" in note for note in notes) and any("risk overlay" in note for note in notes)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_orchestrator.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.orchestrator.orchestrator'`.

- [ ] **Step 3: Write `src/athena/orchestrator/orchestrator.py`**

```python
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
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_orchestrator.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `13 passed`, then `336 passed, 41 skipped`.

- [ ] **Step 5: Mutation check (do not commit these edits)**

In `orchestrator.py`, change `except AthenaError as exc:` to `except ZeroDivisionError as exc:` and run `.venv/Scripts/python -m pytest tests/test_orchestrator.py -q`: expect `test_a_data_failure_in_one_specialist_is_recorded_and_the_others_still_run`, `test_model_failures_are_recorded_as_skipped_specialists` and `test_when_nothing_ran_the_result_is_a_zero_conviction_hold_marked_no_view` to FAIL. Undo it and confirm `git diff` is empty for `orchestrator.py`.

- [ ] **Step 6: Commit and push**

```bash
git add src/athena/orchestrator/orchestrator.py tests/test_orchestrator.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add orchestrator skeleton with resolve, route, run and blend"
git push
```

---

### Task 3: Packet builders and report

**Files:**
- Create: `src/athena/orchestrator/builders.py`, `src/athena/orchestrator/report.py`
- Test: `tests/test_orchestrator_builders.py`, `tests/test_orchestrator_report.py`

**Interfaces:**
- Consumes: `build_technical_packet(instrument, as_of, bars) -> dict` (Plan 1a); `FallbackChain.run(symbol, since=...) -> ChainResult` from `athena.fallback` (`.value` is the bars); `ist_date`, `utc_now`; `OrchestrationResult` and the status constants (Task 2).
- Produces (`athena.orchestrator.builders`): `HISTORY_DAYS = 760`; `history_fetcher(chain, days=760, clock=utc_now) -> Callable[[str], list[Bar]]`; `technical_packet_builder(fetch_bars, clock=utc_now) -> Callable[[Resolution], dict]`.
- Produces (`athena.orchestrator.report`): `DISCLAIMER: str`; `format_result(result) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_orchestrator_builders.py`:

```python
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from athena.contracts import Bar
from athena.orchestrator.builders import HISTORY_DAYS, history_fetcher, technical_packet_builder
from athena.resolver import Resolution

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def bars(count=80):
    days, day = [], date(2026, 10, 2)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return [
        Bar("SBIN", datetime(d.year, d.month, d.day, 18, 30, tzinfo=timezone.utc) - timedelta(days=1),
            100 + i, 101 + i, 99 + i, 100 + i, 1000.0, NOW, "t")
        for i, d in enumerate(reversed(days))
    ]


class FakeChain:
    def __init__(self, value):
        self.value, self.calls = value, []

    def run(self, symbol, **kwargs):
        self.calls.append((symbol, kwargs))
        return SimpleNamespace(value=self.value, source="fake")


def test_history_fetcher_asks_the_chain_for_the_last_760_days_in_ist_dates():
    chain = FakeChain(bars())
    assert history_fetcher(chain, clock=lambda: NOW)("SBIN") == chain.value
    symbol, kwargs = chain.calls[0]
    assert symbol == "SBIN" and kwargs == {"since": date(2026, 10, 5) - timedelta(days=HISTORY_DAYS)}
    assert HISTORY_DAYS == 760


def test_history_fetcher_honours_a_custom_window():
    chain = FakeChain(bars())
    history_fetcher(chain, days=30, clock=lambda: NOW)("SBIN")
    assert chain.calls[0][1]["since"] == date(2026, 9, 5)


def test_technical_packet_builder_builds_a_packet_for_the_resolved_symbol():
    resolution = Resolution("equity", "ticker", "SBIN", "State Bank of India", "", "exact", 1.0, (), ())
    fetched = []
    build = technical_packet_builder(lambda symbol: fetched.append(symbol) or bars(), clock=lambda: NOW)
    packet = build(resolution)
    assert fetched == ["SBIN"] and packet["instrument"] == "SBIN" and packet["as_of"] == NOW.isoformat()
    assert packet["metrics"]["last_close"]["value"] == 179.0
```

`tests/test_orchestrator_report.py`:

```python
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, NO_VIEW, OK, OrchestrationResult
from athena.orchestrator.report import DISCLAIMER, format_result
from athena.resolver import Ambiguity, Candidate, Resolution

RESOLUTION = Resolution("equity", "ticker", "SBIN", "State Bank of India", "INE062A01020", "exact", 1.0, (), ())
SPECIALIST = {"signal": "neutral", "confidence": 35, "reasoning": "Trend is mixed.", "data_coverage": "full", "missing": []}


def result(status=OK, verdict=None, **changes):
    base = dict(
        status=status, query="sbin", resolution=RESOLUTION, ambiguity=None, specialists={"quant_technical": SPECIALIST},
        skipped={"valuation": "not built yet"}, conflict=None, net=0.0,
        verdict=verdict or {"verdict": "Hold", "conviction": 35, "key_risks": ["a risk"], "resolution_path": "blend"},
        notes=("risk overlay missing",),
    )
    base.update(changes)
    return OrchestrationResult(**base)


def test_report_shows_instrument_verdict_specialists_skips_risks_notes_and_disclaimer():
    text = format_result(result())
    for expected in (
        "SBIN  State Bank of India  (equity, exact match)",
        "Verdict: Hold  conviction 35  (blend)",
        "quant_technical: neutral 35  coverage full",
        "Trend is mixed.",
        "Not run: valuation (not built yet)",
        "  - a risk",
        "Note: risk overlay missing",
        DISCLAIMER,
    ):
        assert expected in text, expected


def test_report_flags_a_no_view_result():
    text = format_result(result(NO_VIEW))
    assert "[no specialist had enough data]" in text


def test_report_lists_candidates_for_an_ambiguous_query():
    ambiguity = Ambiguity("sbi", (Candidate("equity", "SBIN", "State Bank of India", 0.81),), "several matches")
    text = format_result(OrchestrationResult(NEEDS_CLARIFICATION, "sbi", None, ambiguity, {}, {}, None, None, None, ()))
    assert "could be more than one instrument (several matches)" in text
    assert "1. SBIN  State Bank of India  (equity, match 0.81)" in text and "Re-run with the exact symbol." in text
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_orchestrator_builders.py tests/test_orchestrator_report.py -q`
Expected: FAIL with `ModuleNotFoundError` for `athena.orchestrator.builders` / `athena.orchestrator.report`.

- [ ] **Step 3: Write the two modules**

`src/athena/orchestrator/builders.py`:

```python
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from athena.clock import utc_now
from athena.contracts import Bar
from athena.fallback import FallbackChain
from athena.resolver import Resolution
from athena.technicals.packet import build_technical_packet
from athena.trading_calendar import ist_date

HISTORY_DAYS = 760  # about 25 months: enough for the monthly trend


def history_fetcher(
    chain: FallbackChain, days: int = HISTORY_DAYS, clock: Callable[[], datetime] = utc_now
) -> Callable[[str], list[Bar]]:
    """Daily bars for a symbol over the last `days`, through an OHLCV fallback chain."""

    def fetch(symbol: str) -> list[Bar]:
        since = ist_date(clock()) - timedelta(days=days)
        return chain.run(symbol, since=since).value

    return fetch


def technical_packet_builder(
    fetch_bars: Callable[[str], Sequence[Bar]], clock: Callable[[], datetime] = utc_now
) -> Callable[[Resolution], dict]:
    """Packet builder for the Quant/Technical specialist: the resolved symbol's bars -> technical packet."""

    def build(resolution: Resolution) -> dict:
        return build_technical_packet(resolution.identifier, clock(), fetch_bars(resolution.identifier))

    return build
```

`src/athena/orchestrator/report.py`:

```python
from __future__ import annotations

from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, NO_VIEW, OrchestrationResult

DISCLAIMER = "A stylized analytical framework, not financial advice; not a registered investment adviser."


def format_result(result: OrchestrationResult) -> str:
    """Plain-text summary of an orchestration result."""
    if result.status == NEEDS_CLARIFICATION and result.ambiguity:
        lines = [f"{result.query!r} could be more than one instrument ({result.ambiguity.reason}):"]
        lines += [
            f"  {i}. {c.identifier}  {c.name}  ({c.asset_class}, match {c.score:.2f})"
            for i, c in enumerate(result.ambiguity.candidates, 1)
        ]
        lines.append("Re-run with the exact symbol.")
        return "\n".join(lines)

    resolution = result.resolution
    assert resolution is not None and result.verdict is not None
    verdict = result.verdict
    lines = [
        f"{resolution.identifier}  {resolution.name}  ({resolution.asset_class}, {resolution.resolution_path} match)",
        f"Verdict: {verdict['verdict']}  conviction {verdict['conviction']}  ({verdict['resolution_path']})"
        + ("  [no specialist had enough data]" if result.status == NO_VIEW else ""),
    ]
    if result.specialists:
        lines.append("Specialists:")
        for name, out in result.specialists.items():
            lines.append(f"  {name}: {out['signal']} {out['confidence']}  coverage {out['data_coverage']}")
            lines.append(f"    {out['reasoning']}")
    if result.skipped:
        lines.append("Not run: " + "; ".join(f"{name} ({why})" for name, why in result.skipped.items()))
    if verdict["key_risks"]:
        lines.append("Key risks:")
        lines += [f"  - {risk}" for risk in verdict["key_risks"]]
    for note in result.notes:
        lines.append(f"Note: {note}")
    lines.append(DISCLAIMER)
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify they pass, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_orchestrator_builders.py tests/test_orchestrator_report.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `6 passed`, then `342 passed, 41 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/orchestrator/builders.py src/athena/orchestrator/report.py tests/test_orchestrator_builders.py tests/test_orchestrator_report.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add technical packet builder and text report"
git push
```

---

### Task 4: Command line, live check, docs and graph refresh

**Files:**
- Create: `src/athena/cli.py`, `tests/test_cli.py`, `tests/live/test_live_orchestrator.py`
- Modify: `TRD.md`

**Interfaces:**
- Consumes: everything above; `build_router` and the `Router` protocol (Plan 1b); `QUANT_TECHNICAL`, `Specialist` (Plan 1a); `ohlcv_chain`, `JugaadPriceAdapter`, `YahooPriceAdapter`; `NseMasterLoader`, `NseHolidayLoader`, `load_calendar`; `InstrumentIndex`, `InstrumentResolver`; `DataStore`; `TradingCalendar`.
- Produces (`athena.cli`): `build_orchestrator(resolver, llm_router, chain, clock=utc_now) -> Orchestrator` (registers `quant_technical` only); `live_orchestrator(env_file=DEFAULT_ENV_FILE) -> Orchestrator` (fresh in-memory store each call; raises `AthenaError` if no provider key is set); `main(argv=None, factory=live_orchestrator) -> int` (0 = report printed, 1 = error, 2 = needs clarification). Usage: `.venv/Scripts/python -m athena.cli SBIN`.

- [ ] **Step 1: Write the failing tests `tests/test_cli.py`**

```python
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from athena.cli import build_orchestrator, main
from athena.contracts import AthenaError, Bar, Record
from athena.evaluation.schema import validate_judge_verdict
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, OK, OrchestrationResult
from athena.resolver import Ambiguity, Candidate, InstrumentIndex, InstrumentResolver, Resolution
from athena.store import DataStore

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def make_resolver():
    store = DataStore()
    for symbol, name in (("SBIN", "State Bank of India"), ("TCS", "Tata Consultancy Services Limited")):
        store.put(Record(EQUITY_DATASET, symbol, NOW, "x", {"name": name, "isin": "INE000000000"}))
    store.put(Record(ETF_DATASET, "NIFTYBEES", NOW, "x", {"name": "NIPINDETFNIFTYBEES", "isin": "INF000000000"}))
    return InstrumentResolver(InstrumentIndex.from_store(store, now=NOW))


def bars(count=400):
    days, day = [], date(2026, 10, 2)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return [
        Bar("X", datetime(d.year, d.month, d.day, 18, 30, tzinfo=timezone.utc) - timedelta(days=1),
            100 + i * 0.5, 101 + i * 0.5, 99 + i * 0.5, 100 + i * 0.5, 1000.0, NOW, "t")
        for i, d in enumerate(reversed(days))
    ]


class Narrator:
    """A fake model that cites real figures from the packet it receives."""

    def complete(self, system, user):
        metrics = json.loads(user[user.index("{") : user.rindex("}") + 1])["metrics"]
        text = f"Last close {metrics['last_close']['value']} with RSI {metrics['rsi_14']['value']} and daily trend {metrics['trend_daily']['value']}."
        return json.dumps({"signal": "bullish", "confidence": 72, "reasoning": text})


class FakeLLMRouter:
    def __init__(self):
        self.roles = []

    def client_for(self, role):
        self.roles.append(role)
        return Narrator()


class FakeChain:
    def run(self, symbol, **kwargs):
        return SimpleNamespace(value=bars(), source="fake")


def test_offline_end_to_end_resolves_fetches_analyzes_and_blends():
    llm_router = FakeLLMRouter()
    orchestrator = build_orchestrator(make_resolver(), llm_router, FakeChain(), clock=lambda: NOW)
    result = orchestrator.analyze("sbin")
    assert llm_router.roles == ["specialist"]
    assert result.status == OK and result.resolution.identifier == "SBIN"
    assert result.specialists["quant_technical"]["data_coverage"] == "full"
    assert set(result.skipped) == {"valuation", "moat_quality", "earnings_intelligence"}
    assert validate_judge_verdict(result.verdict) == []
    assert result.verdict["verdict"] == "Buy" and result.verdict["conviction"] == 72


def test_an_etf_routes_to_quant_technical_and_notes_the_unbuilt_etf_analyst():
    result = build_orchestrator(make_resolver(), FakeLLMRouter(), FakeChain(), clock=lambda: NOW).analyze("NIFTYBEES")
    assert result.resolution.asset_class == "etf" and "quant_technical" in result.specialists
    assert result.skipped == {"etf_analyst": "not built yet"}


class StubOrchestrator:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.queries = result, error, []

    def analyze(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.result


def ok_result():
    resolution = Resolution("equity", "ticker", "SBIN", "State Bank of India", "", "exact", 1.0, (), ())
    verdict = {"verdict": "Hold", "conviction": 10, "key_risks": [], "resolution_path": "blend"}
    return OrchestrationResult(OK, "sbin", resolution, None, {}, {}, None, 0.0, verdict, ())


def test_main_joins_words_prints_the_report_and_returns_zero(capsys):
    stub = StubOrchestrator(ok_result())
    assert main(["state", "bank", "--env-file", "x.env"], factory=lambda env_file: stub) == 0
    assert stub.queries == ["state bank"]
    assert "Verdict: Hold" in capsys.readouterr().out


def test_main_returns_two_when_the_input_is_ambiguous(capsys):
    ambiguity = Ambiguity("sbi", (Candidate("equity", "SBIN", "State Bank of India", 0.8),), "several matches")
    stub = StubOrchestrator(OrchestrationResult(NEEDS_CLARIFICATION, "sbi", None, ambiguity, {}, {}, None, None, None, ()))
    assert main(["sbi"], factory=lambda env_file: stub) == 2
    assert "more than one instrument" in capsys.readouterr().out


def test_main_reports_athena_errors_and_bad_input_without_a_traceback(capsys):
    assert main(["sbin"], factory=lambda env_file: (_ for _ in ()).throw(AthenaError("no LLM provider key is set"))) == 1
    assert "error: no LLM provider key is set" in capsys.readouterr().out
    assert main(["sbin"], factory=lambda env_file: StubOrchestrator(error=ValueError("empty query"))) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.cli'`.

- [ ] **Step 3: Write `src/athena/cli.py`**

```python
from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from athena.adapters.prices import JugaadPriceAdapter, YahooPriceAdapter, ohlcv_chain
from athena.agents.base import Specialist
from athena.agents.quant_technical import QUANT_TECHNICAL
from athena.clock import utc_now
from athena.contracts import AthenaError
from athena.fallback import FallbackChain
from athena.llm.envfile import DEFAULT_ENV_FILE
from athena.llm.router import Router, build_router
from athena.loaders.nse_holidays import DATASET as HOLIDAY_DATASET
from athena.loaders.nse_holidays import NseHolidayLoader, load_calendar
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET, NseMasterLoader
from athena.orchestrator.builders import history_fetcher, technical_packet_builder
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, Orchestrator
from athena.orchestrator.report import format_result
from athena.resolver import InstrumentIndex, InstrumentResolver
from athena.store import DataStore
from athena.trading_calendar import TradingCalendar


def build_orchestrator(
    resolver: InstrumentResolver, llm_router: Router, chain: FallbackChain, clock: Callable[[], datetime] = utc_now
) -> Orchestrator:
    """Wire the specialists that exist (today only Quant/Technical) to their data and models."""
    specialists = {"quant_technical": Specialist(QUANT_TECHNICAL, llm_router.client_for("specialist"))}
    builders = {"quant_technical": technical_packet_builder(history_fetcher(chain, clock=clock), clock)}
    return Orchestrator(resolver, specialists, builders)


def live_orchestrator(env_file: Path | str = DEFAULT_ENV_FILE) -> Orchestrator:
    """Everything wired to real sources: NSE master lists and holidays, jugaad-data then Yahoo for prices, and
    whichever LLM providers have keys. Loads into a fresh in-memory store on every call."""
    llm_router = build_router(env_file=env_file)
    store = DataStore()
    loader = NseMasterLoader(store)
    loader.refresh(EQUITY_DATASET)
    loader.refresh(ETF_DATASET)
    NseHolidayLoader(store).refresh()
    years = [int(record.key) for record in store.latest_records(HOLIDAY_DATASET)]
    calendar = load_calendar(store, years) if years else TradingCalendar(frozenset())
    resolver = InstrumentResolver(InstrumentIndex.from_store(store, calendar=calendar))
    chain = ohlcv_chain([JugaadPriceAdapter(), YahooPriceAdapter()], calendar)
    return build_orchestrator(resolver, llm_router, chain)


def main(argv: list[str] | None = None, factory: Callable[..., Orchestrator] = live_orchestrator) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.cli", description="Analyze one instrument.")
    parser.add_argument("query", nargs="+", help="ticker, ISIN or name, for example SBIN")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    args = parser.parse_args(argv)
    try:
        result = factory(env_file=args.env_file).analyze(" ".join(args.query))
    except (AthenaError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    print(format_result(result))
    return 2 if result.status == NEEDS_CLARIFICATION else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `5 passed`, then `347 passed, 41 skipped`.

- [ ] **Step 5: Write the live tests `tests/live/test_live_orchestrator.py`**

```python
import pytest

from athena.cli import live_orchestrator
from athena.contracts import AthenaError
from athena.evaluation.schema import validate_judge_verdict, validate_specialist_output
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, OK
from athena.orchestrator.report import format_result

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def orchestrator():
    try:
        return live_orchestrator()
    except AthenaError:
        pytest.skip("no LLM provider key is set")


@pytest.mark.parametrize("query, asset_class", [("SBIN", "equity"), ("NIFTYBEES", "etf")])
def test_live_orchestrator_analyzes_an_instrument_end_to_end(orchestrator, query, asset_class):
    result = orchestrator.analyze(query)
    print("\n" + format_result(result))
    assert result.status == OK, result.skipped
    assert result.resolution.asset_class == asset_class
    assert validate_judge_verdict(result.verdict) == []
    output = result.specialists["quant_technical"]
    assert validate_specialist_output(output) == [] and output["data_coverage"] == "full"


def test_live_orchestrator_asks_instead_of_guessing_on_an_ambiguous_name(orchestrator):
    result = orchestrator.analyze("SBI")
    assert result.status == NEEDS_CLARIFICATION and result.ambiguity.candidates
    assert result.specialists == {} and result.verdict is None
```

- [ ] **Step 6: Run the live tests and the command line**

```bash
.venv/Scripts/python -m pytest --live tests/live/test_live_orchestrator.py -q -s
.venv/Scripts/python -m athena.cli NIFTYBEES
```

Expected: `3 passed` with a printed report for SBIN and for NIFTYBEES, then the same report format from the command line. The numbers and verdicts change from day to day (and a model may be rate-limited on a given run: the chain falls through to the next provider); the assertions are on structure only. If a run reports `error: no LLM provider key is set`, ask the user to check `.env`: the assistant cannot read it.

- [ ] **Step 7: Update `TRD.md`**

1. At the end of the §2.1 paragraph (`**2.1 Orchestrator / Portfolio Manager agent.**`), append: ` *Skeleton implemented in Plan 1c:* resolve, route by the §3 table, run the specialists that exist, and a conviction-weighted blend (resolution_path "blend") with TRD §2.15 conflict detection; the debate and judge, the risk overlay and the specialists other than Quant/Technical are not built. Try it with \`python -m athena.cli SBIN\`.`
2. In the revision history, add after the Phase 1b line: `- **Oct 5, 2026 (Phase 1c)** — Orchestrator skeleton and command line implemented (see docs/superpowers/plans/2026-10-05-phase-1c-orchestrator-skeleton.md).`

- [ ] **Step 8: Commit, push, refresh graph**

```bash
git add src/athena/cli.py tests/test_cli.py tests/live/test_live_orchestrator.py TRD.md
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add athena command line and live orchestrator check; document orchestrator in TRD"
git push
.venv/Scripts/python -m graphify update .
```

---

## Self-Review (completed)

**Spec coverage (TRD §2.1, §2.15, §9 item 1 -> task):** resolve and route by the §3 table (Task 2 uses `Resolution.routed_specialists`); collect verdicts and detect conflicts by the §2.15 trigger (Task 1: both confidences >= 60, neutral never conflicts, `insufficient` never takes part); simple blend into the judge-verdict contract (Task 1, validated in every test); debate stubbed and said so in `key_risks` and capped conviction (Tasks 1-2); conviction capped by coverage (Task 1); honest listing of unbuilt or failed specialists and of the missing risk overlay (Task 2); end-to-end command line (Task 4). Not in this plan: debate and judge (Phase 4), risk overlay (§2.7), other specialists (Plan 1d), calibration of the blend constants (needs golden sets), persistence.

**Placeholder scan:** none; every code block is the file that passed the prototype run.

**Type consistency:** `blend_signals`/`BlendResult`/`Conflict`, `OrchestrationResult` fields, `Orchestrator.analyze`/`analyze_resolved`, `history_fetcher`/`technical_packet_builder`, `format_result`, `build_orchestrator`/`live_orchestrator`/`main` and the status constants match across Tasks 1-4. Test totals: 299 + 24 + 13 + 6 + 5 = 347 passed; skipped 41 + 3 live = 44.
