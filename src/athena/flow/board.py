"""The futures and options board: every stock and index with futures, on the latest day of the derivatives history, with what the detectors found.

    python -m athena.flow.board [--store .athena/derivs.duckdb]            one JSON document on stdout

For each underlying with futures on the store's latest day: the future's close, its change on the day before, the open interest over the held expiries and its change, the volume of the
nearest expiry, the put-call open-interest ratio of the nearest expiry, and the events the detectors in `athena.flow.detectors` found that day, each as a line with its figures. The
changes come from the roll-free series (`DerivsStore.futures`), so a roll from one expiry to the next is not a move. End-of-day history only: nothing here is live, and MCX has no
free end-of-day file, so commodity futures are on the Options page, which reads them live.

The document is `{"day": "2026-10-23", "rows": [...], "events": {kind: count}, "universe": n}`; a store that is missing or busy gives `{"error": ...}` or `{"busy": true}`."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from athena.derivs.store import DEFAULT_PATH, DerivsStore
from athena.flow import detectors as d
from athena.flow.scan import events_for

INSTRUMENTS = (("FUTSTK", "stock"), ("FUTIDX", "index"))


def _row(symbol: str, kind: str, series: list[tuple[Any, float, float, float]], events: list[d.Event], ratio: float | None) -> dict[str, Any]:
    (_, close_before, oi_before, _), (day, close, oi, volume) = series[-2], series[-1]
    return {
        "symbol": symbol, "kind": kind, "close": close, "price_change": close / close_before - 1 if close_before > 0 else None, "oi": oi,
        "oi_change": oi / oi_before - 1 if oi_before > 0 else None, "volume": volume, "put_call": ratio,
        "events": [{"kind": e.kind, "line": d.describe(e)} for e in events if e.day == day],
    }


def board(store: DerivsStore) -> dict[str, Any]:
    """The board for the store's latest futures day."""
    latest = store.latest_day()
    rows: list[dict[str, Any]] = []
    if latest is None:
        return {"day": None, "rows": rows, "events": {}, "universe": 0}
    for instrument, kind in INSTRUMENTS:
        for symbol in store.symbols(instrument):
            series = store.futures(symbol, instrument)
            if len(series) < 2 or series[-1][0] != latest:  # it left the futures market, or its file stopped: nothing to say about the latest day
                continue
            events, _, ratio = events_for(store, symbol, instrument)
            rows.append(_row(symbol, kind, series, events, ratio))
    counts = Counter(e["kind"] for r in rows for e in r["events"])
    rows.sort(key=lambda r: (-(len(r["events"])), -(r["oi"] or 0.0)))
    return {"day": latest.isoformat(), "rows": rows, "events": dict(counts), "universe": len(rows)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    if not Path(args.store).exists():
        print(json.dumps({"error": "no derivatives history is stored"}))
        return 0
    try:
        with DerivsStore(args.store, read_only=True) as store:
            doc = board(store)
    except Exception as exc:  # a file another process is writing
        print(json.dumps({"busy": True, "error": type(exc).__name__}))
        return 0
    sys.stdout.write(json.dumps(doc, ensure_ascii=True, allow_nan=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
