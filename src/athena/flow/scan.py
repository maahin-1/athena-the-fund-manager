"""Run the flow detectors over the derivatives history store.

    python -m athena.flow.scan [--symbol ABC] [--limit 40]      # events on the latest loaded day, strongest first

`events_for` reads one stock's futures and strike rows from `.athena/derivs.duckdb` and returns the events on its latest day; `scan` does it for every stock with futures. The same functions feed
the flow analyst's packet (`athena.flow.packet`)."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import timedelta

from athena.derivs.store import DEFAULT_PATH, DerivsStore
from athena.flow import detectors as d


def events_for(
    store: DerivsStore, symbol: str, instrument: str = "FUTSTK", futures: list | None = None, options: list | None = None,
) -> tuple[list[d.Event], int, float | None]:
    """`(events on the latest day, days of futures held, latest put-call ratio)` for one symbol. A caller that already has the symbol's futures series, and its strike rows since 120 days
    before its last day, passes them (the board does, from one scan of each table)."""
    futures = store.futures(symbol, instrument) if futures is None else futures
    if not futures:
        return [], 0, None
    rows = [d.FutDay(day, close, oi, volume) for day, close, oi, volume in futures]
    events = d.futures_events(symbol, rows)
    last = rows[-1].day
    since = last - timedelta(days=120)
    got = store.option_rows(symbol, since) if options is None else [r for r in options if r[0] >= since]
    opts = [d.OptRow(day, expiry, strike, kind, oi) for day, expiry, strike, kind, oi in got]
    days = sorted({o.day for o in opts})
    ratio = d.put_call_ratio(opts, days[-1]) if days and days[-1] == last else None
    if days and days[-1] == last:
        events += d.options_events(symbol, opts, days, rows[-1].close)
    return events, len(rows), ratio


def scan(store: DerivsStore, instrument: str = "FUTSTK") -> list[d.Event]:
    """The events on the store's latest day, strongest first. A symbol whose own latest day is older (it left the futures market, or its file stopped) has nothing to say about today."""
    out: list[d.Event] = []
    latest = store.latest_day()
    for symbol in store.symbols(instrument):
        out += [e for e in events_for(store, symbol, instrument)[0] if e.day == latest]
    return sorted(out, key=lambda e: -max((abs(v) for k, v in e.facts.items() if k.endswith("_z") or k == "z"), default=0.0))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    parser.add_argument("--symbol")
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args(argv)
    with DerivsStore(args.store, read_only=True) as store:
        found = events_for(store, args.symbol.upper())[0] if args.symbol else scan(store)
    for event in found[: args.limit]:
        print(f"{event.symbol}: {d.describe(event)}")
    print(f"{len(found)} events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
