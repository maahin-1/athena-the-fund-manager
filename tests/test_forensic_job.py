from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from datetime import date, datetime, timezone

import pytest

from athena.contracts import AthenaError
from athena.documents.sections import Section
from athena.documents.store import DocStore
from athena.forensic import job
from athena.forensic.job import SCHEMA, checks_view, clean_name, error_document, group_of, run_job, validate_url

URL = "https://www.sebi.gov.in/sebi_data/attachdocs/oct-2026/1_draft.pdf"
NOW = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)

BALANCE = """RESTATED STATEMENT OF ASSETS AND LIABILITIES
(All amounts are in Rs. Millions)
Particulars As at March 31,
2026
As at March 31,
2025
Property, plant and equipment 150.00 120.00
TOTAL NON-CURRENT ASSETS (A) 200.00 160.00
TOTAL CURRENT ASSETS (B) 100.00 90.00
TOTAL ASSETS (A+B) {assets} 250.00
Equity share capital 40.00 40.00
Other equity 160.00 120.00
TOTAL EQUITY (A) 200.00 160.00
TOTAL NON-CURRENT LIABILITIES (B) 60.00 50.00
TOTAL CURRENT LIABILITIES (C) 40.00 40.00
TOTAL EQUITY & LIABILITIES (A+B+C) 300.00 250.00
"""


def held(tmp_path, assets: str = "300.00"):  # noqa: ANN001, ANN202
    path = tmp_path / "docs.duckdb"
    store = DocStore(path)
    pages = ["cover page of the draft", BALANCE.format(assets=assets)]
    sections = [Section("front", "Cover", 1, 1), Section("financials", "Restated financial information", 2, 2)]
    store.add("d1", kind="DRHP", company="Demo Limited", url=URL, sha256="s1", published=date(2026, 9, 1), pages=pages, unreadable=[], sections=sections, section_of=lambda secs, n: "front" if n == 1 else "financials", now=NOW)
    return path


def test_the_address_must_be_a_pdf_on_the_regulators_site_and_nothing_else_is_downloaded() -> None:
    assert validate_url(f"  {URL} ") == URL
    for bad in ("", "   ", "http://www.sebi.gov.in/x.pdf", "https://evil.test/x.pdf", "https://www.sebi.gov.in.evil.test/x.pdf", "https://www.sebi.gov.in@evil.test/x.pdf", "https://user:pw@www.sebi.gov.in/x.pdf",
                "https://www.sebi.gov.in:8443/x.pdf", "https://www.sebi.gov.in/x.html", "file:///etc/passwd", "https://www.sebi.gov.in/a b.pdf", "https://www.sebi.gov.in/" + "x" * 400 + ".pdf"):
        with pytest.raises(AthenaError):
            validate_url(bad)


def test_names_are_kept_short_and_plain() -> None:
    assert clean_name("  Demo   Limited ", "x") == "Demo Limited"
    assert clean_name("Bad;name$(rm)", "fallback") == "fallback" and clean_name("", "fallback") == "fallback" and len(clean_name("a" * 500, "f")) == 120


def test_a_held_draft_gives_the_report_document_with_the_checks_counted_and_the_lanes_not_run_named(tmp_path) -> None:  # noqa: ANN001
    doc = run_job(URL, docs_path=held(tmp_path), forensic_path=tmp_path / "f.duckdb", fetch=lambda u: pytest.fail("a held address is not downloaded again"), now=NOW)
    assert doc["schema"] == SCHEMA and doc["ok"] is True and doc["mode"] == "checks" and doc["generated_at"] == "2026-11-07T09:00:00+00:00"
    assert doc["document"]["company"] == "Demo Limited" and doc["document"]["pages"] == 2 and doc["document"]["sections"][1]["key"] == "financials"
    assert doc["checks"]["summary"]["differ"] == 0 and doc["checks"]["summary"]["held"] >= 3 and doc["flags"] == []
    assert doc["concern"]["established"] == 0 and doc["cost"]["model_calls"] == 0
    assert {n["lane"] for n in doc["not_examined"]} >= {"text", "statements"}  # no such agent in a checks-only run: said, not hidden
    assert doc["limits"] and "not investment advice" in doc["caveat"] and doc["checks"]["not_built"]
    json.dumps(doc, allow_nan=False)  # one plain JSON document


def test_a_difference_in_the_draft_becomes_a_flag_with_the_pages_the_figures_and_the_innocent_explanations(tmp_path) -> None:  # noqa: ANN001
    doc = run_job(URL, docs_path=held(tmp_path, assets="305.00"), forensic_path=tmp_path / "f.duckdb", now=NOW)
    assert doc["ok"] and doc["checks"]["summary"]["differ"] >= 1
    diff = doc["checks"]["differences"][0]
    assert diff["group"] == "statements" and diff["pages"] == [2] and {t["printed"] for t in diff["left"] + diff["right"]} >= {"305.00"}
    assert doc["flags"] and doc["flags"][0]["status"] == "established" and doc["flags"][0]["innocent_explanations"]
    assert doc["concern"]["established"] >= 1
    from athena.guard.scope import advice_in

    assert not advice_in(doc["narrative"]) and not any(advice_in(f["text"]) for f in doc["flags"])  # differences to look at, never a recommendation


def test_the_report_has_every_key_the_pages_reader_uses(tmp_path) -> None:  # noqa: ANN001
    """terminal/web/src/forensiclogic.ts (`readReport`) reads exactly these; terminal/web/test/forensic.test.ts builds a document of the same shape. Change one side and this test says so."""
    doc = run_job(URL, docs_path=held(tmp_path, assets="305.00"), forensic_path=tmp_path / "f.duckdb", now=NOW)
    assert {"schema", "ok", "generated_at", "document", "concern", "flags", "checks", "not_examined", "limits", "narrative", "caveat", "cost"} <= set(doc)
    assert {"company", "kind", "pages", "sections", "sections_not_found", "pages_without_text"} <= set(doc["document"])
    assert {"title", "start", "end", "key"} <= set(doc["document"]["sections"][0])
    assert {"low", "high", "established", "single_source"} <= set(doc["concern"])
    assert {"id", "status", "text", "why", "innocent_explanations"} <= set(doc["flags"][0])
    assert {"summary", "groups", "differences", "not_run", "notes", "not_built"} <= set(doc["checks"])
    assert {"held", "differ", "not_run"} <= set(doc["checks"]["summary"])
    assert {"id", "title", "held", "differ", "not_run"} <= set(doc["checks"]["groups"][0])
    assert {"id", "group", "title", "reason", "pages", "printed_pages", "left", "right"} <= set(doc["checks"]["differences"][0])
    assert {"name", "printed", "page"} <= set(doc["checks"]["differences"][0]["left"][0])
    assert {"lane", "why"} <= set(doc["not_examined"][0]) and "model_calls" in doc["cost"]
    assert set(error_document("x")) == {"schema", "ok", "error"}


def test_groups_are_named_by_the_check_id_and_the_view_counts_each_status() -> None:
    assert [group_of(i) for i in ("holding_shares:7:1", "litigation_count:9:1", "capitalisation_sum:1:2", "creditors_equal_trade_payables", "discussion_change:5:0", "offer_structure", "balance_sheet_balances@4:2026")] == [
        "holdings", "litigation", "capitalisation", "creditors", "discussion", "offer", "statements"]
    result = {"checks": [
        {"id": "holding_shares:7:1", "status": "ok", "title": "t", "reason": "", "left": [], "right": [], "tolerance": 0.0},
        {"id": "holding_pct:7:1", "status": "differs", "title": "off", "reason": "left 1 against right 2", "left": [{"name": "a", "printed": "1", "page": 7, "value": 1}], "right": [{"name": "b", "printed": "2", "page": 8, "value": 2}], "tolerance": 0.01},
        {"id": "creditors_equal_trade_payables", "status": "not_run", "title": "c", "reason": "the table is not stated as at a March 31 year end: x", "left": [], "right": [], "tolerance": 0.0}],
        "summary": {"ok": 1, "differs": 1, "not_run": 1, "run": 2, "statement_tables_read": 0}, "notes": ["n"]}
    view = checks_view(result)
    assert view["summary"] == {"held": 1, "differ": 1, "not_run": 1, "statement_tables_read": 0}
    assert [(g["id"], g["held"], g["differ"], g["not_run"]) for g in view["groups"]] == [("holdings", 1, 1, 0), ("creditors", 0, 0, 1)]
    assert view["differences"][0]["pages"] == [7, 8] and view["not_run"] == [{"reason": "the table is not stated as at a March 31 year end", "count": 1}]


def test_a_bad_address_a_missing_document_and_a_crash_are_all_answered_in_the_same_shape(tmp_path, capsys, monkeypatch) -> None:  # noqa: ANN001
    assert error_document("x" * 500)["ok"] is False and len(error_document("x" * 500)["error"]) == 240
    out = io.StringIO()
    with redirect_stdout(out):
        assert job.main(["--url", "https://evil.test/x.pdf", "--docs", str(tmp_path / "d.duckdb"), "--forensic", str(tmp_path / "f.duckdb")]) == 0
    answer = json.loads(out.getvalue())
    assert answer == {"schema": SCHEMA, "ok": False, "error": "only a document on the regulator's site (https://www.sebi.gov.in) can be analysed"}
    monkeypatch.setattr(job, "run_job", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret internals")))
    out = io.StringIO()
    with redirect_stdout(out):
        assert job.main(["--url", URL]) == 0
    crashed = json.loads(out.getvalue())
    assert crashed["ok"] is False and "secret" not in crashed["error"] and "RuntimeError" in capsys.readouterr().err
