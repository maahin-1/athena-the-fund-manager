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


def test_a_name_the_exchange_abbreviates_is_matched_to_exactly_one_listed_name_and_never_to_two():
    damodaran = [
        row("Berger Paints India Limited (BSE:509480)", "BSE:509480", "Chemical (Basic)"),
        row("Banco Products India Limited (BSE:1)", "BSE:1", "Auto Parts"),
        row("Shared Name India Limited (BSE:2)", "BSE:2", "Steel"),
        row("Shared Name India Ltd (BSE:3)", "BSE:3", "Retail"),
    ]
    companies = [
        m.Company("BERGEPAINT", "Berger Paints (I) Limited", ""), m.Company("BANCOINDIA", "Banco Products (I) Limited", ""), m.Company("SHARED", "Shared Name (I) Limited", ""),
        m.Company("ONEWORD", "Berger", ""), m.Company("OTHER", "Berger Textiles Limited", ""),
    ]
    mapped = {r.symbol: r for r in m.build_company_map(damodaran, companies)}
    assert (mapped["BERGEPAINT"].industry, mapped["BERGEPAINT"].method, mapped["BERGEPAINT"].confidence) == ("Chemical (Basic)", "similar", 0.7)
    assert mapped["BANCOINDIA"].industry == "Auto Parts"
    assert "SHARED" not in mapped  # two industries share the name
    assert "ONEWORD" not in mapped  # one word is no basis
    assert "OTHER" not in mapped  # half the words match, not four fifths


def test_abbreviations_are_spelled_out_when_names_are_compared():
    assert m.name_words("Banco Products (I) Limited") == frozenset({"banco", "products", "india"})
    assert m.name_words("Black Rose Inds. Limited") == frozenset({"black", "rose", "industries"})


def test_a_name_word_that_sits_mostly_in_one_industry_places_a_company_nothing_else_reached():
    damodaran = [row(f"Alpha{i} Hotels Ltd", f"NSEI:H{i}", "Hotel/Gaming") for i in range(9)] + [row("Beta Hotels Ltd", "NSEI:HX", "Retail")] + [row(f"Gamma{i} Cement Ltd", f"NSEI:C{i}", "Construction Supplies") for i in range(4)]
    mapped_companies = [m.Company(f"H{i}", f"Alpha{i} Hotels Ltd", "") for i in range(9)] + [m.Company("HX", "Beta Hotels Ltd", "")] + [m.Company(f"C{i}", f"Gamma{i} Cement Ltd", "") for i in range(4)]
    new = [m.Company("NEWHOTEL", "Zeta Hotels and Resorts Limited", ""), m.Company("NEWCEMENT", "Zeta Cement Limited", ""), m.Company("NOWORD", "Zeta Unrelated Limited", "")]
    mapped = {r.symbol: r for r in m.build_company_map(damodaran, mapped_companies + new)}
    assert (mapped["NEWHOTEL"].industry, mapped["NEWHOTEL"].method, mapped["NEWHOTEL"].confidence) == ("Hotel/Gaming", "words", 0.63)  # 9 of 10 companies with the word, x 0.7
    assert "NEWCEMENT" not in mapped  # four companies are too few for a word to vote
    assert "NOWORD" not in mapped


def test_a_word_split_across_industries_does_not_vote_and_a_company_already_mapped_is_left_alone():
    damodaran = [row(f"Bank{i} Trading Ltd", f"NSEI:T{i}", ("Retail", "Steel")[i % 2]) for i in range(10)]
    companies = [m.Company(f"T{i}", f"Bank{i} Trading Ltd", "") for i in range(10)] + [m.Company("NEWTRADE", "Zeta Trading Limited", "")]
    mapped = {r.symbol: r for r in m.build_company_map(damodaran, companies)}
    assert "NEWTRADE" not in mapped  # 5 of 10 is under 0.6
    assert all(mapped[f"T{i}"].method == "nse_ticker" for i in range(10))
