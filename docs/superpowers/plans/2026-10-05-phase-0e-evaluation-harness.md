# Phase 0e — Evaluation Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the automated checks that make specialist and resolver behaviour verifiable before any specialist exists: a number-grounding validator, contract validators for specialist output and judge verdicts, abstention and order-invariance checks, and a resolver evaluation that scores outcomes by safety (correct / safe / wrong / missed). Then use the evaluation to find and fix the resolver's known weakness, noisy candidate ranking for short ambiguous queries.

**Architecture:** `athena.evaluation` holds pure functions (`grounding`, `schema`, `checks`, `resolver_eval`) with no network access. The abstention and order-invariance checks take a callable, so they work on any specialist or judge, real or fake. The resolver evaluation judges each case into one of four outcomes; "wrong" (a confident misroute) is the one that must stay at zero.

**Tech Stack:** Python >= 3.11, pytest. No new dependencies.

**Spec:** `TRD.md` §2.14 (evaluation harness), §3 (specialist and judge contracts); `PRD.md` FR-2, FR-15.

**Plan series:** 0a-0d (done) -> **0e (this plan)** -> Phase 1 (stocks). The golden sets and the portability test arrive with the specialists they score (Phase 1) and with a named second runtime.

**Suggested models:** Sonnet at medium effort. Prototyped end to end in a scratch copy first: 244 offline tests and the live evaluation passed, and the ranking tests were confirmed to fail on the old resolver and pass on the new one.

## Verified findings (5 Oct 2026, live NSE lists: 2,593 equities + 351 ETFs)

Resolver evaluation on 260 cases (20 hand-labeled + 240 synthetic, seeded):

| Category | Before ranking fix | After ranking fix |
| --- | --- | --- |
| ticker, ticker_lower, ticker_suffix, isin, exact_name, name, typo, ambiguous, garbage | 100% correct or safe | unchanged |
| prefix (e.g. `SUND`): right instrument offered | 22 of 40 safe, 17 missed, 1 wrong | 39 of 40 safe, 1 missed, 0 wrong |
| overall wrong / missed | 0.4% / 6.5% | 0.0% / 0.4% |

- The one "wrong" before the fix was a **labeling bug in the synthetic generator**, not the resolver: the generated prefix `IIFL` is itself a real symbol, so resolving it exactly was right. The generator now skips prefixes that are real symbols.
- The remaining miss (`INDI`) is genuinely ambiguous: many `INDI*` symbols compete for five suggestion slots.
- **Caveat on the synthetic cases:** they test self-consistency (can the resolver find an instrument given a degraded form of its own name), not whether the master lists are right, and they are not a substitute for hand-labeled cases. The hand-labeled set is 20 cases; grow it as real queries accumulate.
- **The ranking fix:** candidates whose symbol or name *starts with* the query (3+ characters) are listed first, scored 0.70-0.89 so a prefix match can never be auto-accepted on its own; auto-accept still requires an edit-similarity of at least 0.90 with a 0.05 lead. Prefix queries such as `SBI` and `TATA` now surface `SBIN, SBILIFE, SBICARD` and the Tata group instead of look-alikes like `BI` or `TATVA`.

## Global Constraints

- No network in the default test run; the live evaluation is opt-in via `--live`.
- Grounding rule: every figure cited in `reasoning` must appear in the input data within rounding to the digits cited (a fraction may be cited as a percentage; a negative value may be cited by magnitude; plain integers 0-10 are ignored by default).
- Contract validators implement TRD §3 exactly; unknown keys are errors (the contract is locked).
- Resolver outcome definitions are fixed: **correct** right answer without hedging; **safe** asked the user and offered the right answer; **wrong** confidently resolved to the wrong instrument; **missed** asked or refused without offering the right answer.
- Commit with the GitHub no-reply identity: `git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit ...`; `git push` after each task.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/athena/evaluation/__init__.py` | Package marker |
| `src/athena/evaluation/grounding.py` | `check_grounded`, `GroundingResult` |
| `src/athena/evaluation/schema.py` | `validate_specialist_output`, `validate_judge_verdict` |
| `src/athena/evaluation/checks.py` | `check_abstention`, `check_order_invariance` |
| `src/athena/evaluation/resolver_eval.py` | `ResolverCase`, `judge_case`, `evaluate_resolver`, `synthetic_cases`, `HAND_LABELED`, `format_report` |
| `src/athena/resolver.py` (modify) | Prefix-first candidate ranking |
| `tests/test_grounding.py`, `test_eval_schema.py`, `test_eval_checks.py`, `test_resolver_eval.py` | Offline tests |
| `tests/test_resolver.py` (modify) | Two ranking regression tests |
| `tests/live/test_live_resolver_eval.py` | Live evaluation with floors |

All commands run from the project root in Git Bash.

---

### Task 1: Number-grounding validator

**Files:**
- Create: `src/athena/evaluation/__init__.py` (empty file), `src/athena/evaluation/grounding.py`
- Test: `tests/test_grounding.py`

**Interfaces:**
- Produces (`athena.evaluation.grounding`): `numbers_in_text(text) -> list[str]`; `collect_values(data) -> list[float]` (every number reachable in nested dicts/lists, including numbers written inside strings; booleans excluded); `@dataclass(frozen=True) GroundingResult(checked: int, ungrounded: tuple[str, ...])` with property `ok`; `check_grounded(reasoning, data, ignore_small_integers=True) -> GroundingResult`.

- [ ] **Step 1: Write the failing tests `tests/test_grounding.py`**

```python
import pytest

from athena.evaluation.grounding import check_grounded, collect_values, numbers_in_text

PACKET = {
    "instrument": "SBIN",
    "as_of": "2026-10-05T04:00:00+00:00",
    "metrics": {
        "beta": {"value": 1.101388, "unit": "ratio", "window": "252 returns 2025-10-01..2026-10-01"},
        "volatility_annualized": {"value": 0.237161, "unit": "fraction"},
        "max_drawdown": {"value": -0.234892, "unit": "fraction"},
    },
    "price": 22421.95,
}


def test_numbers_in_text_reads_signs_commas_decimals_and_percents():
    assert numbers_in_text("beta 1.10, vol 23.7%, drawdown -23.5%, level 22,421.95") == [
        "1.10", "23.7%", "-23.5%", "22,421.95",
    ]


def test_digits_glued_to_letters_are_not_numbers():
    assert numbers_in_text("FY2026 results in Q3") == []


def test_collect_values_walks_nesting_and_numbers_inside_strings():
    values = collect_values(PACKET)
    assert 1.101388 in values and 22421.95 in values
    assert 252.0 in values and 2026.0 in values  # from the window and as_of strings
    assert collect_values({"flag": True}) == []


@pytest.mark.parametrize(
    "reasoning",
    [
        "Beta of 1.10 means the stock moves with the market.",
        "Annualised volatility is 23.7%.",
        "A drawdown of 23.5% over the year; the price is 22,421.95.",
        "Volatility of roughly 24% (rounded).",
        "Beta is 1.1.",
    ],
)
def test_figures_present_in_the_data_are_grounded(reasoning):
    result = check_grounded(reasoning, PACKET)
    assert result.ok, result.ungrounded
    assert result.checked >= 1


def test_invented_figures_are_flagged():
    result = check_grounded("Beta is 1.10 but volatility is 31.2% and alpha is 4.5%.", PACKET)
    assert not result.ok
    assert result.ungrounded == ("31.2%", "4.5%")
    assert result.checked == 3


def test_precision_matters_a_figure_off_by_more_than_rounding_is_flagged():
    assert not check_grounded("Beta is 1.15.", PACKET).ok
    assert check_grounded("Beta is 1.1.", PACKET).ok


def test_small_integers_are_ignored_by_default_but_can_be_checked():
    text = "Three of 5 factors over 2 years."
    assert check_grounded(text, PACKET).ok
    assert not check_grounded(text, PACKET, ignore_small_integers=False).ok


def test_text_without_figures_is_trivially_grounded():
    result = check_grounded("The stock looks fairly valued.", PACKET)
    assert result.ok and result.checked == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_grounding.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.evaluation'`.

- [ ] **Step 3: Create the empty `src/athena/evaluation/__init__.py` and write `src/athena/evaluation/grounding.py`**

```python
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# A number as written in prose: optional sign, digits with thousands commas, optional decimals, optional %.
# Digits glued to letters ("FY2026", "Q3") are not numbers.
_NUMBER_IN_TEXT = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?%?")
_NUMBER_IN_DATA_STRING = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class GroundingResult:
    checked: int
    ungrounded: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.ungrounded


def numbers_in_text(text: str) -> list[str]:
    return _NUMBER_IN_TEXT.findall(text)


def collect_values(data: Any) -> list[float]:
    """Every number reachable in `data`, including numbers written inside strings (windows, dates)."""
    values: list[float] = []
    if isinstance(data, bool):
        return values
    if isinstance(data, (int, float)):
        values.append(float(data))
    elif isinstance(data, str):
        values.extend(float(match) for match in _NUMBER_IN_DATA_STRING.findall(data))
    elif isinstance(data, Mapping):
        for item in data.values():
            values.extend(collect_values(item))
    elif isinstance(data, (list, tuple, set)):
        for item in data:
            values.extend(collect_values(item))
    return values


def _decimals(token: str) -> int:
    return len(token.split(".")[1]) if "." in token else 0


def _matches(token: str, values: list[float]) -> bool:
    cleaned = token.replace(",", "").rstrip("%")
    cited = abs(float(cleaned))
    tolerance = 0.5 * 10 ** (-_decimals(cleaned)) + 1e-9
    for value in values:
        for candidate in (value, value * 100.0):  # a fraction may be cited as a percentage
            if abs(abs(candidate) - cited) <= tolerance:
                return True
    return False


def check_grounded(reasoning: str, data: Any, ignore_small_integers: bool = True) -> GroundingResult:
    """Every figure cited in `reasoning` must appear in `data` (within rounding to the digits cited).

    Plain integers 0-10 (counts such as "3 years") are ignored unless `ignore_small_integers` is False.
    A negative value may be cited by its magnitude ("23.5% drawdown")."""
    values = collect_values(data)
    checked = 0
    ungrounded: list[str] = []
    for token in numbers_in_text(reasoning):
        bare = token.replace(",", "").lstrip("+-")
        if ignore_small_integers and bare.isdigit() and int(bare) <= 10:
            continue
        checked += 1
        if not _matches(token, values):
            ungrounded.append(token)
    return GroundingResult(checked, tuple(ungrounded))
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `206 passed, 36 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/evaluation tests/test_grounding.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add number-grounding validator"
git push
```

---

### Task 2: Contract validators

**Files:**
- Create: `src/athena/evaluation/schema.py`
- Test: `tests/test_eval_schema.py`

**Interfaces:**
- Produces (`athena.evaluation.schema`): `SIGNALS`, `VERDICTS`, `RESOLUTION_PATHS`; `validate_specialist_output(output) -> list[str]` and `validate_judge_verdict(verdict) -> list[str]` returning error messages (empty list = valid). Specialist rules: exactly the keys `signal, confidence, reasoning, data_coverage, missing`; `confidence` an integer 0-100 (booleans rejected); coverage `full` needs an empty `missing`, `partial`/`insufficient` need a non-empty one, and `insufficient` requires an abstention (`neutral`, confidence 0). Verdict rules: required `verdict, conviction, key_risks, resolution_path`; a `debate` path needs `debate_transcript_ref`; optional `panel_agreement` in [0, 1] and boolean `contested`.

- [ ] **Step 1: Write the failing tests `tests/test_eval_schema.py`**

```python
import pytest

from athena.evaluation.schema import validate_judge_verdict, validate_specialist_output

GOOD = {"signal": "bullish", "confidence": 72, "reasoning": "Beta 1.10.", "data_coverage": "full", "missing": []}
GOOD_VERDICT = {"verdict": "Overweight", "conviction": 64, "key_risks": ["rates"], "resolution_path": "blend"}


def variant(**changes):
    return {**GOOD, **changes}


def test_valid_outputs_have_no_errors():
    assert validate_specialist_output(GOOD) == []
    assert validate_specialist_output(variant(data_coverage="partial", missing=["manager_tenure"])) == []
    assert validate_specialist_output(variant(signal="neutral", confidence=0, data_coverage="insufficient", missing=["nav"])) == []


def test_non_objects_are_rejected():
    assert validate_specialist_output("bullish") == ["output must be an object"]
    assert validate_judge_verdict(None) == ["verdict must be an object"]


def test_missing_and_unknown_keys_are_reported():
    bad = {k: v for k, v in GOOD.items() if k != "missing"}
    assert "missing keys: ['missing']" in validate_specialist_output(bad)
    assert "unknown keys: ['target_price']" in validate_specialist_output(variant(target_price=10))


@pytest.mark.parametrize(
    "changes, fragment",
    [
        ({"signal": "buy"}, "signal must be one of"),
        ({"confidence": 101}, "confidence must be an integer 0-100"),
        ({"confidence": 72.5}, "confidence must be an integer 0-100"),
        ({"confidence": True}, "confidence must be an integer 0-100"),
        ({"reasoning": "  "}, "reasoning must be a non-empty string"),
        ({"data_coverage": "mostly"}, "data_coverage must be one of"),
        ({"missing": "nav"}, "missing must be a list of strings"),
    ],
)
def test_field_level_errors(changes, fragment):
    errors = validate_specialist_output(variant(**changes))
    assert any(fragment in error for error in errors), errors


def test_coverage_rules_between_label_and_missing_list():
    assert any("empty missing list" in e for e in validate_specialist_output(variant(missing=["nav"])))
    assert any("must list what is missing" in e for e in validate_specialist_output(variant(data_coverage="partial")))


def test_insufficient_coverage_requires_an_abstention():
    errors = validate_specialist_output(variant(data_coverage="insufficient", missing=["nav"]))
    assert any("requires an abstention" in e for e in errors)


def test_valid_verdicts():
    assert validate_judge_verdict(GOOD_VERDICT) == []
    debate = {**GOOD_VERDICT, "resolution_path": "debate", "debate_transcript_ref": "d-17", "panel_agreement": 0.67, "contested": False}
    assert validate_judge_verdict(debate) == []


@pytest.mark.parametrize(
    "changes, fragment",
    [
        ({"verdict": "Strong Buy"}, "verdict must be one of"),
        ({"conviction": -1}, "conviction must be an integer 0-100"),
        ({"key_risks": "rates"}, "key_risks must be a list of strings"),
        ({"resolution_path": "vote"}, "resolution_path must be one of"),
        ({"resolution_path": "debate"}, "needs a debate_transcript_ref"),
        ({"panel_agreement": 1.5}, "panel_agreement must be a number between 0 and 1"),
        ({"contested": "no"}, "contested must be a boolean"),
    ],
)
def test_verdict_errors(changes, fragment):
    errors = validate_judge_verdict({**GOOD_VERDICT, **changes})
    assert any(fragment in error for error in errors), errors


def test_verdict_missing_key():
    bad = {k: v for k, v in GOOD_VERDICT.items() if k != "conviction"}
    assert "missing keys: ['conviction']" in validate_judge_verdict(bad)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_eval_schema.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.evaluation.schema'`.

- [ ] **Step 3: Write `src/athena/evaluation/schema.py`**

```python
from __future__ import annotations

from typing import Any

from athena.contracts import Coverage

SIGNALS = ("bullish", "bearish", "neutral")
VERDICTS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
RESOLUTION_PATHS = ("debate", "blend")
_SPECIALIST_KEYS = {"signal", "confidence", "reasoning", "data_coverage", "missing"}
_VERDICT_KEYS = {"verdict", "conviction", "key_risks", "resolution_path", "debate_transcript_ref", "panel_agreement", "contested"}
_VERDICT_REQUIRED = {"verdict", "conviction", "key_risks", "resolution_path"}


def _is_int_in_range(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_specialist_output(output: Any) -> list[str]:
    """Errors against the specialist output contract (TRD section 3); an empty list means valid."""
    if not isinstance(output, dict):
        return ["output must be an object"]
    errors: list[str] = []
    missing_keys = sorted(_SPECIALIST_KEYS - output.keys())
    if missing_keys:
        errors.append(f"missing keys: {missing_keys}")
    unknown_keys = sorted(output.keys() - _SPECIALIST_KEYS)
    if unknown_keys:
        errors.append(f"unknown keys: {unknown_keys}")
    if "signal" in output and output["signal"] not in SIGNALS:
        errors.append(f"signal must be one of {list(SIGNALS)}, got {output['signal']!r}")
    if "confidence" in output and not _is_int_in_range(output["confidence"], 0, 100):
        errors.append(f"confidence must be an integer 0-100, got {output['confidence']!r}")
    if "reasoning" in output and not (isinstance(output["reasoning"], str) and output["reasoning"].strip()):
        errors.append("reasoning must be a non-empty string")
    coverage_values = [c.value for c in Coverage]
    if "data_coverage" in output and output["data_coverage"] not in coverage_values:
        errors.append(f"data_coverage must be one of {coverage_values}, got {output['data_coverage']!r}")
    if "missing" in output and not _is_str_list(output["missing"]):
        errors.append("missing must be a list of strings")
    if errors:
        return errors

    coverage, missing = output["data_coverage"], output["missing"]
    if coverage == Coverage.FULL.value and missing:
        errors.append("data_coverage 'full' must have an empty missing list")
    if coverage != Coverage.FULL.value and not missing:
        errors.append(f"data_coverage {coverage!r} must list what is missing")
    if coverage == Coverage.INSUFFICIENT.value and (output["signal"] != "neutral" or output["confidence"] != 0):
        errors.append("data_coverage 'insufficient' requires an abstention: signal 'neutral', confidence 0")
    return errors


def validate_judge_verdict(verdict: Any) -> list[str]:
    """Errors against the judge verdict contract (TRD section 3); an empty list means valid."""
    if not isinstance(verdict, dict):
        return ["verdict must be an object"]
    errors: list[str] = []
    missing_keys = sorted(_VERDICT_REQUIRED - verdict.keys())
    if missing_keys:
        errors.append(f"missing keys: {missing_keys}")
    unknown_keys = sorted(verdict.keys() - _VERDICT_KEYS)
    if unknown_keys:
        errors.append(f"unknown keys: {unknown_keys}")
    if "verdict" in verdict and verdict["verdict"] not in VERDICTS:
        errors.append(f"verdict must be one of {list(VERDICTS)}, got {verdict['verdict']!r}")
    if "conviction" in verdict and not _is_int_in_range(verdict["conviction"], 0, 100):
        errors.append(f"conviction must be an integer 0-100, got {verdict['conviction']!r}")
    if "key_risks" in verdict and not _is_str_list(verdict["key_risks"]):
        errors.append("key_risks must be a list of strings")
    if "resolution_path" in verdict and verdict["resolution_path"] not in RESOLUTION_PATHS:
        errors.append(f"resolution_path must be one of {list(RESOLUTION_PATHS)}, got {verdict['resolution_path']!r}")
    if verdict.get("resolution_path") == "debate" and not verdict.get("debate_transcript_ref"):
        errors.append("a debate verdict needs a debate_transcript_ref")
    if "panel_agreement" in verdict:
        agreement = verdict["panel_agreement"]
        if isinstance(agreement, bool) or not isinstance(agreement, (int, float)) or not 0 <= agreement <= 1:
            errors.append("panel_agreement must be a number between 0 and 1")
    if "contested" in verdict and not isinstance(verdict["contested"], bool):
        errors.append("contested must be a boolean")
    return errors
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `227 passed, 36 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/evaluation/schema.py tests/test_eval_schema.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add specialist output and judge verdict contract validators"
git push
```

---

### Task 3: Abstention and order-invariance checks

**Files:**
- Create: `src/athena/evaluation/checks.py`
- Test: `tests/test_eval_checks.py`

**Interfaces:**
- Consumes: `validate_specialist_output`, `Coverage`.
- Produces (`athena.evaluation.checks`):
  - `check_abstention(run, full_inputs, critical, optional=()) -> list[str]`: removes each declared input in turn and calls `run(inputs)`. Removing a critical input must yield coverage `insufficient`, an optional one `partial`; the output must satisfy the specialist contract and name the removed input under `missing`. Returns failures (empty = honest).
  - `check_order_invariance(judge, parts, max_orderings=6) -> list[str]`: calls `judge(list_of_parts)` over up to `max_orderings` orderings and reports every ordering whose `["verdict"]` differs from the first.

- [ ] **Step 1: Write the failing tests `tests/test_eval_checks.py`**

```python
from athena.evaluation.checks import check_abstention, check_order_invariance

FULL = {"nav": [1.0, 2.0], "ter": 0.9, "manager_tenure": 5}


def honest(inputs):
    missing = [name for name in ("nav", "ter") if name not in inputs]
    optional_missing = [name for name in ("manager_tenure",) if name not in inputs]
    if missing:
        return {"signal": "neutral", "confidence": 0, "reasoning": "Too little data.", "data_coverage": "insufficient", "missing": missing + optional_missing}
    if optional_missing:
        return {"signal": "bullish", "confidence": 60, "reasoning": "Cost looks low.", "data_coverage": "partial", "missing": optional_missing}
    return {"signal": "bullish", "confidence": 70, "reasoning": "Cost looks low.", "data_coverage": "full", "missing": []}


def bluffer(inputs):
    return {"signal": "bullish", "confidence": 90, "reasoning": "Looks great.", "data_coverage": "full", "missing": []}


def test_an_honest_specialist_passes_abstention_checks():
    assert check_abstention(honest, FULL, critical=["nav", "ter"], optional=["manager_tenure"]) == []


def test_a_specialist_that_bluffs_is_caught_for_every_removed_input():
    failures = check_abstention(bluffer, FULL, critical=["nav", "ter"], optional=["manager_tenure"])
    assert len(failures) == 3 * 2  # wrong coverage + not named in missing, for each of 3 inputs
    assert any("without 'nav'" in f and "expected coverage 'insufficient'" in f for f in failures)
    assert any("without 'manager_tenure'" in f and "does not name it" in f for f in failures)


def test_invalid_output_is_reported_not_raised():
    failures = check_abstention(lambda inputs: {"signal": "buy"}, FULL, critical=["nav"])
    assert len(failures) == 1 and "invalid output" in failures[0]


def test_a_stable_judge_passes_order_invariance():
    judge = lambda parts: {"verdict": "Buy" if "bull" in parts else "Hold"}  # noqa: E731
    assert check_order_invariance(judge, ["bull", "bear", "risk"]) == []


def test_a_judge_swayed_by_who_speaks_first_is_caught():
    judge = lambda parts: {"verdict": "Buy" if parts[0] == "bull" else "Sell"}  # noqa: E731
    failures = check_order_invariance(judge, ["bull", "bear"])
    assert len(failures) == 1 and "flipped from 'Buy' to 'Sell'" in failures[0]


def test_orderings_are_capped():
    calls = []
    judge = lambda parts: calls.append(1) or {"verdict": "Hold"}  # noqa: E731
    check_order_invariance(judge, list("abcdef"), max_orderings=4)
    assert len(calls) == 4
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_eval_checks.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.evaluation.checks'`.

- [ ] **Step 3: Write `src/athena/evaluation/checks.py`**

```python
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from itertools import islice, permutations
from typing import Any

from athena.contracts import Coverage
from athena.evaluation.schema import validate_specialist_output


def check_abstention(
    run: Callable[[Mapping[str, Any]], Any],
    full_inputs: Mapping[str, Any],
    critical: Sequence[str],
    optional: Sequence[str] = (),
) -> list[str]:
    """Remove each declared input in turn and confirm the specialist reports it instead of bluffing.

    Removing a critical input must give coverage 'insufficient' (an abstention); removing an optional
    one must give 'partial'; either way the output must satisfy the specialist contract and name the
    removed input under `missing`. Returns a list of failures; empty means the specialist is honest."""
    failures: list[str] = []
    expectations = [(name, Coverage.INSUFFICIENT.value) for name in critical] + [
        (name, Coverage.PARTIAL.value) for name in optional
    ]
    for removed, expected in expectations:
        inputs = {key: value for key, value in full_inputs.items() if key != removed}
        output = run(inputs)
        errors = validate_specialist_output(output)
        if errors:
            failures.append(f"without {removed!r}: invalid output: {errors}")
            continue
        if output["data_coverage"] != expected:
            failures.append(f"without {removed!r}: expected coverage {expected!r}, got {output['data_coverage']!r}")
        if removed not in output["missing"]:
            failures.append(f"without {removed!r}: the missing list does not name it: {output['missing']}")
    return failures


def check_order_invariance(
    judge: Callable[[Sequence[Any]], Mapping[str, Any]],
    parts: Sequence[Any],
    max_orderings: int = 6,
) -> list[str]:
    """A judge must not rule differently when the specialists are presented in another order."""
    orderings = list(islice(permutations(parts), max_orderings))
    baseline = judge(list(orderings[0]))["verdict"]
    failures = []
    for ordering in orderings[1:]:
        verdict = judge(list(ordering))["verdict"]
        if verdict != baseline:
            failures.append(f"verdict flipped from {baseline!r} to {verdict!r} for ordering {list(ordering)}")
    return failures
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `233 passed, 36 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/evaluation/checks.py tests/test_eval_checks.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add abstention and order-invariance checks"
git push
```

---

### Task 4: Resolver evaluation

**Files:**
- Create: `src/athena/evaluation/resolver_eval.py`
- Test: `tests/test_resolver_eval.py`

**Interfaces:**
- Consumes: `InstrumentResolver`, `InstrumentIndex`, `Resolution`, `Ambiguity`, `UnknownInstrument`.
- Produces (`athena.evaluation.resolver_eval`): outcome constants `CORRECT, SAFE, WRONG, MISSED`; kind constants `RESOLVE, AMBIGUOUS, UNKNOWN`; `ResolverCase(query, category, kind, expected_class=None, expected_symbol=None)`; `judge_case(resolver, case) -> (outcome, what_was_returned)`; `ResolverReport` with `count(outcome)`, `rate(outcome)`, `category_rate(category, *outcomes)`, `by_category`, `wrong_cases`, `missed_cases`; `evaluate_resolver(resolver, cases)`; `format_report(report)`; `synthetic_cases(index, seed=7, per_category=40)` (categories `ticker_lower, ticker_suffix, exact_name, typo, prefix, garbage`); `HAND_LABELED` (20 cases).
- Outcome rules: for `RESOLVE` cases, the right instrument and class without hedging is correct, a wrong one is wrong, an `Ambiguity` containing the right one is safe, otherwise missed. For `AMBIGUOUS` cases, an `Ambiguity` is correct and a `Resolution` is wrong. For `UNKNOWN` cases, a refusal is correct, an `Ambiguity` is safe, a `Resolution` is wrong.

- [ ] **Step 1: Write the failing tests `tests/test_resolver_eval.py`**

```python
from datetime import datetime, timezone

from athena.contracts import Record
from athena.evaluation.resolver_eval import (
    AMBIGUOUS,
    CORRECT,
    HAND_LABELED,
    MISSED,
    RESOLVE,
    SAFE,
    UNKNOWN,
    WRONG,
    ResolverCase,
    evaluate_resolver,
    format_report,
    judge_case,
    synthetic_cases,
)
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET
from athena.resolver import InstrumentIndex, InstrumentResolver
from athena.store import DataStore

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def make_resolver(equities, etfs=None):
    store = DataStore()
    for symbol, name in equities:
        store.put(Record(EQUITY_DATASET, symbol, NOW, "x", {"name": name, "isin": "INE000000000"}))
    for symbol, name in (etfs or [("NIFTYBEES", "NIPINDETFNIFTYBEES")]):
        store.put(Record(ETF_DATASET, symbol, NOW, "x", {"name": name, "isin": "INF000000000"}))
    return InstrumentResolver(InstrumentIndex.from_store(store, now=NOW))


SMALL = make_resolver(
    [("SBIN", "State Bank of India"), ("SBILIFE", "SBI Life Insurance Company Limited"),
     ("SBICARD", "SBI Cards and Payment Services Limited"), ("TCS", "Tata Consultancy Services Limited")]
)


def outcome(query, kind, symbol=None, asset_class="equity"):
    return judge_case(SMALL, ResolverCase(query, "t", kind, asset_class if symbol else None, symbol))[0]


def test_resolve_cases():
    assert outcome("sbin", RESOLVE, "SBIN") == CORRECT
    assert outcome("sbin", RESOLVE, "TCS") == WRONG  # confidently resolved to something else
    assert outcome("SBI", RESOLVE, "SBIN") == SAFE  # asked, and the right answer was offered
    assert outcome("SBI", RESOLVE, "TCS") == MISSED  # asked, but the right answer was not offered
    assert outcome("ZZZZQQ", RESOLVE, "SBIN") == MISSED  # refused


def test_wrong_asset_class_counts_as_wrong():
    assert outcome("SBIN", RESOLVE, "SBIN", asset_class="etf") == WRONG


def test_ambiguous_cases_expect_a_question_not_a_guess():
    assert outcome("SBI", AMBIGUOUS) == CORRECT
    assert outcome("SBIN", AMBIGUOUS) == WRONG
    assert outcome("ZZZZQQ", AMBIGUOUS) == MISSED


def test_unknown_cases_expect_a_refusal():
    assert outcome("ZZZZQQ", UNKNOWN) == CORRECT
    assert outcome("SBI", UNKNOWN) == SAFE
    assert outcome("SBIN", UNKNOWN) == WRONG


def test_evaluate_aggregates_by_category_and_collects_failures():
    cases = [
        ResolverCase("sbin", "ticker", RESOLVE, "equity", "SBIN"),
        ResolverCase("sbin", "ticker", RESOLVE, "equity", "TCS"),
        ResolverCase("SBI", "prefix", RESOLVE, "equity", "SBIN"),
        ResolverCase("SBI", "prefix", RESOLVE, "equity", "TCS"),
    ]
    report = evaluate_resolver(SMALL, cases)
    assert report.total == 4
    assert (report.count(CORRECT), report.count(SAFE), report.count(WRONG), report.count(MISSED)) == (1, 1, 1, 1)
    assert report.rate(WRONG) == 0.25
    assert report.category_rate("prefix", CORRECT, SAFE) == 0.5
    assert [c.expected_symbol for c, _ in report.wrong_cases] == ["TCS"]
    assert [c.expected_symbol for c, _ in report.missed_cases] == ["TCS"]
    assert "4 cases | wrong 25.0%" in format_report(report)


def test_hand_labeled_cases_are_well_formed():
    assert len(HAND_LABELED) >= 20
    for case in HAND_LABELED:
        assert case.kind in (RESOLVE, AMBIGUOUS, UNKNOWN)
        assert (case.expected_symbol is not None) == (case.kind == RESOLVE)


WORDS = ["Alpha", "Bravo", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India", "Juliet", "Kilo"]
BIG = make_resolver([(f"{a[:3].upper()}{b[:3].upper()}", f"{a} {b} Industries Limited") for a in WORDS for b in WORDS])
BIG_INDEX = BIG._index


def test_synthetic_cases_are_deterministic_and_cover_every_category():
    first = synthetic_cases(BIG_INDEX, seed=3, per_category=10)
    assert first == synthetic_cases(BIG_INDEX, seed=3, per_category=10)
    assert first != synthetic_cases(BIG_INDEX, seed=4, per_category=10)
    categories = {c.category for c in first}
    assert categories == {"ticker_lower", "ticker_suffix", "exact_name", "typo", "prefix", "garbage"}
    assert sum(1 for c in first if c.category == "garbage") == 10


def test_synthetic_cases_are_valid_against_the_index():
    for case in synthetic_cases(BIG_INDEX, seed=3, per_category=10):
        if case.kind == RESOLVE:
            entry = BIG_INDEX.by_symbol[case.expected_symbol]
            assert entry.asset_class == case.expected_class
            if case.category == "prefix":
                assert case.query not in BIG_INDEX.by_symbol  # a prefix that is a real symbol would be mislabeled
        else:
            assert case.kind == UNKNOWN and case.query not in BIG_INDEX.by_symbol


def test_simple_synthetic_categories_are_all_correct_and_nothing_is_wrong():
    report = evaluate_resolver(BIG, synthetic_cases(BIG_INDEX, seed=3, per_category=10))
    for category in ("ticker_lower", "ticker_suffix", "exact_name"):
        assert report.category_rate(category, CORRECT) == 1.0, category
    assert report.count(WRONG) == 0, report.wrong_cases
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_resolver_eval.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.evaluation.resolver_eval'`.

- [ ] **Step 3: Write `src/athena/evaluation/resolver_eval.py`**

```python
from __future__ import annotations

import random
import string
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from athena.contracts import UnknownInstrument
from athena.resolver import Ambiguity, InstrumentIndex, InstrumentResolver, Resolution

CORRECT = "correct"  # right answer, no hedging
SAFE = "safe"  # asked the user and the right answer was in the candidates
WRONG = "wrong"  # confidently resolved to the wrong instrument: the failure that must not happen
MISSED = "missed"  # asked or refused without offering the right answer

RESOLVE = "resolve"  # expected_symbol should come back
AMBIGUOUS = "ambiguous"  # the resolver should ask, not guess
UNKNOWN = "unknown"  # the resolver should refuse


@dataclass(frozen=True)
class ResolverCase:
    query: str
    category: str
    kind: str
    expected_class: str | None = None
    expected_symbol: str | None = None


@dataclass
class ResolverReport:
    total: int = 0
    by_category: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    wrong_cases: list[tuple[ResolverCase, str]] = field(default_factory=list)
    missed_cases: list[tuple[ResolverCase, str]] = field(default_factory=list)

    def count(self, outcome: str) -> int:
        return sum(counter[outcome] for counter in self.by_category.values())

    def rate(self, outcome: str) -> float:
        return self.count(outcome) / self.total if self.total else 0.0

    def category_rate(self, category: str, *outcomes: str) -> float:
        counter = self.by_category[category]
        size = sum(counter.values())
        return sum(counter[o] for o in outcomes) / size if size else 0.0


def judge_case(resolver: InstrumentResolver, case: ResolverCase) -> tuple[str, str]:
    """(outcome, what the resolver returned) for one case."""
    try:
        result = resolver.resolve(case.query)
    except UnknownInstrument as exc:
        got = f"refused: {exc}"
        return (CORRECT if case.kind == UNKNOWN else MISSED), got

    if isinstance(result, Resolution):
        got = f"resolved {result.asset_class}:{result.identifier} ({result.resolution_path})"
        if case.kind == RESOLVE:
            right = result.identifier == case.expected_symbol and result.asset_class == case.expected_class
            return (CORRECT if right else WRONG), got
        return WRONG, got

    candidates = [(c.asset_class, c.identifier) for c in result.candidates]
    got = f"asked: {[ident for _, ident in candidates]}"
    if case.kind == AMBIGUOUS:
        return CORRECT, got
    if case.kind == UNKNOWN:
        return SAFE, got
    return (SAFE if (case.expected_class, case.expected_symbol) in candidates else MISSED), got


def evaluate_resolver(resolver: InstrumentResolver, cases: list[ResolverCase]) -> ResolverReport:
    report = ResolverReport()
    for case in cases:
        outcome, got = judge_case(resolver, case)
        report.total += 1
        report.by_category[case.category][outcome] += 1
        if outcome == WRONG:
            report.wrong_cases.append((case, got))
        elif outcome == MISSED:
            report.missed_cases.append((case, got))
    return report


def format_report(report: ResolverReport) -> str:
    lines = [f"{report.total} cases | wrong {report.rate(WRONG):.1%} | missed {report.rate(MISSED):.1%} | "
             f"correct {report.rate(CORRECT):.1%} | safe {report.rate(SAFE):.1%}"]
    for category in sorted(report.by_category):
        counter = report.by_category[category]
        lines.append(
            f"  {category:12} n={sum(counter.values()):3}  correct {counter[CORRECT]:3}  safe {counter[SAFE]:3}  "
            f"wrong {counter[WRONG]:3}  missed {counter[MISSED]:3}"
        )
    return "\n".join(lines)


def synthetic_cases(index: InstrumentIndex, seed: int = 7, per_category: int = 40) -> list[ResolverCase]:
    """Deterministic cases generated from the master lists themselves. They test self-consistency
    (could the resolver find an instrument given a degraded form of its own name?), not whether the
    lists are right, and they are no substitute for hand-labeled cases."""
    rng = random.Random(seed)
    entries = sorted(index.by_symbol.values(), key=lambda e: e.symbol)
    known_symbols = set(index.by_symbol)

    def sample(predicate) -> list:
        pool = [e for e in entries if predicate(e)]
        return rng.sample(pool, min(per_category, len(pool)))

    cases: list[ResolverCase] = []

    def add(category, entries_, make_query):
        for entry in entries_:
            cases.append(ResolverCase(make_query(entry), category, RESOLVE, entry.asset_class, entry.symbol))

    add("ticker_lower", sample(lambda e: True), lambda e: e.symbol.lower())
    add("ticker_suffix", sample(lambda e: True), lambda e: f"{e.symbol}.NS")
    add("exact_name", sample(lambda e: len(e.norm_name) >= 4), lambda e: e.name)
    add("typo", sample(lambda e: len(e.norm_name) >= 10), lambda e: e.name[: len(e.name) // 2] + e.name[len(e.name) // 2 + 1:])
    add("prefix", sample(lambda e: len(e.symbol) >= 6 and e.symbol.isalpha() and e.symbol[:4] not in known_symbols), lambda e: e.symbol[:4])

    while sum(1 for c in cases if c.category == "garbage") < per_category:
        junk = "".join(rng.choice("QXZJWKVYG") for _ in range(8))
        if junk not in known_symbols:
            cases.append(ResolverCase(junk, "garbage", UNKNOWN))
    return cases


HAND_LABELED: list[ResolverCase] = [
    ResolverCase("SBIN", "ticker", RESOLVE, "equity", "SBIN"),
    ResolverCase("reliance", "ticker", RESOLVE, "equity", "RELIANCE"),
    ResolverCase("TCS.NS", "ticker", RESOLVE, "equity", "TCS"),
    ResolverCase("NSE:INFY", "ticker", RESOLVE, "equity", "INFY"),
    ResolverCase("M&M", "ticker", RESOLVE, "equity", "M&M"),
    ResolverCase("BAJAJ-AUTO", "ticker", RESOLVE, "equity", "BAJAJ-AUTO"),
    ResolverCase("NIFTYBEES", "ticker", RESOLVE, "etf", "NIFTYBEES"),
    ResolverCase("GOLDBEES", "ticker", RESOLVE, "etf", "GOLDBEES"),
    ResolverCase("INE062A01020", "isin", RESOLVE, "equity", "SBIN"),
    ResolverCase("INF204KB14I2", "isin", RESOLVE, "etf", "NIFTYBEES"),
    ResolverCase("State Bank of India", "name", RESOLVE, "equity", "SBIN"),
    ResolverCase("Tata Consultancy Services", "name", RESOLVE, "equity", "TCS"),
    ResolverCase("Hindustan Unilever", "name", RESOLVE, "equity", "HINDUNILVR"),
    ResolverCase("Larsen and Toubro", "name", RESOLVE, "equity", "LT"),
    ResolverCase("Relience Industries", "typo", RESOLVE, "equity", "RELIANCE"),
    ResolverCase("RELIANC", "typo", RESOLVE, "equity", "RELIANCE"),
    ResolverCase("SBI", "ambiguous", AMBIGUOUS),
    ResolverCase("TATA", "ambiguous", AMBIGUOUS),
    ResolverCase("nifty 50 etf", "ambiguous", AMBIGUOUS),
    ResolverCase("ZZZZQQ", "garbage", UNKNOWN),
]
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `242 passed, 36 skipped`.

- [ ] **Step 5: Measure the baseline on the real lists (before the ranking fix)**

Write this throwaway script as `baseline.py` in the project root (do not commit it) and run `.venv/Scripts/python baseline.py`:

```python
from athena.evaluation.resolver_eval import HAND_LABELED, evaluate_resolver, format_report, synthetic_cases
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET, NseMasterLoader
from athena.resolver import InstrumentIndex, InstrumentResolver
from athena.store import DataStore

store = DataStore()
loader = NseMasterLoader(store)
loader.refresh(EQUITY_DATASET)
loader.refresh(ETF_DATASET)
index = InstrumentIndex.from_store(store)
report = evaluate_resolver(InstrumentResolver(index), HAND_LABELED + synthetic_cases(index))
print(format_report(report))
```

Expected (matches "Before ranking fix" above, give or take the day's new listings): `prefix` category roughly 22 safe / 17 missed; everything else correct.

- [ ] **Step 6: Commit and push**

```bash
git add src/athena/evaluation/resolver_eval.py tests/test_resolver_eval.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add resolver evaluation with outcome scoring and synthetic cases"
git push
```

---

### Task 5: Prefix-first candidate ranking

**Files:**
- Modify: `src/athena/resolver.py` (replace the whole file), `tests/test_resolver.py` (append)

**Interfaces:**
- Changes only candidate *ordering* and the new constants `MIN_PREFIX_LENGTH=3`, `PREFIX_BASE=0.70`, `PREFIX_SPAN=0.19` plus helper `_prefix_score`. Public signatures (`resolve`, `confirm`, `Resolution`, `Ambiguity`, `Candidate`, `InstrumentIndex`) are unchanged. Auto-accept still uses edit similarity only (>= 0.90 and a 0.05 lead), so a prefix match is never accepted on its own.

- [ ] **Step 1: Append the failing regression tests to `tests/test_resolver.py`**

```python
def _mini_resolver():
    store = DataStore()
    for symbol, name in (("SUNDARAM", "Sundaram Brake Linings Limited"), ("SOUND", "Sound Systems Limited"), ("SUNTV", "Sun TV Network Limited")):
        store.put(Record(EQUITY_DATASET, symbol, NOW, "nse.archives", {"name": name, "isin": "INE000000000"}))
    store.put(Record(ETF_DATASET, "NIFTYBEES", NOW, "nse.archives", {"name": "NIPINDETFNIFTYBEES", "isin": "INF000000000"}))
    return InstrumentResolver(InstrumentIndex.from_store(store, now=NOW))


def test_prefix_matches_rank_ahead_of_look_alikes_and_are_never_auto_accepted():
    result = _mini_resolver().resolve("SUND")
    assert isinstance(result, Ambiguity)  # a prefix is not a name, so it is never accepted on its own
    identifiers = [c.identifier for c in result.candidates]
    assert identifiers[0] == "SUNDARAM"
    assert "SOUND" in identifiers and identifiers.index("SUNDARAM") < identifiers.index("SOUND")


def test_name_prefix_surfaces_the_group_for_a_short_ambiguous_query():
    result = resolver_for_tata().resolve("TATA")
    assert isinstance(result, Ambiguity)
    assert {"TATAMOTORS", "TATASTEEL", "TCS"} <= {c.identifier for c in result.candidates}


def resolver_for_tata():
    return InstrumentResolver(InstrumentIndex.from_store(make_store(), now=NOW))
```

- [ ] **Step 2: Run to verify the new tests fail on the current resolver**

Run: `.venv/Scripts/python -m pytest tests/test_resolver.py -q`
Expected: `2 failed, 29 passed`.

- [ ] **Step 3: Replace `src/athena/resolver.py`**

```python
from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Protocol

from athena.clock import utc_now
from athena.contracts import EmptyRefreshError, UnknownInstrument
from athena.freshness import check_fresh
from athena.isin import isin_checksum_ok, isin_type_hint, looks_like_isin
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET
from athena.routing import route
from athena.store import DataStore
from athena.trading_calendar import TradingCalendar

logger = logging.getLogger("athena.resolver")

ACCEPT_SCORE = 0.90  # proposed defaults; tune on the labeled resolver set
ACCEPT_MARGIN = 0.05
CANDIDATE_FLOOR = 0.55
MODEL_THRESHOLD = 0.85
MAX_CANDIDATES = 5
MIN_PREFIX_LENGTH = 3
PREFIX_BASE = 0.70  # a prefix match scores 0.70-0.89: it ranks first but can never reach ACCEPT_SCORE on its own
PREFIX_SPAN = 0.19
_NAME_STOPWORDS = {"limited", "ltd"}


def normalize_input(text: str) -> str:
    cleaned = " ".join(text.strip().upper().split())
    for prefix in ("NSE:", "BSE:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
    for suffix in (".NS", ".BO"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    return cleaned


def normalize_name(text: str) -> str:
    lowered = text.lower().replace("&", " and ")
    words = re.sub(r"[^a-z0-9 ]", " ", lowered).split()
    return " ".join(word for word in words if word not in _NAME_STOPWORDS)


@dataclass(frozen=True)
class Candidate:
    asset_class: str
    identifier: str
    name: str
    score: float


@dataclass(frozen=True)
class Resolution:
    asset_class: str
    identifier_type: str  # "ticker" | "isin" | "name"
    identifier: str
    name: str
    isin: str
    resolution_path: str  # "exact" | "fuzzy" | "model" | "user_confirmed"
    confidence: float
    candidates: tuple[Candidate, ...]
    routed_specialists: tuple[str, ...]


@dataclass(frozen=True)
class Ambiguity:
    query: str
    candidates: tuple[Candidate, ...]
    reason: str


class Classifier(Protocol):
    def choose(self, query: str, candidates: Sequence[Candidate]) -> tuple[int, float]:
        """Return (index into candidates, probability). Only ever chooses among the given candidates."""
        ...


@dataclass(frozen=True)
class _Entry:
    asset_class: str
    symbol: str
    name: str
    isin: str
    norm_name: str


class InstrumentIndex:
    def __init__(self, entries: Sequence[_Entry]) -> None:
        self.entries = list(entries)
        self.by_symbol: dict[str, _Entry] = {}
        self.by_isin: dict[str, _Entry] = {}
        self.by_name: dict[str, list[_Entry]] = {}
        for entry in self.entries:  # callers pass equities before ETFs so an ETF wins a symbol tie
            self.by_symbol[entry.symbol] = entry
            if entry.isin:
                self.by_isin[entry.isin] = entry
        for entry in self.by_symbol.values():
            self.by_name.setdefault(entry.norm_name, []).append(entry)

    @classmethod
    def from_store(
        cls,
        store: DataStore,
        now: datetime | None = None,
        calendar: TradingCalendar | None = None,
    ) -> InstrumentIndex:
        now = now or utc_now()
        entries: list[_Entry] = []
        for dataset, asset_class in ((EQUITY_DATASET, "equity"), (ETF_DATASET, "etf")):
            records = store.latest_records(dataset)
            if not records:
                raise EmptyRefreshError(f"no {dataset} data; run the NSE master loader first")
            check_fresh(dataset, max(record.as_of for record in records), now, calendar=calendar)
            for record in records:
                name = record.payload["name"]
                entries.append(
                    _Entry(asset_class, record.key, name, record.payload["isin"], normalize_name(name))
                )
        return cls(entries)


def _score(query: str, target: str) -> float:
    if not query or not target:
        return 0.0
    matcher = SequenceMatcher(None, query, target, autojunk=False)
    if matcher.real_quick_ratio() < CANDIDATE_FLOOR or matcher.quick_ratio() < CANDIDATE_FLOOR:
        return 0.0
    return matcher.ratio()


def _prefix_score(symbol_query: str, name_key: str, entry: _Entry) -> float:
    """0.0 unless the query starts the symbol or the name; shorter targets score higher."""
    best = 0.0
    if len(symbol_query) >= MIN_PREFIX_LENGTH and entry.symbol.lower().startswith(symbol_query):
        best = PREFIX_BASE + PREFIX_SPAN * len(symbol_query) / len(entry.symbol)
    if len(name_key) >= MIN_PREFIX_LENGTH and entry.norm_name.startswith(name_key):
        best = max(best, PREFIX_BASE + PREFIX_SPAN * len(name_key) / len(entry.norm_name))
    return best


class InstrumentResolver:
    def __init__(self, index: InstrumentIndex, classifier: Classifier | None = None) -> None:
        self._index = index
        self._classifier = classifier

    def resolve(self, query: str) -> Resolution | Ambiguity:
        text = normalize_input(query)
        if not text:
            raise ValueError("empty query")

        entry = self._index.by_symbol.get(text)
        if entry:
            return self._resolved(entry, "ticker", "exact", 1.0)

        if looks_like_isin(text):
            return self._resolve_isin(text)

        name_key = normalize_name(query)
        matches = self._index.by_name.get(name_key, [])
        if len(matches) == 1:
            return self._resolved(matches[0], "name", "exact", 1.0)
        if len(matches) > 1:
            candidates = tuple(self._candidate(m, 1.0) for m in matches[:MAX_CANDIDATES])
            return Ambiguity(query, candidates, "the name matches more than one instrument")

        return self._resolve_fuzzy(query, text, name_key)

    def confirm(self, ambiguity: Ambiguity, index: int) -> Resolution:
        chosen = ambiguity.candidates[index]
        entry = self._index.by_symbol[chosen.identifier]
        return self._resolved(entry, "ticker", "user_confirmed", 1.0, ambiguity.candidates)

    def _resolve_isin(self, text: str) -> Resolution:
        if not isin_checksum_ok(text):
            raise UnknownInstrument(f"{text} is not a valid ISIN (check digit does not match)")
        entry = self._index.by_isin.get(text)
        if entry:
            return self._resolved(entry, "isin", "exact", 1.0)
        note = ""
        if isin_type_hint(text) == "fund_or_etf":
            note = " It looks like a fund ISIN (INF); mutual-fund support is not available yet."
        raise UnknownInstrument(f"ISIN {text} is not in the NSE equity or ETF lists.{note}")

    def _resolve_fuzzy(self, query: str, text: str, name_key: str) -> Resolution | Ambiguity:
        symbol_query = text.lower()
        scored: list[tuple[float, float, bool, str, _Entry]] = []  # edit, shown, is_prefix, kind, entry
        for entry in self._index.by_symbol.values():
            name_score = _score(name_key, entry.norm_name)
            symbol_score = _score(symbol_query, entry.symbol.lower())
            edit = max(name_score, symbol_score)
            prefix = _prefix_score(symbol_query, name_key, entry)
            shown = max(edit, prefix)
            if shown >= CANDIDATE_FLOOR:
                kind = "name" if name_score >= symbol_score else "ticker"
                scored.append((edit, shown, prefix > 0.0, kind, entry))
        if not scored:
            raise UnknownInstrument(f"no instrument matches {query!r}")

        ranked = sorted(scored, key=lambda i: (not i[2], -i[1], i[4].asset_class != "etf", i[4].symbol))
        candidates = tuple(self._candidate(i[4], i[1]) for i in ranked[:MAX_CANDIDATES])

        by_edit = sorted(scored, key=lambda i: (-i[0], i[4].asset_class != "etf", i[4].symbol))
        top_edit, _, _, top_kind, top_entry = by_edit[0]
        runner_up = by_edit[1][0] if len(by_edit) > 1 else 0.0
        if top_edit >= ACCEPT_SCORE and top_edit - runner_up >= ACCEPT_MARGIN:
            return self._resolved(top_entry, top_kind, "fuzzy", round(top_edit, 4), candidates)

        if self._classifier is not None:
            try:
                index, probability = self._classifier.choose(query, candidates)
            except Exception:
                logger.warning("classifier failed for %r; asking the user instead", query, exc_info=True)
            else:
                if probability >= MODEL_THRESHOLD and 0 <= index < len(candidates):
                    entry = self._index.by_symbol[candidates[index].identifier]
                    return self._resolved(entry, "name", "model", probability, candidates)
        return Ambiguity(query, candidates, "no single instrument is a clear match")

    @staticmethod
    def _candidate(entry: _Entry, score: float) -> Candidate:
        return Candidate(entry.asset_class, entry.symbol, entry.name, round(score, 4))

    def _resolved(
        self,
        entry: _Entry,
        identifier_type: str,
        path: str,
        confidence: float,
        candidates: tuple[Candidate, ...] = (),
    ) -> Resolution:
        return Resolution(
            asset_class=entry.asset_class,
            identifier_type=identifier_type,
            identifier=entry.symbol,
            name=entry.name,
            isin=entry.isin,
            resolution_path=path,
            confidence=confidence,
            candidates=candidates,
            routed_specialists=route(entry.asset_class),
        )
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `244 passed, 36 skipped`.

- [ ] **Step 5: Measure again on the real lists**

Run `.venv/Scripts/python baseline.py` (the script from Task 4, then delete it). Expected: `prefix` roughly 39 safe / 1 missed, `wrong` 0, overall missed about 0.4%.

- [ ] **Step 6: Commit and push**

```bash
rm baseline.py
git add src/athena/resolver.py tests/test_resolver.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: rank prefix matches first among resolver candidates"
git push
```

---

### Task 6: Live evaluation test, TRD update, graph

**Files:**
- Create: `tests/live/test_live_resolver_eval.py`
- Modify: `TRD.md`

- [ ] **Step 1: Write `tests/live/test_live_resolver_eval.py`**

```python
import pytest

from athena.evaluation.resolver_eval import (
    CORRECT,
    HAND_LABELED,
    MISSED,
    SAFE,
    WRONG,
    evaluate_resolver,
    format_report,
    synthetic_cases,
)
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET, NseMasterLoader
from athena.resolver import InstrumentIndex, InstrumentResolver
from athena.store import DataStore

pytestmark = pytest.mark.live


def test_live_resolver_evaluation_meets_the_safety_and_recall_floors():
    store = DataStore()
    loader = NseMasterLoader(store)
    loader.refresh(EQUITY_DATASET)
    loader.refresh(ETF_DATASET)
    index = InstrumentIndex.from_store(store)
    report = evaluate_resolver(InstrumentResolver(index), HAND_LABELED + synthetic_cases(index))
    print("\n" + format_report(report))

    assert report.count(WRONG) == 0, report.wrong_cases  # never silently misroute
    assert report.rate(MISSED) <= 0.02, report.missed_cases[:5]
    for category in report.by_category:
        floor = 0.85 if category == "prefix" else 0.95
        assert report.category_rate(category, CORRECT, SAFE) >= floor, (category, report.by_category[category])
```

- [ ] **Step 2: Run the default and live suites**

```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m pytest --live tests/live/test_live_resolver_eval.py tests/live/test_live_resolver.py -q -s
```

Expected: `244 passed, 37 skipped`, then `28 passed` with the printed evaluation table. The synthetic cases are seeded but drawn from the day's NSE lists, so counts can shift slightly; a `wrong` result is never acceptable: investigate it before changing anything.

- [ ] **Step 3: Update `TRD.md`**

1. In §2.12, replace the sentence beginning `Candidate ranking is by edit similarity and is known to be noisy` through the end of that sentence with: `Candidates whose symbol or name starts with the query (3+ characters) are ranked first but can never be auto-accepted; auto-accept needs an edit similarity of at least 0.90 with a 0.05 lead (prefix queries went from 22 to 39 of 40 with the right instrument offered, with no wrong answers).`
2. In §2.14, append this paragraph at the end of the section: `*Implemented in Plan 0e:* number-grounding validator, specialist-output and judge-verdict validators, abstention and order-invariance checks (callable-based, so they apply to any specialist or judge), and a resolver evaluation that scores correct / safe / wrong / missed (wrong must stay at zero). Not yet implemented: golden sets (they need the Phase 1 specialists), the portability test (needs a named second runtime), and the optional multi-model rubric grader.`
3. Revision history, add at the top of the list: `- **Oct 5, 2026 (Phase 0e)** — Evaluation harness implemented; resolver prefix ranking fixed (see docs/superpowers/plans/2026-10-05-phase-0e-evaluation-harness.md).`

- [ ] **Step 4: Commit, push, refresh graph**

```bash
git add tests/live/test_live_resolver_eval.py TRD.md
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "test: add live resolver evaluation; document evaluation harness in TRD"
git push
.venv/Scripts/python -m graphify update .
```

---

## Self-Review (completed)

**Spec coverage (TRD §2.14 -> task):** schema validation of specialist and judge outputs (Task 2); number-grounding validator (Task 1); resolver test set with clean tickers, ISINs, name variants, ambiguity (Tasks 4-6; 20 hand-labeled plus seeded synthetic, short of the 100-200 target, which is stated as the limitation); judge stability under reordering (Task 3); abstention test (Task 3). Not in this plan: golden sets and the portability test (Phase 1 prerequisites), the optional multi-model grader.

**Placeholder scan:** none; every code block is the file that passed the prototype run.

**Type consistency:** `validate_specialist_output`, `check_abstention`, `ResolverCase`/`judge_case`/`evaluate_resolver`, and the resolver constants match across Tasks 1-6. Test totals: 194 (after 0d) + 12 + 21 + 6 + 9 + 2 = 244 passed; skipped 36 + 1 live = 37.

**Verified before writing:** all offline tests passed in a scratch copy (244); the two ranking regression tests fail on the old resolver; the 9 evaluation tests pass on both old and new resolver (so Task 4 precedes Task 5 safely); the live evaluation and the 27 labeled live cases passed with the new ranking.
