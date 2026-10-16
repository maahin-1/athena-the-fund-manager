from athena.fundamentals import lines as spec


def test_every_line_names_a_tag_and_no_key_repeats_inside_a_table():
    tables = [*spec.INCOME_LINES.values(), *spec.BALANCE_LINES.values(), spec.CASHFLOW_LINES, spec.BANKING_LINES]
    for table in tables:
        keys = [line.key for line in table]
        assert len(keys) == len(set(keys)), keys
        assert all(line.tags for line in table)
        assert all(line.how in ("first", "sum") for line in table)


def test_all_three_layouts_have_an_income_statement_and_a_balance_sheet():
    for layout in (spec.INDAS, spec.FINANCE, spec.BANK):
        assert any(line.key == "sales" for line in spec.INCOME_LINES[layout])
        assert any(line.key == "total_assets" for line in spec.BALANCE_LINES[layout])


def test_tags_seen_in_real_filings_are_covered():
    tags = spec.all_tags()
    for tag in (
        "RevenueFromOperations", "InterestEarned", "Advances", "CashFlowsFromUsedInOperatingActivities", "EquityShareCapital",
        "Symbol", "DateOfEndOfReportingPeriod", "Loans", "DebtSecurities", "InterestExpended",
    ):
        assert tag in tags
