"""Which kind of statement evidence a point-in-time result rests on.

The point-in-time cases read company statements from one of two very different sources, and a result is only as strict as its source:

* `archival`: filings taken from the exchange's own results archive, each cut at its real filing date. Strict, as far as the archive's filing dates are right.
* `approximate_deadline`: Yahoo's CURRENT statements, kept for a date when a period's legal filing deadline had passed. Yahoo gives no filing dates, so this cannot tell a late filer (a period filed
  after the case date is still kept) and shows restated figures where a company revised them. Useful for sensitivity, not strict point-in-time evidence.

The two are reported apart and never pooled into one unqualified score. A log row that recorded no source is `unclassified`, never assumed archival.
"""
from __future__ import annotations

SOURCE_NSE, SOURCE_YAHOO, SOURCE_NONE = "NSE results filings", "Yahoo, filtered by legal filing deadline", "none"
ARCHIVAL, APPROXIMATE, NONE, UNCLASSIFIED = "archival", "approximate_deadline", "none", "unclassified"
_BY_SOURCE = {SOURCE_NSE: ARCHIVAL, SOURCE_YAHOO: APPROXIMATE, SOURCE_NONE: NONE}


def evidence_class(source: object) -> str:
    """The evidence class of a `PitCase.statements_source` / run-log `source` value."""
    return _BY_SOURCE.get(source, UNCLASSIFIED) if isinstance(source, str) else UNCLASSIFIED
