"""A company whose fundamentals can be worked out by hand, shared by the metrics and dashboard tests."""
from datetime import date, datetime, timedelta, timezone

C = 1e7  # one crore
NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)  # 5 Oct 2026 IST
YEARS = ["2023-03-31", "2024-03-31", "2025-03-31", "2026-03-31"]
QUARTERS = ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]


def by(periods, values, scale=C):
    return {p: (None if v is None else v * scale) for p, v in zip(periods, values)}


def annual(values, scale=C):
    return by(YEARS, values, scale)


def quarterly(values, scale=C):
    return by(QUARTERS, values, scale)


# A company whose figures can be worked out by hand (amounts in INR crore, 10 crore shares, price 266.2).
ACME = {
    "info": {"sector": "Technology", "sharesOutstanding": 1e8, "dividendRate": 5.0},
    "annual": {
        "income": {
            "Total Revenue": annual([1000, 1100, 1210, 1331]),
            "Net Income": annual([100, 110, 121, 133.1]),
            "Operating Income": annual([130, 165, 181.5, 199.65]),
            "EBIT": annual([130, 165, 181.5, 199.65]),
            "Gross Profit": annual([400, 440, 484, 532.4]),
            "Interest Expense": annual([-20, -20, -20, -20]),
            "Diluted EPS": by(YEARS, [10, 11, 12.1, 13.31], 1),
        },
        "balance": {
            "Stockholders Equity": annual([500, 600, 700, 800]),
            "Total Debt": annual([200, 200, 200, 200]),
            "Total Assets": annual([1000, 1100, 1200, 1300]),
            "Current Assets": annual([400, 450, 480, 500]),
            "Current Liabilities": annual([250, 250, 250, 250]),
        },
        "cashflow": {
            "Operating Cash Flow": annual([120, 130, 140, 160]),
            "Capital Expenditure": annual([-50, -50, -60, -60]),
            "Depreciation And Amortization": annual([30, 30, 35, 40]),
        },
    },
    "quarterly": {
        "income": {
            "Total Revenue": quarterly([300, 320, 330, 350, 345]),
            "Net Income": quarterly([30, 31, 32, 33, 36]),
            "Diluted EPS": by(QUARTERS, [2.8, 3.0, 3.2, 3.4, 3.71], 1),
        }
    },
    "earnings_dates": [
        {"date": "2026-10-20", "estimate": 3.9, "reported": None, "surprise_pct": None},
        {"date": "2026-07-20", "estimate": 3.5, "reported": 3.71, "surprise_pct": 6.0},
        {"date": "2026-04-20", "estimate": 3.3, "reported": 3.4, "surprise_pct": 3.03},
        {"date": "2026-01-20", "estimate": 3.3, "reported": 3.2, "surprise_pct": -3.03},
        {"date": "2025-10-20", "estimate": 2.9, "reported": 3.0, "surprise_pct": 3.45},
    ],
    "quality_flags": [],
}
PRICE = 266.2


def index_history(latest_pe=19.3):
    days, day = [], date(2026, 10, 5)
    while len(days) < 300:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return {d: {"pe": latest_pe if i == 0 else (18.0 if i < 150 else 21.0), "pb": 2.7, "div_yield": 1.2} for i, d in enumerate(days)}
