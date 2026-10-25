"""A long-running helper for the terminal server: the Fundamentals page, the valuation and the split-adjusted daily bars, from one warm process.

Starting Python and importing the modules took most of each request (about 1.2 s of 1.3 s for a page); kept running, a page takes about 0.1 s, a
valuation 0.2 s and a chart's daily bars 0.3 s. Each request runs the same command-line entry the server used to start (`athena.fundamentals.page`,
`athena.valuation.page`, `athena.warehouse.chart_bars`) with the same arguments, so the answer is byte for byte what the command prints.

    python -m athena.terminal_helper

    in:   {"id": 1, "op": "page", "args": ["TCS", "--basis", "consolidated", "--store", ".athena/fundamentals.duckdb", "--price", "3200"]}
    out:  {"ready": true}                                        (once, when the modules are loaded)
          {"id": 1, "ok": true, "out": "<what the command printed>"}
          {"id": 1, "ok": false, "error": "ClassName"}           (the server then runs the command itself, as before)

Anything else the libraries print goes to stderr, so stdout carries only protocol lines."""
from __future__ import annotations

import contextlib
import io
import json
import sys
from collections.abc import Callable, Sequence
from typing import Any, TextIO

MAX_ARGS = 16
MAX_ARG = 300


def operations() -> dict[str, Callable[[Sequence[str]], int]]:
    from athena.fundamentals import page
    from athena.valuation import page as valuation
    from athena.warehouse import chart_bars

    return {"page": page.main, "valuation": valuation.main, "chart_bars": chart_bars.main}


def handle(ops: dict[str, Callable[[Sequence[str]], int]], request: dict[str, Any]) -> dict[str, Any]:
    """The reply to one request. Never raises."""
    reply: dict[str, Any] = {"id": request.get("id")}
    op, args = request.get("op"), request.get("args")
    if op not in ops or not isinstance(args, list) or len(args) > MAX_ARGS or not all(isinstance(a, str) and len(a) <= MAX_ARG for a in args):
        return {**reply, "ok": False, "error": "bad request"}
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = ops[op](list(args))
    except SystemExit as exc:  # argparse refusing the arguments
        return {**reply, "ok": False, "error": f"exit {exc.code}"}
    except Exception as exc:  # the server falls back to running the command, which reports the failure its own way
        return {**reply, "ok": False, "error": type(exc).__name__}
    if code not in (0, None):
        return {**reply, "ok": False, "error": f"exit {code}"}
    return {**reply, "ok": True, "out": out.getvalue()}


def warm(ops: dict[str, Callable[[Sequence[str]], int]], symbol: str = "TCS") -> None:
    """One page and one valuation thrown away, so the setup the first call does (about 0.7 s) is not paid by the first page a person opens. A failure is ignored."""
    for op in ("page", "valuation"):
        if op in ops:
            handle(ops, {"op": op, "args": [symbol]})


def serve(lines: TextIO, write: TextIO, ops: dict[str, Callable[[Sequence[str]], int]]) -> None:
    write.write(json.dumps({"ready": True}) + "\n")
    write.flush()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            request = {}
        reply = handle(ops, request if isinstance(request, dict) else {})
        write.write(json.dumps(reply) + "\n")
        write.flush()


def main() -> int:
    protocol = sys.stdout
    sys.stdout = sys.stderr  # a stray print from a library must not break the protocol
    ops = operations()
    warm(ops)
    serve(sys.stdin, protocol, ops)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
