from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from athena.contracts import Bar
from athena.metrics.series import Series
from athena.trading_calendar import ist_date

OUTPERFORM, INLINE, UNDERPERFORM = "outperform", "inline", "underperform"
SIGNAL_FOR = {OUTPERFORM: "bullish", INLINE: "neutral", UNDERPERFORM: "bearish"}
# horizon in trading days -> the dead zone around zero excess return inside which a call is neither right nor wrong. About a
# quarter of the days a stock beats or lags the index by this much anyway, so a smaller band would score noise.
HORIZONS = {63: 0.05, 126: 0.08, 252: 0.12}


@dataclass(frozen=True)
class Outcome:
    horizon_days: int
    start: date  # the close the call was made on
    end: date
    stock_return: float
    index_return: float
    excess: float  # stock return minus index return
    label: str  # outperform | inline | underperform

    @property
    def signal(self) -> str:
        return SIGNAL_FOR[self.label]


def _level(series: Series, days: list[date], day: date) -> float | None:
    """The index level on `day`, or on the last trading day before it."""
    i = bisect_right(days, day)
    return series[days[i - 1]] if i else None


def forward_outcome(bars: Sequence[Bar], index: Series, as_of: date, horizon_days: int, band: float | None = None) -> Outcome | None:
    """What the stock did over the `horizon_days` trading days after the close of `as_of`, against the index over the same
    dates. `bars` must be adjusted for splits and bonuses, or a split reads as a collapse. None when the history does not
    reach that far (the outcome is not known yet) or the index has no level for either date."""
    band = HORIZONS[horizon_days] if band is None else band
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    days = [ist_date(bar.timestamp) for bar in ordered]
    i = bisect_right(days, as_of) - 1
    if i < 0 or i + horizon_days >= len(ordered):
        return None
    start, end = days[i], days[i + horizon_days]
    index_days = sorted(index)
    first, last = _level(index, index_days, start), _level(index, index_days, end)
    if not first or not last:
        return None
    stock = ordered[i + horizon_days].close / ordered[i].close - 1.0
    market = last / first - 1.0
    excess = stock - market
    label = OUTPERFORM if excess > band else UNDERPERFORM if excess < -band else INLINE
    return Outcome(horizon_days, start, end, stock, market, excess, label)
