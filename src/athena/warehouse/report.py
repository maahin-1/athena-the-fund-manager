"""What is wrong with the market's prices, from the warehouse alone:

    python -m athena.warehouse.report          # stocks with a large overnight price break nothing explains
"""
from __future__ import annotations

import argparse
from pathlib import Path

from athena.warehouse.history import market_price_flags
from athena.warehouse.warehouse import DEFAULT_PATH, Warehouse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.warehouse.report", description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    warehouse = Warehouse(Path(args.db))
    try:
        stocks = [u["symbol"] for u in warehouse.universe("stock")]
        flagged = market_price_flags(warehouse, stocks)
        print(f"{len(flagged)} of {len(stocks):,} stocks have a price break in the last year that no recorded action explains:")
        for symbol, flags in sorted(flagged.items()):
            print(f"  {symbol}: {', '.join(flags)}")
    finally:
        warehouse.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
