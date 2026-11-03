"""Reading the public issue data: the exchange's IPO calendar, its subscription by investor category, its list of issues that have listed, and SEBI's list of draft offer documents.

Every parser takes what the endpoint returned and gives plain records; a row that cannot be read (no symbol, no date) is skipped rather than guessed. Nothing here names a company.
Measured on 2026-10-29: the exchange's calendar returned 8 rows (main board `EQ` and `SME`, status `Forthcoming`, `Active` or `Closed`), the issue size is a number of SHARES (not rupees), the SME rows
carry a lot size, the current-issue detail gives subscription by category, and the past-issues list returned 59 issues for two months. SEBI's listing returned 25 filings with a page for each."""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import unquote

from athena.contracts import AthenaError

SEBI_LISTING = "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=3&ssid=15&smid=10"
NSE_UPCOMING = "https://www.nseindia.com/api/all-upcoming-issues"
NSE_CURRENT = "https://www.nseindia.com/api/ipo-current-issue"
NSE_DETAIL = "https://www.nseindia.com/api/ipo-detail"
NSE_PAST = "https://www.nseindia.com/api/public-past-issues"
STATUSES = ("Forthcoming", "Active", "Closed")


@dataclass(frozen=True)
class Issue:
    symbol: str
    name: str
    series: str  # EQ | SME
    kind: str  # mainboard | sme
    status: str
    start: date
    end: date
    price_low: float | None
    price_high: float | None
    shares: float | None  # the issue size in shares
    lot: int | None


@dataclass(frozen=True)
class Bid:
    sr: str
    category: str
    offered: float | None
    bid: float | None
    times: float | None  # subscribed this many times


@dataclass(frozen=True)
class Listed:
    symbol: str
    name: str
    series: str
    start: date | None
    end: date | None
    issue_price: float | None
    band_low: float | None
    band_high: float | None
    listing: date | None


@dataclass(frozen=True)
class Filing:
    url: str  # the SEBI page for the filing
    company: str
    kind: str  # DRHP | UDRHP | RHP | Prospectus | Addendum | Notice | Other
    filed: date
    title: str
    pdfs: tuple[str, ...]  # documents named in the listing itself


def _num(value: Any) -> float | None:
    try:
        text = str(value).strip().replace(",", "")
        return None if text in ("", "-", "None") else float(text)
    except ValueError:
        return None


def _day(text: Any) -> date | None:
    for form in ("%d-%b-%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(str(text).strip(), form).date()
        except ValueError:
            continue
    return None


def price_band(text: Any) -> tuple[float | None, float | None]:
    """`Rs.78 to Rs.82` -> (78, 82); one price -> (p, p); nothing readable -> (None, None). A band whose low is above its high is read as unreadable."""
    found = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*\.?\d*", str(text or ""))]
    if not found:
        return None, None
    low, high = found[0], found[-1]
    return (low, high) if low <= high else (None, None)


def _rows(payload: Any) -> list[dict[str, Any]]:
    rows = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else None
    if rows is None and isinstance(payload, dict) and not payload:
        return []  # the exchange answers `{}` for a category with nothing in it
    if not isinstance(rows, list):
        raise AthenaError("unexpected reply shape")
    return [r for r in rows if isinstance(r, dict)]


def parse_issues(payload: Any) -> list[Issue]:
    out = []
    for r in _rows(payload):
        symbol, start, end = str(r.get("symbol") or "").strip().upper(), _day(r.get("issueStartDate")), _day(r.get("issueEndDate"))
        if not symbol or start is None or end is None:
            continue
        low, high = price_band(r.get("priceBand") or r.get("issuePrice"))
        series = str(r.get("series") or "EQ").strip().upper()
        lot = _num(r.get("lotSize"))
        status = str(r.get("status") or "").strip().title()
        out.append(Issue(symbol, str(r.get("companyName") or symbol).strip(), series, "sme" if series == "SME" else "mainboard", status if status in STATUSES else "Forthcoming", start, end, low, high, _num(r.get("issueSize")), int(lot) if lot else None))
    return out


def parse_subscription(payload: Any) -> list[Bid]:
    """The exchange's bid detail: one row per investor category, as printed (`1(a)`, `2.1`...), with the shares offered, the shares bid for and the number of times subscribed."""
    rows = payload.get("bidDetails") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise AthenaError("unexpected reply shape")
    return [Bid(str(r.get("srNo") or ""), str(r.get("category") or "").strip(), _num(r.get("noOfSharesOffered")), _num(r.get("noOfsharesBid")), _num(r.get("noOfTime"))) for r in rows if isinstance(r, dict) and r.get("category")]


def parse_past(payload: Any) -> list[Listed]:
    out = []
    for r in _rows(payload):
        symbol = str(r.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        low, high = price_band(r.get("priceRange"))
        out.append(Listed(symbol, str(r.get("company") or symbol).strip(), str(r.get("securityType") or "EQ").strip().upper(), _day(r.get("ipoStartDate")), _day(r.get("ipoEndDate")), _num(r.get("issuePrice")), low, high, _day(r.get("listingDate"))))
    return out


_KINDS = (("udrhp", "UDRHP"), ("addendum", "Addendum"), ("corrigendum", "Addendum"), ("drhp", "DRHP"), ("rhp", "RHP"), ("prospectus", "Prospectus"), ("notice", "Notice"))
_ROW = re.compile(r"<td>\s*([A-Z][a-z]{2} \d{1,2}, \d{4})\s*</td>\s*<td>\s*<a href=\"([^\"]+)\"[^>]*?title=\"(.*?)\"\s+class=\"points\"", re.S)
_TITLE_PDF = re.compile(r"href='([^']+\.pdf)'", re.I)
_COMPANY_END = re.compile(r"^(.*?\b(?:Limited|Ltd\.?|LLP|Private Limited))\b", re.I)


def filing_kind(title: str) -> str:
    low = title.lower()
    return next((label for key, label in _KINDS if re.search(rf"\b{key}", low)), "Other")


def company_of(title: str) -> str:
    """The company in a filing's title (`Oravel Stays Limited - Addendum to DRHP` -> `Oravel Stays Limited`): up to the first Limited or Ltd, else up to the first dash."""
    head = re.split(r"<br", title, maxsplit=1)[0].strip()
    m = _COMPANY_END.match(head)
    if m:
        return m.group(1).strip(" -")
    return re.split(r"\s*[-–]\s*", head, maxsplit=1)[0].strip()


def parse_sebi(page: str | bytes) -> list[Filing]:
    """The listing page's rows: filing date, the SEBI page, the title, and any document linked in the title. A page with no row at all is a failure (the site changed or refused us)."""
    text = page.decode("utf-8", "replace") if isinstance(page, bytes) else page
    out = []
    for when, url, raw in _ROW.findall(text):
        filed = _day(when)
        if filed is None or not url.startswith("https://"):
            continue
        title = html.unescape(re.sub(r"<[^>]+>", " ", raw)).strip()
        title = " ".join(title.split())
        pdfs = tuple(html.unescape(p) for p in _TITLE_PDF.findall(raw) if p.startswith("https://"))
        out.append(Filing(html.unescape(url), company_of(title), filing_kind(title), filed, title, pdfs))
    if not out:
        raise AthenaError("no filing could be read from the SEBI listing")
    return out


def pdf_of_filing_page(page: str | bytes) -> str:
    """The document a SEBI filing page shows: its viewer takes the PDF as `?file=<url>`. Empty when the page names none."""
    text = page.decode("utf-8", "replace") if isinstance(page, bytes) else page
    m = re.search(r"file=(https://[^'\"&\s]+?\.pdf)", text, re.I) or re.search(r"(?:src|href)=['\"](https://[^'\"]+\.pdf)['\"]", text, re.I)
    return unquote(m.group(1)) if m else ""


def normal_name(name: str) -> str:
    """A company name reduced for matching a filing to an issue: lower case, no Limited, no punctuation."""
    low = re.sub(r"\b(limited|ltd|private|pvt)\b", " ", name.lower().replace("&", " and "))
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", low).split())


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)
