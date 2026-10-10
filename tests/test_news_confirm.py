"""Event confirmation against real headlines recorded in the Oct 10, 2026 source check (titles and dates as observed)."""
from datetime import date, datetime, timezone

from athena.news.confirm import EventRef, confirm_events, describe_confirmations
from athena.news.model import NewsItem


def item(day, publisher, title, source="google news"):
    y, m, d = (int(part) for part in day.split("-"))
    return NewsItem(title, publisher, datetime(y, m, d, 9, 0, tzinfo=timezone.utc), "https://x", source)


KOTAK = [
    item("2026-01-13", "The Economic Times", "Kotak Mahindra Bank 5:1 stock split: What shareholders need to know before ex-date"),
    item("2026-01-14", "India Today", "Kotak Mahindra stock split: Did private lender's shares really crash 80% today?"),
    item("2026-03-02", "Mint", "Kotak Mahindra Bank shares gain on strong inflows"),
]
VEDANTA = [
    item("2026-04-20", "NSE", "Record Date: Vedanta Limited has informed the Exchange that Record date for the purpose of Scheme of Arrangement has been fixed as May 01, 2026", "nse"),
    item("2026-04-24", "ICICI Direct", "Vedanta Demerger: Five Pure-Play Entities Finally See the Light of Day"),
    item("2026-06-20", "ET", "Vedanta shares rise on aluminium rally"),
]
WIPRO = [
    item("2026-06-04", "The Economic Times", "Wipro buyback alert! Last date to buy shares to participate in Rs 15,000 crore share buyback"),
    item("2026-06-08", "Univest", "Wipro Share Price Falls 8.37% Post Buyback Ex-Date"),
]
ADANI = [
    item("2025-11-12", "India Infoline", "Adani Enterprises unveils Rs 24,930 Crore rights issue details"),
    item("2025-11-17", "Moneycontrol", "Adani Enterprises stock edges up on rights issue record date after early dip"),
]


def one(day, kind, items, label=""):
    y, m, d = (int(part) for part in day.split("-"))
    return confirm_events([EventRef(date(y, m, d), kind, label)], items)[0]


def test_a_split_is_confirmed_by_headlines_near_its_ex_date_and_the_nearest_come_first():
    got = one("2026-01-14", "split", KOTAK, "5-for-1 split")
    assert got.status == "confirmed" and len(got.headlines) == 2
    assert got.headlines[0].publisher == "India Today" and "crash 80%" in got.headlines[0].title  # on the day itself


def test_a_headline_outside_the_window_or_without_a_keyword_for_that_kind_does_not_confirm():
    assert one("2026-03-02", "split", KOTAK).status == "no coverage found"  # only a March headline, none about a split
    assert one("2026-01-14", "buyback", KOTAK).status == "no coverage found"  # a split headline does not confirm a buyback


def test_a_demerger_is_confirmed_by_the_exchange_filing_and_the_press():
    got = one("2026-04-30", "demerger", VEDANTA)
    assert got.status == "confirmed" and {h.publisher for h in got.headlines} == {"NSE", "ICICI Direct"}


def test_buybacks_and_rights_issues_are_confirmed_by_their_own_words():
    assert one("2026-06-05", "buyback", WIPRO).status == "confirmed"
    assert one("2025-11-17", "rights", ADANI).status == "confirmed"
    assert one("2025-11-17", "buyback", ADANI).status == "no coverage found"


def test_an_unexplained_break_collects_any_plausible_explanation_as_context():
    got = one("2026-04-30", "unexplained", VEDANTA)
    assert got.status == "confirmed" and len(got.headlines) == 2
    text = describe_confirmations([got])[0]
    assert "context only" in text and "nothing was adjusted" in text


def test_no_coverage_is_said_plainly_and_a_confirmed_event_names_a_headline_and_its_date():
    none = describe_confirmations([one("2026-03-02", "split", KOTAK, "5-for-1 split")])[0]
    assert "no coverage found" in none and "2026-03-02" in none
    yes = describe_confirmations([one("2026-01-14", "split", KOTAK, "5-for-1 split")])[0]
    assert "confirmed by 2 headline" in yes and "India Today" in yes and "2026-01-14" in yes


def test_the_event_label_is_shown_and_an_empty_list_of_events_gives_nothing():
    assert "5-for-1 split" in describe_confirmations([one("2026-01-14", "split", KOTAK, "5-for-1 split")])[0]
    assert confirm_events([], KOTAK) == [] and describe_confirmations([]) == ()


def test_the_window_reaches_further_back_than_forward_because_announcements_come_first():
    def demerger_news(day):
        return [item(day, "NSE", "Demerger: board approves the scheme of demerger", "nse")]

    assert one("2026-04-30", "demerger", demerger_news("2026-04-16")).status == "confirmed"  # 14 days before: in
    assert one("2026-04-30", "demerger", demerger_news("2026-04-15")).status == "no coverage found"  # 15 days before: out
    assert one("2026-04-30", "demerger", demerger_news("2026-05-07")).status == "confirmed"  # 7 days after: in
    assert one("2026-04-30", "demerger", demerger_news("2026-05-08")).status == "no coverage found"  # 8 days after: out


# ---- the headline must be about this company

WIPRO_NAMES = ("Wipro", "Wipro Limited")
KOTAK_NAMES = ("Kotak Mahindra Bank", "Kotak Mahindra Bank Limited")


def test_generic_pieces_about_stock_splits_do_not_confirm_a_companys_split():
    generic = [
        item("2024-12-03", "24/7 Wall St.", "Stock Splits Matter: Here's What's Coming in December"),
        item("2024-12-10", "The Economic Times", "Achyut Healthcare among 4 stocks to track today for stock split, rights, bonus and dividend"),
    ]
    got = confirm_events([EventRef(date(2024, 12, 3), "split")], generic, WIPRO_NAMES)[0]
    assert got.status == "no coverage found"
    assert confirm_events([EventRef(date(2024, 12, 3), "split")], generic)[0].status == "confirmed"  # without names nothing is checked


def test_a_headline_with_the_short_company_name_counts_and_so_does_an_exchange_filing():
    got = confirm_events([EventRef(date(2026, 1, 14), "split")], KOTAK, KOTAK_NAMES)[0]
    assert got.status == "confirmed" and "Kotak Mahindra stock split" in got.headlines[0].title  # first word is enough
    nse = [item("2024-11-21", "NSE", "Record Date: the record date for the purpose of Bonus is 03-Dec-2024", "nse")]
    assert confirm_events([EventRef(date(2024, 12, 3), "split")], nse, WIPRO_NAMES)[0].status == "confirmed"


def test_a_generic_first_word_is_not_enough_so_another_banks_split_does_not_confirm_this_one():
    other = [item("2026-01-14", "ET", "State Bank of Maharashtra announces stock split")]
    assert confirm_events([EventRef(date(2026, 1, 14), "split")], other, ("State Bank of India",))[0].status == "no coverage found"


def test_a_yahoo_bonus_recorded_as_a_split_is_confirmed_by_bonus_headlines():
    bonus = [item("2024-11-21", "NSE", "Record Date: the record date for the purpose of Bonus is 03-Dec-2024", "nse")]
    assert confirm_events([EventRef(date(2024, 12, 3), "split")], bonus)[0].status == "confirmed"
