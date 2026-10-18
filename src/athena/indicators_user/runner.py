"""Run an indicator file the user placed in the indicators folder, in a process of its own.

    python -I runner.py list <folder>                    the files there, read but never run: name, pane, SHA-256, a syntax error if any
    python -I runner.py run  <folder> <file> <sha256>    the bars (a JSON list on stdin) through the file's `compute`; one JSON line on stdout

This file uses the standard library only and is started in Python's isolated mode (`-I`: no environment variables of Python's, no user site, no current
directory on the path), so it needs nothing installed and finds nothing it was not given. It is the part the terminal starts and times out.

The contract of an indicator file: it may set `NAME = "..."` and `PANE = "overlay"` or `"own"` (read without running the file), and it must define
`compute(bars)`, where `bars` is a list of dicts with the keys t, o, h, l, c, v (oldest first), returning a dict that maps a line's name to a list with
one number (or None) per bar. At most 8 lines. A number that is not finite becomes None.

`run` reads the file once, checks its SHA-256 against the one that was switched on, and runs those same bytes: a file changed after it was switched on is
refused, and cannot be swapped between the check and the run. Anything the file prints goes to stderr, so stdout carries only the reply. THIS IS NOT A
SANDBOX: the file is the user's own code and runs with the user's rights, which is why each file must be switched on, once, by the user, for the exact
version of it that is on disk."""
from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import sys
import traceback
from pathlib import Path
from typing import Any

FILE_NAME = re.compile(r"^[A-Za-z0-9_ -]{1,60}\.py$")
MAX_BYTES = 200_000
MAX_BARS = 20_000
MAX_LINES = 8
MAX_LINE_NAME = 30


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def literal(tree: ast.Module, name: str) -> Any:
    """The value of a module-level `NAME = <literal>` in the tree, or None. Nothing is evaluated but a literal."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            try:
                return ast.literal_eval(node.value)
            except ValueError:
                return None
    return None


def describe(path: Path) -> dict[str, Any]:
    row: dict[str, Any] = {"file": path.name, "name": path.stem, "pane": "own", "sha256": None, "size": 0, "error": None}
    try:
        data = path.read_bytes()
    except OSError as exc:
        row["error"] = f"cannot be read ({type(exc).__name__})"
        return row
    row["size"], row["sha256"] = len(data), sha256(data)
    if len(data) > MAX_BYTES:
        row["error"] = f"too large ({len(data):,} bytes; at most {MAX_BYTES:,})"
        return row
    try:
        tree = ast.parse(data.decode("utf-8"), filename=path.name)
    except SyntaxError as exc:
        row["error"] = f"syntax error on line {exc.lineno}: {exc.msg}"
        return row
    except UnicodeDecodeError:
        row["error"] = "not UTF-8 text"
        return row
    name, pane = literal(tree, "NAME"), literal(tree, "PANE")
    if isinstance(name, str) and 1 <= len(name.strip()) <= 40:
        row["name"] = name.strip()
    if pane in ("overlay", "own"):
        row["pane"] = pane
    return row


def listing(folder: Path) -> list[dict[str, Any]]:
    if not folder.is_dir():
        return []
    return [describe(p) for p in sorted(folder.glob("*.py")) if FILE_NAME.match(p.name) and p.is_file()]


class ContractError(Exception):
    """The indicator's result breaks the contract (distinct from an exception the user's own code raises, which is reported with its class)."""


def fail(message: str) -> dict[str, str]:
    return {"error": message}


def clean(values: Any, size: int, name: str) -> list[float | None]:
    if not isinstance(values, (list, tuple)):
        raise ContractError(f"the line {name!r} must be a list with one value per bar")
    if len(values) != size:
        raise ContractError(f"the line {name!r} has {len(values)} values for {size} bars")
    out: list[float | None] = []
    for v in values:
        if v is None:
            out.append(None)
        elif isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ContractError(f"the line {name!r} must hold numbers (or None), not {type(v).__name__}")
        else:
            out.append(float(v) if math.isfinite(v) else None)
    return out


def run(folder: Path, file: str, expected: str, raw: str) -> dict[str, Any]:
    if not FILE_NAME.match(file):
        return fail("that is not a usable file name")
    try:
        bars = json.loads(raw)
    except ValueError:
        return fail("the bars are not valid JSON")
    if not isinstance(bars, list) or not all(isinstance(b, dict) for b in bars):
        return fail("the bars must be a list of objects")
    if len(bars) > MAX_BARS:
        return fail(f"at most {MAX_BARS:,} bars can be sent to an indicator")
    path = folder / file
    try:
        data = path.read_bytes()
    except OSError:
        return fail("the file cannot be read")
    if len(data) > MAX_BYTES:
        return fail("the file is too large")
    if sha256(data) != expected:
        return fail("the file changed since it was switched on; read it again and switch it on again")
    try:
        code = compile(data.decode("utf-8"), file, "exec")
    except SyntaxError as exc:
        return fail(f"syntax error on line {exc.lineno}: {exc.msg}")
    except UnicodeDecodeError:
        return fail("the file is not UTF-8 text")
    namespace: dict[str, Any] = {"__name__": "user_indicator", "__file__": file}
    real_stdout, sys.stdout = sys.stdout, sys.stderr  # whatever the file prints must not reach the reply
    try:
        exec(code, namespace)  # noqa: S102 - the user's own file, switched on by the user for this exact version
        compute = namespace.get("compute")
        if not callable(compute):
            return fail("the file must define compute(bars)")
        result = compute(bars)
        if not isinstance(result, dict):
            return fail("compute must return a dict that maps a line's name to its values")
        if not result:
            return fail("compute must return at least one line")
        if len(result) > MAX_LINES:
            return fail(f"compute may return at most {MAX_LINES} lines")
        lines: dict[str, list[float | None]] = {}
        for key, values in result.items():
            if not isinstance(key, str) or not 1 <= len(key) <= MAX_LINE_NAME:
                return fail(f"a line's name must be text of 1 to {MAX_LINE_NAME} characters")
            lines[key] = clean(values, len(bars), key)
        return {"lines": lines}
    except ContractError as exc:
        return fail(str(exc))
    except Exception as exc:  # the user's code failed: say how and on which line of their file
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == file]
        where = f" (line {frames[-1].lineno})" if frames else ""
        return fail(f"{type(exc).__name__}: {str(exc)[:200]}{where}")
    finally:
        sys.stdout = real_stdout


def main(argv: list[str]) -> int:
    out = sys.stdout
    try:
        if len(argv) == 3 and argv[1] == "list":
            reply: Any = listing(Path(argv[2]))
        elif len(argv) == 5 and argv[1] == "run":
            reply = run(Path(argv[2]), argv[3], argv[4], sys.stdin.read())
        else:
            reply = fail("usage: runner.py list <folder> | run <folder> <file> <sha256>")
    except Exception as exc:  # the runner itself failed: say so, never a traceback with paths
        reply = fail(f"the runner failed ({type(exc).__name__})")
    out.write(json.dumps(reply))
    out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
