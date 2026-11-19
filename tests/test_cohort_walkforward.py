"""WP04 step 5 (Q5): a signal study on a cohort frozen from information available at selection time, in which no company is dropped because its outcome is unknown.

The rules are the locked ones in `athena.evaluation.policy` (docs/audit-2026-11-12/WP04-STUDY-POLICY.md). Synthetic panels only; nothing here reads a real price store."""
import json
from datetime import date, timedelta

import numpy as np
import pytest

from athena.evaluation import cohort as co
from athena.evaluation import walkforward as wf
from athena.evaluation.policy import POLICY

N_DAYS = 80
DAYS = [date(2024, 1, 1) + timedelta(days=i) for i in range(N_DAYS)]
I, H = 30, 10  # the selection index and the horizon


def flat(value, n=N_DAYS):
    return np.full(n, float(value))


def make(names, *, min_price_adj=0.0):
    """`names`: symbol -> dict(adj=array, raw=array, traded=array). Every array is on the common DAYS."""
    return wf.Panel(DAYS, {s: v["adj"] for s, v in names.items()}, {s: v["raw"] for s, v in names.items()}, {s: v["traded"] for s, v in names.items()}, min_price_adj)


def liquid():
    return flat(5e7)


def stock(adj, raw=None, traded=None):
    adj = np.asarray(adj, dtype=float)
    return {"adj": adj, "raw": adj.copy() if raw is None else np.asarray(raw, dtype=float), "traded": liquid() if traded is None else traded}


def rising(start=100.0, step=0.5):
    return start + step * np.arange(N_DAYS)


def universe(n=30):
    """n ordinary names, each rising at a different pace, so a score proportional to the pace ranks them like their later return."""
    return {f"S{k:02d}": stock(rising(100.0, 0.2 + 0.05 * k)) for k in range(n)}


def pace_signal(panel, i):
    """The score of a name is its pace (return from the first to the selection day): a rule that, on this panel, genuinely predicts the later return."""
    return {s: a[i] / a[0] - 1.0 for s, a in panel.px.items() if np.isfinite(a[i])}


# ------------------------------------------------------------------ the cohort is frozen from what was known on the date
def test_the_panel_still_builds_from_adjusted_closes_alone_and_the_old_price_floor_is_its_default():
    panel = wf.Panel.build({"A": [(date(2024, 1, 1), 5.0), (date(2024, 1, 2), 6.0)]})
    assert panel.raw is None and panel.traded is None and panel.min_price == wf.MIN_PRICE


def test_eligibility_uses_the_price_as_quoted_on_the_date_not_one_adjusted_for_later_splits():
    names = universe()
    names["SPLIT"] = stock(flat(5.0), raw=flat(50.0))  # quoted at 50 then, 5 after a later 10-for-1 split: eligible on the date
    names["PENNY"] = stock(flat(8.0), raw=flat(8.0))  # quoted at 8: below the floor then too
    frozen = co.freeze_cohort(make(names), I)
    assert "SPLIT" in frozen.members and "PENNY" not in frozen.members
    assert frozen.ineligible == {"price below the floor": 1}
    assert wf.MIN_PRICE > 5.0  # the legacy signals test the ADJUSTED level (5.0 here) against that floor and would have dropped SPLIT


def test_liquidity_is_the_median_traded_value_before_entry_and_a_name_that_did_not_trade_every_session_is_ineligible():
    names = universe(3)
    thin = flat(1e6)  # ₹10 lakh a session: under the ₹1 crore floor
    names["THIN"] = stock(rising(), traded=thin)
    gap = liquid()
    gap[I - 3] = np.nan  # did not trade on one of the last 20 sessions
    names["GAP"] = stock(rising(), traded=gap)
    names["NOHIST"] = stock(rising(), traded=np.full(N_DAYS, np.nan))
    frozen = co.freeze_cohort(make(names), I)
    assert set(frozen.members) == {"S00", "S01", "S02"}
    assert frozen.ineligible == {"median traded value below the floor": 1, "no traded value on every one of the last 20 sessions": 2}
    assert frozen.universe == 6 and len(frozen.members) + sum(frozen.ineligible.values()) == frozen.universe  # every name is accounted for


def test_a_name_with_no_close_on_the_date_is_not_in_the_universe_at_all():
    names = universe(3)
    gone = rising().copy()
    gone[I:] = np.nan  # stopped trading before the selection date
    names["GONE"] = stock(gone)
    frozen = co.freeze_cohort(make(names), I)
    assert frozen.universe == 3 and "GONE" not in frozen.members


def test_nothing_after_the_selection_date_can_change_the_cohort():
    names = universe(5)
    first = co.freeze_cohort(make(names), I)
    for v in names.values():  # a delisting, a collapse, a crazy later price: none of it was known on the date
        v["adj"][I + 1:] = np.nan
        v["raw"][I + 1:] = 0.0
        v["traded"][I + 1:] = np.nan
    again = co.freeze_cohort(make(names), I)
    assert again.members == first.members and again.fingerprint == first.fingerprint and again.ineligible == first.ineligible


# ------------------------------------------------------------------ outcomes: nobody is dropped
def test_a_name_with_a_close_at_the_end_is_priced_and_one_that_stopped_trading_has_unknown_proceeds_under_two_named_scenarios():
    ok = stock(rising(100.0, 1.0))
    stopped_adj = rising(100.0, 1.0).copy()
    stopped_adj[I + 4 :] = np.nan  # last traded 3 sessions after selection
    panel = make({"OK": ok, "STOP": stock(stopped_adj)})
    got = co.outcome(panel, "OK", I, H)
    assert got.status == "priced" and got.forward == pytest.approx(ok["adj"][I + H] / ok["adj"][I] - 1) and got.loss_case == got.last_mark_case == got.forward
    lost = co.outcome(panel, "STOP", I, H)
    assert lost.status == "no_end_price" and lost.forward is None
    assert lost.loss_case == -1.0  # the assumed total-loss scenario
    assert lost.last_mark_case == pytest.approx(stopped_adj[I + 3] / stopped_adj[I] - 1)  # the last close it traded at, against the entry close


def test_a_stop_before_any_later_close_has_a_last_close_scenario_of_zero():
    adj = rising().copy()
    adj[I + 1 :] = np.nan
    got = co.outcome(make({"X": stock(adj)}), "X", I, H)
    assert got.status == "no_end_price" and got.last_mark_case == 0.0 and got.loss_case == -1.0


def test_a_restructuring_inside_the_window_makes_the_return_unknown_not_missing_and_a_raw_price_return_is_shown_apart():
    panel = make({"D": stock(rising(), raw=rising() * 2)})
    unadjustable = {"D": [DAYS[I + 5]]}
    got = co.outcome(panel, "D", I, H, unadjustable)
    assert got.status == "restructured" and got.forward is None and got.loss_case is None and got.last_mark_case is None
    assert got.raw_price == pytest.approx(panel.raw["D"][I + H] / panel.raw["D"][I] - 1)  # named sensitivity, never the result
    assert co.outcome(panel, "D", I, H, {"D": [DAYS[I - 5]]}).status == "priced"  # before the entry: already in the price
    assert co.outcome(panel, "D", I, H, {"D": [DAYS[I + H + 3]]}).status == "priced"  # after the window: not this window's concern


# ------------------------------------------------------------------ the study
def with_failures():
    names = universe(30)
    top = stock(rising(100.0, 5.0))  # the best score by far...
    top["adj"][I + 3 :] = np.nan  # ...and then it stops trading
    names["TOPSTOP"] = top
    names["DEMERGE"] = stock(rising(100.0, 0.4))
    return names, {"DEMERGE": [DAYS[I + 2]]}


def test_the_cohort_study_keeps_the_stopped_and_the_restructured_in_its_accounting_where_the_old_study_dropped_them(monkeypatch):
    names, actions = with_failures()
    panel = make(names)
    monkeypatch.setattr(wf, "MIN_NAMES", 20)
    old = wf.run(wf.Panel.build({s: [(d, v) for d, v in zip(DAYS, a["adj"]) if np.isfinite(v)] for s, a in names.items()}), pace_signal, DAYS[I], H)
    assert old[0]["n"] == 31  # the legacy harness: TOPSTOP (no close at the end) is gone from its population, and DEMERGE is measured as if nothing happened
    rows = co.run_cohort(panel, pace_signal, DAYS[I], H, unadjustable=actions, min_names=20)
    row = rows[0]
    assert row["accounting"]["scored"] == 32 and row["accounting"]["priced"] == 30 and row["accounting"]["no_end_price"] == 1 and row["accounting"]["restructured"] == 1
    assert row["accounting"]["unknown_share"] == pytest.approx(2 / 32)
    assert row["association"]["measurable"]["n"] == 30  # the measurable-only association: the old study's population
    assert row["association"]["zero_recovery"]["n"] == 31 and row["association"]["last_close"]["n"] == 31  # the stopped name is in both scenarios; the restructured one is in neither, and counted


def test_the_two_terminal_scenarios_give_an_interval_and_a_high_scoring_name_that_collapsed_lowers_the_total_loss_end():
    names, actions = with_failures()
    rows = co.run_cohort(make(names), pace_signal, DAYS[I], H, unadjustable=actions, min_names=20)
    a = rows[0]["association"]
    assert a["measurable"]["ic"] > 0.9  # the rule works on the names that could be measured
    assert a["zero_recovery"]["ic"] < a["last_close"]["ic"] < a["measurable"]["ic"] + 1e-9  # the best-scored name collapsed: counting it at -100% hurts the rule most
    for scenario in ("zero_recovery", "last_close"):
        assert a[scenario]["ic"] != a["measurable"]["ic"]


def test_the_report_is_an_association_with_a_scenario_interval_and_says_portfolio_returns_are_not_computed():
    names, actions = with_failures()
    rows = co.run_cohort(make(names), pace_signal, DAYS[I], H, unadjustable=actions, min_names=20)
    rep = co.report_cohort(rows)
    assert rep["study"] == "association" and rep["portfolio_returns"] == "not_computed" and rep["study_policy"] == POLICY.fingerprint()
    lo, hi = rep["overall"]["scenario_interval_mean_ic"]
    assert lo <= hi and lo == pytest.approx(rep["overall"]["zero_recovery"]["mean_ic"]) and hi == pytest.approx(rep["overall"]["last_close"]["mean_ic"])
    acc = rep["cohort_accounting"]
    assert acc["no_end_price"] == sum(r["accounting"]["no_end_price"] for r in rows) == 1 and acc["restructured"] == sum(r["accounting"]["restructured"] for r in rows) == 1
    assert acc["unknown_share"] == pytest.approx(2 / sum(r["accounting"]["scored"] for r in rows)) and acc["scored"] == sum(r["accounting"]["scored"] for r in rows)
    assert "mean_top_minus_bottom" in rep["overall"]["measurable"] and "portfolio" not in rep["overall"]  # an association study: no portfolio return is in it
    json.dumps(rep, default=str)


def test_the_terminal_interval_is_labelled_a_sensitivity_interval_of_assumptions_and_never_a_bound():
    names, actions = with_failures()
    rep = co.report_cohort(co.run_cohort(make(names), pace_signal, DAYS[I], H, unadjustable=actions, min_names=20))
    text = co.format_cohort("pace", rep)
    assert "sensitivity interval" in rep["scenario_note"] and "could exceed the last price" in rep["scenario_note"] and "No aggregate here is a bounded return" in rep["scenario_note"]
    assert "assumed scenarios" in text and "not a bound" in text and "proceeds could exceed the last price" in text
    assert "range_mean_ic" not in rep["overall"] and "scenario_interval_mean_ic" in rep["overall"] and all("scenario_interval_mean_ic" in year for year in rep["years"].values())
    assert "bound" not in rep["label"] and POLICY.terminal_interval_is_bound is False and "bounded" not in POLICY.to_dict().values()


def test_the_cohort_study_does_not_apply_the_adjusted_price_floor_of_the_old_signals():
    names = universe(30)
    names["SPLIT"] = stock(rising(1.0, 0.01) , raw=rising(50.0, 0.5))  # adjusted level ~1-2 (after a later 50-for-1), quoted ~50 then
    rows = co.run_cohort(make(names), pace_signal, DAYS[I], H, min_names=20)
    assert rows[0]["accounting"]["scored"] == 31 and rows[0]["accounting"]["ineligible"] == {}


def test_too_few_names_gives_no_row_and_nobody_is_silently_renormalised():
    assert co.run_cohort(make(universe(10)), pace_signal, DAYS[I], H, min_names=20) == []


def test_ineligible_names_are_counted_by_reason_in_every_row():
    names = universe(30)
    names["PENNY"] = stock(flat(8.0))
    rows = co.run_cohort(make(names), pace_signal, DAYS[I], H, min_names=20)
    assert rows[0]["accounting"]["ineligible"] == {"price below the floor": 1} and rows[0]["accounting"]["universe"] == 31


# ------------------------------------------------------------------ loading the panel
class FakeWarehouse:
    def __init__(self, bars, actions):
        self._bars, self._actions = bars, actions

    def universe(self, kind):
        return [{"symbol": s} for s in self._bars]

    def bars(self, symbol, since=None, until=None):
        return self._bars[symbol]

    def actions(self, symbol):
        return self._actions.get(symbol, [])


def test_the_command_writes_the_cohort_report_to_its_own_file_with_the_policy_and_never_to_the_legacy_report(tmp_path, monkeypatch, capsys):
    from datetime import datetime, timezone

    from athena.contracts import Bar
    from athena.warehouse import history as history_module
    from athena.warehouse import warehouse as warehouse_module

    rng = np.random.default_rng(3)
    bars = {}
    for k in range(120):
        price = 100.0 + rng.normal(0, 1) * np.arange(N_DAYS).cumsum() * 0.01 + k  # a slow drift per name
        bars[f"N{k:03d}"] = [Bar(f"N{k:03d}", datetime(d.year, d.month, d.day, tzinfo=timezone.utc), p, p, p, p, 1_000_000.0, datetime(d.year, d.month, d.day, tzinfo=timezone.utc), "t") for d, p in zip(DAYS, price)]

    class Warehouse(FakeWarehouse):
        def __init__(self, *a, **k):
            super().__init__(bars, {})

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(warehouse_module, "Warehouse", Warehouse)
    monkeypatch.setattr(history_module, "adjusted_history", lambda w: (lambda symbol: bars[symbol]))
    monkeypatch.chdir(tmp_path)
    assert wf.main(["--db", "unused", "--since", "2024-01-25", "--horizon", "10"]) == 0
    written = json.loads((tmp_path / ".athena" / "reports" / "walkforward-cohort.json").read_text(encoding="utf-8"))
    assert written["study_policy_fingerprint"] == POLICY.fingerprint() and written["reversal_1m"]["portfolio_returns"] == "not_computed"
    assert written["reversal_1m"]["cohort_accounting"]["dates"] >= 1 and written["reversal_1m"]["study"] == "association"
    assert not (tmp_path / ".athena" / "reports" / "walkforward.json").exists()  # the earlier report was not written, so it cannot have been overwritten
    assert "cohort study" in capsys.readouterr().out


def test_the_panel_is_loaded_with_nominal_closes_traded_values_and_the_dates_of_unadjustable_actions_only():
    from datetime import datetime, timezone

    from athena.contracts import Bar
    from athena.price_integrity.actions import CorporateAction

    def bar(day, close, volume):
        stamp = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        return Bar("X", stamp, close, close, close, close, volume, stamp, "test")

    bars = {"X": [bar(date(2024, 1, 1), 50.0, 1000.0), bar(date(2024, 1, 2), 52.0, 2000.0)], "EMPTY": []}
    actions = {"X": [CorporateAction(date(2024, 2, 1), "demerger", None, "d"), CorporateAction(date(2024, 2, 2), "split", 10.0, "s"), CorporateAction(date(2024, 2, 3), "capital_reduction", None, "c")]}
    adjusted = lambda symbol: [(b.timestamp.date(), b.close / 10) for b in bars[symbol]]  # noqa: E731
    panel, unadj = co.load_panel(FakeWarehouse(bars, actions), adjusted)
    assert list(panel.px) == ["X"] and panel.raw["X"].tolist() == [50.0, 52.0] and panel.traded["X"].tolist() == [50.0 * 1000.0, 52.0 * 2000.0]
    assert panel.px["X"].tolist() == [5.0, 5.2]
    assert unadj == {"X": [date(2024, 2, 1), date(2024, 2, 3)]}  # a split is adjusted for; a demerger and a capital reduction are not
    assert panel.min_price == 0.0  # the old adjusted-price floor is off: eligibility is the cohort's nominal rule
