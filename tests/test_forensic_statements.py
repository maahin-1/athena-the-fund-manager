"""The statements lane: the annual loader and its point-in-time filter, the measures, the peer ranking, the lane's tools, and a scripted supervised run, over synthetic companies in a real DuckDB file."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import duckdb
import pytest

from athena.contracts import AthenaError
from athena.forensic.model import Case
from athena.forensic.statements.annuals import Annuals, Year, load
from athena.forensic.statements.detect import ELEVATED, Universe
from athena.forensic.statements.lane import STATEMENTS_LANE, StatementsData, statement_tools
from athena.forensic.statements.metrics import BY_KEY, MEASURES, compute
from athena.forensic.store import ForensicStore
from athena.forensic.supervisor import Supervisor
from athena.forensic.tools import ToolContext, ToolError, base_registry
from athena.fundamentals.store import FundamentalsStore

CR = 1e7
AS_OF = date(2026, 11, 3)


def company(**over):  # noqa: ANN003, ANN201
    base = {"sales": 1000.0, "sales_growth": 0.10, "margin": 0.10, "cfo_ratio": 1.0, "rec": 100.0, "rec_growth": 0.10, "inv": 80.0, "inv_growth": 0.10, "pay": 60.0, "pay_growth": 0.10, "tax_rate": 0.25,
            "other_income": 20.0, "fa": 400.0, "fa_growth": 0.08, "cwip": 40.0, "borrow": 300.0, "cash": 50.0, "capex": 45.0, "interest": 9.0, "dep": 35.0, "q4_share": 0.25, "industry": "Widgets", "layout": "indas"}
    base.update(over)
    return base


def make_db(path, spec, years=(2023, 2024, 2025, 2026), basis="consolidated"):  # noqa: ANN001, ANN201
    FundamentalsStore(path).close()  # creates the schema
    con = duckdb.connect(str(path))
    rows = []
    for symbol, p in spec.items():
        con.execute("INSERT INTO companies VALUES (?, ?, ?, ?, ?, ?, ?)", [symbol, f"{symbol} Limited", f"INE{symbol}", p["industry"], p["layout"], 1e8, datetime(2026, 1, 1)])
        for i, year in enumerate(years):
            end, filed = date(year, 3, 31), datetime(year, 5, 10)
            sales = p["sales"] * (1 + p["sales_growth"]) ** i
            profit = p["margin"] * sales
            pbt = profit / (1 - p["tax_rate"])
            fa = p["fa"] * (1 + p["fa_growth"]) ** i
            borrowings = p["borrow"]
            lines = {
                "income": {"sales": sales, "other_income": p["other_income"], "pbt": pbt, "tax": pbt - profit, "net_profit": profit, "depreciation": p["dep"], "interest": p["interest"]},
                "balance": {"receivables": p["rec"] * (1 + p["rec_growth"]) ** i, "inventory": p["inv"] * (1 + p["inv_growth"]) ** i, "payables": p["pay"] * (1 + p["pay_growth"]) ** i, "fixed_assets": fa, "cwip": p["cwip"],
                            "borrowings": borrowings, "cash": p["cash"], "total_assets": fa + p["cwip"] + 300 + sales * 0.2, "current_assets": 300 + sales * 0.2},
                "cashflow": {"cfo": profit * p["cfo_ratio"], "capex": p["capex"]},
            }
            for statement, kv in lines.items():
                start = end if statement == "balance" else end - timedelta(days=364)
                for key, value in kv.items():
                    rows.append((symbol, basis, statement, start, end, key, value * CR, f"u-{symbol}-{year}", filed, "nse-xbrl"))
            qend = end
            for key, value in (("sales", sales * p["q4_share"]), ("net_profit", profit * p["q4_share"])):
                rows.append((symbol, basis, "income", qend - timedelta(days=89), qend, key, value * CR, f"q-{symbol}-{year}", filed, "nse-xbrl"))
    con.executemany("INSERT INTO statement_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.close()


@pytest.fixture
def field_db(tmp_path):  # noqa: ANN001, ANN201
    spec = {f"NORM{i:02d}": company(rec_growth=0.05 + 0.004 * i, cfo_ratio=0.9 + 0.01 * i, other_income=10 + i) for i in range(24)}
    spec["HIGHREC"] = company(rec_growth=0.60, cash=140.0)
    spec["LOWCFO"] = company(cfo_ratio=0.05)
    spec["BANKISH"] = company(layout="bank")
    path = tmp_path / "fund.duckdb"
    make_db(path, spec)
    return path


def connection(path):  # noqa: ANN001, ANN201
    return lambda: duckdb.connect(str(path), read_only=True)


def year(end: date, income=None, balance=None, cashflow=None) -> Year:  # noqa: ANN001
    return Year(end, income or {}, balance or {}, cashflow or {})


def annuals(years, quarters=None) -> Annuals:  # noqa: ANN001
    return Annuals("X", "X Limited", "Widgets", "indas", "consolidated", list(years), quarters or {})


def y4(**series):  # noqa: ANN003, ANN201
    """Four consecutive years from a dict of {statement: {line: [v0, v1, v2, v3]}} in rupees."""
    out = []
    for i in range(4):
        parts = {s: {k: v[i] for k, v in lines.items()} for s, lines in series.items()}
        out.append(year(date(2023 + i, 3, 31), parts.get("income"), parts.get("balance"), parts.get("cashflow")))
    return out


class TestLoader:
    def test_only_what_was_filed_by_the_date_is_read(self, field_db) -> None:  # noqa: ANN001
        con = duckdb.connect(str(field_db), read_only=True)
        now, then = load(con, AS_OF)["NORM00"], load(con, date(2025, 12, 31))["NORM00"]
        assert [y.fy_end.year for y in now.years] == [2023, 2024, 2025, 2026]
        assert [y.fy_end.year for y in then.years] == [2023, 2024, 2025]  # FY2026 was filed in May 2026
        assert load(con, date(2023, 1, 1)) == {}

    def test_banks_and_lenders_are_not_read_here(self, field_db) -> None:  # noqa: ANN001
        assert "BANKISH" not in load(duckdb.connect(str(field_db), read_only=True), AS_OF)

    def test_a_year_without_a_balance_sheet_is_kept_but_its_balance_measures_are_not_computed_and_consolidated_is_preferred(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "f.duckdb"
        make_db(path, {"AAA": company()}, basis="consolidated")
        con = duckdb.connect(str(path))
        con.execute("DELETE FROM statement_lines WHERE statement = 'balance' AND period_end = DATE '2024-03-31'")
        con.execute("INSERT INTO statement_lines SELECT symbol, 'standalone', statement, period_start, period_end, key, value, filing_url, filed, source FROM statement_lines")
        con.close()
        a = load(duckdb.connect(str(path), read_only=True), AS_OF)["AAA"]
        assert a.basis == "consolidated" and [y.fy_end.year for y in a.years] == [2023, 2024, 2025, 2026] and a.years[1].balance == {}
        assert "other_income_share" in compute(a) and "effective_tax_rate" in compute(a)  # income-only measures run whatever balance sheets are held

    def test_a_company_with_income_statements_only_still_gets_the_income_measures(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "f.duckdb"
        make_db(path, {"AAA": company()})
        con = duckdb.connect(str(path))
        con.execute("DELETE FROM statement_lines WHERE statement IN ('balance', 'cashflow')")
        con.close()
        a = load(duckdb.connect(str(path), read_only=True), AS_OF)["AAA"]
        got = set(compute(a))
        assert got == {"other_income_share", "effective_tax_rate", "profit_vs_sales_growth", "q4_profit_share"}

    def test_runs_must_be_consecutive(self) -> None:
        a = annuals([year(date(2020, 3, 31)), year(date(2022, 3, 31)), year(date(2023, 3, 31))])
        assert a.run(2) is not None and a.run(3) is None  # a missing year breaks the run

    def test_quarters_are_kept(self, field_db) -> None:  # noqa: ANN001
        a = load(duckdb.connect(str(field_db), read_only=True), AS_OF)["NORM00"]
        assert date(2026, 3, 31) in a.quarters and a.quarters[date(2026, 3, 31)]["net_profit"] > 0


class TestMeasures:
    def test_cfo_to_profit_and_its_refusals(self) -> None:
        ys = y4(income={"net_profit": [100, 100, 100, 100]}, cashflow={"cfo": [50, 50, 60, 70]})
        value = compute(annuals(ys))["cfo_to_profit"]
        assert value.value == pytest.approx(180 / 300)  # the last three years
        assert "cfo_to_profit" not in compute(annuals(y4(income={"net_profit": [-100] * 4}, cashflow={"cfo": [50] * 4})))  # a ratio of cash to a loss is meaningless
        assert "cfo_to_profit" not in compute(annuals([year(date(2023, 3, 31), {"net_profit": 1}, {}, {"cfo": 1}), year(date(2025, 3, 31), {"net_profit": 1}, {}, {"cfo": 1})]))

    def test_receivables_growth_less_sales_growth(self) -> None:
        ys = y4(income={"sales": [100, 110, 121, 133.1]}, balance={"receivables": [10, 12, 14.4, 17.28]})
        v = compute(annuals(ys))["receivables_vs_sales"]
        assert v.value == pytest.approx(10.0, abs=0.01) and v.series["receivables_growth_pct_a_year"] == pytest.approx(20.0, abs=0.1)

    def test_tax_rate_and_other_income_share(self) -> None:
        ys = y4(income={"tax": [10, 10, 10, 10], "pbt": [100, 100, 100, 100], "other_income": [30, 30, 30, 30]})
        c = compute(annuals(ys))
        assert c["effective_tax_rate"].value == pytest.approx(10.0) and c["other_income_share"].value == pytest.approx(30.0)

    def test_additions_against_cash_capex(self) -> None:
        ys = y4(balance={"fixed_assets": [100, 130, 160, 190], "cwip": [0, 0, 0, 0]}, income={"depreciation": [10, 10, 10, 10]}, cashflow={"capex": [30, 30, 30, 30]})
        # additions = (190 - 100) + 30 of depreciation over three years = 120 against 90 of cash capex
        assert compute(annuals(ys))["additions_vs_capex"].value == pytest.approx(120 / 90, abs=1e-3)

    def test_cash_beside_debt_is_only_for_a_heavy_borrower(self) -> None:
        light = y4(balance={"borrowings": [10] * 4, "total_assets": [1000] * 4, "cash": [500] * 4})
        heavy = y4(balance={"borrowings": [300] * 4, "total_assets": [1000] * 4, "cash": [150] * 4, "bank_balances": [30] * 4})
        assert "cash_beside_debt" not in compute(annuals(light))
        assert compute(annuals(heavy))["cash_beside_debt"].value == pytest.approx(60.0)

    def test_a_missing_line_is_a_gap_not_a_zero(self) -> None:
        assert compute(annuals(y4(income={"sales": [100, 110, 121, 133]}))) == {}

    def test_q4_share_of_the_years_profit(self) -> None:
        ys = y4(income={"net_profit": [100, 100, 100, 100]})
        quarters = {y.fy_end: {"net_profit": 40.0} for y in ys}
        assert compute(annuals(ys, quarters))["q4_profit_share"].value == pytest.approx(40.0)

    def test_every_measure_names_its_innocent_explanations_and_a_side(self) -> None:
        assert len(MEASURES) == 14 and len({m.key for m in MEASURES}) == 14
        assert all(m.innocent and m.bad in ("high", "low") and m.what for m in MEASURES)


class TestUniverse:
    def test_the_outliers_stand_out_on_the_right_side(self, field_db) -> None:  # noqa: ANN001
        u = Universe.build(duckdb.connect(str(field_db), read_only=True), AS_OF)
        high = {f.key: f for f in u.findings("HIGHREC")}
        assert high["receivables_vs_sales"].worse_than >= ELEVATED and high["receivables_vs_sales"].elevated
        low = {f.key: f for f in u.findings("LOWCFO")}
        assert low["cfo_to_profit"].elevated  # a low cash-to-profit ratio is the concerning side
        normal = {f.key: f for f in u.findings("NORM12")}
        assert normal["receivables_vs_sales"].worse_than < ELEVATED

    def test_a_peer_group_is_the_industry_only_when_it_is_big_enough(self, field_db) -> None:  # noqa: ANN001
        u = Universe.build(duckdb.connect(str(field_db), read_only=True), AS_OF)
        f = {x.key: x for x in u.findings("NORM00")}["receivables_vs_sales"]
        assert f.peers == 26 and f.peer_basis == "industry: Widgets"  # 24 + the two outliers; the bank-layout company is not a peer
        assert "More concerning than" in f.reading and "26 peers (industry: Widgets)" in f.reading

    def test_a_small_industry_falls_back_to_everyone_and_says_so(self, tmp_path) -> None:  # noqa: ANN001
        spec = {f"S{i:02d}": company(industry="Rare" if i < 3 else "Widgets", rec_growth=0.05 + 0.01 * i) for i in range(20)}
        make_db(tmp_path / "f.duckdb", spec)
        u = Universe.build(duckdb.connect(str(tmp_path / "f.duckdb"), read_only=True), AS_OF)
        f = {x.key: x for x in u.findings("S00")}["receivables_vs_sales"]
        assert f.peer_basis == "all industrial companies" and f.peers == 20

    def test_convergence_counts_elevated_measures_against_the_field(self, field_db) -> None:  # noqa: ANN001
        u = Universe.build(duckdb.connect(str(field_db), read_only=True), AS_OF)
        c = u.convergence("HIGHREC")
        assert c["elevated_measures"] >= 1 and c["measures_computed"] == 14 and 0 <= c["share_of_companies_with_fewer"] <= 1
        assert u.convergence("BANKISH") is None


class TestTools:
    def ctx(self, store, symbol="HIGHREC"):  # noqa: ANN001, ANN201
        case = Case("c", "listed", symbol, AS_OF)
        store.add_case(case)
        return ToolContext(case, store, "statements")

    def tools(self, field_db):  # noqa: ANN001, ANN201
        return {t.name: t for t in statement_tools(StatementsData(connection(field_db)))}

    def test_profile_table_detect_peers_and_quarters(self, field_db, tmp_path) -> None:  # noqa: ANN001
        store, tools = ForensicStore(tmp_path / "f.duckdb"), self.tools(field_db)
        ctx = self.ctx(store)
        profile = tools["fin.profile"].fn(ctx, {})
        assert profile["years"][-1] == "2026-03-31" and profile["basis"] == "consolidated" and "Beneish" in profile["limits"]
        table = tools["fin.table"].fn(ctx, {"lines": ["sales", "cfo", "receivables"]})
        assert table["unit"] == "crore" and table["sales"]["2023-03-31"] == pytest.approx(1000.0)
        detect = tools["fin.detect"].fn(ctx, {})
        assert detect["elevated"] and detect["elevated"][0]["measure"] in BY_KEY and detect["convergence"]["measures_computed"] == 14 and detect["limits"]
        peers = tools["fin.peers"].fn(ctx, {"measure": "receivables_vs_sales"})
        assert peers["p10"] <= peers["p50"] <= peers["p90"] and peers["more_concerning_than_share"] >= ELEVATED and peers["concerning_side"] == "high"
        assert len(tools["fin.quarters"].fn(ctx, {"last": 2})["quarters"]) == 2

    def test_bad_arguments_and_unknown_companies_are_tool_errors(self, field_db, tmp_path) -> None:  # noqa: ANN001
        store, tools = ForensicStore(tmp_path / "f.duckdb"), self.tools(field_db)
        with pytest.raises(ToolError, match="unknown lines"):
            tools["fin.table"].fn(self.ctx(store), {"lines": ["bogus"]})
        with pytest.raises(ToolError, match="unknown measure"):
            tools["fin.peers"].fn(self.ctx(store), {"measure": "bogus"})
        with pytest.raises(ToolError, match="not an industrial company"):
            tools["fin.profile"].fn(self.ctx(store, "BANKISH"), {})

    def test_the_universe_is_loaded_once_per_date(self, field_db, tmp_path) -> None:  # noqa: ANN001
        opened = []

        def conn():  # noqa: ANN202
            opened.append(1)
            return duckdb.connect(str(field_db), read_only=True)

        tools = {t.name: t for t in statement_tools(StatementsData(conn))}
        store = ForensicStore(tmp_path / "f.duckdb")
        ctx = self.ctx(store)
        for name in ("fin.profile", "fin.detect", "fin.quarters"):
            tools[name].fn(ctx, {})
        assert len(opened) == 1


class Script:
    def __init__(self, replies):  # noqa: ANN001
        self.replies, self.seen = list(replies), []

    def complete(self, system: str, user: str) -> str:
        self.seen.append(user)
        if not self.replies:
            raise AthenaError("script used up")
        r = self.replies.pop(0)
        return r if isinstance(r, str) else json.dumps(r)


def test_a_supervised_run_of_the_lane_cites_the_detector_and_the_peer_table(field_db, tmp_path) -> None:  # noqa: ANN001
    store = ForensicStore(tmp_path / "forensic.duckdb")
    registry = base_registry(statement_tools(StatementsData(connection(field_db))))
    flag = {"action": "write", "kind": "FLAG", "flag_type": "anomaly_vs_peers", "text": "receivables grew far faster than sales and faster than almost every peer", "evidence": ["E1", "E2"],
            "innocent": ["longer credit terms to win a large customer"], "values": {"worse_than": 1.0}}
    llm = Script([{"action": "tool", "tool": "fin.detect", "args": {}}, {"action": "tool", "tool": "fin.peers", "args": {"measure": "receivables_vs_sales"}}, {"action": "write", "kind": "OBSERVATION", "text": "x", "evidence": ["E1"], "values": {"elevated_measures": 99}},
                  flag, {"action": "finish", "summary": "looked at the detectors"}, "the lane found one single-source difference"])
    report = Supervisor(store, registry, {"statements": STATEMENTS_LANE}, llm, total_calls=10, max_rounds=1).investigate(Case("HIGHREC-1", "listed", "HIGHREC", AS_OF))
    flags = report["single_source"]
    assert report["lanes"]["statements"][0]["rejected"] == 1  # the invented count was caught
    assert len(flags) == 1 and flags[0]["type"] == "anomaly_vs_peers" and [r["tool"] for r in flags[0]["references"]] == ["fin.detect", "fin.peers"]
    assert report["concern"]["established"] == 0 and report["concern"]["high"] != "none established"  # one source is shown, not counted
    assert {n["lane"] for n in report["not_examined"]} == {"ownership", "market", "text"}  # the other lanes are not built yet, and the report says so
    assert "fin.detect" in llm.seen[1]  # what it already looked at is shown back to it
