"""Attention around a stock (evidence plan E4): how much market-wide fear there is (India VIX, already in the warehouse) and how much people looked the company up (Wikipedia page views).

    python -m athena.evidence.attention SYMBOL [--company NAME] [--as-of 2026-11-09]      one JSON document on stdout

Attention is not sentiment: a spike in page views says people looked, not whether they liked what they found. Nothing here feeds a verdict (plan section 9). All arithmetic is here, over data dated on or
before the as-of day. Page views come from the Wikimedia REST API (open, no key; one polite request per call with the honest user agent); the article is found by a title search and shown with its
description so a wrong match can be seen. Results are cached for six hours so opening the page twice makes one set of requests. GDELT, BSE, Yahoo and Google Trends were tried or considered and are listed
off with the reason (`athena.evidence.sources.TRIED_AND_OFF`), not hidden."""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

SCHEMA = "athena.attention/1"
VIX_NAME = "India VIX"
CACHE_PATH = Path(".athena") / "attention_cache.json"
CACHE_HOURS = 6
VIEW_DAYS = 120  # days of page views read: a 90-day base and the last week
BASE_DAYS = 90
RECENT_DAYS = 7
SPIKE_FACTOR = 3.0
SEARCH_URL = "https://en.wikipedia.org/w/rest.php/v1/search/title?q={q}&limit=3"
VIEWS_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{article}/daily/{start}/{end}"
NOT_SENTIMENT = "Attention is not sentiment: more page views say people looked, not what they thought. Nothing here feeds a verdict."
FILLER = re.compile(r"\b(limited|ltd|private|pvt|the|of|and|&)\b|[^a-z0-9 ]", re.IGNORECASE)

Get = Callable[[str], Any]  # url -> parsed JSON; raises on any failure


def http_get_json(url: str) -> Any:
    import requests

    from athena.news.publisher_feeds import USER_AGENT

    response = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=15)
    if response.status_code != 200:
        raise RuntimeError(f"the server answered {response.status_code}")
    return response.json()


def vix_view(close: Mapping[date, float], as_of: date) -> dict[str, Any]:
    """The India VIX level on the last day on or before `as_of`, where it stands in the last year and in all history held, and its change over 20 trading days."""
    days = sorted(d for d in close if d <= as_of)
    if len(days) < 30:
        return {"status": "off", "reason": f"India VIX has {len(days)} days on or before {as_of.isoformat()} in the warehouse; at least 30 are needed"}
    last = days[-1]
    level = close[last]
    year = [close[d] for d in days if (last - d).days <= 365]
    every = [close[d] for d in days]

    def rank(values: Sequence[float]) -> float:
        return round(sum(1 for v in values if v <= level) / len(values) * 100.0, 1)

    back = close[days[-21]] if len(days) > 20 else None
    return {"status": "ok", "day": last.isoformat(), "level": round(level, 2), "percentile_1y": rank(year), "percentile_all": rank(every), "history_from": days[0].isoformat(),
            "change_20d_points": None if back is None else round(level - back, 2), "stale_days": (as_of - last).days}


def _tokens(name: str) -> set[str]:
    return {t for t in FILLER.sub(" ", name.lower()).split() if len(t) > 1}


def find_article(company: str, get: Get) -> dict[str, Any] | None:
    """The Wikipedia article for a company by title search: the first candidate whose title (or the name it matched) is the company's name, ignoring `Limited` and punctuation, or is the name with at most one
    word left off (`Tata Consultancy Services` for `Tata Consultancy Services Limited India`). A title that adds words (`Infosys BPM` for `Infosys`) is another entity and is skipped. None when none does."""
    want = _tokens(company)
    if not want:
        return None
    for page in (get(SEARCH_URL.format(q=quote(company))).get("pages") or []):
        matched = _tokens(str(page.get("matched_title") or page.get("title") or ""))
        title = _tokens(str(page.get("title") or ""))
        if want in (matched, title) or (title and title <= want and len(title) >= len(want) - 1):
            return {"article": str(page["key"]), "title": str(page.get("title") or page["key"]), "description": str(page.get("description") or "")}
    return None


def pageview_view(rows: Sequence[Mapping[str, Any]], as_of: date) -> dict[str, Any]:
    """From daily views: the last week's daily average against the median of the 90 days before it, the largest single day and the days above three times the median. Only days on or before `as_of`."""
    series: dict[date, int] = {}
    for r in rows:
        stamp = str(r.get("timestamp") or "")
        if len(stamp) >= 8 and stamp[:8].isdigit() and isinstance(r.get("views"), int):
            d = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
            if d <= as_of:
                series[d] = r["views"]
    days = sorted(series)
    if len(days) < RECENT_DAYS + 30:
        return {"status": "off", "reason": f"only {len(days)} days of page views are held for this article; at least {RECENT_DAYS + 30} are needed"}
    recent_days, base_days = days[-RECENT_DAYS:], days[-(RECENT_DAYS + BASE_DAYS): -RECENT_DAYS]
    base = statistics.median(series[d] for d in base_days)
    recent = sum(series[d] for d in recent_days) / len(recent_days)
    peak = max(days, key=lambda d: series[d])
    return {"status": "ok", "from": days[0].isoformat(), "to": days[-1].isoformat(), "last_7_days_daily_average": round(recent, 1), "median_daily_views_before": round(base, 1),
            "ratio": None if not base else round(recent / base, 2), "peak_day": peak.isoformat(), "peak_views": series[peak],
            "days_above_3x_median": [d.isoformat() for d in days[-BASE_DAYS:] if base and series[d] > SPIKE_FACTOR * base][:10], "stale_days": (as_of - days[-1]).days}


def _cached(path: Path, key: str, now: datetime) -> dict[str, Any] | None:
    try:
        entry = json.loads(path.read_text(encoding="utf-8")).get(key)
        if entry and now - datetime.fromisoformat(entry["at"]) < timedelta(hours=CACHE_HOURS):
            return entry["value"]
    except (OSError, ValueError, KeyError):
        return None
    return None


def _store(path: Path, key: str, value: dict[str, Any], now: datetime) -> None:
    try:
        held = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        held[key] = {"at": now.isoformat(), "value": value}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(held), encoding="utf-8")
    except (OSError, ValueError):
        pass  # a cache that cannot be written only costs a second request


def wikipedia_view(company: str, as_of: date, get: Get, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    try:
        article = find_article(company, get)
        if article is None:
            return {"status": "off", "reason": f"no Wikipedia article was found whose title holds every word of {company!r}"}
        sleep(0.3)  # one polite request after another
        start, end = (as_of - timedelta(days=VIEW_DAYS)).strftime("%Y%m%d"), as_of.strftime("%Y%m%d")
        items = get(VIEWS_URL.format(article=quote(article["article"], safe=""), start=start, end=end)).get("items") or []
        return {**pageview_view(items, as_of), "article": article["title"], "description": article["description"], "link": "https://en.wikipedia.org/wiki/" + quote(article["article"])}
    except Exception as exc:  # noqa: BLE001 - attention is an addition: a failure is a state shown with its reason
        return {"status": "off", "reason": f"{type(exc).__name__}: {str(exc)[:120]}"}


def attention(symbol: str, company: str, as_of: date, vix_close: Mapping[date, float], get: Get = http_get_json, cache: Path | None = CACHE_PATH, now: datetime | None = None,
              sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """The attention document for one stock. The wikipedia part is cached for six hours when the as-of day is today."""
    from athena.evidence.sources import TRIED_AND_OFF

    now = now or datetime.now()
    key = f"{symbol}:{as_of.isoformat()}"
    wiki = _cached(cache, key, now) if cache is not None else None
    if wiki is None:
        wiki = wikipedia_view(company, as_of, get, sleep)
        if cache is not None and wiki.get("status") == "ok":
            _store(cache, key, wiki, now)
    return {"schema": SCHEMA, "symbol": symbol, "as_of": as_of.isoformat(), "vix": vix_view(vix_close, as_of), "wikipedia": wiki, "off": [{"id": s.id, "name": s.name, "reason": s.reason} for s in TRIED_AND_OFF], "caveat": NOT_SENTIMENT}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="attention around one stock")
    parser.add_argument("symbol")
    parser.add_argument("--company", default=None, help="the company name to look up (default: its name in the warehouse)")
    parser.add_argument("--as-of", default=date.today().isoformat())
    args = parser.parse_args(argv)
    symbol, as_of = args.symbol.upper(), date.fromisoformat(args.as_of)
    try:
        from athena.sentiment.page import _company
        from athena.warehouse.warehouse import Warehouse

        warehouse = Path(".athena") / "warehouse.duckdb"
        company = args.company or _company(warehouse, symbol)
        w = Warehouse(warehouse, read_only=True, wait_seconds=10)
        held = {n.lower(): n for n in w.index_names()}
        vix = w.index_close(held[VIX_NAME.lower()]) if VIX_NAME.lower() in held else {}
        print(json.dumps(attention(symbol, company, as_of, vix), indent=1, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
