"""The exchange's structured disclosures: insider trades, the FII/DII daily flows and board-meeting notices.

    python -m athena.evidence.structured              one pass (each source only when it is due)
    python -m athena.evidence.structured --force      every source now
    python -m athena.evidence.structured --backfill-days 400   insider trades and board meetings back to that many days ago, in 30-day chunks

These are filings, not stories: a row says who did what on which day, with the exchange's own timestamp. Each is stored with when WE first saw it. The reader asks a public website endpoint that
needs a cookie from the home page; it is unofficial, so any failure ends as a failed source on the health line, never as an error that stops the pass. Nothing here names a company.

What was measured on 2026-10-29: the insider-trade endpoint answers a date window for every company at once, but returned no rows for any window after April 2026 (it still answered for
earlier months); the page says so rather than showing an empty box as if nothing had happened. The FII/DII endpoint gives only the latest day (two rows), so history builds from the day collection starts."""
from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from athena.contracts import AthenaError
from athena.evidence.store import DEFAULT_PATH, EvidenceStore
from athena.ops.jobs import Job, JobLocked

IST = timezone(timedelta(hours=5, minutes=30))
API = "https://www.nseindia.com/api"
PIT_URL, FLOWS_URL, BOARD_URL = f"{API}/corporates-pit", f"{API}/fiidiiTradeReact", f"{API}/corporate-board-meetings"
SPACING_SECONDS = 1.5
CHUNK_DAYS = 30
# how often each source is worth asking, in hours
EVERY_HOURS = {"nse_pit": 6.0, "nse_fiidii": 3.0, "nse_board": 6.0}
PIT_RECENT_DAYS = 30
BOARD_BACK_DAYS, BOARD_FORWARD_DAYS = 3, 21


@dataclass(frozen=True)
class InsiderTrade:
    trade_id: str
    symbol: str
    person: str
    category: str  # Promoters, Promoter Group, Director, Key Managerial Personnel, Employees/Designated Employees, Immediate relative, Other, -
    txn: str  # Buy, Sell, Pledge, Pledge Revoke, Pledge Invoke, or - (the exchange gave none)
    mode: str  # Market Purchase, Market Sale, ESOP, Off Market, Gift, ...
    security: str
    shares: float | None
    value: float | None  # rupees
    before_shares: float | None
    after_shares: float | None
    before_pct: float | None
    after_pct: float | None
    trade_from: date | None
    trade_to: date | None
    filed_at: datetime  # the exchange's stamp, UTC
    link: str


@dataclass(frozen=True)
class MarketFlow:
    day: date
    category: str  # FII/FPI or DII
    buy: float  # rupees crore
    sell: float
    net: float


@dataclass(frozen=True)
class BoardMeeting:
    meeting_id: str
    symbol: str
    company: str
    meeting_date: date
    purpose: str
    description: str
    announced_at: datetime  # UTC
    link: str


def _num(value: Any) -> float | None:
    try:
        text = str(value).strip().replace(",", "")
        return None if text in ("", "-", "None") else float(text)
    except ValueError:
        return None


def _day(text: Any) -> date | None:
    try:
        return datetime.strptime(str(text).strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def _stamp(text: Any) -> datetime | None:
    """The exchange's own timestamp (India time) as UTC."""
    for form in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return datetime.strptime(str(text).strip(), form).replace(tzinfo=IST).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _link(text: Any) -> str:
    link = str(text or "").strip()
    return link if link.startswith("https://") else ""


def _rows(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise AthenaError("unexpected reply shape")
    return [r for r in rows if isinstance(r, dict)]


def parse_insider(payload: Any) -> list[InsiderTrade]:
    """Insider-trade rows. A row with no symbol or no readable filing time is skipped. The id is built from the exchange's disclosure and person ids plus the figures, so a re-read gives the same id."""
    import hashlib

    out = []
    for r in _rows(payload):
        symbol = str(r.get("symbol") or "").strip().upper()
        filed = _stamp(r.get("date"))
        if not symbol or filed is None:
            continue
        key = "|".join(str(r.get(k, "")) for k in ("did", "pid", "symbol", "acqName", "tdpTransactionType", "secAcq", "acqfromDt", "secType"))
        out.append(InsiderTrade(
            hashlib.sha1(key.encode()).hexdigest(), symbol, str(r.get("acqName") or "").strip(), str(r.get("personCategory") or "-").strip(), str(r.get("tdpTransactionType") or "-").strip(),
            str(r.get("acqMode") or "-").strip(), str(r.get("secType") or "").strip(), _num(r.get("secAcq")), _num(r.get("secVal")), _num(r.get("befAcqSharesNo")), _num(r.get("afterAcqSharesNo")),
            _num(r.get("befAcqSharesPer")), _num(r.get("afterAcqSharesPer")), _day(r.get("acqfromDt")), _day(r.get("acqtoDt")), filed, _link(r.get("xbrl")),
        ))
    return out


def parse_flows(payload: Any) -> list[MarketFlow]:
    out = []
    for r in _rows(payload):
        day, buy, sell, net = _day(r.get("date")), _num(r.get("buyValue")), _num(r.get("sellValue")), _num(r.get("netValue"))
        category = str(r.get("category") or "").strip()
        if day is not None and category and None not in (buy, sell, net):
            out.append(MarketFlow(day, category, buy, sell, net))  # type: ignore[arg-type]
    return out


def parse_board(payload: Any) -> list[BoardMeeting]:
    import hashlib

    out = []
    for r in _rows(payload):
        symbol, meeting, announced = str(r.get("bm_symbol") or "").strip().upper(), _day(r.get("bm_date")), _stamp(r.get("bm_timestamp"))
        if not symbol or meeting is None or announced is None:
            continue
        purpose = str(r.get("bm_purpose") or "").strip()
        key = f"{symbol}|{meeting}|{r.get('bm_timestamp')}|{purpose}"
        out.append(BoardMeeting(hashlib.sha1(key.encode()).hexdigest(), symbol, str(r.get("sm_name") or "").strip(), meeting, purpose, str(r.get("bm_desc") or "").strip()[:600], announced, _link(r.get("ixbrl") or r.get("attachment"))))
    return out


class NseJson:
    """A cookie-primed session for the exchange's JSON endpoints. Anything but a JSON 200 is an `AthenaError`."""

    def __init__(self, session: Any = None):
        if session is None:
            import requests

            session = requests.Session()
        from athena.price_integrity.nse_actions import HEADERS

        self._session = session
        self._session.headers.update(HEADERS)
        self._primed = False

    def __call__(self, url: str, params: dict[str, str] | None = None) -> Any:
        from athena.price_integrity.nse_actions import HOME, TIMEOUT

        try:
            if not self._primed:
                self._session.get(HOME, timeout=TIMEOUT)  # cookies; the status of this one does not matter
                self._primed = True
            response = self._session.get(url, params=params or {}, timeout=TIMEOUT)
            if response.status_code != 200:
                raise AthenaError(f"HTTP {response.status_code}")
            return response.json()
        except AthenaError:
            raise
        except Exception as exc:
            raise AthenaError(f"{type(exc).__name__}: {exc}") from exc


def _fmt(day: date) -> str:
    return day.strftime("%d-%m-%Y")


def _due(health: dict[str, dict[str, Any]], source_id: str, now: datetime, force: bool) -> bool:
    if force:
        return True
    last = health.get(source_id, {}).get("last_ok")
    return last is None or now.replace(tzinfo=None) - last >= timedelta(hours=EVERY_HOURS[source_id])


def collect_insider(store: EvidenceStore, get: Callable[..., Any], now: datetime, since: date | None = None, until: date | None = None) -> tuple[str, int]:
    """Insider trades filed in the window (by default the last 30 days), every company at once."""
    today = now.astimezone(IST).date()
    first, last = since or today - timedelta(days=PIT_RECENT_DAYS), until or today
    try:
        trades = parse_insider(get(PIT_URL, {"index": "equities", "from_date": _fmt(first), "to_date": _fmt(last)}))
    except AthenaError as exc:
        store.record_health("nse_pit", now, False, "failed", str(exc))
        return "failed", 0
    if not trades:
        newest = store.insider_newest()
        store.record_health("nse_pit", now, False, "empty", f"the exchange returned no insider filings for {first} to {last}; the newest held was filed {newest.date() if newest else 'never'}")
        return "empty", 0
    new = store.add_insider(trades, now)
    store.record_health("nse_pit", now, True, "ok", "", len(trades), max(t.filed_at for t in trades))
    return "ok", new


def collect_flows(store: EvidenceStore, get: Callable[..., Any], now: datetime) -> tuple[str, int]:
    try:
        flows = parse_flows(get(FLOWS_URL))
    except AthenaError as exc:
        store.record_health("nse_fiidii", now, False, "failed", str(exc))
        return "failed", 0
    if not flows:
        store.record_health("nse_fiidii", now, False, "empty", "the exchange returned no flow rows")
        return "empty", 0
    new = store.add_flows(flows, now)
    store.record_health("nse_fiidii", now, True, "ok", "", len(flows), datetime.combine(max(f.day for f in flows), datetime.min.time(), tzinfo=timezone.utc))
    return "ok", new


def collect_board(store: EvidenceStore, get: Callable[..., Any], now: datetime, since: date | None = None, until: date | None = None) -> tuple[str, int]:
    today = now.astimezone(IST).date()
    first, last = since or today - timedelta(days=BOARD_BACK_DAYS), until or today + timedelta(days=BOARD_FORWARD_DAYS)
    try:
        meetings = parse_board(get(BOARD_URL, {"index": "equities", "from_date": _fmt(first), "to_date": _fmt(last)}))
    except AthenaError as exc:
        store.record_health("nse_board", now, False, "failed", str(exc))
        return "failed", 0
    if not meetings:
        store.record_health("nse_board", now, False, "empty", f"the exchange listed no board meetings for {first} to {last}")
        return "empty", 0
    new = store.add_board(meetings, now)
    store.record_health("nse_board", now, True, "ok", "", len(meetings), max(m.announced_at for m in meetings))
    return "ok", new


def collect_structured(store: EvidenceStore, get: Callable[..., Any], now: datetime | None = None, force: bool = False, sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print) -> dict[str, tuple[str, int]]:
    """Ask each structured source that is due. One failing source does not stop the others."""
    now = now or datetime.now(timezone.utc)
    health = store.health()
    plan: list[tuple[str, Callable[[], tuple[str, int]]]] = [
        ("nse_fiidii", lambda: collect_flows(store, get, now)),
        ("nse_board", lambda: collect_board(store, get, now)),
        ("nse_pit", lambda: collect_insider(store, get, now)),
    ]
    out: dict[str, tuple[str, int]] = {}
    for sid, run in plan:
        if not _due(health, sid, now, force):
            continue
        if out:
            sleep(SPACING_SECONDS)
        out[sid] = run()
        log(f"{sid:<24} {out[sid][0]:<7} {out[sid][1]:>4} new")
    return out


def backfill(store: EvidenceStore, get: Callable[..., Any], days: int, now: datetime | None = None, sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print) -> int:
    """Insider trades and board meetings for the last `days` days, 30 days per request, oldest chunk first. Items already held are skipped, so it can be run again."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(IST).date()
    total, start = 0, today - timedelta(days=days)
    while start <= today:
        end = min(start + timedelta(days=CHUNK_DAYS - 1), today)
        for label, run in (("insider", lambda s=start, e=end: collect_insider(store, get, now, s, e)), ("board", lambda s=start, e=end: collect_board(store, get, now, s, e))):
            status, new = run()
            log(f"{label:<8} {start} to {end}: {status}, {new} new")
            total += new
            sleep(SPACING_SECONDS)
        start = end + timedelta(days=1)
    return total


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--force", action="store_true", help="ask every source now, not only the ones that are due")
    parser.add_argument("--backfill-days", type=int, default=0, help="fetch insider trades and board meetings back this many days")
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    store = EvidenceStore(args.store)
    try:
        with Job("evidence_nse") as job:  # no shared lock with the feed pass: the store waits for its own file
            get = NseJson()
            if args.backfill_days:
                job.beat(backfill=backfill(store, get, args.backfill_days))
            else:
                results = collect_structured(store, get, force=args.force)
                job.beat(done=len(results))
                if results and not any(status == "ok" for status, _ in results.values()):
                    job.finish("stopped", 3, "no structured source worked")
                    return 3
    except JobLocked as exc:
        print(f"not started: {exc}", file=sys.stderr)
        return 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
