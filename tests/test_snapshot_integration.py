"""Snapshot generations through the boundaries callers use: `Warehouse`, the chart helper and the `refresh` wrapper (audit T5 review items T5-R1, T5-R2 and T5-R5, WP07).

The unit tests of `athena.warehouse.generations` prove the protocol; these prove that what the protocol says arrives, intact, at the callers: which generation was opened and what it held, both reasons a read
failed (the real file's and the snapshot's), a damaged "unchanged" generation being repaired, and a corrupt or unreadable file whose *path* contains `lock` not being reported as a writer's lock.
Synthetic DuckDB files in temporary directories only; a real second process holds the lock where a lock is the point, and DuckDB's own message formats are used where a message is the point."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import date, datetime, timezone

import duckdb
import pytest

from athena.ops import snapshot
from athena.pit.bhavcopy import Row
from athena.warehouse import chart_bars, generations as g
from athena.warehouse.warehouse import Warehouse

T0 = datetime(2026, 11, 15, 10, 15, tzinfo=timezone.utc)
# DuckDB's own wording, as printed on Windows and on POSIX (the first was captured from a real second process holding the file)
LOCK_WINDOWS = 'IO Error: Cannot open file "{p}": The process cannot access the file because it is being used by another process.\n\nFile is already open in \nC:\\Python313\\python.exe (PID 42180)'
LOCK_POSIX = 'IO Error: Could not set lock on file "{p}": Conflicting lock is held in /usr/bin/python3 (PID 4242) by user x. See also https://duckdb.org/docs/connect/concurrency'
REASONS = ["absent", "pointer_invalid", "manifest_invalid", "mismatch", "size_mismatch", "hash_mismatch", "vanished", "unreadable", "open_failed"]


def seed(path, close: float, day: date = date(2026, 11, 13)) -> None:
    """A warehouse holding one daily bar of `OLD` whose close says which generation of the data a reader got."""
    w = Warehouse(path)
    w.upsert_day(day, {"OLD": Row("OLD", "EQ", close, close, close, close, 1.0)}, T0)
    w.close()


def closes(w: Warehouse) -> list[float]:
    return [r[0] for r in w._con.execute("SELECT close FROM bars ORDER BY day").fetchall()]


def lock_main(monkeypatch, main, message: str = LOCK_WINDOWS) -> None:
    """The real file cannot be opened read-only (a writer holds it) while every other file opens as usual."""
    real = duckdb.connect

    def connect(database, *a, **k):
        if str(database) == str(main):
            raise duckdb.IOException(message.format(p=main))
        return real(database, *a, **k)

    monkeypatch.setattr(duckdb, "connect", connect)


def hold(path):
    holder = subprocess.Popen([sys.executable, "-c", f"import duckdb,time; c=duckdb.connect(r'{path}'); c.execute('SELECT 1'); print('held', flush=True); time.sleep(30)"], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    return holder


def release(holder) -> None:
    holder.kill()
    time.sleep(0.2)


def two_generations(src):
    """Generation 1 holds close 1.0, generation 2 holds 2.0; the pointer is left on generation 2. Returns (g1, g2, pointer text of 1, pointer text of 2)."""
    seed(src, 1.0)
    g1 = g.publish(src, clock=lambda: T0).generation_id
    p1 = g.pointer_path(src).read_text(encoding="utf-8")
    seed(src, 2.0, date(2026, 11, 14))
    g2 = g.publish(src, clock=lambda: T0.replace(minute=20)).generation_id
    p2 = g.pointer_path(src).read_text(encoding="utf-8")
    assert g1 != g2 and (src.with_name(g.data_name(src, g1))).exists() and (src.with_name(g.data_name(src, g2))).exists()
    return g1, g2, p1, p2


def drop(src, gen):
    for name in (g.data_name(src, gen), g.manifest_name(src, gen)):
        src.with_name(name).unlink(missing_ok=True)


# ---- T5-R1: through Warehouse and the chart helper ------------------------------------------------------------------------------------------------------------------------------------------------
def test_a_generation_that_disappears_between_validation_and_opening_is_replaced_by_the_newer_one(tmp_path, monkeypatch):
    src = tmp_path / "w.duckdb"
    g1, g2, p1, p2 = two_generations(src)
    g.pointer_path(src).write_text(p1, encoding="utf-8")  # the reader finds generation 1 current...
    old = src.with_name(g.data_name(src, g1))
    real = duckdb.connect
    raced = []

    def connect(database, *a, **k):
        if str(database) == str(src):
            raise duckdb.IOException(LOCK_WINDOWS.format(p=src))
        if str(database) == str(old) and not raced:  # ...and between its validation and its opening the writer publishes generation 2 and cleanup removes generation 1
            raced.append(1)
            g.pointer_path(src).write_text(p2, encoding="utf-8")
            drop(src, g1)
        return real(database, *a, **k)

    monkeypatch.setattr(duckdb, "connect", connect)
    with Warehouse(src, read_only=True) as w:
        assert raced, "the race was not staged"
        assert closes(w) == [1.0, 2.0]  # the data generation 2 holds (generation 1 held only the first day)
        assert w.provenance["source"] == "snapshot" and w.provenance["generation_id"] == g2 and w.provenance["fallback_reason"] == "locked_by_writer"


def test_a_generation_that_disappears_with_no_replacement_is_an_explicit_failure_with_both_reasons(tmp_path, monkeypatch):
    src = tmp_path / "w.duckdb"
    seed(src, 1.0)
    gen = g.publish(src, clock=lambda: T0).generation_id
    data = src.with_name(g.data_name(src, gen))
    real = duckdb.connect

    def connect(database, *a, **k):
        if str(database) == str(src):
            raise duckdb.IOException(LOCK_POSIX.format(p=src))
        if str(database) == str(data):
            drop(src, gen)  # removed after validation, nothing newer published
        return real(database, *a, **k)

    monkeypatch.setattr(duckdb, "connect", connect)
    with pytest.raises(duckdb.IOException) as caught:  # still an IOException: callers that catch it keep working
        Warehouse(src, read_only=True)
    f = caught.value.failure
    assert (f["main_reason"], f["snapshot_reason"]) == ("locked_by_writer", "vanished")
    assert "Conflicting lock is held" in str(caught.value) and "vanished" in str(caught.value)  # the message carries both


def tamper(src, gen, how: str, monkeypatch=None):
    pointer, manifest, data = g.pointer_path(src), src.with_name(g.manifest_name(src, gen)), src.with_name(g.data_name(src, gen))
    if how == "absent":
        pointer.unlink()
    elif how == "pointer_invalid":
        pointer.write_text("{not json", encoding="utf-8")
    elif how == "manifest_invalid":
        manifest.write_text("nope", encoding="utf-8")
    elif how == "mismatch":
        m = json.loads(manifest.read_text(encoding="utf-8"))
        m["generation_id"] = "20200101T000000Z-aaaaaa"
        manifest.write_text(json.dumps(m), encoding="utf-8")
    elif how == "size_mismatch":
        data.write_bytes(data.read_bytes() + b"x")
    elif how == "hash_mismatch":
        raw = bytearray(data.read_bytes())
        raw[len(raw) // 2] ^= 0xFF
        data.write_bytes(bytes(raw))
    elif how == "vanished":
        drop(src, gen)
    elif how == "unreadable":
        def denied(path, sleep=None):
            raise PermissionError("held by a scanner")

        monkeypatch.setattr(g, "_read_text", denied)
    elif how == "open_failed":  # every check agrees, and DuckDB still cannot open the file: a manifest written for bytes that are not a database
        junk = b"this is not a database file " * 40
        import hashlib

        data.write_bytes(junk)
        m = json.loads(manifest.read_text(encoding="utf-8"))
        m["size"], m["sha1"], m["sha256"] = len(junk), hashlib.sha1(junk).hexdigest(), hashlib.sha256(junk).hexdigest()  # noqa: S324
        manifest.write_text(json.dumps(m), encoding="utf-8")
        p = json.loads(pointer.read_text(encoding="utf-8"))
        p["size"], p["sha1"] = len(junk), m["sha1"]
        pointer.write_text(json.dumps(p), encoding="utf-8")
    else:
        raise AssertionError(how)


@pytest.mark.parametrize("reason", REASONS)
def test_every_rejected_snapshot_reason_reaches_the_warehouse_caller_with_the_real_files_own_error(tmp_path, monkeypatch, reason):
    src = tmp_path / "w.duckdb"
    seed(src, 1.0)
    gen = g.publish(src, clock=lambda: T0).generation_id
    tamper(src, gen, reason, monkeypatch)
    lock_main(monkeypatch, src, LOCK_POSIX)
    with pytest.raises(duckdb.IOException) as caught:
        Warehouse(src, read_only=True)
    f = caught.value.failure
    assert (f["main_reason"], f["snapshot_reason"]) == ("locked_by_writer", reason)
    assert f["snapshot_detail"], "it says what disagreed"
    assert "Conflicting lock is held" in str(caught.value) and reason in str(caught.value)


@pytest.mark.parametrize("reason", REASONS)
def test_every_rejected_snapshot_reason_reaches_the_chart_helpers_payload(tmp_path, monkeypatch, capsys, reason):
    src = tmp_path / "w.duckdb"
    seed(src, 1.0)
    gen = g.publish(src, clock=lambda: T0).generation_id
    tamper(src, gen, reason, monkeypatch)
    lock_main(monkeypatch, src, LOCK_WINDOWS)
    monkeypatch.setattr(chart_bars, "WAIT_SECONDS", 0)
    capsys.readouterr()
    code = chart_bars.main(["OLD", "--warehouse", str(src)])
    body = json.loads(capsys.readouterr().out)
    assert code != 0 and body["bars"] == [], "no data is an explicit failure, not an empty success"
    f = body["provenance"]["failure"]
    assert (f["main_reason"], f["snapshot_reason"]) == ("locked_by_writer", reason)
    assert "being used by another process" in f["main_error"]


def test_the_chart_helper_answers_from_the_replacement_when_the_generation_disappears_after_validation(tmp_path, monkeypatch, capsys):
    src = tmp_path / "w.duckdb"
    g1, g2, p1, p2 = two_generations(src)
    g.pointer_path(src).write_text(p1, encoding="utf-8")
    old = src.with_name(g.data_name(src, g1))
    real = duckdb.connect
    raced = []

    def connect(database, *a, **k):
        if str(database) == str(src):
            raise duckdb.IOException(LOCK_WINDOWS.format(p=src))
        if str(database) == str(old) and not raced:
            raced.append(1)
            g.pointer_path(src).write_text(p2, encoding="utf-8")
            drop(src, g1)
        return real(database, *a, **k)

    monkeypatch.setattr(duckdb, "connect", connect)
    monkeypatch.setattr(chart_bars, "WAIT_SECONDS", 0)
    capsys.readouterr()
    assert chart_bars.main(["OLD", "--warehouse", str(src)]) == 0
    body = json.loads(capsys.readouterr().out)
    assert [b["c"] for b in body["bars"]] == [1.0, 2.0]  # generation 2 holds both days; generation 1 held one
    p = body["provenance"]
    assert p["source"] == "snapshot" and p["generation_id"] == g2 and p["fallback_reason"] == "locked_by_writer"


def test_a_real_writer_lock_with_no_usable_snapshot_keeps_the_real_files_message_and_names_the_snapshot_reason(tmp_path):
    src = tmp_path / "w.duckdb"
    seed(src, 1.0)
    holder = hold(src)  # a second process really holds the file; no snapshot was ever published
    try:
        with pytest.raises(duckdb.IOException) as caught:
            Warehouse(src, read_only=True)
    finally:
        release(holder)
    f = caught.value.failure
    assert (f["main_reason"], f["snapshot_reason"]) == ("locked_by_writer", "absent")
    assert "being used by another process" in str(caught.value) or "Conflicting lock" in str(caught.value)


# ---- T5-R2: an "unchanged" generation must be usable --------------------------------------------------------------------------------------------------------------------------------------------
def flip_one_byte(path) -> None:
    raw = bytearray(path.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    path.write_bytes(bytes(raw))  # same length, different content


def test_same_size_damage_of_the_current_generation_with_an_unchanged_source_is_repaired_not_reported_unchanged(tmp_path):
    src = tmp_path / "w.duckdb"
    seed(src, 1.0)
    first = g.publish(src, clock=lambda: T0)
    flip_one_byte(src.with_name(g.data_name(src, first.generation_id)))
    with pytest.raises(g.SnapshotUnavailable) as bad:
        g.resolve(src)
    assert bad.value.reason == "hash_mismatch"
    again = g.publish(src, clock=lambda: T0.replace(minute=30))
    assert again.outcome == "published" and again.generation_id != first.generation_id, "a known-unusable fallback is never advertised as unchanged"
    now = g.resolve(src)
    assert now.generation_id == again.generation_id
    con = duckdb.connect(str(now.path), read_only=True)
    assert con.execute("SELECT close FROM bars").fetchone()[0] == 1.0
    con.close()
    assert g.read_status(src)["last_attempt"]["outcome"] == "published"


def test_a_healthy_unchanged_generation_keeps_the_fast_path_and_is_not_hashed_again(tmp_path, monkeypatch):
    src = tmp_path / "w.duckdb"
    seed(src, 1.0)
    first = g.publish(src, clock=lambda: T0)
    hashed = []
    real = g._sha1_of
    monkeypatch.setattr(g, "_sha1_of", lambda p: hashed.append(p) or real(p))
    second = g.publish(src, clock=lambda: T0.replace(minute=30))
    assert second.outcome == "unchanged" and second.generation_id == first.generation_id
    assert hashed == [], "the content hash of a generation this process wrote is already known"
    assert len(g._generations(src)) == 1


def test_a_damaged_current_generation_that_cannot_be_replaced_is_an_explicit_failure_that_names_the_damage(tmp_path):
    src = tmp_path / "w.duckdb"
    seed(src, 1.0)
    first = g.publish(src, clock=lambda: T0)
    flip_one_byte(src.with_name(g.data_name(src, first.generation_id)))
    res = g.publish(src, clock=lambda: T0.replace(minute=30), hooks={"validate": lambda: (_ for _ in ()).throw(OSError("copy would not open"))})
    assert res.outcome == "failed" and res.stage == "validate"
    attempt = g.read_status(src)["last_attempt"]
    assert attempt["outcome"] == "failed" and attempt["current_unusable"] == "hash_mismatch"
    with pytest.raises(g.SnapshotUnavailable):
        g.resolve(src)  # nothing pretends the damaged generation is fine


def test_the_refresh_wrapper_repairs_a_damaged_generation_and_still_says_false_for_a_healthy_unchanged_one(tmp_path):
    src = tmp_path / "w.duckdb"
    seed(src, 1.0)
    assert snapshot.refresh(src) is True
    assert snapshot.refresh(src) is False  # healthy and unchanged
    gen = g.resolve(src)
    flip_one_byte(gen.path)
    assert snapshot.refresh(src) is True  # damaged: a fresh generation is in place
    assert g.resolve(src).generation_id != gen.generation_id
    assert snapshot.refresh(src) is False


def test_the_refresh_wrapper_reports_false_when_a_damaged_generation_cannot_be_replaced(tmp_path, monkeypatch):
    src = tmp_path / "w.duckdb"
    seed(src, 1.0)
    snapshot.refresh(src)
    flip_one_byte(g.resolve(src).path)
    real = g.publish
    monkeypatch.setattr(g, "publish", lambda s, **k: real(s, hooks={"copy": lambda: (_ for _ in ()).throw(OSError("disk full"))}, **k))
    assert snapshot.refresh(src) is False  # not advertised as current: the damaged generation is still damaged
    attempt = g.read_status(src)["last_attempt"]
    assert attempt["outcome"] == "failed" and attempt["current_unusable"] == "hash_mismatch"


# ---- retention: two generations is a nominal target, not a disk-space ceiling (review note) ---------------------------------------------------------------------------------------------------------
def test_retention_is_a_nominal_target_deferred_deletions_leave_more_files_until_a_later_cleanup(tmp_path):
    src = tmp_path / "w.duckdb"
    held = lambda p: (_ for _ in ()).throw(PermissionError("a reader holds this file"))  # noqa: E731
    seed(src, 1.0)
    g.publish(src, clock=lambda: T0, remove=held)
    deferred_total = []
    for n in range(2, 6):
        seed(src, float(n), date(2026, 11, 12 + n))
        res = g.publish(src, clock=lambda n=n: T0.replace(minute=n), remove=held)  # every deletion is refused: nothing the cleanup wants to remove can go
        deferred_total += list(res.deferred)
    assert len(g._generations(src)) == 5 > g.KEEP and deferred_total, "five generations stayed on disk although two is the target, and the status said which were deferred"
    assert set(g.read_status(src)["cleanup"]["deferred"])  # advisory, readable
    seed(src, 6.0, date(2026, 11, 18))
    final = g.publish(src, clock=lambda: T0.replace(minute=7))  # the holds are over: this cleanup removes what the earlier ones could not
    assert len(g._generations(src)) == g.KEEP and final.deferred == ()
    assert g.resolve(src).generation_id == final.generation_id


# ---- T5-R5: a lock is recognised by DuckDB's wording, not by a substring of a path -----------------------------------------------------------------------------------------------------------
PATH_NAMES = ["clock.duckdb", "block.duckdb", "lock.duckdb", "clockwork/w.duckdb", "unlocked/blocks/w.duckdb"]


@pytest.mark.parametrize("name", PATH_NAMES)
def test_a_corrupt_file_whose_path_contains_lock_is_unreadable_not_locked(tmp_path, name):
    src = tmp_path / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"this is not a database file " * 40)
    with pytest.raises(duckdb.IOException) as err:
        duckdb.connect(str(src), read_only=True)  # DuckDB's real message, with the path in it
    assert g.classify_main_failure(src, err.value) == "main_unreadable"


@pytest.mark.parametrize("name", PATH_NAMES)
def test_corruption_through_warehouse_reports_the_real_file_as_unreadable_and_uses_the_snapshot(tmp_path, name):
    src = tmp_path / name
    src.parent.mkdir(parents=True, exist_ok=True)
    seed(src, 1.0)
    gen = g.publish(src, clock=lambda: T0).generation_id
    src.write_bytes(b"this is not a database file " * 40)
    with Warehouse(src, read_only=True) as w:
        assert w.provenance["generation_id"] == gen and w.provenance["fallback_reason"] == "main_unreadable"


@pytest.mark.parametrize("name", PATH_NAMES)
def test_other_failures_naming_lock_in_the_path_are_not_a_writer(tmp_path, name):
    src = tmp_path / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x")
    for text in (f'IO Error: Cannot open file "{src}": Access is denied.', f'IO Error: Cannot read from file "{src}": the file is truncated', f"IO Error: {src}: Permission denied"):
        assert g.classify_main_failure(src, duckdb.IOException(text)) == "main_unreadable", text


@pytest.mark.parametrize("name", PATH_NAMES)
def test_a_real_lock_message_is_still_a_writer_even_when_the_path_names_lock(tmp_path, name):
    src = tmp_path / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x")
    for fmt in (LOCK_WINDOWS, LOCK_POSIX):
        assert g.classify_main_failure(src, duckdb.IOException(fmt.format(p=src))) == "locked_by_writer"


def test_a_missing_file_is_missing_even_when_its_path_names_a_lock(tmp_path):
    assert g.classify_main_failure(tmp_path / "locks" / "w.duckdb", duckdb.IOException("IO Error: Cannot open file")) == "main_missing"


def test_a_real_second_process_lock_is_recognised_in_a_directory_named_for_clocks(tmp_path):
    src = tmp_path / "clockblock" / "lock.duckdb"
    src.parent.mkdir()
    seed(src, 1.0)
    holder = hold(src)
    try:
        with pytest.raises(duckdb.IOException) as err:
            duckdb.connect(str(src), read_only=True)
        assert g.classify_main_failure(src, err.value) == "locked_by_writer"
    finally:
        release(holder)
