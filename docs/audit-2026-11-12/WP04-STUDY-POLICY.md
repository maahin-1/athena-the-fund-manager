# WP04 study policy (locked before any comparison)

Written 14 November 2026, **before any run of the dated-input valuation study or the cohort walk-forward on a real store** (none has been made: the real `.athena` stores have not been opened). It fixes the four
policies below so that methodology cannot be chosen to improve a result. The same rules are in code as `athena.evaluation.policy.StudyPolicy`; its fingerprint, `84d45dbff07362fd`, is pinned by
`tests/test_study_policy.py` and recorded in every result. Changing a rule means changing the fingerprint, which fails that test until this document and the test are changed on purpose and in the open.

The numeric floors are round figures chosen a priori, not tuned. They are assumptions: they predate any result, but a predeclared threshold is not thereby justified. Each still needs a later justification and a sensitivity test
(different price and traded-value floors, a longer liquidity window) before a result that depends on it is relied on; none has been made.

## Chronology of the fingerprint

All three were set before any result of either study existed. Each earlier fingerprint is kept, with why it changed.

| # | Fingerprint | Change |
|---|---|---|
| 1 | `a02ccf4140e7a48d` | The four policies as first written on 14 November 2026. |
| 2 | `d3681d1ffd4e7c83` | 14 November 2026, while writing the cohort code and before any result: a traded value is required on **each** of the 20 liquidity sessions (`liquidity_needs_every_session`). The first wording was silent on a session with no trade; this made the rule explicit and counted such a name as ineligible instead of guessing its value. |
| 3 | `84d45dbff07362fd` | 14 November 2026, on review and before any result: the terminal-outcome labels were corrected. The interval between `zero_recovery` and `last_close` is a **sensitivity interval of two assumed scenarios, not a bound**. Recovery or liquidation proceeds can exceed the last traded price, so neither end limits the true outcome and no aggregate built on them is a bounded return. The rule value `bounded` became `scenario_interval`, `terminal_lower`/`terminal_upper` became `terminal_loss_scenario`/`terminal_last_mark_scenario`, and `terminal_interval_is_bound: false` was added. No rule's behaviour changed. |

## 1. Cohort policy

- **Universe:** every security that has a close on the selection date, from price history as it stood then. Not today's list of listed companies, which would drop everything that later stopped trading.
- **Frozen at selection:** the set, and each member's eligibility, are fixed on the selection date from information available on it. Nothing that happens later (a delisting, suspension, demerger, restatement) removes a member.
- **Eligibility (all from the date):** the close as quoted that day (the *nominal* price, not one adjusted for later splits or bonuses) of at least ₹10; the median of close × volume over the 20 sessions up to and including
  the date of at least ₹1 crore (a measure available before entry; a traded value is needed on **each** of the 20 sessions, so a name that did not trade on one of them is ineligible and counted, never
  guessed). Every excluded name is counted by reason. (This requirement was added on 14 November 2026 while writing the cohort code and before any result existed: fingerprint 1 to 2 in the chronology above.)
- **Open limitation (listing history):** the cohort can only contain securities the warehouse holds prices for; one it never loaded cannot be in it, and no dated listing table exists (`index_member_versions` only starts when snapshots are first taken). This is tracked separately and is not resolved.
- **Future information:** later corporate actions, delistings and filings never decide eligibility.

## 2. Missing-data policy

- **Strict study (`strict_dated`):** each input must be the version available on the study date: the company's share count from the latest filing by the date, risk-free and premium inputs, industry tables, the
  company-to-industry map, and index membership, each from its own dated row. If one does not exist for that date, the company **abstains** from the signal, the missing input is named, and the company stays in the cohort
  accounting. **Today's value is never substituted.**
- **Approximate study (`approximate_current`):** the earlier behaviour (today's inputs applied to the past). It is kept, labelled as a retrospective sensitivity study, written to its own file, and never pooled with a strict result.

## 3. Corporate-action policy

- **Splits and bonuses:** returns are adjusted by the exchange's listed ratios between entry and exit; share counts by the ratios after the filing's period end.
- **Demerger, scheme, capital reduction or an unreadable ratio between entry and exit:** the return **cannot be read from the price alone**, so the outcome is *unknown*; the name stays in the cohort and counts in the unknown
  share. The unadjusted price return may be shown beside it, named `raw_price`, never as the result.
- **Open limitation (capital changes):** placements, buybacks, warrants and mergers after the filing are not reconstructed from splits. Tracked separately; the share count is an approximation wherever one occurred.

## 4. Terminal-outcome policy

- A name with **no close at the end of its window** (suspended or delisted) stays in the cohort with **unknown proceeds**. It is shown under two **assumed scenarios**, both named: `zero_recovery` (an assumed total loss,
  a return of −100%) and `last_close` (valued at the last price it traded at, from the entry close). The unknown share of the cohort is part of every result.
- The interval between the scenarios is a **sensitivity interval, not a guaranteed bound**: eventual recovery or liquidation proceeds could exceed the last traded price, so neither scenario limits the true outcome and no
  aggregate result built on them is a mathematically bounded return. Neither scenario is "the" result, and a name is never dropped because its outcome is unknown.
- The valuation study holds no last traded closes, so it reports only the assumed total-loss scenario; the cohort walk-forward reports both.

## What a result may call itself

- A **signal study** (rank correlation, quintile spreads) is an *association* on the cohort. It is not a portfolio return.
- **Portfolio returns** are `not_computed`: they need an entry rule (the next session's open), costs and a liquidity model. The two are reported apart and never mixed.
- Neither says anything about whether a model or an analyst adds value, and neither is a claim of profitability.

## Not covered here

Real-store access, migration, swapping database files and `--all-versions` fetching are unauthorised. This policy is about synthetic fixtures and non-production copies only.
