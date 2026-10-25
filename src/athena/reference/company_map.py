"""Which Damodaran industry each of our companies belongs to.

His `indname.xlsx` lists 48,157 companies with an industry group; for India it carries 1,877 NSE tickers (`NSEI:TCS`) and 3,288 BSE codes. We join in three passes, strongest
first, and every row says which pass made it and how sure it is:

  nse_ticker  the NSE symbol is in his list                                   confidence 1.0
  name        the company's name, once written the same way, is in his list    confidence 0.9 (only when it names one industry)
  crosswalk   the company has an NSE industry label and most of the matched companies with that label sit in one of his industries; confidence = that share x 0.8
  similar     the company's name, with the exchange's abbreviations spelled out ("(I)" is India), shares at least 80% of its words with exactly one name in his list  confidence 0.7
  words       a word of the company's name ("hotels", "cement", "securities") that, across the companies already mapped, sits mostly in one industry; confidence = that share x 0.7.
              Tested by leaving each mapped company out and predicting it from the others: 78% right at a share of 0.6 or more, 83% at 0.7 or more
  owner       the owner said so (`python -m athena.reference company SYMBOL "Industry"`); a reload of the map never replaces it                                     confidence 1.0

A company none of the passes reaches is left out of the map and shows up in the audit. Nothing here is written by hand for a single company."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from athena.reference.sheets import read_rows

CROSSWALK_MIN_MATCHED = 5  # an NSE industry label needs this many matched companies before it is trusted
CROSSWALK_MIN_SHARE = 0.5
SIMILAR_MIN_SHARE = 0.8  # of the words in the longer name that the two names share
WORD_MIN_COMPANIES = 8  # mapped companies a name word needs before it votes
WORD_MIN_SHARE = 0.6
WORD_MIN_LENGTH = 3
WORD_CONFIDENCE = 0.7
ABBREVIATIONS = {"i": "india", "intl": "international", "inds": "industries", "ind": "industries", "mfg": "manufacturing", "engg": "engineering", "pharm": "pharma", "chem": "chemicals", "tech": "technologies", "fin": "finance", "infra": "infrastructure"}
NAME_WORDS_DROPPED = {"limited", "ltd", "the", "company", "co", "corporation", "corp", "pvt", "private", "plc", "inc"}


@dataclass(frozen=True)
class IndustryRow:
    name: str
    exchange_ticker: str
    industry: str
    country: str


@dataclass(frozen=True)
class Company:
    symbol: str
    name: str
    nse_industry: str  # '' when the exchange gave none


@dataclass(frozen=True)
class MapRow:
    symbol: str
    industry: str
    method: str
    confidence: float


def normal_name(name: str) -> str:
    """A company name written one way: lower case, the trailing "(Exchange:TICKER)" and punctuation gone, "&" as "and", and the legal suffixes (Limited, Ltd...) dropped."""
    text = re.sub(r"\([^()]*\)\s*$", "", name or "")
    text = text.lower().replace("&", " and ")
    words = [w for w in re.sub(r"[^a-z0-9]+", " ", text).split() if w not in NAME_WORDS_DROPPED]
    return " ".join(words)


def name_words(name: str) -> frozenset[str]:
    """The words of a name with the exchange's usual abbreviations spelled out, as a set."""
    return frozenset(ABBREVIATIONS.get(w, w) for w in normal_name(name).split())


def read_industry_rows(path: str | Path, country: str = "India") -> list[IndustryRow]:
    """The rows of `indname.xlsx` ('By industry' sheet) for one country."""
    rows = read_rows(path, "By industry")
    header = rows[0]
    at = {str(h).strip(): i for i, h in enumerate(header) if h is not None}
    need = ("Company Name", "Exchange:Ticker", "Industry Group", "Country")
    missing = [n for n in need if n not in at]
    if missing:
        from athena.contracts import AthenaError

        raise AthenaError(f"{Path(path).name}: the 'By industry' sheet lacks the columns {', '.join(missing)}")
    out = []
    for r in rows[1:]:
        if len(r) > max(at[n] for n in need) and r[at["Country"]] == country and r[at["Company Name"]] and r[at["Industry Group"]]:
            out.append(IndustryRow(str(r[at["Company Name"]]), str(r[at["Exchange:Ticker"]] or ""), str(r[at["Industry Group"]]), country))
    return out


def build_company_map(damodaran: Iterable[IndustryRow], companies: Sequence[Company]) -> list[MapRow]:
    rows = list(damodaran)
    by_nse = {r.exchange_ticker.split(":", 1)[1]: r.industry for r in rows if r.exchange_ticker.startswith("NSEI:")}
    names: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        key = normal_name(r.name)
        if key:
            names[key].add(r.industry)

    mapped: dict[str, MapRow] = {}
    for c in companies:
        if c.symbol in by_nse:
            mapped[c.symbol] = MapRow(c.symbol, by_nse[c.symbol], "nse_ticker", 1.0)
    for c in companies:
        if c.symbol in mapped:
            continue
        industries = names.get(normal_name(c.name), set())
        if len(industries) == 1:  # a name that two of his industries share is not a match
            mapped[c.symbol] = MapRow(c.symbol, next(iter(industries)), "name", 0.9)

    votes: dict[str, Counter[str]] = defaultdict(Counter)
    for c in companies:
        if c.nse_industry and c.symbol in mapped:
            votes[c.nse_industry][mapped[c.symbol].industry] += 1
    crosswalk: dict[str, tuple[str, float]] = {}
    for label, counter in votes.items():
        total = sum(counter.values())
        industry, count = counter.most_common(1)[0]
        if total >= CROSSWALK_MIN_MATCHED and count / total >= CROSSWALK_MIN_SHARE:
            crosswalk[label] = (industry, round(count / total * 0.8, 4))
    for c in companies:
        if c.symbol not in mapped and c.nse_industry in crosswalk:
            industry, confidence = crosswalk[c.nse_industry]
            mapped[c.symbol] = MapRow(c.symbol, industry, "crosswalk", confidence)

    listed = [(name_words(r.name), r.industry) for r in rows]
    for c in companies:
        if c.symbol in mapped:
            continue
        mine = name_words(c.name)
        if len(mine) < 2:  # one word is no basis for calling two names the same
            continue
        found = {industry for words, industry in listed if len(words) >= 2 and len(mine & words) / max(len(mine), len(words)) >= SIMILAR_MIN_SHARE}
        if len(found) == 1:
            mapped[c.symbol] = MapRow(c.symbol, next(iter(found)), "similar", 0.7)

    mapped.update(_by_name_words(companies, mapped))
    return [mapped[c.symbol] for c in companies if c.symbol in mapped]


def _words_of(name: str) -> set[str]:
    return {w for w in normal_name(name).split() if len(w) >= WORD_MIN_LENGTH}


def _by_name_words(companies: Sequence[Company], mapped: Mapping[str, MapRow]) -> dict[str, MapRow]:
    """The last pass: each word counts the industries of the mapped companies whose names contain it, and a company still unmapped takes the industry of its most one-sided word. The words and
    their industries are learned from the map itself, so nothing is listed by hand; a word needs `WORD_MIN_COMPANIES` mapped companies and `WORD_MIN_SHARE` of them in one industry."""
    by_word: dict[str, Counter[str]] = defaultdict(Counter)
    for c in companies:
        if c.symbol in mapped:
            for word in _words_of(c.name):
                by_word[word][mapped[c.symbol].industry] += 1
    found: dict[str, MapRow] = {}
    for c in companies:
        if c.symbol in mapped:
            continue
        best: tuple[float, str] | None = None
        for word in _words_of(c.name):
            counter = by_word.get(word)
            total = sum(counter.values()) if counter else 0
            if not counter or total < WORD_MIN_COMPANIES:
                continue
            industry, count = counter.most_common(1)[0]
            share = count / total
            if share >= WORD_MIN_SHARE and (best is None or share > best[0]):
                best = (share, industry)
        if best:
            found[c.symbol] = MapRow(c.symbol, best[1], "words", round(best[0] * WORD_CONFIDENCE, 4))
    return found


def audit(companies: Sequence[Company], mapped: Sequence[MapRow]) -> dict[str, object]:
    """How far the map reaches: the count by pass, the unmapped companies and the NSE labels that have no crosswalk."""
    done = {m.symbol for m in mapped}
    left = [c for c in companies if c.symbol not in done]
    return {
        "companies": len(companies),
        "mapped": len(done),
        "by_method": dict(Counter(m.method for m in mapped)),
        "unmapped": len(left),
        "unmapped_with_nse_label": len([c for c in left if c.nse_industry]),
        "unmapped_labels": dict(Counter(c.nse_industry for c in left if c.nse_industry).most_common(12)),
        "low_confidence": len([m for m in mapped if m.confidence < 0.7]),
    }


def as_rows(mapped: Sequence[MapRow], as_of: date) -> list[tuple[str, str, str, float, date]]:
    return [(m.symbol, m.industry, m.method, m.confidence, as_of) for m in mapped]


def labels_from(index_members: Mapping[str, str], company_labels: Mapping[str, str]) -> dict[str, str]:
    """One NSE industry label per symbol: the fundamentals store's when it has one, else the constituent files'."""
    out = {s: v for s, v in index_members.items() if v}
    out.update({s: v for s, v in company_labels.items() if v})
    return out
