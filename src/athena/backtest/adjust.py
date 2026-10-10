from __future__ import annotations

from collections.abc import Sequence

from athena.contracts import Bar, InsufficientData

# An overnight move this large is more than the market normally does in a session (NSE circuit bands make a genuine one-
# session move of 40% down or 70% up very rare), so it is looked at: against a recorded corporate action it is adjusted,
# without one it is flagged and left as it is (athena.price_integrity). Smaller ratios, such as a 3:2 bonus (x0.67), are
# found only through a record.
LOW_BREAK, HIGH_BREAK = 0.6, 1.7


def require_positive_prices(bars: Sequence[Bar]) -> None:
    if any(bar.open <= 0 or bar.close <= 0 for bar in bars):
        raise InsufficientData("the bars contain non-positive prices")
