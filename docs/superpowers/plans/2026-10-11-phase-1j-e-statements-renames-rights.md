# Phase 1j-e: Statement Checks, Renames, Mergers, Rights, NSE Stability — Plan

> **For agentic workers:** execute task by task; steps use checkbox syntax.

**Goal:** The statements are cross-checked before a ratio is built on them, a retired ticker resolves by the exchange's own record, mergers are found from the exchange's filings, a rights issue is adjusted only if the evidence supports it, and the stability of the NSE sources over days is measured.

**Architecture:** Every check was designed from real data first (40 stocks' statements, 37 rights rows, the exchange's rename file, merger filings of HDFC Bank, Tata Steel, ICICI Bank). A check ships only if it separates real failures from normal cases in that data. Nothing is guessed: a ratio that fails a cross-check is withheld with the numbers in its reason; a merger is reported only from a filing that says it took effect.

## Global Constraints

- No assumptions: each threshold is stated with the data it was set from.
- Fail open on sources: an unreachable source is a flag or note, never an error.
- Prices change only against a record (Phase 1j-a/b rule).

## Findings that shaped the design (Oct 10-11, 2026)

- Yahoo does not always restate EPS after a split or bonus (HDFC Bank's FY23 EPS is on the pre-bonus count; its Q1 FY26 average shares are not restated while its EPS is). EPS over net income per current share ran 0.94-1.12 on 40 stocks (buybacks, rights issues), so 0.75-1.33 passes all real cases and stops a 2x or larger mismatch.
- Our last close and Yahoo's current price agreed within a few percent on all 40, so beyond 0.8-1.25 one of them is on another basis.
- HDFC Bank's quarterly statements were stale (latest quarter 2025-06-30) on Oct 10, 2026, found by the new staleness flag.
- NSE's corporate-action list has no merger rows (HDFC Ltd into HDFC Bank, 2023, is absent); its announcements do ("Amalgamation/Merger", "Effective Date of the Scheme...", "allotment ... pursuant to Scheme of Arrangement/Amalgamation").
- NSE publishes a rename list (`symbolchange.csv`, about 1,060 rows). Prices come back whole under either symbol, but the corporate-action and statement lookups need the current one, and name matching resolves a retired ticker wrongly (ZOMATO to Omax Autos, TATAMOTORS to TMCV).
- NSE's rights rows are uniform ("Rights a:b @ Premium Rs X") with a face value in the row, so the issue price is known; whether the shares are partly paid is not.

## Tasks

1. **NSE probe** (`athena.evaluation.nse_probe`): one question per NSE source, logged; `--report` per day. Scheduling is the owner's decision.
2. **Symbol renames** (`athena.symbol_changes`, resolver, orchestrator note, backtest CLI).
3. **Statement checks** in the fundamentals packet: EPS basis, price agreement, staleness.
4. **Merger and scheme filings** (`athena.news.schemes`) as a data-quality flag through `LiveFundamentals`.
5. **Rights adjustment**: decided by the evidence run (formula against observed ex-date gaps).
6. TRD, full suite, live checks, graph, commit.
