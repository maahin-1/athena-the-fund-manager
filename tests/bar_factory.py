from datetime import date, datetime, timedelta, timezone

from athena.contracts import Bar

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def make_bars(closes, end=date(2026, 10, 2), volume=1000.0, symbol="X"):
    """One bar per weekday ending on `end`, one per close, oldest first; stamped the way jugaad-data stamps IST midnight."""
    days, day = [], end
    while len(days) < len(closes):
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return [
        Bar(symbol, datetime(d.year, d.month, d.day, 18, 30, tzinfo=timezone.utc) - timedelta(days=1), c, c + 1, c - 1, c, volume, NOW, "t")
        for d, c in zip(reversed(days), closes)
    ]
