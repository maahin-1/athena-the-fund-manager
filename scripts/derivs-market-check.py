"""Measure the derivative stream while the market is open (09:15 to 15:30 IST, weekdays; MCX trades until late evening).

    python scripts/derivs-market-check.py [--port 8700] [--seconds 120] [--poll 0.25] [--underlying NIFTY ...]

Asks the running terminal server (scripts/terminal.ps1, real feed, not -Fake) for the at-the-money strikes and the future of each underlying's nearest expiry
that has both, through the same /api/derivs routes the Options page uses, and for a few minutes polls the quotes route. It prints, per underlying:

  - whether the exchange times advanced (so the page badge would say "live prices"), and the gap between the exchange clock and this computer's clock;
  - how many distinct updates each contract showed and the median and 95th-percentile gap between them (the tick rate as the page can see it: the server keeps
    the latest quote per contract, so a gap below the poll interval cannot be seen; a smaller --poll shows more);
  - the table to compare with the exchange's own option chain: strike, call and put open interest in lots, the change in open interest the page would show,
    and the stream's change in percent, with last price, bid and ask.

It reads no setting and prints no secret. It only reads; the server must already be running."""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.parse
import urllib.request


def get(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/derivs/{path}", timeout=30) as r:
        return json.load(r)


def contracts_per(seg: str, lot: int) -> int:
    """Open interest and volume are a quantity of the underlying for NFO and BFO and lots already for MCX (checked 2026-10-19)."""
    return 1 if seg == "MCX" or lot <= 1 else lot


def pick(port: int, name: str, around: int = 3):
    listed = get(port, f"expiries?underlying={name}")["expiries"]
    expiries = [e for e in listed if e["futures"] and e["options"]] or [e for e in listed if e["options"]]
    if not expiries:
        raise SystemExit(f"{name}: no expiry with options")
    chain = get(port, f"chain?underlying={name}&expiry={expiries[0]['date']}")
    if not chain["future"]:  # an option on a later future (MCX): the nearest future stands in for the money
        futures = get(port, f"futures?underlying={name}")["futures"]
        if futures:
            chain["future"] = {"token": futures[0]["token"], "lot": futures[0]["lot"]}
    return expiries[0]["date"], chain


def ask(port: int, refs: list[str]) -> dict:
    q = urllib.parse.quote(",".join(refs), safe=":,")
    return get(port, f"quotes?tokens={q}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8700)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--poll", type=float, default=0.25)
    parser.add_argument("underlying", nargs="*")
    args = parser.parse_args()
    names = [n.upper() for n in args.underlying] or ["NIFTY", "BANKNIFTY", "GOLD"]
    first = ask(args.port, [])
    if not first.get("live"):
        print("The server has no broker stream (fake feed, or the broker is not connected): nothing to measure.")
        return 1
    for name in names:
        try:
            date, chain = pick(args.port, name)
        except SystemExit as e:
            print(e)
            continue
        seg = chain["segment"]
        fut = chain["future"]
        rows = [r for r in chain["strikes"] if r["call"] and r["put"]]
        refs: dict[str, str] = {}  # ref -> label
        if fut:
            refs[f"{seg}:{fut['token']}"] = "future"
        # find the money from the future's price (wait for it), else the middle of the strikes
        spot = None
        if fut:
            for _ in range(40):
                got = ask(args.port, [f"{seg}:{fut['token']}"])["quotes"]
                if got and got[0]["last"]:
                    spot = got[0]["last"]
                    break
                time.sleep(0.5)
        mid = min(range(len(rows)), key=lambda i: abs(rows[i]["strike"] - spot)) if spot else len(rows) // 2
        shown = rows[max(0, mid - 3): mid + 4]
        for r in shown:
            refs[f"{seg}:{r['call']['token']}"] = f"{r['strike']:g} CE"
            refs[f"{seg}:{r['put']['token']}"] = f"{r['strike']:g} PE"
        lot = {f"{seg}:{c['token']}": c["lot"] for r in shown for c in (r["call"], r["put"])}
        if fut:
            lot[f"{seg}:{fut['token']}"] = fut["lot"]
        print(f"\n=== {name} {date} ({seg}), {len(refs)} contracts, polling every {args.poll}s for {args.seconds}s")
        seen: dict[str, list[float]] = {k: [] for k in refs}
        last_state: dict[str, tuple] = {}
        latest: dict[str, dict] = {}
        newest_t = None
        first_t = None
        advanced_at = None
        own_t: dict[str, int] = {}
        end = time.time() + args.seconds
        while time.time() < end:
            now = time.time()
            for q in ask(args.port, list(refs))["quotes"]:
                ref = f"{q['seg']}:{q['token']}"
                latest[ref] = q
                state = (q["last"], q["bid"], q["ask"], q["volume"], q["oi"], q["t"])
                if last_state.get(ref) != state:
                    last_state[ref] = state
                    seen[ref].append(now)
                if q["t"] is not None:
                    if first_t is None:
                        first_t = q["t"]
                    if ref in own_t and q["t"] > own_t[ref]:
                        advanced_at = now  # this contract's own exchange time moved on
                    own_t[ref] = q["t"]
                    if newest_t is None or q["t"] > newest_t:
                        newest_t = q["t"]
            time.sleep(args.poll)
        if newest_t is None:
            print("  no quotes arrived")
            continue
        drift = time.time() - newest_t / 1000
        print(f"  exchange time moved: {'YES' if advanced_at else 'no'}  (first {time.strftime('%H:%M:%S', time.localtime(first_t / 1000))}, newest {time.strftime('%H:%M:%S', time.localtime(newest_t / 1000))} local; computer clock minus exchange clock {drift:+.0f}s)")
        print(f"  {'contract':<14}{'updates':>8}{'median gap s':>14}{'p95 gap s':>11}")
        for ref, label in refs.items():
            times = seen[ref]
            gaps = [b - a for a, b in zip(times, times[1:])]
            med = f"{statistics.median(gaps):.2f}" if gaps else "-"
            p95 = f"{sorted(gaps)[min(len(gaps) - 1, int(round(0.95 * (len(gaps) - 1))))]:.2f}" if gaps else "-"
            print(f"  {label:<14}{len(times):>8}{med:>14}{p95:>11}")
        print("\n  compare with the exchange's option chain (open interest in lots):")
        print(f"  {'strike':>8} | {'CE oi':>9}{'CE chg':>9}{'CE chg%':>9}{'CE ltp':>9}{'bid':>9}{'ask':>9} | {'PE oi':>9}{'PE chg':>9}{'PE chg%':>9}{'PE ltp':>9}{'bid':>9}{'ask':>9}")
        for r in shown:
            cells = []
            for side in ("call", "put"):
                ref = f"{seg}:{r[side]['token']}"
                q = latest.get(ref)
                if not q:
                    cells.append(f"{'-':>9}" * 6)
                    continue
                per = contracts_per(seg, lot[ref])
                oi = round(q["oi"] / per)
                pct = q["oip"]
                chg = round(oi - oi / (1 + pct / 100)) if pct > -100 and oi > 0 else 0
                f = lambda v: f"{v:>9.2f}" if v is not None else f"{'-':>9}"
                cells.append(f"{oi:>9}{chg:>9}{pct:>9.2f}{f(q['last'])}{f(q['bid'])}{f(q['ask'])}")
            print(f"  {r['strike']:>8g} | {cells[0]} | {cells[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
