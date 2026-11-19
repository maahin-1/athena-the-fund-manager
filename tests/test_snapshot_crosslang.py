"""The snapshot contract between the Python writer/reader (`athena.warehouse.generations`) and the Rust reader (`terminal/src/snapshot.rs`), in both directions (audit T5, WP07).

Each side's own tests use fixtures written by that same side, so they cannot show that the two agree. These tests run only when told to, with explicit fixture directories, in an orchestrated sequence:

1. `ATHENA_SNAPSHOT_PY_OUT=<dir> pytest tests/test_snapshot_crosslang.py -k writes`    Python publishes a generation of `<dir>/w.duckdb`.
2. `ATHENA_SNAPSHOT_PY_FIXTURE=<dir> cargo test -- --ignored a_generation_published_by_the_python_writer`   Rust validates and opens it.
3. `ATHENA_SNAPSHOT_RUST_OUT=<dir2> cargo test -- --ignored publishes_generations_for_the_python_reader`   Rust publishes generations.
4. `ATHENA_SNAPSHOT_RUST_OUT=<dir2> pytest tests/test_snapshot_crosslang.py -k reads`    Python validates and opens what Rust published.

In an ordinary run all of them are skipped."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from athena.warehouse import generations as g

PY_OUT = os.environ.get("ATHENA_SNAPSHOT_PY_OUT")
RUST_OUT = os.environ.get("ATHENA_SNAPSHOT_RUST_OUT")


@pytest.mark.skipif(not PY_OUT, reason="cross-language: set ATHENA_SNAPSHOT_PY_OUT to an empty directory")
def test_python_writes_a_generation_for_the_rust_reader():
    main = Path(PY_OUT) / "w.duckdb"
    con = duckdb.connect(str(main))
    con.execute("CREATE TABLE bars (symbol VARCHAR, day DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE)")
    con.execute("INSERT INTO bars VALUES ('A', '2026-11-13', 1.0, 1.0, 1.0, 1.0, 10.0)")
    con.close()
    res = g.publish(main, job="prices", clock=lambda: datetime(2026, 11, 15, 10, 15, tzinfo=timezone.utc))
    assert res.outcome == "published"


@pytest.mark.skipif(not RUST_OUT, reason="cross-language: set ATHENA_SNAPSHOT_RUST_OUT to the directory the Rust writer published into")
def test_python_reads_what_the_rust_writer_published():
    main = Path(RUST_OUT) / "w.duckdb"
    gen = g.resolve(main)  # pointer, manifest, size and SHA-1 all have to agree, as written by Rust
    assert gen.manifest["data_as_of"]["daily_bars"] == "2026-11-13" and gen.manifest["holder_job"] == "prices"
    con = duckdb.connect(str(gen.path), read_only=True)
    assert con.execute("SELECT close FROM bars").fetchone()[0] in (1.0, 2.0)  # the newest generation of the two Rust published (the copy of the file as it stood)
    con.close()
    assert gen.generation_id == g.resolve(main, verify_hash=True).generation_id
