"""The shared availability gate for the forensic tools (WP02, A3): nothing published after the case date, no unknown date in strict historical mode, and the policy and counts in the report."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from athena import timing
from athena.forensic import availability
from athena.forensic.model import Case
from athena.forensic.ownership.lane import OwnershipData, ownership_tools, pattern_availability, pattern_quarters
from athena.forensic.store import ForensicStore
from athena.forensic.supervisor import Supervisor
from athena.forensic.text.lane import DocData, document_tools
from athena.forensic.tools import ToolContext, ToolError, base_registry

from test_forensic_ownership import FILED, HOLDINGS, Ev, Fund, pattern  # noqa: E402

TODAY = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr(availability, "utc_now", lambda: TODAY)


class FakeDocs:
    """The few DocStore methods the document tools use, over one stored document."""

    def __init__(self, published):  # noqa: ANN001
        self._published = published

    def document(self, doc_id):  # noqa: ANN001, ANN201
        return {"doc_id": doc_id, "kind": "DRHP", "company": "X", "url": "u", "sha256": "s", "published": self._published, "n_pages": 3, "unreadable": [], "fetched_at": TODAY}

    def figures(self, doc_id):  # noqa: ANN001, ANN201
        return {"offer_total": {"crore": 1000.0}}


def doc_registry(published):  # noqa: ANN001, ANN201
    data = DocData("unused.duckdb")
    data._store = FakeDocs(published)  # type: ignore[assignment]
    return base_registry(document_tools(data))


def doc_ctx(tmp_path, as_of, case_id="c"):  # noqa: ANN001, ANN201
    return ToolContext(Case(case_id, "drhp", "doc1", as_of), ForensicStore(tmp_path / "f.duckdb"), "text")


def test_a_document_published_after_the_case_date_is_refused_by_every_document_tool(tmp_path):
    reg, ctx = doc_registry(date(2027, 1, 1)), doc_ctx(tmp_path, date(2025, 1, 1))
    for tool in ("doc.figures", "doc.map", "doc.open", "doc.search", "doc.baseline"):
        with pytest.raises(ToolError, match="published 2027-01-01, after the case date 2025-01-01"):
            reg.call(tool, {"query": "x", "from_page": 1}, ctx)


def test_a_document_with_an_unknown_publication_date_is_refused_in_a_historical_case_and_admitted_but_labelled_in_a_current_one(tmp_path):
    reg = doc_registry(None)
    with pytest.raises(ToolError, match="unknown"):
        reg.call("doc.figures", {}, doc_ctx(tmp_path, date(2025, 1, 1)))
    result, _ = reg.call("doc.figures", {}, doc_ctx(tmp_path, date(2026, 11, 12), "live"))  # today: the case is not historical
    assert result["figures"] and result["publication"]["status"] == "unknown" and result["publication"]["policy"] == timing.LIVE_POLICY


def test_a_document_published_on_or_before_the_case_date_is_admitted_and_its_date_is_shown(tmp_path):
    reg = doc_registry(date(2025, 1, 1))
    result, _ = reg.call("doc.figures", {}, doc_ctx(tmp_path, date(2025, 1, 1)))
    assert result["publication"] == {"published": "2025-01-01", "status": "admitted", "policy": timing.STRICT_POLICY, "precision": "date"}


def test_the_case_can_force_strict_or_live_mode_whatever_the_dates(tmp_path):
    reg = doc_registry(None)
    forced = Case("f", "drhp", "doc1", date(2026, 11, 12), {"availability": "strict"})
    with pytest.raises(ToolError, match="unknown"):
        reg.call("doc.figures", {}, ToolContext(forced, ForensicStore(tmp_path / "f.duckdb"), "text"))


def test_decisions_are_recorded_and_summarised_with_the_policy_and_the_rejected_items(tmp_path):
    reg, ctx = doc_registry(date(2027, 1, 1)), doc_ctx(tmp_path, date(2025, 1, 1))
    with pytest.raises(ToolError):
        reg.call("doc.figures", {}, ctx)
    with pytest.raises(ToolError):
        reg.call("doc.figures", {}, ctx)  # asking again for the same document does not add a second decision
    doc_registry(date(2024, 6, 1)).call("doc.figures", {}, doc_ctx(tmp_path, date(2025, 1, 1), "c-ok"))
    summary = ctx.store.availability_summary("c")
    assert summary["policy"] == timing.STRICT_POLICY and summary["strict"] is True and summary["admitted"] == 0 and summary["rejected"] == 1 and summary["by_reason"] == {"after_case_date": 1}
    assert summary["rejected_items"][0]["source"] == "document" and "after the case date" in summary["rejected_items"][0]["reason"]
    assert ctx.store.availability_summary("c-ok")["admitted"] == 1 and ctx.store.availability_summary("c-ok")["rejected"] == 0
    assert ctx.store.availability_summary("never-ran") == {"policy": None, "strict": None, "admitted": 0, "rejected": 0, "by_reason": {}, "rejected_items": []}


def test_the_report_carries_the_availability_section(tmp_path):
    store = ForensicStore(tmp_path / "f.duckdb")
    case = Case("r", "drhp", "doc1", date(2025, 1, 1))
    ctx = ToolContext(case, store, "text")
    with pytest.raises(ToolError):
        doc_registry(date(2027, 1, 1)).call("doc.figures", {}, ctx)
    report = Supervisor(store, base_registry(), {}, None, total_calls=0, max_rounds=1).investigate(case)
    assert report["availability"]["policy"] == timing.STRICT_POLICY and report["availability"]["rejected"] == 1 and report["availability"]["strict"] is True


# ---- ownership
def test_a_pattern_with_no_known_filing_time_is_unavailable_in_strict_mode_and_admitted_but_listed_otherwise():
    holdings = {date(2026, 6, 30): pattern(58.0, 38.0, 100.0)}
    assert [q.end for q in pattern_quarters(holdings, {}, date(2026, 6, 30))] == [date(2026, 6, 30)]  # the old behaviour, kept for callers that do not ask for strict
    admitted, rejected = pattern_availability(holdings, {}, date(2026, 6, 30), strict=True)
    assert admitted == [] and rejected[0]["quarter_end"] == "2026-06-30" and "filing time is unknown" in rejected[0]["reason"]
    live, notes = pattern_availability(holdings, {}, date(2026, 6, 30), strict=False)
    assert [q.end for q in live] == [date(2026, 6, 30)] and notes[0]["status"] == "admitted_unknown_filing_time"


def test_a_quarter_filed_after_the_case_date_is_rejected_in_both_modes_and_a_quarter_end_alone_is_not_a_filing():
    for strict in (True, False):
        admitted, rejected = pattern_availability(HOLDINGS, FILED, date(2026, 8, 1), strict=strict)
        assert [q.end for q in admitted] == [date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]
        reasons = {r["quarter_end"]: r["reason"] for r in rejected}
        assert "filed 2026-10-20, after the case date" in reasons["2026-09-30"]
        assert "after the case date" in reasons["2026-09-30"] and "not a quarter end" in reasons["2026-05-15"]


def test_the_quarter_ending_on_the_case_date_is_not_assumed_public_in_a_historical_case(tmp_path):
    fund = Fund({date(2026, 6, 30): pattern(58.0, 38.0, 100.0), date(2026, 3, 31): pattern(58.0, 40.0, 100.0)}, {date(2026, 3, 31): datetime(2026, 4, 20)})  # June has no filing time
    reg = base_registry(ownership_tools(OwnershipData(lambda: fund, lambda: Ev())))
    ctx = ToolContext(Case("o", "listed", "DEMO", date(2026, 6, 30)), ForensicStore(tmp_path / "f.duckdb"), "ownership")  # a historical case: today is 12 November
    out = reg.call("own.holdings", {}, ctx)[0]
    assert [q["quarter_end"] for q in out["quarters"]] == ["2026-03-31"]
    assert out["availability"]["strict"] is True and out["availability"]["rejected"][0]["quarter_end"] == "2026-06-30"
    assert ctx.store.availability_summary("o")["rejected"] == 1
    live = ToolContext(Case("o2", "listed", "DEMO", date(2026, 11, 12)), ForensicStore(tmp_path / "f.duckdb"), "ownership")
    out = reg.call("own.holdings", {}, live)[0]
    assert "2026-06-30" in [q["quarter_end"] for q in out["quarters"]] and out["availability"]["strict"] is False  # today's case: the store itself shows it now holds the pattern
    assert out["availability"]["notes"][0]["status"] == "admitted_unknown_filing_time"
