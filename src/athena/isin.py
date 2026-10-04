from __future__ import annotations

import re

_ISIN_SHAPE = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")


def looks_like_isin(text: str) -> bool:
    return bool(_ISIN_SHAPE.fullmatch(text))


def isin_check_digit(body: str) -> int:
    """Check digit for the first 11 characters of an ISIN (Luhn over letters expanded to numbers)."""
    digits = "".join(str(int(char, 36)) for char in body)
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 0:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return (10 - total % 10) % 10


def isin_checksum_ok(isin: str) -> bool:
    return looks_like_isin(isin) and isin_check_digit(isin[:-1]) == int(isin[-1])


def isin_type_hint(isin: str) -> str | None:
    """Prefix hint only; never a decision rule. INF = fund/ETF units, INE = corporate securities (observed in NSE lists)."""
    if isin.startswith("INF"):
        return "fund_or_etf"
    if isin.startswith("INE"):
        return "corporate"
    return None
