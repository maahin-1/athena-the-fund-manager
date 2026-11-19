"""WP04 step 4: index membership is kept as dated snapshots, so a past date can read the membership that was observed by then. The current table behaves exactly as before.
A date before the first snapshot has no membership available: it is reported, not read from today's table. Temporary warehouse only."""
from datetime import date, datetime

from athena.warehouse.warehouse import Warehouse


def rows(*symbols):
    return [("NIFTY 50", s, "IT") for s in symbols]


def test_membership_snapshots_are_kept_and_read_as_of_a_date(tmp_path):
    with Warehouse(tmp_path / "w.duckdb") as w:
        w.replace_members(rows("A", "B"), datetime(2026, 1, 10, 9, 0))
        w.replace_members(rows("B", "C"), datetime(2026, 6, 10, 9, 0))  # A left, C joined
        assert {r[1] for r in w._con.execute("SELECT * FROM index_members").fetchall()} == {"B", "C"}  # the current table: replaced, as it always was
        assert w.members_as_of("NIFTY 50", date(2026, 1, 9)) is None  # before the first snapshot this system took: unavailable
        assert w.members_as_of("NIFTY 50", date(2026, 2, 1)) == {"A", "B"}
        assert w.members_as_of("NIFTY 50", date(2026, 12, 1)) == {"B", "C"}
        assert w.members_as_of("NIFTY 500", date(2026, 12, 1)) is None


def test_a_failed_refresh_leaves_the_previous_snapshot_and_no_partial_one(tmp_path):
    with Warehouse(tmp_path / "w.duckdb") as w:
        w.replace_members(rows("A"), datetime(2026, 1, 10, 9, 0))
        def broken():
            yield ("NIFTY 50", "B", "IT")
            raise RuntimeError("the list could not be read to the end")

        try:
            w.replace_members(broken(), datetime(2026, 6, 10, 9, 0))
        except RuntimeError:
            pass
        assert w.members_as_of("NIFTY 50", date(2026, 12, 1)) == {"A"}
        assert {r[1] for r in w._con.execute("SELECT * FROM index_members").fetchall()} == {"A"}


def test_the_same_snapshot_taken_twice_is_one_snapshot(tmp_path):
    with Warehouse(tmp_path / "w.duckdb") as w:
        w.replace_members(rows("A", "B"), datetime(2026, 1, 10, 9, 0))
        w.replace_members(rows("A", "B"), datetime(2026, 1, 10, 9, 0))
        assert w._con.execute("SELECT count(*) FROM index_member_versions").fetchone()[0] == 2
