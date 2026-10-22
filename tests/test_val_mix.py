from datetime import date

import pytest

from athena.reference.store import ReferenceStore, StatRow
from athena.valuation import mix
from athena.valuation.inputs import CompanyInputs

DAY = date(2026, 10, 22)
CR = 1e7


def reference() -> ReferenceStore:
    store = ReferenceStore(":memory:")
    rows = []
    for industry, beta, margin, growth, pb, s2c in (
        ("Oil/Gas (Integrated)", 1.0, 0.10, 0.06, 1.5, 1.2),
        ("Retail (General)", 0.8, 0.05, 0.12, 4.0, 3.0),
        ("Financial Svcs. (Non-bank & Insurance)", 0.6, 0.30, 0.10, 2.0, 1.0),
        ("Auto & Truck", 1.1, 0.08, 0.09, 3.0, 2.0),
    ):
        for dataset, metric, value in (
            ("beta", "unlevered_beta_corrected_for_cash", beta), ("margin", "pre_tax_unadjusted_operating_margin", margin),
            ("histgr", "expected_growth_in_revenues_next_5_years", growth), ("pbv", "pbv", pb), ("capex", "sales_invested_capital_ltm", s2c),
        ):
            rows.append(StatRow("damodaran", "India", dataset, industry, metric, value, 50, date(2026, 1, 5), f"{dataset}India.xls"))
    store.put_stats(rows)
    return store


class Fund:
    def __init__(self, end, rows):
        self._got = (end, rows)

    def segments(self, symbol, basis):
        return self._got


def seg(name, revenue, profit, assets=0.0, liabilities=0.0):
    return {"idx": 1, "name": name, "revenue": revenue * CR, "profit": profit * CR, "assets": assets * CR, "liabilities": liabilities * CR}


def company(**over) -> CompanyInputs:
    base = dict(
        symbol="GRP", basis="consolidated", layout="indas", period_end=date(2026, 6, 30), flow_basis="TTM", price=100.0, shares=1e8, revenue=1000.0, operating_profit=200.0,
        depreciation=50.0, ebit=150.0, interest=30.0, pbt=130.0, tax=30.0, net_profit=100.0, effective_tax_rate=0.25, revenue_cagr_3y=0.10, book_equity=800.0,
        borrowings=400.0, cash_and_bank=50.0, investments=20.0, minority=0.0, capex=60.0, market_cap=1000.0, invested_capital=1150.0, missing=(),
    )
    base.update(over)
    return CompanyInputs(**base)


def test_segment_names_are_read_by_the_owners_reading_first_then_by_the_written_rules():
    assert mix.classify("Oil to Chemicals (O2C)") == mix.Reading("operating", "Oil/Gas (Integrated)")
    assert mix.classify("Financial Services") == mix.Reading("financial", "Financial Svcs. (Non-bank & Insurance)")
    assert mix.classify("Investments").kind == "investment" and mix.classify("Treasury").kind == "investment"
    assert mix.classify("Others").kind == "ignored" and mix.classify("Less: Inter-segment assets").kind == "ignored" and mix.classify("Unallocated").kind == "ignored"
    assert mix.classify("Europe").kind == "geography" and mix.classify("Foo Widgets").kind == "unmapped"
    assert mix.classify("Textiles").industry == "Apparel"  # not "tiles"
    assert mix.classify("Farm Equipment").industry == "Machinery"
    assert mix.classify("Foo Widgets", {"foo widgets": "Retail (General)"}) == mix.Reading("operating", "Retail (General)")
    assert mix.classify("Retail", {"retail": "ignore"}).kind == "ignored"
    assert mix.classify("Retail Finance Arm", {"retail finance arm": "Banks (Regional)"}).kind == "financial"


def test_every_industry_the_rules_name_is_one_of_damodarans():
    assert {industry for _, industry, _ in mix.RULES} <= mix.DAMODARAN_INDUSTRIES


def test_two_operating_businesses_blend_the_benchmarks_revenue_weighted_for_operations_and_profit_weighted_for_risk():
    segs = [seg("Oil to Chemicals", 600, 90), seg("Retail", 400, 10), seg("Others", 50, 5)]
    with reference() as ref:
        got = mix.prepare(Fund(date(2026, 3, 31), segs), ref, company(), None, DAY)
    assert dict(got.mix) == pytest.approx({"Oil/Gas (Integrated)": 0.6, "Retail (General)": 0.4})
    ctx = got.industry
    assert ctx.mapping_method == "segment mix" and ctx.industry.startswith("Mix: Oil/Gas (Integrated) 60%, Retail (General) 40%")
    assert ctx.get("operating_margin") == pytest.approx(0.6 * 0.10 + 0.4 * 0.05)  # by revenue
    assert ctx.get("unlevered_beta") == pytest.approx(0.9 * 1.0 + 0.1 * 0.8)  # by profit: 90 and 10
    assert ctx.get("sales_to_capital") == pytest.approx(0.6 * 1.2 + 0.4 * 3.0)
    assert any("business mix from the segments" in f for f in ctx.flags)
    assert got.company == company()  # nothing financial to take out


def test_one_operating_industry_or_poorly_read_segments_leave_the_companys_own_benchmarks():
    with reference() as ref:
        own = mix.ind.for_industry(ref, "Auto & Truck", "nse_ticker", 1.0, DAY)
        one = mix.prepare(Fund(date(2026, 3, 31), [seg("Automotive", 900, 140), seg("Others", 10, 1)]), ref, company(), own, DAY)
        unread = mix.prepare(Fund(date(2026, 3, 31), [seg("Retail", 300, 30), seg("Oil and Gas", 100, 10), seg("Foo", 600, 50), seg("Bar", 100, 5)]), ref, company(), own, DAY)
    assert one.industry is own and one.mix == ()
    assert unread.industry.industry == "Auto & Truck" and any("could not be read well enough" in f for f in unread.industry.flags)


def test_a_financial_arm_is_taken_out_and_its_book_equity_added_at_its_industrys_price_to_book():
    segs = [seg("Automotive", 700, 120, assets=800, liabilities=300), seg("Financing", 250, 60, assets=600, liabilities=450), seg("Others", 10, 0)]
    with reference() as ref:
        own = mix.ind.for_industry(ref, "Auto & Truck", "nse_ticker", 1.0, DAY)
        got = mix.prepare(Fund(date(2026, 3, 31), segs), ref, company(), own, DAY)
    c = got.company
    assert got.carved
    assert c.revenue == pytest.approx(750.0)  # 1000 - 250
    assert c.ebit == pytest.approx(150.0 * 120 / 180)  # the remaining segments' share of segment profit
    assert c.borrowings == 0.0  # the arm's liabilities (450) exceed the debt (400): nothing is left
    assert c.invested_capital == pytest.approx(1150.0 - 600.0)
    assert c.investments == pytest.approx(20.0 + (600 - 450) * 2.0)  # the arm's book equity at the industry's price to book
    assert any("taken out of the operating figures: Financing" in n for n in got.notes)


def test_an_investment_book_is_taken_out_of_the_operating_figures_without_adding_its_assets_a_second_time():
    segs = [seg("Automotive", 900, 120, assets=500), seg("Investments", 20, 18, assets=300)]
    with reference() as ref:
        got = mix.prepare(Fund(date(2026, 3, 31), segs), ref, company(), None, DAY)
    c = got.company
    assert got.carved and c.investments == 20.0 and c.invested_capital == pytest.approx(1150.0 - 300.0) and c.revenue == pytest.approx(980.0)
    assert c.ebit == pytest.approx(150.0 * 120 / 138)


def test_a_small_financial_segment_is_left_in_and_taking_out_too_much_is_refused():
    with reference() as ref:
        small = mix.prepare(Fund(date(2026, 3, 31), [seg("Automotive", 900, 120, assets=1000), seg("Financing", 50, 5, assets=50, liabilities=30)]), ref, company(), None, DAY)
        too_much = mix.prepare(Fund(date(2026, 3, 31), [seg("Automotive", 100, 10, assets=100), seg("Financing", 900, 90, assets=5000, liabilities=4000)]), ref, company(), None, DAY)
    assert not small.carved and small.company == company()
    assert not too_much.carved and too_much.company == company() and any("too little to value" in n for n in too_much.notes)


def test_lenders_no_segments_old_segments_and_geographic_segments_pass_through_unchanged():
    with reference() as ref:
        segs = [seg("Oil to Chemicals", 600, 90), seg("Retail", 400, 10)]
        assert mix.prepare(Fund(date(2026, 3, 31), segs), ref, company(layout="bank"), None, DAY).industry is None
        assert mix.prepare(Fund(None, []), ref, company(), None, DAY).mix == ()
        assert mix.prepare(Fund(date(2024, 3, 31), segs), ref, company(), None, DAY).mix == ()  # older than the latest results by more than 600 days
        assert mix.prepare(Fund(date(2026, 3, 31), [seg("India", 600, 90), seg("Europe", 400, 10)]), ref, company(), None, DAY).mix == ()


def test_the_owners_reading_of_a_segment_changes_the_mix(tmp_path):
    with reference() as ref:
        ref.put_segment_industry("GRP", "Mystery Division", "Retail (General)")
        assert ref.segment_industries("GRP") == {"mystery division": "Retail (General)"}
        segs = [seg("Oil to Chemicals", 500, 50), seg("Mystery Division", 500, 50)]
        got = mix.prepare(Fund(date(2026, 3, 31), segs), ref, company(), None, DAY)
    assert dict(got.mix) == pytest.approx({"Oil/Gas (Integrated)": 0.5, "Retail (General)": 0.5})
