import json
from datetime import datetime, timedelta, timezone

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


def test_run_once_stops_when_nothing_is_due(tmp_path):
    s, ran = make(tmp_path, [Task("a", ("x",), 6)], {"a": 0})
    s.run(once=True)
    assert ran == ["a"]


def test_the_standard_tasks_have_unique_names_and_the_fundamentals_jobs_take_their_own_locks():
    names = [t.name for t in sup.TASKS]
    assert len(names) == len(set(names))
    for t in sup.TASKS:
        if t.argv[0].startswith("athena.fundamentals"):
            assert t.locks == (), t.name  # they wrap themselves in a Job; a lock taken here would refuse them
        if t.argv[:2] in (("athena.warehouse.build",), ("athena.derivs.ingest",)) or t.argv[0] in ("athena.warehouse.build", "athena.derivs.ingest"):
            assert "nse" in t.locks, t.name


def test_describe_lists_every_task(tmp_path):
    lines = sup.describe({"tasks": {}}, {"disabled": ["annual_reports"]}, NOW)
    assert len(lines) == len(sup.TASKS) and any("switched off" in line for line in lines)
