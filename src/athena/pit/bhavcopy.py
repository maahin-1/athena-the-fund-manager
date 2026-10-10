"""The exchange's daily price files (bhavcopy): every listed symbol's open, high, low, close and volume for one day, as printed
that day, so never adjusted for a later split or bonus. They sit on the archive host, which keeps answering when the website's
API refuses a burst."""
from __future__ import annotations

import csv
import io
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from athena.clock import utc_now
from athena.contracts import AthenaError, Bar, SchemaChangedError, UnsupportedOperation
from athena.price_integrity.nse_actions import HEADERS

CACHE_DIR = Path(".athena") / "bhavcopy"
ARCHIVE = "https://nsearchives.nseindia.com/content"
OLD_UNTIL = date(2024, 7, 5)  # the old file stops after this day; the new one is there from at least 2024-06-28
MIN_GAP = 1.5
RETRY_WAITS = (30.0, 90.0)
TIMEOUT = 30
SOURCE = "bhavcopy"
SERIES_RANK = {"EQ": 0, "BE": 1, "BZ": 2, "SM": 3, "ST": 4}  # equity series only; bonds and block deals are not price history
OLD_COLUMNS = {"symbol": "SYMBOL", "series": "SERIES", "open": "OPEN", "high": "HIGH", "low": "LOW", "close": "CLOSE", "volume": "TOTTRDQTY"}
NEW_COLUMNS = {"symbol": "TckrSymb", "series": "SctySrs", "open": "OpnPric", "high": "HghPric", "low": "LwPric", "close": "ClsPric", "volume": "TtlTradgVol"}
FIELDS = ("symbol", "series", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class Row:
    symbol: str
    series: str
    open: float
    high: float
    low: float
    close: float
    volume: float


def urls_for(day: date) -> list[str]:
    """The file addresses to try for a day, the layout in use then first."""
    old = f"{ARCHIVE}/historical/EQUITIES/{day:%Y}/{day.strftime('%b').upper()}/cm{day:%d}{day.strftime('%b').upper()}{day:%Y}bhav.csv.zip"
    new = f"{ARCHIVE}/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    return [old, new] if day <= OLD_UNTIL else [new, old]


def parse_bhavcopy(text: str) -> dict[str, Row]:
    """One equity-series row per symbol (EQ preferred) from either file layout. An unknown header is a `SchemaChangedError`."""
    reader = csv.DictReader(io.StringIO(text))
    header = {name.strip() for name in reader.fieldnames or ()}
    columns = next((cols for cols in (OLD_COLUMNS, NEW_COLUMNS) if set(cols.values()) <= header), None)
    if columns is None:
        raise SchemaChangedError(f"unknown price-file header: {sorted(header)[:8]}")
    best: dict[str, tuple[int, Row]] = {}
    for raw in reader:
        row = {key.strip(): (value or "").strip() for key, value in raw.items() if key}
        rank = SERIES_RANK.get(row[columns["series"]])
        if rank is None:
            continue
        symbol = row[columns["symbol"]]
        if symbol in best and best[symbol][0] <= rank:
            continue
        try:
            values = [float(row[columns[key]]) for key in ("open", "high", "low", "close", "volume")]
        except ValueError:
            raise SchemaChangedError(f"a price file row for {symbol} holds a value that is not a number") from None
        best[symbol] = (rank, Row(symbol, row[columns["series"]], *values))
    return {symbol: row for symbol, (_, row) in best.items()}


class ArchiveClient:
    """Fetches one file: spaced from the last request, retried after a pause when refused. A missing file (404) is None;
    anything else that fails is an `AthenaError`."""

    def __init__(self, session=None, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        self._session.headers.update(HEADERS)
        self._sleep, self._clock = sleep, clock
        self._last = -MIN_GAP

    def get(self, url: str) -> bytes | None:
        status = 0
        for wait in (0.0, *RETRY_WAITS):
            if wait:
                self._sleep(wait)
            gap = MIN_GAP - (self._clock() - self._last)
            if gap > 0:
                self._sleep(gap)
            self._last = self._clock()
            try:
                response = self._session.get(url, timeout=TIMEOUT)
            except Exception as exc:
                raise AthenaError(f"price file unavailable ({url}): {exc}") from exc
            status = response.status_code
            if status == 200:
                return response.content
            if status == 404:
                return None
            if status not in (403, 429, 503):
                break
        raise AthenaError(f"price file refused ({url}): HTTP {status}")


def _unzip(content: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            return archive.read(archive.namelist()[0]).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, IndexError) as exc:
        raise AthenaError(f"price file is not a zip archive: {exc}") from exc


class BhavcopyStore:
    """Each day's file is downloaded once and kept on disk in a short form. A day with no file on either address is kept as an
    empty file, so a holiday is not asked for twice. A published day never changes."""

    def __init__(self, client: ArchiveClient | None = None, directory: Path = CACHE_DIR, fetch: Callable[[str], bytes | None] | None = None):
        self._fetch = fetch or (client or ArchiveClient()).get
        self._directory = directory

    def day(self, day: date) -> dict[str, Row] | None:
        """The day's rows by symbol, or None when the exchange has no file for it (a weekend or holiday)."""
        path = self._directory / f"{day.isoformat()}.csv"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            return _read_short(text) if text else None
        rows: dict[str, Row] | None = None
        for url in urls_for(day):
            content = self._fetch(url)
            if content is not None:
                rows = parse_bhavcopy(_unzip(content))
                break
        self._directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".part")
        temporary.write_text(_write_short(rows) if rows else "", encoding="utf-8")
        temporary.replace(path)  # a run stopped half way never leaves a day half written
        return rows or None


def _write_short(rows: dict[str, Row]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(FIELDS)
    for row in rows.values():
        writer.writerow([getattr(row, field) for field in FIELDS])
    return out.getvalue()


def _read_short(text: str) -> dict[str, Row]:
    return {
        r["symbol"]: Row(r["symbol"], r["series"], *(float(r[key]) for key in ("open", "high", "low", "close", "volume")))
        for r in csv.DictReader(io.StringIO(text))
    }


class BhavcopyPriceAdapter:
    """Daily bars from the day files, in the shape of the other price adapters. `is_trading_day`, when given, makes a missing
    file on a trading day an error: a hole in the history is not read as a holiday."""

    name = SOURCE

    def __init__(
        self,
        store: BhavcopyStore | None = None,
        is_trading_day: Callable[[date], bool] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self._store = store or BhavcopyStore()
        self._is_trading_day = is_trading_day
        self._clock = clock or utc_now

    def describe(self) -> dict:
        return {"fetch_ohlcv": True, "fetch_quote": False, "timeframes": ["1d"]}

    def fetch_quote(self, symbol: str, **params):
        raise UnsupportedOperation(f"{self.name} adapter does not provide quotes")

    def fetch_ohlcv(self, symbol: str, timeframe: str, since=None, limit=None, **params) -> list[Bar]:
        if timeframe != "1d":
            raise UnsupportedOperation(f"{self.name} adapter only supports the 1d timeframe, got {timeframe!r}")
        now = self._clock()
        end = params.get("until") or now.date()
        start = since or end - timedelta(days=30)
        bars: list[Bar] = []
        day = start
        while day <= end:
            if day.weekday() < 5:
                rows = self._store.day(day)
                if rows is None:
                    if self._is_trading_day is not None and self._is_trading_day(day):
                        raise AthenaError(f"no price file for {day}, a trading day")
                elif symbol in rows:
                    row = rows[symbol]
                    # IST midnight written as a naive UTC value, as the jugaad adapter does: the 1 Oct session is 30 Sep 18:30
                    stamp = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - timedelta(hours=5, minutes=30)
                    bars.append(Bar(symbol, stamp, row.open, row.high, row.low, row.close, row.volume, now, SOURCE))
            day += timedelta(days=1)
        return bars[-limit:] if limit else bars
