from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta

from athena.contracts import AthenaError
from athena.price_integrity.actions import CorporateAction, parse_subject

HOME = "https://www.nseindia.com"
API = "https://www.nseindia.com/api/corporates-corporateActions"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
YEARS_BACK = 8  # the backtest's history
TIMEOUT = 20


class NseActions:
    """A stock's corporate actions from NSE's public website endpoint (the one its own pages use): splits, bonuses,
    demergers, rights issues, buybacks and the like, each with an ex-date and the exchange's own wording. It is not a
    documented API, so any failure is an `AthenaError` for the caller to report and carry on without. It states ratios
    only as text, and a demerger has none."""

    def __init__(self, session=None, today: Callable[[], date] = date.today):
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        self._session.headers.update(HEADERS)
        self._today = today
        self._primed = False

    def __call__(self, symbol: str) -> list[CorporateAction]:
        today = self._today()
        params = {
            "index": "equities", "symbol": symbol,
            "from_date": _stamp(today.replace(year=today.year - YEARS_BACK)), "to_date": _stamp(today.replace(year=today.year + 1)),
        }
        try:
            if not self._primed:
                self._session.get(HOME, timeout=TIMEOUT)  # sets the cookies the API wants; its own status does not matter
                self._primed = True
            response = self._session.get(API, params=params, timeout=TIMEOUT)
            if response.status_code != 200:
                raise AthenaError(f"HTTP {response.status_code}")
            rows = response.json()
            if not isinstance(rows, list):
                raise AthenaError("unexpected reply shape")
        except Exception as exc:
            raise AthenaError(f"NSE corporate actions unavailable for {symbol}: {exc}") from exc
        actions = []
        for row in rows:
            try:
                day = datetime.strptime(str(row["exDate"]).strip(), "%d-%b-%Y").date()
                action = parse_subject(day, str(row["subject"]))
                if action is not None:
                    action = replace(action, face_value=_face_value(row.get("faceVal")))
            except (KeyError, ValueError):
                continue  # a row with no usable ex-date or text is not an event we can place
            if action is not None:
                actions.append(action)
        return sorted(actions, key=lambda action: action.day)


def _face_value(text) -> float | None:
    try:
        value = float(str(text).strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _stamp(day: date) -> str:
    return day.strftime("%d-%m-%Y")
