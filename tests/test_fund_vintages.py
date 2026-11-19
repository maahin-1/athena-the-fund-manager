"""WP04 (Q3): statement versions are immutable, the current page still reads the latest value, research reads the version that was public at its cutoff, and the migration that seeds the versions is idempotent,
reversible, and honest about what it cannot recover. Synthetic stores in a temporary directory only: nothing here opens a real `.athena` file.

Acceptance (from the work package): an original of 100 filed in 2024 and a revision of 120 filed in 2025 read 100 at a 2024 cutoff and 120 later; repeated ingest and migration are idempotent; a rollback restores
the previous current-page results; late amendments, withdrawals and basis/layout changes stay distinguishable; a missing original stays missing, never relabelled."""
import dataclasses
import hashlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from athena.contracts import AthenaError
from athena.fundamentals import ingest, vintages
from athena.fundamentals.rows import parse_rows
from athena.fundamentals.store import FundamentalsStore

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"
FY24 = (date(2023, 4, 1), date(2024, 3, 31))
UTC = timezone.utc
FILED_2024, FILED_2025 = datetime(2024, 5, 1, tzinfo=UTC), datetime(2025, 5, 1, tzinfo=UTC)
CUT_2024, CUT_BETWEEN, CUT_AFTER_REVISION, CUT_LATE_2025 = datetime(2024, 6, 1), datetime(2025, 4, 1), datetime(2025, 6, 1), datetime(2025, 12, 1)  # the revision is filed on 1 May 2025


def base():
    return parse_rows((FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8"))


def stmt(sales, profit=None, basis="consolidated", layout=None, only_income=True):
    st = base()
    income = {**st.income[FY24], "sales": sales}
    if profit is not None:
        income["net_profit"] = profit
    changes = {"income": {FY24: income}, "basis": basis}
    if only_income:
        changes.update(balance={}, cashflow={}, banking={})
    if layout:
        changes["layout"] = layout
    return dataclasses.replace(st, **changes)


def sales_at(store, cutoff, **kw):
    return store.lines_as_of("TCS", "consolidated", "income", cutoff, **kw).get(FY24, {}).get("sales")


# ======================================== the model: versions, latest view, as-of view ========================================
def test_original_100_and_revision_120_read_100_at_a_2024_cutoff_and_120_later_and_the_current_page_still_reads_120():
    with FundamentalsStore(":memory:") as store:
        store.save_statements(stmt(100.0), "orig", FILED_2024)
        store.save_statements(stmt(120.0), "rev", FILED_2025)
        assert sales_at(store, CUT_2024) == 100.0
        assert sales_at(store, CUT_BETWEEN) == 100.0  # the revision was filed on 1 May 2025: not yet public
        assert sales_at(store, CUT_AFTER_REVISION) == 120.0 and sales_at(store, CUT_LATE_2025) == 120.0
        assert store.lines("TCS", "consolidated", "income")[FY24]["sales"] == 120.0  # the current page: the latest value, exactly as before
        assert sales_at(store, datetime(2024, 4, 1)) is None  # before any filing: absent, not guessed
        got = {(v["filing_url"], v["value"]) for v in store.versions("TCS", "consolidated", "income") if v["key"] == "sales"}
        assert got == {("orig", 100.0), ("rev", 120.0)}  # both filings remain separately recoverable


def test_an_older_filing_that_arrives_after_a_newer_one_is_kept_as_a_version_not_lost():
    with FundamentalsStore(":memory:") as store:
        store.save_statements(stmt(120.0), "rev", FILED_2025)
        store.save_statements(stmt(100.0), "orig", FILED_2024)  # the exchange list was read in the other order
        assert store.lines("TCS", "consolidated", "income")[FY24]["sales"] == 120.0  # the latest value is unchanged: the later filing wins
        assert sales_at(store, CUT_2024) == 100.0  # and the original is now recoverable, which the destructive table could not do


def test_saving_the_same_filing_twice_adds_no_version():
    with FundamentalsStore(":memory:") as store:
        store.save_statements(stmt(100.0), "orig", FILED_2024)
        first = store._con.execute("SELECT count(*) FROM statement_versions").fetchone()[0]
        store.save_statements(stmt(100.0), "orig", FILED_2024)
        assert store._con.execute("SELECT count(*) FROM statement_versions").fetchone()[0] == first > 0


def test_a_version_carries_its_provenance():
    with FundamentalsStore(":memory:") as store:
        store.save_statements(stmt(100.0), "orig", FILED_2024, source_hash="abc123")
        row = [v for v in store.versions("TCS", "consolidated", "income") if v["key"] == "sales"][0]
        assert row["provenance"] == "ingest" and row["source_hash"] == "abc123" and row["ingested"] is not None
        assert row["filed"] == datetime(2024, 5, 1) and row["parser_version"] and row["source"] == "nse-xbrl" and row["layout"] == "indas"


def test_a_partial_amendment_does_not_inherit_lines_in_the_whole_filing_view_but_does_in_the_fieldwise_view_and_the_current_page():
    with FundamentalsStore(":memory:") as store:
        thin = dataclasses.replace(stmt(100.0), income={FY24: {"sales": 100.0, "net_profit": 10.0}})
        amendment = dataclasses.replace(stmt(120.0), income={FY24: {"sales": 120.0}})  # the revision reports sales only
        store.save_statements(thin, "orig", FILED_2024)
        store.save_statements(amendment, "rev", FILED_2025)
        assert store.lines_as_of("TCS", "consolidated", "income", CUT_LATE_2025) == {FY24: {"sales": 120.0}}  # whole filing: exactly what the amendment reports
        assert store.lines_as_of("TCS", "consolidated", "income", CUT_LATE_2025, fieldwise=True) == {FY24: {"sales": 120.0, "net_profit": 10.0}}  # fieldwise: the newest eligible line of each key
        assert store.lines_as_of("TCS", "consolidated", "income", CUT_BETWEEN) == {FY24: {"sales": 100.0, "net_profit": 10.0}}
        assert store.lines("TCS", "consolidated", "income")[FY24] == {"sales": 120.0, "net_profit": 10.0}  # the current page keeps the behaviour it always had


def test_two_filings_at_the_same_instant_resolve_deterministically_by_filing_identity():
    with FundamentalsStore(":memory:") as store:
        store.save_statements(stmt(1.0), "a-first", FILED_2024)
        store.save_statements(stmt(2.0), "b-second", FILED_2024)
        picks = {sales_at(store, CUT_2024) for _ in range(5)}
        assert picks == {2.0}
    with FundamentalsStore(":memory:") as other:  # insertion order does not decide it
        other.save_statements(stmt(2.0), "b-second", FILED_2024)
        other.save_statements(stmt(1.0), "a-first", FILED_2024)
        assert sales_at(other, CUT_2024) == 2.0


def test_a_withdrawn_filing_stops_counting_from_its_withdrawal_and_the_current_page_falls_back_to_the_filing_before():
    with FundamentalsStore(":memory:") as store:
        store.save_statements(stmt(100.0), "orig", FILED_2024)
        store.save_statements(stmt(120.0), "rev", FILED_2025)
        store.withdraw_filing("rev", datetime(2025, 8, 1), "withdrawn by the company")
        assert sales_at(store, CUT_AFTER_REVISION) == 120.0  # it was public, and visible, before it was withdrawn
        assert sales_at(store, datetime(2025, 9, 1)) == 100.0  # afterwards the earlier filing is the one in force
        assert store.lines("TCS", "consolidated", "income")[FY24]["sales"] == 100.0  # the current page follows
        assert [v["value"] for v in store.versions("TCS", "consolidated", "income") if v["key"] == "sales" and v["filing_url"] == "rev"] == [120.0]  # the withdrawn version is still on record


def test_basis_and_layout_changes_are_distinguishable_and_never_merged():
    with FundamentalsStore(":memory:") as store:
        store.save_statements(stmt(100.0), "cons", FILED_2024)
        store.save_statements(stmt(60.0, basis="standalone"), "stand", FILED_2025)
        assert sales_at(store, CUT_LATE_2025) == 100.0
        assert store.lines_as_of("TCS", "standalone", "income", CUT_LATE_2025)[FY24]["sales"] == 60.0
        store.save_statements(stmt(130.0, layout="bank"), "later-layout", datetime(2025, 7, 1, tzinfo=UTC))
        layouts = {v["filing_url"]: v["layout"] for v in store.versions("TCS", "consolidated", "income") if v["key"] == "sales"}
        assert layouts == {"cons": "indas", "later-layout": "bank"}


def test_a_period_whose_original_is_not_held_is_reported_unavailable_not_filled_from_the_revision():
    with FundamentalsStore(":memory:") as store:
        store.save_statements(stmt(120.0), "rev", FILED_2025)  # only the revision was ever stored
        assert sales_at(store, CUT_2024) is None
        gaps = store.gaps_as_of("TCS", "consolidated", "income", CUT_2024)
        assert len(gaps) == 1 and gaps[0]["period_end"] == FY24[1] and "first held version was filed" in gaps[0]["reason"]
        assert store.gaps_as_of("TCS", "consolidated", "income", CUT_LATE_2025) == []


# ======================================== the migration tool ========================================
def legacy_store(path, with_filings=True):
    """A store as it was before versions: lines from the latest filing only, in the destructive table, written without the new code path."""
    with FundamentalsStore(path) as store:
        store.save_statements(stmt(120.0), "rev", FILED_2025)
        store._con.execute("DROP TABLE statement_versions")  # what a pre-WP04 file looks like
        store._con.execute("DROP TABLE IF EXISTS filing_withdrawals")
        store._con.execute("DROP TABLE IF EXISTS migrations")
        store._con.execute("DROP TABLE IF EXISTS schema_meta")
        if with_filings:
            store.record_filing("rev", "TCS", "consolidated", FY24[1], True, FILED_2025, "ok")
            store.record_filing("orig", "TCS", "consolidated", FY24[1], True, FILED_2024, "ok")
    return path


def page_view(path):
    with FundamentalsStore(path, read_only=True) as store:
        return store.lines("TCS", "consolidated", "income"), store.quarter_figures("TCS"), store.coverage(), store.last_filed("TCS", "consolidated")


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_a_dry_run_reports_counts_and_changes_nothing(tmp_path):
    path = legacy_store(tmp_path / "f.duckdb")
    before = file_hash(path)
    plan = vintages.plan(path, cache_dir=tmp_path / "cache")
    assert plan.to_seed > 0 and plan.rebuilt == 0 and plan.already_versioned == 0 and plan.applied is False
    assert [u["url"] for u in plan.originals_unavailable] == ["orig"]  # filed, recorded ok, no raw copy and no lines: missing, and named
    assert file_hash(path) == before


def test_apply_seeds_extant_rows_with_honest_provenance_and_the_current_page_is_unchanged(tmp_path):
    path = legacy_store(tmp_path / "f.duckdb")
    page = page_view(path)
    result = vintages.apply(path, cache_dir=tmp_path / "cache", now=datetime(2026, 11, 14, 9, 0))
    assert result.seeded > 0 and result.migration_id
    assert page_view(path) == page
    with FundamentalsStore(path, read_only=True) as store:
        rows = store.versions("TCS", "consolidated", "income")
        assert rows and {r["provenance"] for r in rows} == {"seeded_from_latest"}
        assert all(r["ingested"] is None and r["source_hash"] is None and r["filing_url"] == "rev" and "unknown" in r["parser_version"] for r in rows)
        assert sales_at(store, CUT_2024) is None  # the 2024 original is NOT invented from today's restatement
        assert sales_at(store, CUT_LATE_2025) == 120.0


def test_apply_twice_is_idempotent(tmp_path):
    path = legacy_store(tmp_path / "f.duckdb")
    first = vintages.apply(path, cache_dir=tmp_path / "cache")
    with FundamentalsStore(path, read_only=True) as store:
        snapshot = store._con.execute("SELECT * FROM statement_versions ORDER BY ALL").fetchall()
    second = vintages.apply(path, cache_dir=tmp_path / "cache")
    assert first.seeded > 0 and second.seeded == 0 and second.rebuilt == 0
    with FundamentalsStore(path, read_only=True) as store:
        assert store._con.execute("SELECT * FROM statement_versions ORDER BY ALL").fetchall() == snapshot


def raw_original(tmp_path, url="orig"):
    """The kept raw filing of the 2024 original, in the same cache the ingest writes (sha1 of the url)."""
    text = (FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    ingest.raw_path(cache, url).write_text(text, encoding="utf-8")
    return text


def test_an_older_version_is_rebuilt_only_from_a_kept_raw_filing_and_labelled_as_such(tmp_path):
    path = legacy_store(tmp_path / "f.duckdb")
    text = raw_original(tmp_path)
    plan = vintages.plan(path, cache_dir=tmp_path / "cache")
    assert plan.rebuilt > 0 and plan.originals_unavailable == []
    vintages.apply(path, cache_dir=tmp_path / "cache")
    with FundamentalsStore(path, read_only=True) as store:
        original = [r for r in store.versions("TCS", "consolidated", "income") if r["filing_url"] == "orig"]
        assert original and {r["provenance"] for r in original} == {"rebuilt_from_raw"}
        assert {r["source_hash"] for r in original} == {hashlib.sha256(text.encode()).hexdigest()}
        assert sales_at(store, CUT_2024) == base().income[FY24]["sales"]  # the original, recovered from the raw filing
        assert sales_at(store, CUT_LATE_2025) == 120.0 and store.lines("TCS", "consolidated", "income")[FY24]["sales"] == 120.0


def test_a_missing_original_stays_missing_after_the_migration(tmp_path):
    path = legacy_store(tmp_path / "f.duckdb")
    result = vintages.apply(path, cache_dir=tmp_path / "cache")  # no raw copy of "orig" is held
    assert [u["url"] for u in result.originals_unavailable] == ["orig"]
    with FundamentalsStore(path, read_only=True) as store:
        assert sales_at(store, CUT_2024) is None
        assert store.gaps_as_of("TCS", "consolidated", "income", CUT_2024)


def test_rollback_removes_only_what_the_migration_added_and_the_current_page_is_the_same_before_and_after(tmp_path):
    path = legacy_store(tmp_path / "f.duckdb")
    raw_original(tmp_path)
    page = page_view(path)
    result = vintages.apply(path, cache_dir=tmp_path / "cache")
    with FundamentalsStore(path) as store:
        store.save_statements(stmt(60.0, basis="standalone"), "later", datetime(2026, 1, 5, tzinfo=UTC))  # an ordinary ingest after the migration
    removed = vintages.rollback(path, result.migration_id)
    assert removed == result.seeded + result.rebuilt
    with FundamentalsStore(path, read_only=True) as store:
        assert {r["filing_url"] for r in store.versions("TCS")} == {"later"}  # the post-migration version is not the migration's to remove
        assert store._con.execute("SELECT count(*) FROM migrations WHERE migration_id = ?", [result.migration_id]).fetchone()[0] == 0
    assert page_view(path)[0] == page[0]  # the destructive table was never touched by apply or rollback: the current page reads what it read before
    again = vintages.apply(path, cache_dir=tmp_path / "cache")
    assert again.seeded == result.seeded and again.rebuilt == result.rebuilt  # and the migration can be run again after a rollback


def test_rolling_back_an_unknown_migration_is_refused(tmp_path):
    path = legacy_store(tmp_path / "f.duckdb")
    vintages.apply(path, cache_dir=tmp_path / "cache")
    with pytest.raises(AthenaError, match="no migration"):
        vintages.rollback(path, "not-a-migration")


def test_a_failure_part_way_leaves_no_partial_migration(tmp_path, monkeypatch):
    path = legacy_store(tmp_path / "f.duckdb")
    raw_original(tmp_path)
    monkeypatch.setattr(vintages, "parse_rows", lambda text: (_ for _ in ()).throw(RuntimeError("disk full")))
    with pytest.raises(RuntimeError):
        vintages.apply(path, cache_dir=tmp_path / "cache")
    with FundamentalsStore(path, read_only=True) as store:
        assert store._con.execute("SELECT count(*) FROM statement_versions").fetchone()[0] == 0
        assert store._con.execute("SELECT count(*) FROM migrations").fetchone()[0] == 0


def test_a_store_from_a_newer_schema_is_refused(tmp_path):
    path = legacy_store(tmp_path / "f.duckdb")
    vintages.apply(path, cache_dir=tmp_path / "cache")
    with FundamentalsStore(path) as store:
        store._con.execute("UPDATE schema_meta SET value = '99' WHERE name = 'statement_versions'")
    with pytest.raises(AthenaError, match="newer"):
        vintages.apply(path, cache_dir=tmp_path / "cache")


def test_the_tool_works_on_a_copy_and_leaves_the_original_file_byte_identical(tmp_path):
    source = legacy_store(tmp_path / "f.duckdb")
    before = file_hash(source)
    copy = tmp_path / "copy.duckdb"
    assert vintages.main(["--store", str(source), "--copy-to", str(copy), "--apply", "--cache-dir", str(tmp_path / "cache")]) == 0
    assert file_hash(source) == before
    with FundamentalsStore(copy, read_only=True) as store:
        assert store.has_versions()
    assert vintages.main(["--store", str(source), "--copy-to", str(copy), "--apply", "--cache-dir", str(tmp_path / "cache")]) != 0  # an existing copy is never overwritten


def test_the_tool_refuses_to_apply_in_place_unless_it_is_told_the_file_is_a_copy_and_never_touches_the_default_store(tmp_path, monkeypatch):
    source = legacy_store(tmp_path / "f.duckdb")
    assert vintages.main(["--store", str(source), "--apply", "--cache-dir", str(tmp_path / "cache")]) != 0  # in place needs --in-place
    with FundamentalsStore(source, read_only=True) as store:
        assert not store.has_versions()
    monkeypatch.chdir(tmp_path)
    production = tmp_path / ".athena" / "fundamentals.duckdb"
    production.parent.mkdir()
    legacy_store(production)
    for args in (["--apply"], ["--apply", "--in-place"], ["--rollback", "x", "--in-place"], []):  # every form, a dry run included: the default store is production, and this tool does not touch it
        assert vintages.main(["--store", str(production), *args]) != 0, args
    with FundamentalsStore(production, read_only=True) as store:
        assert not store.has_versions()


def test_a_dry_run_through_the_command_prints_counts_and_exits_clean(tmp_path, capsys):
    source = legacy_store(tmp_path / "f.duckdb")
    before = file_hash(source)
    assert vintages.main(["--store", str(source), "--cache-dir", str(tmp_path / "cache")]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and "rows to seed" in out and "originals unavailable" in out and "orig" in out
    assert file_hash(source) == before


# ======================================== the research reader and the ingest ========================================
def test_the_valuation_study_reads_the_version_public_at_its_cutoff_not_the_latest_table_filtered_by_date():
    from athena.valuation.calibrate import AsOfFundamentals

    with FundamentalsStore(":memory:") as store:
        store.save_statements(stmt(100.0), "orig", FILED_2024)
        store.save_statements(stmt(120.0), "rev", FILED_2025)
        vintage = AsOfFundamentals(store, CUT_2024, {}, policy="vintage")
        approximate = AsOfFundamentals(store, CUT_2024, {}, policy="latest_filtered")
        assert vintage.lines("TCS", "consolidated", "income")[FY24]["sales"] == 100.0 and vintage.policy == "vintage"
        assert approximate.lines("TCS", "consolidated", "income") == {}  # the defect: the original is gone, so the period drops out
        assert approximate.policy == "latest_filtered" and "approximate" in approximate.label
        assert AsOfFundamentals(store, CUT_2024, {}).policy == "vintage"  # auto: versions exist, so the strict reader is used


def test_auto_policy_falls_back_to_the_approximate_reader_only_when_there_are_no_versions_and_says_so(tmp_path):
    from athena.valuation.calibrate import AsOfFundamentals

    path = legacy_store(tmp_path / "f.duckdb")
    with FundamentalsStore(path, read_only=True) as store:
        auto = AsOfFundamentals(store, CUT_LATE_2025, {})
        assert auto.policy == "latest_filtered" and "approximate" in auto.label
        assert auto.lines("TCS", "consolidated", "income")[FY24]["sales"] == 120.0
        with pytest.raises(AthenaError, match="no statement versions"):
            AsOfFundamentals(store, CUT_LATE_2025, {}, policy="vintage").lines("TCS", "consolidated", "income")


def test_ingest_can_keep_every_filed_version_of_a_period_and_by_default_still_takes_the_latest_only():
    from athena.pit.nse_results import FilingRef

    ist = timezone(timedelta(hours=5, minutes=30))
    original = FilingRef("TCS", FY24[1], datetime(2024, 4, 10, tzinfo=ist), True, True, "orig")
    revised = FilingRef("TCS", FY24[1], datetime(2025, 5, 10, tzinfo=ist), True, True, "rev")
    assert [r.url for r in ingest.wanted([original, revised])] == ["rev"]
    assert sorted(r.url for r in ingest.wanted([original, revised], all_versions=True)) == ["orig", "rev"]


def test_ingest_writes_versions_with_the_raw_filings_hash(tmp_path):
    from athena.pit.nse_results import FilingRef

    ist = timezone(timedelta(hours=5, minutes=30))
    text = (FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8")
    filing = FilingRef("TCS", FY24[1], datetime(2024, 4, 10, tzinfo=ist), True, True, "tcs24")

    class Results:
        def filings(self, symbol):
            return [filing]

        def text(self, ref):
            return text

    from athena.fundamentals.universe import Member

    ingest.ingest_symbol(Member("TCS", "Tata Consultancy Services", "IT", "INE467B01029"), Results(), tmp_path / "f.duckdb", cache_dir=tmp_path / "cache")
    with FundamentalsStore(tmp_path / "f.duckdb", read_only=True) as store:
        rows = store.versions("TCS", "consolidated", "income")
        assert rows and {r["source_hash"] for r in rows} == {hashlib.sha256(text.encode()).hexdigest()} and {r["provenance"] for r in rows} == {"ingest"}
