"""A small derivatives history for the terminal's end-to-end tests: 70 days of futures for four underlyings, three of them with a move on the last day.

    python scripts/seed_derivs_store.py PATH
"""
import sys
from datetime import date, timedelta
from pathlib import Path

from athena.derivs.store import DerivsStore

DAYS = 70
EXPIRY = date(2030, 1, 30)
# symbol, instrument, price multiple and open-interest multiple on the last day
MOVES = {"RISER": ("FUTSTK", 1.03, 1.06), "FALLER": ("FUTSTK", 0.97, 1.06), "QUIET": ("FUTSTK", 1.0, 1.0), "NIFTY": ("FUTIDX", 1.002, 1.01)}


def main(path: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    with DerivsStore(target) as store:
        for i in range(DAYS):
            day = date(2026, 1, 1) + timedelta(days=i)
            wobble = ((i * 7) % 5 - 2) / 1000
            rows = []
            for symbol, (instrument, price_move, oi_move) in MOVES.items():
                close, oi = 100.0 * (1 + wobble), 1000.0 * (1 + wobble * 2)
                if i == DAYS - 1:
                    close, oi = close * price_move, oi * oi_move
                rows.append((symbol, instrument, EXPIRY, close, close, 10.0 + i, oi, 0.0))
            store.put_day(day, {"futures": rows, "totals": [], "strikes": []})


if __name__ == "__main__":
    main(sys.argv[1])
