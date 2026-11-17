"""WP04 step 4 (Q4): the valuation study's inputs are the versions available on the study date. A strict study reports a missing historical input and abstains; it never substitutes today's value.
The approximate study (today's inputs applied to a past date) is kept, labelled, and separate. Synthetic stores and fakes only."""
import json
from datetime import date, datetime

import pytest

from athena.evaluation.policy import APPROXIMATE_CURRENT, POLICY, STRICT_DATED
from athena.fundamentals import shares as share_counts
from athena.fundamentals.store import FundamentalsStore
from athena.reference.store import ReferenceStore, StatRow
from athena.valuation import calibrate as c
from athena.valuation import industry as ind
from athena.valuation import manifest

DAY = date(2024, 12, 31)


# ------------------------------------------------------------ the company-to-industry map, versioned
def test_a_company_map_row_is_kept_as_a_version_and_an_earlier_date_reads_the_mapping_that_stood_then():
    with ReferenceStore(":memory:") as ref:
        ref.put_company_industries([("ABC", "Software", "keyword", 0.9, date(2022, 1, 1))])
        ref.put_company_industries([("ABC", "Telecom", "owner", 1.0, date(2025, 6, 1))])  # the current map now says something else
        assert ref.company_industry("ABC") == ("Telecom", "owner", 1.0)  # the current-page lookup is unchanged
        assert ref.company_industry_as_of("ABC", date(2021, 6, 1)) is None  # nothing was mapped yet: unavailable, not today's mapping
        assert ref.company_industry_as_of("ABC", date(2024, 12, 31)) == ("Software", "keyword", 0.9, date(2022, 1, 1))
        assert ref.company_industry_as_of("ABC", date(2025, 12, 31)) == ("Telecom", "owner", 1.0, date(2025, 6, 1))


def test_seeding_the_versions_from_a_map_that_had_none_is_labelled_and_idempotent():
    with ReferenceStore(":memory:") as ref:
        ref._con.execute("INSERT INTO ref_company_industry VALUES ('ABC', 'Software', 'keyword', 0.9, DATE '2026-03-01')")  # a map written before versions existed
        assert ref.seed_company_industry_versions() == 1 and ref.seed_company_industry_versions() == 0
        assert ref.company_industry_as_of("ABC", date(2025, 1, 1)) is None  # the mapping is dated when it was made: a 2025 study cannot read a 2026 classification
        assert ref.company_industry_as_of("ABC", date(2026, 4, 1))[0] == "Software"


def stats(ref, as_of):
    ref.put_stats([StatRow("damodaran", "India", "betas", "Software", "unlevered_beta", 1.1, 40, as_of, "f.xlsx")])


def test_the_industry_lookup_as_of_names_why_it_found_nothing():
    with ReferenceStore(":memory:") as ref:
        ref.put_company_industries([("ABC", "Software", "keyword", 0.9, date(2022, 1, 1))])
        context, why = ind.lookup_as_of(ref, "ABC", DAY)
        assert context is None and "no industry tables" in why
        stats(ref, date(2024, 1, 1))
        context, why = ind.lookup_as_of(ref, "ABC", DAY)
        assert context is not None and why == "" and context.industry == "Software" and context.data_as_of == date(2024, 1, 1)
        context, why = ind.lookup_as_of(ref, "ZZZ", DAY)
        assert context is None and "no industry mapping" in why
        context, why = ind.lookup_as_of(ref, "ABC", date(2021, 1, 1))
        assert context is None and "no industry mapping" in why  # mapped only from 2022


# ------------------------------------------------------------ dated share counts
def counts():
    return [  # newest period first, as FundamentalsStore.share_counts gives them
        {"url": "u25", "basis": "consolidated", "period_end": date(2025, 3, 31), "filed": datetime(2025, 5, 1), "declared": 1500.0, "from_profit": 1500.0},
        {"url": "u24", "basis": "consolidated", "period_end": date(2024, 3, 31), "filed": datetime(2024, 5, 1), "declared": 1000.0, "from_profit": 1000.0},
    ]


def test_the_share_count_as_of_a_date_is_the_latest_filing_by_then():
    assert share_counts.choose_as_of(counts(), datetime(2024, 12, 31)).shares == 1000.0
    assert share_counts.choose_as_of(counts(), datetime(2025, 12, 31)).shares == 1500.0
    assert share_counts.choose_as_of(counts(), datetime(2023, 12, 31)).shares is None  # nothing filed yet


class FundFake:
    """What AsOfFundamentals needs of a store: share counts, a company row and (for the legacy reader) the lines."""

    def __init__(self, share_rows, shares_today=1500.0):
        self._rows, self._today = share_rows, shares_today

    def share_counts(self, symbol):
        return list(self._rows)

    def company(self, symbol):
        return {"symbol": symbol, "shares": self._today, "layout": "indas"}

    def has_versions(self):
        return True


def strict(fund, cutoff, actions=None, factor=None):
    return c.AsOfFundamentals(fund, cutoff, factor or {}, policy="vintage", inputs=STRICT_DATED, actions=actions or {}, day=cutoff.date())


def test_a_strict_study_uses_the_share_count_filed_by_the_date_not_todays_count_divided_by_later_splits():
    fund = FundFake(counts())  # today's count is 1,500, after a placement the actions list does not show
    legacy = c.AsOfFundamentals(fund, datetime(2024, 12, 31), {"ABC": 1.0}, policy="latest_filtered")
    assert legacy.company("ABC")["shares"] == 1500.0  # the approximation: today's count (the defect for a company that placed shares)
    dated = strict(fund, datetime(2024, 12, 31))
    assert dated.company("ABC")["shares"] == 1000.0
    version = dated.share_versions["ABC"]
    assert version["period_end"] == date(2024, 3, 31) and version["filed"] == datetime(2024, 5, 1) and version["adjustment"] == 1.0 and version["how"] == "declared"


def test_a_missing_historical_share_count_is_reported_and_never_replaced_by_todays():
    dated = strict(FundFake(counts()), datetime(2023, 12, 31))
    assert dated.company("ABC")["shares"] is None
    assert dated.share_status("ABC") == "no share count had been filed by 2023-12-31"
    assert "ABC" not in dated.share_versions


def test_a_split_after_the_filing_scales_the_dated_count_and_an_unreadable_action_makes_it_unavailable():
    actions = {"ABC": [(date(2024, 8, 1), "split", 2.0)]}
    assert strict(FundFake(counts()), datetime(2024, 12, 31), actions).company("ABC")["shares"] == 2000.0  # 1,000 at 31 March, doubled by the split on 1 August
    assert strict(FundFake(counts()), datetime(2024, 6, 30), actions).company("ABC")["shares"] == 1000.0  # the split is after the date: not used
    bad = {"ABC": [(date(2024, 8, 1), "demerger", None)]}
    dated = strict(FundFake(counts()), datetime(2024, 12, 31), bad)
    assert dated.company("ABC")["shares"] is None and "cannot be adjusted" in dated.share_status("ABC")


def test_a_strict_reader_needs_the_dated_arguments_and_the_approximate_reader_still_works_without_them():
    with pytest.raises(ValueError, match="actions"):
        c.AsOfFundamentals(FundFake(counts()), datetime(2024, 12, 31), {}, policy="vintage", inputs=STRICT_DATED)
    assert c.AsOfFundamentals(FundFake(counts()), datetime(2024, 12, 31), {"ABC": 1.0}, policy="latest_filtered").inputs == APPROXIMATE_CURRENT


# ------------------------------------------------------------ the exact input versions, recorded
def test_the_input_manifest_records_the_version_of_every_input_used_and_names_the_missing_ones():
    with ReferenceStore(":memory:") as ref:
        ref.put_input("risk_free_inr", date(2020, 1, 1), 0.06, "rbi")
        ref.put_input("risk_free_inr", date(2025, 6, 1), 0.07, "rbi")  # after the study date: must not be read
        stats(ref, date(2024, 1, 1))
        ref.put_country_premiums([("India", "Asia", "Baa3", 0.02, 0.07, 0.025, date(2024, 2, 1))])
        got = manifest.input_manifest(ref, DAY, required=("risk_free_inr", "equity_premium"), policy=STRICT_DATED)
    assert got["policy"] == STRICT_DATED and got["study_policy"] == POLICY.fingerprint() and got["day"] == "2024-12-31"
    assert got["inputs"]["risk_free_inr"] == {"as_of": "2020-01-01", "source": "rbi", "value": 0.06}
    assert got["inputs"]["equity_premium"] is None and got["missing"] == ["equity_premium"]
    assert got["country_premium"]["India"]["as_of"] == "2024-02-01" and got["country_premium"]["United States"] is None
    assert got["industry_tables_as_of"] == "2024-01-01"
    json.dumps(got)  # it goes into the result file as it is


def test_the_approximate_manifest_says_it_used_todays_values():
    with ReferenceStore(":memory:") as ref:
        got = manifest.input_manifest(ref, DAY, required=(), policy=APPROXIMATE_CURRENT, today=date(2026, 11, 14))
    assert got["policy"] == APPROXIMATE_CURRENT and got["read_as_of"] == "2026-11-14" and "retrospective sensitivity" in got["label"]


# ------------------------------------------------------------ the snapshot: strict and approximate, side by side on the same synthetic market
def market(tmp_path):
    """Five companies on 31 December 2024 and a six-month window to 30 June 2025: OK (ordinary), TCS (a demerger after the date), STP (stops trading in January), NOSHARE (no share count filed by the date)
    and NOMAP (no industry mapping dated by the date)."""
    import dataclasses
    from pathlib import Path

    import duckdb

    fixtures = Path(__file__).parent / "fixtures" / "xbrl"
    base = __import__("athena.fundamentals.rows", fromlist=["parse_rows"]).parse_rows((fixtures / "tcs_fy2024.xml").read_text(encoding="utf-8"))
    fund = FundamentalsStore(":memory:")
    for symbol in ("OK", "TCS", "STP", "NOSHARE", "NOMAP"):
        st = dataclasses.replace(base, symbol=symbol)
        fund.save_statements(st, f"u-{symbol}", datetime(2024, 5, 1))
        fund.save_company(symbol, f"{symbol} Ltd", "INE", "IT", st.layout, 9.9e9)  # today's count: deliberately different from the one filed
        if symbol != "NOSHARE":
            fund.save_share_count(f"u-{symbol}", symbol, "consolidated", date(2024, 3, 31), datetime(2024, 5, 1), 3.6e9, 3.6e9)
    path = tmp_path / "w.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE bars (symbol VARCHAR, day DATE, close DOUBLE)")
    con.execute("CREATE TABLE actions (symbol VARCHAR, ex_date DATE, subject VARCHAR)")
    for symbol in ("OK", "TCS", "STP", "NOSHARE", "NOMAP"):
        con.execute("INSERT INTO bars VALUES (?, DATE '2024-12-31', 100.0)", [symbol])
        if symbol != "STP":
            con.execute("INSERT INTO bars VALUES (?, DATE '2025-06-27', 120.0)", [symbol])
    con.execute("INSERT INTO bars VALUES ('STP', DATE '2025-01-31', 40.0)")
    con.execute("INSERT INTO bars VALUES ('OK', DATE '2025-07-04', 121.0)")  # the last bar the warehouse holds, after the window's end
    con.execute("INSERT INTO actions VALUES ('TCS', DATE '2025-03-01', 'Demerger')")
    con.close()
    ref = ReferenceStore(":memory:")
    ref.put_company_industries([(s, "Software", "keyword", 0.9, date(2022, 1, 1)) for s in ("OK", "TCS", "STP", "NOSHARE")] + [("NOMAP", "Software", "keyword", 0.9, date(2026, 3, 1))])  # NOMAP: mapped only in 2026
    ref.put_stats([StatRow("damodaran", "India", "betas", "Software", "unlevered_beta", 1.1, 40, date(2024, 1, 1), "f.xlsx")])
    return fund, ref, path


def test_a_strict_snapshot_keeps_every_company_accounted_for_reports_what_was_missing_and_does_not_drop_the_restructured_or_the_stopped(tmp_path):
    fund, ref, path = market(tmp_path)
    snap = c.build_snapshot(fund, ref, path, DAY, date(2026, 11, 14), (6,), input_policy=STRICT_DATED)
    by = {r.symbol: r for r in snap.rows}
    assert set(by) == {"OK", "TCS", "STP"} and snap.inputs == STRICT_DATED
    assert by["OK"].outcome[6] == "priced" and by["OK"].forward[6] == pytest.approx(0.2)
    assert by["TCS"].outcome[6] == "restructured" and 6 not in by["TCS"].forward  # the later demerger did not remove it from the cohort: its outcome is unknown
    assert by["STP"].outcome[6] == "no_end_price" and 6 not in by["STP"].forward  # stopped trading: in, with unknown proceeds
    assert snap.missing == {"share count: none filed by the day": ["NOSHARE"], "industry: no company mapping dated by the day": ["NOMAP"]}
    assert len(snap.rows) + sum(snap.excluded.values()) + sum(len(v) for v in snap.missing.values()) == 5  # rows + excluded + abstained = every symbol
    assert by["OK"].company.shares == 3.6e9  # the count filed by the date, not today's 9.9e9
    assert snap.manifest["study_policy"] == POLICY.fingerprint() and snap.manifest["policy"] == STRICT_DATED and snap.manifest["outcomes"][6]["restructured"] == 1


def test_the_approximate_snapshot_is_the_earlier_behaviour_labelled_as_such_and_reports_no_missing_inputs(tmp_path):
    fund, ref, path = market(tmp_path)
    snap = c.build_snapshot(fund, ref, path, DAY, date(2026, 11, 14), (6,), input_policy=APPROXIMATE_CURRENT)
    by = {r.symbol: r for r in snap.rows}
    assert snap.inputs == APPROXIMATE_CURRENT and snap.missing == {} and "retrospective sensitivity" in snap.manifest["label"]
    assert "TCS" not in by and snap.excluded["a demerger, scheme, capital reduction or unreadable split after the day"] == 1  # a future demerger removes it: the convenience the strict study refuses
    assert by["OK"].company.shares == 9.9e9 and by["NOSHARE"].company.shares == 9.9e9  # today's count, applied to a past date
    assert by["STP"].forward == {} and by["STP"].outcome == {}  # silently unmeasured: the approximate study cannot say what happened to it
    assert snap.manifest["read_as_of"] == "2026-11-14"


def test_a_company_stopping_trading_is_counted_in_the_outcome_accounting_and_the_zero_recovery_scenario_is_named(tmp_path):
    fund, ref, path = market(tmp_path)
    snap = c.build_snapshot(fund, ref, path, DAY, date(2026, 11, 14), (6,), input_policy=STRICT_DATED)
    outs = [c.Out(r.symbol, "fcff", 100.0, 80.0, 110.0, 140.0, None, r.forward, 1000.0, "Software", 0.05, 0.5, r.outcome) for r in snap.rows]
    acct = c.outcome_accounting(outs, 6)
    assert acct["priced"] == 1 and acct["restructured"] == 1 and acct["no_end_price"] == 1 and acct["unknown_share"] == pytest.approx(2 / 3)
    filled = c.zero_recovery_sensitivity(outs, 6, min_cap=0.0)
    assert filled["model: ln(base/price)"]["n"] == 2  # OK, and STP at -100%; the restructured name stays out of both ends: its return is unknown
    assert c.forward_metrics(outs, 6, 0.0)["model: ln(base/price)"]["n"] == 1  # the measurable-only result is a different, smaller thing


# ------------------------------------------------------------ abstentions are counted, with the date handed to the valuation
class Fake:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def snapshot_of(symbols):
    rows = tuple(c.Row(s, Fake(symbol=s, price=100.0, market_cap=1000.0, net_profit=10.0, book_equity=100.0), None, {6: 0.1}) for s in symbols)
    return c.Snapshot(DAY, rows, {}, "vintage")


def test_run_variant_hands_the_valuation_the_study_date_in_a_strict_study_and_lists_who_abstained(monkeypatch):
    seen = {}

    def capital(store, company, context, on_date):
        seen.setdefault("capital", []).append(on_date)
        return Fake(ok=company.symbol != "B", missing=("input risk_free_inr",))

    def value(store, company, context, cap, on_date, policy):
        seen.setdefault("value", []).append(on_date)
        if not cap.ok:
            return Fake(ok=False, missing=cap.missing, base=None, low=None, high=None, model="", justified_price_to_book=None)
        return Fake(ok=True, missing=(), base=120.0, low=100.0, high=140.0, model="fcff", justified_price_to_book=None)

    monkeypatch.setattr(c.wacc, "company_cost_of_capital", capital)
    monkeypatch.setattr(c.scenarios, "value_company", value)
    abstained = {}
    outs = c.run_variant(snapshot_of(["A", "B"]), c.Variant("baseline"), ReferenceStore(":memory:"), date(2026, 11, 14), on_date=DAY, abstained=abstained)
    assert [o.symbol for o in outs] == ["A"] and abstained == {"B": ("input risk_free_inr",)}
    assert set(seen["capital"]) == {DAY} and set(seen["value"]) == {DAY}  # the study date, not today's
    seen.clear()
    c.run_variant(snapshot_of(["A"]), c.Variant("baseline"), ReferenceStore(":memory:"), date(2026, 11, 14))
    assert set(seen["capital"]) == {date(2026, 11, 14)}  # the approximate study keeps reading today's inputs: that is what it is, and its label says so
