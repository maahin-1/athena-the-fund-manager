from datetime import date, datetime, timezone

import pytest

from athena.evidence import claims as cl

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=timezone.utc)  # 11:30 India time
COMPANIES = [
    ("INFY", "Infosys Limited"), ("TCS", "Tata Consultancy Services Limited"), ("SBIN", "State Bank of India"), ("RELIANCE", "Reliance Industries Limited"), ("RELPOWER", "Reliance Power Limited"),
    ("KOTAKBANK", "Kotak Mahindra Bank Limited"), ("TATAMOTORS", "Tata Motors Limited"), ("TATASTEEL", "Tata Steel Limited"), ("ONGC", "Oil and Natural Gas Corporation Limited"),
]
INDEX = cl.NameIndex(COMPANIES)


def claims_of(title, published=NOW):
    return cl.extract_claims(title, published, INDEX.find(title))


def one(title, kind):
    got = [c for c in claims_of(title) if c.kind == kind]
    assert len(got) == 1, (title, claims_of(title))
    return got[0]


# --- who a title is about ---
def test_a_name_that_would_belong_to_two_companies_is_dropped_but_the_full_names_still_work():
    assert [h[0] for h in INDEX.find("Infosys wins a deal")] == ["INFY"]
    assert [h[0] for h in INDEX.find("Tata Motors and Tata Steel rally")] == ["TATAMOTORS", "TATASTEEL"]
    assert INDEX.find("Tata group plans a new fab") == []  # `Tata` is nobody's alone
    assert [h[0] for h in INDEX.find("Reliance Industries gains")] == ["RELIANCE"]
    assert INDEX.find("Reliance and Disney merge") == []  # `Reliance` names two companies here
    assert [h[0] for h in INDEX.find("Kotak shares fall after the update")] == ["KOTAKBANK"]  # a distinctive first word, followed by `shares`
    assert INDEX.find("Kotak says rates will stay") == []  # the first word alone, in a sentence, could be a person or a product
    assert [h[0] for h in INDEX.find("SBI shares fall on the rate cut")] == ["SBIN"] and INDEX.find("sbi shares fall") == []  # the initials are matched in capitals only
    assert [h[0] for h in INDEX.find("SBI raises lending rate")] == ["SBIN"]  # SBI begins the ticker SBIN
    assert INDEX.find("Suppliers and ONG, B2B segments grow") == [] and cl.NameIndex([("OLAELEC", "Ola Electric Mobility Limited")]).find("Gulf Oil: OEM, B2B segments grow") == []  # initials that start no ticker need `shares` after them


def test_a_company_inside_a_longer_name_is_not_counted_twice():
    assert [h[0] for h in INDEX.find("Tata Consultancy Services posts results")] == ["TCS"]


# --- price moves ---
@pytest.mark.parametrize("title,direction,pct", [
    ("Infosys shares jump 5% after the deal", 1, 5.0),
    ("Infosys stock falls 3.2 per cent on weak guidance", -1, 3.2),
    ("Infosys slumps nearly 4% as brokerages cut targets", -1, 4.0),
    ("Infosys shares up 2% in early trade", 1, 2.0),
    ("Infosys surges over 7 percent", 1, 7.0),
])
def test_a_price_move_is_taken_when_the_name_comes_straight_before_the_verb(title, direction, pct):
    c = one(title, "price_move")
    assert c.symbol == "INFY" and c.fields == {"direction": direction, "pct": pct}


@pytest.mark.parametrize("title", [
    "Nifty rises 1%, Infosys leads the gainers",  # the number belongs to the index
    "Infosys profit up 12% in the quarter",  # a profit, not a price
    "Why Infosys could jump 20% by March, say analysts",  # a forecast with words between
    "Infosys cuts 5% of its workforce",  # not a price move
    "Infosys shares jump 90%",  # beyond what a day can do: a typo or a different thing
])
def test_a_title_whose_number_is_not_this_companys_price_gives_no_price_claim(title):
    assert [c for c in claims_of(title) if c.kind == "price_move"] == []


def test_each_company_named_gets_its_own_price_claim_only_when_the_verb_follows_it():
    got = [(c.symbol, c.fields["pct"]) for c in claims_of("Infosys, TCS jump 3% as IT stocks rally") if c.kind == "price_move"]
    assert got == [("TCS", 3.0)]  # `Infosys,` is followed by another name, so nothing is said about it
    got = [(c.symbol, c.fields["direction"], c.fields["pct"]) for c in claims_of("Infosys shares rise 2% while TCS stock falls 1.5% after the update") if c.kind == "price_move"]
    assert got == [("INFY", 1, 2.0), ("TCS", -1, 1.5)]
    assert [c for c in claims_of("Infosys and TCS in talks to merge") if c.kind == "rumour"] == []  # a title about two companies is not read as a claim about one


def test_a_move_over_a_stretch_of_time_is_not_one_days_bar():
    for title in ("Infosys shares fall 8% in a week", "Infosys stock is up 12% this month", "Infosys shares gain 20% since January", "Infosys shares rise 6% in 3 sessions"):
        assert [c for c in claims_of(title) if c.kind == "price_move"] == [], title


def test_a_one_word_alias_needs_a_capital_and_must_not_be_a_common_word():
    index = cl.NameIndex([("SILVERTUC", "Silver Tuc Limited"), ("PIDILITIND", "Pidilite Industries Limited"), ("POWERGRID", "Power Grid Corporation of India Limited")],
                         common=cl.common_lowercase_words(["silver prices rally", "silver rises on demand", "silver and gold gain", "pidilite shares fall"] * 1))
    assert index.find("Gold rises above $4,070 an ounce, silver gains nearly 1%") == []  # a metal, not a company
    assert [h[0] for h in index.find("Pidilite shares drop 3% after Q2")] == ["PIDILITIND"]
    assert index.find("pidilite shares drop 3%") == []  # an alias written in lower case is not a name
    assert [h[0] for h in index.find("Silver Tuc Limited shares fall 3%")] == ["SILVERTUC"]  # the full name always works
    assert index.find("Power sector rallies") == []


def test_common_words_are_those_written_in_lower_case_in_several_titles():
    titles = ["silver prices rally", "silver rises", "silver slips", "Pidilite shares fall", "pidilite? no"]
    assert "silver" in cl.common_lowercase_words(titles) and "pidilite" not in cl.common_lowercase_words(titles)


def test_a_statement_is_read_in_the_clause_that_names_the_company():
    got = claims_of("Q2 Results Highlights: RR Kabel profit jumps 56%; Infosys revenue rises 21%")
    assert [(c.symbol, c.kind, c.fields["metric"], c.fields["pct"]) for c in got] == [("INFY", "results", "sales", 21.0)]
    assert claims_of("Q2 Results Live Updates: Paras Defence revenue rises 42%; Infosys EBITDA up 67%") == []  # a measure we do not check


# --- results ---
def test_a_quarterly_profit_claim_carries_the_metric_the_percentage_the_amount_and_the_basis():
    c = one("Infosys Q2 net profit rises 6% YoY to Rs 7,823 crore", "results")
    assert c.fields == {"metric": "net_profit", "direction": 1, "pct": 6.0, "amount_cr": 7823.0, "basis": "yoy"}
    c = one("TCS posts 5% fall in Q2 profit", "results")
    assert c.symbol == "TCS" and c.fields["direction"] == -1 and c.fields["pct"] == 5.0 and c.fields["amount_cr"] is None
    c = one("Infosys Q2 revenue up 3.5% QoQ at Rs 41,000 crore", "results")
    assert c.fields["metric"] == "sales" and c.fields["basis"] == "qoq" and c.fields["amount_cr"] == 41000.0


@pytest.mark.parametrize("title", [
    "Infosys FY26 net profit rises 8%",
    "Infosys H1 profit up 9% to Rs 15,592 crore",
    "Infosys nine-month revenue grows 6%",
    "Infosys profit view: expect 6% rise",
])
def test_an_annual_or_half_year_figure_is_not_compared_with_a_quarter(title):
    assert [c for c in claims_of(title) if c.kind == "results"] == []


def test_a_forecast_or_a_preview_is_not_a_result():
    for title in ("Infosys Q2 Preview: profit may rise 52% YoY", "Infosys Q2 profit expected to rise 6%", "Infosys Q2 results: what to expect, profit seen up 6%", "Infosys profit likely to rise 6% in the quarter"):
        assert [c for c in claims_of(title) if c.kind == "results"] == [], title


def test_a_rupee_amount_needs_a_rupee_sign_and_a_unit():
    c = one("Infosys Q2 profit rises 6% to $1.2 billion", "results")
    assert c.fields["amount_cr"] is None
    c = one("Infosys Q2 profit rises 6% to Rs 7,800", "results")
    assert c.fields["amount_cr"] is None  # no unit given: not guessed


# --- insiders ---
@pytest.mark.parametrize("title,action", [
    ("Promoters sell 2% stake in Infosys via block deal", "Sell"),
    ("Infosys promoter buys 1 lakh shares from the open market", "Buy"),
    ("Infosys promoter group raises stake to 15%", "Buy"),
    ("Infosys promoters pledge 5% shares", "Pledge"),
    ("Infosys promoter releases pledge on 3 million shares", "Pledge Revoke"),
    ("Promoter offloads Infosys shares worth Rs 400 crore", "Sell"),
])
def test_a_promoter_action_is_read_from_the_verb_after_the_word_promoter(title, action):
    assert one(title, "insider").fields == {"action": action}


def test_an_insider_claim_needs_the_word_promoter_and_exactly_one_company():
    assert [c for c in claims_of("Infosys buys a US firm for $500 million") if c.kind == "insider"] == []
    assert [c for c in claims_of("Promoter of Infosys sells stake; TCS gains") if c.kind == "insider"] == []


# --- board meetings ---
def test_a_board_meeting_date_is_read_in_both_orders_and_the_year_is_inferred_forward():
    assert one("Infosys board to meet on 5 Nov to consider dividend", "board_meeting").fields == {"date": "2026-11-05"}
    assert one("Infosys board meeting on November 12 to approve results", "board_meeting").fields == {"date": "2026-11-12"}
    assert one("Infosys board meets on 3rd January to consider a buyback", "board_meeting").fields == {"date": "2027-01-03"}
    assert [c for c in claims_of("Infosys board approves a dividend") if c.kind == "board_meeting"] == []


# --- institutional flows (market-wide) ---
def test_an_institutional_flow_claim_needs_no_company_and_is_skipped_when_one_is_named_or_it_is_about_derivatives():
    c = [x for x in claims_of("FIIs sold shares worth Rs 3,500 crore on Tuesday; DIIs bought Rs 643 crore") if x.kind == "flow"]
    assert len(c) == 1 and c[0].symbol == "" and c[0].fields == {"category": "FII/FPI", "direction": -1, "amount_cr": 3500.0}
    c = one("DIIs net buyers at Rs 1,200 cr as FPIs stay away", "flow")
    assert c.fields == {"category": "DII", "direction": 1, "amount_cr": 1200.0}
    assert [x for x in claims_of("FIIs sold Infosys shares worth Rs 900 crore") if x.kind == "flow"] == []
    assert [x for x in claims_of("FIIs sold index futures worth Rs 5,000 crore") if x.kind == "flow"] == []


# --- rumours ---
def test_a_rumour_is_kept_only_when_there_is_no_checkable_claim_in_the_title():
    assert one("Infosys in talks to buy a German consultancy, sources said", "rumour").fields["cue"] == "in talks"
    assert one("Infosys may acquire a US firm", "rumour").fields["cue"] == "may acquire"
    for title in ("Infosys weighs on the Sensex", "Infosys Q2 Results Preview: PAT likely to rise 10%", "Infosys IPO allotment out; shares set to list tomorrow", "Infosys likely to report strong growth"):
        assert [c for c in claims_of(title) if c.kind == "rumour"] == [], title
    assert [c.kind for c in claims_of("Infosys shares jump 5% on reports it is in talks to buy a firm")] == ["price_move"]
    assert claims_of("Infosys opens a campus in Pune") == []
