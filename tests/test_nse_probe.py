from datetime import datetime

from athena.evaluation.nse_probe import IST, main, read_log, report, run_probes


def fixed(day, hour=9):
    return lambda: datetime(2026, 10, day, hour, 0, tzinfo=IST)


def boom():
    raise ValueError("HTTP 403")


def test_each_probe_is_logged_once_per_run_and_a_failure_is_recorded_not_raised(tmp_path):
    log = tmp_path / "sub" / "probe.jsonl"
    outcomes = run_probes({"a": lambda: "3 rows", "b": boom}, log, fixed(10))
    assert [(o.probe, o.ok) for o in outcomes] == [("a", True), ("b", False)]
    rows = read_log(log)
    assert [(r["probe"], r["ok"], r["detail"]) for r in rows] == [("a", True, "3 rows"), ("b", False, "ValueError: HTTP 403")]
    assert rows[0]["at"] == "2026-10-10T09:00:00+05:30" and isinstance(rows[0]["ms"], int)


def test_runs_append_and_a_cut_off_line_is_skipped(tmp_path):
    log = tmp_path / "p.jsonl"
    run_probes({"a": lambda: "x"}, log, fixed(10))
    with log.open("a", encoding="utf-8") as handle:
        handle.write('{"at": "2026-10-1')  # a run killed mid-write
    run_probes({"a": lambda: "x"}, log, fixed(11))
    assert len(read_log(log)) == 2 and read_log(tmp_path / "missing.jsonl") == []


def test_the_report_gives_answers_worst_day_and_longest_failure_run_per_source(tmp_path):
    log = tmp_path / "p.jsonl"
    calls = iter([True, False, False, True, True, True])

    def flaky():
        if not next(calls):
            raise ValueError("no segment 'CM'")
        return "ok"

    for day, hour in ((10, 9), (10, 18), (11, 9), (11, 18), (12, 9), (12, 18)):
        run_probes({"holidays": flaky, "steady": lambda: "ok"}, log, fixed(day, hour))
    text = report(read_log(log))
    assert "6 runs over 3 day(s): 2026-10-10 to 2026-10-12" in text
    assert "holidays" in text and "4/6 answered (67%)" in text and "longest failure run 2" in text
    assert "worst day 2026-10-10 50%" in text and "last error: ValueError: no segment 'CM'" in text
    assert "steady" in text and "6/6 answered (100%)" in text and text.count("last error") == 1


def test_an_empty_log_says_so_and_the_command_reports_without_probing(tmp_path, capsys):
    assert report([]) == "No probe runs logged yet."
    assert main(["--report", "--log", str(tmp_path / "none.jsonl")]) == 0
    assert "No probe runs logged yet." in capsys.readouterr().out
