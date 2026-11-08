from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from athena.contracts import AthenaError
from athena.documents.sections import find_sections, section_of
from athena.documents.store import DocStore
from athena.forensic.model import Case
from athena.forensic.peers.lane import PEERS_LANE, peers_tools
from athena.forensic.statements.annuals import Annuals, Year
from athena.forensic.store import ForensicStore
from athena.forensic.supervisor import Supervisor
from athena.forensic.text.lane import DocData, document_tools
from athena.forensic.tools import ToolContext, ToolError, base_registry

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
AS_OF = date(2026, 11, 9)
DOC = "doc1"
CR = 1e7  # the store holds rupees

PEER_TABLE = """BASIS FOR OFFER PRICE
7 Comparison of Accounting Ratios with listed industry peers
Name of the company Total income (Rs in million) P/E Ratio RoNW (%) Profit after tax (Rs in million)
Demo Steel Limited Standalone 26,060.60 NA 27.18 1,711.06
Listed peers
Shyam Alloys and Energy Limited Consolidated 18,755.82 28.29 9.70 1,070.24
Gallant Ispat Limited Consolidated 4,478.52 26.86 15.73 484.27
Jai Balaji Industries Limited Consolidated 5,820.59 44.61 5.93 129.95
"""
PAGES = [
    "DRAFT RED HERRING PROSPECTUS\nDated: September 25, 2026",
    "SECTION II – RISK FACTORS\nWe depend on a single customer for most of our revenue and the loss of that customer would hurt us a great deal.",
    PEER_TABLE,
    "OUR BUSINESS\nWe sell steel products to more than one thousand customers across India and our Limited subsidiary does the same business.",
]


def year(end: date, sales: float, other: float, profit: float, capital: float, reserves: float) -> Year:
    return Year(end, {"sales": sales * CR, "other_income": other * CR, "net_profit": profit * CR}, {"equity_capital": capital * CR, "reserves": reserves * CR}, {})


def annual(symbol: str, name: str, basis: str = "consolidated") -> Annuals:
    return Annuals(symbol, name, "Steel", "indas", basis, [year(date(2025, 3, 31), 4000.0, 100.0, 400.0, 50.0, 1500.0), year(date(2026, 3, 31), 4400.0, 78.516, 484.268, 50.0, 1900.0)])


class Statements:
    def __init__(self, annuals):  # noqa: ANN001
        self._a = {a.symbol: a for a in annuals}

    def universe(self, as_of: date):  # noqa: ANN201
        return SimpleNamespace(annuals=self._a)


ANNUALS = [annual("SHYAMALLOY", "Shyam Alloys and Energy Ltd."), annual("GALLANTT", "Gallantt Ispat Ltd."), annual("SALSTEEL", "S.A.L. Steel Limited", "standalone")]


@pytest.fixture()
def docs(tmp_path):  # noqa: ANN001, ANN201
    path = tmp_path / "documents.duckdb"
    DocStore(path).add(DOC, kind="DRHP", company="Demo Steel", url="https://example.test/d.pdf", sha256="abc", published=date(2026, 9, 25), pages=PAGES, unreadable=[], sections=find_sections(PAGES), section_of=section_of, now=NOW)
    return path


def make(docs, annuals=ANNUALS):  # noqa: ANN001, ANN201
    data = DocData(docs)
    return base_registry([*document_tools(data), *peers_tools(data, Statements(annuals))])


def ctx(tmp_path) -> ToolContext:  # noqa: ANN001
    return ToolContext(Case("c", "drhp", DOC, AS_OF), ForensicStore(tmp_path / "f.duckdb"), "peers")


def test_the_pages_with_the_peer_table_are_found_and_the_business_page_is_not(docs, tmp_path) -> None:  # noqa: ANN001
    out = make(docs).call("peers.pages", {}, ctx(tmp_path))[0]
    assert [p["page"] for p in out["pages"]] == [3] and out["pages"][0]["section"] == "basis_for_price" and "Gallant Ispat Limited" in out["pages"][0]["text"]
    only_business = DocStore(docs.parent / "other.duckdb")
    pages = ["DRAFT RED HERRING PROSPECTUS", "OUR BUSINESS\nWe sell steel products to customers across India through our dealers in many states of the country."]
    only_business.add("d2", kind="DRHP", company="Other", url="https://example.test/o.pdf", sha256="x", published=date(2026, 9, 25), pages=pages, unreadable=[], sections=find_sections(pages), section_of=section_of, now=NOW)
    with pytest.raises(ToolError, match="no page of this document carries a table of listed peers"):
        make(docs.parent / "other.duckdb").call("peers.pages", {}, ToolContext(Case("c2", "drhp", "d2", AS_OF), ForensicStore(tmp_path / "f2.duckdb"), "peers"))


def test_a_peer_is_found_by_name_without_its_legal_suffix_and_with_a_misspelling(docs, tmp_path) -> None:  # noqa: ANN001
    reg = make(docs)
    assert reg.call("peers.find", {"name": "Gallant Ispat Limited"}, ctx(tmp_path))[0]["matches"][0]["symbol"] == "GALLANTT"  # the document prints Gallant, the exchange Gallantt
    assert reg.call("peers.find", {"name": "Shyam Alloys and Energy Limited"}, ctx(tmp_path))[0]["matches"][0]["symbol"] == "SHYAMALLOY"
    with pytest.raises(ToolError, match="no listed company in the store of annual statements matches"):
        reg.call("peers.find", {"name": "Nowhere Textiles Limited"}, ctx(tmp_path))
    with pytest.raises(ToolError, match="give name"):
        reg.call("peers.find", {"name": " "}, ctx(tmp_path))


def test_what_a_peer_filed_is_given_in_crore_with_total_income_as_revenue_plus_other_income_and_net_worth(docs, tmp_path) -> None:  # noqa: ANN001
    out = make(docs).call("peers.filed", {"symbol": "gallantt", "years": 1}, ctx(tmp_path))[0]
    row = out["years"][0]
    assert out["basis"] == "consolidated" and row["fy_end"] == "2026-03-31"
    assert row["sales"] == 4400.0 and row["total_income"] == 4478.52 and row["net_profit"] == 484.27 and row["net_worth"] == 1950.0


def test_compare_works_out_the_difference_in_crore_from_the_documents_unit_and_warns_of_a_basis_mismatch(docs, tmp_path) -> None:  # noqa: ANN001
    reg = make(docs)
    same = reg.call("peers.compare", {"symbol": "GALLANTT", "line": "total_income", "year": 2026, "stated": "4,478.52", "unit": "million"}, ctx(tmp_path))[0]
    assert same["stated_crore"] == 447.852 and same["filed_crore"] == 4478.516 and "basis_warning" not in same  # million is a tenth of a crore: the draft's number is ten times too small for this peer
    ok = reg.call("peers.compare", {"symbol": "GALLANTT", "line": "total_income", "year": 2026, "stated": "44,785.16", "unit": "million"}, ctx(tmp_path))[0]
    assert ok["stated_crore"] == 4478.516 and ok["difference_crore"] == 0.0 and ok["difference_percent"] == 0.0
    off = reg.call("peers.compare", {"symbol": "GALLANTT", "line": "net_profit", "year": 2026, "stated": "5,500", "unit": "million", "basis": "standalone"}, ctx(tmp_path))[0]
    assert off["stated_crore"] == 550.0 and off["filed_crore"] == 484.268 and off["difference_percent"] == pytest.approx(13.58, abs=0.01)
    assert "standalone" in off["basis_warning"] and "consolidated" in off["basis_warning"] and "lakh" not in off["stated_in"]
    worth = reg.call("peers.compare", {"symbol": "SALSTEEL", "line": "net_worth", "year": 2026, "stated": "1,950", "unit": "crore"}, ctx(tmp_path))[0]
    assert worth["difference_crore"] == 0.0 and worth["basis_of_filed_figure"] == "standalone"


def test_compare_refuses_what_it_cannot_work_out_and_says_why(docs, tmp_path) -> None:  # noqa: ANN001
    reg = make(docs)
    with pytest.raises(ToolError, match="no financial year ending in 2027"):
        reg.call("peers.compare", {"symbol": "GALLANTT", "line": "sales", "year": 2027, "stated": "1", "unit": "crore"}, ctx(tmp_path))
    with pytest.raises(ToolError, match="unit must be one of"):
        reg.call("peers.compare", {"symbol": "GALLANTT", "line": "sales", "year": 2026, "stated": "1", "unit": "rupees"}, ctx(tmp_path))
    with pytest.raises(ToolError, match="give year"):
        reg.call("peers.compare", {"symbol": "GALLANTT", "line": "sales", "stated": "abc", "unit": "crore"}, ctx(tmp_path))
    with pytest.raises(ToolError, match="line must be one of"):
        reg.call("peers.compare", {"symbol": "GALLANTT", "line": "ebitda", "year": 2026, "stated": "1", "unit": "crore"}, ctx(tmp_path))
    with pytest.raises(ToolError, match="no annual statements for NOPE"):
        reg.call("peers.filed", {"symbol": "NOPE"}, ctx(tmp_path))


class Script:
    def __init__(self, replies):  # noqa: ANN001
        self.replies = list(replies)

    def complete(self, system: str, user: str) -> str:
        if not self.replies:
            raise AthenaError("script used up")
        r = self.replies.pop(0)
        return r if isinstance(r, str) else json.dumps(r)


def test_a_supervised_run_states_a_difference_between_the_printed_and_the_filed_figure_and_keeps_it_single_source(docs, tmp_path) -> None:  # noqa: ANN001
    store = ForensicStore(tmp_path / "forensic.duckdb")
    flag = {"action": "write", "kind": "FLAG", "flag_type": "claim_vs_outside", "text": "the draft prints a net profit for Gallantt that the peer's own filing does not show",
            "evidence": ["E1"], "innocent": ["a different basis", "a later restatement"], "contradiction": {"relation": "equal", "left": {"ev": "E1", "path": "stated_crore"}, "right": {"ev": "E1", "path": "filed_crore"}, "tol": 0.01}}
    llm = Script([{"action": "tool", "tool": "peers.compare", "args": {"symbol": "GALLANTT", "line": "net_profit", "year": 2026, "stated": "5,500", "unit": "million"}}, flag, {"action": "finish", "summary": "compared one figure"}, "one difference, single source"])
    report = Supervisor(store, make(docs), {"peers": PEERS_LANE}, llm, total_calls=8, max_rounds=1).investigate(Case("peers-1", "drhp", DOC, AS_OF))
    assert report["lanes"]["peers"][0]["rejected"] == 0
    flags = store.entries("peers-1", kind="FLAG")
    assert len(flags) == 1 and flags[0].status == "single_source"  # a relation the model chose is a claim about which numbers must agree, not proof
