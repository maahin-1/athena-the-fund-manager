import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from athena.ops import supervisor as sup
from athena.ops.supervisor import Supervisor, Task

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


class FakeProc:
    def __init__(self, code):
        self.code = code
        self.terminated = False

    def poll(self):
        return None if self.terminated is None else self.code

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        pass


def make(tmp_path, tasks, codes, parent_alive=True, clock=None):
    ran = []

    def spawn(task):
        ran.append(task.name)
        return FakeProc(codes[task.name])

    s = Supervisor(
        tasks, parent_pid=1, state_path=tmp_path / "s.json", config_path=tmp_path / "jobs.json", spawn=spawn,
        clock=clock or (lambda: NOW), sleep=lambda _s: None, alive=lambda pid: parent_alive,
    )
    return s, ran


def test_a_task_that_never_ran_is_due_and_a_finished_one_waits_for_its_gap(tmp_path):
    a = Task("a", ("x",), 6)
    s, ran = make(tmp_path, [a], {"a": 0})
    assert s.step() == "a" and ran == ["a"]
    assert s.step() is None  # not due again for six hours
    state = json.loads((tmp_path / "s.json").read_text())
    assert state["tasks"]["a"]["last_ok"] and state["tasks"]["a"]["fail_streak"] == 0
    later, ran2 = make(tmp_path, [a], {"a": 0}, clock=lambda: NOW + timedelta(hours=6, minutes=1))
    assert later.step() == "a" and ran2 == ["a"]


def test_the_first_due_task_runs_and_one_step_runs_only_one(tmp_path):
    s, ran = make(tmp_path, [Task("a", ("x",), 6), Task("b", ("y",), 6)], {"a": 0, "b": 0})
    assert s.step() == "a" and ran == ["a"]
    assert s.step() == "b" and ran == ["a", "b"]
    assert s.step() is None


def test_exit_codes_set_how_long_a_task_waits(tmp_path):
    for code, hours in ((3, 2.0), (4, 6.0), (5, 0.5), (6, 10 / 60)):
        t = Task("a", ("x",), 6)
        row = sup.after_run(t, {}, code, NOW, {})
        assert datetime.fromisoformat(row["next_due"]) == NOW + timedelta(hours=hours), code
        assert "last_ok" not in row and row.get("fail_streak", 0) == 0


def test_failures_in_a_row_back_off_longer_up_to_a_limit(tmp_path):
    t = Task("a", ("x",), 6)
    row, waits = {}, []
    for _ in range(7):
        row = sup.after_run(t, row, 1, NOW, {})
        waits.append((datetime.fromisoformat(row["next_due"]) - NOW).total_seconds() / 3600)
    assert waits[:3] == [0.5, 1.0, 2.0] and waits[-1] == 6.0 and row["fail_streak"] == 7
    assert sup.after_run(t, row, 0, NOW, {})["fail_streak"] == 0


def test_a_task_switched_off_in_the_jobs_file_does_not_run_and_a_gap_can_be_changed(tmp_path):
    (tmp_path / "jobs.json").write_text(json.dumps({"disabled": ["a"], "every_hours": {"b": 1}}))
    s, ran = make(tmp_path, [Task("a", ("x",), 6), Task("b", ("y",), 6)], {"a": 0, "b": 0})
    assert s.step() == "b" and ran == ["b"]
    state = json.loads((tmp_path / "s.json").read_text())
    assert datetime.fromisoformat(state["tasks"]["b"]["next_due"]) == NOW + timedelta(hours=1)


def test_a_bad_jobs_file_is_ignored_not_fatal(tmp_path):
    (tmp_path / "jobs.json").write_text("{not json")
    s, ran = make(tmp_path, [Task("a", ("x",), 6)], {"a": 0})
    assert s.step() == "a"


def test_when_the_software_has_gone_the_job_is_ended_and_its_health_is_not_judged(tmp_path):
    class Hangs(FakeProc):
        def poll(self):
            return None if not self.terminated else 1

        def terminate(self):
            self.terminated = True

    s = Supervisor([Task("a", ("x",), 6)], parent_pid=1, state_path=tmp_path / "s.json", config_path=tmp_path / "jobs.json", spawn=lambda t: Hangs(1), clock=lambda: NOW, sleep=lambda _s: None, alive=lambda pid: False)
    assert s.step() == "a"
    row = json.loads((tmp_path / "s.json").read_text())["tasks"]["a"]
    assert row["last_exit"] == 130 and "fail_streak" not in row and "last_ok" not in row
    assert s.parent_gone()


class Stubborn:
    """A child process that records every call made to it and whether the writer lock was still held at that moment. `dies_on` names the call after which it is gone
    (`terminate`, `kill`, or None for never): `wait` raises TimeoutExpired until then, exactly as a real Popen does."""

    pid = 424242

    def __init__(self, lock_file, dies_on, terminate_raises=False):
        self.lock_file, self.dies_on, self.terminate_raises = lock_file, dies_on, terminate_raises
        self.calls, self.held_during, self.gone = [], [], False

    def _note(self, call):
        self.calls.append(call)
        self.held_during.append(self.lock_file.exists())

    def poll(self):
        return -15 if self.gone else None

    def terminate(self):
        self._note("terminate")
        self.gone = self.gone or self.dies_on == "terminate"
        if self.terminate_raises:
            raise ProcessLookupError("already gone")

    def kill(self):
        self._note("kill")
        self.gone = self.gone or self.dies_on == "kill"

    def wait(self, timeout=None):
        self._note(f"wait({timeout})")
        if not self.gone:
            raise sup.subprocess.TimeoutExpired("job", timeout)
        return -15


def locked_supervisor(tmp_path, child, alive=True, max_hours=0.0, locks=("writer:warehouse.duckdb",), name="w"):
    """One task that takes the writer lock; the supervisor's clock is real, so max_hours=0 means 'already over time' and nothing sleeps."""
    task = Task(name, ("x",), 6, tuple(locks), max_hours)
    s = Supervisor([task], parent_pid=1, state_path=tmp_path / "s.json", config_path=tmp_path / "jobs.json", spawn=lambda t: child, clock=lambda: NOW,
                   sleep=lambda _s: None, alive=lambda pid: alive, status_dir=tmp_path / "status", lock_dir=tmp_path / "locks")
    s._snapshot = lambda: None  # the real one copies the warehouse; not under test
    return s


def lock_file(tmp_path):
    return tmp_path / "locks" / "writer_warehouse.duckdb.lock"


def test_a_job_that_runs_past_its_time_is_terminated_and_reaped_before_its_writer_lock_is_released(tmp_path):
    child = Stubborn(lock_file(tmp_path), dies_on="terminate")
    s = locked_supervisor(tmp_path, child)
    assert s.step() == "w"
    assert child.calls == ["terminate", f"wait({sup.TERMINATE_WAIT})"] and all(child.held_during)  # exit was confirmed while the lock was still held
    assert not lock_file(tmp_path).exists()  # and only then released
    row = json.loads((tmp_path / "s.json").read_text())["tasks"]["w"]
    assert row["last_exit"] == 124 and row["last_reason"] == "timeout" and row["fail_streak"] == 1 and "unconfirmed_child" not in row  # a timeout is a failure of the job, not an exchange refusal (3)


def test_a_child_that_ignores_terminate_is_killed_and_confirmed_before_the_lock_is_released(tmp_path):
    child = Stubborn(lock_file(tmp_path), dies_on="kill")
    s = locked_supervisor(tmp_path, child)
    s.step()
    assert child.calls == ["terminate", f"wait({sup.TERMINATE_WAIT})", "kill", f"wait({sup.KILL_WAIT})"] and all(child.held_during)
    assert not lock_file(tmp_path).exists()
    assert json.loads((tmp_path / "s.json").read_text())["tasks"]["w"]["last_exit"] == 124


def test_a_child_whose_exit_cannot_be_confirmed_keeps_the_writer_lock_in_its_own_name_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr("athena.ops.jobs.pid_alive", lambda pid: pid == Stubborn.pid)  # the unconfirmed child still counts as running
    child = Stubborn(lock_file(tmp_path), dies_on=None)
    s = locked_supervisor(tmp_path, child)
    s.step()
    assert child.calls == ["terminate", f"wait({sup.TERMINATE_WAIT})", "kill", f"wait({sup.KILL_WAIT})"]
    held = json.loads(lock_file(tmp_path).read_text())  # the writer is not advertised free
    assert held["pid"] == Stubborn.pid and "unconfirmed" in held["job"]
    row = json.loads((tmp_path / "s.json").read_text())["tasks"]["w"]
    assert row["unconfirmed_child"]["pid"] == Stubborn.pid and row["last_exit"] == 124
    status = json.loads((tmp_path / "status" / "w.json").read_text())
    assert status["state"] == "failed" and "not confirmed" in status["last_error"]
    # a second job wanting the same writer is refused (6: try again soon), not allowed to write beside the old child
    child2 = Stubborn(lock_file(tmp_path), dies_on="terminate")
    s2 = locked_supervisor(tmp_path, child2)
    assert s2._run(s2.tasks[0]) == 6 and child2.calls == []
    # once the old child really has gone, the lock is taken over
    monkeypatch.setattr("athena.ops.jobs.pid_alive", lambda pid: False)
    assert s2._run(s2.tasks[0]) == 124


def test_an_unconfirmed_child_is_not_blamed_on_the_next_job(tmp_path, monkeypatch):
    monkeypatch.setattr("athena.ops.jobs.pid_alive", lambda pid: pid == Stubborn.pid)
    s = locked_supervisor(tmp_path, Stubborn(lock_file(tmp_path), dies_on=None))
    s.step()
    assert s.unconfirmed and s.unconfirmed["pid"] == Stubborn.pid and s.unconfirmed["ended_because"] == "timeout"
    s._spawn = lambda t: Stubborn(lock_file(tmp_path), dies_on="terminate")
    assert s._run(s.tasks[0]) == 6 and s.unconfirmed is None  # refused by the held lock; nothing carried over from the earlier run


# ---- an unresolved handover must keep blocking writers (BATCH-1-REVIEW: T4 follow-up)
class Launches:
    """A spawn function that counts launches: the proof that no conflicting writer was started."""

    def __init__(self, lock_path, dies_on="terminate"):
        self.n, self.lock_path, self.dies_on = 0, lock_path, dies_on

    def __call__(self, task):
        self.n += 1
        return Stubborn(self.lock_path, dies_on=self.dies_on)


def fail_lock_writes(monkeypatch, only=None):
    """Make rewriting a lock file fail with a storage error (status and state files are not affected). `only` limits it to lock files whose name contains that text."""
    import athena.ops.jobs as jobs

    original = jobs._write

    def flaky(path, data):
        if str(path).endswith(".lock") and (only is None or only in str(path)):
            raise OSError(28, "No space left on device")
        return original(path, data)

    monkeypatch.setattr(jobs, "_write", flaky)


@pytest.fixture(autouse=True)
def _no_leftover_quarantine():
    import athena.ops.jobs as jobs

    jobs._QUARANTINE.clear()
    yield
    jobs._QUARANTINE.clear()


def test_a_failed_lock_rewrite_still_blocks_the_next_writer_in_the_same_supervisor(tmp_path, monkeypatch):
    monkeypatch.setattr("athena.ops.jobs.pid_alive", lambda pid: pid == Stubborn.pid)
    child = Stubborn(lock_file(tmp_path), dies_on=None)  # the child never confirms its exit
    s = locked_supervisor(tmp_path, child)
    fail_lock_writes(monkeypatch)
    s.step()
    # the lock file still names this (the supervisor's) process, which is exactly what used to be reclaimable
    assert json.loads(lock_file(tmp_path).read_text())["pid"] == os.getpid()
    handover = s.unconfirmed["handover"]
    assert handover["failed"] and handover["failed"][0][0] == "writer:warehouse.duckdb" and "No space left" in handover["failed"][0][1]
    status = json.loads((tmp_path / "status" / "w.json").read_text())
    assert status["state"] == "failed" and "lock handover incomplete" in status["last_error"] and "No space left" in status["last_error"]  # surfaced, not swallowed
    row = json.loads((tmp_path / "s.json").read_text())["tasks"]["w"]["unconfirmed_child"]
    assert row["pid"] == Stubborn.pid and row["locks"] == ["writer:warehouse.duckdb"]
    launches = Launches(lock_file(tmp_path))
    nxt = locked_supervisor(tmp_path, None)
    nxt._spawn = launches
    assert nxt._run(nxt.tasks[0]) == 6 and launches.n == 0  # refused; no second writer started beside the live child


def test_a_child_with_no_usable_pid_quarantines_the_lock_instead_of_leaving_it_reclaimable(tmp_path):
    class NoPid(Stubborn):
        pid = None

    s = locked_supervisor(tmp_path, NoPid(lock_file(tmp_path), dies_on=None))
    s.step()
    held = json.loads(lock_file(tmp_path).read_text())
    assert held["state"] == "unresolved" and held["child_pid"] == 0 and "unknown" in held["note"]
    assert s.unconfirmed["handover"]["quarantined"] == ["writer:warehouse.duckdb"] and not s.unconfirmed["handover"]["failed"]
    launches = Launches(lock_file(tmp_path))
    nxt = locked_supervisor(tmp_path, None)
    nxt._spawn = launches
    assert nxt._run(nxt.tasks[0]) == 6 and launches.n == 0  # the supervisor's own pid on the file does not make it stale
    # a new supervisor process (after a restart) is refused too: the old supervisor being gone proves nothing about the child
    import athena.ops.jobs as jobs

    jobs._QUARANTINE.clear()
    record = json.loads(lock_file(tmp_path).read_text())
    lock_file(tmp_path).write_text(json.dumps({**record, "pid": 2_000_000_000, "supervisor_pid": 2_000_000_000}))
    assert nxt._run(nxt.tasks[0]) == 6 and launches.n == 0
    # only an explicit operator decision frees it
    assert jobs.clear_unresolved("writer:warehouse.duckdb", lock_dir=tmp_path / "locks") is True
    assert nxt._run(nxt.tasks[0]) == 124 and launches.n == 1


def test_partial_handover_across_several_locks_reports_each_lock_and_blocks_all_of_them(tmp_path, monkeypatch):
    monkeypatch.setattr("athena.ops.jobs.pid_alive", lambda pid: pid == Stubborn.pid)
    child = Stubborn(tmp_path / "locks" / "nse.lock", dies_on=None)
    s = locked_supervisor(tmp_path, child, locks=("nse", "writer:warehouse.duckdb"))
    fail_lock_writes(monkeypatch, only="writer_warehouse")  # the first lock transfers, the second cannot be rewritten
    s.step()
    handover = s.unconfirmed["handover"]
    assert handover["transferred"] == ["nse"] and [name for name, _ in handover["failed"]] == ["writer:warehouse.duckdb"]
    assert json.loads((tmp_path / "locks" / "nse.lock").read_text())["pid"] == Stubborn.pid  # the one that worked is in the child's name
    status = json.loads((tmp_path / "status" / "w.json").read_text())
    assert status["handover"]["transferred"] == ["nse"] and "writer:warehouse.duckdb" in status["last_error"]
    for locks in (("nse",), ("writer:warehouse.duckdb",), ("nse", "writer:warehouse.duckdb")):
        launches = Launches(lock_file(tmp_path))
        nxt = locked_supervisor(tmp_path, None, locks=locks, name="other")
        nxt._spawn = launches
        assert nxt._run(nxt.tasks[0]) == 6 and launches.n == 0, locks
    # nothing was left half-held: neither a partly released lock nor a lock held by the dead job
    assert not [p for p in (tmp_path / "locks").glob("*.tmp")]


def test_a_restarted_supervisor_reinstates_the_quarantine_from_its_state_while_the_child_is_unresolved(tmp_path, monkeypatch):
    import athena.ops.jobs as jobs

    monkeypatch.setattr("athena.ops.jobs.pid_alive", lambda pid: pid == Stubborn.pid)
    first = locked_supervisor(tmp_path, Stubborn(lock_file(tmp_path), dies_on=None))
    fail_lock_writes(monkeypatch)
    first.step()  # the lock file could not be rewritten, so only this process's memory and the state file know
    monkeypatch.undo()  # writes work again, as after a restart
    monkeypatch.setattr("athena.ops.jobs.pid_alive", lambda pid: pid == Stubborn.pid)
    jobs._QUARANTINE.clear()  # a new process has no memory of the old one
    lock_file(tmp_path).write_text(json.dumps({"pid": 2_000_000_000, "job": "w", "since": "x"}))  # the old supervisor's file names a process that has now ended
    launches = Launches(lock_file(tmp_path))
    second = locked_supervisor(tmp_path, None)
    second._spawn = launches
    second.recover()
    held = json.loads(lock_file(tmp_path).read_text())
    assert held["state"] == "unresolved" and held["child_pid"] == Stubborn.pid  # re-installed in the child's name, from supervisor.json
    assert second._run(second.tasks[0]) == 6 and launches.n == 0
    # the child is finally gone: recovery clears the record and the writer is free again
    monkeypatch.setattr("athena.ops.jobs.pid_alive", lambda pid: False)
    second.recover()
    assert "unconfirmed_child" not in json.loads((tmp_path / "s.json").read_text())["tasks"]["w"]
    assert second._run(second.tasks[0]) == 124 and launches.n == 1


def test_recovery_with_an_unknown_child_pid_keeps_blocking_and_a_failed_reinstatement_is_reported(tmp_path, monkeypatch):
    import athena.ops.jobs as jobs

    (tmp_path / "s.json").write_text(json.dumps({"tasks": {"w": {"unconfirmed_child": {"pid": 0, "since": "x", "ended_because": "timeout", "locks": ["writer:warehouse.duckdb"]}}}}))
    s = locked_supervisor(tmp_path, None)
    s.recover()
    assert json.loads(lock_file(tmp_path).read_text())["state"] == "unresolved"
    launches = Launches(lock_file(tmp_path))
    s._spawn = launches
    assert s._run(s.tasks[0]) == 6 and launches.n == 0  # an unknown pid is never assumed dead
    jobs.clear_unresolved("writer:warehouse.duckdb", lock_dir=tmp_path / "locks")
    # reinstatement that cannot be written is reported, and the in-memory block still holds
    fail_lock_writes(monkeypatch)
    s2 = locked_supervisor(tmp_path, None)
    s2.recover()
    assert s2.recovery_errors and "writer:warehouse.duckdb" in s2.recovery_errors[0]
    s2._spawn = launches
    assert s2._run(s2.tasks[0]) == 6 and launches.n == 0


LATER = NOW + timedelta(days=2)  # well past any backoff, so a retry is due


def supervisor_at(tmp_path, when, spawn, locks=("writer:warehouse.duckdb",), name="w", max_hours=0.0):
    task = Task(name, ("x",), 6, tuple(locks), max_hours)
    s = Supervisor([task], parent_pid=1, state_path=tmp_path / "s.json", config_path=tmp_path / "jobs.json", spawn=spawn, clock=lambda: when,
                   sleep=lambda _s: None, alive=lambda pid: True, status_dir=tmp_path / "status", lock_dir=tmp_path / "locks")
    s._snapshot = lambda: None
    return s


def state_row(tmp_path, name="w"):
    return json.loads((tmp_path / "s.json").read_text())["tasks"][name]


def test_failed_handover_then_a_blocked_retry_then_a_restart_still_refuses_the_writer(tmp_path, monkeypatch):
    # BATCH-1-REVIEW follow-up: a scheduled retry that is refused (code 6) launched no child, so it says nothing about the earlier unresolved one
    import athena.ops.jobs as jobs

    alive = {Stubborn.pid}
    monkeypatch.setattr("athena.ops.jobs.pid_alive", lambda pid: pid in alive)
    first = supervisor_at(tmp_path, NOW, lambda t: Stubborn(lock_file(tmp_path), dies_on=None))
    fail_lock_writes(monkeypatch)
    assert first.step() == "w"  # timeout; the child never confirms its exit and the lock file cannot be rewritten
    assert state_row(tmp_path)["unconfirmed_child"]["pid"] == Stubborn.pid
    launches = Launches(lock_file(tmp_path))
    retry = supervisor_at(tmp_path, LATER, launches)
    assert retry.step() == "w" and launches.n == 0
    row = state_row(tmp_path)
    assert row["last_exit"] == 6  # refused by the quarantined lock
    assert row["unconfirmed_child"]["pid"] == Stubborn.pid and row["unconfirmed_child"]["locks"] == ["writer:warehouse.duckdb"]  # the outstanding child record is kept
    # the restart: writes work again, memory is gone, the old lock file names a supervisor that has ended
    monkeypatch.undo()
    monkeypatch.setattr("athena.ops.jobs.pid_alive", lambda pid: pid in alive)
    jobs._QUARANTINE.clear()
    lock_file(tmp_path).write_text(json.dumps({"pid": 2_000_000_000, "job": "w", "since": "x"}))
    restarted = supervisor_at(tmp_path, LATER + timedelta(hours=1), launches)  # past the 10-minute back-off a refused run (6) earns
    restarted.recover()
    assert restarted.step() == "w" and launches.n == 0  # the writer is still refused while the original child is alive
    assert state_row(tmp_path)["last_exit"] == 6 and state_row(tmp_path)["unconfirmed_child"]["pid"] == Stubborn.pid
    alive.clear()  # the child's exit is finally confirmed
    again = supervisor_at(tmp_path, LATER + timedelta(days=2), launches)
    again.recover()
    assert "unconfirmed_child" not in state_row(tmp_path)
    assert again.step() == "w" and launches.n == 1


def seed_unknown_pid_child(tmp_path, locks=("writer:warehouse.duckdb",), name="w"):
    """supervisor.json as an earlier supervisor left it: a child whose pid was never known, so its exit can never be confirmed by looking."""
    record = {"pid": 0, "since": "2026-10-28T00:00:00", "ended_because": "timeout", "locks": list(locks), "handover": {"pid": 0, "transferred": [], "quarantined": list(locks), "failed": []}}
    (tmp_path / "s.json").write_text(json.dumps({"tasks": {name: {"last_exit": 124, "next_due": "2026-10-28T00:00:00+00:00", "unconfirmed_child": record}}}))


def test_an_unknown_pid_record_survives_blocked_retries_and_restarts_until_the_operator_resolves_it_durably(tmp_path):
    import athena.ops.jobs as jobs

    seed_unknown_pid_child(tmp_path)
    launches = Launches(lock_file(tmp_path))
    s = supervisor_at(tmp_path, LATER, launches)
    s.recover()
    for attempt in range(3):  # ordinary scheduled retries, each refused by the quarantined lock
        assert s.step() == "w" and launches.n == 0 and state_row(tmp_path)["last_exit"] == 6
        s = supervisor_at(tmp_path, LATER + timedelta(days=attempt + 1), launches)  # and a restart between them
        jobs._QUARANTINE.clear()
        s.recover()
        assert state_row(tmp_path)["unconfirmed_child"]["pid"] == 0  # never dropped by a retry, never assumed gone
    # the operator's decision, applied durably
    result = sup.resolve_unresolved("writer:warehouse.duckdb", state_path=tmp_path / "s.json", lock_dir=tmp_path / "locks", now=NOW)
    assert result["tasks"] == ["w"] and result["lock_record_removed"] is True
    state = json.loads((tmp_path / "s.json").read_text())
    assert "unconfirmed_child" not in state["tasks"]["w"]  # the recovery record itself is resolved
    assert state["operator_resolutions"] == [{"lock": "writer:warehouse.duckdb", "at": NOW.isoformat(timespec="seconds"), "tasks": ["w"]}]  # and the decision is on record
    assert not lock_file(tmp_path).exists()
    restarted = supervisor_at(tmp_path, LATER + timedelta(days=9), launches)
    jobs._QUARANTINE.clear()
    restarted.recover()  # a restart must not reinstate what the operator cleared
    assert not lock_file(tmp_path).exists() and restarted.recovery_errors == []
    assert restarted.step() == "w" and launches.n == 1


def test_operator_clearing_one_lock_leaves_the_childs_other_locks_blocked_after_a_restart(tmp_path):
    import athena.ops.jobs as jobs

    seed_unknown_pid_child(tmp_path, locks=("nse", "writer:warehouse.duckdb"))
    first = supervisor_at(tmp_path, LATER, Launches(lock_file(tmp_path)), locks=("nse", "writer:warehouse.duckdb"))
    first.recover()
    sup.resolve_unresolved("writer:warehouse.duckdb", state_path=tmp_path / "s.json", lock_dir=tmp_path / "locks", now=NOW)
    record = state_row(tmp_path)["unconfirmed_child"]
    assert record["locks"] == ["nse"] and record["operator_cleared"] == ["writer:warehouse.duckdb"]
    jobs._QUARANTINE.clear()
    (tmp_path / "locks" / "nse.lock").unlink()  # even with the lock file gone, the durable record still guards it
    again = supervisor_at(tmp_path, LATER, Launches(lock_file(tmp_path)), locks=("nse", "writer:warehouse.duckdb"))
    again.recover()
    assert json.loads((tmp_path / "locks" / "nse.lock").read_text())["state"] == "unresolved" and not lock_file(tmp_path).exists()


def test_a_running_supervisor_drops_a_memory_only_block_when_the_operator_has_cleared_it_durably(tmp_path, monkeypatch):
    monkeypatch.setattr("athena.ops.jobs.pid_alive", lambda pid: pid == Stubborn.pid)
    first = supervisor_at(tmp_path, NOW, lambda t: Stubborn(lock_file(tmp_path), dies_on=None))
    fail_lock_writes(monkeypatch)
    first.step()  # the lock file could not be written, so the block lives only in this process's memory
    monkeypatch.undo()
    monkeypatch.setattr("athena.ops.jobs.pid_alive", lambda pid: pid == Stubborn.pid)
    launches = Launches(lock_file(tmp_path))
    first._spawn = launches
    first._clock = lambda: LATER
    assert first.step() == "w" and launches.n == 0  # still refused
    sup.resolve_unresolved("writer:warehouse.duckdb", state_path=tmp_path / "s.json", lock_dir=tmp_path / "locks", now=NOW)  # done from another process
    first._clock = lambda: LATER + timedelta(days=1)
    assert first.step() == "w" and launches.n == 1  # this supervisor followed the durable decision at its next step


def test_unresolved_children_from_earlier_attempts_are_kept_beside_a_new_one_and_dropped_only_when_gone(tmp_path, monkeypatch):
    alive = {111, 222}

    class Child(Stubborn):
        def __init__(self, pid):
            super().__init__(lock_file(tmp_path), dies_on=None)
            self.pid = pid

    monkeypatch.setattr("athena.ops.jobs.pid_alive", lambda pid: pid in alive)
    children = iter([Child(111), Child(222)])
    first = supervisor_at(tmp_path, NOW, lambda t: next(children), locks=())  # a task with no lock of its own, so nothing refuses its second run
    first.step()
    first._clock = lambda: LATER
    first.step()
    head = state_row(tmp_path)["unconfirmed_child"]
    assert head["pid"] == 222 and [p["pid"] for p in head["previous"]] == [111]  # the older child is not forgotten
    alive.discard(111)
    sup_again = supervisor_at(tmp_path, LATER, lambda t: None, locks=())
    sup_again.recover()
    head = state_row(tmp_path)["unconfirmed_child"]
    assert head["pid"] == 222 and "previous" not in head  # 111 is confirmed gone; 222 is not
    alive.clear()
    sup_again.recover()
    assert "unconfirmed_child" not in state_row(tmp_path)


def test_the_clear_command_updates_the_saved_recovery_state_too(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # the command uses the default relative .athena paths
    state_path, lock_dir = tmp_path / ".athena" / "supervisor.json", tmp_path / ".athena" / "locks"
    state_path.parent.mkdir()
    record = {"pid": 0, "since": "x", "ended_because": "timeout", "locks": ["writer:warehouse.duckdb"], "handover": {}}
    state_path.write_text(json.dumps({"tasks": {"prices": {"unconfirmed_child": record}}}))
    lock_dir.mkdir()
    (lock_dir / "writer_warehouse.duckdb.lock").write_text(json.dumps({"pid": 1, "job": "prices (child pid unknown)", "state": "unresolved", "child_pid": 0, "supervisor_pid": 1}))
    assert sup.main(["--clear-unresolved", "writer:warehouse.duckdb"]) == 0
    assert "removed" in capsys.readouterr().out and not (lock_dir / "writer_warehouse.duckdb.lock").exists()
    state = json.loads(state_path.read_text())
    assert "unconfirmed_child" not in state["tasks"]["prices"] and state["operator_resolutions"][0]["lock"] == "writer:warehouse.duckdb"


def test_ending_a_job_because_the_software_closed_uses_the_same_terminate_wait_kill_wait_and_keeps_the_lock_until_exit(tmp_path):
    child = Stubborn(lock_file(tmp_path), dies_on="kill")
    s = locked_supervisor(tmp_path, child, alive=False, max_hours=8.0)  # parent gone, job not over time
    s.step()
    assert child.calls == ["terminate", f"wait({sup.TERMINATE_WAIT})", "kill", f"wait({sup.KILL_WAIT})"] and all(child.held_during)
    row = json.loads((tmp_path / "s.json").read_text())["tasks"]["w"]
    assert row["last_exit"] == 130 and "fail_streak" not in row and not lock_file(tmp_path).exists()


def test_terminate_failing_because_the_process_is_already_gone_is_still_reaped(tmp_path):
    child = Stubborn(lock_file(tmp_path), dies_on="terminate", terminate_raises=True)
    s = locked_supervisor(tmp_path, child)
    assert s._run(s.tasks[0]) == 124 and not lock_file(tmp_path).exists()


def test_a_job_that_ends_by_itself_is_never_terminated_and_its_lock_is_released(tmp_path):
    class Done(Stubborn):
        def poll(self):
            self._note("poll")
            return 0

    child = Done(lock_file(tmp_path), dies_on=None)
    s = locked_supervisor(tmp_path, child, max_hours=8.0)
    assert s._run(s.tasks[0]) == 0 and child.calls == ["poll"] and child.held_during == [True]
    assert not lock_file(tmp_path).exists()


def test_run_once_stops_when_nothing_is_due(tmp_path):
    s, ran = make(tmp_path, [Task("a", ("x",), 6)], {"a": 0})
    s.run(once=True)
    assert ran == ["a"]


def test_the_standard_tasks_have_unique_names_and_the_fundamentals_jobs_take_their_own_locks():
    names = [t.name for t in sup.TASKS]
    assert len(names) == len(set(names))
    for t in sup.TASKS:
        if t.argv[0].startswith("athena.fundamentals") or t.argv[0] in ("athena.evidence.collect", "athena.evidence.structured", "athena.evidence.verify", "athena.ipo.collect"):
            assert t.locks == (), t.name  # they wrap themselves in a Job; a lock taken here would refuse them
        if t.argv[:2] in (("athena.warehouse.build",), ("athena.derivs.ingest",)) or t.argv[0] in ("athena.warehouse.build", "athena.derivs.ingest"):
            assert "nse" in t.locks, t.name


def test_describe_lists_every_task(tmp_path):
    lines = sup.describe({"tasks": {}}, {"disabled": ["annual_reports"]}, NOW)
    assert len(lines) == len(sup.TASKS) and any("switched off" in line for line in lines)


def test_a_jobs_file_written_with_a_byte_order_mark_is_still_read(tmp_path):
    p = tmp_path / "jobs.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"disabled": ["a"]}).encode())
    assert sup.load_config(p) == {"disabled": ["a"]}
