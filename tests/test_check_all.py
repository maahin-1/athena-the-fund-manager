import importlib.util
import subprocess
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("check_all", Path(__file__).resolve().parent.parent / "scripts" / "check_all.py")
check_all = importlib.util.module_from_spec(SPEC)
sys.modules["check_all"] = check_all
SPEC.loader.exec_module(check_all)


def fake_run(results):
    """A subprocess.run that answers by the first word of the command: a CompletedProcess, or an exception to raise."""

    def run(command, **kwargs):
        outcome = results[command[0] if command[0] in results else "python"]
        if isinstance(outcome, Exception):
            raise outcome
        return subprocess.CompletedProcess(command, outcome[0], outcome[1], outcome[2])

    return run


def test_every_step_passing_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(check_all.subprocess, "run", fake_run({"python": (0, "ok\n", ""), "cargo": (0, "ok\n", ""), "npm": (0, "ok\n", "")}))
    assert check_all.main([]) == 0
    assert "all checks passed" in capsys.readouterr().out


def test_a_failing_step_is_reported_by_name_and_the_exit_is_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(check_all.subprocess, "run", fake_run({"python": (0, "ok\n", ""), "cargo": (101, "", "error[E0425]\n"), "npm": (0, "ok\n", "")}))
    assert check_all.main([]) == 1
    out = capsys.readouterr().out
    assert "FAIL rust tests" in out and "failed: rust tests" in out and "error[E0425]" in out


def test_a_tool_that_is_not_installed_is_a_failed_step_not_a_crash(monkeypatch, capsys):
    # cargo missing from PATH used to raise FileNotFoundError out of the whole runner, hiding the steps after it and any summary
    monkeypatch.setattr(check_all.subprocess, "run", fake_run({"python": (0, "ok\n", ""), "cargo": FileNotFoundError(2, "no such file"), "npm": (0, "ok\n", "")}))
    assert check_all.main([]) == 1
    out = capsys.readouterr().out
    assert "FAIL rust tests" in out and "web unit tests" in out and "failed: rust tests" in out and "could not be started" in out


def test_fast_skips_the_native_steps(monkeypatch, capsys):
    monkeypatch.setattr(check_all.subprocess, "run", fake_run({"python": (0, "ok\n", "")}))
    assert check_all.main(["--fast"]) == 0
    out = capsys.readouterr().out
    assert "rust tests" not in out and "web unit tests" not in out
