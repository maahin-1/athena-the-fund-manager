"""The Sentiment page for one stock: what the exchange and the press reported, what people posted, and optionally a reading of the news. Display only.

    python -m athena.sentiment.page SYMBOL [--tone] [--warehouse PATH]       one JSON document on stdout

*News*: the exchange's own filings, the publishers' feeds and, only when the owner has switched it on, Google News, over the last 30 days, each marked as routine (a filing every company
files) or not. *Social*: Reddit posts that name the company, when the owner has set the app's keys (`athena.sentiment.reddit`); otherwise the reason it is off. *Tone* (`--tone`, one model
call): the news analyst's reading of the news only, never the posts. Nothing here feeds a verdict: whether the tone of news or posts says anything about later returns has not been tested,
and the page says so. Untrusted text is cut and shown, never followed."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from athena.contracts import AthenaError
from athena.news.model import NewsItem, company_names, loose_names, mentions
from athena.news.packet import WINDOW_DAYS, build_news_packet, is_routine
from athena.sentiment.reddit import OFF_NOTE, RedditSearch, SocialPost, credentials

SYMBOL_OK = __import__("re").compile(r"^[A-Z0-9&-]{1,20}$")
NOT_TESTED = "Whether the tone of news or posts says anything about later returns has not been tested here; this page is for reading, and nothing on it feeds a verdict."


def _safe_link(link: str) -> str:
    return link if link.startswith(("https://", "http://")) else ""


def news_rows(items: Sequence[NewsItem], company: str, today: date) -> list[dict[str, Any]]:
    """The last 30 days' items newest first, each with whether it is a routine filing and whether a press item names the company (a press result that does not is not used)."""
    names = loose_names(company_names(company))
    since = today - timedelta(days=WINDOW_DAYS)
    rows = []
    for i in sorted(items, key=lambda x: x.published, reverse=True):
        if not since <= i.published.date() <= today:
            continue
        routine = i.source == "nse" and is_routine(i.title)
        rows.append({
            "date": i.published.date().isoformat(), "publisher": i.publisher, "source": i.source, "title": i.title, "link": _safe_link(i.link), "routine": routine,
            "used": not routine and (i.source == "nse" or mentions(i, names)),
        })
    return rows


def social_rows(posts: Sequence[SocialPost], today: date) -> list[dict[str, Any]]:
    return [
        {"date": p.created.date().isoformat(), "subreddit": p.subreddit, "title": p.title, "snippet": p.snippet, "score": p.score, "comments": p.comments, "link": _safe_link(p.link)}
        for p in posts
        if p.created.date() <= today
    ]


def build_page(symbol: str, company: str, today: date, hub: Any, reddit: RedditSearch, tone: Any | None = None) -> dict[str, Any]:
    """The document. `hub` has `fetch(symbol, company, since, until)`, `tone` is a callable from the news packet to the analyst's output (or None)."""
    result = hub.fetch(symbol, company, today - timedelta(days=WINDOW_DAYS), today)
    rows = news_rows(result.items, company, today)
    social: dict[str, Any] = {"enabled": reddit.enabled, "notes": [], "items": [], "by_subreddit": {}}
    if not reddit.enabled:
        social["notes"].append(OFF_NOTE)
    else:
        try:
            got = reddit.search(symbol, company)
            social["items"] = social_rows(got, today)
            social["by_subreddit"] = dict(Counter(p["subreddit"] for p in social["items"]))
        except AthenaError as exc:
            social["notes"].append(str(exc))
    reading: dict[str, Any] | None = None
    if tone is not None:
        packet = build_news_packet(symbol, company, result.items, [], today, result.notes)
        reading = tone(packet)
    return {
        "symbol": symbol, "company": company, "as_of": today.isoformat(), "window_days": WINDOW_DAYS,
        "news": {"items": rows, "notes": list(result.notes), "counts": {"all": len(rows), "used": sum(r["used"] for r in rows), "routine": sum(r["routine"] for r in rows), "press": sum(r["source"] != "nse" for r in rows)}},
        "social": social, "tone": reading, "caveat": NOT_TESTED,
    }


def _company(warehouse: Path, symbol: str) -> str:
    import duckdb

    try:
        con = duckdb.connect(str(warehouse), read_only=True)
        try:
            row = con.execute("SELECT coalesce(name, symbol) FROM universe WHERE symbol = ?", [symbol]).fetchone()
        finally:
            con.close()
        return row[0] if row else symbol
    except Exception:
        return symbol


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("symbol")
    parser.add_argument("--tone", action="store_true", help="also ask the news analyst for a reading (one model call)")
    parser.add_argument("--warehouse", default=str(Path(".athena") / "warehouse.duckdb"))
    args = parser.parse_args(argv)
    symbol = args.symbol.upper()
    if not SYMBOL_OK.match(symbol):
        print(json.dumps({"error": "bad symbol"}))
        return 0
    from athena.cli import GOOGLE_NEWS_SETTING
    from athena.llm.envfile import DEFAULT_ENV_FILE, read_setting
    from athena.news.google_news import GoogleNews
    from athena.news.hub import NewsHub, default_sources
    from athena.news.nse_announcements import NseAnnouncements
    from athena.news.publisher_feeds import PublisherFeeds

    google_on = read_setting(GOOGLE_NEWS_SETTING, DEFAULT_ENV_FILE).strip() == "1"
    hub = NewsHub(default_sources(NseAnnouncements(), PublisherFeeds(), GoogleNews(enabled=google_on)))
    reddit = RedditSearch(credentials())
    tone = None
    if args.tone:
        try:
            from athena.agents.base import Specialist
            from athena.agents.news_sentiment import NEWS_SENTIMENT
            from athena.llm.budget import Meter
            from athena.llm.cache import ReplyCache
            from athena.llm.router import build_router

            analyst = Specialist(NEWS_SENTIMENT, build_router(meter=Meter(), cache=ReplyCache()).client_for("specialist"), fail_quiet=True)
            tone = analyst.analyze
        except Exception:
            tone = lambda packet: {"signal": "neutral", "confidence": 0, "reasoning": "No reading: no model is available.", "data_coverage": "insufficient", "missing": []}  # noqa: E731
    doc = build_page(symbol, _company(Path(args.warehouse), symbol), date.today(), hub, reddit, tone)
    sys.stdout.write(json.dumps(doc, ensure_ascii=True, allow_nan=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
