"""Tests of rust_evidence.py and origin_report.py: zero observed Python children, a wrong interpreter, an early-returning test and a missing or extra test are all failures. Pure JSON in, verdict out.
Run with: python -m pytest docs/audit-2026-11-12/git-prep/verify/test_rust_evidence.py -p no:cacheprovider"""
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import origin_report as orr  # noqa: E402
import rust_evidence as re_  # noqa: E402

EXPECTED = {"full_run_min_processes": 3, "tests": [{"id": "a::t1", "min_python_processes": 1}, {"id": "b::t2", "min_python_processes": 2}]}
GOOD_ISO = [
    {"id": "a::t1", "exit": 0, "passed": 1, "failed": 0, "outcome": "ok", "processes_on_intended_executable": 1, "processes_on_other_executable": 0, "violations": 0},
    {"id": "b::t2", "exit": 0, "passed": 1, "failed": 0, "outcome": "ok", "processes_on_intended_executable": 2, "processes_on_other_executable": 0, "violations": 0},
]
GOOD_FULL = {"processes_observed": 3, "processes_on_other_executable": 0, "violations": 0}
GOOD_INTERP = {"ran": True, "is_candidate_venv_path": True, "same_environment_as_intended": True, "athena_from_candidate": True}


def verdict(expected=EXPECTED, iso=GOOD_ISO, full=GOOD_FULL, interp=GOOD_INTERP):
    return re_.judge(expected, [dict(r) for r in iso], dict(full), dict(interp))


def test_complete_evidence_passes():
    v = verdict()
    assert v["ok"] and v["tests_verified"] == 2, v


def test_a_test_that_passed_without_starting_python_fails():
    iso = [dict(GOOD_ISO[0], processes_on_intended_executable=0), GOOD_ISO[1]]
    v = verdict(iso=iso)
    assert not v["ok"] and any("a::t1" in r and "early return" in r for r in v["reasons"]), v


def test_zero_observed_python_processes_never_counts_as_coverage():
    v = verdict(full=dict(GOOD_FULL, processes_observed=0), iso=[dict(r, processes_on_intended_executable=0) for r in GOOD_ISO])
    assert not v["ok"] and v["tests_verified"] == 0, v


def test_an_empty_recorded_list_cannot_claim_coverage():
    v = verdict(expected={"full_run_min_processes": 0, "tests": []}, iso=[])
    assert not v["ok"] and any("empty" in r for r in v["reasons"]), v


def test_a_wrong_or_unreachable_interpreter_fails():
    for key in GOOD_INTERP:
        v = verdict(interp=dict(GOOD_INTERP, **{key: False}))
        assert not v["ok"], key


def test_another_interpreter_and_foreign_imports_fail():
    assert not verdict(iso=[dict(GOOD_ISO[0], processes_on_other_executable=1), GOOD_ISO[1]])["ok"]
    assert not verdict(iso=[dict(GOOD_ISO[0], violations=1), GOOD_ISO[1]])["ok"]
    assert not verdict(full=dict(GOOD_FULL, processes_on_other_executable=1))["ok"]


def test_missing_extra_failed_or_multi_pass_results_fail():
    assert not verdict(iso=[GOOD_ISO[0]])["ok"]
    assert not verdict(iso=GOOD_ISO + [dict(GOOD_ISO[0], id="c::t3")])["ok"]
    assert not verdict(iso=[dict(GOOD_ISO[0], exit=101, failed=1, passed=0), GOOD_ISO[1]])["ok"]
    assert not verdict(iso=[dict(GOOD_ISO[0], passed=2), GOOD_ISO[1]])["ok"]
    assert not verdict(iso=[dict(GOOD_ISO[0], outcome="ignored"), GOOD_ISO[1]])["ok"]


def test_the_full_run_must_observe_the_recorded_minimum():
    assert not verdict(full=dict(GOOD_FULL, processes_observed=2))["ok"]


def row(exe, modules=0, bad=()):
    return {"pid": 1, "argv": ["-c"], "executable": exe, "athena_modules": modules, "violations": list(bad)}


def test_a_process_that_was_killed_is_still_counted_through_its_start_record():
    want = r"C:\cand\.venv\Scripts\python.exe"
    start = {"event": "start", "proc": "a", "pid": 7, "argv": ["-c"], "executable": want}
    exit_ = {"event": "exit", "proc": "a", "pid": 7, "argv": ["-c"], "executable": want, "athena_modules": 2, "violations": []}
    killed = {"event": "start", "proc": "b", "pid": 8, "argv": ["-c"], "executable": want}  # no exit record: terminated by a deadline
    rep = orr.summarise([start, exit_, killed], min_athena=1, min_processes=2, executable=want)
    assert rep["processes_observed"] == 2 and rep["processes_ended_normally"] == 1 and rep["processes_on_intended_executable"] == 2 and rep["ok"], rep
    # a start record alone never counts as an athena import, and an exit record with a foreign import still fails
    assert not orr.summarise([killed], min_athena=1)["ok"]
    bad_exit = dict(exit_, violations=[["athena.x", r"C:\original\src\athena"]])
    assert not orr.summarise([start, bad_exit], min_athena=0)["ok"]


def test_origin_report_counts_processes_and_executables():
    want = r"C:\cand\.venv\Scripts\python.exe"
    ok = orr.summarise([row(want), row(want.upper())], min_athena=0, min_processes=2, executable=want)
    assert ok["ok"] and ok["processes_on_intended_executable"] == 2, ok
    assert not orr.summarise([], min_athena=0, min_processes=1, executable=want)["ok"]
    other = orr.summarise([row(want), row(r"C:\elsewhere\python.exe")], min_athena=0, executable=want)
    assert not other["ok"] and other["processes_on_other_executable"] == 1, other
    assert not orr.summarise([row(want, 3, [["athena.x", r"C:\original\src\athena"]])], min_athena=1)["ok"]
    assert not orr.summarise([row(want, 0)], min_athena=1)["ok"]
