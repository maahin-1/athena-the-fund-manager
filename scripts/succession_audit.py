"""Check the symbol-succession join against every symbol the warehouse holds.

    python scripts/succession_audit.py [--db .athena/warehouse.duckdb] [--show 15]

For each rename whose prices the warehouse has judged joinable, read the joined, adjusted series and look for what would mean the join is wrong:
a step at the join of more than 25% once adjusted, a break no record explains that lies in the joined part, or a series that is not in time order.
Also counts the renames that were left unjoined, by reason. Reads the warehouse only; writes nothing."""
from __future__ import annotations

import argparse
import collections
from datetime import date
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from athena.trading_calendar import ist_date  # noqa: E402
from athena.warehouse.history import adjusted_history  # noqa: E402
from athena.warehouse.warehouse import DEFAULT_PATH, Warehouse  # noqa: E402

STEP = 0.25
SEAM_DAYS = 5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    parser.add_argument("--show", type=int, default=15)
    args = parser.parse_args(argv)
    problems: list[str] = []
    with Warehouse(args.db, read_only=True, wait_seconds=60) as w:
        rows = w._con.execute("SELECT old, new, change_day, joinable, retired, reason FROM symbol_renames").fetchall()
        history = adjusted_history(w)
        joinable_symbols = sorted({new for old, new, day, joinable, retired, reason in rows if joinable})
        reasons = collections.Counter(reason.split(" ")[0] or "joined" for *_, reason in rows)
        gained: list[int] = []
        inherited: list[str] = []
        for symbol in joinable_symbols:
            own = w.bars(symbol)
            bars = history(symbol)
            added = len(bars) - len(own)
            if added <= 0:
                problems.append(f"{symbol}: judged joinable but no bars were added")
                continue
            gained.append(added)
            days = [ist_date(b.timestamp) for b in bars]
            if days != sorted(set(days)):
                problems.append(f"{symbol}: joined series is not in strict time order")
            step = bars[added].close / bars[added - 1].close - 1
            if abs(step) > STEP:
                problems.append(f"{symbol}: {step:+.0%} step at the join after adjustment ({days[added - 1]} to {days[added]})")
            seam = days[added - 1]
            older = [f for f in history.price_flags(symbol, len(bars)) if f[:10] <= seam.isoformat()]
            if any(abs((date.fromisoformat(f[:10]) - seam).days) <= SEAM_DAYS for f in older):
                problems.append(f"{symbol}: an unexplained break at the join: {', '.join(older[-3:])}")
            elif older:
                inherited.append(symbol)  # a break the older symbol's own bars already had (an ETF unit change, say); the join did not make it
        print(f"renames recorded: {len(rows)}; prices judged joinable: {sum(1 for r in rows if r[3])}; symbols that gained history: {len(joinable_symbols)}")
        print("by reason: " + ", ".join(f"{k} {v}" for k, v in reasons.most_common()))
        if gained:
            print(f"bars added: {sum(gained):,} in all (median {sorted(gained)[len(gained) // 2]}, most {max(gained)})")
        print(f"unexplained breaks already in the older symbol's own bars (not made by the join): {len(inherited)} symbols")
        print(f"problems made by the join: {len(problems)}")
        for line in problems[: args.show]:
            print("  " + line)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
