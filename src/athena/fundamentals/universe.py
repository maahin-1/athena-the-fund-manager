"""Which companies to ingest, in the order that makes the first hour useful: Nifty 50, the rest of Nifty 500, then every other equity-series name."""
from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass

import requests

from athena.price_integrity.nse_actions import HEADERS, TIMEOUT

NIFTY50_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"
NIFTY500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
EQUITY_SERIES = ("EQ", "BE", "BZ")


@dataclass(frozen=True)
class Member:
    symbol: str
    name: str
    industry: str
    isin: str


def _rows(text: str) -> list[dict[str, str]]:
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(io.StringIO(text))]


def parse_index_csv(text: str) -> list[Member]:
    return [Member(r["Symbol"].upper(), r["Company Name"], r.get("Industry", ""), r.get("ISIN Code", "")) for r in _rows(text) if r.get("Symbol")]


def parse_equity_master(text: str) -> list[Member]:
    return [
        Member(r["SYMBOL"].upper(), r.get("NAME OF COMPANY", ""), "", r.get("ISIN NUMBER", ""))
        for r in _rows(text)
        if r.get("SYMBOL") and r.get("SERIES") in EQUITY_SERIES
    ]


def ordered(nifty50: list[Member], nifty500: list[Member], equities: list[Member]) -> list[Member]:
    seen: set[str] = set()
    out: list[Member] = []
    for group in (nifty50, nifty500, equities):
        for member in group:
            if member.symbol not in seen:
                seen.add(member.symbol)
                out.append(member)
    return out


def fetch_text(url: str, get: Callable = requests.get) -> str:
    response = get(url, headers=HEADERS, timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"{url} answered HTTP {response.status_code}")
    return response.text


def universe(name: str, get: Callable = requests.get) -> list[Member]:
    """`nifty50`, `nifty500`, `all`, or a comma-separated list of symbols."""
    key = name.strip().lower()
    if key == "nifty50":
        return parse_index_csv(fetch_text(NIFTY50_URL, get))
    if key == "nifty500":
        return ordered(parse_index_csv(fetch_text(NIFTY50_URL, get)), parse_index_csv(fetch_text(NIFTY500_URL, get)), [])
    if key == "all":
        return ordered(
            parse_index_csv(fetch_text(NIFTY50_URL, get)), parse_index_csv(fetch_text(NIFTY500_URL, get)), parse_equity_master(fetch_text(EQUITY_URL, get))
        )
    return [Member(s.strip().upper(), "", "", "") for s in name.split(",") if s.strip()]
