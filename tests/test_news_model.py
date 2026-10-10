from datetime import datetime, timezone

import pytest

from athena.news.model import NewsItem, clip, company_names, mentions

WHEN = datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)


def item(title, snippet=""):
    return NewsItem(title, "ET", WHEN, "https://x", "test", snippet)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Vedanta Limited", ("Vedanta", "Vedanta Limited")),
        ("Kotak Mahindra Bank Limited", ("Kotak Mahindra Bank", "Kotak Mahindra Bank Limited")),
        ("State Bank of India", ("State Bank of India",)),
        ("Wipro Ltd.", ("Wipro", "Wipro Ltd.")),
        ("Limited", ("Limited",)),
    ],
)
def test_the_names_a_headline_might_use_for_a_company(name, expected):
    assert company_names(name) == expected


def test_a_company_is_mentioned_only_as_a_whole_word_in_the_title_or_snippet():
    names = company_names("Idea Limited")
    assert mentions(item("Idea shares rally"), names)
    assert not mentions(item("An ideal time to buy"), names)
    assert mentions(item("Telecom stocks", "Brokerages upgrade IDEA on tariff hike"), names)
    assert not mentions(item("Telecom stocks"), names)


def test_long_text_is_cut_with_an_ellipsis_and_short_text_is_untouched():
    assert clip("short") == "short" and clip("x" * 400) == "x" * 299 + "…" and len(clip("x" * 400)) == 300
    assert clip("  spaced   out \n text ") == "spaced out text"
