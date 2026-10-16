"""Compare stored annual figures with Yahoo's for the same companies and years: evidence, not a test (it needs the network).

    python scripts/fund_crosscheck.py TCS SBIN RELIANCE
Prints, per company and year, our figure over Yahoo's for revenue and net profit; a ratio outside 0.99 to 1.01 is flagged. Banks are compared on net
profit only (Yahoo's revenue for a bank is a different construct from interest earned)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import yfinance  # noqa: E402

from athena.fundamentals import ratios  # noqa: E402
from athena.fundamentals.store import DEFAULT_PATH, FundamentalsStore  # noqa: E402


def main(symbols: list[str]) -> int:
    flagged = 0
    with FundamentalsStore(DEFAULT_PATH, read_only=True) as store:
        for symbol in symbols:
            company = store.company(symbol) or {}
            ours = ratios.annual(store.lines(symbol, "consolidated", "income") or store.lines(symbol, "standalone", "income"))
            frame = yfinance.Ticker(f"{symbol}.NS").financials
            print(f"{symbol} (layout {company.get('layout', '?')})")
            for column in frame.columns:
                end = column.date()
                mine = ours.get(end)
                if not mine:
                    continue
                for key, row in (("sales", "Total Revenue"), ("net_profit", "Net Income")):
                    if key == "sales" and company.get("layout") == "bank":
                        continue
                    theirs = frame.loc[row, column] if row in frame.index else None
                    if mine.get(key) is None or theirs is None or theirs != theirs:
                        continue
                    ratio = mine[key] / theirs
                    flag = "" if 0.99 <= ratio <= 1.01 else "   <-- check"
                    flagged += bool(flag)
                    print(f"  {end}  {key:<11} ours {ratios.crore(mine[key]):>12,.0f} cr  yahoo {ratios.crore(theirs):>12,.0f} cr  ratio {ratio:.4f}{flag}")
    print(f"{flagged} figure(s) outside 1% of Yahoo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
