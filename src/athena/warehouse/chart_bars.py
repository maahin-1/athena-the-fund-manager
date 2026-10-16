"""One stock's daily bars with recorded splits and bonuses applied, as JSON for the terminal's chart.

    python -m athena.warehouse.chart_bars SBIN --limit 400

Prints {"bars": [{"t", "o", "h", "l", "c", "v"}, ...], "flags": [...], "notes": [...]}, oldest first. A daily bar's `t` is midnight UTC of
its trading date, as the terminal's page expects. Reads the warehouse read-only (it waits when a build holds the file) and writes nothing."""
from __future__ import annotations

import argparse
import calendar
import json
import sys
from collections.abc import Sequence
from typing import Any

from athena.trading_calendar import ist_date
from athena.warehouse.history import adjusted_history
from athena.warehouse.warehouse import DEFAULT_PATH, Warehouse

WAIT_SECONDS = 20.0


def chart_bars(warehouse: Warehouse, symbol: str, limit: int) -> dict[str, Any]:
    """The newest `limit` adjusted bars of `symbol`, with the breaks no record explains and what was adjusted."""
    history = adjusted_history(warehouse)
    bars = history(symbol)
    out = [
        {
            "t": calendar.timegm(ist_date(bar.timestamp).timetuple()),
            "o": round(bar.open, 4),
            "h": round(bar.high, 4),
            "l": round(bar.low, 4),
            "c": round(bar.close, 4),
            "v": bar.volume,
        }
        for bar in bars[-limit:]
    ]
    return {"bars": out, "flags": list(history.price_flags(symbol)), "notes": list(history.notes(symbol))}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--warehouse", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    with Warehouse(args.warehouse, read_only=True, wait_seconds=WAIT_SECONDS) as warehouse:
        json.dump(chart_bars(warehouse, args.symbol.upper(), max(1, args.limit)), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
