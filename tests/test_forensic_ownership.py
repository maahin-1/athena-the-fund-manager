from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from athena.contracts import AthenaError
from athena.forensic.model import Case
from athena.forensic.ownership.lane import OWNERSHIP_LANE, OwnershipData, ownership_tools, pattern_quarters
from athena.forensic.store import ForensicStore
from athena.forensic.supervisor import Supervisor
from athena.forensic.tools import ToolContext, ToolError, base_registry

AS_OF = date(2026, 11, 9)


def pattern(promoters: float, public: float, total: float, fii: float = 10.0, holders: float = 1000.0) -> dict[str, tuple[float, float | None]]:
    return {"promoters": (promoters, 5.0), "public_total": (public, holders), "total": (total, holders + 5), "fii": (fii, 30.0), "dii": (5.0, 10.0), "mutual_funds": (3.0, 4.0), "retail": (20.0, 900.0)}


HOLDINGS = {
    date(2025, 12, 31): pattern(60.0, 40.0, 100.0),
    date(2026, 3, 31): pattern(58.0, 40.0, 100.0, fii=12.0),
    date(2026, 6, 30): pattern(58.0, 38.0, 100.0, fii=14.0),
    date(2026, 5, 15): pattern(1.0, 1.0, 100.0),  # a one-off pattern on a day that is not a quarter end
    date(2026, 9, 30): pattern(50.0, 50.0, 100.0),  # filed after the as-of day in some tests
}
FILED = {
    date(2025, 12, 31): datetime(2026, 1, 20), date(2026, 3, 31): datetime(2026, 4, 20), date(2026, 6, 30): datetime(2026, 7, 20), date(2026, 9, 30): datetime(2026, 10, 20),
}


class Fund:
    def __init__(self, holdings=HOLDINGS, filed=FILED):  # noqa: ANN001
        self._h, self._f = holdings, filed

    def holdings(self, symbol: str):  # noqa: ANN201
        return self._h

    def holding_filed(self, symbol: str):  # noqa: ANN201
        return self._f


def trade(category: str, txn: str, shares: float, filed: datetime, to: date | None = None, person: str = "A Person", value: float = 100000.0) -> dict:
    return {"symbol": "DEMO", "person": person, "category": category, "txn": txn, "mode": "Market Sale", "security": "Equity Shares", "shares": shares, "value": value, "before_pct": 1.0, "after_pct": 0.9,
            "trade_from": to, "trade_to": to, "filed_at": filed, "first_seen_at": filed, "link": ""}


class Ev:
    def __init__(self, trades=(), board=()):  # noqa: ANN001
        self._t, self._b = list(trades), list(board)

    def insider_between(self, since: date, until: date, symbol=None, limit: int = 500):  # noqa: ANN001, ANN201
        return [t for t in self._t if since <= t["filed_at"].date() <= until][:limit]

    def insider_newest(self):  # noqa: ANN201
        return max((t["filed_at"] for t in self._t), default=None)

    def board_for(self, symbol: str, since: date, until: date):  # noqa: ANN001, ANN201
        return [b for b in self._b if since <= b["meeting_date"] <= until]


def ctx(tmp_path, as_of: date = AS_OF) -> ToolContext:  # noqa: ANN001
    return ToolContext(Case("c", "listed", "DEMO", as_of), ForensicStore(tmp_path / "f.duckdb"), "ownership")


def run(tmp_path, name: str, fund=None, ev=None, as_of: date = AS_OF, **args):  # noqa: ANN001, ANN003, ANN201
    registry = base_registry(ownership_tools(OwnershipData(lambda: fund or Fund(), lambda: ev or Ev())))
    return registry.call(name, args, ctx(tmp_path, as_of))[0]


def test_only_quarter_ends_filed_by_the_day_are_used_and_the_gap_between_the_groups_is_shown(tmp_path) -> None:  # noqa: ANN001
    rows = pattern_quarters(HOLDINGS, FILED, date(2026, 8, 1))
    assert [q.end for q in rows] == [date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]  # the 15 May pattern is not a quarter end; 30 Sep is not yet filed
    out = run(tmp_path, "own.holdings", as_of=date(2026, 8, 1))
    last = out["quarters"][-1]
    assert last["percent"]["promoters"] == 58.0 and last["change_points"]["promoters"] == 0.0 and last["change_points"]["fii"] == 2.0
    assert last["percent"]["unclassified"] == 4.0  # 100 less 58 less 38: a pattern whose groups do not add up is shown with the gap, not dropped
    assert "parts of dii" in out["unit"]
    later = run(tmp_path, "own.holdings", as_of=date(2026, 11, 1))["quarters"][-1]
    assert later["quarter_end"] == "2026-09-30" and later["change_points"]["promoters"] == -8.0


def test_no_pattern_filed_by_the_day_is_a_clear_error(tmp_path) -> None:  # noqa: ANN001
    with pytest.raises(ToolError, match="no shareholding pattern filed by 2025-01-01"):
        run(tmp_path, "own.holdings", as_of=date(2025, 1, 1))


def test_insider_filings_are_summed_by_category_without_a_name_and_the_window_is_the_filing_date(tmp_path) -> None:  # noqa: ANN001
    ev = Ev([
        trade("Promoters", "Sell", 1000, datetime(2026, 5, 4), date(2026, 5, 1), person="First Name"),
        trade("Promoters", "Buy", 300, datetime(2026, 5, 20), date(2026, 5, 18), person="Second Name"),
        trade("Promoters", "Pledge", 5000, datetime(2026, 6, 1), date(2026, 5, 30), person="First Name"),
        trade("Director", "Sell", 50, datetime(2025, 1, 4), date(2025, 1, 2)),  # a year and more before the day
    ])
    out = run(tmp_path, "own.insider", ev=ev)
    assert out["filings"] == 3 and set(out["by_category"]) == {"Promoters"}
    p = out["by_category"]["Promoters"]
    assert p["sold_shares"] == 1000 and p["bought_shares"] == 300 and p["net_shares"] == -700 and p["pledge_lines"] == 1 and p["distinct_people"] == 2
    assert "First Name" not in json.dumps(out) and "Second Name" not in json.dumps(out)  # a flag never names a person
    assert out["insider_reaches"] == "2026-06-01" and "different sources" in out["limits"]
    assert run(tmp_path, "own.insider", ev=ev, days=1000)["filings"] == 4


def test_reconcile_compares_the_change_in_promoter_shares_with_the_insider_net_and_scales_for_a_bonus(tmp_path) -> None:  # noqa: ANN001
    holdings = {
        date(2026, 3, 31): pattern(600.0, 400.0, 1000.0),
        date(2026, 6, 30): pattern(1190.0, 800.0, 2000.0),  # a one-for-one bonus doubles every holding; the promoters also sold ten shares
        date(2026, 9, 30): pattern(1190.0, 800.0, 2000.0),  # nothing changed and nothing was filed
    }
    filed = {date(2026, 3, 31): datetime(2026, 4, 20), date(2026, 6, 30): datetime(2026, 7, 20), date(2026, 9, 30): datetime(2026, 10, 20)}
    ev = Ev([trade("Promoters", "Sell", 10, datetime(2026, 7, 5), date(2026, 7, 1)), trade("Director", "Sell", 99, datetime(2026, 5, 5), date(2026, 5, 1))])  # a promoter sale on 1 July (September quarter) and a director's sale in May (not a promoter)
    out = run(tmp_path, "own.reconcile", fund=Fund(holdings, filed), ev=ev, as_of=date(2026, 11, 1))
    june, sept = out["quarters"]
    assert june["quarter_end"] == "2026-06-30" and june["total_shares_change_percent"] == 100.0
    assert june["promoter_shares_change_in_patterns"] == 590 and june["change_if_stake_unchanged"] == 600
    assert june["insider_filings_counted"] == 0 and june["gap_shares"] == -10  # the promoters hold ten fewer than a constant stake would, and no filing in the quarter explains it
    assert sept["quarter_end"] == "2026-09-30" and sept["insider_net_shares_promoters"] == -10 and sept["insider_filings_counted"] == 1 and sept["gap_shares"] == 10
    assert sept["gap_points_of_all_shares"] == 0.5  # ten shares in two thousand


def test_reconcile_needs_two_quarters_and_board_meetings_are_listed_up_to_the_day(tmp_path) -> None:  # noqa: ANN001
    one = {date(2026, 3, 31): pattern(600.0, 400.0, 1000.0)}
    with pytest.raises(ToolError, match="two quarters"):
        run(tmp_path, "own.reconcile", fund=Fund(one, {date(2026, 3, 31): datetime(2026, 4, 20)}))
    ev = Ev(board=[{"meeting_date": date(2026, 8, 1), "purpose": "Fund Raising", "description": "to consider raising funds"}, {"meeting_date": date(2026, 12, 1), "purpose": "Results", "description": ""}])
    meetings = run(tmp_path, "own.board", ev=ev)["meetings"]
    assert [m["purpose"] for m in meetings] == ["Fund Raising"]  # a meeting after the day is not known yet


class Script:
    def __init__(self, replies):  # noqa: ANN001
        self.replies = list(replies)

    def complete(self, system: str, user: str) -> str:
        if not self.replies:
            raise AthenaError("script used up")
        r = self.replies.pop(0)
        return r if isinstance(r, str) else json.dumps(r)


def test_a_supervised_run_cites_the_holdings_and_a_number_not_in_the_result_is_refused(tmp_path) -> None:  # noqa: ANN001
    store = ForensicStore(tmp_path / "forensic.duckdb")
    registry = base_registry(ownership_tools(OwnershipData(lambda: Fund(), lambda: Ev())))
    good = {"action": "write", "kind": "OBSERVATION", "text": "the promoters held 58.0 percent in the quarter ending 2026-06-30", "evidence": ["E1"], "values": {"promoters": 58.0}}
    made_up = {"action": "write", "kind": "OBSERVATION", "text": "the promoters held 71.5 percent in the quarter ending 2026-06-30", "evidence": ["E1"], "values": {"promoters": 71.5}}
    llm = Script([{"action": "tool", "tool": "own.holdings", "args": {}}, good, made_up, {"action": "finish", "summary": "read the holdings"}, "one observation, one refused"])
    report = Supervisor(store, registry, {"ownership": OWNERSHIP_LANE}, llm, total_calls=8, max_rounds=1).investigate(Case("own-1", "listed", "DEMO", AS_OF))
    assert report["lanes"]["ownership"][0]["rejected"] == 1
    kept = [e.text for e in store.entries("own-1", kind="OBSERVATION") if e.status != "rejected"]
    assert any("58.0 percent" in t for t in kept) and not any("71.5" in t for t in kept)
