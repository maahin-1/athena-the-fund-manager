# Phase 1j-b: Exchange corporate actions (demergers, rights, buybacks) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use NSE's corporate-actions record to cross-check split and bonus ratios, to adjust a demerger that Yahoo does not record, and to note rights issues and buybacks, without ever adjusting from a guess.

**Architecture:** `athena.price_integrity.nse_actions` fetches and parses NSE's website JSON (`/api/corporates-corporateActions`) into `CorporateAction` rows (ex-date, kind, parsed ratio where the text carries one). `merge.py` reconciles NSE split/bonus rows with Yahoo's events (agree: apply; disagree: flag, apply neither; NSE only: apply NSE's parsed ratio). `reconcile` gains an `actions` input and claims an otherwise-unexplained break near a recorded demerger, using the observed gap as the factor and saying so. `CheckedHistory` takes the new source and fails open. Spec: `docs/superpowers/specs/2026-10-10-phase-1j-data-integrity-design.md`; source check Oct 10, 2026 (NSE endpoint works 5/5, no ratio field, BSE blocked, Yahoo has no demergers).

**Tech Stack:** Python 3.13, requests (already installed), pytest. No new dependencies.

## Global Constraints

- Decided with the owner Oct 10, 2026: split and bonus ratios come from a record (Yahoo or NSE text), never from the price; NSE confirms the date. A disagreement is flagged and neither is applied.
- A demerger is adjusted from the observed gap only when NSE records a demerger within one trading day of a break outside 0.6x-1.7x; the note says the factor comes from the price gap, not a recorded ratio, and may include that day's market move. A demerger with no such break is noted, not adjusted.
- Rights issues, buybacks, special dividends, capital reductions, schemes of arrangement: a note only, never a price adjustment. Ordinary dividends are ignored entirely.
- Anything NSE says that the parser cannot read is kept as kind `other` with its text and noted; never guessed.
- NSE bonus text `Bonus A:B` means A new shares per B held: price ratio `(A+B)/B`. A face-value split `From Rs X To Rs Y` has ratio `X/Y`.
- The NSE endpoint is unofficial: one polite client (browser User-Agent, `Referer: https://www.nseindia.com/`, about 2 requests a second at most), a failure is a note and never fails a request, and no stealth or evasion tooling is used. BSE is not used (blocked).
- Events outside the held price history are ignored. Existing tests keep passing (961 passed, 73 skipped at the start).

## File Structure

- Create `src/athena/price_integrity/actions.py`: `CorporateAction`, `parse_subject`
- Create `src/athena/price_integrity/nse_actions.py`: `NseActions` (fetch, cache-friendly callable)
- Create `src/athena/price_integrity/merge.py`: `merge_events`
- Modify `src/athena/price_integrity/events.py`: `PriceEvent.kind`
- Modify `src/athena/price_integrity/reconcile.py`: `actions` input, demerger claim, `noted`, `conflicts`
- Modify `src/athena/price_integrity/history.py`, `src/athena/cli.py`, `src/athena/dashboard/service.py`: wiring
- Modify `src/athena/evaluation/golden.py`, `capture.py`: snapshots carry `actions`
- Create `tests/test_price_actions.py`, `tests/test_price_nse.py`, `tests/test_price_merge.py`; modify `tests/test_price_reconcile.py`, `tests/test_price_history.py`, `tests/test_price_benchmark.py`, `tests/golden/price_events.json`
- Modify `TRD.md`

## Tasks

### Task 1: Parse NSE's subject text
`CorporateAction(day: date, kind: str, ratio: float | None, text: str, premium: float | None = None, source: str = "nse")` and `parse_subject(day, subject, face_value) -> CorporateAction | None` (None for ordinary dividends and AGMs). Kinds: `bonus`, `split`, `demerger`, `rights`, `buyback`, `capital_reduction`, `scheme`, `other`.
Tests (from the real rows): `Bonus 1:1` and `Bonus 1: 1` give ratio 2.0; `Bonus 1:4` gives 1.25; `Face Value Split (Sub-Division) - From Rs 5/- Per Share To Re 1/- Per Share` gives 5.0; `Face Value Split From Rs.10/- To Rs.5/-` gives 2.0; `Rights 1:15 @ Premium Rs 1247` gives kind rights, ratio 1/15, premium 1247.0; `Demerger` gives kind demerger, ratio None; `Buy Back` and `Buyback` give buyback; `Dividend - Rs 6 Per Share`, `Interim Dividend - Re 1 Per Share`, `Annual General Meeting/Dividend Rs 0.80 Per Share` give None; `Special Dividend` and unreadable text give kind other with the text; `Bonus` with no ratio gives kind bonus, ratio None.

### Task 2: The NSE client
`NseActions(session=None, today=date.today)`, callable `(symbol) -> list[CorporateAction]`, raising `AthenaError("NSE corporate actions unavailable for X: ...")` on any HTTP, JSON or schema failure. Query `from_date` eight years back to `to_date` one year ahead (DD-MM-YYYY). Prime the session with one GET of the home page (status ignored), reuse the session, send browser headers. Rows with an unparseable `exDate` are skipped. Tests use a fake session: parses the rows saved in the source check (Kotak split, Vedanta demerger, Wipro buyback, Reliance rights), an empty list, a non-JSON body, a non-200, and asserts the request carries the Referer and date range.

### Task 3: Merge Yahoo's events with NSE's split and bonus rows
`merge_events(yahoo: Sequence[PriceEvent], actions: Sequence[CorporateAction]) -> Merged(events, conflicts, notes)`. Same date (within one trading day, matched on calendar days <= 3 apart to span a weekend) and ratios within 2%: keep Yahoo's event. Ratios differ: conflict, neither applied. NSE only with a parsed ratio: a `PriceEvent(source="nse")`. Yahoo only: kept. NSE split or bonus with no parsed ratio and no Yahoo match: a note. Tests cover each branch.

### Task 4: Demergers and notes in `reconcile`
`reconcile(bars, events, actions=())`. After the event claims, each `demerger` action inside the history claims the unclaimed break outside 0.6x-1.7x within one trading day; its factor is the observed gap and it appears in `applied` as `PriceEvent(kind="demerger", ratio=1/gap, source="nse demerger, factor from the price gap")`. A demerger with no break: noted. `Reconciled` gains `noted: tuple[CorporateAction, ...]` (rights, buyback, capital reduction, scheme, other, unadjusted demergers) inside the history. `describe` gets a sentence per group, and the demerger sentence carries the price-gap caveat. Tests: Vedanta-like series with a demerger row adjusts and is no longer unexplained; the same series without the row stays unexplained; a demerger row with no break is noted; rights and buyback rows are noted and change no price; a row outside the history is ignored; two demergers compose with a split.

### Task 5: Wire it in and fail open
`CheckedHistory(fetch, events, actions=None)`: both sources run, `merge_events` feeds `reconcile`, and either failing is a note that does not stop the other. `live_sources` builds `NseActions`, caches it with `lru_cache`, and exposes `actions`; `live_service` wraps the long history the same way. Tests: both sources feed the result; NSE failing leaves Yahoo working and notes it; Yahoo failing leaves NSE's demerger working.

### Task 6: Benchmark on real snapshots
Snapshots carry `actions` (captured with the NSE client; `--backfill-events` also backfills them). `tests/golden/price_events.json` gains `noted` and kind expectations: VEDL moves from unexplained to applied (demerger, 2026-04-30); ADANIENT has a rights note and WIPRO a buyback note, neither adjusted; Kotak, HDFC Bank, Reliance, Nestle and Wipro still apply by Yahoo and agree with NSE (no conflict). Genuine-crash controls stay: with the record removed, VEDL goes back to unexplained and untouched.

### Task 7: Record it and check live
TRD 2.16 and the changelog get the NSE layer, its limits (unofficial, one-source, no ratio for demergers) and what is still open (mergers and symbol changes, rights adjustment, retiring `adjust_for_splits`). Run the full suite, then the live service on Vedanta, Reliance and Kotak and report the figures and notes.
