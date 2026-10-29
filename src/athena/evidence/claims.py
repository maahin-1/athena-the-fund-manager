"""Claims: the statements in a headline that our own data can be asked about.

Only a statement with a number, a date or an action that a filing or a price bar would show is pulled out, by patterns, never by a model, and only from the title. The patterns are narrow on purpose:
a claim that is missed costs nothing (the item is still shown), a claim that is invented would be checked and counted against a source that never said it. So a price move is taken only when the
company's name comes straight before the verb (`Infosys shares jump 5%`, not `Nifty rises 1%, Infosys leads`), a profit figure only for a quarter, an insider action only after the word promoter.

    price_move     {direction: +1|-1, pct}                       checked against the day's bar
    results        {metric: net_profit|sales, direction, pct, amount_cr, basis: yoy|qoq}   checked against the filed statements
    insider        {action: Buy|Sell|Pledge|Pledge Revoke}       checked against the exchange's insider filings
    board_meeting  {date}                                         checked against the exchange's board-meeting notices
    flow           {category: FII/FPI|DII, direction, amount_cr}  market-wide; checked against the daily flows
    rumour         {}                                             cannot be checked here: counted by how many independent stories say it

Nothing here decides who is right. `athena.evidence.checks` compares a claim with the data."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from athena.news.model import company_names, loose_names

IST = timezone(timedelta(hours=5, minutes=30))
SHOWN_TEXT = 200
KINDS = ("price_move", "results", "insider", "board_meeting", "flow", "rumour")

_UP = r"(?:surg\w*|jump\w*|rall(?:y|ies|ied)|soar\w*|gain\w*|ris(?:e|es|ing)|rose|climb\w*|spurt\w*|zoom\w*|advanc\w*|leap\w*|add(?:s|ed)?|up)"
_DOWN = r"(?:fall\w*|fell|drop\w*|slump\w*|tumbl\w*|plung\w*|sink\w*|sank|slid\w*|slide\w*|declin\w*|dip\w*|crash\w*|tank\w*|shed\w*|lose\w*|lost|slip\w*|sag\w*|skid\w*|down)"
_PCT = r"(\d+(?:\.\d+)?)\s?(?:%|per\s?cent|percent)"
_APPROX = r"(?:\s+(?:by|over|nearly|about|around|almost|more than|up to|as much as|close to|as high as))*"
_PRICE_GAP_OK = frozenset({"shares", "share", "stock", "stocks", "scrip", "price", "prices", "ltd", "limited", "today", "intraday", "early", "trade", "trading"})
_PRICE_NOUNS = frozenset({"shares", "share", "stock", "stocks", "scrip", "price", "prices"})
_UPDOWN_ONLY_WITH_NOUN = frozenset({"up", "down"})

_SPAN = re.compile(r"\b(?:weeks?|months?|days?|sessions?|years?|ytd|so far|since|in\s+\d+)\b", re.I)  # a move over a stretch of time is not one day's bar
_PRICE = re.compile(rf"^[\s,]*(?P<gap>(?:[A-Za-z]+\s+){{0,3}}?)(?P<verb>{_UP}|{_DOWN})\b{_APPROX}\s+{_PCT}", re.I)

_PROFIT = r"(?:consolidated\s+|standalone\s+)?(?:net\s+profit|profit\s+after\s+tax|pat|net\s+income|profit)"
_SALES = r"(?:consolidated\s+|standalone\s+)?(?:net\s+sales|revenue(?:\s+from\s+operations)?|sales|topline)"
_METRIC = rf"(?P<metric>{_PROFIT}|{_SALES})"
_RISE = r"(?:ris(?:es|e|ing)|rose|jump\w*|surg\w*|grow\w*|climb\w*|up|increas\w*|gain\w*|soar\w*|spurt\w*|improv\w*|expand\w*|growth|advanc\w*)"
_FALL = r"(?:fall\w*|fell|declin\w*|drop\w*|dip\w*|down|slump\w*|plung\w*|shrink\w*|shrank|decreas\w*|slid\w*|tumbl\w*|contract\w*|decline)"
_RESULTS_A = re.compile(rf"\b{_METRIC}\s+(?:\w+\s+){{0,2}}?(?P<verb>{_RISE}|{_FALL})\b{_APPROX}\s+{_PCT}", re.I)
_RESULTS_B = re.compile(rf"{_PCT}\s+(?P<verb>{_RISE}|{_FALL})\s+in\s+(?:\w+[\s-]+){{0,3}}?{_METRIC}", re.I)
_FORWARD_GAP = frozenset({"may", "might", "could", "should", "will", "would", "likely", "expected", "seen", "set", "to", "poised", "tipped"})
_PREVIEW = re.compile(r"\b(?:preview|ahead of|poll|what to expect|expectations? (?:for|from))\b", re.I)
_NOT_QUARTER = re.compile(r"\b(?:fy\s?\d{2,4}|h[12]|full[- ]year|annual|half[- ]year|six[- ]month|nine[- ]month|9m|12m|year ended|yearly|cumulative)\b", re.I)
_QOQ = re.compile(r"\b(?:qoq|q-o-q|sequential\w*|quarter[- ]on[- ]quarter|from (?:the )?previous quarter)\b", re.I)
_AMOUNT = re.compile(r"(?:\bto|\bat|\bof|\bstands at|\bcame in at|\bat a)\s+(?:rs\.?|₹|inr)\s?(?P<amt>[\d,]+(?:\.\d+)?)\s?(?P<unit>lakh crore|crore|cr|billion|bn|million|mn)?\b", re.I)

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_MON = r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DAY_FIRST = re.compile(rf"(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}\b\.?(?:,?\s+(?P<year>\d{{4}}))?", re.I)
_MON_FIRST = re.compile(rf"{_MON}\b\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<year>\d{{4}}))?", re.I)
_BOARD = re.compile(r"\bboard\b[^.;]{0,50}?\b(?:meet\w*|meeting)\b[^.;]{0,50}", re.I)

_PROMOTER = re.compile(r"\b(?:promoters?(?:\s+group)?(?:\s+entit\w+)?|insiders?)\b", re.I)
_BUY = re.compile(r"\b(?:buy\w*|bought|acquir\w*|rais\w*\s+(?:its\s+|their\s+)?(?:stake|holding)|increas\w*|hik\w*|pick\w*\s+up|boost\w*|lift\w*|add\w*)\b", re.I)
_SELL = re.compile(r"\b(?:sell\w*|sold|offload\w*|off-load\w*|dump\w*|trim\w*|cut\w*|reduc\w*|exit\w*|pare\w*|liquidat\w*|monetis\w*|monetiz\w*|dilut\w*)\b", re.I)
_REVOKE = re.compile(r"\b(?:releas\w*|revok\w*|free\w*|cancel\w*|lift\w*)\b[^.;]{0,30}\bpledg\w*|\bpledg\w*[^.;]{0,20}\b(?:releas\w*|revok\w*|cancel\w*)", re.I)
_PLEDGE = re.compile(r"\bpledg\w*", re.I)

_FLOW_WHO = r"(?P<who>fiis?|fpis?|foreign\s+institutional\s+investors?|foreign\s+portfolio\s+investors?|diis?|domestic\s+institutional\s+investors?)"
_FLOW_BUY = r"bought|buy|purchased|net\s+buyers?|net\s+bought|pumped\s+in|infused|poured|snapped\s+up|invested"
_FLOW_SELL = r"sold|sell|offloaded|dumped|net\s+sellers?|net\s+sold|pulled\s+out|withdrew|pulled"
_FLOW = re.compile(rf"\b{_FLOW_WHO}\b(?:\s+\w+){{0,4}}?\s+(?P<verb>{_FLOW_BUY}|{_FLOW_SELL})\b[^.]{{0,40}}?(?:rs\.?|₹|inr)\s?(?P<amt>[\d,]+(?:\.\d+)?)\s?(?P<unit>lakh crore|crore|cr)\b", re.I)

_DERIVATIVES = re.compile(r"\b(?:futures?|options?|derivatives?|f&o|index|nifty|sensex|stake|shares of|block deal)\b", re.I)
_DEAL = r"(?:buy|sell|acquire|acquisition|raise|launch|merge|merger|stake|takeover|bid|divest|demerge|invest)\w*"
_RUMOUR = re.compile(rf"\b(?:in\s+(?:advanced\s+)?talks|(?:likely|plans?|mulls?|mulling|considers?|considering|looks?|eyes?|eyeing|may|could)\s+(?:to\s+)?{_DEAL}|reportedly|sources?\s+(?:said|say|told))\b", re.I)


@dataclass(frozen=True)
class Claim:
    kind: str
    symbol: str  # "" for a market-wide claim
    text: str  # the words of the title the claim was read from
    fields: dict[str, Any] = field(default_factory=dict)


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def _crore(amount: str, unit: str | None) -> float | None:
    """A rupee amount in crore. A unit that is not rupees' (a bare number) is read as crore only when stated; none given is not guessed."""
    try:
        value = _number(amount)
    except ValueError:
        return None
    unit = (unit or "").lower()
    factor = {"crore": 1.0, "cr": 1.0, "lakh crore": 1e5, "billion": 100.0, "bn": 100.0, "million": 0.1, "mn": 0.1}.get(unit)
    return value * factor if factor is not None else None


def price_claim(title: str, name_end: int) -> Claim | None:
    """`<name> [shares|stock] <verb> <n>%`: the name comes straight before the verb, with only a stock noun or an adverb between."""
    m = _PRICE.match(title[name_end:])
    if not m or _SPAN.search(title[name_end:]):
        return None
    gap = [w.lower() for w in m.group("gap").split()]
    if any(w not in _PRICE_GAP_OK and not w.endswith("ly") for w in gap):
        return None
    verb = m.group("verb").lower()
    if verb in _UPDOWN_ONLY_WITH_NOUN and not (_PRICE_NOUNS & set(gap)):
        return None  # `profit up 12%` is not a price; `shares up 5%` is
    up = re.fullmatch(_UP, verb) is not None
    pct = _number(m.group(m.lastindex))
    if pct <= 0 or pct > 60:
        return None
    return Claim("price_move", "", title[name_end:name_end + m.end()].strip(" ,"), {"direction": 1 if up else -1, "pct": pct})


def results_claim(title: str) -> Claim | None:
    """A quarterly profit or sales change with its percentage, and the rupee amount when the headline gives one. Annual, half-year and nine-month headlines are not read: they would be compared with the wrong period."""
    if _NOT_QUARTER.search(title) or _PREVIEW.search(title):
        return None
    m = _RESULTS_A.search(title)
    if m:
        between = title[m.end("metric"):m.start("verb")].lower().split()
        if _FORWARD_GAP & set(between):
            return None  # `profit may rise 52%` is a forecast
        verb, pct, metric = m.group("verb"), m.group(m.lastindex), m.group("metric")
    else:
        m = _RESULTS_B.search(title)
        if not m:
            return None
        verb, pct, metric = m.group("verb"), m.group(1), m.group("metric")
    up = re.fullmatch(_RISE, verb.lower()) is not None
    key = "net_profit" if re.search(r"profit|pat|income", metric, re.I) else "sales"
    amount = _AMOUNT.search(title)
    return Claim("results", "", m.group(0).strip(), {
        "metric": key, "direction": 1 if up else -1, "pct": _number(pct), "amount_cr": _crore(amount.group("amt"), amount.group("unit")) if amount else None, "basis": "qoq" if _QOQ.search(title) else "yoy",
    })


def insider_claim(title: str) -> Claim | None:
    """A promoter's purchase, sale or pledge: the verb must follow the word promoter within the same clause."""
    p = _PROMOTER.search(title)
    if not p:
        return None
    rest = re.split(r"[;|]", title[p.end():], maxsplit=1)[0]
    text = title[p.start():p.end() + len(rest)].strip()
    if _REVOKE.search(rest):
        action = "Pledge Revoke"
    elif _PLEDGE.search(rest):
        action = "Pledge"
    else:
        buy, sell = _BUY.search(rest), _SELL.search(rest)
        if not buy and not sell:
            return None
        action = "Buy" if buy and (not sell or buy.start() < sell.start()) else "Sell"
    return Claim("insider", "", text, {"action": action})


def _with_year(day: int, month: int, year: int | None, published: date) -> date | None:
    for y in ([year] if year else [published.year, published.year + 1]):
        try:
            candidate = date(y, month, day)
        except ValueError:
            return None
        if year or candidate >= published - timedelta(days=30):
            return candidate
    return None


def board_claim(title: str, published: date) -> Claim | None:
    """`board meeting on 5 Nov`: the date named for a board meeting. A year left out is the next one that puts the date within a month before the item."""
    b = _BOARD.search(title)
    if not b:
        return None
    seg = b.group(0)
    for pattern in (_DAY_FIRST, _MON_FIRST):
        d = pattern.search(seg)
        if d:
            when = _with_year(int(d.group("day")), _MONTHS[d.group("mon")[:3].lower()], int(d.group("year")) if d.group("year") else None, published)
            if when is not None:
                return Claim("board_meeting", "", b.group(0).strip(), {"date": when.isoformat()})
    return None


def flow_claim(title: str) -> Claim | None:
    """An institution's net buying or selling in rupees crore. Derivatives and index figures are not the cash-market flow the exchange publishes, so a title about them is left alone."""
    m = _FLOW.search(title)
    if not m or _DERIVATIVES.search(title):
        return None
    amount = _crore(m.group("amt"), m.group("unit"))
    if amount is None:
        return None
    who = m.group("who").lower()
    category = "DII" if who.startswith("d") or who.startswith("domestic") else "FII/FPI"
    sell = re.search(r"sell|sold|offload|dump|pull|withdr", m.group("verb"), re.I) is not None
    return Claim("flow", "", m.group(0).strip(), {"category": category, "direction": -1 if sell else 1, "amount_cr": amount})


def rumour_claim(title: str) -> Claim | None:
    """A report of a deal or plan that is not yet done (talks, a possible purchase or sale). A results forecast is not one."""
    m = _RUMOUR.search(title)
    if _PREVIEW.search(title):
        return None
    return Claim("rumour", "", title.strip(), {"cue": m.group(0).lower()}) if m else None


def _clause_around(title: str, start: int, end: int) -> str:
    """The part of the title between semicolons or bars that holds the company's name."""
    pos = 0
    for part in re.split(r"[;|]", title):
        if pos <= start and end <= pos + len(part):
            return part.strip()
        pos += len(part) + 1
    return title


def extract_claims(title: str, published: datetime, companies: Iterable[tuple[str, int, int]]) -> list[Claim]:
    """The claims in one title. `companies` is every company the title names, as `(symbol, start, end)`; a company claim is made only when exactly one company is named, so a claim is never given to the wrong one.
    A market-wide claim (institutional flows) does not need a company."""
    when = published.astimezone(IST).date() if published.tzinfo else published.date()
    named = {c[0]: c for c in companies}
    out: list[Claim] = []
    if not named:
        flow = flow_claim(title)
        if flow:
            out.append(Claim(flow.kind, "", title.strip()[:SHOWN_TEXT], flow.fields))
    for symbol, begin, end in named.values():
        price = price_claim(title, end)
        if price:
            out.append(Claim(price.kind, symbol, f"{title[begin:end]} {price.text}", price.fields))  # the claim reads with the name it is about
    if len(named) != 1:
        return out
    symbol, start, end = next(iter(named.values()))
    clause = _clause_around(title, start, end)  # `RR Kabel profit jumps 56%; Hero MotoCorp revenue rises 21%` is two statements about two companies
    found = [c for c in (results_claim(clause), insider_claim(clause), board_claim(clause, when)) if c]
    if not found and not any(c.kind == "price_move" for c in out):
        rumour = rumour_claim(clause)
        found = [rumour] if rumour else []
    out.extend(Claim(c.kind, symbol, clause[:SHOWN_TEXT], c.fields) for c in found)  # the statement is shown in the words of its clause, so it reads with the company it is about
    return out


_ALIAS_NEXT = frozenset({"shares", "share", "stock", "stocks", "scrip", "ltd", "limited"})
GENERIC_ALIASES = frozenset({
    "gold", "silver", "copper", "crude", "oil", "gas", "power", "steel", "sugar", "cement", "paper", "global", "green", "royal", "general", "golden", "universal", "premier", "modern", "supreme", "unique",
    "super", "star", "sun", "bharat", "india", "indian", "national", "first", "new", "united", "world", "asian", "eastern", "western", "northern", "southern", "central", "capital", "century", "prime",
})


def common_lowercase_words(titles: Iterable[str], min_titles: int = 3) -> frozenset[str]:
    """Words that appear in lower case in at least `min_titles` of the given titles: ordinary words (`silver`, `power`) as against a company's own name (`Pidilite`, which is only ever capitalised)."""
    seen: dict[str, int] = {}
    for title in titles:
        for word in set(re.findall(r"\b[a-z][a-z]{3,}\b", title)):
            seen[word] = seen.get(word, 0) + 1
    return frozenset(w for w, n in seen.items() if n >= min_titles)


_STOP_PHRASES = frozenset({"bank", "india", "indian", "limited", "ltd", "industries", "global", "group", "company", "corporation", "finance", "financial", "capital", "power", "energy", "steel", "motors"})


class NameIndex:
    """Phrase to company, built from the exchange's own names. A phrase that would name two companies (`Reliance`, `Tata`, `Adani`) is dropped, so a title is matched only by a name that is its company's alone.
    The names tried are the full name, the name without Limited, the first word when it is distinctive (`Kotak`) and the capital-letter initials of a long name (`SBI`, `ONGC`, case-sensitive)."""

    def __init__(self, companies: Iterable[tuple[str, str]], common: frozenset[str] = frozenset()):
        from athena.sentiment.page import acronym

        phrases: dict[str, set[str]] = {}
        initials: dict[str, set[str]] = {}
        self._aliases: set[str] = set()
        for symbol, name in companies:
            if not name or not name.strip():
                continue
            full = {p.lower().strip() for p in company_names(name)}
            for phrase in loose_names(company_names(name)):
                key = phrase.lower().strip()
                alias = key not in full
                if alias and (key in common or key in GENERIC_ALIASES):
                    continue
                if len(key) >= 4 and key not in _STOP_PHRASES:
                    phrases.setdefault(key, set()).add(symbol)
                    if alias:
                        self._aliases.add(key)
            a = acronym(name)
            if a:
                initials.setdefault(a, set()).add(symbol)
        self._phrases = {k: next(iter(v)) for k, v in phrases.items() if len(v) == 1}
        self._initials = {k: next(iter(v)) for k, v in initials.items() if len(v) == 1}
        self._longest = max((len(k.split()) for k in self._phrases), default=1)

    def __len__(self) -> int:
        return len(self._phrases) + len(self._initials)

    def find(self, title: str) -> list[tuple[str, int, int]]:
        """Every company the title names as `(symbol, start, end)`. A name inside a longer name of another company is not counted."""
        words = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[A-Za-z0-9&][A-Za-z0-9&.'-]*", title)]
        hits: list[tuple[str, int, int]] = []
        for i in range(len(words)):
            for n in range(min(self._longest, len(words) - i), 0, -1):
                chunk = words[i:i + n]
                phrase = " ".join(w[0] for w in chunk).lower().rstrip(".")
                symbol = self._phrases.get(phrase)
                if symbol and phrase in self._aliases:
                    after = words[i + n][0].lower() if i + n < len(words) else ""
                    if not chunk[0][0][0].isupper() or after not in _ALIAS_NEXT:
                        symbol = None  # a one-word alias must be capitalised and followed by `shares` or `stock`: `Silver` and `Akash Ambani` are not companies
                if symbol:
                    hits.append((symbol, chunk[0][1], chunk[-1][2]))
                    break
            token = words[i][0]
            symbol = self._initials.get(token)
            if symbol and (symbol.startswith(token) or (words[i + 1][0].lower() if i + 1 < len(words) else "") in _ALIAS_NEXT):
                hits.append((symbol, words[i][1], words[i][2]))  # `TCS` is also the ticker and `SBI` starts SBIN; `OEM, B2B` starts nothing and is not followed by `shares`
        # a hit inside a longer hit is dropped; the same company named twice keeps its last mention (the claim reads what follows it)
        kept = [h for h in hits if not any(o is not h and o[1] <= h[1] and h[2] <= o[2] and (o[2] - o[1]) > (h[2] - h[1]) for o in hits)]
        last: dict[str, tuple[str, int, int]] = {}
        for h in kept:
            last[h[0]] = h
        return list(last.values())
