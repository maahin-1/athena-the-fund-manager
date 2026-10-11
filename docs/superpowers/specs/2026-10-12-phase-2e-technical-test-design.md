# Phase 2e: testing the technical analyst from the warehouse

Parent: [market warehouse design](2026-10-12-market-warehouse-design.md) ("keep improving verdict quality in parallel, by testing from the database") and [2d](2026-10-12-phase-2d-verdicts-design.md) (which ended on "only the technical analyst can be tested cleanly from stored prices").

## Question

Does the technical analyst (the model reading a technical packet) call what a stock does against the NIFTY 50 over the next 63, 126 and 252 trading days better than (a) always giving the most common outcome and (b) a plain trend rule read off the same packet?

## Method

- **Data.** The warehouse's stored raw bars, extended back to 2023-01-02 so that month-end dates from 2024-01-31 have 253 closes behind them (2023 has about 246 trading days) and the 252-day outcome is known for dates up to late 2025. Splits and bonuses are applied from the stored actions. The benchmark is the NIFTY 50 close from the exchange's index file. No statements: the fundamentals analysts are not tested here (the stored statements are as fetched today, so a past date would look ahead).
- **Point in time.** The packet for a date is built from bars up to that date only. The outcome uses the bars after it.
- **Cases.** Every month end from the first date with 253 closes to the last date with a known 63-day outcome.
- **Stocks, chosen by a rule and not by hand.** Ranked by traded value on the last loaded day. Of the 100 most traded, the first 20 with a full history and no price break nothing explains; of ranks 101 to 600, 20 drawn with a fixed seed (11) under the same conditions. Current listings only, so stocks delisted in the period are absent (survivorship); the outcome is against the index, which limits but does not remove this.
- **Blind.** The symbol is removed and the dates moved back 28 years in the packets, because the model may remember how a famous stock did. Magnitudes are not hidden.
- **Analysts in the log.** `quant_technical` (the model) and `rule_trend` (aligned up is bullish, aligned down is bearish, anything else neutral; no model call). Same cases, same outcomes, one log: `.athena/pit_runs/wh_tech.jsonl`, resumable.
- **Scoring** is the existing Phase 1k scorer: accuracy, accuracy of the best fixed answer, directional accuracy, accuracy at confidence 70 and over, run-to-run agreement.

## Rules

1. The model is run once per case (it is deterministic; Phase 1k).
2. The report prints both analysts at each horizon beside the best fixed answer, and says how many cases each has.
3. The calibration file is rebuilt from this log together with the Phase 1k log by the existing `--calibrate` command; it is not changed automatically.
4. A result is described as measured on these stocks and dates, not as a property of the analyst.

## Not in 2e

- Fundamentals analysts, the news analyst and the blended verdict.
- Changing the technical analyst's prompt or packet. That follows from what the report shows.
