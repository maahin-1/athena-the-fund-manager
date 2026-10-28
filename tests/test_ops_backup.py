from datetime import datetime, timedelta, timezone

import duckdb

from athena.ops import backup


def make(path, rows=3):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE t (a INTEGER, b VARCHAR)")
    con.executemany("INSERT INTO t VALUES (?, ?)", [(i, f"x{i}") for i in range(rows)])
    con.close()


def test_a_store_is_exported_to_parquet_and_comes_back_whole(tmp_path):
    make(tmp_path / "w.duckdb", rows=5)
    state, text = backup.export_one("warehouse", tmp_path / "w.duckdb", tmp_path / "out", keep=2, now=datetime(2026, 10, 28, tzinfo=timezone.utc))
    assert state == "ok" and "exported" in text
    target = tmp_path / "out" / "warehouse" / "20261028T000000Z"
    assert any(p.suffix == ".parquet" for p in target.iterdir())
    restored = duckdb.connect(":memory:")
    restored.execute(f"IMPORT DATABASE '{target.as_posix()}'")
    assert restored.execute("SELECT count(*), sum(a) FROM t").fetchone() == (5, 10)


def test_only_the_newest_exports_stay_and_nothing_else_in_the_folder_is_touched(tmp_path):
    make(tmp_path / "w.duckdb")
    other = tmp_path / "out" / "warehouse" / "notes.txt"
    other.parent.mkdir(parents=True)
    other.write_text("keep me")
    day = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for n in range(4):
        backup.export_one("warehouse", tmp_path / "w.duckdb", tmp_path / "out", keep=2, now=day + timedelta(days=n))
    kept = sorted(p.name for p in (tmp_path / "out" / "warehouse").iterdir() if p.is_dir())
    assert kept == ["20261003T000000Z", "20261004T000000Z"] and other.read_text() == "keep me"


def test_a_missing_store_is_skipped_and_a_store_a_job_is_writing_is_reported_busy_not_waited_for(tmp_path):
    state, text = backup.export_one("derivs", tmp_path / "nope.duckdb", tmp_path / "out", keep=2)
    assert state == "skipped" and "no file" in text
    path = tmp_path / "busy.duckdb"
    holder = duckdb.connect(str(path))
    holder.execute("CREATE TABLE t (a INTEGER)")
    state, text = backup.export_one("warehouse", path, tmp_path / "out", keep=2)
    holder.close()
    assert state in ("skipped", "failed")  # a store another connection holds is not exported, and no half export is left
    assert not list((tmp_path / "out" / "warehouse").glob("*")) if (tmp_path / "out" / "warehouse").exists() else True


def test_the_command_refuses_a_destination_on_the_same_drive_unless_told_otherwise(tmp_path, capsys, monkeypatch):
    make(tmp_path / "w.duckdb")
    monkeypatch.setattr(backup, "STORES", {"warehouse": tmp_path / "w.duckdb"})  # never the real stores in a test
    monkeypatch.setattr(backup, "same_drive", lambda a, b: True)
    assert backup.main(["--to", str(tmp_path / "out")]) == 2
    assert "same drive" in capsys.readouterr().err and not (tmp_path / "out").exists()
    assert backup.main(["--to", str(tmp_path / "out"), "--only", "nonsense", "--allow-same-drive"]) == 2
    assert backup.main(["--to", str(tmp_path / "out"), "--allow-same-drive"]) == 0
    assert (tmp_path / "out" / "warehouse").is_dir()
