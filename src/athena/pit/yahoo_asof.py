from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

QUARTERLY_DEADLINE = timedelta(days=45)  # SEBI's listing rules: quarterly results within 45 days of the quarter end
ANNUAL_DEADLINE = timedelta(days=60)  # and annual audited results within 60 days of the year end
KEPT_INFO = ("sector", "industry", "financialCurrency")
EVIDENCE_CLASS = "approximate_deadline"  # see athena.evaluation.evidence: never pooled with archival filings
NOTE = (
    "statements are Yahoo's current copy filtered to periods whose legal filing deadline had passed. Yahoo gives no filing dates, so this cannot tell a late filer: a period actually filed after "
    "the as-of date is still kept, and a company that revised its figures shows the revised ones. It is an approximate input, not strict point-in-time evidence"
)


def yahoo_statements_as_of(payload: Mapping[str, Any], as_of: date) -> dict[str, Any]:
    """The stored Yahoo statements as they could have been known on `as_of`, for dates the exchange's own filings no longer
    cover. Yahoo gives only today's copy and no filing dates, so a period is kept when its legal filing deadline (45 days
    after a quarter, 60 after a year) was before `as_of`. That is an APPROXIMATION and not strict: the code cannot identify a
    late filer, so a period filed after `as_of` is still kept (a March year-end actually filed on 20 June is kept for 1 June),
    and later restatements cannot be undone. The returned flag says so, and the result belongs to the `approximate_deadline`
    evidence class, never pooled with archival filings. Nothing describing today (price, market cap, dividend, earnings dates,
    today's share count) is kept; the share count is the latest year's balance sheet one."""
    out = copy.deepcopy(dict(payload))

    def keep(day: str, deadline: timedelta) -> bool:
        return date.fromisoformat(day) + deadline < as_of

    annual = out.get("annual", {})
    for group in annual.values():
        for line, series in group.items():
            group[line] = {day: value for day, value in series.items() if keep(day, ANNUAL_DEADLINE)}
    quarterly = out.get("quarterly", {}).get("income", {})
    for line, series in quarterly.items():
        quarterly[line] = {day: value for day, value in series.items() if keep(day, QUARTERLY_DEADLINE)}
    shares = annual.get("balance", {}).get("Ordinary Shares Number", {})
    info = {key: payload.get("info", {}).get(key) for key in KEPT_INFO if payload.get("info", {}).get(key) is not None}
    info["sharesOutstanding"] = shares[max(shares)] if shares else None
    out["info"] = info
    out["earnings_dates"] = []
    out["quality_flags"] = [flag for flag in out.get("quality_flags", [])] + [NOTE]
    return out
