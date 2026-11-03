"""The IPO tab's document: what is open, coming, closed and waiting to list, recently listed, and what has been filed with SEBI, from the store `athena.ipo.collect` keeps.

    python -m athena.ipo.page [--warehouse PATH]       one JSON document on stdout

Facts only. A listed issue's gain is worked out here from the issue price and our own price bars (listing price against issue price, the first close, and the latest close), never by a model; a figure
we cannot work out is null, not zero. A draft filed with SEBI is matched to an issue by company name; one with no issue yet is listed on its own. The page carries no view on whether to apply: it is
display only, for the owner, not advice."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from athena.ipo.sources import normal_name
from athena.ipo.store import DEFAULT_PATH, IpoStore

LISTED_DAYS = 60
FILED_DAYS = 120
ISSUE_DAYS = 14  # an issue that closed this long ago and has not listed is no longer shown
NSE_PAGE = "https://www.nseindia.com/market-data/all-upcoming-issues-ipo"
CAVEAT = "Facts from the exchange and SEBI, display only. Nothing here says whether to apply for an issue; read the offer document before deciding. Not investment advice."
_LEVEL = re.compile(r"^\d+(\.\d+)?$")
SOURCE_NAMES = {"nse_upcoming": "NSE issue calendar", "nse_subscription": "NSE subscription", "nse_listed": "NSE issues that listed", "sebi_drafts": "SEBI draft offer documents"}


def _safe(link: str | None) -> str:
    return link if link and link.startswith("https://") else ""


def same_company(a: str, b: str) -> bool:
    """Two names for one company: equal once Limited and punctuation are gone, or one is the other plus extra words at the end (`Oravel Stays` and `Oravel Stays India`), with at least two words in common."""
    x, y = normal_name(a), normal_name(b)
    if not x or not y:
        return False
    if x == y:
        return True
    short, long_ = (x, y) if len(x) <= len(y) else (y, x)
    return len(short.split()) >= 2 and long_.startswith(short + " ")


def filing_view(f: dict[str, Any]) -> dict[str, Any]:
    return {"company": f["company"], "kind": f["kind"], "filed": f["filed"].isoformat(), "title": f["title"], "page": _safe(f["url"]), "pdf": _safe(f["pdf"])}


def subscription_view(sub: dict[str, Any] | None) -> dict[str, Any] | None:
    if not sub:
        return None
    rows = [{"category": r["category"], "offered": r["offered"], "bid": r["bid"], "times": r["times"], "level": 0 if "." not in r["sr"] else 1} for r in sub["rows"] if _LEVEL.match(r["sr"] or "")]
    total = next((r["times"] for r in sub["rows"] if r["category"].strip().lower() == "total"), None)
    return {"taken_at": sub["taken_at"].isoformat(), "snapshots": sub["snapshots"], "total_times": total, "rows": rows}


def issue_view(i: dict[str, Any], sub: dict[str, Any] | None, filings: Sequence[dict[str, Any]]) -> dict[str, Any]:
    high = i["price_high"]
    return {
        "symbol": i["symbol"], "name": i["name"], "kind": i["kind"], "status": i["status"], "start": i["start"].isoformat(), "end": i["end"].isoformat(), "price_low": i["price_low"], "price_high": high,
        "shares": i["shares"], "lot": i["lot"], "size_cr": round(i["shares"] * high / 1e7, 1) if i["shares"] and high else None,  # shares times the upper price: the most the issue can raise, an estimate
        "min_application": round(i["lot"] * high) if i["lot"] and high else None, "subscription": subscription_view(sub), "filings": [filing_view(f) for f in filings], "nse_page": NSE_PAGE,
    }


def listed_view(row: dict[str, Any], bars: Sequence[tuple[date, float, float]], filings: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """`bars` are `(day, open, close)` from the listing day on. Percentages are against the issue price; all of it is null when the issue price or the bars are missing."""
    price = row["issue_price"]
    first = bars[0] if bars and row["listing"] and bars[0][0] == row["listing"] else None
    pct = lambda v: round((v / price - 1) * 100, 1) if price and v else None  # noqa: E731
    return {
        "symbol": row["symbol"], "name": row["name"], "kind": "sme" if row["series"] == "SME" else "mainboard", "issue_price": price, "band_high": row["band_high"], "listing": row["listing"].isoformat() if row["listing"] else "",
        "listing_open": first[1] if first else None, "listing_pct": pct(first[1]) if first else None, "first_close": first[2] if first else None, "first_close_pct": pct(first[2]) if first else None,
        "last_close": bars[-1][2] if bars else None, "last_pct": pct(bars[-1][2]) if bars else None, "filings": [filing_view(f) for f in filings],
    }


def _bars(warehouse: Path | None, symbol: str, since: date, until: date) -> list[tuple[date, float, float]]:
    if warehouse is None:
        return []
    try:
        from athena.warehouse.warehouse import Warehouse

        with Warehouse(warehouse, read_only=True, wait_seconds=5) as wh:
            return [((b.timestamp + timedelta(hours=5, minutes=30)).date(), b.open, b.close) for b in wh.bars(symbol, since, until)]
    except Exception:  # noqa: BLE001 - a missing price is a missing price
        return []


def build_page(store: IpoStore, today: date, warehouse: Path | None = None) -> dict[str, Any]:
    all_filings = store.filings(today - timedelta(days=FILED_DAYS))
    held = store.issues(today - timedelta(days=ISSUE_DAYS))
    listed_rows = store.listed(today - timedelta(days=LISTED_DAYS))
    listed_symbols = {r["symbol"] for r in listed_rows}
    matched: set[str] = set()

    def filings_of(name: str) -> list[dict[str, Any]]:
        got = [f for f in all_filings if same_company(f["company"], name)]
        matched.update(f["url"] for f in got)
        return got

    open_, upcoming, closed = [], [], []
    for i in held:
        if i["symbol"] in listed_symbols:
            continue  # it has listed: shown with the listed
        view = issue_view(i, store.subscription(i["symbol"]), filings_of(i["name"]))
        (open_ if i["status"] == "Active" else upcoming if i["status"] == "Forthcoming" else closed).append(view)
    listed = []
    for r in listed_rows:
        bars = _bars(warehouse, r["symbol"], r["listing"], today) if r["listing"] else []
        listed.append(listed_view(r, bars, filings_of(r["name"])))
    health = store.health()
    sources = [{"id": sid, "name": name, "status": health.get(sid, {}).get("status", "never tried"), "reason": health.get(sid, {}).get("error", ""),
                "last_ok": health[sid]["last_ok"].replace(tzinfo=timezone.utc).isoformat() if health.get(sid, {}).get("last_ok") else ""} for sid, name in SOURCE_NAMES.items()]
    return {
        "as_of": today.isoformat(), "open": open_, "upcoming": upcoming, "closed": closed, "listed": listed,
        "filed": [filing_view(f) for f in all_filings if f["url"] not in matched], "sources": sources, "caveat": CAVEAT,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--warehouse", default=str(Path(".athena") / "warehouse.duckdb"))
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    if not Path(args.store).is_file():
        doc = {"as_of": date.today().isoformat(), "open": [], "upcoming": [], "closed": [], "listed": [], "filed": [], "sources": [], "caveat": CAVEAT, "empty": True}
    else:
        doc = build_page(IpoStore(args.store, wait_seconds=5.0, read_only=True), datetime.now(timezone.utc).astimezone().date(), Path(args.warehouse))
    sys.stdout.write(json.dumps(doc, ensure_ascii=True, allow_nan=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
