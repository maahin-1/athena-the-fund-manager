from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from athena.contracts import AthenaError


@dataclass(frozen=True)
class PriceEvent:
    """A recorded corporate action that changes the price without changing value. `ratio` is shares received per share
    held: 5.0 for a 5:1 split, 2.0 for a 1:1 bonus, 0.1 for a 1:10 consolidation."""

    day: date  # the ex-date, an IST trading date
    ratio: float
    source: str = "yahoo"
    kind: str = "split"  # split | bonus | demerger: a label for the notes; the adjustment is the ratio

    @property
    def factor(self) -> float:
        """What every earlier price is multiplied by to read on today's basis."""
        return 1.0 / self.ratio


def yahoo_split_events(symbol: str, ticker=None) -> list[PriceEvent]:
    """Yahoo's split list (splits and bonus issues, with ratios) for an NSE symbol, oldest first. A stock that never split
    gives an empty list. Raises `AthenaError` when Yahoo cannot be reached."""
    try:
        if ticker is None:
            import yfinance

            ticker = yfinance.Ticker(f"{symbol}.NS")
        splits = ticker.splits
    except Exception as exc:
        raise AthenaError(f"corporate actions unavailable for {symbol}: {exc}") from exc
    events = [PriceEvent(stamp.date(), float(ratio)) for stamp, ratio in splits.items() if ratio and ratio > 0 and ratio != 1]
    return sorted(events, key=lambda event: event.day)
