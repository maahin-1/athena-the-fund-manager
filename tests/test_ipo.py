from datetime import date, datetime, timedelta, timezone

import pytest

from athena.contracts import AthenaError
from athena.ipo import collect as col
from athena.ipo import page as pg
from athena.ipo import sources as src
from athena.ipo.store import IpoStore

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 3)

MAIN = {"companyName": "Bombay Coated and Special Steels Limited", "issueEndDate": "02-Nov-2026", "issuePrice": "Rs.78 to Rs.82", "issueSize": "16533012", "issueStartDate": "29-Oct-2026", "series": "EQ", "status": "Active", "symbol": "BCSSL"}
SME = {"companyName": "Vedant Poly Fabs Limited", "issueEndDate": "04-Nov-2026", "issuePrice": "Rs.88 to Rs.94", "issueSize": "6030000", "issueStartDate": "02-Nov-2026", "lotSize": "1200", "priceBand": "Rs.88 to Rs.94", "series": "SME", "status": "Forthcoming", "symbol": "VPFL"}
BIDS = {"bidDetails": [
    {"category": "Qualified Institutional Buyers(QIBs)", "noOfSharesOffered": "4798588", "noOfTime": "2.5", "noOfsharesBid": "11996470", "srNo": "1"},
    {"category": "Foreign Institutional Investors(FIIs)", "noOfSharesOffered": "", "noOfTime": "", "noOfsharesBid": "0", "srNo": "1(a)"},
    {"category": "Non Institutional Investors(Bid amount of more than Ten Lakh Rupees)", "noOfSharesOffered": "2346885", "noOfTime": "0.18", "noOfsharesBid": "428246", "srNo": "2.1"},
    {"category": "Total", "noOfSharesOffered": "16533012", "noOfTime": "1.1", "noOfsharesBid": "18000000", "srNo": "4"},
]}
PAST = {"company": "Paras Healthcare Limited", "ipoEndDate": "23-OCT-2026", "ipoStartDate": "21-OCT-2026", "issuePrice": "   693", "listingDate": "28-OCT-2026", "priceRange": "Rs.660 to Rs.693", "securityType": "EQ", "symbol": "PARASHLTH"}
SEBI_PAGE = """<table><tbody>
<tr role='row' class='odd'>
  <td>Oct 29, 2026</td>
  <td><a href="https://www.sebi.gov.in/filings/public-issues/oct-2026/carlsberg-india-limited-udrhp-1_105485.html"  target="_blank" title="Carlsberg India Limited- UDRHP-1 <br><a href='https://www.sebi.gov.in/sebi_data/commondocs/oct-2026/Carlsberg%20India%20Limited_DA_p.pdf' style=color:#000000>Carlsberg India Limited - Draft Abridged Prospectus</a>" class="points"> Carlsberg India Limited- UDRHP-1 </a>
</tr>
<tr role='row' class='odd'>
  <td>Oct 26, 2026</td>
  <td><a href="https://www.sebi.gov.in/filings/public-issues/oct-2026/oravel-stays-limited-addendum-to-drhp_105418.html"  target="_blank" title="Oravel Stays Limited -  Addendum to DRHP" class="points"> Oravel Stays Limited -  Addendum to DRHP</a>
</tr>
<tr role='row' class='odd'>
  <td>Oct 21, 2026</td>
  <td><a href="https://www.sebi.gov.in/filings/public-issues/oct-2026/gamp-services-limited-drhp_105424.html"  target="_blank" title="GAMP Services Limited - DRHP" class="points"> GAMP Services Limited - DRHP</a>
</tr></tbody></table>"""


def test_a_price_band_is_read_as_low_and_high_and_an_odd_one_is_not_guessed():
    assert src.price_band("Rs.78 to Rs.82") == (78.0, 82.0) and src.price_band("   693") == (693.0, 693.0) and src.price_band("Rs.1,585 to Rs.1,665") == (1585.0, 1665.0)
    assert src.price_band("") == (None, None) and src.price_band("Rs.90 to Rs.80") == (None, None) and src.price_band(None) == (None, None)


def test_the_calendar_gives_issues_with_kind_status_and_shares_and_skips_rows_it_cannot_place():
    got = src.parse_issues([MAIN, SME, {"symbol": "", "issueStartDate": "01-Nov-2026", "issueEndDate": "02-Nov-2026"}, {"symbol": "NODATE", "issueStartDate": "x", "issueEndDate": "y"}])
    assert [(i.symbol, i.kind, i.status, i.price_low, i.price_high, i.shares, i.lot) for i in got] == [("BCSSL", "mainboard", "Active", 78.0, 82.0, 16533012.0, None), ("VPFL", "sme", "Forthcoming", 88.0, 94.0, 6030000.0, 1200)]
    assert src.parse_issues({}) == []  # the exchange answers `{}` for a category with nothing in it
    with pytest.raises(AthenaError):
        src.parse_issues({"data": "x"})


def test_subscription_and_listing_rows_are_read():
    bids = src.parse_subscription(BIDS)
    assert [(b.sr, b.times) for b in bids] == [("1", 2.5), ("1(a)", None), ("2.1", 0.18), ("4", 1.1)]
    with pytest.raises(AthenaError):
        src.parse_subscription({"nope": 1})
    (l,) = src.parse_past([PAST])  # noqa: E741
    assert (l.symbol, l.issue_price, l.band_high, l.listing, l.start) == ("PARASHLTH", 693.0, 693.0, date(2026, 10, 28), date(2026, 10, 21))


def test_sebi_filings_give_company_kind_date_and_the_document_named_in_the_listing():
    got = src.parse_sebi(SEBI_PAGE)
    assert [(f.company, f.kind, f.filed.isoformat()) for f in got] == [("Carlsberg India Limited", "UDRHP", "2026-10-29"), ("Oravel Stays Limited", "Addendum", "2026-10-26"), ("GAMP Services Limited", "DRHP", "2026-10-21")]
    assert got[0].pdfs == ("https://www.sebi.gov.in/sebi_data/commondocs/oct-2026/Carlsberg%20India%20Limited_DA_p.pdf",) and got[1].pdfs == ()
    with pytest.raises(AthenaError):
        src.parse_sebi("<html>blocked</html>")  # a page with no row at all is a failure, not an empty week


def test_filing_kinds_and_company_names_from_titles():
    assert [src.filing_kind(t) for t in ("X Limited - DRHP", "X - UDRHP-1", "X - Addendum to the DRHP", "X - RHP", "X Notice to Investors", "X - Prospectus", "X something")] == ["DRHP", "UDRHP", "Addendum", "RHP", "Notice", "Prospectus", "Other"]
    assert src.company_of("Oravel Stays Limited -  Addendum to DRHP") == "Oravel Stays Limited" and src.company_of("Pentacle Consultants (I) Limited - DRHP") == "Pentacle Consultants (I) Limited"
    assert src.company_of("Some Trust - DRHP") == "Some Trust"


def test_a_filing_page_shows_its_document_through_the_viewer_link():
    page = "<iframe src='../../../web/?file=https://www.sebi.gov.in/sebi_data/attachdocs/oct-2026/1792987300020.pdf'></iframe>"
    assert src.pdf_of_filing_page(page) == "https://www.sebi.gov.in/sebi_data/attachdocs/oct-2026/1792987300020.pdf"
    assert src.pdf_of_filing_page("<p>none</p>") == ""


# --- store ---
def test_a_later_read_updates_the_status_but_does_not_erase_a_figure_another_endpoint_left_out(tmp_path):
    store = IpoStore(tmp_path / "i.duckdb")
    assert store.upsert_issues(src.parse_issues([SME]), NOW) == 1
    current = {**SME, "status": "Active"}
    for k in ("issuePrice", "priceBand", "issueSize", "lotSize"):
        current.pop(k)
    assert store.upsert_issues(src.parse_issues([current]), NOW + timedelta(hours=2)) == 0
    (row,) = store.issues(date(2026, 10, 1))
    assert row["status"] == "Active" and row["price_high"] == 94.0 and row["lot"] == 1200 and row["shares"] == 6030000.0 and row["first_seen_at"] == datetime(2026, 11, 3, 9, 0)


def test_a_subscription_snapshot_is_kept_only_when_it_changed_and_never_when_empty(tmp_path):
    store = IpoStore(tmp_path / "i.duckdb")
    bids = src.parse_subscription(BIDS)
    assert store.add_subscription("BCSSL", bids, NOW) is True
    assert store.add_subscription("BCSSL", bids, NOW + timedelta(hours=1)) is False
    changed = src.parse_subscription({"bidDetails": [{**BIDS["bidDetails"][0], "noOfTime": "3.0"}, *BIDS["bidDetails"][1:]]})
    assert store.add_subscription("BCSSL", changed, NOW + timedelta(hours=2)) is True
    empty = src.parse_subscription({"bidDetails": [{"category": "QIB", "noOfSharesOffered": "", "noOfTime": "", "noOfsharesBid": "", "srNo": "1"}]})
    assert store.add_subscription("X", empty, NOW) is False and store.subscription("X") is None
    latest = store.subscription("BCSSL")
    assert latest["snapshots"] == 2 and latest["rows"][0]["times"] == 3.0


def test_filings_are_kept_once_and_the_ones_without_a_document_are_found(tmp_path):
    store = IpoStore(tmp_path / "i.duckdb")
    filings = src.parse_sebi(SEBI_PAGE)
    assert len(store.add_filings(filings, NOW)) == 3 and store.add_filings(filings, NOW) == []
    missing = store.filings_without_pdf()
    assert len(missing) == 2
    store.set_pdf(missing[0], "https://www.sebi.gov.in/sebi_data/attachdocs/oct-2026/1.pdf")
    store.set_pdf(missing[0], "https://www.sebi.gov.in/other.pdf")  # a document already found is not replaced
    assert len(store.filings_without_pdf()) == 1


# --- collect ---
def fake_nse(replies):
    def get(url, params=None):
        reply = replies[url]
        if isinstance(reply, Exception):
            raise reply
        return reply

    return get


def test_a_pass_keeps_what_each_source_gave_and_one_failing_source_does_not_stop_the_others(tmp_path):
    store = IpoStore(tmp_path / "i.duckdb")
    nse = fake_nse({src.NSE_UPCOMING: [SME], src.NSE_CURRENT: [MAIN], src.NSE_DETAIL: BIDS, src.NSE_PAST: AthenaError("HTTP 403")})
    pages = {src.SEBI_LISTING: SEBI_PAGE.encode()}

    def page(url):
        if url in pages:
            return pages[url]
        return b"<iframe src='x?file=https://www.sebi.gov.in/sebi_data/attachdocs/oct-2026/9.pdf'></iframe>"

    out = col.collect(store, nse, page, NOW, sleep=lambda s: None, log=lambda s: None)
    assert out == {"nse_upcoming": ("ok", 1), "nse_subscription": ("ok", 1), "nse_listed": ("failed", 0), "sebi_drafts": ("ok", 3)}
    health = store.health()
    assert health["nse_listed"]["status"] == "failed" and "403" in health["nse_listed"]["error"] and health["sebi_drafts"]["status"] == "ok"
    assert store.subscription("BCSSL")["snapshots"] == 1
    assert all(f["pdf"].startswith("https://www.sebi.gov.in/") for f in store.filings(date(2026, 1, 1)))


# --- page ---
def test_company_names_match_with_or_without_limited_and_a_longer_name_that_starts_the_same():
    assert pg.same_company("Oravel Stays Limited", "ORAVEL STAYS LTD.") and pg.same_company("Oravel Stays Limited", "Oravel Stays India Limited")
    assert not pg.same_company("Tata Motors Limited", "Tata Steel Limited") and not pg.same_company("Tata Limited", "Tata Motors Limited")  # one shared word is not a match
    assert not pg.same_company("", "X")


def test_the_page_sorts_issues_by_status_attaches_drafts_and_leaves_unmatched_drafts_on_their_own(tmp_path):
    store = IpoStore(tmp_path / "i.duckdb")
    store.upsert_issues(src.parse_issues([MAIN, SME, {**MAIN, "symbol": "OLD", "companyName": "Old Issue Limited", "issueStartDate": "01-Sep-2026", "issueEndDate": "04-Sep-2026", "status": "Closed"}]), NOW)
    store.add_subscription("BCSSL", src.parse_subscription(BIDS), NOW)
    store.upsert_listed(src.parse_past([PAST]), NOW)
    store.add_filings(src.parse_sebi(SEBI_PAGE) + [src.Filing("https://www.sebi.gov.in/filings/x.html", "Vedant Poly Fabs Limited", "DRHP", date(2026, 9, 1), "Vedant Poly Fabs Limited - DRHP", ())], NOW)
    doc = pg.build_page(store, TODAY)
    assert [i["symbol"] for i in doc["open"]] == ["BCSSL"] and [i["symbol"] for i in doc["upcoming"]] == ["VPFL"] and doc["closed"] == []  # the old closed issue is too old to show
    bcssl = doc["open"][0]
    assert bcssl["size_cr"] == round(16533012 * 82 / 1e7, 1) and bcssl["subscription"]["total_times"] == 1.1
    assert [r["level"] for r in bcssl["subscription"]["rows"]] == [0, 1, 0] and "1(a)" not in str(bcssl["subscription"]["rows"])  # sub-sub rows are left out
    vpfl = doc["upcoming"][0]
    assert vpfl["min_application"] == 1200 * 94 and [f["kind"] for f in vpfl["filings"]] == ["DRHP"]
    assert [f["company"] for f in doc["filed"]] == ["Carlsberg India Limited", "Oravel Stays Limited", "GAMP Services Limited"]  # the matched draft is under its issue, not repeated
    assert doc["listed"][0]["symbol"] == "PARASHLTH" and doc["caveat"].endswith("Not investment advice.")
    assert {s["id"]: s["status"] for s in doc["sources"]}["sebi_drafts"] == "never tried"


def test_a_listing_gain_is_worked_out_from_the_issue_price_and_our_bars_and_is_null_without_them():
    row = {"symbol": "X", "name": "X Limited", "series": "EQ", "issue_price": 100.0, "band_high": 100.0, "listing": date(2026, 10, 28)}
    bars = [(date(2026, 10, 28), 120.0, 110.0), (date(2026, 10, 29), 110.0, 90.0)]
    v = pg.listed_view(row, bars, [])
    assert (v["listing_open"], v["listing_pct"], v["first_close_pct"], v["last_close"], v["last_pct"]) == (120.0, 20.0, 10.0, 90.0, -10.0)
    late = pg.listed_view(row, [(date(2026, 10, 29), 110.0, 90.0)], [])
    assert late["listing_pct"] is None and late["last_pct"] == -10.0  # the first bar is not the listing day: no listing figure is claimed
    none = pg.listed_view({**row, "issue_price": None}, bars, [])
    assert none["listing_pct"] is None and pg.listed_view(row, [], [])["last_close"] is None
