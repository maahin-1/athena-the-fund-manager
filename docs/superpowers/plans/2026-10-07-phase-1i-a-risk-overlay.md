# Phase 1i-a — Risk Overlay and Investor Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After the specialists have spoken, check the stock against the person's own risk limits (a profile with warn and hard levels, optional holdings and an amount to invest) and, on a hard breach, hold a Buy or Overweight back to Hold while showing both calls and every finding.

**Architecture:** A new package `athena.risk_overlay` holds plain data (`RiskProfile`, `Holding`, `Overlay`, `Finding`), parsers for a profile (JSON) and holdings (CSV) that raise only `ProfileError`, the checks (`figures_from_bars`, `run_checks`: volatility, drawdown, VaR, CVaR, liquidity, position, concentration) and the override (`apply_overlay`). `value_at_risk` and `expected_shortfall` join the metrics engine. The orchestrator takes an optional `Overlay` and its own price source, applies the override after the blend and puts the findings in the verdict (`risk_findings`, `pre_overlay_verdict`); the command line gains `--profile`, `--holdings`, `--amount`; the dashboard gains a sidebar and a "Risk overlay" table. Without an overlay nothing changes.

**Tech Stack:** Python >= 3.11, pytest, numpy, Streamlit 1.65. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-07-phase-1i-a-risk-overlay-design.md` (Task 5 brings it in line with what was built); `PRD.md` FR-10; `TRD.md` §2.7 and §2.1.

**Plan series:** 0a-0e, 1a-1h-c (done) -> **1i-a (this plan)** -> 1i-b (golden test sets) -> 1i-c (blend calibration) -> later phases.

**Suggested models:** Sonnet at medium effort for the implementers (every step carries complete code or a verified script), Sonnet for reviewers, Opus for the final review. Prototyped in a scratch copy first: 845 offline tests passed (723 before) and the 5 new live checks passed on real SBIN, HDFCBANK and IDEA prices; 66 deliberate mutations were each caught; the task-by-task steps below were re-run on a clean copy of the repository's tracked files to prove they apply and pass (786, then 820, then 836, then 845 passed). A real end-to-end run (`python -m athena.cli SBIN --profile moderate --amount 50000000`) printed the specialists, the Risk overlay block and the profile note.

## Verified findings (9 Oct 2026)

- Real one-year figures on the moderate preset, amount 1 lakh: SBIN (volatility 24%, drawdown 23%, VaR 2.2%, CVaR 3.6%) passes every check; HDFCBANK, ITC and TCS warn on drawdown (32% to 40%, warning level 30%); IDEA (volatility 46%, VaR 4.0%, CVaR 5.5%) warns on four measures; the traded value of large caps is in the hundreds of crores a day, so a normal retail amount is far inside the liquidity limit.
- VaR at 95% is minus the 5th percentile of the daily returns (numpy's linear interpolation) and CVaR the mean of the returns at or below it; on 100 evenly spaced returns from -5.0% to +4.9% they are 4.505% and 4.8%, which the tests pin.
- The overlay needs only the instrument's own bars (no NIFTY series): the orchestrator reuses the price source the specialists and charts already share.
- Streamlit's `AppTest` drives the sidebar widgets (`app.sidebar.checkbox(key=...)`) and a pasted CSV; it cannot drive `st.file_uploader`, so the upload path is the one part of the page without a test (the pasted path shares the same parser).
- An unrelated click on the page does not ask the service again: the remembered answer is keyed by the query and the overlay's canonical JSON.
- A mutation run counts a mutant caught only when pytest reports failed tests; a collection error is not a catch (a broken test file once made every mutant look caught).

**Honest limits:**
- One instrument at a time against the person's own numbers; no portfolio VaR across the holdings' histories, no sector or factor limits, no rebalancing.
- VaR and CVaR describe about a year of daily returns and say nothing about a crash outside that window.
- The preset limits are uncalibrated starting points and the report says so.
- Holdings values and the amount are what the person types; the app does not check them and stores nothing.
- Beta is not part of the overlay (the risk panel already shows it).
- Mutual funds and bonds are not covered.

## Global Constraints

- No network in the default test run; the live tests are opt-in via `--live` and need no API key.
- The overlay is deterministic code: no language model, no randomness, every number computed from the instrument's bars and the person's inputs.
- A hard breach may hold a Buy or Overweight back to Hold; it never changes a Hold, Underweight or Sell, and a warning never changes a verdict.
- Profile, holdings and amount are plain data passed per request; the app stores nothing and holds no global state; nothing reads keys from the environment.
- Arbitrary input (a pasted profile or CSV, a file) is parsed so that only `ProfileError` (a `ValueError`) comes out, never a traceback.
- With no overlay the orchestrator's output is unchanged apart from the wording of the one note.
- Files in the repository use LF; no new dependency; match the surrounding code's comment density.
- Commit with the GitHub no-reply identity: `git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit ...` and end each commit message with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; `git push` after each task; before committing run `git status --short` and add any new file the task created (a forgotten new file once left a commit that did not build).
- Windows, Git Bash: run Python as `.venv/Scripts/python`. Helper scripts live outside the repository (save them under `$TEMP` with the Write tool, never shell heredocs) and are run from the repository root. Never read or mention the repository's secrets file.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/athena/metrics/stats.py` | modified: `value_at_risk`, `expected_shortfall` |
| `src/athena/risk_overlay/__init__.py` | new, empty |
| `src/athena/risk_overlay/model.py` | new: `MEASURES`, `LABELS`, `Limit`, `RiskProfile`, `Holding`, `Overlay`, `Finding`, `PRESETS` |
| `src/athena/risk_overlay/parse.py` | new: `ProfileError`, `parse_profile`, `profile_to_dict`, `parse_holdings`, `load_profile`, `load_holdings`, `build_overlay`, `overlay_to_dict`, `overlay_token` |
| `src/athena/risk_overlay/checks.py` | new: `Figures`, `figures_from_bars`, `run_checks` |
| `src/athena/risk_overlay/apply.py` | new: `apply_overlay` |
| `src/athena/evaluation/schema.py` | modified: the verdict contract's optional `pre_overlay_verdict`, `risk_findings` |
| `src/athena/orchestrator/orchestrator.py`, `report.py` | modified: optional overlay, price source, report block |
| `src/athena/cli.py` | modified: `--profile`, `--holdings`, `--amount`; the orchestrator gets its price source |
| `src/athena/dashboard/service.py`, `risk_form.py`, `app.py` | modified/new: overlay through the service; the sidebar; the Risk overlay table |
| `tests/test_risk_overlay_parse.py`, `tests/test_risk_overlay_checks.py` | new |
| `tests/test_metrics_stats.py`, `test_orchestrator.py`, `test_orchestrator_report.py`, `test_cli.py`, `test_dashboard_service.py`, `test_dashboard_app.py`, `tests/dash_fakes.py` | modified |
| `tests/live/test_live_risk_overlay.py` | new, opt-in |
| `TRD.md`, `PRD.md`, the 1i-a design | modified: §2.7, §2.1, revision history, FR-10 status, the design brought in line |

---

### Task 1: Value at risk, the risk profile and the holdings parser

**Files:**
- Create: `src/athena/risk_overlay/__init__.py` (empty), `model.py`, `parse.py`, `tests/test_risk_overlay_parse.py`
- Modify (by the scripts below): `src/athena/metrics/stats.py`, `tests/test_metrics_stats.py`

**Interfaces:**
- Produces: `stats.value_at_risk(returns, confidence=0.95, min_obs=20) -> float` and `stats.expected_shortfall(...)` (positive fractions; 0.0 when the tail was a gain; `InsufficientData` when too few); `model.MEASURES`, `LABELS`, `OK/WARN/BREACH/UNCHECKED`, `Limit(warn, hard)`, `RiskProfile(name, limits: dict[str, Limit])` (a measure not in `limits` is off), `Holding(symbol, value)`, `Overlay(profile, holdings=(), amount=None)`, `Finding(check, status, value, warn, hard, message)` with `to_dict()`, `PRESETS` (conservative, moderate, aggressive); `parse.ProfileError`, `parse_profile(data) -> RiskProfile` (data: `{"preset"?, "name"?, "limits"?: {measure: {"warn","hard"} | null}}`), `profile_to_dict`, `parse_holdings(text) -> tuple[Holding, ...]` (CSV with the header `symbol,value`), `load_profile(spec)` (preset name or JSON path), `load_holdings(path)`, `build_overlay(profile, holdings, amount) -> Overlay | None`, `overlay_to_dict`, `overlay_token`.
- Consumes: `athena.resolver.normalize_input`; the existing `athena.metrics.stats` helpers (`_array`, `MIN_OBS`).

- [ ] **Step 1: Write the failing tests**

Save as `$TEMP/s1i_tests_stats.py` and run `.venv/Scripts/python $TEMP/s1i_tests_stats.py` from the repository root (it appends the VaR and shortfall tests to `tests/test_metrics_stats.py`). Then create `tests/test_risk_overlay_parse.py` with the content below.

```python
import pathlib


def edit(path, pairs, append=""):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t + append, encoding="utf-8", newline="\n")


# ---- value at risk and expected shortfall, against hand-computed values
edit(
    "tests/test_metrics_stats.py",
    [],
    append='''

TAIL = [i / 1000 for i in range(-50, 50)]  # 100 returns from -5.0% to +4.9%


def test_value_at_risk_is_the_loss_at_the_fifth_percentile():
    # the 5th percentile sits 4.95 steps above the worst return: -0.050 + 4.95 x 0.001 = -0.04505
    assert stats.value_at_risk(TAIL) == pytest.approx(0.04505)
    assert stats.value_at_risk(TAIL, confidence=0.99) == pytest.approx(0.04901)


def test_expected_shortfall_is_the_average_loss_beyond_the_cutoff_and_never_below_var():
    assert stats.expected_shortfall(TAIL) == pytest.approx(0.048)  # mean of -0.050 ... -0.046
    assert stats.expected_shortfall(TAIL) >= stats.value_at_risk(TAIL)
    assert stats.expected_shortfall(TAIL, confidence=0.99) >= stats.value_at_risk(TAIL, confidence=0.99)


def test_value_at_risk_and_shortfall_are_zero_when_the_whole_tail_was_a_gain():
    gains = [0.01 + i / 10000 for i in range(30)]
    assert stats.value_at_risk(gains) == 0.0 and stats.expected_shortfall(gains) == 0.0


def test_value_at_risk_and_shortfall_need_enough_observations():
    for function in (stats.value_at_risk, stats.expected_shortfall):
        with pytest.raises(InsufficientData, match="at least 20"):
            function([-0.01] * 5)
''',
)

print("stats tests added")
```

`tests/test_risk_overlay_parse.py`:

```python
import json
import re

import pytest

from athena.risk_overlay.model import MEASURES, PRESETS, Holding, Limit, Overlay
from athena.risk_overlay.parse import (
    ProfileError,
    build_overlay,
    load_holdings,
    load_profile,
    overlay_token,
    parse_holdings,
    parse_profile,
    profile_to_dict,
)


def test_every_preset_has_every_check_and_each_warning_is_below_its_hard_limit():
    assert set(PRESETS) == {"conservative", "moderate", "aggressive"}
    for profile in PRESETS.values():
        assert tuple(profile.limits) == MEASURES
        assert all(0 < limit.warn < limit.hard for limit in profile.limits.values())


def test_a_stricter_preset_has_lower_limits_for_every_check():
    for measure in MEASURES:
        hard = [PRESETS[name].limits[measure].hard for name in ("conservative", "moderate", "aggressive")]
        assert hard == sorted(hard) and len(set(hard)) == 3


def test_a_preset_alone_is_the_preset_and_overrides_replace_or_switch_off_single_checks():
    assert parse_profile({"preset": "moderate"}) == PRESETS["moderate"]
    profile = parse_profile({"preset": "moderate", "name": "mine", "limits": {"volatility": {"warn": 0.4, "hard": 0.6}, "liquidity": None}})
    assert profile.name == "mine"
    assert profile.limits["volatility"] == Limit(0.4, 0.6) and "liquidity" not in profile.limits
    assert profile.limits["drawdown"] == PRESETS["moderate"].limits["drawdown"]


def test_a_profile_without_a_preset_is_exactly_the_limits_given_and_keeps_the_report_order():
    profile = parse_profile({"limits": {"position": {"warn": 0.1, "hard": 0.2}, "volatility": {"warn": 1, "hard": 2}}})
    assert profile.name == "custom" and list(profile.limits) == ["volatility", "position"]


def test_a_profile_turns_back_into_data_that_parses_to_the_same_profile():
    profile = PRESETS["aggressive"]
    assert parse_profile(profile_to_dict(profile)) == profile


@pytest.mark.parametrize(
    "data, expected",
    [
        ([], "a profile must be an object"),
        ({"limits": {}}, "needs at least one limit"),
        ({"preset": "reckless"}, "preset: unknown preset 'reckless'"),
        ({"preset": 3}, "preset: unknown preset 3"),
        ({"preset": "moderate", "colour": 1}, "unexpected key(s) ['colour']"),
        ({"preset": "moderate", "name": ""}, "name: must be text"),
        ({"preset": "moderate", "name": "x" * 61}, "name: must be text"),
        ({"preset": "moderate", "limits": []}, "limits: must be an object"),
        ({"preset": "moderate", "limits": {"luck": {"warn": 1, "hard": 2}}}, "limits.luck: unknown check"),
        ({"preset": "moderate", "limits": {"volatility": 0.3}}, "limits.volatility: must be an object with exactly 'warn' and 'hard'"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": 0.3}}}, "limits.volatility: must be an object with exactly"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": 0.3, "hard": 0.4, "x": 1}}}, "limits.volatility: must be an object with exactly"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": 0.5, "hard": 0.4}}}, "limits.volatility: 'warn' must be below 'hard'"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": 0.4, "hard": 0.4}}}, "limits.volatility: 'warn' must be below 'hard'"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": 0, "hard": 0.4}}}, "limits.volatility.warn: must be a number above zero"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": -1, "hard": 0.4}}}, "limits.volatility.warn: must be a number above zero"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": True, "hard": 0.4}}}, "limits.volatility.warn: must be a number"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": "0.3", "hard": 0.4}}}, "limits.volatility.warn: must be a number"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": 0.3, "hard": float("inf")}}}, "limits.volatility.hard: must be a number above zero"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": 0.3, "hard": float("nan")}}}, "limits.volatility.hard: must be a number above zero"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": 0.3, "hard": 10**400}}}, "limits.volatility.hard: is too large"),
    ],
)
def test_a_bad_profile_is_refused_with_the_path_of_the_wrong_part(data, expected):
    with pytest.raises(ProfileError, match=re.escape(expected)):
        parse_profile(data)


@pytest.mark.parametrize("data", [None, 5, "text", {"preset": []}, {"preset": {}}, {"limits": {"volatility": []}}, {"name": []}, {1: 2}])
def test_whatever_the_data_holds_only_a_profile_error_comes_out(data):
    with pytest.raises(ProfileError):
        parse_profile(data)


def test_a_profile_error_is_a_value_error_so_callers_can_catch_either():
    assert issubclass(ProfileError, ValueError)


HEADER = "symbol,value\n"


def test_holdings_are_read_with_names_cleaned_numbers_with_commas_and_repeats_added_up():
    holdings = parse_holdings(HEADER + "sbin, 50000\nNSE:TCS.NS,\"1,20,000\"\nSBIN,25000.5\n\n")
    assert holdings == (Holding("SBIN", 75000.5), Holding("TCS", 120000.0))


def test_the_header_may_have_any_case_spaces_and_a_byte_order_mark_and_empty_text_is_no_holdings():
    assert parse_holdings("﻿ Symbol , VALUE \nITC,10\n") == (Holding("ITC", 10.0),)
    assert parse_holdings("") == () and parse_holdings("   \n") == ()


@pytest.mark.parametrize(
    "text, expected",
    [
        ("ticker,amount\nSBIN,1\n", "the first row must be the header 'symbol,value'"),
        ("SBIN,1\n", "the first row must be the header 'symbol,value'"),
        (HEADER + "SBIN\n", "row 2: expected 2 columns"),
        (HEADER + "SBIN,1,2\n", "row 2: expected 2 columns"),
        (HEADER + "SBIN,1\n,5\n", "row 3: the symbol is empty"),
        (HEADER + "SBIN,lots\n", "row 2: 'lots' is not a number"),
        (HEADER + "SBIN,0\n", "row 2: the value must be a number above zero"),
        (HEADER + "SBIN,-5\n", "row 2: the value must be a number above zero"),
        (HEADER + "SBIN,nan\n", "row 2: the value must be a number above zero"),
        (HEADER + "SBIN,inf\n", "row 2: the value must be a number above zero"),
    ],
)
def test_bad_holdings_are_refused_with_the_row(text, expected):
    with pytest.raises(ProfileError, match=expected):
        parse_holdings(text)


def test_too_many_different_symbols_are_refused():
    rows = "".join(f"S{n},1\n" for n in range(501))
    with pytest.raises(ProfileError, match="at most 500 different symbols"):
        parse_holdings(HEADER + rows)
    assert len(parse_holdings(HEADER + rows[: rows.index("S500")])) == 500


def test_a_text_that_is_not_csv_at_all_is_a_profile_error():
    with pytest.raises(ProfileError):
        parse_holdings("symbol,value\n\"unclosed,1\n")


def test_a_profile_loads_from_a_preset_name_or_a_json_file(tmp_path):
    assert load_profile("conservative") is PRESETS["conservative"]
    path = tmp_path / "mine.json"
    path.write_text(json.dumps({"preset": "moderate", "name": "from file"}), encoding="utf-8")
    assert load_profile(str(path)).name == "from file"


def test_a_profile_that_cannot_be_loaded_says_why(tmp_path):
    with pytest.raises(ProfileError, match="is not a preset .* and cannot be read as a file"):
        load_profile(str(tmp_path / "missing.json"))
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileError, match="is not valid JSON"):
        load_profile(str(broken))
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 100000, encoding="utf-8")
    with pytest.raises(ProfileError, match="is not valid JSON"):
        load_profile(str(deep))
    binary = tmp_path / "binary.json"
    binary.write_bytes(b"\xff\xfe\x00\x80")
    with pytest.raises(ProfileError, match="is not a text file"):
        load_profile(str(binary))
    wrong = tmp_path / "wrong.json"
    wrong.write_text("[1]", encoding="utf-8")
    with pytest.raises(ProfileError, match="a profile must be an object"):
        load_profile(str(wrong))


def test_holdings_load_from_a_file_and_a_missing_or_binary_file_is_a_profile_error(tmp_path):
    path = tmp_path / "h.csv"
    path.write_text(HEADER + "SBIN,100\n", encoding="utf-8")
    assert load_holdings(str(path)) == (Holding("SBIN", 100.0),)
    with pytest.raises(ProfileError, match="cannot read"):
        load_holdings(str(tmp_path / "nope.csv"))
    binary = tmp_path / "b.csv"
    binary.write_bytes(b"\xff\xfe\x00\x80")
    with pytest.raises(ProfileError, match="is not a text file"):
        load_holdings(str(binary))


def test_the_command_line_builds_an_overlay_only_when_a_profile_is_named(tmp_path):
    assert build_overlay(None, None, None) is None
    overlay = build_overlay("moderate", None, 5000.0)
    assert overlay == Overlay(PRESETS["moderate"], (), 5000.0)
    path = tmp_path / "h.csv"
    path.write_text(HEADER + "SBIN,100\n", encoding="utf-8")
    assert build_overlay("moderate", str(path), None).holdings == (Holding("SBIN", 100.0),)
    for bad in (dict(profile=None, holdings=str(path), amount=None), dict(profile=None, holdings=None, amount=10.0)):
        with pytest.raises(ProfileError, match="need --profile"):
            build_overlay(**bad)
    for amount in (0.0, -1.0, float("inf"), float("nan")):
        with pytest.raises(ProfileError, match="--amount must be a number above zero"):
            build_overlay("moderate", None, amount)


def test_an_overlay_token_changes_with_anything_that_changes_the_checks():
    base = Overlay(PRESETS["moderate"], (Holding("SBIN", 100.0),), 5000.0)
    tokens = {
        overlay_token(None),
        overlay_token(base),
        overlay_token(Overlay(PRESETS["conservative"], base.holdings, base.amount)),
        overlay_token(Overlay(base.profile, (Holding("SBIN", 101.0),), base.amount)),
        overlay_token(Overlay(base.profile, base.holdings, 5001.0)),
        overlay_token(Overlay(base.profile, base.holdings, None)),
    }
    assert len(tokens) == 6 and overlay_token(base) == overlay_token(Overlay(PRESETS["moderate"], (Holding("SBIN", 100.0),), 5000.0))


@pytest.mark.parametrize("error", [TypeError("x"), OverflowError("x"), RecursionError("x"), ValueError("x")])
def test_an_unforeseen_failure_while_reading_a_profile_still_comes_out_as_a_profile_error(monkeypatch, error):
    def boom(data):
        raise error

    monkeypatch.setattr("athena.risk_overlay.parse._parse_profile", boom)
    with pytest.raises(ProfileError, match="this is not a valid profile"):
        parse_profile({"preset": "moderate"})


def test_an_unforeseen_failure_while_reading_holdings_still_comes_out_as_a_profile_error(monkeypatch):
    import csv

    def boom(text):
        raise csv.Error("x")

    monkeypatch.setattr("athena.risk_overlay.parse._parse_holdings", boom)
    with pytest.raises(ProfileError, match="not a readable CSV"):
        parse_holdings(HEADER + "SBIN,1\n")
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_metrics_stats.py tests/test_risk_overlay_parse.py -q`
Expected: failures for `value_at_risk` (no attribute) and a collection error `No module named 'athena.risk_overlay'`.

- [ ] **Step 3: Write the implementation**

Save the script below as `$TEMP/s1i_a_stats.py` and run it from the repository root (it appends the two functions to `stats.py` and prints `stats edited`):

```python
import pathlib


def edit(path, pairs, append=""):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t + append, encoding="utf-8", newline="\n")


# ---- value at risk and expected shortfall
edit(
    "src/athena/metrics/stats.py",
    [],
    append='''

def value_at_risk(returns: Sequence[float], confidence: float = 0.95, min_obs: int = MIN_OBS) -> float:
    """Historical one-period VaR: the loss, as a positive fraction, that the worst `1 - confidence` of the returns
    reach (the percentile, linearly interpolated). 0.0 when even that tail was a gain."""
    values = _array(returns, "value at risk", min_obs)
    return max(0.0, float(-np.percentile(values, (1.0 - confidence) * 100.0)))


def expected_shortfall(returns: Sequence[float], confidence: float = 0.95, min_obs: int = MIN_OBS) -> float:
    """Historical CVaR: the average loss over the returns at or below the VaR cutoff, as a positive fraction."""
    values = _array(returns, "expected shortfall", min_obs)
    cutoff = np.percentile(values, (1.0 - confidence) * 100.0)
    return max(0.0, float(-np.mean(values[values <= cutoff])))
''',
)

print("stats edited")
```

Create `src/athena/risk_overlay/__init__.py` as an empty file, then the two modules below (copy each verbatim).

`src/athena/risk_overlay/model.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field

# The things a profile can limit, in the order a report lists them.
MEASURES = ("volatility", "drawdown", "var_95", "cvar_95", "position", "concentration", "liquidity")
LABELS = {
    "volatility": "Volatility",
    "drawdown": "Worst drawdown",
    "var_95": "1-day VaR (95%)",
    "cvar_95": "1-day CVaR (95%)",
    "position": "Position size",
    "concentration": "Concentration (HHI)",
    "liquidity": "Amount against daily traded value",
}
OK, WARN, BREACH, UNCHECKED = "ok", "warn", "breach", "unchecked"
MAX_HOLDINGS = 500


@dataclass(frozen=True)
class Limit:
    """Warn at or above `warn`, breach at or above `hard`."""

    warn: float
    hard: float


@dataclass(frozen=True)
class RiskProfile:
    """A person's limits. A measure that is not in `limits` is switched off."""

    name: str
    limits: dict[str, Limit] = field(default_factory=dict)


@dataclass(frozen=True)
class Holding:
    symbol: str
    value: float  # rupees held now


@dataclass(frozen=True)
class Overlay:
    """Everything the risk overlay needs from the person: limits, what they own, and what they might buy."""

    profile: RiskProfile
    holdings: tuple[Holding, ...] = ()
    amount: float | None = None  # rupees they are thinking of putting in


@dataclass(frozen=True)
class Finding:
    check: str  # one of MEASURES
    status: str  # OK, WARN, BREACH or UNCHECKED
    value: float | None
    warn: float
    hard: float
    message: str

    def to_dict(self) -> dict:
        return {"check": self.check, "status": self.status, "value": self.value, "warn": self.warn, "hard": self.hard, "message": self.message}


def _limits(**pairs: tuple[float, float]) -> dict[str, Limit]:
    return {name: Limit(*pair) for name, pair in pairs.items()}


# Starting points, not calibrated to any data.
PRESETS: dict[str, RiskProfile] = {
    "conservative": RiskProfile("conservative", _limits(
        volatility=(0.25, 0.35), drawdown=(0.20, 0.30), var_95=(0.020, 0.030), cvar_95=(0.030, 0.045),
        position=(0.05, 0.10), concentration=(0.15, 0.25), liquidity=(0.01, 0.03),
    )),
    "moderate": RiskProfile("moderate", _limits(
        volatility=(0.35, 0.50), drawdown=(0.30, 0.45), var_95=(0.030, 0.045), cvar_95=(0.045, 0.065),
        position=(0.10, 0.15), concentration=(0.20, 0.30), liquidity=(0.02, 0.05),
    )),
    "aggressive": RiskProfile("aggressive", _limits(
        volatility=(0.50, 0.70), drawdown=(0.45, 0.60), var_95=(0.045, 0.065), cvar_95=(0.065, 0.090),
        position=(0.15, 0.25), concentration=(0.30, 0.45), liquidity=(0.05, 0.10),
    )),
}
```

`src/athena/risk_overlay/parse.py`:

```python
from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from typing import Any

from athena.resolver import normalize_input
from athena.risk_overlay.model import MAX_HOLDINGS, MEASURES, PRESETS, Holding, Limit, Overlay, RiskProfile

NAME_LIMIT = 60
PROFILE_KEYS = {"preset", "name", "limits"}
HOLDINGS_HEADER = ("symbol", "value")


class ProfileError(ValueError):
    """A profile or a holdings list that cannot be used; the message says which part and why."""


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(f"{path}: must be a number")
    try:
        number = float(value)  # a huge integer overflows here
    except OverflowError:
        raise ProfileError(f"{path}: is too large") from None
    if not math.isfinite(number) or number <= 0:
        raise ProfileError(f"{path}: must be a number above zero")
    return number


def _limit(data: Any, path: str) -> Limit:
    if not isinstance(data, dict) or set(data) != {"warn", "hard"}:
        raise ProfileError(f"{path}: must be an object with exactly 'warn' and 'hard' (or null to switch the check off)")
    warn, hard = _number(data["warn"], f"{path}.warn"), _number(data["hard"], f"{path}.hard")
    if warn >= hard:
        raise ProfileError(f"{path}: 'warn' must be below 'hard'")
    return Limit(warn, hard)


def _parse_profile(data: Any) -> RiskProfile:
    if not isinstance(data, dict):
        raise ProfileError("a profile must be an object")
    extra = sorted(str(key) for key in set(data) - PROFILE_KEYS)
    if extra:
        raise ProfileError(f"unexpected key(s) {extra}; allowed: {sorted(PROFILE_KEYS)}")
    preset = data.get("preset")
    if preset is not None and (not isinstance(preset, str) or preset not in PRESETS):
        raise ProfileError(f"preset: unknown preset {preset!r}; choose from {', '.join(PRESETS)}")
    name = data.get("name", preset or "custom")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= NAME_LIMIT:
        raise ProfileError(f"name: must be text of 1 to {NAME_LIMIT} characters")
    limits = dict(PRESETS[preset].limits) if preset else {}
    given = data.get("limits", {})
    if not isinstance(given, dict):
        raise ProfileError("limits: must be an object")
    for measure, raw in given.items():
        if not isinstance(measure, str) or measure not in MEASURES:
            raise ProfileError(f"limits.{measure}: unknown check; choose from {', '.join(MEASURES)}")
        if raw is None:
            limits.pop(measure, None)
        else:
            limits[measure] = _limit(raw, f"limits.{measure}")
    if not limits:
        raise ProfileError("a profile needs at least one limit (give a preset or some limits)")
    return RiskProfile(name.strip(), {measure: limits[measure] for measure in MEASURES if measure in limits})


def parse_profile(data: Any) -> RiskProfile:
    """A profile from plain data; raises ProfileError, never anything else, whatever the data holds."""
    try:
        return _parse_profile(data)
    except ProfileError:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ProfileError("this is not a valid profile") from None


def profile_to_dict(profile: RiskProfile) -> dict:
    return {"name": profile.name, "limits": {m: {"warn": limit.warn, "hard": limit.hard} for m, limit in profile.limits.items()}}


def parse_holdings(text: str) -> tuple[Holding, ...]:
    """Holdings from CSV text with a `symbol,value` header; the same symbol on several rows is added up."""
    try:
        return _parse_holdings(text)
    except ProfileError:
        raise
    except (csv.Error, TypeError, ValueError, OverflowError):
        raise ProfileError("holdings: this is not a readable CSV with 'symbol,value' columns") from None


def _parse_holdings(text: str) -> tuple[Holding, ...]:
    if not text.strip():
        return ()
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = [cell.strip().lower() for cell in next(reader, [])]
    if tuple(header) != HOLDINGS_HEADER:
        raise ProfileError("holdings: the first row must be the header 'symbol,value'")
    totals: dict[str, float] = {}
    for number, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != 2:
            raise ProfileError(f"holdings row {number}: expected 2 columns (symbol,value), got {len(row)}")
        symbol = normalize_input(row[0])
        if not symbol:
            raise ProfileError(f"holdings row {number}: the symbol is empty")
        try:
            value = float(row[1].strip().replace(",", ""))
        except ValueError:
            raise ProfileError(f"holdings row {number}: {row[1]!r} is not a number") from None
        if not math.isfinite(value) or value <= 0:
            raise ProfileError(f"holdings row {number}: the value must be a number above zero")
        totals[symbol] = totals.get(symbol, 0.0) + value
        if len(totals) > MAX_HOLDINGS:
            raise ProfileError(f"holdings: at most {MAX_HOLDINGS} different symbols")
    return tuple(Holding(symbol, value) for symbol, value in totals.items())


def overlay_to_dict(overlay: Overlay) -> dict:
    """Plain data that identifies an overlay request, for caching and for tests."""
    return {
        "profile": profile_to_dict(overlay.profile),
        "holdings": [[h.symbol, h.value] for h in overlay.holdings],
        "amount": overlay.amount,
    }


def overlay_token(overlay: Overlay | None) -> str:
    return "none" if overlay is None else json.dumps(overlay_to_dict(overlay), sort_keys=True)


def load_profile(spec: str) -> RiskProfile:
    """A preset name, or the path of a JSON file holding a profile."""
    if spec in PRESETS:
        return PRESETS[spec]
    try:
        text = Path(spec).read_text(encoding="utf-8")
    except OSError:
        raise ProfileError(f"profile: {spec!r} is not a preset ({', '.join(PRESETS)}) and cannot be read as a file") from None
    except UnicodeDecodeError:
        raise ProfileError(f"profile: {spec} is not a text file") from None
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        raise ProfileError(f"profile: {spec} is not valid JSON") from None
    return parse_profile(data)


def load_holdings(path: str) -> tuple[Holding, ...]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        raise ProfileError(f"holdings: cannot read {path}") from None
    except UnicodeDecodeError:
        raise ProfileError(f"holdings: {path} is not a text file") from None
    return parse_holdings(text)


def build_overlay(profile: str | None, holdings: str | None, amount: float | None) -> Overlay | None:
    """The overlay the command line asks for, or None when no profile was given."""
    if profile is None:
        if holdings is not None or amount is not None:
            raise ProfileError("--holdings and --amount need --profile")
        return None
    if amount is not None and (not math.isfinite(amount) or amount <= 0):
        raise ProfileError("--amount must be a number above zero")
    return Overlay(load_profile(profile), load_holdings(holdings) if holdings else (), amount)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_metrics_stats.py tests/test_risk_overlay_parse.py -q` (expect `75 passed`), `.venv/Scripts/python -m pyflakes src tests` (prints nothing), then the whole suite `.venv/Scripts/python -m pytest -q` (expect `786 passed, 65 skipped`).

- [ ] **Step 5: Mutation check**

Save the helper below as `$TEMP/mutate_1i.py` (used again in Tasks 2 to 4). Run `.venv/Scripts/python $TEMP/mutate_1i.py stats parse` from the repository root. Every line must start with `CAUGHT`; `SURVIVED`, `ERROR` or `NOT FOUND` means a test or the transcribed code is wrong. A mutant counts as caught only when pytest reports failed tests. Files are restored automatically.

```python
import glob
import pathlib
import subprocess
import sys

PY = sys.executable
STATS, PARSE, CHECKS, APPLY, ORCH, REPORT, CLI, SERVICE, FORM, APP = (
    "src/athena/metrics/stats.py",
    "src/athena/risk_overlay/parse.py",
    "src/athena/risk_overlay/checks.py",
    "src/athena/risk_overlay/apply.py",
    "src/athena/orchestrator/orchestrator.py",
    "src/athena/orchestrator/report.py",
    "src/athena/cli.py",
    "src/athena/dashboard/service.py",
    "src/athena/dashboard/risk_form.py",
    "src/athena/dashboard/app.py",
)
TESTS = [
    path
    for path in (
        "tests/test_metrics_stats.py", *sorted(glob.glob("tests/test_risk_overlay_*.py")), "tests/test_eval_schema.py",
        "tests/test_orchestrator.py", "tests/test_orchestrator_report.py", "tests/test_cli.py", "tests/test_dashboard_service.py",
        "tests/test_dashboard_app.py",
    )
    if pathlib.Path(path).exists()  # a task may run this before the later tests exist
]

MUTATIONS = [
    ("stats: value at risk reads the wrong tail", STATS, "max(0.0, float(-np.percentile(values, (1.0 - confidence) * 100.0)))", "max(0.0, float(-np.percentile(values, confidence * 100.0)))"),
    ("stats: shortfall is just the cutoff", STATS, "float(-np.mean(values[values <= cutoff]))", "float(-cutoff)"),
    ("stats: a gain is a negative loss", STATS, "max(0.0, float(-np.percentile(values, (1.0 - confidence) * 100.0)))", "float(-np.percentile(values, (1.0 - confidence) * 100.0))"),
    ("parse: warn may equal hard", PARSE, "    if warn >= hard:", "    if warn > hard:"),
    ("parse: a limit of zero is allowed", PARSE, "if not math.isfinite(number) or number <= 0:", "if not math.isfinite(number) or number < 0:"),
    ("parse: true counts as a number", PARSE, "if isinstance(value, bool) or not isinstance(value, (int, float)):", "if not isinstance(value, (int, float)):"),
    ("parse: the preset is ignored", PARSE, "limits = dict(PRESETS[preset].limits) if preset else {}", "limits = {}"),
    ("parse: null does not switch a check off", PARSE, "            limits.pop(measure, None)", "            pass"),
    ("parse: an unknown check is accepted", PARSE, "if not isinstance(measure, str) or measure not in MEASURES:", "if False:"),
    ("parse: unexpected keys are ignored", PARSE, "    if extra:\n        raise ProfileError(f\"unexpected key(s)", "    if False:\n        raise ProfileError(f\"unexpected key(s)"),
    ("parse: a repeated symbol replaces the first", PARSE, "totals[symbol] = totals.get(symbol, 0.0) + value", "totals[symbol] = value"),
    ("parse: symbols are not cleaned", PARSE, "symbol = normalize_input(row[0])", "symbol = row[0].strip()"),
    ("parse: the header is not checked", PARSE, "if tuple(header) != HOLDINGS_HEADER:", "if False:"),
    ("parse: a negative holding is allowed", PARSE, "if not math.isfinite(value) or value <= 0:", "if not math.isfinite(value):"),
    ("parse: holdings are not limited", PARSE, "if len(totals) > MAX_HOLDINGS:", "if False:"),
    ("parse: an amount of zero is allowed", PARSE, "(not math.isfinite(amount) or amount <= 0)", "(not math.isfinite(amount) or amount < 0)"),
    ("parse: holdings without a profile are allowed", PARSE, "if holdings is not None or amount is not None:", "if False:"),
    ("parse: the token ignores the amount", PARSE, '"amount": overlay.amount,', '"amount": None,'),
    ("parse: a huge number is not caught", PARSE, "    except OverflowError:\n        raise ProfileError(f\"{path}: is too large\") from None", "    except ZeroDivisionError:\n        raise ProfileError(f\"{path}: is too large\") from None"),
    ("checks: the window is longer", CHECKS, "WINDOW = stats.TRADING_DAYS + 1", "WINDOW = stats.TRADING_DAYS + 2"),
    ("checks: a fall counts as a negative drawdown", CHECKS, "lambda levels: -stats.max_drawdown(levels)", "lambda levels: stats.max_drawdown(levels)"),
    ("checks: traded value is a mean", CHECKS, "statistics.median(", "statistics.mean("),
    ("checks: traded value looks back too far", CHECKS, "LIQUIDITY_DAYS = 20", "LIQUIDITY_DAYS = 60"),
    ("checks: a warning needs to exceed its level", CHECKS, "WARN if value >= limit.warn", "WARN if value > limit.warn"),
    ("checks: a breach needs to exceed its limit", CHECKS, "BREACH if value >= limit.hard", "BREACH if value > limit.hard"),
    ("checks: a position forgets what is held", CHECKS, "held[symbol] = held.get(symbol, 0.0) + (overlay.amount or 0.0)", "held[symbol] = (overlay.amount or 0.0)"),
    ("checks: concentration is not squared", CHECKS, "sum(weight**2 for weight in weights.values())", "sum(weight for weight in weights.values())"),
    ("checks: liquidity is upside down", CHECKS, "overlay.amount / figures.traded_value if figures.traded_value else None", "figures.traded_value / overlay.amount if figures.traded_value else None"),
    ("checks: no amount is not noticed", CHECKS, "        elif not overlay.amount:", "        elif False:"),
    ("checks: no holdings is not noticed", CHECKS, "        elif not overlay.holdings:", "        elif False:"),
    ("checks: the largest holding is not named", CHECKS, '                detail = f"The largest holding would be', '                detail = "" and f"The largest holding would be'),
    ("checks: zero volume counts as traded", CHECKS, "    if traded > 0:", "    if traded >= 0:"),
    ("checks: the checks come in the wrong order", CHECKS, "    for check in MEASURES:\n        limit = overlay.profile.limits.get(check)", "    for check in reversed(MEASURES):\n        limit = overlay.profile.limits.get(check)"),
    ("checks: a missing figure looks fine", CHECKS, "return Finding(check, UNCHECKED, None,", "return Finding(check, OK, None,"),
    ("apply: a sale can be stopped", APPLY, 'CAPPABLE = ("Buy", "Overweight")', 'CAPPABLE = ("Buy", "Overweight", "Hold", "Underweight", "Sell")'),
    ("apply: an overweight is not held back", APPLY, 'CAPPABLE = ("Buy", "Overweight")', 'CAPPABLE = ("Buy",)'),
    ("apply: a warning holds a buy back", APPLY, "    if breaches and verdict", "    if (breaches or warnings) and verdict"),
    ("apply: the specialists call is lost", APPLY, '        out["pre_overlay_verdict"] = verdict["verdict"]', "        pass"),
    ("apply: warnings are not listed", APPLY, "[f.message for f in warnings]", "[]"),
    ("apply: the findings are not carried", APPLY, 'out["risk_findings"] = [f.to_dict() for f in findings]', 'out["risk_findings"] = []'),
    ("apply: warnings come before breaches", APPLY, "risks = [f.message for f in breaches] + [f.message for f in warnings]", "risks = [f.message for f in warnings] + [f.message for f in breaches]"),
    ("apply: the held-back line is not first", APPLY, "risks.insert(0, ", "risks.append("),
    ("apply: the specialists risks are dropped", APPLY, 'out["key_risks"] = risks + list(verdict["key_risks"])', 'out["key_risks"] = risks'),
    ("apply: the input verdict is changed", APPLY, "    out = dict(verdict)", "    out = verdict"),
    ("orchestrator: the overlay is not applied", ORCH, "verdict, note = self._apply_overlay(resolution, verdict, overlay)", 'note = "x"'),
    ("orchestrator: no price source is skipped quietly", ORCH, '            raise AthenaError("the risk overlay needs a price source")', '            return verdict, "skipped"'),
    ("orchestrator: a price failure stops the analysis", ORCH, '            return verdict, f"risk limits could not be checked: {exc}"', "            raise"),
    ("orchestrator: the wrong symbol is priced", ORCH, "bars = self._bars(resolution.identifier)", "bars = self._bars(resolution.name)"),
    ("orchestrator: the profile is not named in the notes", ORCH, "f\"risk profile '{overlay.profile.name}' applied: {UNCALIBRATED_NOTE}\"", '"applied"'),
    ("orchestrator: analyze drops the overlay", ORCH, "return self.analyze_resolved(resolved, query, overlay)", "return self.analyze_resolved(resolved, query)"),
    ("report: the held-back mark is missing", REPORT, 'if "pre_overlay_verdict" in verdict else ""', 'if False else ""'),
    ("report: the overlay block is missing", REPORT, '    if verdict.get("risk_findings"):', "    if False:"),
    ("report: a breach reads as ok", REPORT, '"breach": "BREACH"', '"breach": "ok"'),
    ("cli: the overlay is not passed on", CLI, "result = orchestrator.analyze(query) if overlay is None else orchestrator.analyze(query, overlay)", "result = orchestrator.analyze(query)"),
    ("service: the overlay is not passed on", SERVICE, "result = self._orchestrator.analyze(query) if overlay is None else self._orchestrator.analyze(query, overlay)", "result = self._orchestrator.analyze(query)"),
    ("page: the limits are on by default", FORM, 'if not st.checkbox("Apply my risk limits"', 'if st.checkbox("Apply my risk limits"'),
    ("page: every preset shows the moderate limits", FORM, "    base = PRESETS[preset].limits", '    base = PRESETS["moderate"].limits'),
    ("page: percentages are not scaled", FORM, "    return 100.0 if measure in SHOWN_AS_PERCENT else 1.0", "    return 1.0"),
    ("page: a switched-off check stays on", FORM, "            limits[measure] = None\n            continue", "            pass\n            continue"),
    ("page: an amount of zero is sent", FORM, "amount or None", "amount"),
    ("page: pasted holdings are ignored", FORM, "        return pasted, None", '        return "", None'),
    ("page: a problem is not shown", APP, "    if problem:\n        st.error(problem)\n        return", "    if False:\n        st.error(problem)\n        return"),
    ("page: the answer ignores the overlay when remembered", APP, 'f"{query}|{overlay_token(overlay)}"', "query"),
    ("page: a held-back verdict is not flagged", APP, '    if "pre_overlay_verdict" in verdict:\n        st.warning', '    if False:\n        st.warning'),
    ("page: the checks are not listed", APP, "    if not findings:\n        return", "    if True:\n        return"),
    ("page: a breach reads as ok in the table", APP, '"breach": "BREACH"', '"breach": "ok"'),
]

only = sys.argv[1:]
for name, path, old, new in MUTATIONS:
    if only and not any(name.startswith(o) for o in only):
        continue
    p = pathlib.Path(path)
    original = p.read_bytes()
    text = original.decode("utf-8").replace("\r\n", "\n")
    if old not in text:
        print("NOT FOUND", name, flush=True)
        continue
    p.write_bytes(text.replace(old, new, 1).encode("utf-8"))
    try:
        run = subprocess.run([PY, "-m", "pytest", *TESTS, "-q", "-x", "-p", "no:cacheprovider"], capture_output=True, text=True)
        tail = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else run.stderr[-200:]
        print(("CAUGHT  " if run.returncode == 1 and " failed" in tail else "ERROR   " if run.returncode else "SURVIVED"), name, "|", tail, flush=True)
    finally:
        p.write_bytes(original)
```

Expected: 19 lines, all `CAUGHT`.

- [ ] **Step 6: Commit**

```bash
git status --short
git add src/athena/risk_overlay src/athena/metrics/stats.py tests/test_risk_overlay_parse.py tests/test_metrics_stats.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add value at risk, a risk profile and a holdings parser" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 2: The checks and the override

**Files:**
- Create: `src/athena/risk_overlay/checks.py`, `src/athena/risk_overlay/apply.py`, `tests/test_risk_overlay_checks.py`
- Modify (by the script below): `src/athena/evaluation/schema.py`

**Interfaces:**
- Produces: `checks.Figures(volatility, drawdown, var_95, cvar_95, traded_value, reasons, window)`, `checks.figures_from_bars(bars) -> Figures` (the last 253 closes; a figure that cannot be computed is None with a reason), `checks.run_checks(figures, overlay, symbol) -> tuple[Finding, ...]` (one finding per switched-on measure in `MEASURES` order; at or above `warn` is warn, at or above `hard` is breach; a figure or input that is missing is `unchecked` with the reason); `apply.apply_overlay(verdict, findings) -> dict` (pure; breaches and warnings lead `key_risks`; a breach holds `Buy`/`Overweight` back to `Hold` and sets `pre_overlay_verdict`; always sets `risk_findings`), `apply.HELD_BACK`, `apply.UNCALIBRATED_NOTE`; the verdict contract accepts the optional `pre_overlay_verdict` and `risk_findings`.
- Consumes: Task 1's package; `athena.contracts.Bar`, `athena.metrics.stats`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_risk_overlay_checks.py`:

```python
import math
from dataclasses import replace

import pytest
from bar_factory import make_bars

from athena.evaluation.schema import validate_judge_verdict
from athena.risk_overlay.apply import HELD_BACK, apply_overlay
from athena.risk_overlay.checks import Figures, figures_from_bars, run_checks
from athena.risk_overlay.model import MEASURES, PRESETS, Finding, Holding, Limit, Overlay, RiskProfile


def alternating(count, up=1.01, down=0.99):
    closes, level = [100.0], 100.0
    for i in range(count):
        level *= up if i % 2 == 0 else down
        closes.append(level)
    return closes


def test_volatility_and_the_loss_figures_come_from_the_last_252_daily_returns():
    bars = make_bars([50.0] * 40 + alternating(400), volume=1000.0)
    figures = figures_from_bars(bars)
    expected = math.sqrt(252 * 0.01**2 / 251) * math.sqrt(252)
    assert figures.window == "last 252 daily returns"
    assert figures.volatility == pytest.approx(expected, rel=1e-6)
    assert figures.var_95 == pytest.approx(0.01, rel=1e-6) and figures.cvar_95 == pytest.approx(0.01, rel=1e-6)
    assert figures.reasons == {}


def test_the_drawdown_is_the_worst_fall_as_a_positive_size():
    closes = [100.0] * 10 + [120.0, 90.0] + [100.0] * 20
    figures = figures_from_bars(make_bars(closes))
    assert figures.drawdown == pytest.approx(0.25)


def test_a_history_too_short_for_volatility_says_so_but_still_gives_what_it_can():
    figures = figures_from_bars(make_bars([100.0, 90.0, 95.0, 99.0, 98.0]))
    assert figures.volatility is None and figures.var_95 is None and figures.cvar_95 is None
    assert "at least 20 observations" in figures.reasons["volatility"] and "var_95" in figures.reasons and "cvar_95" in figures.reasons
    assert figures.drawdown == pytest.approx(0.10)


def with_volume(bar, volume):
    return replace(bar, volume=volume)


def test_traded_value_is_the_median_of_the_last_twenty_days_so_one_huge_day_does_not_count():
    bars = make_bars([100.0] * 40)
    figures = figures_from_bars(bars[:-1] + [with_volume(bars[-1], 10**9)])
    assert figures.traded_value == pytest.approx(100.0 * 1000.0)


def test_traded_value_ignores_days_before_the_last_twenty():
    bars = make_bars([100.0] * 60, volume=1000.0)
    figures = figures_from_bars([with_volume(bar, 50000.0) for bar in bars[:30]] + bars[30:])
    assert figures.traded_value == pytest.approx(100.0 * 1000.0)


def test_no_volume_is_reported_not_treated_as_zero_risk():
    figures = figures_from_bars(make_bars([100.0 + i for i in range(40)], volume=0.0))
    assert figures.traded_value is None and figures.reasons["liquidity"] == "there is no volume data"
    assert figures_from_bars([]).reasons["liquidity"] == "there is no volume data"


def profile(**limits):
    return RiskProfile("t", {name: Limit(*pair) for name, pair in limits.items()})


def findings_for(figures, overlay, symbol="SBIN"):
    return {f.check: f for f in run_checks(figures, overlay, symbol)}


@pytest.mark.parametrize(
    "value, status",
    [(0.10, "ok"), (0.1999, "ok"), (0.20, "warn"), (0.25, "warn"), (0.30, "breach"), (0.90, "breach")],
)
def test_a_stock_figure_warns_at_its_warning_level_and_breaches_at_its_hard_limit(value, status):
    found = findings_for(Figures(volatility=value), Overlay(profile(volatility=(0.2, 0.3))))["volatility"]
    assert (found.status, found.value, found.warn, found.hard) == (status, value, 0.2, 0.3)


def test_each_stock_figure_is_checked_against_its_own_limit():
    figures = Figures(volatility=0.1, drawdown=0.5, var_95=0.01, cvar_95=0.09)
    overlay = Overlay(profile(volatility=(0.2, 0.3), drawdown=(0.2, 0.4), var_95=(0.02, 0.03), cvar_95=(0.03, 0.05)))
    got = {check: found.status for check, found in findings_for(figures, overlay).items()}
    assert got == {"volatility": "ok", "drawdown": "breach", "var_95": "ok", "cvar_95": "breach"}


def test_the_messages_say_what_was_measured_and_the_limit_in_plain_numbers():
    overlay = Overlay(profile(volatility=(0.2, 0.3), concentration=(0.2, 0.4)), (Holding("A", 900.0),), 100.0)
    found = findings_for(Figures(volatility=0.482), overlay)
    assert found["volatility"].message == "Volatility 48.2% is at or above your hard limit of 30.0%."
    assert "The largest holding would be A at 90.0%." in found["concentration"].message
    assert "Concentration (HHI) 0.820 is at or above your hard limit of 0.400." in found["concentration"].message
    ok = findings_for(Figures(volatility=0.1), Overlay(profile(volatility=(0.2, 0.3))))["volatility"]
    assert ok.message == "Volatility 10.0% is within your limits (warning at 20.0%)."
    warn = findings_for(Figures(volatility=0.25), Overlay(profile(volatility=(0.2, 0.3))))["volatility"]
    assert warn.message == "Volatility 25.0% is at or above your warning level of 20.0% (hard limit 30.0%)."


def test_a_figure_that_could_not_be_computed_is_unchecked_with_the_reason_never_ok():
    figures = Figures(reasons={"volatility": "volatility needs at least 20 observations, got 5"})
    found = findings_for(figures, Overlay(profile(volatility=(0.2, 0.3), drawdown=(0.2, 0.3))))
    assert found["volatility"].status == "unchecked" and found["volatility"].value is None
    assert found["volatility"].message == "Volatility not checked: volatility needs at least 20 observations, got 5."
    assert found["drawdown"].status == "unchecked"


def test_liquidity_compares_the_amount_with_the_typical_daily_traded_value():
    overlay = Overlay(profile(liquidity=(0.02, 0.05)), amount=20_000.0)
    figures = Figures(traded_value=1_000_000.0)
    assert findings_for(figures, overlay)["liquidity"].status == "warn"
    assert findings_for(figures, Overlay(overlay.profile, amount=50_000.0))["liquidity"].status == "breach"
    assert findings_for(figures, Overlay(overlay.profile, amount=19_999.0))["liquidity"].status == "ok"
    assert findings_for(figures, Overlay(overlay.profile, amount=20_000.0))["liquidity"].value == pytest.approx(0.02)


def test_the_checks_that_need_an_amount_say_so_when_there_is_none():
    overlay = Overlay(profile(liquidity=(0.02, 0.05), position=(0.1, 0.2), concentration=(0.2, 0.4)), (Holding("A", 100.0),))
    found = findings_for(Figures(traded_value=1e6), overlay)
    assert {f.status for f in found.values()} == {"unchecked"}
    assert all("no amount to invest was given" in f.message for f in found.values())
    zero = Overlay(overlay.profile, overlay.holdings, 0.0)
    assert {f.status for f in findings_for(Figures(traded_value=1e6), zero).values()} == {"unchecked"}


def test_liquidity_without_a_traded_value_is_unchecked_with_the_reason():
    found = findings_for(Figures(reasons={"liquidity": "there is no volume data"}), Overlay(profile(liquidity=(0.02, 0.05)), amount=1000.0))
    assert found["liquidity"].status == "unchecked" and "there is no volume data" in found["liquidity"].message


def test_position_is_the_weight_after_the_buy_and_adds_to_what_is_already_held():
    overlay = Overlay(profile(position=(0.10, 0.15)), (Holding("A", 900_000.0), Holding("SBIN", 50_000.0)), 50_000.0)
    found = findings_for(Figures(), overlay)["position"]
    assert found.value == pytest.approx(0.10) and found.status == "warn"
    bigger = Overlay(overlay.profile, overlay.holdings, 200_000.0)
    assert findings_for(Figures(), bigger)["position"].value == pytest.approx(250_000 / 1_150_000)


def test_a_stock_not_yet_owned_weighs_just_the_amount_against_the_portfolio():
    overlay = Overlay(profile(position=(0.10, 0.15), concentration=(0.5, 0.9)), (Holding("A", 600.0), Holding("B", 300.0)), 100.0)
    found = findings_for(Figures(), overlay)
    assert found["position"].value == pytest.approx(0.1)
    assert found["concentration"].value == pytest.approx(0.6**2 + 0.3**2 + 0.1**2)


def test_concentration_is_the_sum_of_squared_weights_after_the_buy():
    overlay = Overlay(profile(concentration=(0.15, 0.25)), (Holding("A", 500.0), Holding("B", 500.0)), 1000.0)
    found = findings_for(Figures(), overlay)["concentration"]
    assert found.value == pytest.approx(0.25**2 + 0.25**2 + 0.5**2) and found.status == "breach"


def test_without_holdings_position_and_concentration_are_unchecked_not_one_hundred_percent():
    found = findings_for(Figures(), Overlay(profile(position=(0.1, 0.2), concentration=(0.2, 0.4)), (), 1000.0))
    assert {f.status for f in found.values()} == {"unchecked"}
    assert all("no holdings were given" in f.message for f in found.values())


def test_only_the_checks_the_profile_switches_on_are_run_and_in_the_report_order():
    assert [f.check for f in run_checks(Figures(volatility=0.1), Overlay(profile(volatility=(0.2, 0.3))), "X")] == ["volatility"]
    every = Overlay(PRESETS["moderate"], (Holding("A", 1.0),), 1.0)
    assert [f.check for f in run_checks(Figures(), every, "X")] == list(MEASURES)
    assert run_checks(Figures(), Overlay(RiskProfile("empty")), "X") == ()


def finding(status, message="m", check="volatility"):
    return Finding(check, status, 0.5, 0.2, 0.3, message)


VERDICT = {"verdict": "Buy", "conviction": 70, "key_risks": ["a data gap"], "resolution_path": "blend"}


@pytest.mark.parametrize("verdict", ["Buy", "Overweight"])
def test_a_breach_holds_back_a_buy_or_an_overweight_and_keeps_the_specialists_call(verdict):
    out = apply_overlay({**VERDICT, "verdict": verdict}, [finding("breach", "too wild")])
    assert out["verdict"] == "Hold" and out["pre_overlay_verdict"] == verdict
    assert out["key_risks"] == [f"{HELD_BACK}: the specialists said {verdict}.", "too wild", "a data gap"]
    assert out["conviction"] == 70 and out["resolution_path"] == "blend"


@pytest.mark.parametrize("verdict", ["Hold", "Underweight", "Sell"])
def test_a_breach_never_changes_a_hold_or_a_sale_but_is_still_listed(verdict):
    out = apply_overlay({**VERDICT, "verdict": verdict}, [finding("breach", "too wild")])
    assert out["verdict"] == verdict and "pre_overlay_verdict" not in out
    assert out["key_risks"] == ["too wild", "a data gap"]


@pytest.mark.parametrize("status", ["ok", "warn", "unchecked"])
def test_only_a_breach_changes_a_verdict(status):
    out = apply_overlay(VERDICT, [finding(status)])
    assert out["verdict"] == "Buy" and "pre_overlay_verdict" not in out


def test_warnings_are_listed_after_breaches_and_before_the_specialists_own_risks_and_ok_checks_are_not():
    findings = [finding("warn", "w1"), finding("ok", "fine"), finding("breach", "b1"), finding("unchecked", "n/a"), finding("warn", "w2")]
    out = apply_overlay({**VERDICT, "verdict": "Hold"}, findings)
    assert out["key_risks"] == ["b1", "w1", "w2", "a data gap"]


def test_every_finding_travels_with_the_verdict_as_plain_data_and_the_input_is_not_changed():
    original = {**VERDICT, "key_risks": ["a data gap"]}
    findings = [finding("ok", "fine"), finding("breach", "b")]
    out = apply_overlay(original, findings)
    assert out["risk_findings"] == [f.to_dict() for f in findings]
    assert out["risk_findings"][1] == {"check": "volatility", "status": "breach", "value": 0.5, "warn": 0.2, "hard": 0.3, "message": "b"}
    assert original == {**VERDICT, "key_risks": ["a data gap"]} and "risk_findings" not in original


def test_a_verdict_after_the_overlay_still_passes_the_verdict_contract():
    assert validate_judge_verdict(apply_overlay(VERDICT, [finding("breach")])) == []
    assert validate_judge_verdict(apply_overlay(VERDICT, [])) == []
    bad = {**VERDICT, "pre_overlay_verdict": "Maybe", "risk_findings": ["not an object"]}
    errors = validate_judge_verdict(bad)
    assert any("pre_overlay_verdict" in e for e in errors) and any("risk_findings" in e for e in errors)
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_risk_overlay_checks.py -q`
Expected: a collection error `No module named 'athena.risk_overlay.checks'`.

- [ ] **Step 3: Write the implementation**

Create `src/athena/risk_overlay/checks.py`:

```python
from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from athena.contracts import Bar, InsufficientData
from athena.metrics import stats
from athena.risk_overlay.model import (
    BREACH, LABELS, MEASURES, OK, UNCHECKED, WARN, Finding, Limit, Overlay,
)

WINDOW = stats.TRADING_DAYS + 1  # 253 closes: 252 daily returns, the same window as the metrics packet
LIQUIDITY_DAYS = 20
MONEY_MEASURES = ("position", "concentration", "liquidity")


@dataclass(frozen=True)
class Figures:
    """What the stock's own price history says about its risk. A figure that cannot be computed is None, with the reason."""

    volatility: float | None = None
    drawdown: float | None = None  # a positive size
    var_95: float | None = None
    cvar_95: float | None = None
    traded_value: float | None = None  # median of close x volume over the last 20 bars, in rupees
    reasons: dict[str, str] = field(default_factory=dict)
    window: str = ""


def figures_from_bars(bars: Sequence[Bar]) -> Figures:
    closes = [bar.close for bar in bars][-WINDOW:]
    reasons: dict[str, str] = {}
    values: dict[str, float] = {}
    returns = [later / earlier - 1.0 for earlier, later in zip(closes, closes[1:])]
    for name, compute, data in (
        ("volatility", stats.annualized_volatility, returns),
        ("var_95", stats.value_at_risk, returns),
        ("cvar_95", stats.expected_shortfall, returns),
        ("drawdown", lambda levels: -stats.max_drawdown(levels), closes),
    ):
        try:
            values[name] = compute(data)
        except InsufficientData as exc:
            reasons[name] = str(exc)
    recent = [(bar.close, bar.volume) for bar in bars][-LIQUIDITY_DAYS:]
    traded = statistics.median(close * volume for close, volume in recent) if recent else 0.0
    if traded > 0:
        values["traded_value"] = traded
    else:
        reasons["liquidity"] = "there is no volume data"
    return Figures(window=f"last {len(returns)} daily returns", reasons=reasons, **values)


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _index(value: float) -> str:
    return f"{value:.3f}"


FORMAT = {"concentration": _index}


def _status(value: float, limit: Limit) -> str:
    return BREACH if value >= limit.hard else WARN if value >= limit.warn else OK


def _finding(check: str, value: float | None, limit: Limit, reason: str = "", detail: str = "") -> Finding:
    label, show = LABELS[check], FORMAT.get(check, _percent)
    if value is None:
        return Finding(check, UNCHECKED, None, limit.warn, limit.hard, f"{label} not checked: {reason}.")
    status = _status(value, limit)
    if status == BREACH:
        text = f"{label} {show(value)} is at or above your hard limit of {show(limit.hard)}."
    elif status == WARN:
        text = f"{label} {show(value)} is at or above your warning level of {show(limit.warn)} (hard limit {show(limit.hard)})."
    else:
        text = f"{label} {show(value)} is within your limits (warning at {show(limit.warn)})."
    return Finding(check, status, value, limit.warn, limit.hard, text + (f" {detail}" if detail else ""))


def _weights(overlay: Overlay, symbol: str) -> dict[str, float]:
    """Portfolio weights after the intended buy."""
    held: dict[str, float] = {}
    for holding in overlay.holdings:
        held[holding.symbol] = held.get(holding.symbol, 0.0) + holding.value
    held[symbol] = held.get(symbol, 0.0) + (overlay.amount or 0.0)
    total = sum(held.values())
    return {name: value / total for name, value in held.items()}


def run_checks(figures: Figures, overlay: Overlay, symbol: str) -> tuple[Finding, ...]:
    """One finding for every check the profile switches on, in the order of MEASURES."""
    findings: list[Finding] = []
    for check in MEASURES:
        limit = overlay.profile.limits.get(check)
        if limit is None:
            continue
        value: float | None
        reason = detail = ""
        if check in ("volatility", "drawdown", "var_95", "cvar_95"):
            value = getattr(figures, check)
            reason = figures.reasons.get(check, "")
        elif not overlay.amount:
            value, reason = None, "no amount to invest was given"
        elif check == "liquidity":
            value = overlay.amount / figures.traded_value if figures.traded_value else None
            reason = figures.reasons.get("liquidity", "")
        elif not overlay.holdings:
            value, reason = None, "no holdings were given"
        else:
            weights = _weights(overlay, symbol)
            if check == "position":
                value = weights[symbol]
            else:
                value = sum(weight**2 for weight in weights.values())
                largest = max(weights, key=lambda name: weights[name])
                detail = f"The largest holding would be {largest} at {_percent(weights[largest])}."
        findings.append(_finding(check, value, limit, reason, detail))
    return tuple(findings)
```

Create `src/athena/risk_overlay/apply.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from athena.risk_overlay.model import BREACH, WARN, Finding

CAPPABLE = ("Buy", "Overweight")  # a limit can stop a purchase, never a sale
HELD_BACK = "Held back by your risk limits"
UNCALIBRATED_NOTE = "the risk limits are starting points that are not calibrated to any data; VaR and CVaR describe about a year of the past"


def apply_overlay(verdict: dict[str, Any], findings: Sequence[Finding]) -> dict[str, Any]:
    """The verdict after the risk limits. Breaches and warnings always go to the front of the key risks; a breach also
    holds a Buy or Overweight back to Hold and keeps the specialists' own call in `pre_overlay_verdict`."""
    breaches = [f for f in findings if f.status == BREACH]
    warnings = [f for f in findings if f.status == WARN]
    out = dict(verdict)
    risks = [f.message for f in breaches] + [f.message for f in warnings]
    if breaches and verdict["verdict"] in CAPPABLE:
        out["pre_overlay_verdict"] = verdict["verdict"]
        out["verdict"] = "Hold"
        risks.insert(0, f"{HELD_BACK}: the specialists said {verdict['verdict']}.")
    out["key_risks"] = risks + list(verdict["key_risks"])
    out["risk_findings"] = [f.to_dict() for f in findings]
    return out
```

Save the script below as `$TEMP/s1i_b_schema.py` and run it from the repository root; it teaches the verdict contract the two optional keys and prints `schema edited`.

```python
import pathlib


def edit(path, pairs, append=""):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t + append, encoding="utf-8", newline="\n")


# ---- the verdict contract learns the overlay's two optional keys
edit(
    "src/athena/evaluation/schema.py",
    [
        (
            '_VERDICT_KEYS = {"verdict", "conviction", "key_risks", "resolution_path", "debate_transcript_ref", "panel_agreement", "contested"}',
            '_VERDICT_KEYS = {\n    "verdict", "conviction", "key_risks", "resolution_path", "debate_transcript_ref", "panel_agreement", "contested",\n    "pre_overlay_verdict", "risk_findings",\n}',
        ),
        (
            '    if "contested" in verdict and not isinstance(verdict["contested"], bool):\n        errors.append("contested must be a boolean")\n',
            '    if "contested" in verdict and not isinstance(verdict["contested"], bool):\n        errors.append("contested must be a boolean")\n'
            '    if "pre_overlay_verdict" in verdict and verdict["pre_overlay_verdict"] not in VERDICTS:\n'
            '        errors.append(f"pre_overlay_verdict must be one of {list(VERDICTS)}, got {verdict[\'pre_overlay_verdict\']!r}")\n'
            '    if "risk_findings" in verdict and not (\n'
            '        isinstance(verdict["risk_findings"], list) and all(isinstance(item, dict) for item in verdict["risk_findings"])\n'
            '    ):\n'
            '        errors.append("risk_findings must be a list of objects")\n',
        ),
    ],
)

print("schema edited")
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_risk_overlay_checks.py -q` (expect `34 passed`), `.venv/Scripts/python -m pyflakes src tests` (prints nothing), then the whole suite (expect `820 passed, 65 skipped`).

- [ ] **Step 5: Mutation check**

Run: `.venv/Scripts/python $TEMP/mutate_1i.py checks apply` from the repository root.
Expected: 25 lines, all `CAUGHT`.

- [ ] **Step 6: Commit**

```bash
git status --short
git add src/athena/risk_overlay src/athena/evaluation/schema.py tests/test_risk_overlay_checks.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: check a stock against a risk profile and hold a purchase back on a breach" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 3: The orchestrator, the report, the command line and the service use the overlay

**Files:**
- Modify (by the scripts below): `src/athena/orchestrator/orchestrator.py`, `src/athena/orchestrator/report.py`, `src/athena/cli.py`, `src/athena/dashboard/service.py`, `tests/test_orchestrator.py`, `tests/test_orchestrator_report.py`, `tests/test_cli.py`, `tests/test_dashboard_service.py`

**Interfaces:**
- Produces: `Orchestrator(resolver, specialists, packet_builders, conflict_min_confidence=..., bars=None)` with `analyze(query, overlay=None)` and `analyze_resolved(resolution, query=None, overlay=None)`; without an overlay the note `RISK_OVERLAY_NOTE` is added as before (reworded); with one, the verdict carries `risk_findings` (and `pre_overlay_verdict` when held back) and a note `risk profile '<name>' applied: ...`; no price source with an overlay raises `AthenaError`; a price failure leaves the verdict and adds the note `risk limits could not be checked: ...`; `build_orchestrator` passes its price source; the report prints `[held back from X by your risk limits]` and a `Risk overlay:` block (`ok`, `WARN`, `BREACH`, `not checked`); the command line takes `--profile NAME|FILE`, `--holdings FILE.csv`, `--amount RUPEES` (an error line, exit 1, for anything unusable); `DashboardService.view(query, overlay=None)`.
- Consumes: Tasks 1 and 2.

- [ ] **Step 1: Write the failing tests**

Save the script below as `$TEMP/s1i_tests_wiring.py` and run it from the repository root. It adds tests to `tests/test_orchestrator.py`, `tests/test_orchestrator_report.py`, `tests/test_cli.py` and `tests/test_dashboard_service.py` (it prints `wiring tests added`).

```python
import pathlib


def edit(path, pairs, append=""):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t + append, encoding="utf-8", newline="\n")


# ---- the orchestrator applies an overlay
edit(
    "tests/test_orchestrator.py",
    [
        (
            "import pytest\n\nfrom athena.agents.base import SpecialistError\n",
            "import pytest\nfrom bar_factory import make_bars\n\nfrom athena.agents.base import SpecialistError\n",
        ),
        (
            "from athena.resolver import Ambiguity, Candidate, Resolution\n",
            "from athena.resolver import Ambiguity, Candidate, Resolution\nfrom athena.risk_overlay.model import Holding, Limit, Overlay, RiskProfile\n",
        ),
        ("    OK,\n    Orchestrator,\n)", "    OK,\n    RISK_OVERLAY_NOTE,\n    Orchestrator,\n)"),
    ],
    append='''

def wild_bars():
    closes, level = [], 100.0
    for i in range(300):
        level *= 1.05 if i % 2 == 0 else 0.95
        closes.append(level)
    return make_bars(closes, volume=1000.0, symbol="SBIN")


class Prices:
    def __init__(self, bars=None, error=None):
        self.bars, self.error, self.symbols = bars if bars is not None else wild_bars(), error, []

    def __call__(self, symbol):
        self.symbols.append(symbol)
        if self.error:
            raise self.error
        return self.bars


CAUTIOUS = Overlay(RiskProfile("cautious", {"volatility": Limit(0.3, 0.6)}))


def with_prices(prices, specialists=None):
    specialists = specialists or {"quant_technical": FakeSpecialist(out("bullish", 80))}
    builders = {name: (lambda res: {}) for name in specialists}
    return Orchestrator(FakeResolver(resolution()), specialists, builders, bars=prices)


def test_a_breached_limit_holds_a_buy_back_to_hold_and_keeps_the_specialists_call():
    prices = Prices()
    result = with_prices(prices).analyze("sbin", CAUTIOUS)
    assert result.verdict["verdict"] == "Hold" and result.verdict["pre_overlay_verdict"] == "Buy"
    assert [f["status"] for f in result.verdict["risk_findings"]] == ["breach"]
    assert result.verdict["key_risks"][0].startswith("Held back by your risk limits")
    assert validate_judge_verdict(result.verdict) == [] and prices.symbols == ["SBIN"]
    assert "risk profile 'cautious' applied" in " ".join(result.notes) and RISK_OVERLAY_NOTE not in result.notes
    assert result.specialists["quant_technical"]["signal"] == "bullish"  # the specialists are untouched


def test_without_an_overlay_the_verdict_has_no_overlay_keys_and_the_note_says_no_limits_were_applied():
    prices = Prices()
    result = with_prices(prices).analyze("sbin")
    assert result.verdict["verdict"] == "Buy" and set(result.verdict) == {"verdict", "conviction", "key_risks", "resolution_path"}
    assert RISK_OVERLAY_NOTE in result.notes and prices.symbols == []


def test_the_overlay_also_applies_when_the_instrument_is_already_resolved():
    result = with_prices(Prices()).analyze_resolved(resolution(), None, CAUTIOUS)
    assert result.verdict["verdict"] == "Hold" and result.verdict["pre_overlay_verdict"] == "Buy"


def test_a_calm_stock_within_its_limits_keeps_its_verdict_and_still_shows_every_check():
    calm = Prices(make_bars([100.0 + 0.1 * (i % 3) for i in range(300)], symbol="SBIN"))
    result = with_prices(calm).analyze("sbin", CAUTIOUS)
    assert result.verdict["verdict"] == "Buy" and "pre_overlay_verdict" not in result.verdict
    assert [f["status"] for f in result.verdict["risk_findings"]] == ["ok"]


def test_the_checks_that_need_holdings_and_an_amount_use_them():
    overlay = Overlay(RiskProfile("p", {"position": Limit(0.05, 0.10)}), (Holding("INFY", 90_000.0),), 30_000.0)
    result = with_prices(Prices()).analyze("sbin", overlay)
    (finding,) = result.verdict["risk_findings"]
    assert finding["status"] == "breach" and finding["value"] == 0.25 and result.verdict["verdict"] == "Hold"


def test_when_prices_cannot_be_fetched_the_verdict_stands_and_the_note_says_the_limits_were_not_checked():
    result = with_prices(Prices(error=AllSourcesFailed("no prices"))).analyze("sbin", CAUTIOUS)
    assert result.verdict["verdict"] == "Buy" and "risk_findings" not in result.verdict
    assert any(note.startswith("risk limits could not be checked") for note in result.notes)


def test_asking_for_an_overlay_with_no_price_source_is_an_error_not_a_silent_skip():
    orchestrator = Orchestrator(FakeResolver(resolution()), {"quant_technical": FakeSpecialist(out())}, {"quant_technical": lambda res: {}})
    with pytest.raises(AthenaError, match="needs a price source"):
        orchestrator.analyze("sbin", CAUTIOUS)


def test_an_ambiguous_query_asks_which_instrument_before_any_limit_is_checked():
    prices = Prices()
    ambiguity = Ambiguity("sbi", (Candidate("equity", "SBIN", "State Bank of India", 0.8),), "several")
    orchestrator = Orchestrator(FakeResolver(ambiguity), {}, {}, bars=prices)
    assert orchestrator.analyze("sbi", CAUTIOUS).status == NEEDS_CLARIFICATION and prices.symbols == []


def test_with_no_specialist_view_the_hold_stands_and_the_findings_are_still_listed():
    result = with_prices(Prices(), {"quant_technical": FakeSpecialist(out("neutral", 0, "insufficient", ["x"]))}).analyze("sbin", CAUTIOUS)
    assert result.status == NO_VIEW and result.verdict["verdict"] == "Hold" and "pre_overlay_verdict" not in result.verdict
    assert result.verdict["risk_findings"][0]["status"] == "breach"
''',
)
edit(
    "tests/test_orchestrator.py",
    [("from athena.contracts import AllSourcesFailed, InsufficientData\n", "from athena.contracts import AllSourcesFailed, AthenaError, InsufficientData\n")],
)

# ---- the report
edit(
    "tests/test_orchestrator_report.py",
    [],
    append='''

def test_report_lists_the_risk_overlay_and_says_when_a_verdict_was_held_back():
    findings = [
        {"check": "volatility", "status": "breach", "value": 0.8, "warn": 0.3, "hard": 0.6, "message": "Volatility 80.0% is at or above your hard limit of 60.0%."},
        {"check": "position", "status": "unchecked", "value": None, "warn": 0.1, "hard": 0.2, "message": "Position size not checked: no amount to invest was given."},
        {"check": "drawdown", "status": "ok", "value": 0.1, "warn": 0.3, "hard": 0.4, "message": "Worst drawdown 10.0% is within your limits (warning at 30.0%)."},
        {"check": "var_95", "status": "warn", "value": 0.04, "warn": 0.03, "hard": 0.06, "message": "1-day VaR (95%) 4.0% is at or above your warning level of 3.0% (hard limit 6.0%)."},
    ]
    verdict = {
        "verdict": "Hold", "conviction": 35, "key_risks": ["a risk"], "resolution_path": "blend",
        "pre_overlay_verdict": "Buy", "risk_findings": findings,
    }
    text = format_result(result(verdict=verdict))
    assert "Verdict: Hold  conviction 35  (blend)  [held back from Buy by your risk limits]" in text
    assert "Risk overlay:" in text
    for expected in (
        "  [BREACH] Volatility 80.0% is at or above your hard limit of 60.0%.",
        "  [not checked] Position size not checked: no amount to invest was given.",
        "  [ok] Worst drawdown 10.0% is within your limits (warning at 30.0%).",
        "  [WARN] 1-day VaR (95%) 4.0% is at or above your warning level of 3.0% (hard limit 6.0%).",
    ):
        assert expected in text, expected
    assert text.index("Risk overlay:") < text.index("Key risks:")


def test_report_without_an_overlay_has_no_overlay_block_or_held_back_mark():
    text = format_result(result())
    assert "Risk overlay:" not in text and "held back" not in text
''',
)

# ---- the command line
edit(
    "tests/test_cli.py",
    [],
    append='''

class OverlayStub:
    def __init__(self, result):
        self.result, self.calls = result, []

    def analyze(self, query, overlay=None):
        self.calls.append((query, overlay))
        return self.result


def test_main_without_a_profile_calls_analyze_with_just_the_query(capsys):
    stub = StubOrchestrator(ok_result())
    assert main(["sbin"], factory=lambda env_file: stub) == 0 and stub.queries == ["sbin"]


def test_main_passes_a_profile_holdings_and_an_amount_on(capsys, tmp_path):
    path = tmp_path / "h.csv"
    path.write_text("symbol,value\\nSBIN,1000\\n", encoding="utf-8")
    stub = OverlayStub(ok_result())
    argv = ["sbin", "--profile", "conservative", "--holdings", str(path), "--amount", "2500"]
    assert main(argv, factory=lambda env_file: stub) == 0
    ((query, overlay),) = stub.calls
    assert query == "sbin" and overlay.profile.name == "conservative" and overlay.amount == 2500.0
    assert [(h.symbol, h.value) for h in overlay.holdings] == [("SBIN", 1000.0)]


def test_main_reports_a_bad_profile_holdings_or_amount_without_a_traceback(capsys, tmp_path):
    stub = OverlayStub(ok_result())
    assert main(["sbin", "--holdings", "h.csv"], factory=lambda env_file: stub) == 1
    assert "error: --holdings and --amount need --profile" in capsys.readouterr().out
    assert main(["sbin", "--profile", "reckless"], factory=lambda env_file: stub) == 1
    assert "is not a preset" in capsys.readouterr().out
    assert main(["sbin", "--profile", "moderate", "--holdings", str(tmp_path / "none.csv")], factory=lambda env_file: stub) == 1
    assert "holdings: cannot read" in capsys.readouterr().out
    assert main(["sbin", "--profile", "moderate", "--amount", "-5"], factory=lambda env_file: stub) == 1
    assert "--amount must be a number above zero" in capsys.readouterr().out
    assert stub.calls == []


def test_offline_end_to_end_a_purchase_too_big_for_the_traded_value_is_held_back_and_printed(capsys):
    orchestrator = build_orchestrator(make_resolver(), FakeLLMRouter(), FakeChain(), clock=lambda: NOW)
    assert main(["sbin", "--profile", "moderate", "--amount", "5000000"], factory=lambda env_file: orchestrator) == 0
    out = capsys.readouterr().out
    assert "Verdict: Hold" in out and "[held back from Buy by your risk limits]" in out
    assert "[BREACH] Amount against daily traded value" in out and "[not checked] Position size not checked: no holdings were given." in out
    assert "risk profile 'moderate' applied" in out
''',
)

# ---- the dashboard service
edit(
    "tests/test_dashboard_service.py",
    [
        (
            "    def __init__(self, cache, result=None):\n        self.cache, self.result, self.queries = cache, result, []\n\n    def analyze(self, query):\n        self.queries.append(query)\n",
            "    def __init__(self, cache, result=None):\n        self.cache, self.result, self.queries, self.overlays = cache, result, [], []\n\n    def analyze(self, query, overlay=None):\n        self.queries.append(query)\n        self.overlays.append(overlay)\n",
        ),
    ],
    append='''

def test_a_view_passes_the_risk_overlay_to_the_orchestrator_only_when_there_is_one():
    from athena.risk_overlay.model import PRESETS, Overlay

    service, _, orchestrator = make_service()
    service.view("sbin")
    overlay = Overlay(PRESETS["moderate"], (), 5000.0)
    service.view("sbin", overlay)
    assert orchestrator.overlays == [None, overlay]
''',
)
print("wiring tests added")
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_orchestrator.py tests/test_orchestrator_report.py tests/test_cli.py tests/test_dashboard_service.py -q`
Expected: failures and errors (`unexpected keyword argument 'bars'`, `unrecognized arguments: --profile`).

- [ ] **Step 3: Apply the source edits**

Save the script below as `$TEMP/s1i_c_wiring.py` and run it from the repository root; it edits the four source files and prints `orchestrator, report, command line and service edited`. An `AssertionError` means a file differs from what the script expects: stop and report.

```python
import pathlib


def edit(path, pairs, append=""):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t + append, encoding="utf-8", newline="\n")


# ---- the orchestrator applies a risk overlay when it is given one
edit(
    "src/athena/orchestrator/orchestrator.py",
    [
        ("from collections.abc import Callable, Mapping\n", "from collections.abc import Callable, Mapping, Sequence\n"),
        ("from athena.contracts import AthenaError\n", "from athena.contracts import AthenaError, Bar\n"),
        (
            "from athena.resolver import Ambiguity, InstrumentResolver, Resolution\n",
            "from athena.resolver import Ambiguity, InstrumentResolver, Resolution\nfrom athena.risk_overlay.apply import UNCALIBRATED_NOTE, apply_overlay\n"
            "from athena.risk_overlay.checks import figures_from_bars, run_checks\nfrom athena.risk_overlay.model import Overlay\n",
        ),
        (
            'RISK_OVERLAY_NOTE = "the risk overlay (TRD 2.7) is not built, so no position or concentration limits were applied"',
            'RISK_OVERLAY_NOTE = "no risk profile was given, so the risk overlay applied no limits (--profile on the command line, or switch on the risk profile in the dashboard)"',
        ),
        (
            "        conflict_min_confidence: int = CONFLICT_MIN_CONFIDENCE,\n    ):\n        self._resolver = resolver\n",
            "        conflict_min_confidence: int = CONFLICT_MIN_CONFIDENCE,\n        bars: Callable[[str], Sequence[Bar]] | None = None,\n    ):\n        self._resolver = resolver\n        self._bars = bars  # the price history the risk overlay measures; shared with whoever else reads it\n",
        ),
        (
            "    def analyze(self, query: str) -> OrchestrationResult:",
            "    def analyze(self, query: str, overlay: Overlay | None = None) -> OrchestrationResult:",
        ),
        (
            "        return self.analyze_resolved(resolved, query)\n\n    def analyze_resolved(self, resolution: Resolution, query: str | None = None) -> OrchestrationResult:",
            "        return self.analyze_resolved(resolved, query, overlay)\n\n"
            "    def analyze_resolved(\n        self, resolution: Resolution, query: str | None = None, overlay: Overlay | None = None\n    ) -> OrchestrationResult:",
        ),
        (
            "        notes.append(RISK_OVERLAY_NOTE)\n",
            "        verdict = blended.verdict\n        if overlay is None:\n            notes.append(RISK_OVERLAY_NOTE)\n        else:\n            verdict, note = self._apply_overlay(resolution, verdict, overlay)\n            notes.append(note)\n",
        ),
        ("            verdict=blended.verdict,\n            notes=tuple(notes),\n        )\n",
         "            verdict=verdict,\n            notes=tuple(notes),\n        )\n\n"
         "    def _apply_overlay(\n        self, resolution: Resolution, verdict: dict[str, Any], overlay: Overlay\n    ) -> tuple[dict[str, Any], str]:\n"
         "        \"\"\"The verdict after the person's risk limits, and the note that says so. Without prices the verdict stands and the\n        note says the limits could not be checked.\"\"\"\n"
         "        if self._bars is None:\n            raise AthenaError(\"the risk overlay needs a price source\")\n"
         "        try:\n            bars = self._bars(resolution.identifier)\n        except AthenaError as exc:\n"
         "            return verdict, f\"risk limits could not be checked: {exc}\"\n"
         "        findings = run_checks(figures_from_bars(bars), overlay, resolution.identifier)\n"
         "        return apply_overlay(verdict, findings), f\"risk profile '{overlay.profile.name}' applied: {UNCALIBRATED_NOTE}\"\n"),
    ],
)

# ---- the report says what the overlay did
edit(
    "src/athena/orchestrator/report.py",
    [
        (
            '        f"Verdict: {verdict[\'verdict\']}  conviction {verdict[\'conviction\']}  ({verdict[\'resolution_path\']})"\n'
            '        + ("  [no specialist had enough data]" if result.status == NO_VIEW else ""),\n',
            '        f"Verdict: {verdict[\'verdict\']}  conviction {verdict[\'conviction\']}  ({verdict[\'resolution_path\']})"\n'
            '        + (f"  [held back from {verdict[\'pre_overlay_verdict\']} by your risk limits]" if "pre_overlay_verdict" in verdict else "")\n'
            '        + ("  [no specialist had enough data]" if result.status == NO_VIEW else ""),\n',
        ),
        (
            '    if verdict["key_risks"]:\n        lines.append("Key risks:")',
            '    if verdict.get("risk_findings"):\n        lines.append("Risk overlay:")\n'
            '        lines += [f"  [{STATUS_WORDS[item[\'status\']]}] {item[\'message\']}" for item in verdict["risk_findings"]]\n'
            '    if verdict["key_risks"]:\n        lines.append("Key risks:")',
        ),
        (
            'DISCLAIMER = "A stylized analytical framework, not financial advice; not a registered investment adviser."\n',
            'DISCLAIMER = "A stylized analytical framework, not financial advice; not a registered investment adviser."\n'
            'STATUS_WORDS = {"ok": "ok", "warn": "WARN", "breach": "BREACH", "unchecked": "not checked"}\n',
        ),
    ],
)

# ---- the command line takes a profile, holdings and an amount
edit(
    "src/athena/cli.py",
    [
        (
            "from athena.resolver import InstrumentIndex, InstrumentResolver, Resolution\n",
            "from athena.resolver import InstrumentIndex, InstrumentResolver, Resolution\nfrom athena.risk_overlay.parse import build_overlay\n",
        ),
        (
            "    return Orchestrator(resolver, specialists, builders)\n",
            "    return Orchestrator(resolver, specialists, builders, bars=fetch)\n",
        ),
        (
            '    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))\n    args = parser.parse_args(argv)\n    try:\n        result = factory(env_file=args.env_file).analyze(" ".join(args.query))\n',
            '    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))\n'
            '    parser.add_argument("--profile", help="your risk limits: conservative, moderate, aggressive, or a JSON file")\n'
            '    parser.add_argument("--holdings", metavar="FILE.csv", help="what you own now: a CSV with the columns symbol,value (rupees)")\n'
            '    parser.add_argument("--amount", type=float, help="rupees you are thinking of putting into this instrument")\n'
            "    args = parser.parse_args(argv)\n    try:\n"
            "        overlay = build_overlay(args.profile, args.holdings, args.amount)\n"
            "        orchestrator = factory(env_file=args.env_file)\n"
            '        query = " ".join(args.query)\n'
            "        result = orchestrator.analyze(query) if overlay is None else orchestrator.analyze(query, overlay)\n",
        ),
    ],
)

# ---- the dashboard service passes the overlay on
edit(
    "src/athena/dashboard/service.py",
    [
        ("from athena.resolver import Resolution\n", "from athena.resolver import Resolution\nfrom athena.risk_overlay.model import Overlay\n"),
        (
            "    def view(self, query: str) -> DashboardView:\n        self._bars.clear()",
            "    def view(self, query: str, overlay: Overlay | None = None) -> DashboardView:\n        self._bars.clear()",
        ),
        (
            "        result = self._orchestrator.analyze(query)\n        if result.status == NEEDS_CLARIFICATION:",
            "        result = self._orchestrator.analyze(query) if overlay is None else self._orchestrator.analyze(query, overlay)\n        if result.status == NEEDS_CLARIFICATION:",
        ),
    ],
)
print("orchestrator, report, command line and service edited")
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest -q` (expect `836 passed, 65 skipped`) and `.venv/Scripts/python -m pyflakes src tests` (prints nothing).

- [ ] **Step 5: Mutation check**

Run: `.venv/Scripts/python $TEMP/mutate_1i.py orchestrator report cli service` from the repository root.
Expected: 11 lines, all `CAUGHT`.

- [ ] **Step 6: Commit**

```bash
git status --short
git add -A src tests
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: apply the risk overlay in the orchestrator, the report, the command line and the service" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 4: The risk profile sidebar and the Risk overlay table on the dashboard

**Files:**
- Create: `src/athena/dashboard/risk_form.py`
- Modify (by the scripts below): `src/athena/dashboard/app.py`, `tests/dash_fakes.py`, `tests/test_dashboard_app.py`

**Interfaces:**
- Produces: `risk_form.risk_controls() -> (Overlay | None, str | None)` (None while "Apply my risk limits" is off; the second value is a message when the limits, the holdings or an uploaded file cannot be used); widget keys `risk_on`, `risk_preset`, `risk-<preset>-<measure>-on|warn|hard`, `risk_holdings`, `risk_upload`, `risk_amount`; the page asks `service.view(query, overlay)`, keys its remembered answer by the query and `overlay_token(overlay)`, shows a message and analyses nothing while the entries are unusable, warns "Held back from X by your risk limits" and lists the findings in a "Risk overlay" table (check, status, detail).
- Consumes: Tasks 1 to 3.

- [ ] **Step 1: Update and add the tests**

Save the script below as `$TEMP/s1i_tests_page.py` and run it from the repository root; it appends the page tests (it prints `risk page tests added`).

```python
import pathlib

p = pathlib.Path("tests/test_dashboard_app.py")
t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
t += '''

from athena.risk_overlay.model import PRESETS


def risk_on(app):
    app.sidebar.checkbox(key="risk_on").check().run()


FINDINGS = [
    {"check": "volatility", "status": "breach", "value": 0.8, "warn": 0.3, "hard": 0.6, "message": "Volatility 80.0% is at or above your hard limit of 60.0%."},
    {"check": "position", "status": "unchecked", "value": None, "warn": 0.1, "hard": 0.2, "message": "Position size not checked: no amount to invest was given."},
]


def test_the_risk_profile_is_off_until_it_is_switched_on_and_nothing_extra_is_asked_of_the_service():
    service = FakeService(full_view())
    app = open_app(service, "sbin")
    assert not app.exception and service.overlays == [None]
    assert app.sidebar.checkbox(key="risk_on").value is False and len(app.sidebar.number_input) == 0


def test_switching_it_on_applies_the_moderate_preset_untouched_with_no_holdings_and_no_amount():
    service = FakeService(full_view())
    app = open_app(service, "sbin")
    risk_on(app)
    overlay = service.overlays[-1]
    assert not app.exception and overlay.profile == PRESETS["moderate"] and overlay.holdings == () and overlay.amount is None


def test_choosing_another_starting_point_changes_every_limit_to_that_presets():
    service = FakeService(full_view())
    app = open_app(service, "sbin")
    risk_on(app)
    app.sidebar.selectbox(key="risk_preset").select("conservative").run()
    assert not app.exception and service.overlays[-1].profile == PRESETS["conservative"]
    assert app.sidebar.number_input(key="risk-conservative-volatility-hard").value == 35.0


def test_a_limit_can_be_edited_and_a_check_switched_off():
    service = FakeService(full_view())
    app = open_app(service, "sbin")
    risk_on(app)
    app.sidebar.number_input(key="risk-moderate-volatility-hard").set_value(55.0).run()
    app.sidebar.checkbox(key="risk-moderate-liquidity-on").uncheck().run()
    limits = service.overlays[-1].profile.limits
    assert limits["volatility"].hard == 0.55 and limits["volatility"].warn == 0.35 and "liquidity" not in limits
    assert limits["concentration"] == PRESETS["moderate"].limits["concentration"]


def test_holdings_and_the_amount_reach_the_service_and_change_what_is_asked():
    service = FakeService(full_view())
    app = open_app(service, "sbin")
    risk_on(app)
    asked = len(service.overlays)
    app.sidebar.text_area(key="risk_holdings").set_value("symbol,value\\nINFY,90000\\nsbin,10000").run()
    app.sidebar.number_input(key="risk_amount").set_value(25000.0).run()
    overlay = service.overlays[-1]
    assert [(h.symbol, h.value) for h in overlay.holdings] == [("INFY", 90000.0), ("SBIN", 10000.0)] and overlay.amount == 25000.0
    assert len(service.overlays) == asked + 2 and not app.exception


def test_a_bad_holdings_list_or_limit_is_explained_and_nothing_is_analysed_until_it_is_fixed():
    service = FakeService(full_view())
    app = open_app(service, "sbin")
    risk_on(app)
    asked = len(service.overlays)
    app.sidebar.text_area(key="risk_holdings").set_value("symbol,value\\nSBIN,lots").run()
    assert not app.exception and "holdings row 2: 'lots' is not a number" in app.error[0].value
    app.sidebar.text_area(key="risk_holdings").set_value("").run()
    app.sidebar.number_input(key="risk-moderate-volatility-warn").set_value(60.0).run()
    assert "limits.volatility: 'warn' must be below 'hard'" in app.error[0].value
    assert len(service.overlays) == asked  # nothing was analysed with a profile that could not be used
    app.sidebar.number_input(key="risk-moderate-volatility-warn").set_value(35.0).run()
    assert not app.error and app.metric[0].value == "Buy"  # the same request as before, so the page reuses its answer
    assert len(service.overlays) == asked


def test_an_unrelated_click_does_not_ask_the_service_again_but_a_new_amount_does():
    service = FakeService(full_view())
    app = open_app(service, "sbin")
    risk_on(app)
    asked = len(service.overlays)
    app.checkbox(key="show_volume").uncheck().run()
    assert len(service.overlays) == asked
    app.sidebar.number_input(key="risk_amount").set_value(1000.0).run()
    assert len(service.overlays) == asked + 1


def test_a_held_back_verdict_is_flagged_and_every_check_is_listed_with_its_status():
    verdict = {
        "verdict": "Hold", "conviction": 60, "key_risks": ["Held back by your risk limits: the specialists said Buy."],
        "resolution_path": "blend", "pre_overlay_verdict": "Buy", "risk_findings": FINDINGS,
    }
    app = open_app(FakeService(replace(full_view(), verdict=verdict)), "sbin")
    assert not app.exception
    assert any("Held back from Buy by your risk limits" in w.value for w in app.warning)
    table = next(frame for frame in app.dataframe if "check" in frame.value.columns).value
    assert list(table["check"]) == ["Volatility", "Position size"] and list(table["status"]) == ["BREACH", "not checked"]
    assert "Volatility 80.0%" in table["detail"][0] and app.metric[0].value == "Hold"


def test_a_verdict_without_findings_shows_no_risk_overlay_and_no_held_back_warning():
    app = open_app(FakeService(full_view()), "sbin")
    assert not any("Held back" in w.value for w in app.warning)
    assert not any("check" in frame.value.columns for frame in app.dataframe)
'''
p.write_text(t, encoding="utf-8", newline="\n")
print("risk page tests added")
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_app.py -q`
Expected: failures (the sidebar has no `risk_on` checkbox).

- [ ] **Step 3: Write the implementation**

Create `src/athena/dashboard/risk_form.py`:

```python
from __future__ import annotations

import streamlit as st

from athena.risk_overlay.model import LABELS, MEASURES, PRESETS, Overlay
from athena.risk_overlay.parse import ProfileError, parse_holdings, parse_profile

RISK_ON = "risk_on"
PRESET_KEY = "risk_preset"
HOLDINGS_KEY = "risk_holdings"
UPLOAD_KEY = "risk_upload"
AMOUNT_KEY = "risk_amount"
SHOWN_AS_PERCENT = tuple(measure for measure in MEASURES if measure != "concentration")  # the index is shown as a number
HOLDINGS_HELP = "One row per holding, with the header: symbol,value (what you hold now, in rupees)."


def _scale(measure: str) -> float:
    return 100.0 if measure in SHOWN_AS_PERCENT else 1.0


def _limit_boxes(preset: str) -> dict:
    """The limits as the person has edited them, as the `limits` part of a profile."""
    base = PRESETS[preset].limits
    limits: dict = {}
    for measure in MEASURES:
        scale, unit = _scale(measure), "%" if measure in SHOWN_AS_PERCENT else ""
        key = f"risk-{preset}-{measure}"
        if not st.checkbox(LABELS[measure], value=True, key=f"{key}-on"):
            limits[measure] = None
            continue
        warn_box, hard_box = st.columns(2)
        step = 1.0 if scale == 100.0 else 0.05
        warn = warn_box.number_input(f"Warn at {unit}".strip(), min_value=0.0, value=round(base[measure].warn * scale, 6), step=step, key=f"{key}-warn")
        hard = hard_box.number_input(f"Hard limit {unit}".strip(), min_value=0.0, value=round(base[measure].hard * scale, 6), step=step, key=f"{key}-hard")
        limits[measure] = {"warn": warn / scale, "hard": hard / scale}
    return limits


def _holdings_text() -> tuple[str, str | None]:
    """The holdings CSV: the uploaded file if there is one, otherwise what was pasted."""
    pasted = st.text_area("Holdings (CSV)", key=HOLDINGS_KEY, height=120, placeholder="symbol,value\nSBIN,50000", help=HOLDINGS_HELP)
    uploaded = st.file_uploader("...or upload a CSV file", type=["csv"], key=UPLOAD_KEY)
    if uploaded is None:
        return pasted, None
    try:
        return uploaded.getvalue().decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return "", "holdings: the uploaded file is not a text file"


def risk_controls() -> tuple[Overlay | None, str | None]:
    """The sidebar: the person's risk limits, holdings and the amount they might invest. Returns the overlay to apply
    (None while the limits are switched off) and a message when something they entered cannot be used."""
    with st.sidebar:
        st.header("Risk profile")
        if not st.checkbox("Apply my risk limits", key=RISK_ON, help="Checks the stock against your limits after the specialists have spoken."):
            st.caption("Off: the verdict is the specialists' blend, with no risk limits.")
            return None, None
        preset = st.selectbox("Starting point", list(PRESETS), index=1, key=PRESET_KEY)
        with st.expander("Limits"):  # a fixed label: a changing one would collapse the section on every click
            limits = _limit_boxes(preset)
        text, upload_problem = _holdings_text()
        amount = st.number_input("Amount you might invest (rupees, 0 for none)", min_value=0.0, step=10000.0, value=0.0, key=AMOUNT_KEY)
        st.caption("Limits are starting points, not advice. Nothing you enter here is stored.")
    if upload_problem:
        return None, upload_problem
    try:
        return Overlay(parse_profile({"preset": preset, "limits": limits}), parse_holdings(text), amount or None), None
    except ProfileError as exc:
        return None, str(exc)
```

Save the script below as `$TEMP/s1i_source_page.py` and run it from the repository root. It edits `src/athena/dashboard/app.py` and `tests/dash_fakes.py` (the fake services take and record the overlay) and prints `page edited for the risk profile`.

```python
import pathlib


def edit(path, pairs):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t, encoding="utf-8", newline="\n")


edit(
    "src/athena/dashboard/app.py",
    [
        (
            "from athena.dashboard.backtest_view import BacktestView\n",
            "from athena.dashboard.backtest_view import BacktestView\nfrom athena.dashboard.risk_form import risk_controls\n",
        ),
        (
            "from athena.orchestrator.report import DISCLAIMER\n",
            "from athena.orchestrator.report import DISCLAIMER\nfrom athena.risk_overlay.model import LABELS, Overlay\nfrom athena.risk_overlay.parse import overlay_token\n",
        ),
        (
            "BACKTESTABLE = (\"equity\", \"etf\")\n",
            "BACKTESTABLE = (\"equity\", \"etf\")\nSTATUS_WORDS = {\"ok\": \"ok\", \"warn\": \"warning\", \"breach\": \"BREACH\", \"unchecked\": \"not checked\"}\n",
        ),
        ("    def view(self, query: str) -> DashboardView: ...\n", "    def view(self, query: str, overlay: Overlay | None = None) -> DashboardView: ...\n"),
        (
            "def _chosen() -> list[dict]:",
            "def _risk_overlay(verdict: dict) -> None:\n"
            "    \"\"\"The risk limits the person set, one row per check, and what they did to the verdict.\"\"\"\n"
            "    findings = verdict.get(\"risk_findings\")\n"
            "    if not findings:\n        return\n"
            "    st.markdown(\"**Risk overlay**\")\n"
            "    st.dataframe(\n"
            "        [{\"check\": LABELS[item[\"check\"]], \"status\": STATUS_WORDS[item[\"status\"]], \"detail\": item[\"message\"]} for item in findings],\n"
            "        hide_index=True, width=\"stretch\",\n"
            "    )\n\n\n"
            "def _chosen() -> list[dict]:",
        ),
        (
            "    right.metric(\"Conviction\", verdict.get(\"conviction\", 0))\n",
            "    right.metric(\"Conviction\", verdict.get(\"conviction\", 0))\n"
            "    if \"pre_overlay_verdict\" in verdict:\n"
            "        st.warning(f\"Held back from {verdict['pre_overlay_verdict']} by your risk limits: the specialists said {verdict['pre_overlay_verdict']}.\")\n",
        ),
        (
            "    for panel in view.panels:\n        _panel(panel)\n\n    risks =",
            "    for panel in view.panels:\n        _panel(panel)\n    _risk_overlay(verdict)\n\n    risks =",
        ),
        (
            "    st.title(\"Athena\")\n    picked =",
            "    st.title(\"Athena\")\n    overlay, problem = risk_controls()\n    picked =",
        ),
        (
            "        st.info(\"Type an NSE ticker, an ISIN or a name to analyze it.\")\n        return\n    try:\n        with st.spinner(\"Resolving, fetching data and asking the specialists...\"):\n"
            "            view = _remembered(\"view\", query, lambda: service.view(query))\n",
            "        st.info(\"Type an NSE ticker, an ISIN or a name to analyze it.\")\n        return\n"
            "    if problem:\n        st.error(problem)\n        return\n"
            "    try:\n        with st.spinner(\"Resolving, fetching data and asking the specialists...\"):\n"
            "            view = _remembered(\"view\", f\"{query}|{overlay_token(overlay)}\", lambda: service.view(query, overlay))\n",
        ),
    ],
)

edit(
    "tests/dash_fakes.py",
    [
        (
            "        self.canned, self.error, self.queries = view, error, []\n",
            "        self.canned, self.error, self.queries, self.overlays = view, error, [], []\n",
        ),
        (
            "    def view(self, query):\n        self.queries.append(query)\n        if self.error:\n            raise self.error\n        return self.canned\n",
            "    def view(self, query, overlay=None):\n        self.queries.append(query)\n        self.overlays.append(overlay)\n        if self.error:\n            raise self.error\n        return self.canned\n",
        ),
        (
            "    def view(self, query):\n        self.queries.append(query)\n        return self.views[query]\n",
            "    def view(self, query, overlay=None):\n        self.queries.append(query)\n        self.overlays.append(overlay)\n        return self.views[query]\n",
        ),
    ],
)
print("page edited for the risk profile")
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest -q` (expect `845 passed, 65 skipped`) and `.venv/Scripts/python -m pyflakes src tests` (prints nothing).

- [ ] **Step 5: Mutation check**

Run: `.venv/Scripts/python $TEMP/mutate_1i.py page` from the repository root.
Expected: 11 lines, all `CAUGHT`.

- [ ] **Step 6: Commit**

```bash
git status --short
git add -A src tests
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: let the person set a risk profile on the dashboard and see the risk overlay" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 5: Live check, TRD, PRD and the design brought in line

**Files:**
- Create: `tests/live/test_live_risk_overlay.py`
- Modify (by the script below): `TRD.md`, `PRD.md`, `docs/superpowers/specs/2026-10-07-phase-1i-a-risk-overlay-design.md`

- [ ] **Step 1: Add the live test**

Create `tests/live/test_live_risk_overlay.py`:

```python
import math
from datetime import date, timedelta

import pytest

from athena.adapters.prices import JugaadPriceAdapter
from athena.risk_overlay.apply import apply_overlay
from athena.risk_overlay.checks import figures_from_bars, run_checks
from athena.risk_overlay.model import PRESETS, Holding, Limit, Overlay, RiskProfile

pytestmark = pytest.mark.live

VERDICT = {"verdict": "Buy", "conviction": 70, "key_risks": [], "resolution_path": "blend"}


@pytest.fixture(scope="module")
def adapter():
    return JugaadPriceAdapter()


def bars_for(adapter, symbol):
    end = date.today()
    return adapter.fetch_ohlcv(symbol, "1d", since=end - timedelta(days=760), until=end)


@pytest.mark.parametrize("symbol", ["SBIN", "HDFCBANK", "IDEA"])
def test_live_every_figure_is_real_and_in_a_sensible_range(adapter, symbol):
    figures = figures_from_bars(bars_for(adapter, symbol))
    assert figures.reasons == {} and figures.window == "last 252 daily returns"
    for value in (figures.volatility, figures.drawdown, figures.var_95, figures.cvar_95, figures.traded_value):
        assert value is not None and math.isfinite(value) and value > 0
    assert 0.05 < figures.volatility < 1.5 and 0 < figures.drawdown < 1 and figures.var_95 < figures.cvar_95 < 0.5
    assert figures.traded_value > 1e7  # a listed large cap trades more than a crore of rupees a day


def test_live_a_stock_is_held_to_the_limits_on_its_own_measured_risk(adapter):
    figures = figures_from_bars(bars_for(adapter, "IDEA"))
    strict = RiskProfile("strict", {"volatility": Limit(figures.volatility / 3, figures.volatility / 2), "var_95": Limit(figures.var_95 / 3, figures.var_95 / 2)})
    loose = RiskProfile("loose", {"volatility": Limit(figures.volatility * 2, figures.volatility * 3), "var_95": Limit(figures.var_95 * 2, figures.var_95 * 3)})
    assert {f.status for f in run_checks(figures, Overlay(strict), "IDEA")} == {"breach"}
    assert {f.status for f in run_checks(figures, Overlay(loose), "IDEA")} == {"ok"}
    held = apply_overlay(VERDICT, run_checks(figures, Overlay(strict), "IDEA"))
    assert held["verdict"] == "Hold" and held["pre_overlay_verdict"] == "Buy"
    print("\n" + "\n".join(held["key_risks"]))
    assert apply_overlay(VERDICT, run_checks(figures, Overlay(loose), "IDEA"))["verdict"] == "Buy"


def test_live_an_amount_and_holdings_are_checked_against_the_real_traded_value(adapter):
    figures = figures_from_bars(bars_for(adapter, "SBIN"))
    amount = figures.traded_value * 0.10  # a tenth of a normal day's trading: far more than any preset allows
    overlay = Overlay(PRESETS["moderate"], (Holding("INFY", 900_000.0),), amount)
    found = {f.check: f for f in run_checks(figures, overlay, "SBIN")}
    assert found["liquidity"].status == "breach" and found["liquidity"].value == pytest.approx(0.10)
    assert found["position"].status == "breach" and found["concentration"].status in ("warn", "breach")
    small = Overlay(PRESETS["moderate"], (Holding("INFY", 900_000.0),), 10_000.0)
    assert {f.check: f.status for f in run_checks(figures, small, "SBIN")}["liquidity"] == "ok"
```

- [ ] **Step 2: Run the live checks against real data**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest tests/live/test_live_risk_overlay.py --live -q -s`
Expected: `5 passed` (about a second; prices come from NSE through jugaad-data and need no API key). If NSE is unreachable the price fetch fails: that is an environment problem; report it rather than editing the tests.

- [ ] **Step 3: Record it in the docs**

Save the script below as `$TEMP/docs_1ia.py` and run it from the repository root. It prints three `docs_1ia applied to ...` lines; an `AssertionError` means the text differs from what the script expects: stop and report.

```python
import pathlib
import re
import sys


def load(path):
    raw = pathlib.Path(path).read_bytes().decode("utf-8")
    return raw, raw.replace("\r\n", "\n")


def save(path, raw, text):
    if "\r\n" in raw:
        text = text.replace("\n", "\r\n")
    pathlib.Path(path).write_bytes(text.encode("utf-8"))


def swap(text, old, new):
    assert text.count(old) == 1, old[:70]
    return text.replace(old, new)


# ---- TRD
raw, t = load("TRD.md")
t = swap(
    t,
    "a limit breach is always surfaced even when every specialist says buy.",
    "a limit breach is always surfaced even when every specialist says buy. *Implemented in Plan 1i-a (one instrument at a time, opt-in):* "
    "a person's `RiskProfile` (three presets, conservative, moderate and aggressive, every number editable; each check has a warn level and a hard limit; "
    "the defaults are uncalibrated starting points) is checked after the blend by `athena.risk_overlay`: annualised volatility, worst one-year drawdown, "
    "1-day historical VaR and CVaR at 95% (about a year of daily returns, from the instrument's own bars), liquidity (the amount against the median daily traded value of the last 20 bars) and, "
    "when the person gives holdings (a CSV of symbol and rupee value) and an amount, the position's weight after the buy and the portfolio's Herfindahl concentration. "
    "Each check is a finding (ok, warn, breach, or unchecked with the reason when a figure cannot be computed or an input was not given); a hard breach holds a Buy or Overweight back to Hold "
    "and keeps the specialists' call in `pre_overlay_verdict`, it never stops a Hold, Underweight or Sell, and a warning never changes a verdict; breaches and warnings always lead the key risks. "
    "The profile, holdings and amount are plain data per request (no stored state): `--profile`, `--holdings` and `--amount` on the command line, a sidebar on the dashboard. "
    "Not built: portfolio VaR across the holdings' own histories, sector or factor limits, and Riskfolio-Lib or PyPortfolioOpt optimisation.",
)
t = swap(
    t,
    "the debate and judge, the risk overlay and every specialist other than Quant/Technical are not built.",
    "the debate and judge are not built; the equity specialists arrived in Plan 1f and the risk overlay in Plan 1i-a (§2.7).",
)
t = swap(
    t,
    "- **Oct 7, 2026 (resolver, person at a screen)**",
    "- **Oct 7, 2026 (Phase 1i-a)** — Risk overlay and investor profile (`athena.risk_overlay`): a profile with warn and hard limits, optional holdings and an amount, seven checks, "
    "and a rule that holds a Buy or Overweight back to Hold on a hard breach while showing both calls; `value_at_risk` and `expected_shortfall` join the metrics engine; the verdict contract gains the "
    "optional `pre_overlay_verdict` and `risk_findings`; the orchestrator, command line and dashboard take an optional overlay (design docs/superpowers/specs/2026-10-07-phase-1i-a-risk-overlay-design.md, "
    "plan docs/superpowers/plans/2026-10-07-phase-1i-a-risk-overlay.md). Golden test sets (1i-b) and blend calibration (1i-c) follow.\n"
    "- **Oct 7, 2026 (resolver, person at a screen)**",
)
save("TRD.md", raw, t)
print("docs_1ia applied to TRD.md")

# ---- PRD
raw, t = load("PRD.md")
t = swap(
    t,
    "*Acceptance:* a risk-limit breach is surfaced even when every specialist individually says buy.",
    "*Acceptance:* a risk-limit breach is surfaced even when every specialist individually says buy. "
    "*Status (Oct 7, 2026): implemented for one instrument at a time against the person's own limits, with holdings and an amount optional; "
    "portfolio-level VaR across the holdings' histories is not built; see TRD section 2.7.*",
)
save("PRD.md", raw, t)
print("docs_1ia applied to PRD.md")

# ---- the 1i-a design, brought in line with what was built
spec = "docs/superpowers/specs/2026-10-07-phase-1i-a-risk-overlay-design.md"
raw, t = load(spec)
t = swap(t, "All from figures already fetched for the analysis (about 1 year of daily bars; the metrics packet's window):", "All from the instrument's own price history, which the analysis has already fetched (about 1 year of daily bars; the metrics packet's window of 252 returns):")
t = swap(t, "- `volatility`, `drawdown`: the metrics packet's `volatility` and `max_drawdown` (drawdown sign flipped to a positive size).", "- `volatility`, `drawdown`: the metrics engine's `annualized_volatility` and `max_drawdown`, computed by the overlay on that window (drawdown sign flipped to a positive size).")
t = re.sub(r" Both added to the packet\.", " The overlay computes them itself from the stock's bars; they are not added to the metrics packet, so the overlay needs no market series.", t, count=1)
t = swap(t, "- `beta` against NIFTY 50 is shown for context only (no limit).\n", "- Beta is not part of the overlay: the risk panel already shows it.\n")
t = swap(t, "1. Every `breach` and `warn` is appended to `key_risks` (breaches first), even when the verdict does not change.", "1. Every `breach` and `warn` is put at the front of `key_risks` (breaches first, ahead of the specialists' own risks), even when the verdict does not change.")
t = swap(t, "after the blend it computes the findings from the risk packet and bars it already builds and applies the override.", "after the blend it computes the findings from the instrument's bars (the same shared price download the specialists and charts use) and applies the override.")
t = swap(t, "a CSV upload, an amount box)", "a box to paste the holdings CSV or a file upload, an amount box)")
t = swap(t, "`DashboardView` carries the plain findings and the pre-overlay verdict;", "the findings and the pre-overlay verdict travel inside `view.verdict`, so `DashboardView` itself is unchanged;")
save(spec, raw, t)
print("docs_1ia applied to the 1i-a design")
```

- [ ] **Step 4: Final verification**

Run the whole suite `.venv/Scripts/python -m pytest -q` (expect `845 passed, 70 skipped`), `.venv/Scripts/python -m pyflakes src tests` (prints nothing) and `git status --short` (only `TRD.md`, `PRD.md`, the design file and the new live test).

- [ ] **Step 5: Commit**

```bash
git add TRD.md PRD.md docs/superpowers/specs/2026-10-07-phase-1i-a-risk-overlay-design.md tests/live/test_live_risk_overlay.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "test: add live risk overlay checks; record the risk overlay in the TRD and PRD" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```
