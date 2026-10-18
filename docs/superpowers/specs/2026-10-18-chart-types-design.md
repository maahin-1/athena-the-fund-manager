# Chart types, the rest of the list (5b, 2026-10-18)

## Scope

Four types added to the Chart Lab's type menu: **High-low**, **Volume candles**, **Kagi**, **Point and figure**. They work on every symbol and every interval, because they read the same bars as the others; nothing names a symbol.

| Type | Kind | What it draws |
|---|---|---|
| High-low | time-based | a floating bar from each bar's low to its high, one colour (the trend colour of the close against the open is not used: the type shows range only) |
| Volume candles | time-based | the usual candle, its body (and wick) width in proportion to the bar's volume, against the largest volume in view, so a heavy day is wide and a quiet one thin |
| Kagi | price-move based | a line that turns only when the close reverses by the reversal amount; thick (yang) once it passes the previous swing high, thin (yin) once it passes the previous swing low, split at the price where it changes |
| Point and figure | price-move based | columns of X (rising) and O (falling) boxes of one box size; a column turns when price reverses by 3 boxes |

Time-based types keep volume, indicators, compare, drawings and the data window, as candles do. Price-move types (Kagi, point and figure) are "derived" like Renko, line break and range: no volume, indicators, compare or drawings, bars stamped in sequence, data window shows the line's or column's own numbers.

## Decisions

- **Sizes are automatic**, as for Renko and range: the reversal amount (Kagi) is the latest 14-bar average true range rounded to two significant figures (`autoSize`, as for the Renko brick) and the point-and-figure box is half of it (as for range bars), since a column turns after three boxes. A setting for them is a later item (it would be one for all four price-move types together).
- **Close-based**: Kagi and point and figure read closes (not highs and lows). Source: the standard textbook method; TradingView offers a high/low variant too, which is not built.
- **Drawn by one custom series** (Lightweight Charts `addCustomSeries`), with the geometry in pure functions (`chartlab/shapes.ts`) that return shapes in price and x terms and are tested without a canvas. High-low, volume candles, Kagi and point and figure share it.
- **Builders are pure** (`derived.ts`: `kagi`, `pointAndFigure`) and tested against hand-worked sequences.

## Not in scope

Reversal and box settings, high/low-based P&F and Kagi, ATR-or-percent sizing choice, Renko/Kagi on a "traditional" fixed box table, line with markers, volume footprint (needs ticks).

## Evidence required

Unit tests for both builders (worked sequences, reversal rules, thickness flips, determinism, empty and tiny inputs), for every shape function, and for the model (types listed, store accepts them, displayBars); an e2e that opens each new type on three symbols and every interval group, reads pixels that were drawn, and fails on any console error; the full suites.
