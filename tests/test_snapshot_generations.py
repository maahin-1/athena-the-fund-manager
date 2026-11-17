"""Snapshot generations (audit T5, WP07): a reader returns a validated generation or an explicit failure; a failed publication changes nothing a reader sees; cleanup and a moving pointer cannot make a
reader use a half-published or mismatched generation. Synthetic DuckDB files in temporary directories only."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from athena.ops import snapshot
from athena.warehouse import generations as g
from athena.warehouse.warehouse import _open

T0 = datetime(2026, 11, 15, 10, 15, tzinfo=timezone.utc)


def make(path, day="2026-11-13", value=1.0):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE IF NOT EXISTS bars (symbol VARCHAR, day DATE, close DOUBLE)")
    con.execute("DELETE FROM bars")
    con.execute("INSERT INTO bars VALUES ('A', ?, ?)", [day, value])
    con.close()


def at(n):
    return lambda: T0 + timedelta(seconds=n)


def pub(src, n=0, **kw):
    return g.publish(src, clock=at(n), **kw)


def listing(path):
    return sorted(p.name for p in path.parent.iterdir())


def raiser(exc):
    def hook():
        raise exc

    return hook


@pytest.fixture(autouse=True)
def fresh_cache():
    g._VERIFIED.clear()
    yield
    g._VERIFIED.clear()


@pytest.fixture
def src(tmp_path):
    path = tmp_path / "w.duckdb"
    make(path)
    return path


# --- publication ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------
def test_a_publication_writes_the_copy_a_manifest_that_pins_it_and_the_pointer(src):
    res = pub(src, job="prices")
    assert res.outcome == "published" and g.GEN_RE.match(res.generation_id)
    gen = g.resolve(src)
    data = gen.path.read_bytes()
    m = gen.manifest
    assert m["schema"] == g.MANIFEST_SCHEMA and m["generation_id"] == res.generation_id and m["file"] == gen.path.name == f"w.snap.{res.generation_id}.duckdb"
    assert m["size"] == len(data) and m["sha1"] == hashlib.sha1(data).hexdigest() and m["sha256"] == hashlib.sha256(data).hexdigest()
    assert m["created_at"] == "2026-11-15T10:15:00Z" and m["holder_job"] == "prices" and m["source"] == {"size": src.stat().st_size, "mtime_ns": src.stat().st_mtime_ns}
    assert m["data_as_of"] == {"daily_bars": "2026-11-13", "index_bars": None}  # read from the copy; the creation time is a different fact
    assert m["created_at"][:10] != m["data_as_of"]["daily_bars"]
    pointer = json.loads(g.pointer_path(src).read_text())
    assert pointer["generation_id"] == res.generation_id and pointer["size"] == m["size"] and pointer["sha1"] == m["sha1"] and pointer["file"] == m["file"]
    assert duckdb.connect(str(gen.path), read_only=True).execute("SELECT close FROM bars").fetchone()[0] == 1.0


def test_an_unchanged_source_publishes_nothing_and_a_changed_one_publishes_a_new_generation(src):
    first = pub(src, 0)
    assert pub(src, 1).outcome == "unchanged" and pub(src, 1).generation_id == first.generation_id
    make(src, "2026-11-14", 2.0)
    second = pub(src, 2)
    assert second.outcome == "published" and second.generation_id != first.generation_id
    assert g.resolve(src).manifest["data_as_of"]["daily_bars"] == "2026-11-14"


def test_a_missing_source_is_not_an_attempt(tmp_path):
    res = pub(tmp_path / "absent.duckdb")
    assert res.outcome == "unchanged" and not g.status_path(tmp_path / "absent.duckdb").exists()


def test_only_the_two_newest_generations_stay_and_the_earlier_scheme_copy_is_removed(src):
    legacy = src.with_suffix(".snap.duckdb")
    legacy.write_bytes(b"old single-file copy")
    ids = []
    for n in range(4):
        make(src, "2026-11-1%d" % (n + 1), float(n))
        ids.append(pub(src, n).generation_id)
    names = listing(src)
    assert [x for x in names if x.endswith(".duckdb") and ".snap." in x] == [f"w.snap.{i}.duckdb" for i in ids[-2:]]
    assert [x for x in names if x.endswith(".json") and x != "w.snap.status.json"] == [f"w.snap.{i}.json" for i in ids[-2:]]
    assert not legacy.exists() and g.resolve(src).generation_id == ids[-1]
    assert ids[1] in json.loads(g.status_path(src).read_text())["cleanup"]["removed"]  # the last publication's cleanup removed the third-newest


def test_refresh_reports_only_whether_a_new_generation_is_in_place(src, tmp_path):
    assert snapshot.refresh(src) is True and snapshot.refresh(src) is False and snapshot.refresh(tmp_path / "absent.duckdb") is False
    make(src, "2026-11-14")
    assert snapshot.refresh(src) is True


# --- publication failures: a reader sees the previous generation, exactly --------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("stage", ["copy", "validate", "manifest", "pointer"])
def test_a_failed_publication_leaves_the_previous_generation_and_pointer_untouched_and_no_orphans(src, stage):
    first = pub(src, 0)
    gen1 = g.resolve(src)
    before = {"pointer": g.pointer_path(src).read_bytes(), "manifest": src.with_name(g.manifest_name(src, first.generation_id)).read_bytes(), "data": gen1.path.read_bytes()}
    names_before = listing(src)
    make(src, "2026-11-14", 5.0)
    res = pub(src, 1, hooks={stage: raiser(OSError(f"disk full at {stage}"))})
    assert res.outcome == "failed" and res.stage == stage and f"disk full at {stage}" in res.error
    assert g.pointer_path(src).read_bytes() == before["pointer"]
    assert src.with_name(g.manifest_name(src, first.generation_id)).read_bytes() == before["manifest"] and gen1.path.read_bytes() == before["data"]
    assert g.resolve(src).generation_id == first.generation_id  # a reader still gets the validated previous generation
    assert [n for n in listing(src) if n not in names_before and n not in ("w.snap.status.json", "w.duckdb.wal")] == []  # no tmp file, no orphan copy, no orphan manifest
    status = g.read_status(src)
    assert status["last_attempt"]["outcome"] == "failed" and status["last_attempt"]["stage"] == stage and f"disk full at {stage}" in status["last_attempt"]["error"]
    assert status["current_generation"] == first.generation_id and status["last_success"]["generation_id"] == first.generation_id
    assert pub(src, 2).outcome == "published"  # the next attempt is not blocked by the failure


def test_a_first_publication_that_fails_leaves_no_snapshot_at_all_and_says_why(src):
    res = pub(src, hooks={"pointer": raiser(OSError("cannot replace the pointer"))})
    assert res.outcome == "failed" and res.stage == "pointer"
    with pytest.raises(g.SnapshotUnavailable) as e:
        g.resolve(src)
    assert e.value.reason == g.ABSENT
    assert g.read_status(src)["current_generation"] is None and g.read_status(src)["last_success"] is None
    assert [n for n in listing(src) if ".snap." in n and n != "w.snap.status.json"] == []


def test_a_source_that_changes_while_it_is_copied_is_not_published(src):
    pub(src, 0)
    make(src, "2026-11-14")
    res = pub(src, 1, hooks={"copy": lambda: os.utime(src, ns=(1, 1))})  # the identity read before the copy no longer matches after it
    assert res.outcome == "failed" and res.stage == "copy" and "changed while it was being copied" in res.error


def test_the_status_is_a_separate_file_and_a_damaged_one_changes_nothing_a_reader_sees(src):
    first = pub(src, 0)
    g.status_path(src).write_text("{not json", encoding="utf-8")
    assert g.read_status(src) is None and g.resolve(src).generation_id == first.generation_id
    make(src, "2026-11-14")
    assert pub(src, 1).outcome == "published" and g.read_status(src)["last_attempt"]["outcome"] == "published"
    assert "last_attempt" not in json.loads(src.with_name(g.manifest_name(src, g.resolve(src).generation_id)).read_text())  # a manifest never carries a status


def test_a_cleanup_that_cannot_remove_a_file_is_reported_and_does_not_fail_the_publication(src):
    ids = []
    for n in range(2):
        make(src, "2026-11-1%d" % (n + 1))
        ids.append(pub(src, n).generation_id)
    make(src, "2026-11-19")

    def stuck(path):
        raise PermissionError(f"{path.name} is in use")

    res = pub(src, 2, remove=stuck)
    assert res.outcome == "published" and ids[0] in res.deferred and g.resolve(src).generation_id == res.generation_id
    assert g.read_status(src)["cleanup"]["deferred"] == list(res.deferred)
    assert src.with_name(g.data_name(src, ids[0])).exists()  # still there, still validates, retried by the next publication
    assert g.resolve(src).generation_id == res.generation_id


def test_a_failing_cleanup_stage_never_fails_the_publication(src):
    res = pub(src, hooks={"cleanup": raiser(RuntimeError("boom"))})
    assert res.outcome == "published" and res.deferred == ("cleanup",) and g.resolve(src).generation_id == res.generation_id


# --- the reader: a validated generation or an explicit failure ----------------------------------------------------------------------------------------------------------------------------
def _edit_json(path, change):
    value = json.loads(path.read_text())
    change(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def _tamper(src, how):
    gen = g.resolve(src)
    gid = gen.generation_id
    pointer, manifest, data = g.pointer_path(src), src.with_name(g.manifest_name(src, gid)), gen.path
    g._VERIFIED.clear()
    if how == "pointer_absent":
        pointer.unlink()
    elif how == "pointer_truncated":
        pointer.write_text(pointer.read_text()[:20], encoding="utf-8")
    elif how == "pointer_wrong_schema":
        _edit_json(pointer, lambda v: v.update(schema="something/9"))
    elif how == "pointer_path_traversal_id":
        _edit_json(pointer, lambda v: v.update(generation_id="../../x"))
    elif how == "pointer_names_another_file":
        _edit_json(pointer, lambda v: v.update(file="other.duckdb"))
    elif how == "pointer_size_not_a_number":
        _edit_json(pointer, lambda v: v.update(size="12"))
    elif how == "manifest_missing":
        manifest.unlink()
    elif how == "manifest_truncated":
        manifest.write_text(manifest.read_text()[:30], encoding="utf-8")
    elif how == "manifest_other_generation":
        _edit_json(manifest, lambda v: v.update(generation_id="20200101T000000Z-aaaaaa"))
    elif how == "manifest_wrong_schema":
        _edit_json(manifest, lambda v: v.update(schema="something/9"))
    elif how == "manifest_size_differs_from_pointer":
        _edit_json(manifest, lambda v: v.update(size=v["size"] + 1))
    elif how == "manifest_hash_differs_from_pointer":
        _edit_json(manifest, lambda v: v.update(sha1="0" * 40))
    elif how == "manifest_without_size":
        _edit_json(manifest, lambda v: v.pop("size"))
    elif how == "data_longer":
        with open(data, "ab") as f:
            f.write(b"x")
    elif how == "data_one_byte_changed_same_size":
        raw = bytearray(data.read_bytes())
        raw[len(raw) // 2] ^= 0xFF
        data.write_bytes(bytes(raw))
    elif how == "data_missing":
        data.unlink()
    else:
        raise AssertionError(how)


CASES = [
    ("pointer_absent", g.ABSENT), ("pointer_truncated", g.POINTER_INVALID), ("pointer_wrong_schema", g.POINTER_INVALID), ("pointer_path_traversal_id", g.POINTER_INVALID),
    ("pointer_names_another_file", g.MISMATCH), ("pointer_size_not_a_number", g.POINTER_INVALID), ("manifest_missing", g.VANISHED), ("manifest_truncated", g.MANIFEST_INVALID),
    ("manifest_other_generation", g.MISMATCH), ("manifest_wrong_schema", g.MANIFEST_INVALID), ("manifest_size_differs_from_pointer", g.MISMATCH),
    ("manifest_hash_differs_from_pointer", g.MISMATCH), ("manifest_without_size", g.MANIFEST_INVALID), ("data_longer", g.SIZE_MISMATCH),
    ("data_one_byte_changed_same_size", g.HASH_MISMATCH), ("data_missing", g.VANISHED),
]


@pytest.mark.parametrize("how, reason", CASES, ids=[c[0] for c in CASES])
def test_every_disagreement_is_an_explicit_failure_never_a_generation(src, how, reason):
    pub(src)
    _tamper(src, how)
    with pytest.raises(g.SnapshotUnavailable) as raised:
        g.resolve(src)
    assert raised.value.reason == reason
    assert raised.value.detail  # every failure says what disagreed


def test_a_generation_that_validated_is_returned_with_the_facts_it_was_validated_against(src):
    pub(src)
    gen = g.resolve(src)
    assert gen.path.name == gen.manifest["file"] and gen.path.stat().st_size == gen.manifest["size"]


def test_the_content_hash_is_computed_once_per_change_of_the_file_not_once_per_read(src, monkeypatch):
    pub(src)
    g._VERIFIED.clear()  # (intentional change, T5-R2: the process that published a generation already knows its hash, so a reader in another process is simulated by forgetting what was verified)
    calls = []
    real = g._sha1_of
    monkeypatch.setattr(g, "_sha1_of", lambda p: calls.append(p) or real(p))
    for _ in range(5):
        g.resolve(src)
    assert len(calls) == 1
    gen = g.resolve(src)
    raw = bytearray(gen.path.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    gen.path.write_bytes(bytes(raw))  # a changed file (new modification time) is hashed again, and caught
    with pytest.raises(g.SnapshotUnavailable) as e:
        g.resolve(src)
    assert e.value.reason == g.HASH_MISMATCH and len(calls) == 2


# --- a pointer that moves, and cleanup that runs, while a reader is reading ---------------------------------------------------------------------------------------------------------------
def test_a_pointer_that_moves_after_the_read_gives_a_consistent_validated_pair_not_a_mixture(src):
    first = pub(src, 0)

    def move():
        make(src, "2026-11-14", 9.0)
        pub(src, 1)

    got = g.resolve(src, after_pointer=move)
    assert got.generation_id == first.generation_id  # the generation the pointer named when it was read, whole
    assert got.manifest["generation_id"] == got.generation_id and got.path.name == g.data_name(src, got.generation_id)
    assert hashlib.sha1(got.path.read_bytes()).hexdigest() == got.manifest["sha1"]
    assert g.resolve(src).generation_id != first.generation_id  # and the next read gets the new one


def test_cleanup_that_removes_the_generation_between_the_pointer_read_and_its_use_is_retried_through_a_fresh_pointer(src):
    first = pub(src, 0)

    def move_twice():
        for n in (1, 2):
            make(src, "2026-11-1%d" % (n + 3), float(n))
            pub(src, n)

    got = g.resolve(src, after_pointer=move_twice)
    assert not src.with_name(g.data_name(src, first.generation_id)).exists()  # the first generation is gone
    assert got.generation_id != first.generation_id and got.manifest["generation_id"] == got.generation_id
    assert hashlib.sha1(got.path.read_bytes()).hexdigest() == got.manifest["sha1"]


def test_a_generation_removed_with_no_replacement_is_an_explicit_vanished_failure(src):
    pub(src)

    def delete_all():
        for p in src.parent.iterdir():
            if ".snap." in p.name and p.name.endswith((".duckdb", ".json")) and p.name != "w.snap.status.json":
                p.unlink()

    with pytest.raises(g.SnapshotUnavailable) as e:
        g.resolve(src, after_pointer=delete_all)
    assert e.value.reason == g.VANISHED


def test_a_reader_holding_the_previous_generation_open_survives_cleanup_which_either_removes_or_defers_it(src):
    ids = [pub(src, 0).generation_id]
    held = duckdb.connect(str(g.resolve(src).path), read_only=True)  # a reader has the first generation open
    outcomes = []
    for n in (1, 2, 3):
        make(src, "2026-11-1%d" % (n + 3), float(n))
        res = pub(src, n)
        ids.append(res.generation_id)
        outcomes.append(res)
    assert held.execute("SELECT close FROM bars").fetchone()[0] == 1.0  # still answering, whatever cleanup did
    first_gone = not src.with_name(g.data_name(src, ids[0])).exists()
    deferred = any(ids[0] in r.deferred for r in outcomes)
    assert first_gone or deferred  # removed (nothing held it) or reported as deferred (Windows keeps an open file); never silently left unaccounted
    assert g.resolve(src).generation_id == ids[-1]
    held.close()


def test_a_writer_publishing_while_readers_read_never_yields_anything_but_a_validated_generation_or_an_explicit_failure(src):
    stop = threading.Event()
    problems: list[str] = []
    seen: list[str] = []

    def reader():
        while not stop.is_set():
            try:
                gen = g.resolve(src)
            except g.SnapshotUnavailable as exc:
                if exc.reason not in (g.VANISHED, g.ABSENT):  # these can occur around a publication; any other reason would be a half-published generation
                    problems.append(f"unexpected failure {exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{type(exc).__name__}: {exc}")
                continue
            ok = gen.path.name == gen.manifest["file"] == g.data_name(src, gen.generation_id) and gen.manifest["generation_id"] == gen.generation_id
            if not ok:
                problems.append("manifest and file name disagree")
            seen.append(gen.generation_id)

    pub(src, 0)
    threads = [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    try:
        for n in range(1, 13):
            make(src, "2026-11-%02d" % (n + 1), float(n))
            assert pub(src, n).outcome in ("published", "failed")
            time.sleep(0.01)
    finally:
        stop.set()
        for t in threads:
            t.join(10)
    assert problems == [] and len(seen) > 0


# --- the Python reader (athena.warehouse.warehouse._open) ----------------------------------------------------------------------------------------------------------------------------------
def _hold(src):
    holder = subprocess.Popen([sys.executable, "-c", f"import duckdb,time; c=duckdb.connect(r'{src}'); c.execute('UPDATE bars SET close = 99'); print('held', flush=True); time.sleep(30)"], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    return holder


def test_a_locked_file_is_read_from_a_validated_generation_and_a_damaged_one_is_an_error_not_a_guess(src):
    pub(src)
    holder = _hold(src)
    try:
        con = _open(src, True, 0, lambda s: None)
        assert con.execute("SELECT close FROM bars").fetchone()[0] == 1.0  # the generation: the data as it stood before the writer began
        con.close()
        gen = g.resolve(src)
        raw = bytearray(gen.path.read_bytes())
        raw[len(raw) // 2] ^= 0xFF
        gen.path.write_bytes(bytes(raw))
        g._VERIFIED.clear()
        with pytest.raises(duckdb.IOException):
            _open(src, True, 0, lambda s: None)  # the copy no longer matches its manifest: the busy file stays an error
    finally:
        holder.kill()
        time.sleep(0.2)


def test_a_legacy_single_file_copy_is_not_read(src):
    src.with_suffix(".snap.duckdb").write_bytes(src.read_bytes())  # an earlier scheme's copy with no manifest
    holder = _hold(src)
    try:
        with pytest.raises(duckdb.IOException):
            _open(src, True, 0, lambda s: None)
    finally:
        holder.kill()
        time.sleep(0.2)
