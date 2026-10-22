"""The US 10-year Treasury yield, the one market rate the dollar-based cost of capital needs.

Damodaran works his cost of capital in US dollars from the US government bond rate less the US default spread, and turns it into another currency with the two expected
inflation rates. That avoids needing an Indian government bond yield, for which no free daily source was found that holds up (FBIL and CCIL serve their yields through
scripted pages, and the free data sites tried either block scripts or carry no Indian series). The Treasury yield comes from Yahoo's public chart service (`^TNX`, quoted in
percent) and is stored as the dated input `us_10y_yield`; it can also be entered by hand with `python -m athena.reference input us_10y_yield 0.0425`."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

import requests

from athena.contracts import AthenaError
from athena.reference.store import ReferenceStore

URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?range=10d&interval=1d"
KEY = "us_10y_yield"
PLAUSIBLE = (0.0, 0.2)  # a yield outside 0 to 20 percent is a bad reading


def parse_chart(payload: dict[str, Any]) -> tuple[date, float]:
    """The latest daily close in a Yahoo chart reply as (day, yield as a fraction). `^TNX` is quoted in percent (4.25 is 4.25%)."""
    try:
        result = payload["chart"]["result"][0]
        stamps, closes = result["timestamp"], result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AthenaError(f"the treasury-yield reply has no price series: {exc!r}") from exc
    offset = int(result.get("meta", {}).get("gmtoffset", 0))
    pairs = [(t, c) for t, c in zip(stamps, closes) if isinstance(c, (int, float))]
    if not pairs:
        raise AthenaError("the treasury-yield reply holds no closes")
    stamp, close = pairs[-1]
    day = datetime.fromtimestamp(stamp + offset, tz=timezone.utc).date()
    value = close / 100.0
    if not PLAUSIBLE[0] < value < PLAUSIBLE[1]:
        raise AthenaError(f"the treasury yield read {close} percent, which is not plausible")
    return day, value


def fetch_us_10y(get: Callable[..., Any] = requests.get, timeout: float = 30.0) -> tuple[date, float]:
    response = get(URL, headers={"User-Agent": "Mozilla/5.0 (personal research)"}, timeout=timeout)
    if response.status_code != 200:
        raise AthenaError(f"the treasury-yield service answered {response.status_code}")
    return parse_chart(response.json())


def store_us_10y(store: ReferenceStore, get: Callable[..., Any] = requests.get) -> tuple[date, float]:
    day, value = fetch_us_10y(get)
    store.put_input(KEY, day, value, "Yahoo Finance ^TNX", "US 10-year Treasury yield, daily close")
    return day, value
