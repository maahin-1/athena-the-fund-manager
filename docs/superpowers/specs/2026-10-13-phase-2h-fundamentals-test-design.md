# Phase 2h: testing the fundamentals analysts at scale

Parent: [2e](2026-10-12-phase-2e-technical-test-design.md) (method) and Phase 1k (the first point-in-time test: 6 large caps, 70 to 89 cases per analyst, all three fundamentals analysts below the best fixed answer).

## Question

Do the valuation, moat and quality, and earnings analysts, reading a point-in-time fundamentals packet, call what a stock does against the NIFTY 50 over the next 63, 126 and 252 trading days better than always giving the most common outcome?

## Method

- **Stocks.** The 40 stocks pinned for 2i (`.athena/kronos/stocks.json`; drawn by the 2e rule, so a mix of the most traded and a seeded draw from ranks 101 to 600). Pinned so the run is repeatable.
- **Packets.** Built by the existing `PitCaseBuilder` from what was known at each date: for dates up to 2025-03-31, the exchange's own dated results filings (its list is complete to about then); for the clean window 2026-03-31 to 2026-07-31, the statements stored in the warehouse (Yahoo's, as fetched in October 2026, cut to the periods that had been reported by the date). Prices are the warehouse's raw bars (as printed, which is the share count the filings report against), adjusted bars for returns and outcomes, and the NIFTY 50 P/E and P/B history from the exchange's index files. The gap from 2025-04-30 to 2026-02-28 is not tested: the filings list is incomplete there and the stored statements would reach too far forward.
- **Dates.** Month ends 2024-01-31 to 2025-03-31 (15) and 2026-03-31 to 2026-07-31 (5). Outcomes known for the later dates are 63 days for March to June 2026 only.
- **Blind.** Symbol removed, dates moved back 28 years, as in 2e.
- **Analysts.** valuation, moat_quality, earnings_intelligence, each run once per case (deterministic, Phase 1k). Model: the router's specialist tier, as the live app uses.
- **Scoring.** The Phase 1k scorer: accuracy against the best fixed answer, directional accuracy, accuracy at confidence 70 and over. One log, `.athena/pit_runs/wh_fund.jsonl`, resumable.

## Rules

1. A case is built only from filings published and statements for periods ended by its date; this is the harness's existing point-in-time rule, kept unchanged.
2. The report states the number of cases per analyst, horizon and source (exchange filings or stored statements) so the two windows are not blended without notice.
3. The calibration file is rebuilt only after the report is read, from this log with the earlier ones, by the existing command.
4. A result is described as measured on these stocks and dates, not as a property of the analyst.

## Not in 2h

Changing any analyst's prompt or packet (that follows from the result), banks' book value before the FY24 filings (withheld on purpose), and the news analyst.

## Result (run finished 2026-10-13)

40 pinned stocks, 19 month-ends (2024-01-31 to 2026-06-30), 2,000 analyst runs each. Errors: 54 to 84 runs per analyst (3 to 4 percent). Accuracy against the best fixed answer ("always bullish"), measured on these stocks and dates in a rising market:

| Analyst | 63 days | 126 days | 252 days |
|---|---|---|---|
| valuation | 32% vs 41% | 27% vs 43% | 30% vs 37% |
| moat_quality | 34% vs 41% | 32% vs 43% | 38% vs 37% |
| earnings_intelligence | 39% vs 40% | 34% vs 40% | 32% vs 37% |

None beat the best fixed answer by the 5-point margin at every horizon, so verdicts stay uncalibrated. Valuation says bearish about 70 percent of the time while 63 to 74 percent of stocks did not underperform; that looks like a systematic bias and is the first thing to examine. Calibration was rebuilt from the v2 unblinded, technical and fundamentals logs; trust is now valuation 0.13, moat_quality 0.29, earnings_intelligence 0.31, quant_technical 0.43 (before: 0.11, 0.14, 0.10, 0.43). The earlier file is kept at `.athena/calibration.before_2h.json`.
