from datetime import datetime, timezone
from pathlib import Path

from athena.fundamentals import show
from athena.fundamentals.rows import parse_rows
from athena.fundamentals.store import FundamentalsStore

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"


def test_a_stored_company_renders_in_crores_with_screener_row_names():
    with FundamentalsStore(":memory:") as store:
        store.save_statements(parse_rows((FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8")), "u", datetime(2024, 4, 12, tzinfo=timezone.utc))
        text = show.render(store, "TCS", "consolidated")
    assert "Profit & Loss" in text and "Balance Sheet" in text and "Cash Flows" in text
    assert "Sales" in text and "240,893" in text  # 2,408,930,000,000 rupees
    assert "Mar 2024" in text
    assert "Total Assets" in text and "146,449" in text


def test_a_company_with_no_rows_says_so():
    with FundamentalsStore(":memory:") as store:
        assert "no statements" in show.render(store, "NOPE", "consolidated").lower()
