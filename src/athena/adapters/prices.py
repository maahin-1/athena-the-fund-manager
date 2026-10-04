from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

from athena.clock import utc_now
from athena.contracts import Bar, Quote, SchemaChangedError, UnsupportedOperation
from athena.fallback import FallbackChain
from athena.trading_calendar import TradingCalendar, ist_date

UTC = timezone.utc
_DEFAULT_WINDOW_DAYS = 30
_JUGAAD_COLUMNS = ("DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME")
_YAHOO_COLUMNS = ("Date", "Open", "High", "Low", "Close", "Volume")


def bars_from_jugaad(rows: list[dict], symbol: str, as_of: datetime) -> list[Bar]:
    bars = []
    for row in rows:
        missing = [c for c in _JUGAAD_COLUMNS if c not in row]
        if missing:
            raise SchemaChangedError(f"jugaad row missing columns {missing}")
        ts = row["DATE"]
        # jugaad-data returns IST midnight as a naive UTC value: the 1 Oct session is 30 Sep 18:30.
        if (ts.hour, ts.minute) != (18, 30):
            raise SchemaChangedError(
                f"unexpected jugaad timestamp {ts!r}: expected 18:30 (IST midnight as naive UTC)"
            )
        stamp = datetime(ts.year, ts.month, ts.day, ts.hour, ts.minute, tzinfo=UTC)
        bars.append(
            Bar(
                symbol, stamp, float(row["OPEN"]), float(row["HIGH"]), float(row["LOW"]),
                float(row["CLOSE"]), float(row["VOLUME"]), as_of, "jugaad",
            )
        )
    return sorted(bars, key=lambda bar: bar.timestamp)


def bars_from_yahoo(rows: list[dict], symbol: str, as_of: datetime) -> list[Bar]:
    bars = []
    for row in rows:
        missing = [c for c in _YAHOO_COLUMNS if c not in row]
        if missing:
            raise SchemaChangedError(f"yahoo row missing columns {missing}")
        ts = row["Date"]
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        if ts.tzinfo is None:
            raise SchemaChangedError("yahoo timestamp must be timezone-aware")
        bars.append(
            Bar(
                symbol, ts.astimezone(UTC), float(row["Open"]), float(row["High"]), float(row["Low"]),
                float(row["Close"]), float(row["Volume"]), as_of, "yahoo",
            )
        )
    return sorted(bars, key=lambda bar: bar.timestamp)


def drop_non_trading_days(bars: list[Bar], calendar: TradingCalendar) -> list[Bar]:
    return [bar for bar in bars if calendar.is_trading_day(ist_date(bar.timestamp))]


class _DailyBarAdapter:
    name = ""

    def __init__(
        self,
        fetch_rows: Callable[[str, date, date], list[dict]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._fetch_rows = fetch_rows or self._default_fetch
        self._clock = clock or utc_now

    def describe(self) -> dict:
        return {"fetch_ohlcv": True, "fetch_quote": False, "timeframes": ["1d"]}

    def fetch_quote(self, symbol: str, **params: Any) -> Quote:
        raise UnsupportedOperation(f"{self.name} adapter does not provide quotes")

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: Any = None, limit: Any = None, **params: Any
    ) -> list[Bar]:
        if timeframe != "1d":
            raise UnsupportedOperation(
                f"{self.name} adapter only supports the 1d timeframe, got {timeframe!r}"
            )
        now = self._clock()
        end = params.get("until") or now.date()
        start = since or end - timedelta(days=_DEFAULT_WINDOW_DAYS)
        bars = self._to_bars(self._fetch_rows(symbol, start, end), symbol, now)
        return bars[-limit:] if limit else bars


class JugaadPriceAdapter(_DailyBarAdapter):
    name = "jugaad"

    @staticmethod
    def _default_fetch(symbol: str, start: date, end: date) -> list[dict]:
        from jugaad_data.nse import stock_df

        return stock_df(symbol, from_date=start, to_date=end, series="EQ").to_dict("records")

    @staticmethod
    def _to_bars(rows: list[dict], symbol: str, as_of: datetime) -> list[Bar]:
        return bars_from_jugaad(rows, symbol, as_of)


class YahooPriceAdapter(_DailyBarAdapter):
    name = "yahoo"

    @staticmethod
    def _default_fetch(symbol: str, start: date, end: date) -> list[dict]:
        import yfinance as yf

        frame = yf.Ticker(f"{symbol}.NS").history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,
        )
        return frame.reset_index().to_dict("records")

    @staticmethod
    def _to_bars(rows: list[dict], symbol: str, as_of: datetime) -> list[Bar]:
        return bars_from_yahoo(rows, symbol, as_of)


def ohlcv_chain(adapters: Sequence[Any], calendar: TradingCalendar) -> FallbackChain:
    def source(adapter: Any) -> Callable[..., list[Bar]]:
        def fetch(symbol: str, since: Any = None, limit: Any = None, until: Any = None) -> list[Bar]:
            bars = adapter.fetch_ohlcv(symbol, "1d", since=since, until=until)
            bars = drop_non_trading_days(bars, calendar)
            return bars[-limit:] if limit else bars

        return fetch

    return FallbackChain([(adapter.name, source(adapter)) for adapter in adapters])
