"""Build the company page of every stored company and report what is missing or odd, so a whole universe can be checked at once.

    python scripts/fund_audit.py [--store PATH]

Prints one line per company (layout, basis, annual years, quarters held, latest period, rows with no data in the latest year) and a summary."""
from __future__ import annotations

import argparse
from collections import Counter

from athena.fundamentals import page
from athena.fundamentals.store import DEFAULT_PATH, open_read


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    args = parser.parse_args()
    problems: Counter[str] = Counter()
    with open_read(args.store) as store:
        symbols = [r[0] for r in store._con.execute("SELECT DISTINCT symbol FROM statement_lines ORDER BY symbol").fetchall()]
        for symbol in symbols:
            for basis in store.bases(symbol):
                doc = page.build_page(store, symbol, basis, None)
                if doc.get("error"):
                    problems["error"] += 1
                    print(f"{symbol:<12} {basis:<13} ERROR {doc['error']}")
                    continue
                years = doc["annual"]["ends"]
                quarters = doc["quarters"]["ends"]
                latest = doc["as_of"]
                income = {r["key"]: r["values"][-1] if r["values"] else None for r in doc["annual"]["income"]}
                notes = []
                if len(years) < 7:
                    notes.append(f"only {len(years)} years")
                    problems["short history"] += 1
                if len(quarters) < 8:
                    notes.append(f"only {len(quarters)} quarters")
                    problems["few quarters"] += 1
                if income.get("sales") is None:
                    notes.append("no sales in the latest year")
                    problems["no sales"] += 1
                if income.get("net_profit") is None:
                    notes.append("no net profit in the latest year")
                    problems["no profit"] += 1
                if not doc["annual"]["balance"]:
                    notes.append("no balance sheet")
                    problems["no balance sheet"] += 1
                print(f"{symbol:<12} {basis:<13} {doc['layout']:<7} years {len(years):>2} quarters {len(quarters):>2} latest {latest} {'; '.join(notes)}")
    print(f"\n{len(symbols)} companies; problems: {dict(problems) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
