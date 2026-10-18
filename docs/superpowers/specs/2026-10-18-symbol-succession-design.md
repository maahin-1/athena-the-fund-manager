# Symbol succession: one record, every ticker (2026-10-18)

## Why

A company that changes its exchange symbol (rename, merger, a listing that continues under a new code) has its prices and statements stored under the symbol of the day. A chart of the new symbol began on the rename day (SHRIRAMFIN on 2022-12-20 although the company trades back to 2018), and the fix of 2026-10-18 joined three of them from a hand-written list. That does not scale and it is the wrong shape for a product people use on any ticker. The same hand-kept shortcut turned up in three more places:

| Where | What was hand-kept |
|---|---|
| `terminal/src/bars.rs` | `PREDECESSORS`, 3 pairs (chart bars) |
| `terminal/src/search.rs` | `ALIASES`, 5 pairs (search by old symbol) |
| `src/athena/fundamentals/history.py` | `PREDECESSORS`, 5 entries with prose (statements, shareholding, documents) |
| Python readers (`Warehouse.bars`, `adjusted_history`, so backtest, packets, technicals) | not joined at all |

## Decision

One record of successions in the warehouse, built from the exchange's own published rename list (the file `athena.symbol_changes` already reads for the resolver), checked against our own bars, and read by every consumer. No ticker is named in code.

**Table `symbol_renames`** (warehouse.duckdb), one row per exchange rename: `old, new, change_day, company, last_old_day, first_new_day, gap_days, step, joinable, retired, event, reason, checked_at`.

- `step` = first new close / last old close - 1, on the prices as printed.
- `joinable` (prices): both symbols have bars, the old bars end before the new ones begin, `0 <= gap_days <= 14`, and `|step| <= 25%`. Measured on the real warehouse on 2026-10-18: 332 renames have bars under both names, 321 pass, 11 do not (for example INDOSOLAR to WAAREEINDO, three years apart and +5152%), and those are left unjoined with the reason kept.
- `retired` (statements, search): the old symbol has no bar after `change_day` + 7 days, so a reused symbol is never taken for a predecessor.
- `event`: wording of a demerger, arrangement, amalgamation or merger action of either symbol within 45 days of the rename, so a page can say that earlier figures may include business now outside the company. Never used to decide a join.
- Chains (A to B to C) are followed, at most 5 hops, never through a loop.

**Prices.** Python: `adjusted_history` reads the joined raw bars (old symbols' bars, then the symbol's own, as printed, which already join because the step is small) and the exchange's actions of the symbol and its predecessors (de-duplicated: the exchange lists past actions under the current symbol). The adjustment then applies each split at its own ex-date, so the old bars are put on the same footing without any scale factor. Rust: the raw path (`adj=0`, or adjusted unavailable) joins from the same table; the adjusted path is Python's. The response says which symbols were joined.

**Search.** Aliases are the `retired` old symbols of each listed symbol, from the table.

**Statements.** `fundamentals.history.PREDECESSORS` becomes a read-through view of the table (`retired` rows), with a generic note built from the record.

**Refresh.** A `renames` step in the nightly build after prices and actions, and `python -m athena.warehouse.renames` by hand. A failed fetch keeps the last table (the step reports it); nothing is scheduled.

## Not in scope

- The Tata Motors demerger's own price step (exchange series continuity is joined as the exchange prints it; the price-break flags report unexplained breaks as before). The parked Reliance/Jio pass stays parked.
- Intraday history (the free source holds only recent days).
- Companies that changed from one *symbol* to another with an ISIN change and no entry in the exchange's list.

## Evidence required

Unit tests for the rules and the joined adjustment (a 1:5 split after the rename), a test that no ticker name appears in the production code of the join, and a run over every symbol in the universe that reports how many join, how many do not and why, and that no joined series has a day-over-day move beyond 40% that the unjoined one did not.
