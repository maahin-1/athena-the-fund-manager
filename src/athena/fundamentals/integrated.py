"""Results filed on NSE since the March 2025 quarter.

From 1 April 2025 NSE requires listed companies to file results through its "Integrated Filing - Financial" XBRL utility, and its own results page says
that all results for the March 2025 quarter and after are listed there. The older listing (`corporates-financial-results`, see `athena.pit.nse_results`)
has nothing after the December 2024 quarter. The new listing is `/api/integrated-filing-results`, one page of rows per request, each with the XBRL
file's link; the file keeps the element names and the `OneD`, `FourD`, `OneI` contexts of the old one under SEBI's `in-capmkt` taxonomy, so
`athena.fundamentals.rows.parse_rows` reads both. `FundamentalsResults` lists both sources for a symbol, through one throttled client."""
from __future__ import annotations

from datetime import datetime

from athena.contracts import AthenaError
from athena.pit.nse_results import IST, FilingRef, NseResults

INTEGRATED_API = "https://www.nseindia.com/api/integrated-filing-results"
INTEGRATED_TYPE = "Integrated Filing- Financials"  # the exchange's own spelling, with the stray space
PAGE_SIZE = 50
BLOCK_MARKERS = ("HTTP 403", "HTTP 429", "HTTP 503")


def _stamp(text: str, formats: tuple[str, ...]) -> datetime | None:
    for form in formats:
        try:
            return datetime.strptime(text.strip(), form).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def integrated_ref(symbol: str, row: dict) -> FilingRef | None:
    """One listing row as a filing reference, or None when it has no usable XBRL link or no readable dates. A March filing is marked annual because
    it carries the whole year as well as the quarter."""
    end = _stamp(str(row.get("qe_Date", "")), ("%d-%b-%Y",))
    filed = _stamp(str(row.get("broadcast_Date", "")), ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"))
    url = str(row.get("xbrl") or "")
    if end is None or filed is None or not url.startswith("http") or not url.endswith(".xml"):
        return None
    consolidated = str(row.get("consolidated", "")).strip().lower().startswith("consolidated")
    return FilingRef(symbol, end.date(), filed, consolidated, end.month == 3, url)


def is_block(exc: Exception) -> bool:
    return any(marker in str(exc) for marker in BLOCK_MARKERS)


class FundamentalsResults(NseResults):
    """The old listing and the new one, as one list of `FilingRef` per symbol, oldest first."""

    def integrated(self, symbol: str, size: int = PAGE_SIZE) -> list[FilingRef]:
        refs: list[FilingRef] = []
        page = 1
        try:
            while True:
                body = self._get(INTEGRATED_API, {"type": INTEGRATED_TYPE, "page": page, "size": size, "symbol": symbol}).json()
                rows = body.get("data") if isinstance(body, dict) else None
                if not isinstance(rows, list):
                    raise AthenaError("unexpected reply shape")
                refs += [ref for row in rows if (ref := integrated_ref(symbol, row)) is not None]
                if not rows or page * size >= int(body.get("totalCount") or 0):
                    break
                page += 1
        except Exception as exc:
            raise AthenaError(f"NSE integrated filings unavailable for {symbol}: {exc}") from exc
        return refs

    def filings(self, symbol: str) -> list[FilingRef]:
        """Both listings. A refusal (403, 429, 503) from either is raised so the caller can stop; any other failure of one listing is passed over
        when the other answered, and both failing is an error."""
        refs: list[FilingRef] = []
        errors: list[str] = []
        for fetch in (super().filings, self.integrated):
            try:
                refs += fetch(symbol)
            except AthenaError as exc:
                if is_block(exc):
                    raise
                errors.append(str(exc))
        if len(errors) == 2:
            raise AthenaError("; ".join(errors))
        return sorted(refs, key=lambda ref: (ref.filed, ref.consolidated))
