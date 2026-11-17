"""Snapshot generations of the warehouse: an immutable copy, an immutable manifest that pins it, and one atomically replaced pointer (audit T5, WP07).

A long job holds `warehouse.duckdb` for writing, so before it starts the supervisor saves a read-only copy for the charts and pages (see `athena.ops.snapshot`). The copy used to be one file replaced in
place, with nothing to say which database generation a reader got, when the copy was taken, or what date its data stops at. This module replaces that with a protocol in which a reader either gets
a generation it has *validated* or an explicit failure, and never a guessed one.

Files beside `X.duckdb` (all names below derive from the stem `X` and a generation id `<gen>` such as `20261115T101500Z-a1b2c3`):

- `X.snap.<gen>.duckdb`   the copy. Never modified after it is published.
- `X.snap.<gen>.json`     the manifest: generation id, file name, byte size, SHA-1 and SHA-256 of the copy (computed while it was copied), creation time, the source's size and modification time,
                          the job that held the lock, and the data's own dates (`data_as_of`, read from the copy, never from a file time). Never modified after it is published.
- `X.snap.current`        the pointer: names the current generation, its size and SHA-1. The only file that is replaced, and replaced atomically. Readers reach a snapshot only through it.
- `X.snap.status.json`    the outcome of the last publication attempt (published, failed at which stage and why), the last success and what cleanup removed or could not remove. **Separate from the
                          manifest**: a failed attempt writes here and never touches a manifest or the pointer, so a failure cannot make a valid generation look invalid or the reverse.

Writer: copy to a temporary name while hashing, validate the copy by opening it read-only, move it to its generation name, write the manifest, replace the pointer, then clean up. A failure at any stage
before the pointer moves leaves the previous pointer and generation exactly as they were and removes the attempt's files. Cleanup runs after the pointer moves and cannot fail the publication.

Reader (`resolve`): read the pointer once, then use only the immutable files of the generation it names. Every check must agree (pointer, manifest, file name, size, content hash) or the result is an
explicit `SnapshotUnavailable` with a reason. A generation that is deleted between the pointer read and the use (cleanup racing the reader) is retried once through a fresh pointer read; it is never
replaced by a guess. The Rust reader in `terminal/src/snapshot.rs` implements the same checks."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

MANIFEST_SCHEMA = "athena.snapshot-manifest/1"
POINTER_SCHEMA = "athena.snapshot-pointer/1"
STATUS_SCHEMA = "athena.snapshot-status/1"
KEEP = 2  # a NOMINAL retention target, not a ceiling: the current generation and the one before it are kept (a reader that validated the previous one a moment ago can still open it), but a deletion deferred because a reader or scanner holds the file (Windows), or orphans a crash left, can leave more on disk until a later publication's cleanup removes them
GEN_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$")
CHUNK = 1024 * 1024
STALE_TMP_SECONDS = 600
POINTER_RETRIES = 5
READ_RETRIES = 6
READ_RETRY_SLEEP = 0.02

# the reasons a snapshot cannot be used; each is explicit, none is "empty"
ABSENT, POINTER_INVALID, MANIFEST_INVALID, MISMATCH, SIZE_MISMATCH, HASH_MISMATCH, VANISHED, UNREADABLE = (
    "absent", "pointer_invalid", "manifest_invalid", "mismatch", "size_mismatch", "hash_mismatch", "vanished", "unreadable")
OPEN_FAILED = "open_failed"  # every check agreed and DuckDB still could not open the generation (only `open_validated` reports it)


class SnapshotUnavailable(Exception):
    """No validated generation can be returned. `reason` is one of the constants above; `detail` says what disagreed."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


@dataclass(frozen=True)
class Generation:
    """A validated generation: the pointer, the manifest and the file agree."""

    generation_id: str
    path: Path
    manifest: Mapping[str, Any]


@dataclass(frozen=True)
class PublishResult:
    outcome: str  # published | unchanged | failed
    generation_id: str | None = None
    stage: str | None = None  # the stage that failed
    error: str | None = None
    removed: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()
    current_unusable: str | None = None  # why the generation the pointer names could not be used when this attempt began or failed (a damaged copy is never reported as unchanged)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stem(source: Path) -> str:
    return source.name[: -len(source.suffix)] if source.suffix else source.name


def pointer_path(source: Path | str) -> Path:
    s = Path(source)
    return s.with_name(f"{_stem(s)}.snap.current")


def status_path(source: Path | str) -> Path:
    s = Path(source)
    return s.with_name(f"{_stem(s)}.snap.status.json")


def data_name(source: Path | str, gen: str) -> str:
    return f"{_stem(Path(source))}.snap.{gen}.duckdb"


def manifest_name(source: Path | str, gen: str) -> str:
    return f"{_stem(Path(source))}.snap.{gen}.json"


def new_generation_id(now: datetime | None = None, token: Callable[[int], str] = secrets.token_hex) -> str:
    return f"{(now or _now()).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{token(3)}"


def _write_atomic(path: Path, text: str, *, sleep: Callable[[float], None] = time.sleep) -> None:
    """Write `text` beside `path` and replace `path` with it in one step. A reader sees the old file or the new one, never half of either."""
    tmp = path.with_name(path.name + f".{secrets.token_hex(3)}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(POINTER_RETRIES):  # on Windows a reader that has the file open for a moment makes the replace fail; it is retried briefly
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == POINTER_RETRIES - 1:
                    raise
                sleep(0.05)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


# --- reading: a validated generation or an explicit failure ----------------------------------------------------------------------------------------------------------------------------------
_VERIFIED: dict[tuple[str, int, int], str] = {}  # (path, size, mtime_ns) -> sha1 already checked, so a large file is hashed once per change, not per request


def _sha1_of(path: Path) -> str:
    h = hashlib.sha1()  # noqa: S324 (an integrity check against accidental damage, not a security boundary)
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _read_text(path: Path, sleep: Callable[[float], None] = time.sleep) -> str:
    """Read a small file. On Windows a file being replaced, or scanned, is briefly unreadable (`PermissionError`); that is retried a few times over about a tenth of a second, because it is a moment
    and not a verdict on the file. After that the failure is explicit (`unreadable`)."""
    for attempt in range(READ_RETRIES):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            if attempt == READ_RETRIES - 1:
                raise
            sleep(READ_RETRY_SLEEP)
    raise AssertionError("unreachable")


def _load_json(path: Path, bad: str) -> dict[str, Any]:
    try:
        text = _read_text(path)
    except FileNotFoundError:
        raise
    except PermissionError as exc:
        raise SnapshotUnavailable(UNREADABLE, f"{path.name} stayed unreadable ({type(exc).__name__})") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise SnapshotUnavailable(bad, f"{path.name} cannot be read ({type(exc).__name__})") from exc
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise SnapshotUnavailable(bad, f"{path.name} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SnapshotUnavailable(bad, f"{path.name} is not an object")
    return value


def _once(source: Path, verify_hash: bool, after_pointer: Callable[[], None] | None) -> Generation:
    """One attempt: raises FileNotFoundError when a file that should exist is gone (the caller decides whether that is a race), SnapshotUnavailable otherwise."""
    pointer = _load_json(pointer_path(source), POINTER_INVALID)  # FileNotFoundError here is "absent", handled by the caller
    gen, file, size, sha1 = pointer.get("generation_id"), pointer.get("file"), pointer.get("size"), pointer.get("sha1")
    if pointer.get("schema") != POINTER_SCHEMA or not isinstance(gen, str) or not GEN_RE.match(gen) or not isinstance(file, str) or not isinstance(size, int) or isinstance(size, bool) or not isinstance(sha1, str):
        raise SnapshotUnavailable(POINTER_INVALID, "the pointer does not have the expected fields")
    if file != data_name(source, gen):
        raise SnapshotUnavailable(MISMATCH, f"the pointer names {file!r} for generation {gen}, not {data_name(source, gen)!r}")
    if after_pointer is not None:
        after_pointer()
    manifest = _load_json(source.with_name(manifest_name(source, gen)), MANIFEST_INVALID)  # the manifest of a generation never changes, so a later pointer move cannot make this pair inconsistent
    for key, want in (("schema", MANIFEST_SCHEMA), ("generation_id", gen), ("file", file)):
        if manifest.get(key) != want:
            raise SnapshotUnavailable(MISMATCH if key != "schema" else MANIFEST_INVALID, f"the manifest's {key} is {manifest.get(key)!r}, the pointer's is {want!r}")
    m_size, m_sha1 = manifest.get("size"), manifest.get("sha1")
    if not isinstance(m_size, int) or isinstance(m_size, bool) or not isinstance(m_sha1, str):
        raise SnapshotUnavailable(MANIFEST_INVALID, "the manifest does not give a size and a SHA-1")
    if m_size != size or m_sha1 != sha1:
        raise SnapshotUnavailable(MISMATCH, "the pointer and the manifest disagree on the size or the content hash")
    data = source.with_name(file)
    st = data.stat()  # FileNotFoundError when cleanup removed it
    if st.st_size != m_size:
        raise SnapshotUnavailable(SIZE_MISMATCH, f"{file} is {st.st_size} bytes; the manifest says {m_size}")
    if verify_hash:
        key = (str(data), st.st_size, st.st_mtime_ns)
        if _VERIFIED.get(key) != m_sha1:
            actual = _sha1_of(data)
            if actual != m_sha1:
                raise SnapshotUnavailable(HASH_MISMATCH, f"{file} does not have the content hash its manifest records")
            _VERIFIED[key] = m_sha1
    return Generation(gen, data, manifest)


def resolve(source: Path | str, *, verify_hash: bool = True, after_pointer: Callable[[], None] | None = None) -> Generation:
    """The validated current generation of `source`'s snapshot, or `SnapshotUnavailable` with a reason. `after_pointer` is called between the pointer read and the use of the generation (a test hook for
    a pointer that moves, or cleanup that runs, in that window)."""
    src = Path(source)
    last: FileNotFoundError | None = None
    for attempt in range(2):
        try:
            return _once(src, verify_hash, after_pointer if attempt == 0 else None)
        except FileNotFoundError as exc:
            if not pointer_path(src).exists():
                raise SnapshotUnavailable(ABSENT, "no snapshot has been published") from exc
            last = exc  # a file named by the pointer is gone: cleanup raced this read; read the pointer again once
    raise SnapshotUnavailable(VANISHED, f"a file of the generation named by the pointer was removed ({last.filename and Path(last.filename).name})") from last


def open_validated(source: Path | str, *, after_validate: Callable[[], None] | None = None) -> tuple[duckdb.DuckDBPyConnection, Generation]:
    """Open the validated current generation read-only: the connection and the generation it is a connection to, or `SnapshotUnavailable`.

    Validating and opening are two steps, and cleanup can remove a generation between them (a newer one was published and this one fell out of the kept two). That is retried once through a fresh pointer
    read, exactly like a removal between the pointer read and the use; a generation still missing after that is `vanished`, and one that is present but that DuckDB cannot open is `open_failed`. `after_validate`
    runs between the two steps on the first attempt (a test hook for that race)."""
    src = Path(source)
    last: SnapshotUnavailable | None = None
    for attempt in range(2):
        gen = resolve(src)
        if attempt == 0 and after_validate is not None:
            after_validate()
        try:
            return duckdb.connect(str(gen.path), read_only=True), gen
        except duckdb.Error as exc:
            if gen.path.exists():
                raise SnapshotUnavailable(OPEN_FAILED, f"{gen.path.name} could not be opened ({type(exc).__name__})") from exc
            last = SnapshotUnavailable(VANISHED, f"{gen.path.name} was removed before it could be opened")
    assert last is not None
    raise last


def main_provenance(source: Path | str) -> dict[str, Any]:
    """Which source answered when the real file was opened: the file itself, identified by what was observed of it (size and modification time), not by a version the database vouches for."""
    p = Path(source)
    try:
        st = p.stat()
        gid = f"main:{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        gid = "main:unobserved"
    return {"source": "main", "source_path": str(p), "generation_id": gid, "generation_basis": "observed", "snapshot_created_at": None, "data_as_of": None, "fallback_reason": None, "validated": False,
            "snapshot_status": None}


def snapshot_provenance(source: Path | str, gen: Generation, fallback_reason: str | None) -> dict[str, Any]:
    """Which source answered when a validated generation was opened because the real file could not be: the generation's manifest facts and the separate publication status."""
    return {"source": "snapshot", "source_path": str(gen.path), "generation_id": gen.generation_id, "generation_basis": "manifest", "snapshot_created_at": gen.manifest.get("created_at"),
            "data_as_of": gen.manifest.get("data_as_of"), "fallback_reason": fallback_reason, "validated": True, "snapshot_status": read_status(source)}


LOCK_PHRASES = ("could not set lock on file", "conflicting lock is held", "being used by another process", "file is already open in", "sharing violation")  # DuckDB's own wording, and Windows' for a file in use
_QUOTED = re.compile(r'"[^"]*"')


def _without_paths(text: str, source: Path) -> str:
    """The error text with every file path taken out: the quoted ones DuckDB prints and any occurrence of the file's own path, directory or name. What is left is the wording of the failure, which a
    directory or file named `clock`, `block` or `lock` cannot change."""
    t = text.lower()
    names = {str(source), source.as_posix(), str(source.parent), source.parent.as_posix(), source.name}
    for name in sorted((n.lower() for n in names if len(n) >= 3), key=len, reverse=True):
        t = t.replace(name, " ")
    return _QUOTED.sub(" ", t)


def classify_main_failure(source: Path | str, error: BaseException) -> str:
    """Why the real file could not be opened: `main_missing`, `locked_by_writer` (the expected case) or `main_unreadable` (anything else, including damage).

    A lock is recognised by the failure's own wording (`LOCK_PHRASES`), never by a substring of a path: a corrupt `clock.duckdb`, or a file under a directory named `blocks`, is not a writer."""
    src = Path(source)
    if not src.exists():
        return "main_missing"
    text = _without_paths(str(error), src)
    return "locked_by_writer" if any(p in text for p in LOCK_PHRASES) else "main_unreadable"


def read_status(source: Path | str) -> dict[str, Any] | None:
    """The last publication status, or None when there is none or it cannot be read (it is advisory and never decides which generation is used)."""
    try:
        value = json.loads(status_path(source).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


# --- writing -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
def default_describe(con: duckdb.DuckDBPyConnection) -> dict[str, str | None]:
    """The dates the copy's data stops at, read from the copy: the latest daily-bar day and the latest index-bar day. A table that does not exist gives None."""
    out: dict[str, str | None] = {}
    for key, sql in (("daily_bars", "SELECT CAST(MAX(day) AS VARCHAR) FROM bars"), ("index_bars", "SELECT CAST(MAX(day) AS VARCHAR) FROM index_bars")):
        try:
            row = con.execute(sql).fetchone()
            out[key] = row[0] if row else None
        except duckdb.Error:
            out[key] = None
    return out


@dataclass
class _Attempt:
    gen: str
    tmp: Path
    data: Path
    manifest: Path
    made: list[Path] = field(default_factory=list)


def _identity(path: Path) -> dict[str, int]:
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _generations(source: Path) -> list[str]:
    stem = _stem(source)
    rx = re.compile(rf"^{re.escape(stem)}\.snap\.([0-9]{{8}}T[0-9]{{6}}Z-[0-9a-f]{{6}})\.(duckdb|json)$")
    return sorted({m.group(1) for p in source.parent.iterdir() if (m := rx.match(p.name))})


def _cleanup(source: Path, current: str, remove: Callable[[Path], None], now: float) -> tuple[list[str], list[str]]:
    """Remove generations beyond `KEEP`, old temporary files and the legacy single-file copy. Returns (removed, deferred); a file that cannot be removed (a reader holds it) is deferred, not an error, and stays
    on disk until a later cleanup: `KEEP` is how many cleanup aims to leave, not a bound on how many files exist."""
    removed: list[str] = []
    deferred: list[str] = []
    gens = _generations(source)
    keep = {current, *[g for g in sorted(gens, reverse=True) if g != current][: KEEP - 1]}
    for gen in gens:
        if gen in keep:
            continue
        data, manifest = source.with_name(data_name(source, gen)), source.with_name(manifest_name(source, gen))
        try:
            if data.exists():
                remove(data)  # the data first: while it stays, the manifest stays and the generation still validates
            if manifest.exists():
                remove(manifest)
            removed.append(gen)
        except OSError:
            deferred.append(gen)
    for tmp in source.parent.iterdir():
        if tmp.name.startswith(f"{_stem(source)}.snap.") and tmp.name.endswith(".tmp"):
            try:
                if now - tmp.stat().st_mtime > STALE_TMP_SECONDS:
                    remove(tmp)
            except OSError:
                pass
    legacy = source.with_suffix(".snap.duckdb")  # the single-file copy of the earlier scheme: nothing reads it any more
    if legacy.is_file():
        try:
            remove(legacy)
            removed.append(legacy.name)
        except OSError:
            deferred.append(legacy.name)
    return removed, deferred


def _write_status(source: Path, attempt: Mapping[str, Any], previous: Mapping[str, Any] | None, current: str | None, removed: list[str], deferred: list[str], at: str) -> None:
    last_success = (previous or {}).get("last_success")
    if attempt["outcome"] == "published":
        last_success = {"at": at, "generation_id": attempt.get("generation_id")}
    body = {"schema": STATUS_SCHEMA, "updated_at": at, "last_attempt": dict(attempt), "last_success": last_success, "current_generation": current, "cleanup": {"removed": removed, "deferred": deferred}}
    try:
        _write_atomic(status_path(source), json.dumps(body, indent=1))
    except OSError:
        pass  # the status is advisory; failing to write it must not fail anything else


def publish(
    source: Path | str,
    *,
    job: str | None = None,
    clock: Callable[[], datetime] = _now,
    token: Callable[[int], str] = secrets.token_hex,
    hooks: Mapping[str, Callable[[], None]] | None = None,
    describe: Callable[[duckdb.DuckDBPyConnection], dict[str, str | None]] = default_describe,
    remove: Callable[[Path], None] = lambda p: p.unlink(),
    sleep: Callable[[float], None] = time.sleep,
) -> PublishResult:
    """Publish a new generation of `source`. `hooks` maps a stage name (`copy`, `validate`, `manifest`, `pointer`, `cleanup`) to a callable run at the start of that stage (tests raise from them).

    Outcomes: `published`, `unchanged` (the source is the one the current generation was taken from), `failed` (a stage before the pointer moved failed: the previous generation and pointer are untouched
    and this attempt's files are gone). A cleanup that cannot remove something is reported in `deferred` and does not fail the publication."""
    src = Path(source)
    run = hooks or {}
    started = clock()
    at = _iso(started)
    previous = read_status(src)
    if not src.is_file():
        return PublishResult("unchanged", error="no source file")  # nothing to copy; not an attempt, and the status is left as it is
    try:
        current = resolve(src, verify_hash=False)
    except SnapshotUnavailable:
        current = None
    identity = _identity(src)
    unusable: str | None = None  # why the generation taken from this very source cannot be used (T5-R2)
    if current is not None and current.manifest.get("source") == identity:
        # the source has not changed since this generation was taken, but "unchanged" is only true if the generation is usable: a same-sized damaged copy passes the cheap checks and would otherwise
        # be kept for ever. The content hash is checked here (once per file and change: a generation this process wrote, or has verified, is not hashed again).
        try:
            resolve(src, verify_hash=True)
            return PublishResult("unchanged", generation_id=current.generation_id)
        except SnapshotUnavailable as damaged:
            unusable = damaged.reason  # fall through: take a fresh copy of the (unchanged, usable) source
    gen = new_generation_id(started, token)
    a = _Attempt(gen, src.with_name(f"{_stem(src)}.snap.{gen}.duckdb.tmp"), src.with_name(data_name(src, gen)), src.with_name(manifest_name(src, gen)))
    stage = "copy"
    try:
        run.get("copy", lambda: None)()
        sha1, sha256, size = hashlib.sha1(), hashlib.sha256(), 0  # noqa: S324
        a.made.append(a.tmp)
        with open(src, "rb") as fin, open(a.tmp, "wb") as fout:
            while chunk := fin.read(CHUNK):
                fout.write(chunk)
                sha1.update(chunk)
                sha256.update(chunk)
                size += len(chunk)
            fout.flush()
            os.fsync(fout.fileno())
        shutil.copystat(src, a.tmp)
        after = _identity(src)
        if after != identity:  # the source changed while it was being copied: the copy is not a consistent checkpoint
            raise OSError("the source changed while it was being copied")
        stage = "validate"
        run.get("validate", lambda: None)()
        con = duckdb.connect(str(a.tmp), read_only=True)
        try:
            con.execute("SELECT 1").fetchone()
            data_as_of = describe(con)
        finally:
            con.close()
        stage = "manifest"
        run.get("manifest", lambda: None)()
        os.replace(a.tmp, a.data)
        a.made.append(a.data)
        manifest = {"schema": MANIFEST_SCHEMA, "generation_id": gen, "file": a.data.name, "size": size, "sha1": sha1.hexdigest(), "sha256": sha256.hexdigest(), "created_at": _iso(started),
                    "source": identity, "source_name": src.name, "holder_job": job, "data_as_of": data_as_of}
        a.made.append(a.manifest)
        _write_atomic(a.manifest, json.dumps(manifest, indent=1), sleep=sleep)
        stage = "pointer"
        run.get("pointer", lambda: None)()
        pointer = {"schema": POINTER_SCHEMA, "generation_id": gen, "file": a.data.name, "manifest": a.manifest.name, "size": size, "sha1": sha1.hexdigest()}
        _write_atomic(pointer_path(src), json.dumps(pointer, indent=1), sleep=sleep)
        try:
            st = a.data.stat()
            _VERIFIED[(str(a.data), st.st_size, st.st_mtime_ns)] = sha1.hexdigest()  # the hash was computed while the copy was written: this process need not read it back to know it
        except OSError:
            pass
    except Exception as exc:  # noqa: BLE001 (every failure before the pointer moved is reported the same way)
        for p in a.made:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        now_current = None
        try:
            now_current = resolve(src).generation_id  # the generation still in place, and only if it is usable
        except SnapshotUnavailable as bad:
            if bad.reason != ABSENT:
                unusable = bad.reason  # a pointer is there and what it names is not usable: the failure says so, so the status never reads as "a good copy is still in place"
        attempt = {"at": at, "outcome": "failed", "stage": stage, "error": f"{type(exc).__name__}: {exc}"[:300], "generation_attempted": gen, "current_unusable": unusable}
        _write_status(src, attempt, previous, now_current, [], [], at)
        return PublishResult("failed", generation_id=gen, stage=stage, error=f"{type(exc).__name__}: {exc}"[:300], current_unusable=unusable)
    removed: list[str] = []
    deferred: list[str] = []
    try:
        run.get("cleanup", lambda: None)()
        removed, deferred = _cleanup(src, gen, remove, time.time())
    except Exception:  # noqa: BLE001 (cleanup runs after the pointer moved and can never fail the publication)
        deferred = ["cleanup"]
    _write_status(src, {"at": at, "outcome": "published", "generation_id": gen, "replaced_unusable": unusable}, previous, gen, removed, deferred, at)
    return PublishResult("published", generation_id=gen, removed=tuple(removed), deferred=tuple(deferred), current_unusable=unusable)
