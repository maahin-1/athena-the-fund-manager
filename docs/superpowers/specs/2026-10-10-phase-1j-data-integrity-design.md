# Phase 1j: Data integrity — corporate events, statement checks, price checks — design

Decided Oct 10, 2026 with the owner. Replaces the earlier idea of comparing four approaches one by one: they are layers of one pipeline, built in order, each acting only on evidence.

## Problem (seen in real data on Oct 10, 2026)

Prices arrive raw from the exchange. A split, bonus or demerger moves the price without moving value, and the technical panel, risk panel, price chart and technical specialist read it as a crash (Kotak 5:1 split: volatility 83%, max drawdown −84%). Two earlier layers of the dashboard already adjust, but by guessing from the size of the overnight move (`backtest/adjust.py`), which would also adjust away a genuine crash. Statements have their own faults (Infosys and HCL Tech report in USD, fixed Oct 10; HDFC Bank's equity jumps across its merger).

## Rule

Nothing is adjusted, recomputed or filled in from a guess. A correction needs a recorded event whose size agrees with the price move. A large move with no matching record is left as it is, flagged on the dashboard, and the figures that span it are marked unreliable. A genuine 50% crash and a 1:1 bonus look alike in prices; only the record tells them apart.

## Layers (built in this order; each is optional and fails open)

1. **Event-verified price adjustment.** Events from Yahoo (`Ticker.splits`: splits and bonuses, with ratios). Raw NSE prices stay the record. For each event, find the overnight gap within one trading day of the ex-date; if the observed gap is within 0.75x–1.33x of the one the ratio predicts, rescale earlier bars by the recorded ratio (prices times 1/ratio, volume times ratio). No matching gap: the event is listed as unseen (prices may already be adjusted). The comparison of NSE and Yahoo adjusted prices considered earlier adds nothing here: the price gap itself is the cross-check, and Yahoo's adjusted close mixes in dividends.
2. **Alarm for what is unexplained.** Any overnight move outside 0.6x–1.7x (NSE circuit bands make a genuine one-session move that large very rare) with no matching event is returned as unexplained, never adjusted.
3. **Exchange event list** (demergers, rights, buybacks, symbol changes, mergers). Only after a source check on the benchmark events shows a free NSE/BSE source that returns ratios reliably; unofficial endpoints break (TRD 6).
4. **News confirmation.** A free news source (GDELT, RSS) raises or lowers confidence in an event and finds ones the lists miss. It never supplies a ratio. Built with the news specialist.
5. **Statement checks.** Reporting currency (done), EPS x shares against net income (catches an EPS not restated for a split, and unit errors), growth and averages across a merger date marked not comparable, standalone versus consolidated where the source says which.
6. **Price-data checks.** History keyed by ISIN across a rename (Zomato to Eternal), stale and zero-volume days, two sources disagreeing, liquid ETFs treated as total-return, too little history after a listing or demerger.
7. **Share classes.** Group holdings by issuer (ISIN issuer code) for the concentration check; DVR shares carry a liquidity note; preference dividends and capital come out of earnings and equity in P/E, P/B and ROE. Preference shares belong to Phase 4.

## Where it plugs in (from the code graph)

One seam: `history_fetcher(chain)` feeds `RequestCache`, which feeds the technical packet, the risk panel, the charts, the fundamentals price and the dashboard service. A `CheckedHistory` wrapper at that seam corrects every consumer at once. The backtest's `long_history` is a second fetcher and is wrapped the same way. Valuation uses the latest close, which adjustment never changes.

## Acceptance

A benchmark of real events with known answers (`tests/golden/price_events.json`, offline from frozen snapshots): Kotak (split), HDFC Bank and Reliance (1:1 bonus), Vedanta (demerger, must stay flagged), and the Kotak series with its event removed (must stay unadjusted and flagged: the genuine-crash control). A wrong adjustment scores zero. The golden labels that were drawn from unadjusted data (HDFC Bank, Reliance) are re-checked.

## Open decisions

- The guess-based `adjust_for_splits` in the backtest and the risk check: after layer 1 it only acts on unexplained breaks, which is exactly the guess the rule forbids. Retire it once the benchmark passes; until then it stays so nothing regresses.
- Which exchange source (layer 3) and news source (layer 4): decided by the source check, not before.

## Out of scope here

Sentiment scoring and the news specialist itself; X/Twitter (paid or against its terms); bonds and funds (Phase 4).
