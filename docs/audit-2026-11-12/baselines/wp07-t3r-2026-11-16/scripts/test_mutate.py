"""Tests of the mutation harness itself (T3-C2). A synthetic runner writes vitest-shaped JSON reports for an INERT target file; no application code is mutated and no real test suite is run.

    python -m pytest test_mutate.py -q

The acceptance list of the review: a passing baseline, then a mutated syntax / transform / import / setup error, no tests, a missing report, an unexplained non-zero exit, a timeout, a real assertion
failure and a surviving mutant, and a mixed run (assertions plus a suite that did not load) that must not be called an unqualified kill."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutate  # noqa: E402

FAKE = r'''
import json, os, sys, time
args = sys.argv[1:]
out = next((a.split("=", 1)[1] for a in args if a.startswith("--outputFile.json=")), None)
scenario = open(os.environ["FAKE_TARGET"], encoding="utf-8").read().split("SCENARIO:")[1].split()[0]
def t(title, status="passed", msg=None):
    return {"fullName": title, "title": title, "status": status, "failureMessages": [msg] if msg else []}
def suite(name, tests, status=None, message=""):
    status = status or ("failed" if any(x["status"] == "failed" for x in tests) else "passed")
    return {"name": "/x/terminal/web/test/" + name, "status": status, "message": message, "assertionResults": tests}
def doc(suites, **over):
    tests = [a for s in suites for a in s["assertionResults"]]
    n = lambda st: sum(1 for a in tests if a["status"] == st)
    d = {"numTotalTests": len(tests), "numPassedTests": n("passed"), "numFailedTests": n("failed"), "numPendingTests": n("skipped"),
         "numTotalTestSuites": len(suites), "numFailedTestSuites": sum(1 for s in suites if s["status"] == "failed"), "success": n("failed") == 0, "testResults": suites}
    d.update(over)
    return d
A = lambda *ts: suite("A.test.ts", list(ts))
B = lambda *ts: suite("B.test.ts", list(ts))
ASSERT = "AssertionError: expected 1 to be 2"
code, report, stdout = 0, None, ""
if scenario in ("ok", "survive"):
    report = doc([A(t("t1"), t("t2")), B(t("t3"))])
elif scenario == "assert":
    report, code = doc([A(t("t1", "failed", ASSERT), t("t2")), B(t("t3"))]), 1
elif scenario == "syntax":
    report, code, stdout = doc([suite("A.test.ts", [], "failed", "SyntaxError: deliberate synthetic load failure"), suite("B.test.ts", [], "failed", "SyntaxError")]), 1, "FAIL test/synthetic.test.ts\nSyntaxError: deliberate synthetic load failure\nTests no tests\n"
elif scenario == "import":
    report, code, stdout = doc([suite("A.test.ts", [], "failed", "Failed to resolve import ./gone"), B(t("t3"))]), 1, "Failed to resolve import\n"
elif scenario == "mixed":
    report, code = doc([suite("A.test.ts", [], "failed", "Error: setup failed"), B(t("t3", "failed", ASSERT))]), 1
elif scenario == "notests":
    report, code = doc([]), 1
elif scenario == "noreport":
    code = 1
elif scenario == "badreport":
    report, code = "not json at all", 1
elif scenario == "exit":
    report, code = doc([A(t("t1"), t("t2")), B(t("t3"))]), 1
elif scenario == "erroronly":
    report, code = doc([A(t("t1", "failed", "TypeError: x is not a function"), t("t2")), B(t("t3"))]), 1
elif scenario == "stray":
    report, code = doc([A(t("t1"), t("t9", "failed", ASSERT)), B(t("t3"))]), 1
elif scenario == "drift":
    report = doc([A(t("t1")), B(t("t3"))])
elif scenario == "counts":
    report = doc([A(t("t1"), t("t2")), B(t("t3"))], numPassedTests=99)
elif scenario == "timeout":
    time.sleep(60)
elif scenario == "undecodable":
    report, code = doc([A(t("t1", "failed", ASSERT), t("t2")), B(t("t3"))]), 1
    sys.stdout.buffer.write(b"\xff\xfe not utf-8 \x80\n")
elif scenario == "badbaseline":
    report, code = doc([A(t("t1", "failed", ASSERT), t("t2")), B(t("t3"))]), 1
if report is not None and out:
    open(out, "w", encoding="utf-8").write(report if isinstance(report, str) else json.dumps(report))
sys.stdout.write(stdout)
sys.exit(code)
'''


def project(tmp_path: Path, scenarios: dict[str, str], baseline: str = "ok", overrides: dict[str, list[str]] | None = None) -> tuple[Path, Path, Path]:
    fake = tmp_path / "fake_runner.py"
    fake.write_text(FAKE, encoding="utf-8")
    target = tmp_path / "target.txt"
    target.write_text(f"SCENARIO: {baseline}\n", encoding="utf-8")
    spec = {
        "cwd": str(tmp_path), "kind": "vitest", "timeout": 4, "env": {"FAKE_TARGET": str(target)}, "command": ["python", str(fake)],
        "mutations": [{"id": name, "file": str(target), "old": f"SCENARIO: {baseline}", "new": f"SCENARIO: {new}", **({"command": overrides[name]} if overrides and name in overrides else {})} for name, new in scenarios.items()],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return spec_path, target, fake


def run(tmp_path: Path, scenarios: dict[str, str], **kw) -> tuple[int, dict[str, dict], Path, Path]:
    spec_path, target, _ = project(tmp_path, scenarios, **kw)
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    runs = tmp_path / "runs"
    runs.mkdir()
    code = mutate.main([str(spec_path), "t"], runs=runs)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before, "the target file must be restored byte for byte"
    run_dir = next(runs.iterdir())
    records = {}
    if (run_dir / "mutations.jsonl").exists():
        records = {r["id"]: r for r in map(json.loads, (run_dir / "mutations.jsonl").read_text(encoding="utf-8").splitlines())}
    return code, records, run_dir, target


EXPECTED = {
    "assert": ("caught", "assertion failures"),
    "survive": ("SURVIVED", "every test passed"),
    "syntax": ("invalid", "suite_load_error"),
    "import": ("invalid", "suite_load_error"),
    "mixed": ("mixed", "not an unqualified kill"),
    "notests": ("invalid", "no_tests"),
    "noreport": ("invalid", "no_report"),
    "badreport": ("invalid", "no_report"),
    "exit": ("invalid", "unexplained_exit"),
    "erroronly": ("error_only", "none by an assertion"),
    "stray": ("invalid", "failing_test_not_in_baseline"),
    "drift": ("invalid", "test_count_drift"),
    "counts": ("invalid", "inconsistent report"),
    "timeout": ("timeout", "did not finish"),
    "undecodable": ("caught", "assertion failures"),
}


def test_every_failure_mode_of_the_acceptance_list_is_classified_apart_and_only_an_assertion_failure_is_caught(tmp_path):
    code, records, run_dir, _ = run(tmp_path, {k: k for k in EXPECTED})
    assert code == 1, "a run that is not entirely caught does not exit 0"
    for name, (outcome, detail) in EXPECTED.items():
        assert records[name]["outcome"] == outcome, (name, records[name])
        assert detail in records[name]["detail"], (name, records[name]["detail"])
    caught = sorted(k for k, r in records.items() if r["outcome"] == "caught")
    assert caught == ["assert", "undecodable"], "nothing but a real assertion failure is caught"
    assert json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))["counts"]["caught"] == 2


def test_the_review_probe_a_suite_level_fail_line_is_not_assertion_evidence(tmp_path):
    # the probe of the review: stdout "FAIL test/synthetic.test.ts / SyntaxError / Tests no tests", exit 1: the old classifier said caught
    code, records, _, _ = run(tmp_path, {"syntax": "syntax"})
    assert records["syntax"]["outcome"] == "invalid"
    assert records["syntax"]["evidence"]["load_errors"], "the failing suites are listed in the evidence"
    assert code == 1


def test_a_mixed_run_is_never_reported_as_an_unqualified_catch(tmp_path):
    _, records, _, _ = run(tmp_path, {"mixed": "mixed"})
    assert records["mixed"]["outcome"] == "mixed"
    assert records["mixed"]["evidence"]["assertion_failures"] == 1 and len(records["mixed"]["evidence"]["load_errors"]) == 1


def test_the_log_keeps_the_raw_bytes_even_when_they_are_not_utf8(tmp_path):
    _, records, run_dir, _ = run(tmp_path, {"undecodable": "undecodable"})
    assert b"\xff\xfe not utf-8 \x80" in (run_dir / "logs" / "undecodable.log").read_bytes()
    assert records["undecodable"]["outcome"] == "caught"


def test_the_evidence_of_a_caught_mutant_is_in_a_report_file_that_names_the_failing_test(tmp_path):
    _, records, run_dir, _ = run(tmp_path, {"assert": "assert"})
    rep = json.loads((run_dir / "reports" / "assert.json").read_text(encoding="utf-8"))
    assert rep["testResults"][0]["assertionResults"][0]["failureMessages"] == ["AssertionError: expected 1 to be 2"]
    assert records["assert"]["failing"] == ["test/A.test.ts :: t1"]


def test_a_baseline_that_is_not_clean_makes_the_whole_run_invalid_and_mutates_nothing(tmp_path):
    code, records, run_dir, _ = run(tmp_path, {"assert": "assert"}, baseline="badbaseline")
    assert code == 2 and records == {}
    assert (run_dir / "INVALID.txt").exists()
    assert "1 tests failed" in (run_dir / "INVALID.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("scenario,problem", [("syntax", "suites did not load"), ("notests", "no tests ran"), ("noreport", "no report"), ("exit", "exit code 1")])
def test_a_baseline_with_a_load_error_no_tests_no_report_or_a_bad_exit_is_invalid(tmp_path, scenario, problem):
    code, _, run_dir, _ = run(tmp_path, {"x": "survive"}, baseline=scenario)
    assert code == 2
    assert problem in (run_dir / "INVALID.txt").read_text(encoding="utf-8")


def test_a_mutation_with_its_own_command_has_its_own_baseline(tmp_path):
    # the override runs a runner whose unmutated tree is never clean: its mutation is invalid, the others are judged
    bad = tmp_path / "bad_runner.py"
    bad.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    _, records, run_dir, _ = run(tmp_path, {"assert": "assert", "own": "assert"}, overrides={"own": ["python", str(bad)]})
    assert records["assert"]["outcome"] == "caught"
    assert records["own"]["outcome"] == "invalid" and "own command is not clean" in records["own"]["detail"]
    names = [b["name"] for b in json.loads((run_dir / "baseline.json").read_text(encoding="utf-8"))]
    assert len(names) == 2


def test_a_pattern_that_is_missing_is_a_pattern_error_and_nothing_is_run(tmp_path):
    spec_path, target, _ = project(tmp_path, {"gone": "assert"})
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["mutations"][0]["old"] = "NOT THERE"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    runs = tmp_path / "runs"
    runs.mkdir()
    mutate.main([str(spec_path), "t"], runs=runs)
    rec = json.loads((next(runs.iterdir()) / "mutations.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["outcome"] == "pattern_error"


def test_a_failure_while_reading_the_result_is_a_harness_error_and_the_log_is_kept(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(mutate, "decide", boom)
    _, records, run_dir, _ = run(tmp_path, {"assert": "assert"})
    assert records["assert"]["outcome"] == "harness_error" and "synthetic" in records["assert"]["detail"]
    assert (run_dir / "logs" / "assert.log").exists()


def test_the_intended_tests_can_be_named_and_a_catch_elsewhere_is_unattributed(tmp_path):
    spec_path, target, _ = project(tmp_path, {"assert": "assert"})
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["mutations"][0]["expect"] = ["t3"]
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    runs = tmp_path / "runs"
    runs.mkdir()
    mutate.main([str(spec_path), "t"], runs=runs)
    rec = json.loads((next(runs.iterdir()) / "mutations.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["outcome"] == "unattributed"


# ---- the text parsers (reclassification of logs saved before the JSON report was kept) ----
VITEST_LOAD = "\n ❯ test/a.test.ts (0 test)\n ✓ test/b.test.ts (2 tests) 5ms\n\n FAIL  test/a.test.ts [ test/a.test.ts ]\nSyntaxError: deliberate\n\n Test Files  1 failed | 1 passed (2)\n      Tests  no tests\n"
VITEST_ASSERT = " ❯ test/a.test.ts (3 tests | 1 failed) 9ms\n ✓ test/b.test.ts (2 tests) 5ms\n\n Test Files  1 failed | 1 passed (2)\n      Tests  1 failed | 4 passed (5)\n\n FAIL  test/a.test.ts > s > one\nAssertionError: expected 1 to be 2\n"
VITEST_OK = " ✓ test/a.test.ts (3 tests) 9ms\n ✓ test/b.test.ts (2 tests) 5ms\n\n Test Files  2 passed (2)\n      Tests  5 passed (5)\n"


def test_the_text_parser_reads_a_load_failure_an_assertion_failure_and_a_clean_run():
    load, assertion, ok = (mutate.parse_vitest_text(x) for x in (VITEST_LOAD, VITEST_ASSERT, VITEST_OK))
    assert load["load_errors"] and load["total"] == 0
    assert assertion["assertion_failures"] == ["test/a.test.ts > s > one"] and assertion["total"] == 5
    assert not ok["load_errors"] and ok["total"] == 5 and ok["files"] == {"test/a.test.ts": 3, "test/b.test.ts": 2}
    assert mutate.decide(load, ok, 1)[0] == "invalid"
    assert mutate.decide(assertion, ok, 1)[0] == "caught"
    assert mutate.decide(ok, ok, 0)[0] == "SURVIVED"


def test_the_libtest_parser_separates_an_assertion_panic_from_another_panic_and_from_a_compile_error():
    base = "running 2 tests\ntest m::a ... ok\ntest m::b ... ok\n\ntest result: ok. 2 passed; 0 failed; 0 ignored\n"
    assertion = "running 2 tests\ntest m::a ... FAILED\ntest m::b ... ok\n\n---- m::a stdout ----\nthread 'm::a' panicked at src/x.rs:1:1:\nassertion `left == right` failed\n  left: 1\n right: 2\n\ntest result: FAILED. 1 passed; 1 failed; 0 ignored\n"
    unwrap = assertion.replace("assertion `left == right` failed", "called `Option::unwrap()` on a `None` value")
    # cargo ends every failing run with this line; it is not a build error (the first rerun under v2 on real output read it as one)
    real_tail = assertion + "   Compiling athena-terminal v0.1.0 (C:\\x)\n    Finished `test` profile\n     Running unittests src\\main.rs (C:/t/athena_terminal.exe)\nerror: test failed, to rerun pass `--bin athena-terminal`\n"
    assert mutate.parse_libtest(real_tail)["build_errors"] == []
    broken = "error[E0425]: cannot find value `x`\nerror: could not compile `athena-terminal`\n"
    b = mutate.parse_libtest(base)
    assert mutate.baseline_problems(b, 0) == []
    assert mutate.decide(mutate.parse_libtest(real_tail), b, 101)[0] == "caught"
    assert mutate.decide(mutate.parse_libtest(assertion), b, 101)[0] == "caught"
    assert mutate.decide(mutate.parse_libtest(unwrap), b, 101)[0] == "error_only"
    assert mutate.decide(mutate.parse_libtest(broken), b, 101)[0] == "invalid"
