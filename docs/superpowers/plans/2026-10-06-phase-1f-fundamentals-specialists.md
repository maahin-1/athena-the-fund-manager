# Phase 1f — Valuation, Moat & Quality and Earnings Intelligence Specialists Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the Plan 1e fundamentals to work: three new specialists (TRD §2.3) that read the valuation, quality and earnings figures and return the standard specialist output, routed and blended with Quant/Technical by the Plan 1c orchestrator, so a stock verdict rests on four views instead of one.

**Architecture:** A specialist is still only data: a persona string plus the figures it must have (`critical`), may have (`optional`) and is shown (`shows`). Two small additions to the shared `Specialist` base make that work for fundamentals: `SpecialistSpec.shows` limits what the model sees (and what its numbers are checked against), and a packet's `not_applicable` list marks figures that cannot exist for this instrument (a bank has no free cash flow), so they count neither as missing nor against coverage. All three specialists read one packet, fetched once per request through a shared `LiveFundamentals` source, which the dashboard shares too. The orchestrator, blend and report from Plan 1c need no change.

**Tech Stack:** Python >= 3.11, pytest. No new dependencies.

**Spec:** `TRD.md` §2.2 (base class), §2.3 (equity specialists, ratio-only mode), §3 (output contract), §8 (persona shape); `PRD.md` FR-1, FR-2.

**Plan series:** 0a-0e, 1a-1e (done) -> **1f (this plan)** -> 1g (stock backtest) and the risk overlay -> later phases (debt, ETF analyst, mutual funds, full arbitration, options, live feed, brokers).

**Suggested models:** Sonnet at medium effort, inline execution (three code tasks plus a live task). Prototyped end to end in a scratch copy first: 458 offline tests passed, three deliberate mutations were each caught, and the live tests ran all four specialists on real data with real models for a stock and a bank.

## Verified findings (6 Oct 2026)

Live runs through the real resolver, real prices, Yahoo statements and the Plan 1b model chain (NVIDIA first). Illustrative, not recommendations:

| Stock | Specialists (signal, confidence) | Verdict |
| --- | --- | --- |
| TCS | valuation bearish 60-70, moat & quality bullish 82, quant/technical bearish 68, earnings neutral 45-55 | Hold, conviction 40 (a conflict is flagged: the debate step is not built, so conviction is capped) |
| SBIN (bank) | valuation bullish 85, moat & quality bullish 80, quant/technical neutral 35, earnings bullish 80, all four at **full** coverage | Buy, conviction 61 |
| ITC | valuation bearish 60, moat & quality neutral 60, quant/technical bearish 55, earnings neutral 58 | Underweight, conviction 29 |

Every reasoning text cited figures that exist in the data it was shown, and the numbers read sensibly (for example the moat specialist described TCS's return on equity of 0.487 as "high and stable across the cycle").

**Two real defects the live runs exposed, both fixed and tested in this plan:**
1. **A unit the model read backwards.** `index_pe_percentile` was reported with unit `percent` but came out as 0.86 (meaning the 0.9th percentile: the NIFTY 50 P/E is within 17 trading days of its eight-year low). The valuation specialist read 0.86 as a fraction and wrote "the market P/E is historically high at the 85.8th percentile", the opposite of the truth. The number-grounding check passed it, because it deliberately accepts a fraction cited as a percentage. Fix: the figure is now a plain 0-1 fraction like every other share, its note says which direction is cheap, and the valuation persona spells out how to read it.
2. **Fractions read as tiny percentages.** One run called a return on equity of 0.487 "only 0.487" (it is 48.7%). Fix: every specialist's instructions now state how to read each unit (a `fraction` is a decimal share, `percentage points` are already percent, a `ratio` is a plain multiple). After both fixes, repeat runs on TCS and ITC read the figures correctly.

**Honest limits:**
- The grounding check proves a cited number exists in the data, not that it was *interpreted* correctly; the two defects above are exactly that gap. The persona and unit guidance reduce it, and the golden sets (a later plan) are what will measure it.
- Models are free-tier and non-deterministic: repeat runs shift confidences by a few points and a specialist can occasionally fail validation twice and be skipped (the orchestrator then lists it and notes "only N of M routed specialists ran"). The live tests tolerate that (at least two of the three must answer).
- With four specialists, disagreement is now common (quality bullish, price and trend bearish). The blend caps conviction at 40 and flags it; resolving such conflicts is the debate step (Phase 4).
- Valuation is ratio-only: no discounted cash flow, no peer comparables, no Graham net-net. Moat evidence is ratios only, and the persona forbids naming a moat source.
- The Quant/Technical specialist is unchanged and still sees the whole technical packet.

## Global Constraints

- No network in the default test run; the live tests are opt-in via `--live` and use the keys in `.env` (read by the code at runtime; the assistant cannot read `.env`).
- A specialist cites only figures it was shown (`shows`), and the grounding check runs against that same view.
- A figure marked not applicable is never treated as missing: it does not lower coverage, is not listed in `missing`, and the prompt tells the model it is not a data gap.
- Compute in code, judge in prompts: personas say "do not calculate your own"; no model produces a number the packet does not contain.
- Compliance language in every persona: "not financial advice; not a registered investment adviser", no price targets or fair values, a base case and a named risk.
- Commit with the GitHub no-reply identity: `git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit ...`; `git push` after each task.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/athena/agents/base.py` | modified: `SpecialistSpec.shows`, `not_applicable` handling, unit guidance in the instructions |
| `src/athena/metrics/fundamentals.py` | modified: `not_applicable` in the packet; `index_pe_percentile` is a 0-1 fraction |
| `src/athena/agents/valuation.py`, `moat_quality.py`, `earnings_intelligence.py` | the three specialists: persona, `CRITICAL`, `OPTIONAL`, `SHOWS`, spec |
| `src/athena/orchestrator/builders.py` | modified: `RequestCache` moves here |
| `src/athena/orchestrator/fundamentals_source.py` | `LiveFundamentals`: one fetch per symbol per request |
| `src/athena/cli.py` | modified: registers the three specialists, `LiveSources` gains `bars` and `fundamentals` |
| `src/athena/dashboard/service.py` | modified: uses the shared sources, clears them per request |
| `tests/test_specialist_visibility.py`, `test_metrics_not_applicable.py`, `test_fundamentals_specialists.py`, `test_fundamentals_source.py`, `test_cli_fundamentals.py`, `tests/live/test_live_specialists.py` | one test module per concern, plus the live check |
| `tests/test_metrics_fundamentals.py`, `test_orchestrator_builders.py`, `test_dashboard_service.py` | modified |

---

### Task 1: Base-class visibility, not-applicable figures and unambiguous units

**Files:**
- Modify (replace the whole file with the version below): `src/athena/agents/base.py`, `src/athena/metrics/fundamentals.py`, `tests/test_metrics_fundamentals.py`
- Create: `tests/test_specialist_visibility.py`, `tests/test_metrics_not_applicable.py`

**Interfaces:**
- Produces (`athena.agents.base`): `SpecialistSpec(name, persona, critical, optional=(), shows=None)`; `Specialist.analyze(packet)` now (a) drops any name in `packet["not_applicable"]` from `critical` and `optional` before deriving coverage, (b) shows the model only the metrics named in `spec.shows` when it is not `None` (packet-level fields such as `sector` and `data_quality_flags` always stay), runs number grounding against that same view, and filters `missing`, `missing_reasons` and `not_applicable` to it, (c) adds a prompt line `Not applicable to this instrument (this is not a data gap; ...)` when the view has not-applicable figures; the system prompt adds one sentence explaining the units.
- Produces (`athena.metrics.fundamentals`): the packet gains `not_applicable: list[str]` (the metrics whose reason is `LENDER_REASON`); `index_pe_percentile` is a fraction 0-1 (unit `fraction`) with a note that low means the market is cheap against its own history.

- [ ] **Step 1: Write the failing tests**

`tests/test_specialist_visibility.py`:

```python
import json

import pytest

from athena.agents.base import Specialist, SpecialistError, SpecialistSpec

REPLY_A = json.dumps({"signal": "bullish", "confidence": 70, "reasoning": "Metric a is 42.5."})
WIDE = {
    "instrument": "X",
    "sector": "Technology",
    "metrics": {"a": {"value": 42.5}, "b": {"value": 7.25}, "c": {"value": 9.5}},
    "missing": ["z"],
    "missing_reasons": {"z": "why"},
}


class Scripted:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete(self, system, user):
        self.calls.append((system, user))
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def test_a_specialist_that_declares_shows_is_only_shown_those_figures_plus_the_packet_level_facts():
    spec = SpecialistSpec("Test", "You are a test analyst.", critical=("a",), optional=("b",), shows=("a",))
    llm = Scripted(REPLY_A)
    Specialist(spec, llm).analyze(WIDE)
    prompt = llm.calls[0][1]
    assert "42.5" in prompt and "7.25" not in prompt and "9.5" not in prompt
    assert '"sector": "Technology"' in prompt and '"z"' not in prompt  # unshown figures vanish from missing, too


def test_grounding_is_checked_against_what_the_model_was_shown():
    spec = SpecialistSpec("Test", "p", critical=("a",), shows=("a",))
    cites_a_hidden_figure = json.dumps({"signal": "bullish", "confidence": 70, "reasoning": "Metric b is 7.25."})
    with pytest.raises(SpecialistError) as caught:
        Specialist(spec, Scripted(cites_a_hidden_figure)).analyze(WIDE)
    assert "figures not found in the data" in str(caught.value)


def test_without_shows_the_whole_packet_is_shown_as_before():
    spec = SpecialistSpec("Test", "p", critical=("a",), optional=("b",))
    llm = Scripted(REPLY_A)
    Specialist(spec, llm).analyze(WIDE)
    assert "9.5" in llm.calls[0][1]


def test_figures_marked_not_applicable_are_neither_required_nor_missing_and_the_prompt_says_so():
    spec = SpecialistSpec("Test", "p", critical=("a", "z"), optional=("b",))
    packet = {"metrics": {"a": {"value": 42.5}}, "not_applicable": ["z", "b"]}
    llm = Scripted(REPLY_A)
    out = Specialist(spec, llm).analyze(packet)
    assert (out["data_coverage"], out["missing"]) == ("full", [])
    assert "not a data gap" in llm.calls[0][1] and "z, b" in llm.calls[0][1]


def test_a_missing_figure_that_is_not_marked_not_applicable_still_counts_against_coverage():
    spec = SpecialistSpec("Test", "p", critical=("a",), optional=("b",))
    out = Specialist(spec, Scripted(REPLY_A)).analyze({"metrics": {"a": {"value": 42.5}}, "not_applicable": []})
    assert (out["data_coverage"], out["missing"]) == ("partial", ["b"])


def test_the_system_prompt_explains_how_to_read_the_units():
    llm = Scripted(REPLY_A)
    Specialist(SpecialistSpec("Test", "You are a test analyst.", critical=("a",)), llm).analyze(WIDE)
    system = llm.calls[0][0]
    assert "0.25 means 25 percent" in system and "percentage points" in system and "plain multiple" in system
```

`tests/test_metrics_not_applicable.py`:

```python
import copy
from datetime import datetime, timezone

from fund_fixtures import ACME, PRICE, index_history

from athena.metrics.fundamentals import LENDER_REASON, build_fundamentals_packet

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def packet(payload=ACME, price=PRICE):
    return build_fundamentals_packet("ACME", NOW, payload, price, NOW, index_history())


def test_a_company_has_no_not_applicable_figures():
    assert packet()["not_applicable"] == []


def test_lender_only_gaps_are_listed_as_not_applicable():
    bank = copy.deepcopy(ACME)
    bank["info"]["sector"] = "Financial Services"
    p = packet(bank)
    assert {"fcf_yield", "owner_earnings_yield", "accruals_ratio", "cash_conversion", "debt_to_equity"} <= set(p["not_applicable"])
    assert set(p["not_applicable"]) <= set(p["missing"])
    assert all(p["missing_reasons"][name] == LENDER_REASON for name in p["not_applicable"])


def test_a_genuine_gap_is_not_marked_not_applicable():
    p = packet(price=None)
    assert "pe_trailing" in p["missing"] and "pe_trailing" not in p["not_applicable"]
```

`tests/test_metrics_fundamentals.py` (replace; the percentile expectation changes to a fraction and gains a unit and note check):

```python
import copy
import json
import pytest

from athena.metrics.fundamentals import LENDER_REASON, build_fundamentals_packet

from fund_fixtures import ACME, NOW, PRICE, QUARTERS, annual, by, index_history


def packet(payload=ACME, price=PRICE, history=None, **overrides):
    return build_fundamentals_packet("ACME", NOW, payload, price, NOW, history if history is not None else index_history(), **overrides)


def value(p, name):
    return p["metrics"][name]["value"]


def test_valuation_figures_match_a_hand_calculation():
    p = packet()
    assert value(p, "last_price") == PRICE and value(p, "market_cap") == pytest.approx(2662.0)
    assert value(p, "pe_trailing") == pytest.approx(20.0, abs=1e-3)  # 266.2 over 3.0 + 3.2 + 3.4 + 3.71
    assert value(p, "pb") == pytest.approx(3.3275, abs=1e-3)  # book value 80 per share
    assert value(p, "earnings_yield") == pytest.approx(0.05, abs=1e-4)
    assert value(p, "fcf_yield") == pytest.approx(100 / 2662, abs=1e-4)  # (160 - 60) over market cap
    assert value(p, "owner_earnings_yield") == pytest.approx(113.1 / 2662, abs=1e-4)  # 133.1 + 40 - 60
    assert value(p, "peg") == pytest.approx(2.0, abs=5e-3)  # P/E 20 over 10 percent growth
    assert value(p, "graham_number_premium") == pytest.approx(266.2 / (22.5 * 13.31 * 80) ** 0.5 - 1, abs=1e-4)
    assert value(p, "dividend_yield") == pytest.approx(5 / 266.2, abs=1e-4)


def test_index_context_compares_the_stock_with_the_nifty_50():
    p = packet()
    assert value(p, "index_pe") == 19.3 and value(p, "pe_vs_index") == pytest.approx(20 / 19.3, abs=1e-3)
    assert value(p, "index_pe_percentile") == pytest.approx(150 / 300, abs=1e-4)  # 149 days at 18.0 plus itself, below the 150 at 21.0
    assert p["metrics"]["index_pe_percentile"]["unit"] == "fraction" and "low means the market is cheap" in p["metrics"]["index_pe_percentile"]["note"]


def test_quality_figures_match_a_hand_calculation():
    p = packet()
    assert value(p, "roe_latest") == pytest.approx(133.1 / 750, abs=1e-4)  # average of 800 and 700
    assert value(p, "roe_minimum") == pytest.approx(133.1 / 750, abs=1e-4)
    assert 0.17 < value(p, "roe_average") < 0.21
    assert value(p, "net_margin_latest") == pytest.approx(0.1, abs=1e-6)
    assert value(p, "operating_margin_latest") == pytest.approx(0.15, abs=1e-6)
    assert value(p, "operating_margin_change") == pytest.approx(2.0, abs=1e-6)  # 13 percent to 15 percent
    assert value(p, "gross_margin_latest") == pytest.approx(0.4, abs=1e-6)
    assert value(p, "revenue_cagr") == pytest.approx(0.1, abs=2e-3) and value(p, "earnings_cagr") == pytest.approx(0.1, abs=2e-3)
    assert value(p, "debt_to_equity") == pytest.approx(0.25) and value(p, "current_ratio") == pytest.approx(2.0)
    assert value(p, "interest_coverage") == pytest.approx(199.65 / 20, abs=1e-3)
    assert value(p, "asset_turnover") == pytest.approx(1331 / 1250, abs=1e-3)


def test_earnings_figures_match_a_hand_calculation():
    p = packet()
    assert value(p, "accruals_ratio") == pytest.approx((133.1 - 160) / 1250, abs=1e-4)
    assert value(p, "cash_conversion") == pytest.approx(160 / 133.1, abs=1e-4)
    assert value(p, "revenue_growth_yoy_quarter") == pytest.approx(0.15) and value(p, "net_income_growth_yoy_quarter") == pytest.approx(0.2)
    assert value(p, "eps_surprise_last") == pytest.approx(0.06) and value(p, "beats_last_4") == 3
    assert value(p, "eps_surprise_average_4") == pytest.approx((6.0 + 3.03 - 3.03 + 3.45) / 400, abs=1e-4)
    assert value(p, "days_to_next_earnings") == 15


def test_a_complete_company_has_nothing_missing_and_every_metric_names_its_group_unit_inputs_and_window():
    p = packet()
    assert p["missing"] == [] and p["missing_reasons"] == {}
    for name, metric in p["metrics"].items():
        assert metric["group"] in ("valuation", "quality", "earnings") and metric["unit"] and metric["inputs"] and metric["window"], name
    assert p["latest_annual_period"] == "2026-03-31" and p["latest_quarter"] == "2026-06-30" and p["is_lender"] is False


def test_the_packet_is_json_ready():
    json.dumps(packet())


def test_a_lender_gets_no_cash_flow_or_working_capital_ratios_with_the_reason_stated():
    bank = copy.deepcopy(ACME)
    bank["info"]["sector"] = "Financial Services"
    for line in ("Operating Income", "EBIT", "Gross Profit", "Interest Expense"):
        del bank["annual"]["income"][line]
    for line in ("Current Assets", "Current Liabilities"):
        del bank["annual"]["balance"][line]
    p = packet(bank)
    assert p["is_lender"] is True
    for name in ("fcf_yield", "owner_earnings_yield", "operating_margin_latest", "operating_margin_change", "gross_margin_latest",
                 "debt_to_equity", "current_ratio", "interest_coverage", "asset_turnover", "accruals_ratio", "cash_conversion"):
        assert p["missing_reasons"][name] == LENDER_REASON, name
    for name in ("pe_trailing", "pb", "graham_number_premium", "roe_latest", "net_margin_latest", "revenue_cagr", "eps_surprise_last", "peg"):
        assert name in p["metrics"], name


def test_without_a_price_every_price_based_figure_is_missing_but_fundamentals_still_compute():
    p = packet(price=None)
    for name in ("last_price", "market_cap", "pe_trailing", "pb", "fcf_yield", "peg", "graham_number_premium", "dividend_yield"):
        assert p["missing_reasons"][name] == "no current price", name
    assert "roe_latest" in p["metrics"] and "revenue_cagr" in p["metrics"] and "eps_surprise_last" in p["metrics"]


def test_losses_and_stalled_growth_remove_the_ratios_that_would_mislead():
    losing = copy.deepcopy(ACME)
    losing["quarterly"]["income"]["Diluted EPS"] = by(QUARTERS, [-1, -1, -1, -1, -1], 1)
    p = packet(losing)
    assert p["missing_reasons"]["pe_trailing"] == "earnings per share is not positive"
    assert "graham_number_premium" in p["missing"] and "earnings_yield" in p["missing"]
    flat = copy.deepcopy(ACME)
    flat["annual"]["income"]["Net Income"] = annual([100, 100, 100, 100])
    assert packet(flat)["missing_reasons"]["peg"] == "earnings are not growing"


def test_too_few_fiscal_years_remove_the_growth_and_persistence_figures():
    short = copy.deepcopy(ACME)
    for group in short["annual"].values():
        for line, series in group.items():
            group[line] = {period: v for period, v in series.items() if period >= "2025-03-31"}
    p = packet(short)
    for name in ("revenue_cagr", "earnings_cagr", "peg", "roe_average", "roe_minimum"):
        assert name in p["missing"], name
    assert "at least 3 annual periods" in p["missing_reasons"]["revenue_cagr"]
    assert "roe_latest" in p["metrics"]


def test_a_gap_in_the_quarters_falls_back_to_annual_eps_and_says_so():
    gappy = copy.deepcopy(ACME)
    gappy["quarterly"]["income"]["Diluted EPS"]["2025-12-31"] = None
    p = packet(gappy)
    assert value(p, "pe_trailing") == pytest.approx(PRICE / 13.31, abs=1e-3)
    assert "annual EPS used" in p["metrics"]["pe_trailing"]["note"]
    assert "fiscal year to 2026-03-31" in p["metrics"]["pe_trailing"]["window"]


def test_quarter_growth_needs_the_same_quarter_a_year_earlier():
    no_base = copy.deepcopy(ACME)
    del no_base["quarterly"]["income"]["Total Revenue"]["2025-06-30"]
    p = packet(no_base)
    assert p["missing_reasons"]["revenue_growth_yoy_quarter"] == "the same quarter a year earlier is not available"
    assert "net_income_growth_yoy_quarter" in p["metrics"]


def test_a_short_index_history_keeps_the_index_pe_but_drops_the_percentile():
    short = {d: row for d, row in list(index_history().items())[:50]}
    p = packet(history=short)
    assert "index_pe" in p["metrics"] and "index_pe_percentile" in p["missing"]
    assert "pe_vs_index" in p["metrics"]


def test_no_upcoming_date_and_no_dividend_are_reported_not_invented():
    quiet = copy.deepcopy(ACME)
    quiet["earnings_dates"] = quiet["earnings_dates"][1:]
    quiet["info"]["dividendRate"] = None
    p = packet(quiet)
    assert p["missing_reasons"]["days_to_next_earnings"] == "no upcoming earnings date"
    assert p["missing_reasons"]["dividend_yield"] == "no dividend reported"


def test_an_empty_payload_is_all_missing_not_an_error_and_flags_travel_with_the_packet():
    empty = packet({}, history={})
    assert empty["metrics"].keys() == {"last_price"} and len(empty["missing"]) > 20
    flagged = copy.deepcopy(ACME)
    flagged["quality_flags"] = ["quarterly periods x, y carry identical figures (possible duplicate); treated as missing"]
    assert packet(flagged)["data_quality_flags"] == flagged["quality_flags"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_specialist_visibility.py tests/test_metrics_not_applicable.py tests/test_metrics_fundamentals.py -q`
Expected: FAIL (`TypeError: ... unexpected keyword argument 'shows'` and `KeyError: 'not_applicable'`, plus the percentile assertion).

- [ ] **Step 3: Replace `src/athena/agents/base.py` and `src/athena/metrics/fundamentals.py`**

`src/athena/agents/base.py`:

```python
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from athena.contracts import AthenaError, Coverage
from athena.coverage import derive_coverage
from athena.evaluation.grounding import check_grounded
from athena.evaluation.schema import validate_specialist_output

MODEL_KEYS = ("signal", "confidence", "reasoning")
OUTPUT_INSTRUCTIONS = (
    'Reply with one JSON object and nothing else: {"signal": "bullish"|"bearish"|"neutral", '
    '"confidence": 0-100, "reasoning": "2-4 sentences citing specific figures from the data"}. '
    "Cite only figures that appear in the data. Every figure carries a unit: a \"fraction\" is a decimal share (0.25 means 25 percent, so a return on equity of 0.487 is a very high 48.7 percent), \"percentage points\" are already in percent, and a \"ratio\" is a plain multiple."
)


class LLMClient(Protocol):
    """Anything that turns a system prompt and a user message into text. Provider choice lives behind this."""

    def complete(self, system: str, user: str) -> str: ...


class SpecialistError(AthenaError):
    """The model never produced an output that passed validation (fail-loud mode)."""


@dataclass(frozen=True)
class SpecialistSpec:
    """A specialist is only data: a name, a persona prompt, and the metrics it must / may have."""

    name: str
    persona: str
    critical: tuple[str, ...]
    optional: tuple[str, ...] = ()
    shows: tuple[str, ...] | None = None  # the only figures the model is shown; None means the whole packet


def abstention(missing: Sequence[str], reason: str) -> dict[str, Any]:
    return {
        "signal": "neutral",
        "confidence": 0,
        "reasoning": reason,
        "data_coverage": Coverage.INSUFFICIENT.value,
        "missing": list(missing),
    }


def parse_model_json(text: str) -> dict[str, Any]:
    """The first JSON object in `text`, tolerating a markdown fence or a sentence around it."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text[text.find("{") : text.rfind("}") + 1] if "{" in text else ""
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"no JSON object found in the reply ({exc.msg})") from exc
    if not isinstance(parsed, dict):
        raise ValueError("the reply is not a JSON object")
    return parsed


class Specialist:
    """Shared plumbing for every specialist (TRD 2.2): coverage from declared inputs, abstention
    without calling the model, JSON parsing, contract validation, number grounding, retry with
    feedback, response caching, and fail-loud / fail-quiet handling."""

    def __init__(self, spec: SpecialistSpec, llm: LLMClient, fail_quiet: bool = False, max_attempts: int = 2):
        self.spec = spec
        self.llm = llm
        self.fail_quiet = fail_quiet
        self.max_attempts = max_attempts
        self._cache: dict[str, dict[str, Any]] = {}
        self.model_calls = 0

    def analyze(self, packet: Mapping[str, Any]) -> dict[str, Any]:
        not_applicable = set(packet.get("not_applicable", ()))  # meaningless for this instrument, so not a data gap
        critical = tuple(name for name in self.spec.critical if name not in not_applicable)
        optional = tuple(name for name in self.spec.optional if name not in not_applicable)
        coverage, missing = derive_coverage(critical, optional, packet.get("metrics", {}))
        if coverage is Coverage.INSUFFICIENT:
            critical_gaps = [name for name in missing if name in critical]
            return abstention(missing, f"No view formed: required inputs are missing ({', '.join(critical_gaps)}).")
        shown = self._visible(packet)

        system = f"{self.spec.persona}\n\n{OUTPUT_INSTRUCTIONS}"
        user = self._render(shown, coverage, missing)
        key = hashlib.sha256(f"{system}\n---\n{user}".encode()).hexdigest()
        if key in self._cache:
            return json.loads(json.dumps(self._cache[key]))

        feedback = ""
        errors: list[str] = []
        for _ in range(self.max_attempts):
            self.model_calls += 1
            reply = self.llm.complete(system, user + feedback)
            output, errors = self._check(reply, shown, coverage, missing)
            if not errors:
                self._cache[key] = output
                return json.loads(json.dumps(output))
            feedback = "\n\nYour previous reply was rejected: " + "; ".join(errors) + ". Reply again, fixing this."

        if self.fail_quiet:
            return abstention(["valid_model_output"], "No view formed: the model output failed validation.")
        raise SpecialistError(f"{self.spec.name}: no valid output after {self.max_attempts} attempts: {errors}")

    def _visible(self, packet: Mapping[str, Any]) -> Mapping[str, Any]:
        """The packet as the model sees it. A specialist that declares `shows` gets only those figures, and the
        number-grounding check runs against the same view, so it cannot cite a figure it was never shown."""
        if self.spec.shows is None:
            return packet
        names = set(self.spec.shows)
        listed = ("metrics", "missing", "missing_reasons", "groups", "not_applicable")
        view = {key: value for key, value in packet.items() if key not in listed}
        view["metrics"] = {name: metric for name, metric in packet.get("metrics", {}).items() if name in names}
        view["missing"] = [name for name in packet.get("missing", []) if name in names]
        view["missing_reasons"] = {n: why for n, why in packet.get("missing_reasons", {}).items() if n in names}
        view["not_applicable"] = [name for name in packet.get("not_applicable", []) if name in names]
        return view

    def _render(self, packet: Mapping[str, Any], coverage: Coverage, missing: Sequence[str]) -> str:
        lines = [f"Specialist: {self.spec.name}", "Data (metrics packet):", json.dumps(packet, indent=2, sort_keys=True)]
        if packet.get("not_applicable"):
            lines.append(
                f"Not applicable to this instrument (this is not a data gap; do not call it missing): "
                f"{', '.join(packet['not_applicable'])}."
            )
        if coverage is Coverage.PARTIAL:
            lines.append(
                f"Data coverage is PARTIAL. Missing: {', '.join(missing)}. "
                "Say which of your conclusions this gap affects, and qualify them."
            )
        return "\n".join(lines)

    def _check(
        self, reply: str, packet: Mapping[str, Any], coverage: Coverage, missing: Sequence[str]
    ) -> tuple[dict[str, Any], list[str]]:
        try:
            parsed = parse_model_json(reply)
        except ValueError as exc:
            return {}, [str(exc)]
        extra = sorted(parsed.keys() - set(MODEL_KEYS))
        if extra:
            return {}, [f"reply has keys the contract does not allow: {extra}"]
        output = {**parsed, "data_coverage": coverage.value, "missing": list(missing)}
        errors = validate_specialist_output(output)
        if errors:
            return {}, errors
        grounding = check_grounded(output["reasoning"], packet)
        if not grounding.ok:
            return {}, [f"figures not found in the data: {list(grounding.ungrounded)}"]
        return output, []
```

`src/athena/metrics/fundamentals.py`:

```python
from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, NamedTuple

from athena.contracts import InsufficientData
from athena.trading_calendar import ist_date

FINANCIAL_SECTORS = frozenset({"Financial Services"})
LENDER_REASON = "not meaningful for banks and lenders"
CRORE = 1e7
SOURCE = "computed from Yahoo statements (INR)"
GRAHAM_MULTIPLIER = 22.5  # Graham's ceiling: P/E 15 x P/B 1.5
QUARTER_SPAN_DAYS = 290  # four consecutive quarter-ends span about 273 days
YEAR_WINDOW = (350, 380)  # days between a quarter and the same quarter a year earlier
MIN_INDEX_HISTORY = 250  # trading days needed before an index percentile means anything
VALUATION, QUALITY, EARNINGS = "valuation", "quality", "earnings"
GROUPS = (VALUATION, QUALITY, EARNINGS)


class R(NamedTuple):
    value: float | str
    window: str
    note: str | None = None


def _periods(series: Mapping[str, float | None]) -> list[tuple[date, float]]:
    return sorted((date.fromisoformat(period), value) for period, value in series.items() if value is not None)


@dataclass(frozen=True)
class Statements:
    """Parsed `equity.fundamentals` payload with small accessors; missing items give empty series, never errors."""

    info: Mapping[str, Any]
    annual: Mapping[str, Mapping[str, Mapping[str, float | None]]]
    quarterly: Mapping[str, Mapping[str, Mapping[str, float | None]]]
    earnings_dates: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Statements:
        return cls(
            payload.get("info", {}), payload.get("annual", {}), payload.get("quarterly", {}), tuple(payload.get("earnings_dates", ()))
        )

    @property
    def is_financial(self) -> bool:
        return self.info.get("sector") in FINANCIAL_SECTORS

    def annual_series(self, group: str, line: str) -> list[tuple[date, float]]:
        return _periods(self.annual.get(group, {}).get(line, {}))

    def quarterly_series(self, line: str) -> list[tuple[date, float]]:
        return _periods(self.quarterly.get("income", {}).get(line, {}))


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise InsufficientData(message)


def _span(points: list[tuple[date, float]]) -> str:
    return f"{points[0][0].isoformat()}..{points[-1][0].isoformat()} ({len(points)} periods)"


def _aligned(first: list[tuple[date, float]], second: list[tuple[date, float]]) -> list[tuple[date, float, float]]:
    other = dict(second)
    return [(day, value, other[day]) for day, value in first if day in other]


def _cagr(points: list[tuple[date, float]], what: str) -> tuple[float, str]:
    _need(len(points) >= 3, f"{what} needs at least 3 annual periods, have {len(points)}")
    (start, first), (end, last) = points[0], points[-1]
    _need(first > 0 and last > 0, f"{what} needs positive first and last values")
    years = (end - start).days / 365.25
    return (last / first) ** (1 / years) - 1, _span(points)


def _percentile(values: list[float], x: float) -> float:
    return sum(1 for value in values if value <= x) / len(values)


def build_fundamentals_packet(
    instrument: str,
    as_of: datetime,
    payload: Mapping[str, Any],
    price: float | None,
    now: datetime,
    index_history: Mapping[date, Mapping[str, float]] | None = None,
) -> dict[str, Any]:
    """Valuation, quality and earnings figures for one stock, in the metrics-packet shape (TRD section 3).

    Every figure is computed here from the stored statements; one that cannot be computed (too few periods, a
    lender where the ratio has no meaning, a missing price) is listed in `missing` with the reason."""
    st = Statements.from_payload(payload)
    metrics: dict[str, dict[str, Any]] = {}
    reasons: dict[str, str] = {}
    groups: dict[str, str] = {}

    def attempt(name: str, group: str, unit: str, inputs: list[str], compute: Callable[[], R]) -> None:
        groups[name] = group
        try:
            result = compute()
        except InsufficientData as exc:
            reasons[name] = str(exc)
            return
        metric: dict[str, Any] = {
            "value": result.value if isinstance(result.value, str) else round(float(result.value), 4),
            "unit": unit,
            "inputs": inputs,
            "window": result.window,
            "source": SOURCE,
            "group": group,
        }
        if result.note:
            metric["note"] = result.note
        metrics[name] = metric

    def non_lender() -> None:
        _need(not st.is_financial, LENDER_REASON)

    def have_price() -> float:
        _need(price is not None and price > 0, "no current price")
        return price  # type: ignore[return-value]

    def shares() -> float:
        reported = st.info.get("sharesOutstanding")
        if reported:
            return float(reported)
        counted = st.annual_series("balance", "Ordinary Shares Number")
        _need(bool(counted), "no share count")
        return counted[-1][1]

    def eps_ttm() -> tuple[float, str, str | None]:
        quarters = st.quarterly_series("Diluted EPS")[-4:]
        if len(quarters) == 4 and (quarters[-1][0] - quarters[0][0]).days <= QUARTER_SPAN_DAYS:
            return sum(value for _, value in quarters), f"last 4 quarters to {quarters[-1][0].isoformat()}", None
        annual = st.annual_series("income", "Diluted EPS")
        _need(bool(annual), "no diluted EPS")
        return annual[-1][1], f"fiscal year to {annual[-1][0].isoformat()}", "annual EPS used because four consecutive quarters were not available"

    def book_value_per_share() -> float:
        equity = st.annual_series("balance", "Stockholders Equity")
        _need(bool(equity), "no stockholders' equity")
        return equity[-1][1] / shares()

    def earnings_cagr() -> tuple[float, str]:
        return _cagr(st.annual_series("income", "Net Income"), "earnings growth")

    # ---- valuation
    attempt("last_price", VALUATION, "INR", ["close"], lambda: R(have_price(), "last close"))
    attempt("market_cap", VALUATION, "INR crore", ["close", "shares"], lambda: R(have_price() * shares() / CRORE, "last close x shares outstanding"))

    def pe() -> R:
        eps, window, note = eps_ttm()
        _need(eps > 0, "earnings per share is not positive")
        return R(have_price() / eps, window, note)

    attempt("pe_trailing", VALUATION, "ratio", ["close", "diluted EPS"], pe)

    def pb() -> R:
        bvps = book_value_per_share()
        _need(bvps > 0, "book value is not positive")
        return R(have_price() / bvps, "latest fiscal year equity")

    attempt("pb", VALUATION, "ratio", ["close", "stockholders equity", "shares"], pb)

    def earnings_yield() -> R:
        result = pe()
        return R(1 / float(result.value), result.window, result.note)

    attempt("earnings_yield", VALUATION, "fraction", ["close", "diluted EPS"], earnings_yield)

    def cash_yield(owner: bool) -> R:
        non_lender()
        if owner:
            profit_and_depreciation = _aligned(st.annual_series("income", "Net Income"), st.annual_series("cashflow", "Depreciation And Amortization"))
            earned = [(day, profit + depreciation) for day, profit, depreciation in profit_and_depreciation]
        else:
            earned = st.annual_series("cashflow", "Operating Cash Flow")
        joined = _aligned(earned, st.annual_series("cashflow", "Capital Expenditure"))
        _need(bool(joined), "no fiscal year has all the cash-flow lines")
        day, cash, spend = joined[-1]
        note = "capital expenditure is not split into maintenance and growth, so all of it is deducted" if owner else None
        return R((cash - abs(spend)) / (have_price() * shares()), f"fiscal year to {day.isoformat()}", note)

    attempt("fcf_yield", VALUATION, "fraction", ["operating cash flow", "capital expenditure", "market cap"], lambda: cash_yield(False))
    attempt(
        "owner_earnings_yield", VALUATION, "fraction",
        ["net income", "depreciation and amortization", "capital expenditure", "market cap"], lambda: cash_yield(True),
    )

    def peg() -> R:
        growth, window = earnings_cagr()
        _need(growth > 0, "earnings are not growing")
        return R(float(pe().value) / (growth * 100), window, "trailing P/E over the earnings growth rate in percent")

    attempt("peg", VALUATION, "ratio", ["pe_trailing", "net income"], peg)

    def graham() -> R:
        eps, window, _ = eps_ttm()
        bvps = book_value_per_share()
        _need(eps > 0 and bvps > 0, "Graham's number needs positive earnings and book value")
        return R(have_price() / math.sqrt(GRAHAM_MULTIPLIER * eps * bvps) - 1, window, "above 0 means the price is above Graham's ceiling")

    attempt("graham_number_premium", VALUATION, "fraction", ["close", "diluted EPS", "book value per share"], graham)

    def dividend_yield() -> R:
        rate = st.info.get("dividendRate")
        _need(bool(rate), "no dividend reported")
        return R(float(rate) / have_price(), "annual dividend rate over last close", "computed from the dividend rate, not Yahoo's yield field")

    attempt("dividend_yield", VALUATION, "fraction", ["dividend rate", "close"], dividend_yield)

    def index_pe() -> R:
        _need(bool(index_history), "no index valuation history")
        latest = max(index_history)  # type: ignore[arg-type]
        return R(index_history[latest]["pe"], f"NIFTY 50 on {latest.isoformat()}")  # type: ignore[index]

    attempt("index_pe", VALUATION, "ratio", ["NIFTY 50 P/E"], index_pe)
    attempt("pe_vs_index", VALUATION, "ratio", ["pe_trailing", "index_pe"], lambda: R(float(pe().value) / float(index_pe().value), "stock P/E over NIFTY 50 P/E"))

    def index_percentile() -> R:
        _need(bool(index_history) and len(index_history) >= MIN_INDEX_HISTORY, "index valuation history is too short")
        values = [row["pe"] for row in index_history.values()]  # type: ignore[union-attr]
        latest = index_history[max(index_history)]["pe"]  # type: ignore[index]
        return R(
            _percentile(values, latest),
            f"{len(values)} trading days of NIFTY 50 P/E",
            "share of those days on which the NIFTY 50 P/E was at or below today's: low means the market is cheap against its own history, high means dear",
        )

    attempt("index_pe_percentile", VALUATION, "fraction", ["NIFTY 50 P/E history"], index_percentile)

    # ---- quality (the ratio-only view of moat and business quality)
    def roe_by_year() -> list[tuple[date, float]]:
        equity = st.annual_series("balance", "Stockholders Equity")
        out = []
        for day, profit, closing in _aligned(st.annual_series("income", "Net Income"), equity):
            earlier = [value for d, value in equity if d < day]
            average = (closing + earlier[-1]) / 2 if earlier else closing
            if average > 0:
                out.append((day, profit / average))
        return out

    def roe(pick: Callable[[list[float]], float], minimum: int) -> R:
        values = roe_by_year()
        _need(len(values) >= minimum, f"needs {minimum} fiscal years of net income and equity, have {len(values)}")
        return R(pick([v for _, v in values]), _span(values), "net income over average equity")

    attempt("roe_latest", QUALITY, "fraction", ["net income", "stockholders equity"], lambda: roe(lambda v: v[-1], 1))
    attempt("roe_average", QUALITY, "fraction", ["net income", "stockholders equity"], lambda: roe(lambda v: sum(v) / len(v), 3))
    attempt("roe_minimum", QUALITY, "fraction", ["net income", "stockholders equity"], lambda: roe(min, 3))

    def margin(numerator: str, change: bool = False, lender_ok: bool = True) -> R:
        if not lender_ok:
            non_lender()
        pairs = _aligned(st.annual_series("income", numerator), st.annual_series("income", "Total Revenue"))
        _need(bool(pairs) and (len(pairs) >= 2 or not change), f"no fiscal year has {numerator} and revenue")
        ratios = [(day, top / revenue) for day, top, revenue in pairs if revenue]
        _need(bool(ratios), "revenue is zero")
        if change:
            _need(len(ratios) >= 2, "needs 2 fiscal years")
            return R((ratios[-1][1] - ratios[0][1]) * 100, _span(ratios), "latest minus earliest, in percentage points")
        return R(ratios[-1][1], f"fiscal year to {ratios[-1][0].isoformat()}")

    attempt("net_margin_latest", QUALITY, "fraction", ["net income", "revenue"], lambda: margin("Net Income"))
    attempt("operating_margin_latest", QUALITY, "fraction", ["operating income", "revenue"], lambda: margin("Operating Income", lender_ok=False))
    attempt("operating_margin_change", QUALITY, "percentage points", ["operating income", "revenue"], lambda: margin("Operating Income", change=True, lender_ok=False))
    attempt("gross_margin_latest", QUALITY, "fraction", ["gross profit", "revenue"], lambda: margin("Gross Profit", lender_ok=False))

    def cagr(group: str, line: str, what: str) -> R:
        value, window = _cagr(st.annual_series(group, line), what)
        return R(value, window, "compound annual growth between the first and last fiscal year")

    attempt("revenue_cagr", QUALITY, "fraction", ["revenue"], lambda: cagr("income", "Total Revenue", "revenue growth"))
    attempt("earnings_cagr", QUALITY, "fraction", ["net income"], lambda: cagr("income", "Net Income", "earnings growth"))

    def latest_ratio(top: tuple[str, str], bottom: tuple[str, str], what: str) -> R:
        non_lender()
        pairs = _aligned(st.annual_series(*top), st.annual_series(*bottom))
        _need(bool(pairs), f"no fiscal year has the lines for {what}")
        day, upper, lower = pairs[-1]
        _need(lower != 0, f"{what} has a zero denominator")
        return R(upper / lower, f"fiscal year to {day.isoformat()}")

    attempt("debt_to_equity", QUALITY, "ratio", ["total debt", "stockholders equity"], lambda: latest_ratio(("balance", "Total Debt"), ("balance", "Stockholders Equity"), "debt to equity"))
    attempt("current_ratio", QUALITY, "ratio", ["current assets", "current liabilities"], lambda: latest_ratio(("balance", "Current Assets"), ("balance", "Current Liabilities"), "current ratio"))

    def interest_coverage() -> R:
        non_lender()
        pairs = _aligned(st.annual_series("income", "EBIT"), [(d, abs(v)) for d, v in st.annual_series("income", "Interest Expense")])
        _need(bool(pairs), "no fiscal year has EBIT and interest expense")
        day, ebit, interest = pairs[-1]
        _need(interest > 0, "no interest expense")
        return R(ebit / interest, f"fiscal year to {day.isoformat()}")

    attempt("interest_coverage", QUALITY, "ratio", ["EBIT", "interest expense"], interest_coverage)

    def average_assets(day: date) -> float:
        assets = dict(st.annual_series("balance", "Total Assets"))
        _need(day in assets, "no total assets for the year")
        earlier = sorted((d, value) for d, value in assets.items() if d < day)
        return (assets[day] + earlier[-1][1]) / 2 if earlier else assets[day]

    def asset_turnover() -> R:
        non_lender()
        pairs = st.annual_series("income", "Total Revenue")
        _need(bool(pairs), "no revenue")
        day, revenue = pairs[-1]
        return R(revenue / average_assets(day), f"fiscal year to {day.isoformat()}")

    attempt("asset_turnover", QUALITY, "ratio", ["revenue", "total assets"], asset_turnover)

    # ---- earnings
    def accruals() -> tuple[date, float, float, float]:
        non_lender()
        joined = _aligned(st.annual_series("income", "Net Income"), st.annual_series("cashflow", "Operating Cash Flow"))
        _need(bool(joined), "no fiscal year has net income and operating cash flow")
        day, profit, cash = joined[-1]
        return day, profit, cash, average_assets(day)

    def accruals_ratio() -> R:
        day, profit, cash, assets = accruals()
        return R((profit - cash) / assets, f"fiscal year to {day.isoformat()}", "Sloan accruals: (net income - operating cash flow) over average assets; high positive values flag low earnings quality")

    def cash_conversion() -> R:
        day, profit, cash, _ = accruals()
        _need(profit > 0, "net income is not positive")
        return R(cash / profit, f"fiscal year to {day.isoformat()}", "operating cash flow over net income")

    attempt("accruals_ratio", EARNINGS, "fraction", ["net income", "operating cash flow", "total assets"], accruals_ratio)
    attempt("cash_conversion", EARNINGS, "ratio", ["operating cash flow", "net income"], cash_conversion)

    def quarter_growth(line: str) -> R:
        points = st.quarterly_series(line)
        _need(bool(points), f"no quarterly {line}")
        day, latest = points[-1]
        earlier = [value for d, value in points if YEAR_WINDOW[0] <= (day - d).days <= YEAR_WINDOW[1]]
        _need(bool(earlier), "the same quarter a year earlier is not available")
        _need(earlier[0] > 0, "the year-earlier figure is not positive")
        return R(latest / earlier[0] - 1, f"quarter to {day.isoformat()} against the same quarter a year earlier")

    attempt("revenue_growth_yoy_quarter", EARNINGS, "fraction", ["quarterly revenue"], lambda: quarter_growth("Total Revenue"))
    attempt("net_income_growth_yoy_quarter", EARNINGS, "fraction", ["quarterly net income"], lambda: quarter_growth("Net Income"))

    def surprises() -> list[tuple[str, float]]:
        rows = [(row["date"], row["surprise_pct"] / 100) for row in st.earnings_dates if row.get("reported") is not None and row.get("surprise_pct") is not None]
        _need(bool(rows), "no reported earnings with a surprise figure")
        return sorted(rows, reverse=True)

    attempt("eps_surprise_last", EARNINGS, "fraction", ["reported EPS", "EPS estimate"], lambda: R(surprises()[0][1], f"report of {surprises()[0][0]}", "against Yahoo's EPS estimate"))
    attempt("eps_surprise_average_4", EARNINGS, "fraction", ["reported EPS", "EPS estimate"], lambda: R(sum(v for _, v in surprises()[:4]) / len(surprises()[:4]), f"last {len(surprises()[:4])} reports"))
    attempt("beats_last_4", EARNINGS, "count", ["reported EPS", "EPS estimate"], lambda: R(sum(1 for _, v in surprises()[:4] if v > 0), f"of the last {len(surprises()[:4])} reports"))

    def days_to_next() -> R:
        today = ist_date(now)
        future = sorted(date.fromisoformat(row["date"]) for row in st.earnings_dates if date.fromisoformat(row["date"]) > today)
        _need(bool(future), "no upcoming earnings date")
        return R((future[0] - today).days, f"next report {future[0].isoformat()}")

    attempt("days_to_next_earnings", EARNINGS, "days", ["earnings calendar"], days_to_next)

    annual_dates = [day for day, _ in st.annual_series("income", "Total Revenue")]
    quarter_dates = [day for day, _ in st.quarterly_series("Total Revenue")]
    return {
        "instrument": instrument,
        "as_of": as_of.isoformat(),
        "sector": st.info.get("sector"),
        "is_lender": st.is_financial,
        "latest_annual_period": annual_dates[-1].isoformat() if annual_dates else None,
        "latest_quarter": quarter_dates[-1].isoformat() if quarter_dates else None,
        "data_quality_flags": list(payload.get("quality_flags", [])),
        "metrics": metrics,
        "missing": list(reasons),
        "missing_reasons": reasons,
        "not_applicable": [name for name, why in reasons.items() if why == LENDER_REASON],
        "groups": groups,
    }
```

- [ ] **Step 4: Run to verify they pass, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_specialist_visibility.py tests/test_metrics_not_applicable.py tests/test_metrics_fundamentals.py tests/test_specialist_base.py tests/test_quant_technical.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: all pass (6 + 3 new tests), then `441 passed, 53 skipped`. The existing specialist and Quant/Technical tests pass unchanged: `shows=None` keeps their behaviour.

- [ ] **Step 5: Mutation checks (do not commit these edits)**

In `base.py`: (a) replace the line `not_applicable = set(packet.get("not_applicable", ()))  # ...` with `not_applicable = set()`: expect `test_figures_marked_not_applicable_are_neither_required_nor_missing_and_the_prompt_says_so` to FAIL. Undo. (b) replace `if self.spec.shows is None:` with `if True:`: expect `test_a_specialist_that_declares_shows_is_only_shown_those_figures_plus_the_packet_level_facts` and `test_grounding_is_checked_against_what_the_model_was_shown` to FAIL. Undo and confirm `git diff` is empty for `base.py`.

- [ ] **Step 6: Commit and push**

```bash
git add src/athena/agents/base.py src/athena/metrics/fundamentals.py tests/test_specialist_visibility.py tests/test_metrics_not_applicable.py tests/test_metrics_fundamentals.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: let specialists declare what they are shown; mark figures not applicable; make units unambiguous"
git push
```

---

### Task 2: The three specialists

**Files:**
- Create: `src/athena/agents/valuation.py`, `src/athena/agents/moat_quality.py`, `src/athena/agents/earnings_intelligence.py`
- Test: `tests/test_fundamentals_specialists.py`

**Interfaces:**
- Consumes: `SpecialistSpec`, `Specialist` (Task 1); `build_fundamentals_packet` (Plan 1e); `check_abstention`, `validate_specialist_output`; `ACME`, `index_history` fixtures (Plan 1e).
- Produces: in each module `PERSONA`, `CRITICAL`, `OPTIONAL`, `SHOWS` and the spec (`VALUATION`, `MOAT_QUALITY`, `EARNINGS_INTELLIGENCE`). Their routing names are `valuation`, `moat_quality`, `earnings_intelligence` (already in `athena.routing.ROUTING["equity"]`).
  - Valuation: critical `last_price`, `pe_trailing`, `pb`; optional `fcf_yield`, `owner_earnings_yield`, `peg`, `graham_number_premium`, `pe_vs_index`, `earnings_cagr`; also shown `market_cap`, `earnings_yield`, `dividend_yield`, `index_pe`, `index_pe_percentile`, `roe_latest`.
  - Moat & Quality: critical `roe_latest`, `net_margin_latest`; optional `roe_average`, `roe_minimum`, `operating_margin_latest`, `operating_margin_change`, `revenue_cagr`, `earnings_cagr`, `debt_to_equity`; also shown `gross_margin_latest`, `interest_coverage`, `current_ratio`, `asset_turnover`.
  - Earnings Intelligence: critical `net_income_growth_yoy_quarter`; optional `revenue_growth_yoy_quarter`, `accruals_ratio`, `cash_conversion`, `eps_surprise_last`, `eps_surprise_average_4`, `beats_last_4`, `days_to_next_earnings`.

- [ ] **Step 1: Write the failing tests `tests/test_fundamentals_specialists.py`**

```python
import copy
import json

from fund_fixtures import ACME, NOW, PRICE, index_history

from athena.agents.base import Specialist
from athena.agents.earnings_intelligence import EARNINGS_INTELLIGENCE
from athena.agents.earnings_intelligence import PERSONA as EARNINGS_PERSONA
from athena.agents.moat_quality import MOAT_QUALITY
from athena.agents.moat_quality import PERSONA as MOAT_PERSONA
from athena.agents.valuation import PERSONA as VALUATION_PERSONA
from athena.agents.valuation import VALUATION
from athena.evaluation.checks import check_abstention
from athena.evaluation.schema import validate_specialist_output
from athena.metrics.fundamentals import build_fundamentals_packet

SPECS = {"valuation": VALUATION, "moat_quality": MOAT_QUALITY, "earnings_intelligence": EARNINGS_INTELLIGENCE}
FULL = build_fundamentals_packet("ACME", NOW, ACME, PRICE, NOW, index_history())


def bank_payload():
    bank = copy.deepcopy(ACME)
    bank["info"]["sector"] = "Financial Services"
    for line in ("Operating Income", "EBIT", "Gross Profit", "Interest Expense"):
        del bank["annual"]["income"][line]
    for line in ("Current Assets", "Current Liabilities"):
        del bank["annual"]["balance"][line]
    return bank


BANK = build_fundamentals_packet("BANK", NOW, bank_payload(), PRICE, NOW, index_history())


class Narrator:
    """A fake model that cites the first two numeric figures in the packet it was shown."""

    def __init__(self):
        self.prompts = []

    def complete(self, system, user):
        self.prompts.append(user)
        metrics = json.loads(user[user.index("{") : user.rindex("}") + 1])["metrics"]
        cited = [(name, m["value"]) for name, m in metrics.items() if not isinstance(m["value"], str)][:2]
        text = " ".join(f"{name} is {value}." for name, value in cited)
        return json.dumps({"signal": "neutral", "confidence": 55, "reasoning": text})


def run(spec, packet):
    narrator = Narrator()
    return Specialist(spec, narrator).analyze(packet), narrator


def test_every_declared_figure_exists_in_a_full_packet_and_critical_and_optional_are_shown():
    for name, spec in SPECS.items():
        declared = set(spec.critical) | set(spec.optional) | set(spec.shows)
        assert declared <= set(FULL["metrics"]), (name, declared - set(FULL["metrics"]))
        assert set(spec.critical) | set(spec.optional) <= set(spec.shows), name
        assert spec.critical and not set(spec.critical) & set(spec.optional), name


def test_personas_encode_the_trd_rules_and_the_compliance_language():
    for persona in (VALUATION_PERSONA, MOAT_PERSONA, EARNINGS_PERSONA):
        assert "do not calculate your own" in persona and "price target" in persona and "not financial advice" in persona
        assert "bank or lender" in persona and "as_of" in persona
    for rule in ("Graham", "owner_earnings_yield", "peg", "trap"):
        assert rule in VALUATION_PERSONA
    assert "fraction between 0 and 1" in VALUATION_PERSONA and "0.01 means the market is near its cheapest" in VALUATION_PERSONA
    for rule in ("roe_minimum", "WHY a business is advantaged", "never name a moat source"):
        assert rule in MOAT_PERSONA
    for rule in ("accruals_ratio", "Sloan", "days_to_next_earnings", "data_quality_flags", "unverified"):
        assert rule in EARNINGS_PERSONA


def test_complete_data_gives_each_specialist_full_coverage_and_a_valid_grounded_output():
    for name, spec in SPECS.items():
        out, _ = run(spec, FULL)
        assert out["data_coverage"] == "full" and out["missing"] == [], name
        assert validate_specialist_output(out) == [], name


def test_each_specialist_is_shown_only_its_own_figures():
    _, valuation = run(VALUATION, FULL)
    _, moat = run(MOAT_QUALITY, FULL)
    _, earnings = run(EARNINGS_INTELLIGENCE, FULL)
    assert "pe_trailing" in valuation.prompts[0] and "roe_minimum" not in valuation.prompts[0]
    assert "accruals_ratio" not in valuation.prompts[0]
    assert "roe_minimum" in moat.prompts[0] and "pe_trailing" not in moat.prompts[0] and "beats_last_4" not in moat.prompts[0]
    assert "accruals_ratio" in earnings.prompts[0] and "pe_trailing" not in earnings.prompts[0]
    assert "roe_minimum" not in earnings.prompts[0]


def test_a_bank_is_not_marked_partial_for_ratios_that_never_apply_to_it():
    assert BANK["is_lender"] and BANK["not_applicable"]
    for name, spec in SPECS.items():
        out, _ = run(spec, BANK)
        assert (out["data_coverage"], out["missing"]) == ("full", []), (name, out["missing"])
    _, narrator = run(VALUATION, BANK)
    assert "not a data gap" in narrator.prompts[0] and "fcf_yield" in narrator.prompts[0]


def test_a_missing_critical_figure_makes_the_specialist_abstain_without_calling_the_model():
    no_price = build_fundamentals_packet("ACME", NOW, ACME, None, NOW, index_history())
    out, narrator = run(VALUATION, no_price)
    assert (out["signal"], out["confidence"], out["data_coverage"]) == ("neutral", 0, "insufficient")
    assert narrator.prompts == []
    losing = copy.deepcopy(ACME)
    losing["quarterly"]["income"]["Net Income"] = {period: None for period in losing["quarterly"]["income"]["Net Income"]}
    out, narrator = run(EARNINGS_INTELLIGENCE, build_fundamentals_packet("ACME", NOW, losing, PRICE, NOW, index_history()))
    assert out["data_coverage"] == "insufficient" and narrator.prompts == []


def test_a_missing_optional_figure_makes_coverage_partial_and_names_it():
    thin = copy.deepcopy(ACME)
    thin["earnings_dates"] = []
    out, _ = run(EARNINGS_INTELLIGENCE, build_fundamentals_packet("ACME", NOW, thin, PRICE, NOW, index_history()))
    assert out["data_coverage"] == "partial" and {"eps_surprise_last", "days_to_next_earnings"} <= set(out["missing"])


def test_the_abstention_harness_passes_for_each_specialist():
    for name, spec in SPECS.items():
        specialist = Specialist(spec, Narrator(), fail_quiet=True)
        failures = check_abstention(
            lambda metrics: specialist.analyze({**FULL, "metrics": metrics}),
            FULL["metrics"], critical=list(spec.critical), optional=list(spec.optional),
        )
        assert failures == [], (name, failures)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_fundamentals_specialists.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.agents.valuation'`.

- [ ] **Step 3: Write the three specialist modules**

`src/athena/agents/valuation.py`:

```python
from __future__ import annotations

from athena.agents.base import SpecialistSpec

PERSONA = """You are a valuation analyst who works only from ratios, never from forecasts. A stylized analytical
framework, not financial advice; not a registered investment adviser.
Interpret the figures in the metrics packet; do not calculate your own. Work the checklist:
1. Price against earnings: pe_trailing and earnings_yield, and pe_vs_index (this stock's P/E over the NIFTY 50's).
   index_pe_percentile is a fraction between 0 and 1: the share of days on which the NIFTY 50 P/E was at or below
   today's. Read it carefully: 0.01 means the market is near its cheapest in the period, 0.99 near its dearest.
   It is about the whole market, not this stock, so use it only as context.
2. Cash the business really produces: owner_earnings_yield and fcf_yield. A yield that is high and backed by
   cash is stronger evidence of value than a low P/E alone.
3. Price for the growth: peg (below about 1 is cheap for the growth, above about 2 is expensive) and earnings_cagr.
   A low multiple on shrinking earnings is a trap, not a bargain.
4. Graham's test: graham_number_premium above 0 means the price is above his ceiling, below 0 means inside it.
5. Book value: pb, read together with roe_latest (a high P/B needs a high return on equity to justify it).
6. dividend_yield, when present, is support for the price, not a reason to buy.
For a bank or lender the cash-flow yields do not apply: lean on pe_trailing, pb, roe_latest and growth.
Reason ONLY from the data provided and treat as_of as the present day. If a figure is listed as missing, say so
and qualify your conclusion. Never state a price target or a fair value: describe the stock as expensive, fairly
priced or cheap relative to its own earnings and growth, give a base case and name the main risk."""

# Figures that must exist for a ratio-only valuation to mean anything.
CRITICAL = ("last_price", "pe_trailing", "pb")
# Figures that sharpen it; their absence makes coverage partial.
OPTIONAL = ("fcf_yield", "owner_earnings_yield", "peg", "graham_number_premium", "pe_vs_index", "earnings_cagr")
# Everything the model is shown (dividend_yield and the index percentile are context, not requirements).
SHOWS = CRITICAL + OPTIONAL + ("market_cap", "earnings_yield", "dividend_yield", "index_pe", "index_pe_percentile", "roe_latest")

VALUATION = SpecialistSpec(name="Valuation", persona=PERSONA, critical=CRITICAL, optional=OPTIONAL, shows=SHOWS)
```

`src/athena/agents/moat_quality.py`:

```python
from __future__ import annotations

from athena.agents.base import SpecialistSpec

PERSONA = """You are a business-quality analyst. A stylized analytical framework, not financial advice; not a
registered investment adviser. You can only see ratios, so you judge the evidence for a durable competitive
advantage, not its source. Interpret the figures in the metrics packet; do not calculate your own. Work the checklist:
1. Returns that last: roe_latest, roe_average and roe_minimum. A durable advantage shows as returns that stay high
   through the cycle (roe_minimum close to roe_average), not one good year. Check debt_to_equity so the return is
   not simply borrowed.
2. Pricing power: operating_margin_latest, operating_margin_change (percentage points over the period; rising
   suggests pricing power, falling suggests pressure), gross_margin_latest and net_margin_latest.
3. Growth that compounds: revenue_cagr and earnings_cagr. Earnings growing faster than revenue points to operating
   leverage; the reverse points to squeezed margins.
4. Balance-sheet strength: debt_to_equity, interest_coverage and current_ratio.
5. Capital efficiency: asset_turnover.
Ratios cannot show WHY a business is advantaged (brand, switching costs, network effects or cost position). Say so,
and describe the evidence as consistent or inconsistent with a moat; never name a moat source or a moat width as
fact. For a bank or lender the margin, working-capital and leverage ratios do not apply: lean on roe_latest and
growth.
Reason ONLY from the data provided and treat as_of as the present day. If a figure is listed as missing, say so
and qualify your conclusion. Never state a price target or a fair value; give a base case and name the main risk."""

CRITICAL = ("roe_latest", "net_margin_latest")
OPTIONAL = (
    "roe_average", "roe_minimum", "operating_margin_latest", "operating_margin_change",
    "revenue_cagr", "earnings_cagr", "debt_to_equity",
)
SHOWS = CRITICAL + OPTIONAL + ("gross_margin_latest", "interest_coverage", "current_ratio", "asset_turnover")

MOAT_QUALITY = SpecialistSpec(name="Moat & Quality", persona=PERSONA, critical=CRITICAL, optional=OPTIONAL, shows=SHOWS)
```

`src/athena/agents/earnings_intelligence.py`:

```python
from __future__ import annotations

from athena.agents.base import SpecialistSpec

PERSONA = """You are an earnings-quality and event analyst. A stylized analytical framework, not financial advice; not
a registered investment adviser. Interpret the figures in the metrics packet; do not calculate your own. Work the
checklist:
1. Are profits backed by cash? accruals_ratio is Sloan's measure: high and positive means reported profit is running
   ahead of cash (a warning), negative means cash exceeds profit (a good sign). cash_conversion below about 0.8 over
   time is a warning; well above 1 is reassuring.
2. Momentum: revenue_growth_yoy_quarter and net_income_growth_yoy_quarter compare the latest quarter with the same
   quarter a year earlier. Profit growing faster than revenue is operating leverage; the reverse is margin pressure.
3. Surprise history: eps_surprise_last, eps_surprise_average_4 and beats_last_4. Steady beats are a positive, but
   they can also mean analysts set the bar low; the estimates come from a free source whose accuracy for Indian
   stocks is unverified, so say that.
4. Event risk: days_to_next_earnings. If the next report is within about 7 days, a binary event is close: say so
   and keep your confidence low.
5. data_quality_flags: if the packet reports a duplicated or missing period, the quarterly figures are less reliable;
   say which conclusions that weakens.
For a bank or lender the accruals and cash-conversion measures do not apply: lean on growth, surprises and the
report date.
Reason ONLY from the data provided and treat as_of as the present day. If a figure is listed as missing, say so
and qualify your conclusion. Never state a price target or a fair value; give a base case and name the main risk."""

# One quarter-on-quarter-a-year-ago figure is the minimum for any view of recent earnings.
CRITICAL = ("net_income_growth_yoy_quarter",)
OPTIONAL = (
    "revenue_growth_yoy_quarter", "accruals_ratio", "cash_conversion", "eps_surprise_last",
    "eps_surprise_average_4", "beats_last_4", "days_to_next_earnings",
)
SHOWS = CRITICAL + OPTIONAL  # the packet-level data_quality_flags are always visible too

EARNINGS_INTELLIGENCE = SpecialistSpec(
    name="Earnings Intelligence", persona=PERSONA, critical=CRITICAL, optional=OPTIONAL, shows=SHOWS
)
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_fundamentals_specialists.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `8 passed`, then `449 passed, 53 skipped`. `test_every_declared_figure_exists_in_a_full_packet_and_critical_and_optional_are_shown` is the guard against a misspelled metric name in any spec.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/agents/valuation.py src/athena/agents/moat_quality.py src/athena/agents/earnings_intelligence.py tests/test_fundamentals_specialists.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add Valuation, Moat & Quality and Earnings Intelligence specialists"
git push
```

---

### Task 3: Shared sources and wiring

**Files:**
- Modify (replace the whole file with the version below): `src/athena/orchestrator/builders.py`, `src/athena/cli.py`, `src/athena/dashboard/service.py`, `tests/test_orchestrator_builders.py`, `tests/test_dashboard_service.py`
- Create: `src/athena/orchestrator/fundamentals_source.py`
- Test: `tests/test_fundamentals_source.py`, `tests/test_cli_fundamentals.py`

**Interfaces:**
- Produces (`athena.orchestrator.builders`): `RequestCache(fetch)` (moved here from the dashboard; callable `symbol -> bars`, `clear()`).
- Produces (`athena.orchestrator.fundamentals_source`): `LiveFundamentals(loader, index_history, fetch_bars, clock=utc_now)` with `__call__(resolution) -> dict` (refreshes the statements once per symbol until `clear()`, builds the packet against the last close and the index history; a failure raises and is not cached) and `clear()`.
- Produces (`athena.cli`): `FUNDAMENTAL_SPECIALISTS` (`valuation`, `moat_quality`, `earnings_intelligence`); `build_orchestrator(resolver, llm_router, chain, clock=utc_now, fetch_bars=None, fundamentals=None)` registers the three specialists, each reading the same `fundamentals` packet builder, only when a source is given (otherwise they stay "not built yet", as before); `LiveSources(resolver, llm_router, chain, store, bars, fundamentals)`; `live_sources` also loads eight years of NIFTY 50 valuation; `live_orchestrator` wires everything, so `python -m athena.cli TCS` now runs four specialists.
- Produces (`athena.dashboard.service`): `FundamentalsSource = Callable[[Resolution], dict]`; `DashboardService` clears the bars cache and the fundamentals source (if it has `clear`) at the start of each request and calls the source with the resolution only; `live_service` reuses the shared sources.

- [ ] **Step 1: Write the failing tests and update the existing ones**

`tests/test_fundamentals_source.py`:

```python
import pytest
from bar_factory import make_bars
from fund_fixtures import ACME, NOW, PRICE, index_history

from athena.contracts import EmptyRefreshError
from athena.loaders.fundamentals import FundamentalsLoader
from athena.orchestrator.fundamentals_source import LiveFundamentals
from athena.resolver import Resolution
from athena.store import DataStore

RESOLUTION = Resolution("equity", "ticker", "ACME", "Acme Ltd", "", "exact", 1.0, (), ())


class Counting:
    def __init__(self, payload):
        self.fetches, self.payload = [], payload

    def __call__(self, symbol):
        self.fetches.append(symbol)
        return self.payload


def make(payload=ACME):
    statements, bar_calls = Counting(payload), []

    def fetch_bars(symbol):
        bar_calls.append(symbol)
        return make_bars([PRICE - 1, PRICE])

    loader = FundamentalsLoader(DataStore(), fetch=statements, clock=lambda: NOW)
    source = LiveFundamentals(loader, index_history(), fetch_bars, clock=lambda: NOW)
    return source, statements, bar_calls


def test_the_packet_is_built_from_the_statements_the_last_close_and_the_index_history():
    source, statements, _ = make()
    packet = source(RESOLUTION)
    assert packet["instrument"] == "ACME" and packet["metrics"]["last_price"]["value"] == PRICE
    assert packet["metrics"]["index_pe"]["value"] == 19.3
    assert packet["metrics"]["pe_trailing"]["value"] == pytest.approx(20.0, abs=1e-3)
    assert statements.fetches == ["ACME"]


def test_each_symbol_is_fetched_once_until_the_request_is_cleared():
    source, statements, bar_calls = make()
    first, second = source(RESOLUTION), source(RESOLUTION)  # three specialists would each call it
    assert first is second and statements.fetches == ["ACME"] and bar_calls == ["ACME"]
    source.clear()
    source(RESOLUTION)
    assert statements.fetches == ["ACME", "ACME"]


def test_a_source_with_no_statements_fails_loud_and_does_not_cache_the_failure():
    source, statements, _ = make({"annual": {"income": {}}})
    for _ in range(2):
        with pytest.raises(EmptyRefreshError):
            source(RESOLUTION)
    assert statements.fetches == ["ACME", "ACME"]
```

`tests/test_cli_fundamentals.py`:

```python
import json
from types import SimpleNamespace

from bar_factory import make_bars
from fund_fixtures import ACME, NOW, PRICE, index_history

from athena.cli import FUNDAMENTAL_SPECIALISTS, build_orchestrator
from athena.contracts import EmptyRefreshError, Record
from athena.evaluation.schema import validate_judge_verdict
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET
from athena.metrics.fundamentals import build_fundamentals_packet
from athena.orchestrator.orchestrator import OK
from athena.resolver import InstrumentIndex, InstrumentResolver
from athena.store import DataStore

FULL = build_fundamentals_packet("SBIN", NOW, ACME, PRICE, NOW, index_history())


def make_resolver():
    store = DataStore()
    for symbol, name in (("SBIN", "State Bank of India"), ("TCS", "Tata Consultancy Services Limited")):
        store.put(Record(EQUITY_DATASET, symbol, NOW, "x", {"name": name, "isin": "INE000000000"}))
    store.put(Record(ETF_DATASET, "NIFTYBEES", NOW, "x", {"name": "NIPINDETFNIFTYBEES", "isin": "INF000000000"}))
    return InstrumentResolver(InstrumentIndex.from_store(store, now=NOW))


class GenericNarrator:
    """A fake model that cites the first two numeric figures of whichever packet it is shown."""

    def complete(self, system, user):
        metrics = json.loads(user[user.index("{") : user.rindex("}") + 1])["metrics"]
        cited = [(name, m["value"]) for name, m in metrics.items() if not isinstance(m["value"], str)][:2]
        text = " ".join(f"{name} is {value}." for name, value in cited)
        return json.dumps({"signal": "bullish", "confidence": 70, "reasoning": text})


class FakeLLMRouter:
    def client_for(self, role):
        return GenericNarrator()


class FakeChain:
    def run(self, symbol, **kwargs):
        return SimpleNamespace(value=make_bars([100.0 + i * 0.5 for i in range(400)], symbol=symbol), source="fake")


class FakeFundamentals:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def __call__(self, resolution):
        self.calls.append(resolution.identifier)
        if self.error:
            raise self.error
        return FULL


def orchestrator(fundamentals):
    return build_orchestrator(make_resolver(), FakeLLMRouter(), FakeChain(), clock=lambda: NOW, fundamentals=fundamentals)


def test_the_three_fundamentals_specialists_are_the_ones_the_routing_table_names():
    assert set(FUNDAMENTAL_SPECIALISTS) == {"valuation", "moat_quality", "earnings_intelligence"}


def test_with_a_fundamentals_source_a_stock_is_analysed_by_all_four_equity_specialists():
    source = FakeFundamentals()
    result = orchestrator(source).analyze("sbin")
    assert result.status == OK and result.skipped == {}
    assert set(result.specialists) == {"quant_technical", "valuation", "moat_quality", "earnings_intelligence"}
    assert all(out["data_coverage"] == "full" for out in result.specialists.values())
    assert validate_judge_verdict(result.verdict) == [] and result.verdict["verdict"] == "Buy"
    assert not any("routed specialists ran" in note for note in result.notes)
    assert source.calls == ["SBIN"] * 3  # one call per specialist; LiveFundamentals caches the work behind them


def test_an_etf_never_calls_the_fundamentals_source_and_keeps_its_own_routing():
    source = FakeFundamentals()
    result = orchestrator(source).analyze("niftybees")
    assert source.calls == [] and set(result.specialists) == {"quant_technical"}
    assert result.skipped == {"etf_analyst": "not built yet"}


def test_without_a_fundamentals_source_the_three_are_listed_as_not_built_as_before():
    result = orchestrator(None).analyze("sbin")
    assert set(result.specialists) == {"quant_technical"}
    assert result.skipped == {name: "not built yet" for name in FUNDAMENTAL_SPECIALISTS}


def test_a_fundamentals_failure_skips_the_three_but_the_technical_specialist_still_answers():
    result = orchestrator(FakeFundamentals(error=EmptyRefreshError("no statements for 'SBIN'"))).analyze("sbin")
    assert set(result.specialists) == {"quant_technical"} and result.status == OK
    assert set(result.skipped) == set(FUNDAMENTAL_SPECIALISTS)
    assert all("EmptyRefreshError" in why for why in result.skipped.values())
    assert any("only 1 of 4 routed specialists ran" in note for note in result.notes)
```

`tests/test_orchestrator_builders.py` (replace; it gains the `RequestCache` test):

```python
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from athena.contracts import Bar
from athena.orchestrator.builders import HISTORY_DAYS, RequestCache, history_fetcher, technical_packet_builder
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


def test_request_cache_downloads_each_symbol_once_until_cleared():
    fetched = []

    def fetch(symbol):
        fetched.append(symbol)
        return bars()

    cache = RequestCache(fetch)
    cache("SBIN"), cache("SBIN"), cache("TCS")
    assert fetched == ["SBIN", "TCS"]
    cache.clear()
    cache("SBIN")
    assert fetched == ["SBIN", "TCS", "SBIN"]
```

`tests/test_dashboard_service.py` (replace; the fundamentals fake takes only the resolution, the cache is cleared per request, and the `RequestCache` test moved to the builders module):

```python
from bar_factory import NOW, make_bars
from dash_fakes import FUNDAMENTALS, ambiguous_result, resolution

from athena.contracts import EmptyRefreshError
from athena.dashboard.risk import RiskWorld
from athena.dashboard.service import DashboardService
from athena.orchestrator.builders import RequestCache
from athena.orchestrator.orchestrator import OK, OrchestrationResult

BARS = make_bars([100.0 + i * 0.5 for i in range(320)], symbol="SBIN")
VERDICT = {"verdict": "Hold", "conviction": 20, "key_risks": [], "resolution_path": "blend"}


class CountingFetch:
    def __init__(self):
        self.symbols = []

    def __call__(self, symbol):
        self.symbols.append(symbol)
        return BARS


class FakeOrchestrator:
    """Like the real one, it pulls the bars through the shared cache while it works."""

    def __init__(self, cache, result=None):
        self.cache, self.result, self.queries = cache, result, []

    def analyze(self, query):
        self.queries.append(query)
        result = self.result or OrchestrationResult(OK, query, resolution(), None, {}, {}, None, 0.0, VERDICT, ())
        if result.resolution:
            self.cache(result.resolution.identifier)
        return result


def make_service(result=None):
    fetch = CountingFetch()
    cache = RequestCache(fetch)
    orchestrator = FakeOrchestrator(cache, result)
    service = DashboardService(orchestrator, cache, RiskWorld({}, {}, {}), clock=lambda: NOW)
    return service, fetch, orchestrator


def test_a_view_shares_one_download_between_the_specialists_and_the_charts():
    service, fetch, orchestrator = make_service()
    view = service.view("sbin")
    assert orchestrator.queries == ["sbin"] and fetch.symbols == ["SBIN"]
    assert view.identifier == "SBIN" and view.price_figure is not None
    assert [panel.title.split()[0] for panel in view.panels] == ["Technical", "Risk"]


def test_the_technical_panel_is_computed_and_the_risk_panel_reports_what_it_could_not():
    view = make_service()[0].view("sbin")
    technical, risk = view.panels
    assert technical.coverage == "full" and any(row.name == "rsi_14" for row in technical.rows)
    assert risk.coverage == "partial" and {"volatility_annualized", "max_drawdown"} <= {r.name for r in risk.rows}
    assert "beta" in risk.missing  # the (empty) market series were not available


def test_every_request_starts_with_a_fresh_download():
    service, fetch, _ = make_service()
    service.view("sbin")
    service.view("sbin")
    assert fetch.symbols == ["SBIN", "SBIN"]


def test_an_ambiguous_query_downloads_nothing_and_returns_candidates():
    service, fetch, _ = make_service(ambiguous_result())
    view = service.view("sbi")
    assert fetch.symbols == [] and view.candidates and view.price_figure is None


# ---- fundamentals source
class FakeFundamentals:
    def __init__(self, packet=None, error=None):
        self.packet, self.error, self.calls, self.clears = packet, error, [], 0

    def __call__(self, resolution):
        self.calls.append(resolution.identifier)
        if self.error:
            raise self.error
        return self.packet

    def clear(self):
        self.clears += 1


def service_with(fundamentals, asset_class="equity"):
    fetch = CountingFetch()
    cache = RequestCache(fetch)
    result = OrchestrationResult(OK, "sbin", resolution(asset_class), None, {}, {}, None, 0.0, VERDICT, ())
    orchestrator = FakeOrchestrator(cache, result)
    return DashboardService(orchestrator, cache, RiskWorld({}, {}, {}), clock=lambda: NOW, fundamentals=fundamentals)


def test_a_stock_view_gets_the_fundamentals_panels_built_from_the_same_bars():
    source = FakeFundamentals(FUNDAMENTALS)
    view = service_with(source).view("sbin")
    assert source.calls == ["SBIN"]
    assert [p.title for p in view.panels][2:] == ["Valuation", "Business quality", "Earnings"]


def test_each_request_starts_by_clearing_the_fundamentals_cache():
    source = FakeFundamentals(FUNDAMENTALS)
    service = service_with(source)
    service.view("sbin")
    service.view("sbin")
    assert source.clears == 2


def test_an_etf_never_asks_for_fundamentals():
    source = FakeFundamentals(FUNDAMENTALS)
    view = service_with(source, "etf").view("niftybees")
    assert source.calls == [] and len(view.panels) == 2


def test_a_fundamentals_failure_becomes_a_note_and_the_rest_of_the_page_survives():
    view = service_with(FakeFundamentals(error=EmptyRefreshError("no statements for 'SBIN'"))).view("sbin")
    assert len(view.panels) == 2 and view.price_figure is not None
    assert "fundamentals unavailable: no statements for 'SBIN'" in view.notes


def test_without_a_fundamentals_source_nothing_changes():
    assert len(service_with(None).view("sbin").panels) == 2
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_fundamentals_source.py tests/test_cli_fundamentals.py tests/test_orchestrator_builders.py tests/test_dashboard_service.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'athena.orchestrator.fundamentals_source'` and an `ImportError` for `RequestCache`).

- [ ] **Step 3: Replace the sources and add the new module**

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


class RequestCache:
    """Remembers each symbol's bars for one request, so every specialist, the charts and the fundamentals share one
    price download. Whoever owns the request calls `clear()` when it starts."""

    def __init__(self, fetch: Callable[[str], list[Bar]]):
        self._fetch = fetch
        self._bars: dict[str, list[Bar]] = {}

    def __call__(self, symbol: str) -> list[Bar]:
        if symbol not in self._bars:
            self._bars[symbol] = self._fetch(symbol)
        return self._bars[symbol]

    def clear(self) -> None:
        self._bars.clear()
```

`src/athena/orchestrator/fundamentals_source.py`:

```python
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any

from athena.clock import utc_now
from athena.contracts import Bar
from athena.loaders.fundamentals import DATASET, FundamentalsLoader
from athena.metrics.fundamentals import build_fundamentals_packet
from athena.resolver import Resolution


class LiveFundamentals:
    """Fetches a stock's statements (Yahoo) into the store and builds its fundamentals packet against the last close
    and the NIFTY 50 valuation history. The three fundamentals specialists and the dashboard all call it for the same
    request, so each symbol is fetched once until `clear()`. Raises an `AthenaError` when the source has nothing for
    the symbol."""

    def __init__(
        self,
        loader: FundamentalsLoader,
        index_history: Mapping[date, Mapping[str, float]],
        fetch_bars: Callable[[str], list[Bar]],
        clock: Callable[[], datetime] = utc_now,
    ):
        self._loader = loader
        self._index_history = index_history
        self._fetch_bars = fetch_bars
        self._clock = clock
        self._packets: dict[str, dict[str, Any]] = {}

    def __call__(self, resolution: Resolution) -> dict[str, Any]:
        symbol = resolution.identifier
        if symbol not in self._packets:
            self._loader.refresh(symbol=symbol)
            record = self._loader.read(DATASET, symbol)
            bars = self._fetch_bars(symbol)
            price = bars[-1].close if bars else None
            self._packets[symbol] = build_fundamentals_packet(
                symbol, record.as_of, record.payload, price, self._clock(), self._index_history
            )
        return self._packets[symbol]

    def clear(self) -> None:
        self._packets.clear()
```

`src/athena/cli.py`:

```python
from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from athena.adapters.prices import JugaadPriceAdapter, YahooPriceAdapter, ohlcv_chain
from athena.agents.base import Specialist
from athena.agents.earnings_intelligence import EARNINGS_INTELLIGENCE
from athena.agents.moat_quality import MOAT_QUALITY
from athena.agents.quant_technical import QUANT_TECHNICAL
from athena.agents.valuation import VALUATION
from athena.clock import utc_now
from athena.contracts import AthenaError, Bar
from athena.fallback import FallbackChain
from athena.llm.envfile import DEFAULT_ENV_FILE
from athena.llm.router import Router, build_router
from athena.loaders.fundamentals import FundamentalsLoader
from athena.loaders.index_valuation import IndexValuationLoader, valuation_history
from athena.loaders.nse_holidays import DATASET as HOLIDAY_DATASET
from athena.loaders.nse_holidays import NseHolidayLoader, load_calendar
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET, NseMasterLoader
from athena.orchestrator.builders import RequestCache, history_fetcher, technical_packet_builder
from athena.orchestrator.fundamentals_source import LiveFundamentals
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, Orchestrator
from athena.orchestrator.report import format_result
from athena.resolver import InstrumentIndex, InstrumentResolver, Resolution
from athena.store import DataStore
from athena.trading_calendar import TradingCalendar, ist_date

INDEX_VALUATION_DAYS = 365 * 8  # NSE has published index P/E since 2015; eight years is plenty for a percentile
FUNDAMENTAL_SPECIALISTS = {
    "valuation": VALUATION,
    "moat_quality": MOAT_QUALITY,
    "earnings_intelligence": EARNINGS_INTELLIGENCE,
}


def build_orchestrator(
    resolver: InstrumentResolver,
    llm_router: Router,
    chain: FallbackChain,
    clock: Callable[[], datetime] = utc_now,
    fetch_bars: Callable[[str], list[Bar]] | None = None,
    fundamentals: Callable[[Resolution], dict] | None = None,
) -> Orchestrator:
    """Wire the specialists to their data and models: Quant/Technical always, and Valuation, Moat & Quality and
    Earnings Intelligence when a `fundamentals` source is given (all three read the same packet).
    `fetch_bars` and `fundamentals` let a caller share downloads with other consumers (the dashboard does)."""
    fetch = fetch_bars or history_fetcher(chain, clock=clock)
    client = llm_router.client_for("specialist")
    specialists = {"quant_technical": Specialist(QUANT_TECHNICAL, client)}
    builders = {"quant_technical": technical_packet_builder(fetch, clock)}
    if fundamentals is not None:
        for name, spec in FUNDAMENTAL_SPECIALISTS.items():
            specialists[name] = Specialist(spec, client)
            builders[name] = fundamentals
    return Orchestrator(resolver, specialists, builders)


@dataclass(frozen=True)
class LiveSources:
    resolver: InstrumentResolver
    llm_router: Router
    chain: FallbackChain
    store: DataStore
    bars: RequestCache
    fundamentals: LiveFundamentals


def live_sources(env_file: Path | str = DEFAULT_ENV_FILE) -> LiveSources:
    """Everything wired to real sources: NSE master lists, holidays and index valuation, jugaad-data then Yahoo for
    prices, Yahoo for statements, and whichever LLM providers have keys. Loads into a fresh in-memory store on every
    call."""
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
    IndexValuationLoader(store).refresh(since=ist_date(utc_now()) - timedelta(days=INDEX_VALUATION_DAYS))
    bars = RequestCache(history_fetcher(chain))
    fundamentals = LiveFundamentals(FundamentalsLoader(store), valuation_history(store), bars)
    return LiveSources(resolver, llm_router, chain, store, bars, fundamentals)


def live_orchestrator(env_file: Path | str = DEFAULT_ENV_FILE) -> Orchestrator:
    sources = live_sources(env_file)
    return build_orchestrator(
        sources.resolver, sources.llm_router, sources.chain, fetch_bars=sources.bars, fundamentals=sources.fundamentals
    )


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

`src/athena/dashboard/service.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from athena.clock import utc_now
from athena.cli import build_orchestrator, live_sources
from athena.contracts import AthenaError
from athena.dashboard.risk import RiskWorld, refresh_risk_data, risk_packet, risk_world_from_store
from athena.dashboard.view import DashboardView, build_view
from athena.llm.envfile import DEFAULT_ENV_FILE
from athena.orchestrator.builders import HISTORY_DAYS, RequestCache
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, Orchestrator
from athena.resolver import Resolution
from athena.technicals.packet import build_technical_packet
from athena.trading_calendar import ist_date


FundamentalsSource = Callable[[Resolution], dict]


class DashboardService:
    """Turns a typed instrument into a `DashboardView`: the orchestrator's verdict plus the charts and metric
    panels that sit around it."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        bars: RequestCache,
        risk_world: RiskWorld,
        clock: Callable[[], datetime] = utc_now,
        fundamentals: FundamentalsSource | None = None,
    ):
        self._orchestrator = orchestrator
        self._bars = bars
        self._risk_world = risk_world
        self._clock = clock
        self._fundamentals = fundamentals

    def view(self, query: str) -> DashboardView:
        self._bars.clear()
        if hasattr(self._fundamentals, "clear"):
            self._fundamentals.clear()
        result = self._orchestrator.analyze(query)
        if result.status == NEEDS_CLARIFICATION:
            return build_view(result)
        assert result.resolution is not None
        bars = self._bars(result.resolution.identifier)
        now = self._clock()
        technical = build_technical_packet(result.resolution.identifier, now, bars)
        risk = risk_packet(result.resolution, bars, self._risk_world, now)
        fundamentals, note = None, None
        if self._fundamentals is not None and result.resolution.asset_class == "equity":
            try:
                fundamentals = self._fundamentals(result.resolution)
            except AthenaError as exc:
                note = f"fundamentals unavailable: {exc}"
        return build_view(result, bars, technical, risk, fundamentals, note)


def live_service(env_file: Path | str = DEFAULT_ENV_FILE) -> DashboardService:
    """The dashboard wired to real sources. The market series for the risk panel and the NIFTY 50 valuation history
    are loaded once, when the service is built, so a session left open across days should be restarted."""
    sources = live_sources(env_file)
    refresh_risk_data(sources.store, since=ist_date(utc_now()) - timedelta(days=HISTORY_DAYS))
    orchestrator = build_orchestrator(
        sources.resolver, sources.llm_router, sources.chain, fetch_bars=sources.bars, fundamentals=sources.fundamentals
    )
    return DashboardService(
        orchestrator, sources.bars, risk_world_from_store(sources.store), fundamentals=sources.fundamentals
    )
```

- [ ] **Step 4: Run to verify they pass, then the full suite and lint**

Run: `.venv/Scripts/python -m pytest tests/test_fundamentals_source.py tests/test_cli_fundamentals.py tests/test_orchestrator_builders.py tests/test_dashboard_service.py tests/test_cli.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: all pass, then `458 passed, 53 skipped`. Lint: `.venv/Scripts/python -m pyflakes src/athena tests` prints only the two pre-existing unused imports in `src/athena/evaluation/resolver_eval.py`.

- [ ] **Step 5: Mutation check (do not commit this edit)**

In `cli.py`, replace `    if fundamentals is not None:` with `    if False:` and run `.venv/Scripts/python -m pytest tests/test_cli_fundamentals.py -q`: expect `test_with_a_fundamentals_source_a_stock_is_analysed_by_all_four_equity_specialists` and `test_a_fundamentals_failure_skips_the_three_but_the_technical_specialist_still_answers` to FAIL. Undo it and confirm `git diff` is empty for `cli.py`.

- [ ] **Step 6: Commit and push**

```bash
git add src/athena/orchestrator/builders.py src/athena/orchestrator/fundamentals_source.py src/athena/cli.py src/athena/dashboard/service.py tests/test_fundamentals_source.py tests/test_cli_fundamentals.py tests/test_orchestrator_builders.py tests/test_dashboard_service.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: route the three fundamentals specialists through the orchestrator and share one fundamentals fetch"
git push
```

---

### Task 4: Live check, docs and graph refresh

**Files:**
- Create: `tests/live/test_live_specialists.py`
- Modify: `TRD.md`, `docs/superpowers/plans/2026-10-05-phase-1e-fundamentals-data-and-metrics.md`

- [ ] **Step 1: Write the live tests `tests/live/test_live_specialists.py`**

```python
import pytest

from athena.cli import live_orchestrator
from athena.contracts import AthenaError
from athena.evaluation.schema import validate_judge_verdict, validate_specialist_output
from athena.orchestrator.orchestrator import OK
from athena.orchestrator.report import format_result

pytestmark = pytest.mark.live

FUNDAMENTAL = {"valuation", "moat_quality", "earnings_intelligence"}


@pytest.fixture(scope="module")
def orchestrator():
    try:
        return live_orchestrator()
    except AthenaError:
        pytest.skip("no LLM provider key is set")


def test_live_stock_is_analysed_by_the_fundamentals_specialists_on_real_data(orchestrator):
    result = orchestrator.analyze("TCS")
    print("\n" + format_result(result))
    assert result.status == OK and validate_judge_verdict(result.verdict) == []
    # a free-tier model can fail validation twice for one specialist; the others must still have answered
    assert len(FUNDAMENTAL & set(result.specialists)) >= 2, result.skipped
    for name, output in result.specialists.items():
        assert validate_specialist_output(output) == [], name


def test_live_bank_is_not_marked_partial_for_ratios_that_do_not_apply_to_banks(orchestrator):
    result = orchestrator.analyze("SBIN")
    print("\n" + format_result(result))
    assert result.status == OK
    ran = FUNDAMENTAL & set(result.specialists)
    assert len(ran) >= 2, result.skipped
    for name in ran:
        assert result.specialists[name]["data_coverage"] == "full" and result.specialists[name]["missing"] == [], name
```

- [ ] **Step 2: Run the default and live suites, then look at real output**

```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m pytest --live tests/live/test_live_specialists.py -q -s
.venv/Scripts/python -m athena.cli TCS
.venv/Scripts/python -m athena.cli SBIN
```

Expected: `458 passed, 55 skipped`, then `2 passed` with a full report printed for TCS and SBIN, then the same report format from the command line (about 30 s each: the first call loads the NSE lists and eight years of index history). Read the reasoning for sense: figures should match the packet, units should be read correctly (a return on equity of 0.487 is 48.7%, an index percentile of 0.0086 is near the market's cheapest), and a bank must show all four specialists at coverage `full`. Verdicts and confidences vary from run to run; a specialist that fails validation twice is listed under "Not run". Also restart the dashboard (`.venv/Scripts/python -m athena.dashboard --server.headless true`) and check that the specialist rows on the page now show four entries for a stock.

- [ ] **Step 3: Update `TRD.md` and the Plan 1e record**

1. At the end of the §2.2 paragraph (`**2.2 Specialist agent base class.**`), append: ` A spec can also declare \`shows\` (the only figures the model sees, and the only figures its numbers are checked against) and a packet can list \`not_applicable\` figures (meaningless for this instrument, for example free cash flow for a bank), which count neither as missing nor against coverage. Every system prompt explains how to read each unit.`
2. At the end of the §2.3 paragraph (`**2.3 Equity specialists**`), append: ` *Valuation, Moat & Quality and Earnings Intelligence implemented in Plan 1f in ratio-only mode* (\`agents/valuation.py\`, \`moat_quality.py\`, \`earnings_intelligence.py\`): P/E, P/B, owner-earnings and free-cash-flow yield, PEG, Graham's number and the market's own valuation percentile; returns, margins, growth and leverage as evidence for a moat (never its source); Sloan accruals, cash conversion, quarter growth, surprise history and the next report date. Not built: discounted cash flow, peer comparables, Graham net-net, and moat source analysis.`
3. In the revision history, add after the Phase 1e line: `- **Oct 6, 2026 (Phase 1f)** — Valuation, Moat & Quality and Earnings Intelligence specialists implemented and routed (see docs/superpowers/plans/2026-10-06-phase-1f-fundamentals-specialists.md).`
4. In `docs/superpowers/plans/2026-10-05-phase-1e-fundamentals-data-and-metrics.md`, insert this block directly after the `**Goal:**` paragraph: `> **Amendment (Plan 1f, 6 Oct 2026):** \`index_pe_percentile\` was changed from a 0-100 "percent" to a 0-1 fraction after a live model read the old value 0.86 (the 0.9th percentile, near the eight-year low) as 85.8%; wherever this plan says percent or 0-100 for that figure, read fraction or 0-1. The plan below is kept as executed.`

- [ ] **Step 4: Commit, push, refresh graph**

```bash
git add tests/live/test_live_specialists.py TRD.md docs/superpowers/plans/2026-10-05-phase-1e-fundamentals-data-and-metrics.md
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "test: add live specialist checks; document fundamentals specialists in TRD"
git push
.venv/Scripts/python -m graphify update .
```

---

## Self-Review (completed)

**Spec coverage (TRD §2.2, §2.3 -> task):** persona-as-prompt specialists on the shared base (Task 2); coverage derived from declared required inputs, with ratio-only mode honest about gaps and lenders handled without false "partial" (Tasks 1-2); number grounding against what the model saw (Task 1); compliance language and no price targets (Task 2 persona tests); routed by the existing table and blended by the existing orchestrator, with a failing specialist listed rather than hidden (Task 3); verified on real data and models, including two defects found and fixed (Tasks 1, 4). Not in this plan: DCF, peer comparables, Graham net-net, moat-source analysis, calibration of the blend, the debate, and golden sets that would measure interpretation errors.

**Placeholder scan:** none; every code block is the file that passed the prototype run.

**Type consistency:** `SpecialistSpec(shows=...)`, `packet["not_applicable"]`, `VALUATION`/`MOAT_QUALITY`/`EARNINGS_INTELLIGENCE` with `CRITICAL`/`OPTIONAL`/`SHOWS`, `FUNDAMENTAL_SPECIALISTS`, `LiveFundamentals(loader, index_history, fetch_bars, clock)`, `RequestCache`, `build_orchestrator(..., fetch_bars, fundamentals)`, `LiveSources(..., bars, fundamentals)` and `DashboardService(..., fundamentals)` match across Tasks 1-4. Test totals: 432 + 9 + 8 + 9 = 458 passed; skipped 53 + 2 live = 55.
