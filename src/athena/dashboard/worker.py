"""A long-running worker for the terminal: builds the live dashboard service once (about 15 seconds of loading market series), then answers
one JSON request per line on stdin with one JSON line on stdout.

    python -m athena.dashboard.worker

    in:   {"id": 1, "op": "view", "query": "TCS", "relatives": false}
          {"id": 2, "op": "backtest", "symbol": "TCS"}      (the built-in rules over up to eight years, each against buy and hold)
    out:  {"ready": true}                                         (once, when the service is built)
          {"id": 1, "ok": true, "result": {...the DashboardView...}}
          {"id": 1, "ok": false, "error": "..."}   (only the project's own messages; any other failure says "the analysis failed (ClassName)")

`relatives` lists related instruments for a typed ticker (the dashboard does this for a typed box and not for a chosen row). Anything the
libraries print goes to stderr, so stdout carries only protocol lines. The terminal starts this on the first analysis or backtest and keeps it."""
from __future__ import annotations

import dataclasses
import json
import sys
from collections.abc import Iterable
from typing import Any, TextIO

from athena.contracts import AthenaError

MAX_QUERY = 60


def backtest_json(view: Any) -> dict[str, Any]:
    """A `BacktestView` as plain data. The plotting figure stays behind; the page draws the `curve` itself."""
    return {
        "identifier": view.identifier,
        "assumptions": view.assumptions,
        "notes": list(view.notes),
        "panels": [
            {
                "rule": p.rule, "description": p.description, "rows": [list(r) for r in p.rows], "facts": list(p.facts),
                "blinded_identical": p.blinded_identical, "curve": dataclasses.asdict(p.curve) if p.curve else None,
            }
            for p in view.panels
        ],
    }


def handle(service: Any, request: dict[str, Any]) -> dict[str, Any]:
    """The reply to one request. Never raises: a failure is a reply with `ok` false and the exception's class and message."""
    reply: dict[str, Any] = {"id": request.get("id")}
    try:
        op = request.get("op")
        if op == "view":
            query = str(request.get("query", "")).strip()
            if not query or len(query) > MAX_QUERY:
                raise ValueError(f"the query must be 1 to {MAX_QUERY} characters")
            view = service.view(query, None, bool(request.get("relatives")))
            reply.update(ok=True, result=dataclasses.asdict(view))
        elif op == "backtest":
            symbol = str(request.get("symbol", "")).strip()
            if not symbol or len(symbol) > MAX_QUERY:
                raise ValueError(f"the symbol must be 1 to {MAX_QUERY} characters")
            reply.update(ok=True, result=backtest_json(service.backtest(symbol)))
        else:
            raise ValueError(f"unknown operation {op!r}")
    except (AthenaError, ValueError) as exc:  # the project's own messages are written to be shown
        reply.update(ok=False, error=str(exc))
    except Exception as exc:  # the worker must outlive any one bad request; a library's message is not forwarded (it may carry a URL or a key)
        reply.update(ok=False, error=f"the analysis failed ({type(exc).__name__})")
    return reply


def serve(service: Any, lines: Iterable[str], out: TextIO) -> None:
    """Answer each line of `lines` in order, writing replies to `out`. A line that is not a JSON object is answered with an error."""
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            request = json.loads(text)
            if not isinstance(request, dict):
                raise ValueError("a request is a JSON object")
            reply = handle(service, request)
        except ValueError as exc:
            reply = {"id": None, "ok": False, "error": f"ValueError: {exc}"}
        out.write(json.dumps(reply, default=str) + "\n")
        out.flush()


def main() -> int:
    protocol = sys.stdout
    sys.stdout = sys.stderr  # nothing a library prints may reach the protocol
    from athena.dashboard.service import live_service

    service = live_service()
    protocol.write(json.dumps({"ready": True}) + "\n")
    protocol.flush()
    serve(service, sys.stdin, protocol)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
