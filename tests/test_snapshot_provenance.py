"""Provenance of what a Warehouse read: the real file, or a validated generation and why (audit T5, WP07). Synthetic DuckDB files in temporary directories only."""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone

import duckdb
import pytest

from athena.warehouse import generations as g
from athena.warehouse.warehouse import Warehouse, _open_with

T0 = datetime(2026, 11, 15, 10, 15, tzinfo=timezone.utc)


def make(path, day="2026-11-13"):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE IF NOT EXISTS bars (symbol VARCHAR, day DATE, close DOUBLE)")
    con.execute("DELETE FROM bars")
    con.execute("INSERT INTO bars VALUES ('A', ?, 1.0)", [day])
    con.close()


def hold(src):
    holder = subprocess.Popen([sys.executable, "-c", f"import duckdb,time; c=duckdb.connect(r'{src}'); c.execute('UPDATE bars SET close = 99'); print('held', flush=True); time.sleep(30)"], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    return holder


def test_the_real_file_is_reported_as_main_identified_by_what_was_observed_of_it(tmp_path):
    src = tmp_path / "w.duckdb"
    make(src)
    with Warehouse(src, read_only=True) as w:
        p = w.provenance
    st = src.stat()
    assert p["source"] == "main" and p["generation_basis"] == "observed" and p["generation_id"] == f"main:{st.st_size}:{st.st_mtime_ns}"
    assert p["fallback_reason"] is None and p["snapshot_created_at"] is None and p["validated"] is False  # nothing vouches for a main file's version


def test_a_snapshot_answer_carries_the_generation_its_creation_time_its_data_dates_and_the_reason(tmp_path):
    src = tmp_path / "w.duckdb"
    make(src, "2026-11-13")
    res = g.publish(src, job="prices", clock=lambda: T0)
    holder = hold(src)
    try:
        con, p = _open_with(src, True, 0, lambda s: None)
        con.close()
    finally:
        holder.kill()
        time.sleep(0.2)
    assert p["source"] == "snapshot" and p["generation_id"] == res.generation_id and p["generation_basis"] == "manifest" and p["validated"] is True
    assert p["fallback_reason"] == "locked_by_writer"
    assert p["snapshot_created_at"] == "2026-11-15T10:15:00Z" and p["data_as_of"]["daily_bars"] == "2026-11-13"  # when the copy was taken is not what its data stops at
    assert p["snapshot_status"]["last_attempt"]["outcome"] == "published" and p["snapshot_status"]["current_generation"] == res.generation_id


def test_a_failed_attempt_after_a_good_generation_is_visible_in_the_status_the_answer_carries(tmp_path):
    src = tmp_path / "w.duckdb"
    make(src)
    good = g.publish(src, clock=lambda: T0)
    make(src, "2026-11-14")
    g.publish(src, clock=lambda: T0, hooks={"validate": lambda: (_ for _ in ()).throw(OSError("copy would not open"))})
    holder = hold(src)
    try:
        con, p = _open_with(src, True, 0, lambda s: None)
        con.close()
    finally:
        holder.kill()
        time.sleep(0.2)
    assert p["generation_id"] == good.generation_id  # the older generation answered
    assert p["snapshot_status"]["last_attempt"]["outcome"] == "failed" and p["snapshot_status"]["last_attempt"]["stage"] == "validate"  # and the answer says a newer one was attempted and failed
    assert p["snapshot_status"]["last_success"]["generation_id"] == good.generation_id


def test_the_reasons_the_real_file_could_not_be_used_are_told_apart(tmp_path):
    missing = tmp_path / "absent.duckdb"
    assert g.classify_main_failure(missing, duckdb.IOException("no such file")) == "main_missing"
    real = tmp_path / "w.duckdb"
    make(real)
    assert g.classify_main_failure(real, duckdb.IOException('IO Error: Could not set lock on file "w.duckdb": Conflicting lock is held in python.exe (PID 1)')) == "locked_by_writer"
    assert g.classify_main_failure(real, duckdb.IOException("Invalid Input Error: the file is not a database")) == "main_unreadable"


def test_no_validated_generation_and_a_locked_file_is_an_error_with_no_provenance_to_claim(tmp_path):
    src = tmp_path / "w.duckdb"
    make(src)
    holder = hold(src)
    try:
        with pytest.raises(duckdb.IOException):
            _open_with(src, True, 0, lambda s: None)
    finally:
        holder.kill()
        time.sleep(0.2)


def _chart_json(path, capsys):
    import json

    from athena.warehouse import chart_bars

    capsys.readouterr()
    chart_bars.main(["OLD", "--warehouse", str(path)])
    return json.loads(capsys.readouterr().out)


def test_the_chart_helper_reports_which_source_the_bars_came_from(tmp_path, capsys):
    from datetime import date

    from athena.pit.bhavcopy import Row

    path = tmp_path / "w.duckdb"
    w = Warehouse(path)
    w.upsert_day(date(2022, 12, 15), {"OLD": Row("OLD", "EQ", 10, 10, 10, 10, 1.0)}, T0)
    w.close()
    body = _chart_json(path, capsys)
    assert len(body["bars"]) == 1 and body["provenance"]["source"] == "main" and body["provenance"]["generation_id"].startswith("main:")
    res = g.publish(path, clock=lambda: T0)
    holder = subprocess.Popen([sys.executable, "-c", f"import duckdb,time; c=duckdb.connect(r'{path}'); c.execute('SELECT 1'); print('held', flush=True); time.sleep(30)"], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    try:
        body = _chart_json(path, capsys)
    finally:
        holder.kill()
        time.sleep(0.2)
    p = body["provenance"]
    assert len(body["bars"]) == 1 and p["source"] == "snapshot" and p["generation_id"] == res.generation_id and p["fallback_reason"] == "locked_by_writer"
    assert p["snapshot_created_at"] == "2026-11-15T10:15:00Z" and p["data_as_of"]["daily_bars"] == "2022-12-15"


def test_an_in_memory_warehouse_is_labelled_as_such(tmp_path):
    from athena.warehouse.chart_bars import chart_bars

    w = Warehouse(":memory:")
    assert chart_bars(w, "NONE", 10)["provenance"]["source"] == "memory"
