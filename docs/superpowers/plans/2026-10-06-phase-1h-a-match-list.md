# Phase 1h-a — Full Match List and Click-to-Analyse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a search matches several instruments, show every match (up to 50) instead of five, and let the person click a row to analyse that instrument without retyping.

**Architecture:** The resolver keeps two lists: the long ranked list (`MAX_LISTED = 50`) that goes into `Ambiguity.candidates` for people to read, and the short top-five (`MAX_CANDIDATES = 5`) that the model classifier and `Resolution.candidates` still use, so the evaluation harness and its prompts are unchanged. Text reports (the orchestrator report and the backtest command) print ten and say how many more. The dashboard shows the whole list as a selectable table; a click stores the pick in a pending session key, which the next run applies to the search box before the box is created (Streamlit forbids changing a widget's value after it exists) and the analysis starts.

**Tech Stack:** Python >= 3.11, pytest, Streamlit 1.65 (`st.dataframe(..., on_select="rerun")`; `AppTest` can drive a selection by setting the widget's session-state key). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-06-phase-1h-research-tools-design.md` part 1h-a; `TRD.md` §2.12.

**Plan series:** 0a-0e, 1a-1g (done) -> **1h-a (this plan)** -> 1h-b (indicator registry and chart controls) -> 1h-c (strategy format, engine support, rule builder) -> 1h-d (text to strategy) -> risk overlay and investor profile -> later phases.

**Suggested models:** Sonnet at medium effort for the implementers (every step carries complete code or a verified edit script), Sonnet for reviewers, Opus for the final review. Prototyped in a scratch copy first: 567 offline tests passed (555 existing + 12 new), the live resolver checks passed on the real NSE lists, 12 deliberate mutations were each caught, and the edit scripts below were re-run on a clean copy of the repository to prove they apply and pass.

## Verified findings (6 Oct 2026)

- The cap was `MAX_CANDIDATES = 5` (`src/athena/resolver.py:26`), used for the exact-name path and the fuzzy path alike, so a query such as "TATA" showed five of the many Tata listings.
- `AppTest` can drive a dataframe row selection: set `app.session_state["<table key>"] = {"selection": {"rows": [i], "columns": [], "cells": []}}` and call `app.run()`. Verified on Streamlit 1.65.
- A dataframe's selection state is dropped when the table is not drawn in a run, so after a click the analysis page replaces the list and a later ambiguous search starts with nothing selected. A test pins this (`test_a_later_ambiguous_search_does_not_repeat_the_old_click`).
- The resolver evaluation (`evaluate_resolver`) counted a "safe" answer when the right instrument was anywhere in the candidates. With a 50-long list that would inflate the score, so it now looks at the top five only, and its results do not change.

**Honest limits:**
- The ranking is unchanged; a very short query ("S") still produces a noisy list. The minimum for prefix matching is three characters (`MIN_PREFIX_LENGTH`).
- The click works in the Streamlit page only. The terminal text reports cap at ten with "...and N more; type the exact symbol", because a click is not possible there.
- The row-selection event cannot be pressed with a mouse in `AppTest`; the live check below was done by setting the selection state, and a person should click once on the real page to confirm (the controller does this before closing the phase).

## Global Constraints

- No network in the default test run; the live test is opt-in via `--live` and needs no API key.
- The model classifier and `Resolution.candidates` keep seeing at most `MAX_CANDIDATES = 5` candidates.
- Files in the repository use LF; no new dependency; match the surrounding code's comment density.
- Commit with the GitHub no-reply identity: `git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit ...` and end each commit message with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; `git push` after each task.
- Windows, Git Bash: run Python as `.venv/Scripts/python`. The helper scripts in this plan live outside the repository (save them under `$TEMP`), run from the repository root, and never inside it.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/athena/resolver.py` | modified: `MAX_LISTED`, `CLI_SHOWN`, `more_candidates_line`, the long list in `Ambiguity` |
| `src/athena/evaluation/resolver_eval.py` | modified: judges the top five of the ranking |
| `src/athena/orchestrator/report.py`, `src/athena/backtest/cli.py` | modified: print ten candidates and say how many more |
| `src/athena/dashboard/app.py` | modified: selectable table, pending-pick key, text box bound to a key |
| `tests/test_resolver.py`, `test_orchestrator_report.py`, `test_backtest_cli.py`, `test_resolver_eval.py`, `test_dashboard_app.py`, `tests/dash_fakes.py` | modified |
| `tests/live/test_live_resolver.py` | modified: one live check |
| `TRD.md` | modified: §2.12 and the revision history |

---

### Task 1: A long candidate list in the resolver and the text reports

**Files:**
- Modify (by the two scripts below): `src/athena/resolver.py`, `src/athena/evaluation/resolver_eval.py`, `src/athena/orchestrator/report.py`, `src/athena/backtest/cli.py`, `tests/test_resolver.py`, `tests/test_orchestrator_report.py`, `tests/test_backtest_cli.py`, `tests/test_resolver_eval.py`

**Interfaces:**
- Produces: `resolver.MAX_LISTED = 50`, `resolver.CLI_SHOWN = 10`, `resolver.more_candidates_line(total, shown) -> str | None` (returns `"  ...and N more; type the exact symbol."` when `total > shown`); `Ambiguity.candidates` holds up to `MAX_LISTED` candidates, best first; `Resolution.candidates` and the classifier input stay at most `MAX_CANDIDATES`.
- Consumes: existing `InstrumentResolver`, `Candidate`, `Ambiguity`, `Resolution`.

- [ ] **Step 1: Add the failing tests**

Save as `$TEMP/s1ha_tests_resolver.py` and run `.venv/Scripts/python $TEMP/s1ha_tests_resolver.py` from the repository root. It appends tests to `tests/test_resolver.py`, `tests/test_orchestrator_report.py`, `tests/test_backtest_cli.py` and `tests/test_resolver_eval.py` and changes the import list at the top of `tests/test_resolver.py`; any `AssertionError` means the file differs from what the script expects: stop and report.

```python
import pathlib


def edit(path, pairs, append=""):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t + append, encoding="utf-8", newline="\n")


# ---- tests/test_resolver.py
edit(
    "tests/test_resolver.py",
    [
        (
            "from athena.resolver import (\n    Ambiguity,\n    Candidate,\n    InstrumentIndex,\n    InstrumentResolver,\n    Resolution,\n    normalize_input,\n    normalize_name,\n)\n",
            "from athena.resolver import (\n    MAX_CANDIDATES,\n    MAX_LISTED,\n    Ambiguity,\n    Candidate,\n    InstrumentIndex,\n    InstrumentResolver,\n    Resolution,\n    more_candidates_line,\n    normalize_input,\n    normalize_name,\n)\n",
        ),
    ],
    append='''

def many_listings(count, name="Alpha Industries {n:03d} Limited"):
    """A master list where `count` equities share a name stem, plus the one ETF the index requires."""
    store = DataStore()
    for n in range(count):
        store.put(Record(EQUITY_DATASET, f"ALPHA{n:03d}", NOW, "nse.archives", {"name": name.format(n=n), "series": "EQ", "isin": "INE000000000", "listing_date": "x"}))
    store.put(Record(ETF_DATASET, "NIFTYBEES", NOW, "nse.archives", {"name": "NIPINDETFNIFTYBEES", "isin": "INF204KB14I2", "underlying": "u", "underlying_class": "EQUITY", "underlying_key": "k"}))
    return store


def test_a_short_query_lists_every_match_up_to_the_listing_limit():
    twelve = InstrumentResolver(InstrumentIndex.from_store(many_listings(12), now=NOW)).resolve("alpha")
    assert isinstance(twelve, Ambiguity) and len(twelve.candidates) == 12 > MAX_CANDIDATES
    sixty = InstrumentResolver(InstrumentIndex.from_store(many_listings(60), now=NOW)).resolve("alpha")
    assert isinstance(sixty, Ambiguity) and len(sixty.candidates) == MAX_LISTED == 50
    scores = [c.score for c in sixty.candidates]
    assert scores == sorted(scores, reverse=True)  # still ranked, best first


def test_a_name_shared_by_many_listings_lists_them_all_up_to_the_limit():
    shared = InstrumentResolver(InstrumentIndex.from_store(many_listings(55, name="Shared Name Limited"), now=NOW))
    result = shared.resolve("Shared Name Limited")
    assert isinstance(result, Ambiguity) and len(result.candidates) == MAX_LISTED
    assert result.reason == "the name matches more than one instrument"


def test_the_model_classifier_and_a_resolution_still_see_only_the_top_five():
    fake = FakeClassifier(index=0, probability=0.9)
    result = InstrumentResolver(InstrumentIndex.from_store(many_listings(60), now=NOW), fake).resolve("alpha")
    assert isinstance(result, Resolution) and result.resolution_path == "model"
    assert len(fake.seen[0][1]) == MAX_CANDIDATES and len(result.candidates) == MAX_CANDIDATES


def test_confirm_accepts_any_candidate_on_the_long_list():
    resolver = InstrumentResolver(InstrumentIndex.from_store(many_listings(60), now=NOW))
    ambiguity = resolver.resolve("alpha")
    chosen = resolver.confirm(ambiguity, 30)
    assert chosen.identifier == ambiguity.candidates[30].identifier and chosen.resolution_path == "user_confirmed"


def test_a_text_list_says_how_many_more_there_are():
    assert more_candidates_line(25, 10) == "  ...and 15 more; type the exact symbol."
    assert more_candidates_line(10, 10) is None and more_candidates_line(3, 3) is None
''',
)

# ---- tests/test_orchestrator_report.py
edit(
    "tests/test_orchestrator_report.py",
    [],
    append='''

def test_the_report_shows_ten_candidates_and_says_how_many_more():
    candidates = tuple(Candidate("equity", f"SYM{n:02d}", f"Name {n}", 0.8) for n in range(25))
    ambiguity = Ambiguity("sy", candidates, "several matches")
    text = format_result(OrchestrationResult(NEEDS_CLARIFICATION, "sy", None, ambiguity, {}, {}, None, None, None, ()))
    assert "10. SYM09" in text and "11. SYM10" not in text
    assert "  ...and 15 more; type the exact symbol." in text and "Re-run with the exact symbol." in text


def test_the_report_has_no_more_line_when_everything_fits():
    candidates = tuple(Candidate("equity", f"SYM{n:02d}", f"Name {n}", 0.8) for n in range(10))
    ambiguity = Ambiguity("sy", candidates, "several matches")
    text = format_result(OrchestrationResult(NEEDS_CLARIFICATION, "sy", None, ambiguity, {}, {}, None, None, None, ()))
    assert "10. SYM09" in text and "...and" not in text
''',
)

# ---- tests/test_backtest_cli.py
edit(
    "tests/test_backtest_cli.py",
    [],
    append='''

def test_an_ambiguous_backtest_query_prints_ten_candidates_and_says_how_many_more():
    from dataclasses import replace

    store = DataStore()
    for n in range(25):
        store.put(Record(EQUITY_DATASET, f"ALPHA{n:02d}", NOW, "x", {"name": f"Alpha Industries {n:02d} Limited", "isin": "INE000000000"}))
    store.put(Record(ETF_DATASET, "NIFTYBEES", NOW, "x", {"name": "NIPINDETFNIFTYBEES", "isin": "INF000000000"}))
    many = replace(world(), resolver=InstrumentResolver(InstrumentIndex.from_store(store, now=NOW)))
    text, code = analyze(many, "alpha", ["trend"])
    assert code == 2 and "ALPHA09" in text and "ALPHA10" not in text
    assert "...and 15 more; type the exact symbol." in text
''',
)

# ---- tests/test_resolver_eval.py
edit(
    "tests/test_resolver_eval.py",
    [],
    append='''

def test_the_evaluation_credits_a_safe_answer_only_when_it_is_near_the_top_of_the_ranking():
    twelve = make_resolver([(f"ALPHA{n:02d}", f"Alpha Industries {n:02d} Limited") for n in range(12)])

    def judged(symbol):
        return judge_case(twelve, ResolverCase("alpha", "t", RESOLVE, "equity", symbol))[0]

    assert judged("ALPHA00") == SAFE  # in the top five
    assert judged("ALPHA11") == MISSED  # the person would see it on the long list, but the ranking did not put it near the top
''',
)
print("resolver, report, backtest and evaluation tests added")
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_resolver.py tests/test_orchestrator_report.py tests/test_backtest_cli.py tests/test_resolver_eval.py -q`
Expected: a collection error `ImportError: cannot import name 'MAX_LISTED' from 'athena.resolver'`.

- [ ] **Step 3: Apply the source edits**

Save as `$TEMP/s1ha_source_resolver.py` and run it from the repository root; it edits `src/athena/resolver.py`, `src/athena/evaluation/resolver_eval.py`, `src/athena/orchestrator/report.py` and `src/athena/backtest/cli.py` (each edit asserts its target text is present exactly once).

```python
import pathlib


def edit(path, pairs, append=""):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t + append, encoding="utf-8", newline="\n")


# ---- resolver: a long list for the user, the same short list for the model classifier
edit(
    "src/athena/resolver.py",
    [
        (
            "MAX_CANDIDATES = 5\n",
            "MAX_CANDIDATES = 5  # what the model classifier and `Resolution.candidates` see\n"
            "MAX_LISTED = 50  # the most candidates a person is shown when a query is ambiguous\n"
            "CLI_SHOWN = 10  # how many a text report prints before saying how many more there are\n",
        ),
        (
            "            candidates = tuple(self._candidate(m, 1.0) for m in matches[:MAX_CANDIDATES])\n"
            "            return Ambiguity(query, candidates, \"the name matches more than one instrument\")",
            "            candidates = tuple(self._candidate(m, 1.0) for m in matches[:MAX_LISTED])\n"
            "            return Ambiguity(query, candidates, \"the name matches more than one instrument\")",
        ),
        (
            "        candidates = tuple(self._candidate(i[4], i[1]) for i in ranked[:MAX_CANDIDATES])\n",
            "        listed = tuple(self._candidate(i[4], i[1]) for i in ranked[:MAX_LISTED])\n"
            "        candidates = listed[:MAX_CANDIDATES]\n",
        ),
        (
            "        return Ambiguity(query, candidates, \"no single instrument is a clear match\")",
            "        return Ambiguity(query, listed, \"no single instrument is a clear match\")",
        ),
        (
            "def normalize_input(text: str) -> str:",
            "def more_candidates_line(total: int, shown: int) -> str | None:\n"
            "    \"\"\"The closing line of a text list that was cut short, or None when everything was shown.\"\"\"\n"
            "    return f\"  ...and {total - shown} more; type the exact symbol.\" if total > shown else None\n"
            "\n"
            "\n"
            "def normalize_input(text: str) -> str:",
        ),
    ],
)

# ---- the evaluation measures the ranking the model and the first screen of a list would show, not a 50-long list
edit(
    "src/athena/evaluation/resolver_eval.py",
    [
        (
            "    candidates = [(c.asset_class, c.identifier) for c in result.candidates]\n",
            "    candidates = [(c.asset_class, c.identifier) for c in result.candidates[:MAX_CANDIDATES]]  # the top of the ranking\n",
        ),
        (
            "from athena.resolver import InstrumentIndex, InstrumentResolver, Resolution\n",
            "from athena.resolver import MAX_CANDIDATES, InstrumentIndex, InstrumentResolver, Resolution\n",
        ),
    ],
)

# ---- text reports
edit(
    "src/athena/orchestrator/report.py",
    [
        (
            "from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, NO_VIEW, OrchestrationResult\n",
            "from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, NO_VIEW, OrchestrationResult\nfrom athena.resolver import CLI_SHOWN, more_candidates_line\n",
        ),
        (
            "        lines += [\n"
            "            f\"  {i}. {c.identifier}  {c.name}  ({c.asset_class}, match {c.score:.2f})\"\n"
            "            for i, c in enumerate(result.ambiguity.candidates, 1)\n"
            "        ]\n",
            "        shown = result.ambiguity.candidates[:CLI_SHOWN]\n"
            "        lines += [\n"
            "            f\"  {i}. {c.identifier}  {c.name}  ({c.asset_class}, match {c.score:.2f})\" for i, c in enumerate(shown, 1)\n"
            "        ]\n"
            "        more = more_candidates_line(len(result.ambiguity.candidates), len(shown))\n"
            "        if more:\n"
            "            lines.append(more)\n",
        ),
    ],
)
edit(
    "src/athena/backtest/cli.py",
    [
        (
            "from athena.resolver import Ambiguity, InstrumentIndex, InstrumentResolver\n",
            "from athena.resolver import CLI_SHOWN, Ambiguity, InstrumentIndex, InstrumentResolver, more_candidates_line\n",
        ),
        (
            "        lines += [f\"  {c.identifier}  {c.name}  ({c.asset_class})\" for c in resolved.candidates]\n",
            "        shown = resolved.candidates[:CLI_SHOWN]\n"
            "        lines += [f\"  {c.identifier}  {c.name}  ({c.asset_class})\" for c in shown]\n"
            "        more = more_candidates_line(len(resolved.candidates), len(shown))\n"
            "        if more:\n"
            "            lines.append(more)\n",
        ),
    ],
)
print("resolver, evaluation and text reports edited")
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_resolver.py tests/test_orchestrator_report.py tests/test_backtest_cli.py tests/test_resolver_eval.py -q`
Expected: all pass. Then `.venv/Scripts/python -m pyflakes src tests` prints nothing, and the whole suite `.venv/Scripts/python -m pytest -q` shows `564 passed, 60 skipped` (555 existing plus 9 new in this task).

- [ ] **Step 5: Mutation check**

Save the helper below as `$TEMP/mutate_1ha.py` (used again in Task 2). Run `.venv/Scripts/python $TEMP/mutate_1ha.py resolver report backtest eval` from the repository root. Every line must start with `CAUGHT`; `SURVIVED`, `ERROR` or `NOT FOUND` means a test or the transcribed code is wrong. Files are restored automatically.

```python
import pathlib
import subprocess
import sys

PY = sys.executable
RESOLVER, EVAL, REPORT, BCLI, APP = (
    "src/athena/resolver.py",
    "src/athena/evaluation/resolver_eval.py",
    "src/athena/orchestrator/report.py",
    "src/athena/backtest/cli.py",
    "src/athena/dashboard/app.py",
)
TESTS = ["tests/test_resolver.py", "tests/test_orchestrator_report.py", "tests/test_backtest_cli.py",
         "tests/test_resolver_eval.py", "tests/test_dashboard_app.py"]

MUTATIONS = [
    ("resolver: a shared name is cut to five", RESOLVER, "for m in matches[:MAX_LISTED])", "for m in matches[:MAX_CANDIDATES])"),
    ("resolver: the fuzzy list is cut to five", RESOLVER, 'return Ambiguity(query, listed, "no single instrument is a clear match")', 'return Ambiguity(query, candidates, "no single instrument is a clear match")'),
    ("resolver: the classifier sees the whole list", RESOLVER, "candidates = listed[:MAX_CANDIDATES]", "candidates = listed"),
    ("resolver: the list limit is lost", RESOLVER, "for i in ranked[:MAX_LISTED])", "for i in ranked)"),
    ("resolver: no more-line when exactly full", RESOLVER, "if total > shown else None", "if total >= shown else None"),
    ("report: everything is printed", REPORT, "shown = result.ambiguity.candidates[:CLI_SHOWN]", "shown = result.ambiguity.candidates"),
    ("backtest cli: everything is printed", BCLI, "shown = resolved.candidates[:CLI_SHOWN]", "shown = resolved.candidates"),
    ("eval: credits the whole long list", EVAL, "for c in result.candidates[:MAX_CANDIDATES]]", "for c in result.candidates]"),
    ("app: a click does not rerun", APP, "            st.rerun()\n        return", "            pass\n        return"),
    ("app: a click always picks the first row", APP, "view.candidates[event.selection.rows[0]].identifier", "view.candidates[0].identifier"),
    ("app: the pick is applied after the box exists", APP,
     '    picked = st.session_state.pop(PICK_KEY, None)  # applied before the box exists: Streamlit forbids changing it after\n    if picked:\n        st.session_state[QUERY_KEY] = picked\n    query = st.text_input("Ticker, ISIN or name", placeholder=PLACEHOLDER, key=QUERY_KEY).strip()\n',
     '    query = st.text_input("Ticker, ISIN or name", placeholder=PLACEHOLDER, key=QUERY_KEY).strip()\n    picked = st.session_state.pop(PICK_KEY, None)\n    if picked:\n        st.session_state[QUERY_KEY] = picked\n'),
    ("app: the list has no count line", APP, 'st.caption(f"{len(view.candidates)} matches. Click a row to analyse it.")', "pass"),
]

only = sys.argv[1:]
for name, path, old, new in MUTATIONS:
    if only and not any(name.startswith(o) for o in only):
        continue
    p = pathlib.Path(path)
    original = p.read_bytes()
    text = original.decode("utf-8").replace("\r\n", "\n")
    if old not in text:
        print("NOT FOUND", name)
        continue
    p.write_bytes(text.replace(old, new, 1).encode("utf-8"))
    try:
        run = subprocess.run([PY, "-m", "pytest", *TESTS, "-q", "-x", "-p", "no:cacheprovider"], capture_output=True, text=True)
        tail = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else run.stderr[-200:]
        print(("CAUGHT  " if run.returncode == 1 else "ERROR   " if run.returncode else "SURVIVED"), name, "|", tail)
    finally:
        p.write_bytes(original)
```

Expected: 8 lines, all `CAUGHT`.

- [ ] **Step 6: Commit**

```bash
git add src tests
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: list up to 50 matches for an ambiguous query; the model classifier still sees five" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 2: Click a candidate to analyse it

**Files:**
- Modify (by the two scripts below): `src/athena/dashboard/app.py`, `tests/dash_fakes.py`, `tests/test_dashboard_app.py`

**Interfaces:**
- Produces: the search box is bound to the session key `query` (`app.QUERY_KEY`); a table click stores the identifier under `picked_instrument` (`app.PICK_KEY`) and reruns; the next run copies it into `query` before the box is created; `render()` draws `"{n} matches. Click a row to analyse it."` and a selectable table (`key="candidate_table"`, single row) for an ambiguous view. Test helpers: `dash_fakes.ambiguous_result(count=1)` (the first candidate is `SBIN`, the others `SBI01`, `SBI02`, ...), `dash_fakes.RoutingService({query: view})`.
- Consumes: Task 1's long `Ambiguity.candidates` (the view already carries every candidate; no change in `view.py`).

- [ ] **Step 1: Add the failing tests**

Save as `$TEMP/s1ha_tests_page.py` and run it from the repository root. It edits `tests/dash_fakes.py` and `tests/test_dashboard_app.py`.

```python
import pathlib


def edit(path, pairs, append=""):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t + append, encoding="utf-8", newline="\n")


# ---- tests/dash_fakes.py: a many-candidate ambiguity and a service that has one view per query
edit(
    "tests/dash_fakes.py",
    [
        (
            'def ambiguous_result():\n    ambiguity = Ambiguity("sbi", (Candidate("equity", "SBIN", "State Bank of India", 0.81),), "several matches")\n',
            'def ambiguous_result(count=1):\n    candidates = (Candidate("equity", "SBIN", "State Bank of India", 0.81),) + tuple(\n        Candidate("equity", f"SBI{n:02d}", f"SBI Holding {n}", 0.7) for n in range(1, count)\n    )\n    ambiguity = Ambiguity("sbi", candidates, "several matches")\n',
        ),
        (
            "CURRENT = {\"service\": FakeService(full_view())}\n",
            "class RoutingService(FakeService):\n    \"\"\"A FakeService with one canned view per query, so a page can be driven through several searches.\"\"\"\n\n"
            "    def __init__(self, views):\n        super().__init__()\n        self.views = views\n\n"
            "    def view(self, query):\n        self.queries.append(query)\n        return self.views[query]\n\n\n"
            "CURRENT = {\"service\": FakeService(full_view())}\n",
        ),
    ],
)

# ---- tests/test_dashboard_app.py
edit(
    "tests/test_dashboard_app.py",
    [
        (
            "from dash_fakes import FakeService, ambiguous_result, athena_error, full_view, sample_backtest_view\n",
            "from dash_fakes import FakeService, RoutingService, ambiguous_result, athena_error, full_view, sample_backtest_view\n",
        ),
    ],
    append='''

def many():
    return build_view(ambiguous_result(12))


def click(app, row):
    app.session_state["candidate_table"] = {"selection": {"rows": [row], "columns": [], "cells": []}}
    app.run()


def test_every_candidate_is_listed_and_the_page_says_to_click_a_row():
    app = open_app(FakeService(many()), "sbi")
    assert not app.exception and len(app.dataframe[0].value) == 12
    assert any("12 matches. Click a row to analyse it." in c for c in texts(app.caption))


def test_clicking_a_candidate_fills_the_search_box_and_analyses_that_instrument():
    service = RoutingService({"sbi": many(), "SBI02": full_view()})
    app = open_app(service, "sbi")
    click(app, 2)
    assert not app.exception and app.text_input[0].value == "SBI02"
    assert service.queries == ["sbi", "SBI02"]
    assert len(app.metric) == 2 and not app.warning  # the verdict page replaced the ambiguity warning and list


def test_a_later_ambiguous_search_does_not_repeat_the_old_click():
    service = RoutingService({"sbi": many(), "SBIN": full_view(), "tat": many()})
    app = open_app(service, "sbi")
    click(app, 0)
    assert service.queries == ["sbi", "SBIN"]
    app.text_input[0].set_value("tat").run()
    assert not app.exception and service.queries == ["sbi", "SBIN", "tat"]
    assert len(app.dataframe) == 1 and not app.metric  # the list is shown again and nothing was picked for the user
''',
)
print("dashboard fixtures and tests added")
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_app.py -q`
Expected: 3 failures (the list has no count line, a click does nothing, a later search is not checked yet) and the existing tests still passing.

- [ ] **Step 3: Apply the source edit**

Save as `$TEMP/s1ha_source_page.py` and run it from the repository root; it edits `src/athena/dashboard/app.py`.

```python
import pathlib


def edit(path, pairs, append=""):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t + append, encoding="utf-8", newline="\n")


# ---- the page: every candidate, a click picks one
edit(
    "src/athena/dashboard/app.py",
    [
        (
            'PLACEHOLDER = "SBIN"\n',
            'PLACEHOLDER = "SBIN"\nQUERY_KEY = "query"  # the search box\nPICK_KEY = "picked_instrument"  # a table click waiting to be moved into the search box\n',
        ),
        (
            "        st.dataframe(\n"
            "            [{\"symbol\": c.identifier, \"name\": c.name, \"class\": c.asset_class, \"match\": round(c.score, 2)} for c in view.candidates],\n"
            "            hide_index=True, width=\"stretch\",\n"
            "        )\n"
            "        return\n",
            "        st.caption(f\"{len(view.candidates)} matches. Click a row to analyse it.\")\n"
            "        event = st.dataframe(\n"
            "            [{\"symbol\": c.identifier, \"name\": c.name, \"class\": c.asset_class, \"match\": round(c.score, 2)} for c in view.candidates],\n"
            "            hide_index=True, width=\"stretch\", on_select=\"rerun\", selection_mode=\"single-row\", key=\"candidate_table\",\n"
            "        )\n"
            "        if event.selection.rows:\n"
            "            st.session_state[PICK_KEY] = view.candidates[event.selection.rows[0]].identifier\n"
            "            st.rerun()\n"
            "        return\n",
        ),
        (
            "    st.title(\"Athena\")\n"
            "    query = st.text_input(\"Ticker, ISIN or name\", placeholder=PLACEHOLDER).strip()\n",
            "    st.title(\"Athena\")\n"
            "    picked = st.session_state.pop(PICK_KEY, None)  # applied before the box exists: Streamlit forbids changing it after\n"
            "    if picked:\n"
            "        st.session_state[QUERY_KEY] = picked\n"
            "    query = st.text_input(\"Ticker, ISIN or name\", placeholder=PLACEHOLDER, key=QUERY_KEY).strip()\n",
        ),
    ],
)
print("dashboard page edited")
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_app.py -q` (all pass), then `.venv/Scripts/python -m pyflakes src tests` (prints nothing), then the whole suite `.venv/Scripts/python -m pytest -q`: expect `567 passed, 60 skipped`.

- [ ] **Step 5: Mutation check**

Run: `.venv/Scripts/python $TEMP/mutate_1ha.py app`
Expected: 4 lines, all `CAUGHT`.

- [ ] **Step 6: Commit**

```bash
git add src tests
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: click a candidate on the dashboard to analyse it" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 3: Live check and TRD

**Files:**
- Modify: `tests/live/test_live_resolver.py` (append one test), `TRD.md` (by the script below)

- [ ] **Step 1: Add the live test**

Append to the end of `tests/live/test_live_resolver.py`:

```python
def test_live_short_query_lists_more_than_five_candidates_and_any_of_them_can_be_confirmed(resolver):
    result = resolver.resolve("TATA")
    assert isinstance(result, Ambiguity) and 5 < len(result.candidates) <= 50
    chosen = resolver.confirm(result, len(result.candidates) - 1)
    assert chosen.identifier == result.candidates[-1].identifier and chosen.resolution_path == "user_confirmed"
```

- [ ] **Step 2: Run it against the real NSE lists**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest tests/live/test_live_resolver.py --live -q`
Expected: `28 passed` (27 existing cases plus this one). No API key is needed. If NSE is unreachable the fixture fails with the fetch error: that is an environment problem; report it instead of editing the test.

- [ ] **Step 3: Record it in the TRD**

Save the script below as `$TEMP/trd_1ha.py` and run `.venv/Scripts/python $TEMP/trd_1ha.py` from the repository root. It prints `trd_1ha applied to ...`; an `AssertionError` means the TRD text differs from what the script expects: stop and report.

```python
import pathlib
import sys

path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "TRD.md")
raw = path.read_bytes().decode("utf-8")
crlf = "\r\n" in raw
t = raw.replace("\r\n", "\n")


def swap(old, new):
    global t
    assert t.count(old) == 1, old[:70]
    t = t.replace(old, new)


swap(
    "Otherwise the resolver returns the candidate list and asks the user. The model classifier may be Jev",
    "Otherwise the resolver returns the candidate list and asks the user. The list holds up to 50 ranked candidates (`MAX_LISTED`); the model classifier and `Resolution.candidates` see only the top 5 (`MAX_CANDIDATES`), text reports print 10 and say how many more there are, and the dashboard shows every candidate and analyses the one the user clicks. The model classifier may be Jev",
)
swap(
    "- **Oct 6, 2026 (Phase 1g)**",
    "- **Oct 6, 2026 (Phase 1h-a)** — Ambiguous queries now list up to 50 candidates instead of 5, and the dashboard analyses the row the user clicks (design: docs/superpowers/specs/2026-10-06-phase-1h-research-tools-design.md; plan: docs/superpowers/plans/2026-10-06-phase-1h-a-match-list.md). The resolver evaluation still judges only the top 5 of the ranking, so its scores are unchanged.\n- **Oct 6, 2026 (Phase 1g)**",
)
if crlf:
    t = t.replace("\n", "\r\n")
path.write_bytes(t.encode("utf-8"))
print("trd_1ha applied to", path)
```

- [ ] **Step 4: Final verification**

Run the whole suite `.venv/Scripts/python -m pytest -q` (expect `567 passed, 61 skipped`: the new live test is skipped without `--live`), `.venv/Scripts/python -m pyflakes src tests` (prints nothing) and `git status --short` (only `TRD.md` and `tests/live/test_live_resolver.py`).

- [ ] **Step 5: Commit**

```bash
git add TRD.md tests/live/test_live_resolver.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "test: add a live long-list check; record the match list in the TRD" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```
