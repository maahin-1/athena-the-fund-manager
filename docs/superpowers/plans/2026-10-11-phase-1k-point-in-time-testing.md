# Phase 1k: Point-in-Time Testing of the Analysts — Plan

**Goal:** Score the four price-and-statement analysts against what actually happened next, on past dates, without leaking the future into what they read.

**Architecture:** `athena.pit` rebuilds company statements as they were public on a date (NSE results filings to March 2025, Yahoo filtered by legal filing deadline after). `athena.evaluation.pit_cases` builds the packets the analysts read for a stock on a date, optionally blinded. `outcomes` labels the 63, 126 and 252-trading-day result against the NIFTY 50. `pit_run` runs the analysts (resumable, repeatable) and `pit_score` reports accuracy against the best fixed answer.

## Findings that set the design (Oct 11, 2026)

- NSE's results list has a filing date and an XBRL file per filing from Oct 2018, but is complete only to about March 2025; after that a handful of late filers per month appear (106 filings in Mar 2025, 9 in Apr, 8 in May).
- The specialists' model (Nemotron 3 Super 120B) has pretraining data to June 2025 and post-training to Feb 2026 (NVIDIA's model card); a leak-free test needs as-of dates after about Feb 2026, which leaves 3-month outcomes for March to July 2026 only. Earlier dates are tested blinded, and the gap between blinded and unblinded scores measures the memory leak.
- NSE answers 503, then 403, to a burst of requests, for several minutes; jugaad prices fail with it and the price chain then falls back to Yahoo, whose prices are already split-adjusted. The case builder refuses to build per-share ratios on such prices.
- Yahoo's total revenue for banks is net of interest expense; NSE's `Income` is gross, so the two differ for lenders (Kotak FY24: 94,274 cr against 69,670 cr); net income and EPS agree (EPS differs by exactly a later split).

## Done

1. XBRL parser, results index with throttling and backoff, disk cache, statements as of a date.
2. Yahoo statements filtered by the legal filing deadline.
3. Outcome labeller, case builder with blinding, runner, scorer.
4. Smoke run with the real model (TCS, three dates): the technical analyst answered neutral each time at confidence 35 to 45.

## Not done

- A real run (about 50 stocks, monthly dates): waits for NSE to stop refusing requests.
- News is excluded (no record of what was known on a day).
- Blend calibration from the results.
