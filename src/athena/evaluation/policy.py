"""The rules of the historical studies, written down BEFORE any comparison was run, and locked.

A study that picks its cohort, its treatment of missing data, its corporate-action rule or its terminal outcome after seeing which choice flatters the result is not a test. These four policies are
therefore fixed here, in one frozen object with a fingerprint that a test pins: changing a rule changes the fingerprint and fails that test until the change is made deliberately, in the open, and
the document `docs/audit-2026-11-12/WP04-STUDY-POLICY.md` is updated with it. Every study result carries the fingerprint it ran under, so a result can always be tied to the rules that produced it.

Chronology of the fingerprint, all before any result was inspected (the history is kept in the policy document): `a02ccf4140e7a48d` as first written; `d3681d1ffd4e7c83` after a traded value was required on every
liquidity session; and the current one after the terminal-outcome labels were corrected on review: the two terminal scenarios (total loss, last traded price) are a sensitivity interval of assumptions, not
bounds on the truth. That change renamed and clarified labels and added `terminal_interval_is_bound: False`; no rule's behaviour changed.

The numbers below (the price floor, the traded-value floor, the session count) were chosen as round figures on 14 November 2026, before any result was inspected, and are not tuned: no run of
these studies on the real stores had been made. They are a stated assumption, not a finding.

Two input policies exist for the valuation study and are never pooled: `strict_dated` (every input is the one available on the study date; a missing one is reported and the company abstains) and
`approximate_current` (the earlier behaviour: today's share count, rates, industry tables and company map applied to a past date, labelled as such)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

POLICY_VERSION = "wp04-study-policy/1"
STRICT_DATED = "strict_dated"
APPROXIMATE_CURRENT = "approximate_current"
INPUT_POLICY_LABELS = {
    STRICT_DATED: "strict: every input is the version available on the study date; a missing input is reported and the company abstains, never filled with today's value",
    APPROXIMATE_CURRENT: "approximate: today's share count, rates, industry tables and company-to-industry map applied to a past date (a retrospective sensitivity study, not a replay of what an analyst could have done)",
}
# What a terminal (no end price) or restructured name is counted as, by name. Never a dropped observation.
ZERO_RECOVERY, LAST_CLOSE, RAW_PRICE = "zero_recovery", "last_close", "raw_price"


@dataclass(frozen=True)
class StudyPolicy:
    version: str = POLICY_VERSION

    # --- cohort: who is in a study, from information available when the selection is made ---
    cohort_universe: str = "every security with a close on the selection date"  # not today's listed universe
    cohort_frozen: bool = True  # the set and each member's eligibility are fixed on the selection date; nothing later removes a member
    min_nominal_price: float = 10.0  # rupees: the close as quoted on the date, NOT adjusted for later splits or bonuses
    min_median_traded_value: float = 10_000_000.0  # rupees: median of close x volume over the sessions before entry; a name with no such history is ineligible and counted, not guessed
    liquidity_sessions: int = 20  # sessions up to and including the selection date
    liquidity_needs_every_session: bool = True  # a traded value is needed on each of those sessions: a name that did not trade on one of them is, by that, not liquid enough to select
    future_information_in_selection: bool = False  # later corporate actions, delistings and filings never decide who is eligible

    # --- missing data ---
    missing_input: str = "abstain_and_report"  # strict mode: a required dated input that does not exist for the date is named, the company abstains from that signal and stays in the cohort accounting
    substitute_today: bool = False  # today's value is never used for a past date in a strict study; an approximate study is a separate, labelled one

    # --- corporate actions ---
    split_and_bonus: str = "adjust_returns"  # returns are adjusted by the exchange's listed ratios between entry and exit
    restructuring: str = "unknown_outcome"  # a demerger, scheme, capital reduction or unreadable ratio between entry and exit: the return is unknown from price alone; the name stays in the cohort
    restructuring_sensitivity: str = RAW_PRICE  # shown beside, named, never as the result: the unadjusted price return
    share_count: str = "latest_filing_by_date_adjusted_for_splits_and_bonuses"  # placements, buybacks, warrants and mergers after the filing are NOT reconstructed (a stated limit)

    # --- terminal outcomes ---
    no_end_price: str = "scenario_interval"  # a name that has no close at the end of its window (suspended, delisted) is in the cohort with unknown proceeds, shown under two ASSUMED scenarios as a sensitivity interval
    terminal_loss_scenario: str = ZERO_RECOVERY  # assumed scenario: total loss, a return of -100%
    terminal_last_mark_scenario: str = LAST_CLOSE  # assumed scenario: the position is valued at the last price it traded at, from the entry close
    terminal_interval_is_bound: bool = False  # the two scenarios are not limits on the truth: recovery or liquidation proceeds can exceed the last traded price, and no aggregate built on them is a bounded return
    report_unknown_share: bool = True  # the share of the cohort with an unknown outcome is part of every result

    # --- what a result may call itself ---
    signal_study: str = "association"  # rank correlation and quintile spreads of a signal with later returns on the cohort
    portfolio_returns: str = "not_computed"  # an executable portfolio return needs an entry rule (the next session's open), costs and a liquidity model; none is part of this study and the two are never mixed

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def fingerprint(self) -> str:
        """A short hash of every rule above: the identity a result carries."""
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode("utf-8")).hexdigest()[:16]


POLICY = StudyPolicy()
