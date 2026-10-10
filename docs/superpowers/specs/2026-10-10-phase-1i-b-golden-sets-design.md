# Phase 1i-b: Golden sets for stocks and ETFs — design

Decided Oct 10, 2026 with the owner: frozen snapshots, labels drafted by Claude and reviewed by the owner, quant/technical and valuation first.

## Why

The risk-overlay work found, by accident, that Infosys (statements in USD, price in INR) got a P/E of 1279 and a confident "bearish 92" from valuation. A hand-labelled set run on every change would have caught it. PRD FR-15: 20–30 instruments per asset class, each with an expected signal *range*, not an exact verdict.

## Two layers

All four specialists are model-driven (a persona over a metrics packet), so nothing is scored offline on a signal. The set therefore has two layers, both reading the same frozen data:

1. **Evidence layer (offline, deterministic, runs in CI).** Rebuild the technical and fundamentals packets from the frozen snapshot and check what a human labeller can assert about them: a figure inside a sane range (`pe_trailing` between 5 and 80), a figure that must be *missing* (a USD reporter has no `pe_trailing`), a coverage level. This is the layer that catches data and arithmetic bugs.
2. **Signal layer (live, needs the NVIDIA NIM key, marked `live`).** Feed the same frozen packets to the specialists and check each signal against the labelled range (e.g. quant/technical on a long downtrend: `bearish` or `neutral`). It scores the persona, not the data, and a free-tier model may fail validation, so a case that returns no output is reported as *no answer*, never as a pass.

## Data

`tests/golden/snapshots/<SYMBOL>.json`: `symbol`, `name`, `asset_class`, `captured_on`, `bars` (daily rows `[date, open, high, low, close, volume]`, about 25 months), and for stocks the stored fundamentals `payload` and its `as_of`. `tests/golden/index_valuation.json` holds the shared NIFTY 50 valuation history. A snapshot is rebuilt into packets with `now = captured_on`, so a label never drifts with the market. Re-capturing is a deliberate act (`python -m athena.evaluation.capture SYMBOL...`) and shows up in the diff.

## Labels

`tests/golden/cases.json`, one entry per instrument: `symbol`, `why` (one line), `figures` (`{metric: [min, max]}`), `missing` (metrics that must be absent), `signals` (`{specialist: [allowed signals]}`). Chosen from clear-cut cases: a sustained downtrend, a sustained uptrend, a high-multiple stock, a steady compounder, a bank, a USD reporter, a loss-maker, and large index ETFs. 20–30 stocks and 20–30 ETFs is the target; the first cut is smaller and grows by review.

## Code

`athena.evaluation.golden`: `GoldenCase`, `Snapshot`, `load_cases`, `load_snapshot`, `build_packets(snapshot)`, `check_evidence(case, packets)`, `check_signals(case, outputs)`, and a `GoldenReport` (pass / fail / no-answer per component), in the style of `resolver_eval`. `athena.evaluation.capture` writes snapshots from the live sources.

## Not in this slice

Moat-quality and earnings-intelligence signal ranges (live layer, added as labels are reviewed); ETF specialists beyond quant/technical (there are none yet); blend calibration (1i-c); bonds and funds (Phase 4).
