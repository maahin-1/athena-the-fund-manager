"""A read-only copy of the warehouse for the screens to read while a long job writes the real file.

DuckDB lets one process write or many read, never both. A build that holds `.athena/warehouse.duckdb` for an hour used to turn every chart into "warehouse busy". Before the supervisor starts a job
that writes the warehouse it calls `refresh`, which publishes a new *generation* of the copy (`athena.warehouse.generations`): an immutable copy and manifest and an atomically replaced pointer, so a
reader (`athena.warehouse.warehouse` in Python, `snapshot.rs` in the terminal) opens the real file and, when a writer holds it, opens only a generation it has validated, and says which one. The copy is
made only while no job of ours writes the file, so it is a consistent checkpoint; a copy that cannot be made leaves the previous generation and pointer exactly as they were, and the failure is recorded
in the separate status file (never in a manifest)."""
from __future__ import annotations

from pathlib import Path

from athena.warehouse import generations
from athena.warehouse.warehouse import DEFAULT_PATH

WAREHOUSE = Path(DEFAULT_PATH)


def publish(source: Path | str, job: str | None = None) -> generations.PublishResult:
    """Publish a new generation of `source` (see `athena.warehouse.generations.publish`). `job` names the job about to hold the file for writing, recorded in the manifest."""
    return generations.publish(source, job=job)


def refresh(source: Path | str, job: str | None = None) -> bool:
    """Publish a new generation of `source`. Returns whether a new generation is in place (False: nothing to copy, already current, or the copy could not be made: read the status for which)."""
    return publish(source, job).outcome == "published"
