from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

from athena.documents.sections import find_sections, section_of
from athena.documents.store import DocStore
from athena.forensic import assemble
from athena.forensic.model import Case
from athena.forensic.store import ForensicStore
from athena.forensic.supervisor import KIND_LANES, Supervisor

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
PAGES = ["DRAFT RED HERRING PROSPECTUS\nDated: September 25, 2026", "SECTION II – RISK FACTORS\nWe depend on a single customer for most of our revenue and the loss of that customer would hurt us a great deal."]


def test_a_document_case_has_the_checks_text_and_peers_lanes_and_they_are_the_lanes_the_supervisor_plans_for_a_drhp(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "documents.duckdb"
    DocStore(path).add("d1", kind="DRHP", company="Demo", url="https://example.test/d.pdf", sha256="a", published=date(2026, 9, 25), pages=PAGES, unreadable=[], sections=find_sections(PAGES), section_of=section_of, now=NOW)
    a = assemble.for_document(path, statements=SimpleNamespace(universe=lambda as_of: SimpleNamespace(annuals={})))
    assert set(a.lanes) == {"text", "peers"} and set(a.code_lanes) == {"checks"}
    assert {"doc.open", "doc.checks", "peers.pages", "peers.compare", "ledger.read"} <= set(a.registry.names())
    sup = Supervisor(ForensicStore(tmp_path / "f.duckdb"), a.registry, a.lanes, None, total_calls=0, code_lanes=a.code_lanes)  # type: ignore[arg-type]
    assert sup.plan(Case("c", "drhp", "d1", date(2026, 11, 9))) == ["checks", "text", "peers"]


def test_a_listed_case_has_the_statements_ownership_and_market_lanes_in_the_order_the_supervisor_plans_them() -> None:
    a = assemble.for_listed()
    assert set(a.lanes) == {"statements", "ownership", "market"} and not a.code_lanes
    assert {"fin.detect", "own.holdings", "own.reconcile", "mkt.summary", "mkt.claims"} <= set(a.registry.names())
    assert [n for n in KIND_LANES["listed"] if n in a.lanes] == ["statements", "ownership", "market"]
    for lane in a.lanes.values():
        assert all(t in a.registry.names() for t in lane.tools), lane.name  # a lane never names a tool that is not registered


def test_every_tool_a_document_lane_names_is_registered(tmp_path) -> None:  # noqa: ANN001
    a = assemble.for_document(tmp_path / "documents.duckdb", statements=SimpleNamespace())
    for lane in a.lanes.values():
        assert all(t in a.registry.names() for t in lane.tools), lane.name


def test_the_command_without_an_agent_runs_the_code_lanes_only_and_prints_a_report(tmp_path, capsys) -> None:  # noqa: ANN001
    path = tmp_path / "documents.duckdb"
    DocStore(path).add("d1", kind="DRHP", company="Demo", url="https://example.test/d.pdf", sha256="a", published=date(2026, 9, 25), pages=PAGES, unreadable=[], sections=find_sections(PAGES), section_of=section_of, now=NOW)
    assert assemble.main(["drhp", "d1", "--db", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert "checks" in report["lanes"] and "text" not in report["lanes"] and report["cost"]["model_calls"] == 0
