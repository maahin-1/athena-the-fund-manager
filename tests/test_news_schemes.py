from datetime import datetime, timedelta, timezone

from athena.news.model import NewsItem
from athena.news.schemes import MAX_DATES, SchemeFiling, scheme_completions, scheme_flag

IST = timezone(timedelta(hours=5, minutes=30))


def item(day, title, source="nse"):
    return NewsItem(title, "NSE", datetime(2024, 1, day, 10, tzinfo=IST), "", source)


def test_filings_that_say_a_scheme_took_effect_are_found_and_proposals_and_option_schemes_are_not():
    items = [
        item(1, "Amalgamation/Merger: Pronouncement of Order by the Hon ble NCLT sanctioning the Scheme of Amalgamation amongst A and B"),
        item(5, "Scheme of Arrangement: Effective Date of the Scheme of Amalgamation amongst Tata Steel Limited and Tata Metaliks Limited"),
        item(9, "Allotment of Securities: Tata Steel Limited has informed the Exchange regarding allotment of 99701239 securities pursuant to Scheme of Arrangement/Amalgamation at its meeting"),
        item(12, "Amalgamation/Merger: Proposed composite scheme of amalgamation for the amalgamation of X into Y"),
        item(13, "Allotment of Securities: ICICI Bank has allotted 869,928 equity shares of face value of Rs 2 each under the ICICI Bank Employees Stock Option Scheme-2000"),
        item(14, "Effective Date of the Scheme of Amalgamation", source="google news"),
    ]
    found = scheme_completions(items)
    assert [f.day.day for f in found] == [9, 5, 1]  # newest first; the proposal, the option scheme and the press item are left out


def test_two_filings_on_one_day_count_once():
    twin = "Effective Date of the Scheme of Amalgamation amongst A and B"
    assert len(scheme_completions([item(5, f"Amalgamation/Merger: {twin}"), item(5, f"Scheme of Arrangement: {twin}")])) == 1


def test_the_flag_names_the_dates_says_the_growth_is_not_organic_and_caps_the_list():
    filings = [SchemeFiling(datetime(2024, 1, d).date(), f"title {d}") for d in range(9, 0, -1)]
    text = scheme_flag(filings)
    assert "2024-01-09, 2024-01-08, 2024-01-07, 2024-01-06 and 5 more" in text and "not organic" in text and text.endswith("title 9")
    assert MAX_DATES == 4 and scheme_flag([]) is None
