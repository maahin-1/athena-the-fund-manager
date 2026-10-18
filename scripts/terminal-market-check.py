"""Measure the terminal's live feed during market hours (09:15 to 15:30 IST, weekdays).

    python scripts/terminal-market-check.py [--port 8700] [--seconds 120] [SYMBOL ...]

Connects to the running terminal server the way the page does, watches the symbols (default: ten large stocks) and prints, per symbol, how
many updates arrived and the median and 95th-percentile gap between them, plus the delay from the server building a frame to this script
receiving it. It reads no setting and prints no secret; the server must already be running (scripts/terminal.ps1).

Still to check in a market session (derivatives, Task B2 of docs/superpowers/plans/2026-10-19-derivatives.md; nothing here does it yet):
  - subscribe a few NFO option and future tokens (exchange type 2) and an MCX one (5) in snap-quote mode and capture raw packets;
  - compare the decoder's open interest (int64 at offset 131) and change in open interest percent (float64 at 139) with the exchange's own option chain;
  - compare the Options page's implied volatility and Greeks for one at-the-money contract with the broker's or the exchange's figures;
  - the live derivative route and a live OptionFeed do not exist until that is done, and the Options page shows synthetic prices."""
from __future__ import annotations

import argparse
import json
import statistics
import time

import websocket  # websocket-client, already installed for the Angel SDK

DEFAULT = ["SBIN", "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "LUPIN", "RVNL", "KOTAKBANK", "ITC"]


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8700)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("symbols", nargs="*")
    args = parser.parse_args()
    symbols = [s.upper() for s in args.symbols] or DEFAULT
    ws = websocket.create_connection(f"ws://127.0.0.1:{args.port}/ws", origin=f"http://127.0.0.1:{args.port}", timeout=5)
    ws.send(json.dumps({"type": "watch", "symbols": symbols}))
    arrivals: dict[str, list[float]] = {s: [] for s in symbols}
    exchange_changes: dict[str, int] = {s: 0 for s in symbols}
    last_exchange: dict[str, int | None] = {s: None for s in symbols}
    sources: dict[str, set[str]] = {s: set() for s in symbols}
    delays: list[float] = []
    end = time.time() + args.seconds
    while time.time() < end:
        try:
            message = json.loads(ws.recv())
        except websocket.WebSocketTimeoutException:
            continue
        if message.get("type") != "q":
            continue
        now = time.time()
        delays.append(now * 1000 - message["sm"])
        for q in message["q"]:
            symbol = q["s"]
            arrivals.setdefault(symbol, []).append(now)
            sources.setdefault(symbol, set()).add(q["src"])
            if q["t"] != last_exchange.get(symbol):
                exchange_changes[symbol] = exchange_changes.get(symbol, 0) + 1
                last_exchange[symbol] = q["t"]
    ws.close()
    print(f"{'symbol':<11}{'updates':>8}{'new exch time':>15}{'median gap s':>14}{'p95 gap s':>11}  source")
    for symbol in symbols:
        times = arrivals.get(symbol, [])
        gaps = [b - a for a, b in zip(times, times[1:])]
        median = f"{statistics.median(gaps):.2f}" if gaps else "-"
        p95 = f"{percentile(gaps, 0.95):.2f}" if gaps else "-"
        print(f"{symbol:<11}{len(times):>8}{exchange_changes.get(symbol, 0):>15}{median:>14}{p95:>11}  {','.join(sorted(sources.get(symbol, set()))) or '-'}")
    if delays:
        print(f"\nserver to script delay: median {statistics.median(delays):.1f} ms, 95th percentile {percentile(delays, 0.95):.1f} ms ({len(delays)} frames)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
