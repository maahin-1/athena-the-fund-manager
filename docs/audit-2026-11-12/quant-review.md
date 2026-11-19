# Quantitative research, data and backtesting review

Reviewed 12 November 2026 against checkout `37f1fc1`. Source review, existing local result files, and read-only database queries; no network, LLM calls, broker actions, or environment files were used. Source was not changed. This is a review of research validity, not a claim about expected investment returns.

## Judgment

The deterministic research infrastructure is substantial and suitable for a carefully scoped demonstration. It can help a human inspect filings, compare assumptions and test simple price rules. The evidence does **not** establish that its LLM analysts, blend, council or valuation model deliver an investable edge. Several timing and cohort issues prevent interpreting the current evaluation as a faithful simulation of a deployable trading process.

The strongest distinction is between **a correct calculation**, **a defensible research study**, and **an executable investment process**. Athena has many checks for the first, useful exploratory work toward the second, and limited evidence for the third. Adding more personas would not resolve the gaps below.

## Verified findings, in priority order

### Q1 — High: stored-verdict settlement can award returns earned before the verdict existed

**Evidence:** `src/athena/verdicts/decisions.py:104` calls `settle_one(v["data_as_of"], ...)`, while `src/athena/verdicts/runner.py:146` obtains the actual current clock and `:159` separately stores it as the verdict creation time. `decisions.py:54-76` uses the data date's close and subsequent stock bars. Creation time is carried into the output but does not constrain the return window.

**Reproduction:** an in-memory history with `made_at=2026-01-07`, `data_as_of=2026-01-01`, and daily closes 100, 110, 120, ... yields a five-bar outcome of +50% ending January 6. The outcome precedes the verdict's creation. These synthetic dates deliberately simplify the timing; the defect does not depend on weekends.

**Current exposure:** the actual warehouse contains 15 verdicts created October 11–29, using data dates October 9–27. The maximum creation-date lag is three calendar days. COALINDIA's verdict was created October 12 at 18:35 UTC using October 9 data. It cannot have been traded at October 9's close. Even same-day after-close creation cannot assume a fill at that close.

**Implication:** this is a useful measure of a historical data-date association, but is not deployable realized performance. Subtracting transaction costs does not correct the entry-time mismatch. No exact aggregate inflation amount was estimated in this audit.

**Fix:** retain both data time and decision time; record evidence availability, quote time, model completion time, first eligible session and executable entry price. Settle a prospective decision at the next eligible open or another predeclared executable policy. Keep a separately named historical association statistic if desired. Reject or visibly classify stale-input decisions.

### Q2 — High: the Yahoo path is not strict point-in-time evidence, including a mistaken late-filer claim

**Evidence:** `src/athena/evaluation/pit_cases.py:24` sets an NSE-filings boundary of March 31, 2025; its `_payload` method uses `yahoo_statements_as_of` after that date. `src/athena/pit/yahoo_asof.py:25-34` retains a period when its legal deadline precedes the requested date. It has no actual publication timestamp. Its docstring says late filers lose a period, but the code cannot identify a late filer and does not make that exclusion.

**Concrete example:** a March 31 annual period, actually first filed June 20, is retained for June 1 once the assumed 60-day deadline has elapsed. The same function also uses today's restated numbers for an earlier date. A quality flag admits the restatement limitation, which is good disclosure, but the flag does not make the historical evidence valid.

**Current exposure:** `.athena/pit_runs/wh_fund.jsonl` holds 6,000 horizon rows: 5,256 labeled NSE results filings, 600 labeled Yahoo filtered by legal filing deadline, 144 none. These are rows, not 6,000 independent cases. Its dates span January 2024–June 2026.

**Fix:** divide results into strict archival filings and approximate deadline-filtered studies, never pool them into one unqualified PIT score. Use actual first-publication times and immutable filing payloads. If archival evidence is absent, abstain for strict tests. Add an explicit late-filer synthetic case. Update the inaccurate explanatory comment.

### Q3 — High for historical validation: latest-value storage destroys the original financial history

**Evidence:** `src/athena/fundamentals/store.py:150-154` deletes an older same-key statement line when a newer filing arrives. `src/athena/valuation/calibrate.py:126-134` later filters this latest-value table by `filed <= cutoff`. Filtering a table that no longer holds original versions cannot reconstruct the past.

**Reproduction using the actual public methods:** save FY2024 sales 100 filed May 1, 2024; save a revision 120 filed May 1, 2025; request June 1, 2024 via `AsOfFundamentals.lines`. Result: `{}`. The only stored row is 120 with the 2025 filing timestamp. This loses evidence rather than silently reading the revised value, but it changes which historical companies and periods qualify.

**Scope:** the separate `pit.statements.statements_as_of` path is better: it filters filing references by a 15:30 IST cutoff, chooses the latest then-public revision, and loads that filing. Do not attribute the latest-table defect to that separate path. The valuation calibration and forensic statement paths using the latest-line table need their own assessment. The calibration module already documents dropped restated periods; documentation does not remove the selection effect.

**Fix:** store immutable versions keyed by company, basis, statement, period, line and filing identity, with publication and ingestion timestamps. Build a latest-value view for the current UI and an as-of view for research. Preserve the raw document hash, parser version and mapping version. This is more valuable than changing DuckDB to a larger database: the missing feature is the data model, not the database brand.

### Q4 — Medium/high methodology limitation: valuation calibration is a conditional exploratory study, not a full historical replay

**Evidence:** `src/athena/valuation/calibrate.py:136-142` reads today's company share count and divides by the split/bonus factor; `:225` uses actions after the historical date to determine eligibility; `:231` uses end-of-day rather than a market-close cutoff; `:249` supplies today's industry context; `:296-297` supplies today's rates and assumptions to past-date valuations. The module itself explicitly documents several of these limits.

**Mechanism:** reversing splits cannot reconstruct shares changed by placements, buybacks, warrants or mergers. Today's shares affect per-share model value and historical market-cap filters. Using future demergers to exclude names conditions a cohort on information unavailable at selection time. Today's industry margins and cost of capital are not necessarily harmless to rankings: firms have different leverage, betas and terminal-value exposures, so a common rate change affects them differently.

**Interpretation:** the study is still useful for checking sensitivity and asking why prices exceed a particular model's assumed value. It cannot show that an analyst using the model at the time could reproduce the reported rankings and returns. The code and calibration document are candid about many of these limits; the README's universal PIT statement is broader than the implementation supports.

**Fix:** use dated share-count filings, dated industry tables and dated reference-rate snapshots. Use actual publication cutoff times. Define treatment of restructuring events prospectively, including receivable cash and successor securities, rather than removing future difficult cases. Until then label these studies “retrospective sensitivity with approximate historical inputs.”

### Q5 — Medium: the factor walk-forward output conditions on end-window survival and does not simulate execution

**Evidence:** `src/athena/evaluation/walkforward.py:98` discards a company without a positive end-window close. `:159` starts from `warehouse.universe("stock")`, not an explicit dated membership table. `:58`, `:65` and `:75` apply the minimum-price filter to fully adjusted prices; future splits can change a past-date absolute threshold. `:163` applies one delivery-cost estimate for ₹100,000 and ₹100 crore daily traded value to every period. This is a cross-sectional signal study, not an order/fill engine.

**Mechanism:** a suspended or delisted stock can disappear from the measurable-return cohort at the very time it hurts an actual portfolio. Loading old bars and stopped-trading company statements is useful progress, but missing end prices still removes terminal failures. A ₹10 price floor is also a weak liquidity proxy; volume and order size matter. The report's top-fifth excess is relative to the cross-sectional median, not an investable benchmark portfolio.

**Current artifact:** `.athena/reports/walkforward.json` reports momentum mean IC +0.0601 over 184 dates, naive t=6.14, mean top-fifth excess +2.517% per window, and +1.979% after its generic costs. Reversal and low-volatility also have positive average rank scores. These are worthwhile leads, **not** annualized returns, verified live performance, or evidence that the LLM system earns these results.

**Fix:** freeze dated universe construction; retain all selected names through suspension and terminal events; explicitly model unrecoverable/delisting proceeds and delayed liquidation. Use raw nominal price for historical price eligibility and pre-entry traded value for liquidity. Compute holdings, turnover, gross/net exposures, cash and realizable fills. Add name-specific costs and capacity sensitivity. Report both factor association and portfolio P&L with their distinct meanings.

### Q6 — Medium, reproduced correctness bug: tied repeat votes can crash PIT scoring

**Evidence:** `src/athena/evaluation/pit_score.py:80-82` resolves a modal tie to neutral, then averages confidence only from runs that gave the chosen signal. Two runs `[bullish, bearish]` contain no neutral confidence, so the divisor is zero. Calling `score` on two such `Call` instances produced `ZeroDivisionError: division by zero` in this audit. The newer `evaluation.scorecard.cases` guards this absent-neutral case; the older PIT report path does not.

**Impact:** a valid outcome of stochastic repeat evaluation can prevent reporting the entire study. This is especially relevant with two repeats or four repeats split evenly between bull and bear.

**Fix:** define confidence for a synthetic tie explicitly (for example zero) and test it. Share vote aggregation between the two scoring implementations so they cannot diverge.

### Q7 — Medium usability limitation: daily-bar execution is intentionally simple and trade statistics omit the terminal position

**Evidence:** `src/athena/backtest/engine.py:71` describes a long-only single-position model. Signals become next-open orders; gap stops use the open, which is a good conservative feature. A same-bar stop takes precedence over a target. End-of-run equity includes an exit charge, but the terminal position is not appended to `trades` (`engine.py:157-163`; `tests/test_backtest_engine.py:64-69` explicitly expects no trade). `backtest.summary.summarize` computes win rate and average trade from that closed-trade list.

**Implication:** equity and reported closed-trade statistics describe different accounting scopes. A buy-and-hold-like strategy can show a material return and zero trades/win rate. The daily engine cannot establish that a stop was executable during a circuit limit, in a thin stock or at the requested quantity. Fractional shares and flat per-side basis-point costs simplify the simulation. This is acceptable for a disclosed research harness, insufficient for a production execution claim.

**Fix:** identify terminal mark-to-market positions explicitly or record a forced-close reason consistently in the ledger. Display ambiguous same-bar stop/target assumptions and offer conservative/sensitivity policies. Add tick/lot sizing, order rejection, liquidity/participation and price-limit handling before using it to select a live strategy.

## Existing evidence: what has actually been achieved

Read-only local database observations during this audit:

| Item | Observed value | Meaning and limit |
|---|---:|---|
| Market bars | 7,719,549 | Raw history, January 4, 2010–November 6, 2026 |
| Distinct symbols with bars | 5,097 | Includes historical names; not 5,097 current investable companies |
| Current universe | 2,632 stock; 463 SME; 359 ETF | 3,454 rows; dated historical membership is a separate concern |
| Fundamentals companies | 2,613 | Stored company metadata, not verified completeness for every period |
| Statement lines | 1,643,641 | Latest line versions; filing timestamps June 2017–November 2026 |
| Filings rows | 77,671 | Catalog entries, not a guarantee every filing parsed successfully |
| Stored verdicts | 15 | October 11–29 creation dates; a small prospective record |
| Stored packets | 5,208 | Data dates October 14–November 6; not all packets equally fresh |

The latest stored nightly-build report is November 7 and says prices reach November 6. Since this audit is November 12, freshness should be refreshed and checked before a “current market” demonstration. This is an observed local snapshot, not evidence that the supervisor is presently broken.

The existing `.athena/reports/scorecard.json` does not establish LLM predictive skill. For 63-day outcomes, earnings has 612 cases across 24 dates with mean IC +0.0304, t=0.71; moat 740/26, IC -0.0068, t=-0.12; technical 1,269/37, IC +0.0081, t=0.22; valuation 669/26, IC -0.0235, t=-0.47. Case counts are not independent stock/date observations across overlapping horizons or repeated runs. The 252-day technical and valuation rows have naive t-statistics above 2 but negative bullish-minus-bearish spreads. The artifact's prose is older than the current `_verdict` implementation, which now describes that disagreement explicitly. Preserve artifacts with the producing code version rather than assuming current code generated an older file.

At least eight local PIT logs exist, including archival NSE and blinded/unblinded experiments. This is stronger evidence discipline than a single cherry-picked success story. Still, a clean fresh prospective evaluation is needed after timing/data fixes; reusing the observed cohort to choose prompts or thresholds consumes its holdout status.

## How the quantitative architecture works, and why the choices matter

1. Exchange/Yahoo loaders normalize bars, actions and statements into DuckDB. The warehouse is the common numerical substrate for UI packets and research. DuckDB suits a single-user analytical desk because batch scans and joins are efficient without a service to operate. It does not by itself guarantee PIT correctness: versions, timestamps and cohort rules do.
2. Corporate-action reconciliation adjusts split/bonus price history and flags unexplained breaks. Rename joining avoids treating a company's old and new ticker as unrelated data. This prevents mechanically false crashes and broken indicators. These are real strengths.
3. Technicals and accounting ratios are calculated in deterministic Python/TA-Lib, then passed as packets to LLM specialists. Pure formula tests and workbook golden comparisons reduce arithmetic errors. A matching workbook validates implementation of the model, not the economic truth of assumptions.
4. Valuation uses a ten-year FCFF model for operating firms and excess-return treatment for lenders, with low/base/high scenarios, reinvestment, distress, dilution and business-mix handling. The main uncertainty is future growth, margins, reinvestment and cost of capital, not whether the LLM can multiply. Keeping those calculations out of prose generation is sound.
5. Historical PIT analysts are evaluated on later excess returns, while the price-rule engine simulates next-open entries. These are different evaluation layers. A rating classifier can rank outcomes weakly without producing a tradable portfolio; a good research summary may also be useful without predicting return.
6. Forensic statement studies deliberately distinguish elevated accounting measures from proof of wrongdoing and add size, volatility and data-coverage controls. The continuous regression documents overlapping windows and outlier sensitivity. This is good scientific restraint. Future validation should add industry/issuer clustered uncertainty and newly frozen cohorts; these controls are still associations, not causal or fraud ground truth.

## Implementation chronology supported by repository records

- October 10–11: corporate-action/price-integrity and PIT archive design addressed raw-price discontinuities and information availability (`TRD.md`, sections describing phases 1j/1k; dated specs/plans).
- October 12–13: warehouse, stored verdicts and technical baseline evaluation. The technical specialist and Kronos were not promoted as demonstrated alpha; the technical evidence did not justify tuning (`TRD.md:594-595`).
- October 22: share-count calibration uncovered and corrected major current-data errors. The documented change affected 1,451 of 2,334 counts; `docs/valuation-calibration-2026-10-22.md` explains overwrite order and filing scale errors. This is an example of evaluation improving data quality even when no trading edge emerges.
- October 24: typed decisions, settlement, analyst scorecard, cost overlay and shadow council. Commit `9788a41` applied costs to settlement and walk-forward output; shadow promotion was gated on evidence.
- October 25 onward: fuller valuation assumptions, distress/holdings and calibration commands; October 28 improved model resilience and honest evaluation; November 9 added continuous forensic regression and stopped-trading statement coverage. These are design rationales documented in source and TRD, not inferred personal motives of the author.

## Ordered practical improvement plan

1. Correct the decision/execution time contract and the tied-vote crash; split strict archival and approximate PIT scorecards. These are concrete correctness fixes, not a research redesign.
2. Introduce immutable financial/reference versions and dated shares. Keep latest-value UI views for speed. Pin data snapshot, input hashes, prompt/model identity, parser, code commit, cost policy and calendar version in each experiment manifest.
3. Create a canonical portfolio ledger with explicit missing/terminal outcomes. Reconcile equity, trade list, dividends, corporate actions and fills. Add small adversarial fixtures: restatement, post-close filing, late filer, rename, suspension, delisting, split, rights issue, merger and same-day stop/target.
4. Pre-register a fresh prospective study comparing no-LLM deterministic baseline, one evidence-grounded summarizer, specialist blend, and blend plus council, on identical inputs and dates. Track abstention, factual correctness, latency, spend and analyst task completion as well as net returns. Council value is its *incremental* gain after cost and latency, not a plausible debate transcript.
5. For predictive claims, use nonoverlapping or purged windows, date/issuer block bootstrap or autocorrelation-robust uncertainty, regime breakdown, turnover and liquidity/capacity stress. Freeze a holdout once; do not repeatedly consult it for prompt selection. Compare Nifty TRI or another declared total-return benchmark when stock dividends are included.
6. For analyst usefulness, commission a blinded task evaluation: can a reader retrieve the cited filing, verify the arithmetic, find a real accounting concern and complete the work faster? Measure unsupported claims and missed risks. This is a more feasible near-term success criterion than “beats the market.”

No live calls were made and no production data was modified. The parent review runs the broader test suite; passing tests do not alter the above research-validity findings.
