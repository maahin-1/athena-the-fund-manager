"""Build a tiny fundamentals store from the XBRL test fixtures, for the terminal's end-to-end test.

    python scripts/seed_fund_fixture.py terminal/web/e2e-fund/f.duckdb"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from athena.fundamentals.rows import parse_rows
from athena.fundamentals.store import FundamentalsStore

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "xbrl"


def main(path: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    with FundamentalsStore(target) as store:
        for i, name in enumerate(("tcs_fy2020", "tcs_fy2024")):
            statements = parse_rows((FIXTURES / f"{name}.xml").read_text(encoding="utf-8"))
            store.save_statements(statements, f"fixture-{name}", datetime(2024, 4, 12 + i, tzinfo=timezone.utc))
            store.save_company(statements.symbol, "Tata Consultancy Services", "INE467B01029", "IT", statements.layout, statements.shares)


if __name__ == "__main__":
    main(sys.argv[1])
