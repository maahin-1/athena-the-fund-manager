from datetime import date, datetime

from athena.fundamentals import segments
from athena.fundamentals.store import FundamentalsStore

HEAD = '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:in-capmkt="http://www.sebi.gov.in/xbrl/capmkt">'
COMMON = (
    '<in-capmkt:Symbol contextRef="OneD">GROUP</in-capmkt:Symbol>'
    '<in-capmkt:DateOfEndOfReportingPeriod contextRef="OneD">2026-03-31</in-capmkt:DateOfEndOfReportingPeriod>'
    '<in-capmkt:NatureOfReportStandaloneConsolidated contextRef="OneD">Consolidated</in-capmkt:NatureOfReportStandaloneConsolidated>'
)


def filing(kind: str = "Multi segment", n: int = 3) -> str:
    out = [HEAD, COMMON, f'<in-capmkt:IsCompanyReportingMultisegmentOrSingleSegment contextRef="OneD">{kind}</in-capmkt:IsCompanyReportingMultisegmentOrSingleSegment>']
    names = ["Oil to Chemicals", "Retail", "Digital Services", "Others"]
    for i in range(1, n + 1):
        out.append(f'<in-capmkt:DescriptionOfReportableSegment contextRef="FourReportable{i}D">{names[i - 1]}</in-capmkt:DescriptionOfReportableSegment>')
        out.append(f'<in-capmkt:SegmentRevenue contextRef="FourReportable{i}D" decimals="-7" unitRef="INR">{1000 * i}0000000</in-capmkt:SegmentRevenue>')
        out.append(f'<in-capmkt:SegmentRevenue contextRef="OneReportable{i}D" decimals="-7" unitRef="INR">1</in-capmkt:SegmentRevenue>')  # the quarter: not the year
        out.append(f'<in-capmkt:DescriptionOfReportableSegment contextRef="FourReportableFinance{i}D">{names[i - 1]}</in-capmkt:DescriptionOfReportableSegment>')
        out.append(f'<in-capmkt:SegmentProfitLossBeforeTaxAndFinanceCosts contextRef="FourReportableFinance{i}D" decimals="-7" unitRef="INR">{100 * i}0000000</in-capmkt:SegmentProfitLossBeforeTaxAndFinanceCosts>')
        out.append(f'<in-capmkt:SegmentAssets contextRef="OneReportable3{i}I" decimals="-7" unitRef="INR">{5000 * i}0000000</in-capmkt:SegmentAssets>')
        out.append(f'<in-capmkt:SegmentLiabilities contextRef="OneReportable4{i}I" decimals="-7" unitRef="INR">{2000 * i}0000000</in-capmkt:SegmentLiabilities>')
    out.append("</xbrli:xbrl>")
    return "".join(out)


def test_a_multi_segment_filing_gives_each_segment_with_the_years_revenue_profit_assets_and_liabilities():
    got = segments.parse_segments(filing())
    assert got is not None and got.symbol == "GROUP" and got.basis == "consolidated" and got.period_end == date(2026, 3, 31) and got.multi
    assert [s.name for s in got.segments] == ["Oil to Chemicals", "Retail", "Digital Services"]
    first = got.segments[0]
    assert (first.revenue, first.profit, first.assets, first.liabilities) == (1000 * 1e7, 100 * 1e7, 5000 * 1e7, 2000 * 1e7)  # the year's revenue, not the quarter's 1
    assert got.segments[2].revenue == 3000 * 1e7


def test_a_single_segment_filing_or_one_that_is_not_xbrl_gives_no_segments():
    one = segments.parse_segments(filing("Single segment", 1))
    assert one is not None and one.segments == () and not one.multi
    assert segments.parse_segments("<html>an error page") is None
    assert segments.parse_segments(HEAD + "</xbrli:xbrl>") is None  # no period end


def test_the_store_keeps_the_newest_filings_segments_and_replaces_a_filing_it_already_holds(tmp_path):
    got = segments.parse_segments(filing())
    newer = segments.parse_segments(filing(n=2))
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        assert store.segments("GROUP", "consolidated") == (None, [])
        store.save_segments("u1", "GROUP", "consolidated", date(2025, 3, 31), datetime(2025, 5, 1), got.segments)
        store.save_segments("u2", "GROUP", "consolidated", date(2026, 3, 31), datetime(2026, 5, 1), newer.segments)
        store.save_segments("u2", "GROUP", "consolidated", date(2026, 3, 31), datetime(2026, 5, 1), newer.segments)  # again: no duplicates
        end, rows = store.segments("GROUP", "consolidated")
        assert end == date(2026, 3, 31) and [r["name"] for r in rows] == ["Oil to Chemicals", "Retail"]
        assert store.segments("GROUP", "standalone") == (None, [])
