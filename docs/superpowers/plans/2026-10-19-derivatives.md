# Futures, options and MCX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The derivatives instrument list and API, Black-76 maths, an Options tab with chain, volatility curve and payoff builder, all against a synthetic feed; the live stream and continuous futures follow as marked steps.

**Architecture:** Rust `derivs.rs` builds a compact contract table from Angel's public list and serves it; the snap-quote decoder gains open interest. The page has pure `options/` modules (maths, chain statistics, payoff, synthetic feed) and an Options view that reads quotes through one `OptionFeed` interface. Continuous futures need a futures history fill and are a separate step.

**Tech Stack:** Rust (axum, serde_json), TypeScript, vitest, Playwright.

## Global Constraints

- Spec `docs/superpowers/specs/2026-10-19-derivatives-design.md`.
- Strike = the list's number / 100; time to expiry to 15:30 IST; Black-76; default rate 7%.
- Live derivative prices are not built (they need a market session to check); the page uses synthetic prices, labelled as such.
- No margin estimate, no order entry.

---

### Task A: derivative contract table and API (Rust) — done

**Files:** create `terminal/src/derivs.rs`; modify `terminal/src/main.rs`, `terminal/src/server.rs`, `terminal/src/angel/master.rs` (keep the full list, not only cash rows); fixtures `terminal/tests/derivs_rows.json` or inline.

- [ ] Tests first with real rows (an index future, an index option with strike in hundredths, a stock future, a stock option with a decimal strike, an MCX future, an MCX option, a BFO option, a test name, an expired contract): kind, strike, expiry, lot, underlying, flags; grouping into underlyings and expiries; chain by expiry with strikes sorted; futures by expiry; bounds and bad input; a refresh that returns nothing keeps the table.
- [ ] Implement `Contract`, `Derivs::from_rows`, `underlyings`, `expiries`, `chain`, `futures`; the routes; the daily refresh sharing the cash list's cache and fail-safe; `cargo test`; commit.

### Task B: open interest in the stream decoder

**Files:** modify `terminal/src/angel/decode.rs`, `terminal/src/angel/stream.rs`.

- [x] Tests first: a crafted 379-byte packet with open interest at offset 131 and its change in percent at 139; the cash packet still decodes; neighbouring fields undisturbed; the exchange type for each segment.
- [x] Implement `open_interest` and `oi_change_pct` on the tick and the segment-to-exchange-type table (NSE cash 1, NFO 2, BSE cash 3, BFO 4, MCX 5, NCDEX 7, currency 13).

### Task B2 (needs a market session): live derivative quotes

- [ ] Subscribe derivative tokens with the segment's exchange type from the contract table, carry open interest through the quote and the hub, add a route that returns quotes for a list of contract tokens (at most 100), an `OptionFeed` on the page that reads it, and compare the decoder's offsets with real packets; add the derivative stream and open interest to `scripts/terminal-market-check.py`. Not started: it cannot be checked without a session.

### Task C: Black-76, implied volatility and Greeks (pure)

**Files:** create `terminal/web/src/options/black76.ts`, `terminal/web/test/black76.test.ts`.

- [ ] Tests first: textbook prices, put-call parity, delta and gamma by finite difference, vega and theta signs, IV round trip over a grid of moneyness and expiries, refusals (below intrinsic, above the bound, no time left), time to expiry to 15:30 IST with a one-minute floor.
- [ ] Implement `price`, `greeks`, `impliedVol`, `yearsToExpiry`; commit.

### Task D: chain statistics and payoff (pure)

**Files:** create `terminal/web/src/options/chain.ts`, `terminal/web/src/options/payoff.ts`, tests.

- [ ] Tests first: forward from a future or from spot, ATM strike, PCR by open interest and volume, max pain, volatility curve (calls above the forward, puts below), ATM term structure; payoff at expiry and now for each leg kind, a straddle, a bull call spread, an iron condor, a covered future; break-evens (including none and more than two), maximum profit and loss with unlimited ends reported as unlimited; net Greeks.
- [ ] Implement; commit.

### Task E: synthetic option feed

**Files:** create `terminal/web/src/options/synthetic.ts`, `terminal/web/test/synthetic.test.ts`.

- [ ] Tests first: determinism for a contract and day; put-call parity of its prices; its implied volatilities reproduce the smile; open interest peaks near the money; every number finite and non-negative; bid below ask.
- [ ] Implement `OptionFeed` and the synthetic feed; commit.

### Task F: the Options tab

**Files:** create `terminal/web/src/options/view.ts`, `chainview.ts`, `curve.ts`, `payoffview.ts`; modify `main.ts`, `command.ts`, `style.css`; tests and `e2e/options.spec.ts`.

- [ ] E2E first: open under the fake feed, choose an expiry, see chain, ATM, PCR and the curve; click prices to add a call and a put (a straddle); read break-evens, max loss and net premium; "synthetic prices" label.
- [ ] Implement in slices: chain table, statistics line, curve charts, payoff builder, futures list; full suites; commit.

### Task G: docs and the live-check list

- [ ] TRD 2.46 and 2.47, master design section 28, roadmap row (5f: built, awaiting live check), add the derivative stream, open interest and Greeks comparison to `scripts/terminal-market-check.py`'s checklist, `graphify update .`, full suites, commit.

### Task H (separate, after the above): continuous futures

- [ ] Own spec and plan: the F&O bhavcopy fill into the warehouse, the roll rule and roll table, `NAME1!` and `NAME2!` symbols in the Chart Lab, back-adjusted option.
