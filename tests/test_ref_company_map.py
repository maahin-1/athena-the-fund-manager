from datetime import date

import openpyxl

from athena.reference import company_map as m


def row(name, ticker, industry):
    return m.IndustryRow(name, ticker, industry, "India")


def test_names_are_written_one_way_before_they_are_compared():
    assert m.normal_name("Tata Consultancy Services Limited (NSEI:TCS)") == "tata consultancy services"
    assert m.normal_name("Larsen & Toubro Ltd.") == "larsen and toubro"
    assert m.normal_name("Larsen and Toubro Limited") == "larsen and toubro"
    assert m.normal_name("") == ""


def test_a_company_is_joined_by_ticker_first_then_by_name_and_a_shared_name_is_not_a_match():
    damodaran = [
        row("Tata Consultancy Services Limited (NSEI:TCS)", "NSEI:TCS", "Computer Services"),
        row("ABB India Limited (BSE:500002)", "BSE:500002", "Electrical Equipment"),
        row("Twin Name Ltd (BSE:1)", "BSE:1", "Steel"),
        row("Twin Name Limited (BSE:2)", "BSE:2", "Chemical (Basic)"),
    ]
    companies = [m.Company("TCS", "Tata Consultancy Services Ltd", ""), m.Company("ABB", "ABB India Ltd.", ""), m.Company("TWIN", "Twin Name Ltd", ""), m.Company("NONE", "Nobody", "")]
    mapped = {r.symbol: r for r in m.build_company_map(damodaran, companies)}
    assert (mapped["TCS"].industry, mapped["TCS"].method, mapped["TCS"].confidence) == ("Computer Services", "nse_ticker", 1.0)
    assert (mapped["ABB"].industry, mapped["ABB"].method, mapped["ABB"].confidence) == ("Electrical Equipment", "name", 0.9)
    assert "TWIN" not in mapped and "NONE" not in mapped


def test_an_nse_industry_label_is_carried_to_unmatched_companies_only_when_matched_ones_agree():
    damodaran = [row(f"Steel Co {i} Ltd", f"NSEI:S{i}", "Steel") for i in range(6)] + [row(f"Mixed {i} Ltd", f"NSEI:M{i}", ("Steel", "Retail", "Chemical")[i % 3]) for i in range(6)]
    matched = [m.Company(f"S{i}", f"Steel Co {i} Ltd", "Metals") for i in range(6)] + [m.Company(f"M{i}", f"Mixed {i} Ltd", "Misc") for i in range(6)]
    unmatched = [m.Company("NEWSTEEL", "Brand New Steel", "Metals"), m.Company("NEWMIX", "Brand New Mix", "Misc"), m.Company("NOLABEL", "Brand New Other", "")]
    mapped = {r.symbol: r for r in m.build_company_map(damodaran, matched + unmatched)}
    assert (mapped["NEWSTEEL"].industry, mapped["NEWSTEEL"].method, mapped["NEWSTEEL"].confidence) == ("Steel", "crosswalk", 0.8)
    assert "NEWMIX" not in mapped  # 4 of 6 agree is 0.67, so it is carried... checked below


def test_the_crosswalk_needs_enough_matched_companies():
    damodaran = [row(f"A{i} Ltd", f"NSEI:A{i}", "Steel") for i in range(3)]
    companies = [m.Company(f"A{i}", f"A{i} Ltd", "Metals") for i in range(3)] + [m.Company("X", "Unknown", "Metals")]
    assert {r.symbol for r in m.build_company_map(damodaran, companies)} == {"A0", "A1", "A2"}  # three matched examples are too few to trust a label


def test_the_audit_counts_each_pass_and_lists_what_was_left():
    companies = [m.Company("A", "A", "L1"), m.Company("B", "B", "L1"), m.Company("C", "C", "")]
    mapped = [m.MapRow("A", "Steel", "nse_ticker", 1.0), m.MapRow("B", "Steel", "crosswalk", 0.4)]
    report = m.audit(companies, mapped)
    assert report["mapped"] == 2 and report["unmapped"] == 1 and report["low_confidence"] == 1
    assert report["by_method"] == {"nse_ticker": 1, "crosswalk": 1}


def test_the_industry_rows_are_read_for_one_country(tmp_path):
    book = openpyxl.Workbook()
    ws = book.active
    ws.title = "By industry"
    ws.append(["Company Name", "Exchange:Ticker", "Industry Group", "Primary Sector", "SIC Code", "Country"])
    ws.append(["Tata Consultancy Services Limited (NSEI:TCS)", "NSEI:TCS", "Computer Services", "IT", "7370", "India"])
    ws.append(["Apple Inc. (NasdaqGS:AAPL)", "NasdaqGS:AAPL", "Computers/Peripherals", "IT", "3571", "United States"])
    path = tmp_path / "indname.xlsx"
    book.save(path)
    assert m.read_industry_rows(path) == [m.IndustryRow("Tata Consultancy Services Limited (NSEI:TCS)", "NSEI:TCS", "Computer Services", "India")]
    assert m.as_rows([m.MapRow("TCS", "Computer Services", "nse_ticker", 1.0)], date(2026, 1, 5)) == [("TCS", "Computer Services", "nse_ticker", 1.0, date(2026, 1, 5))]
