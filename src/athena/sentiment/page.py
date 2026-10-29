"""The Sentiment page for one stock: what the exchange and the press reported, what people posted, and optionally a reading of the news. Display only.

    python -m athena.sentiment.page SYMBOL [--tone] [--warehouse PATH]       one JSON document on stdout

*News*: the exchange's own filings, the publishers' feeds and, only when the owner has switched it on, Google News, over the last 30 days, each marked as routine (a filing every company
files) or not. *Social*: Reddit posts that name the company, when the owner has set the app's keys (`athena.sentiment.reddit`); otherwise the reason it is off. *Tone* (`--tone`, one model
call): the news analyst's reading of the news only, never the posts. Nothing here feeds a verdict: whether the tone of news or posts says anything about later returns has not been tested,
and the page says so. Untrusted text is cut and shown, never followed."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from athena.contracts import AthenaError
from athena.evidence.conflicts import claim_view, disagreements, rumour_groups, trust
from athena.evidence.sources import BY_ID, FEED_SOURCES, STRUCTURED_SOURCES, tier_of
from athena.evidence.store import EvidenceStore, StoredItem, headline_words
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
            "date": i.published.date().isoformat(), "published": i.published.isoformat(), "publisher": i.publisher, "source": i.source, "source_id": "nse" if i.source == "nse" else i.publisher.lower().replace(" ", "_"),
            "tier": tier_of(i.publisher, i.source), "title": i.title, "link": _safe_link(i.link), "routine": routine,
            "used": not routine and (i.source == "nse" or mentions(i, names)),
        })
    return rows


_ACRONYM_SKIP = frozenset({"of", "and", "&", "the", "limited", "ltd", "ltd."})


def acronym(company: str) -> str:
    """The initials of a company's name when it has three or more significant words, in capitals (State Bank of India: SBI; Tata Consultancy Services Limited: TCS), else "".
    Headlines use these (`SBI`, `ONGC`) where the name is long. It is matched as a whole word in capitals only, so `sbi` inside a word or in lower case does not count."""
    words = [w for w in re.split(r"\s+", company.strip()) if w and w.lower() not in _ACRONYM_SKIP and w[0].isalpha()]
    letters = "".join(w[0].upper() for w in words)
    return letters if len(words) >= 3 and 3 <= len(letters) <= 6 else ""


def _names_company(news: NewsItem, names: tuple[str, ...], acro: str) -> bool:
    if mentions(news, names):
        return True
    return bool(acro) and re.search(rf"(?<![A-Za-z0-9]){acro}(?![A-Za-z0-9])", f"{news.title} {news.snippet}") is not None


def _title_key(title: str) -> frozenset[str]:
    return headline_words(title)


def evidence_rows(items: Sequence[StoredItem], names: tuple[str, ...], held: Sequence[dict[str, Any]], acro: str = "") -> list[dict[str, Any]]:
    """Stored items that name the company, as page rows with their citation (source, tier, link, published and first-seen times, story). An item already on the page from the older path is not shown twice."""
    seen = [_title_key(r["title"]) for r in held]
    rows = []
    for it in items:
        spec = BY_ID.get(it.source_id)
        news = NewsItem(it.title, it.publisher, it.published_at, it.link, "regulator" if spec and spec.kind == "regulator" else "publisher feed", it.snippet)
        if not _names_company(news, names, acro):
            continue
        words = _title_key(it.title)
        if any(words == s or (len(words) >= 3 and len(words & s) / len(words | s) >= 0.7) for s in seen):
            continue
        seen.append(words)
        rows.append({
            "date": it.published_at.date().isoformat(), "published": it.published_at.isoformat(), "first_seen": it.first_seen_at.isoformat(), "publisher": it.publisher, "source": news.source,
            "source_id": it.source_id, "tier": spec.tier if spec else "C", "title": it.title, "link": _safe_link(it.link), "routine": False, "used": True, "story_id": it.story_id, "item_id": it.item_id,
        })
    return rows


def market_rows(items: Sequence[StoredItem], limit: int = 20) -> list[dict[str, Any]]:
    """Regulator and policy items (SEBI, RBI), newest first, for the Market and policy list: they are about the market, so they are not matched to a company."""
    out = []
    for it in items:
        spec = BY_ID.get(it.source_id)
        if spec is None or spec.scope != "market":
            continue
        out.append({"date": it.published_at.date().isoformat(), "published": it.published_at.isoformat(), "first_seen": it.first_seen_at.isoformat(), "publisher": it.publisher, "source_id": it.source_id,
                    "tier": spec.tier, "title": it.title, "link": _safe_link(it.link), "story_id": it.story_id})
        if len(out) >= limit:
            break
    return out


def source_rows(health: dict[str, dict[str, Any]], shown: Sequence[dict[str, Any]], record: dict[str, dict[str, int]] | None = None) -> list[dict[str, Any]]:
    """Every source in the registry with how it is doing, so the page can say which sources were read, which are off and why, and how many of this stock's rows each gave. `record` is each source's
    settled claims (`EvidenceStore.source_record`); with it a source also carries its trust: its tier's prior, moved toward its own record."""
    per = Counter(r.get("source_id") for r in shown)
    record = record or {}
    out = []
    for s in FEED_SOURCES:
        h = health.get(s.id, {})
        status = "off" if not s.active else h.get("status", "never tried")
        rec = record.get(s.id, {})
        out.append({
            "id": s.id, "name": s.name, "kind": s.kind, "tier": s.tier, "scope": s.scope, "status": status, "reason": s.off_reason or h.get("error") or "", "held": h.get("items_held", 0),
            "newest": h["newest_item"].isoformat() if h.get("newest_item") else "", "last_ok": h["last_ok"].isoformat() if h.get("last_ok") else "", "rows_here": per.get(s.id, 0),
            "trust": trust(s.tier, rec.get("confirmed", 0), rec.get("partly", 0), rec.get("contradicted", 0)),
        })
    for s in STRUCTURED_SOURCES:
        h = health.get(s.id, {})
        out.append({
            "id": s.id, "name": s.name, "kind": s.kind, "tier": s.tier, "scope": s.scope, "status": h.get("status", "never tried"), "reason": h.get("error") or "", "held": h.get("items_held", 0),
            "newest": h["newest_item"].isoformat() if h.get("newest_item") else "", "last_ok": h["last_ok"].isoformat() if h.get("last_ok") else "", "rows_here": 0,
        })
    out.append({"id": "nse", "name": "NSE announcements", "kind": "filing", "tier": "A", "scope": "company", "status": "ok", "reason": "", "held": 0, "newest": "", "last_ok": "", "rows_here": per.get("nse", 0)})
    return out


INSIDER_DAYS = 180
PROMOTER_CATEGORIES = ("Promoters", "Promoter Group")


def _money(value: Any) -> float | None:
    return round(value, 2) if isinstance(value, (int, float)) else None


def structured_boxes(evidence: EvidenceStore, symbol: str, today: date) -> dict[str, Any]:
    """The Flows and insiders box: the stock's insider filings (last 180 days) with a count by direction for promoters, its board meetings (30 days back to 60 ahead), and the last ten days of
    FII/DII flows. Counts are made here in code. `insider_reach` is when the newest insider filing held was filed, so a quiet box is not mistaken for a quiet company."""
    trades = evidence.insider_between(today - timedelta(days=INSIDER_DAYS), today, symbol, limit=200)
    insiders = [{
        "filed": t["filed_at"].replace(tzinfo=timezone.utc).isoformat(), "first_seen": t["first_seen_at"].replace(tzinfo=timezone.utc).isoformat(), "person": t["person"], "category": t["category"], "txn": t["txn"], "mode": t["mode"],
        "security": t["security"], "shares": _money(t["shares"]), "value": _money(t["value"]), "after_pct": _money(t["after_pct"]), "from": t["trade_from"].isoformat() if t["trade_from"] else "", "link": _safe_link(t["link"] or ""),
    } for t in trades[:25]]
    promoter = Counter(t["txn"] for t in trades if t["category"] in PROMOTER_CATEGORIES)
    meetings = evidence.board_for(symbol, today - timedelta(days=30), today + timedelta(days=60))
    board = [{
        "date": m["meeting_date"].isoformat(), "purpose": m["purpose"], "description": m["description"], "announced": m["announced_at"].replace(tzinfo=timezone.utc).isoformat(), "link": _safe_link(m["link"] or ""),
    } for m in meetings]
    flows: dict[str, dict[str, Any]] = {}
    for f in evidence.flows_between(today - timedelta(days=14), today):
        flows.setdefault(f["day"].isoformat(), {"day": f["day"].isoformat()})[f["category"]] = _money(f["net"])
    newest = evidence.insider_newest()
    return {
        "insiders": insiders, "insider_counts": {"Buy": promoter.get("Buy", 0), "Sell": promoter.get("Sell", 0), "Pledge": promoter.get("Pledge", 0), "Pledge Revoke": promoter.get("Pledge Revoke", 0)},
        "insider_reach": newest.isoformat() if newest else "", "board": board, "flows": list(flows.values())[:10],
    }


CLAIM_DAYS = 30
SHOWN = 20


def claims_box(evidence: EvidenceStore, symbol: str, today: date) -> dict[str, Any]:
    """The Checked claims box: what the headlines said that our own data can be asked about, with what it said. Contradicted and partly-right claims come first and stay visible; two sources that
    point opposite ways are shown side by side; a report we cannot check is shown with the number of independent stories behind it, never as confirmed. Counts are made here in code."""
    end = datetime.combine(today, datetime.max.time(), tzinfo=timezone.utc)
    start = end - timedelta(days=CLAIM_DAYS)
    tiers = {s.id: s.tier for s in FEED_SOURCES}
    seen: set[tuple[str, str, str]] = set()
    rows = []
    for r in sorted(evidence.claims_for(symbol, start, end), key=lambda r: r["published_at"]):  # copies of one story in several feeds are one claim: the earliest is shown
        key = (r["story_id"] or r["claim_id"], r["kind"], r["text"])
        if key not in seen:
            seen.add(key)
            rows.append(r)
    rows.reverse()
    checked = [r for r in rows if r["kind"] != "rumour"]
    counts = Counter(r["outcome"] for r in checked)
    return {
        "conflicts": [claim_view(r, tiers) for r in checked if r["outcome"] in ("contradicted", "partly")][:SHOWN],
        "disagreements": disagreements(rows, tiers)[:SHOWN],
        "confirmed": [claim_view(r, tiers) for r in checked if r["outcome"] == "confirmed"][:SHOWN],
        "unconfirmed": rumour_groups(rows, tiers)[:SHOWN],
        "market": [claim_view({**r, "title": ""} | {"first_seen_at": r["published_at"]}, tiers) for r in evidence.market_claims(start, end)][:SHOWN],
        "counts": {k: counts.get(k, 0) for k in ("confirmed", "partly", "contradicted", "pending", "unverified", "not_checkable")},
        "by_item": {r["item_id"]: {"outcome": r["outcome"], "kind": r["kind"], "detail": r["detail"]} for r in checked},
        "method": "A claim is read from a headline by fixed patterns (a price move, a quarterly profit or sales figure, a promoter's buying or selling, a board-meeting date, an institution's net flow) and compared by a program with our own prices, filed figures and the exchange's filings. Reports of talks or plans cannot be checked here.",
    }


def social_rows(posts: Sequence[SocialPost], today: date) -> list[dict[str, Any]]:
    return [
        {"date": p.created.date().isoformat(), "subreddit": p.subreddit, "title": p.title, "snippet": p.snippet, "score": p.score, "comments": p.comments, "link": _safe_link(p.link)}
        for p in posts
        if p.created.date() <= today
    ]


def build_page(symbol: str, company: str, today: date, hub: Any, reddit: RedditSearch, tone: Any | None = None, evidence: EvidenceStore | None = None) -> dict[str, Any]:
    """The document. `hub` has `fetch(symbol, company, since, until)`, `tone` is a callable from the news packet to the analyst's output (or None)."""
    result = hub.fetch(symbol, company, today - timedelta(days=WINDOW_DAYS), today)
    rows = news_rows(result.items, company, today)
    market: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    boxes: dict[str, Any] = {"insiders": [], "insider_counts": {}, "insider_reach": "", "board": [], "flows": []}
    claims: dict[str, Any] = {"conflicts": [], "disagreements": [], "confirmed": [], "unconfirmed": [], "market": [], "counts": {}, "method": ""}
    notes = list(result.notes)
    if evidence is not None:
        try:
            start = datetime.combine(today - timedelta(days=WINDOW_DAYS), datetime.min.time(), tzinfo=timezone.utc)
            stored = evidence.items_between(start, datetime.combine(today, datetime.max.time(), tzinfo=timezone.utc))
            health = evidence.health()
            if any(h.get("status") == "ok" for h in health.values()):  # the collector has been reading the same feeds, and keeps them: press comes from the store, so each row has a source a chip can name
                rows = [r for r in rows if r["source"] != "publisher feed"]
            rows = sorted(rows + evidence_rows(stored, loose_names(company_names(company)), rows, acronym(company)), key=lambda r: r["published"], reverse=True)
            market = market_rows(stored)
            sources = source_rows(health, rows, evidence.source_record())
            boxes = structured_boxes(evidence, symbol, today)
            claims = claims_box(evidence, symbol, today)
            by_item = claims.pop("by_item")
            for r in rows:
                if r.get("item_id") in by_item:
                    r["claim"] = by_item[r["item_id"]]
        except Exception as exc:  # noqa: BLE001 - the stored sources are an addition: the page still shows what the live path found
            notes.append(f"stored sources unavailable: {type(exc).__name__}")
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
        "news": {"items": rows, "notes": notes, "counts": {"all": len(rows), "used": sum(r["used"] for r in rows), "routine": sum(r["routine"] for r in rows), "press": sum(r["source"] != "nse" for r in rows)}},
        "market": market, "sources": sources, "filings": boxes, "claims": claims, "social": social, "tone": reading, "caveat": NOT_TESTED,
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
    from athena.evidence.store import DEFAULT_PATH as EVIDENCE_PATH

    evidence = EvidenceStore(EVIDENCE_PATH, wait_seconds=5.0, read_only=True) if EVIDENCE_PATH.is_file() else None
    doc = build_page(symbol, _company(Path(args.warehouse), symbol), date.today(), hub, reddit, tone, evidence)
    sys.stdout.write(json.dumps(doc, ensure_ascii=True, allow_nan=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
