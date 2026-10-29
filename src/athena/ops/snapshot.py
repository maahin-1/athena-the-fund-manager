"""A read-only copy of the warehouse for the screens to read while a long job writes the real file.

DuckDB lets one process write or many read, never both. A build that holds `.athena/warehouse.duckdb` for an hour used to turn every chart into "warehouse busy". Before the supervisor starts a job
that writes the warehouse it calls `refresh`, which saves the file beside itself as `warehouse.snap.duckdb`; readers (`athena.warehouse.warehouse` in Python, `snapshot.rs` in the terminal) open the
real file and, when that is locked by a writer, open the copy. The copy is the data as it stood when the job began, replaced the same way before the next job. It is made only while no job of ours
writes the file, so it is a consistent checkpoint; a copy that cannot be made (a reader holds the old one) leaves the old one in place."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from athena.warehouse.warehouse import DEFAULT_PATH, snapshot_path

WAREHOUSE = Path(DEFAULT_PATH)


def refresh(source: Path | str) -> bool:
    """Save `source` as its snapshot. Returns whether a new copy is in place (False: nothing to copy, already current, or the copy could not be made)."""
    src = Path(source)
    dst = snapshot_path(src)
    try:
        if not src.is_file():
            return False
        if dst.is_file() and dst.stat().st_mtime >= src.stat().st_mtime:
            return False  # the copy is already as new as the file
        tmp = dst.with_suffix(".tmp")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
        return True
    except OSError:
        try:
            dst.with_suffix(".tmp").unlink(missing_ok=True)
        except OSError:
            pass
        return False
