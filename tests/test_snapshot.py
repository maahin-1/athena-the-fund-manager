import os

import duckdb
import pytest

from athena.ops import snapshot
from athena.warehouse.warehouse import Warehouse, snapshot_path


def make(path, value):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE t (v INTEGER)")
    con.execute("INSERT INTO t VALUES (?)", [value])
    con.close()


def test_the_snapshot_path_sits_beside_the_file(tmp_path):
    assert snapshot_path(tmp_path / "warehouse.duckdb") == tmp_path / "warehouse.snap.duckdb"


def test_refresh_copies_the_file_and_leaves_an_up_to_date_copy_alone(tmp_path):
    src = tmp_path / "w.duckdb"
    make(src, 1)
    assert snapshot.refresh(src) is True
    assert duckdb.connect(str(snapshot_path(src)), read_only=True).execute("SELECT v FROM t").fetchone()[0] == 1
    assert snapshot.refresh(src) is False  # already as new as the file
    assert snapshot.refresh(tmp_path / "absent.duckdb") is False


def test_a_reader_gets_the_snapshot_when_a_writer_holds_the_file(tmp_path):
    src = tmp_path / "w.duckdb"
    make(src, 1)
    snapshot.refresh(src)
    # a writer in ANOTHER process holds the file: DuckDB will not let a second open it, in this process or any other
    import subprocess
    import sys
    import time

    holder = subprocess.Popen([sys.executable, "-c", f"import duckdb,time; c=duckdb.connect(r'{src}'); c.execute('UPDATE t SET v = 2'); print('held', flush=True); time.sleep(30)"], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        w = Warehouse.__new__(Warehouse)  # only the opening is under test
        from athena.warehouse.warehouse import _open

        con = _open(src, True, 0, lambda s: None)
        assert con.execute("SELECT v FROM t").fetchone()[0] == 1  # the copy: the data as it stood before the writer began
        with pytest.raises(duckdb.IOException):
            _open(src, False, 0, lambda s: None)  # a writer is never given the copy
        os.remove(snapshot_path(src))
        with pytest.raises(duckdb.IOException):
            _open(src, True, 0, lambda s: None)  # no copy, no data: still an error the caller reports as busy
        del w
    finally:
        holder.kill()
        time.sleep(0.2)
