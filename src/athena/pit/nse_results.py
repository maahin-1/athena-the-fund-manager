from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from athena.contracts import AthenaError
from athena.pit.xbrl import Filing, parse_filing
from athena.price_integrity.nse_actions import HEADERS, HOME, TIMEOUT

API = "https://www.nseindia.com/api/corporates-financial-results"
CACHE_DIR = Path(".athena") / "xbrl"
IST = timezone(timedelta(hours=5, minutes=30))
MIN_GAP = 1.5  # seconds between requests: NSE answers 503 and then 403 to a burst, and the block lasts minutes
RETRY_WAITS = (30.0, 90.0)  # pauses before the second and third attempt at a refused request
XBRL_FROM = date(2018, 10, 1)  # the exchange has XBRL for results from Ind-AS adoption; earlier filings answer 404


@dataclass(frozen=True)
class FilingRef:
    """One entry of a company's results list: which period, when it was filed, and where its XBRL is."""

    symbol: str
    period_end: date
    filed: datetime  # timezone-aware, to the minute
    consolidated: bool
    annual: bool
    url: str


def _stamp(text: str, formats: tuple[str, ...]) -> datetime | None:
    for form in formats:
        try:
            return datetime.strptime(text.strip(), form).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


class NseResults:
    """A company's results filings from NSE's website endpoint (unofficial), with the date and time each was filed. Any failure
    is an `AthenaError`. Only filings that have both a filing time and an XBRL file are listed: the rest cannot be placed in
    time, and a figure whose publication date is unknown is never used."""

    def __init__(self, session=None, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        self._session.headers.update(HEADERS)
        self._sleep, self._clock = sleep, clock
        self._primed = False
        self._last = -MIN_GAP

    def _get(self, url: str, params: dict | None = None):
        """One request, spaced from the last, retried after a pause when the exchange refuses (503, 403, 429). A refusal that
        outlasts the retries is an `AthenaError`: the caller reports it and carries on, and nothing is guessed."""
        problem = "no reply"
        for wait in (0.0, *RETRY_WAITS):
            if wait:
                self._sleep(wait)
                self._primed = False  # a refused session needs its cookies again
            gap = MIN_GAP - (self._clock() - self._last)
            if gap > 0:
                self._sleep(gap)
            if not self._primed:
                self._session.get(HOME, timeout=TIMEOUT)  # cookies; its own status does not matter
                self._primed = True
            self._last = self._clock()
            try:
                response = self._session.get(url, params=params, timeout=TIMEOUT)
            except requests.RequestException as exc:  # a timeout or a dropped connection: wait, then try again
                problem = f"{type(exc).__name__}"
                continue
            if response.status_code == 200:
                return response
            problem = f"HTTP {response.status_code}"
            if response.status_code not in (403, 429, 503):
                break
        raise AthenaError(problem)

    def filings(self, symbol: str) -> list[FilingRef]:
        refs: list[FilingRef] = []
        try:
            for period, annual in (("Quarterly", False), ("Annual", True)):
                rows = self._get(API, {"index": "equities", "symbol": symbol, "period": period}).json()
                if not isinstance(rows, list):
                    raise AthenaError("unexpected reply shape")
                for row in rows:
                    ref = _ref(symbol, row, annual)
                    if ref is not None:
                        refs.append(ref)
        except Exception as exc:
            raise AthenaError(f"NSE results unavailable for {symbol}: {exc}") from exc
        return sorted(refs, key=lambda ref: (ref.filed, ref.consolidated))

    def text(self, ref: FilingRef) -> str:
        try:
            return self._get(ref.url).text
        except AthenaError:
            raise
        except Exception as exc:
            raise AthenaError(f"NSE filing unavailable ({ref.url}): {exc}") from exc


def _ref(symbol: str, row: dict, annual: bool) -> FilingRef | None:
    end = _stamp(str(row.get("toDate", "")), ("%d-%b-%Y",))
    filed = _stamp(str(row.get("filingDate", "")), ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"))
    url = str(row.get("xbrl") or "")
    if end is None or filed is None or not url.endswith(".xml") or end.date() < XBRL_FROM:
        return None
    nature = str(row.get("consolidated", "")).strip().lower()
    return FilingRef(symbol, end.date(), filed, not nature.startswith("non"), annual, url)


class FilingStore:
    """Downloads each filing once and keeps the text on disk, so a test run that revisits a company does not ask the exchange
    again. A filing never changes once published, so the cache never expires."""

    def __init__(self, results: NseResults, directory: Path = CACHE_DIR, fetch: Callable[[FilingRef], str] | None = None):
        self._results = results
        self._directory = directory
        self._fetch = fetch or results.text

    def filing(self, ref: FilingRef) -> Filing:
        path = self._directory / (hashlib.sha1(ref.url.encode()).hexdigest()[:20] + ".xml")
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            text = self._fetch(ref)
            self._directory.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        try:
            return parse_filing(text)
        except ValueError as exc:
            raise AthenaError(f"unreadable filing {ref.url}: {exc}") from exc


class CachedFilings:
    """A company's results list kept on disk after the first successful fetch, so a run that revisits it, or restarts, does not ask
    the exchange again. Meant for dates well in the past, where the list no longer grows: a filing made after the list was saved
    would be missing from it."""

    def __init__(self, results: NseResults, directory: Path = CACHE_DIR, fetch: Callable[[str], list[FilingRef]] | None = None):
        self._fetch = fetch or results.filings
        self._directory = directory

    def filings(self, symbol: str) -> list[FilingRef]:
        path = self._directory / f"index_{symbol}.json"
        if path.exists():
            try:
                return [
                    FilingRef(r["symbol"], date.fromisoformat(r["period_end"]), datetime.fromisoformat(r["filed"]), r["consolidated"], r["annual"], r["url"])
                    for r in json.loads(path.read_text(encoding="utf-8"))
                ]
            except (ValueError, KeyError, TypeError):
                pass  # an unreadable cache is fetched again
        refs = self._fetch(symbol)
        self._directory.mkdir(parents=True, exist_ok=True)
        rows = [
            {"symbol": r.symbol, "period_end": r.period_end.isoformat(), "filed": r.filed.isoformat(), "consolidated": r.consolidated, "annual": r.annual, "url": r.url}
            for r in refs
        ]
        temporary = path.with_suffix(".part")
        temporary.write_text(json.dumps(rows), encoding="utf-8")
        temporary.replace(path)
        return refs
