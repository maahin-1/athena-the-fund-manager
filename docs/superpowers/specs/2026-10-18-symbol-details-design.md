# Symbol details panel and advanced watchlist view (5e, second slice) — design

Date: 2026-10-18. Batch B3 in the master design (`2026-10-17-parity-master-design.md`). Requirements source: `2026-10-17-tradingview-breakdown.md` section 4.4 (symbol details: key facts, performance 1W to 1Y and YTD, seasonals, technicals gauge, profile) and the 5e-2 notes of `2026-10-18-watchlists-live-indices-design.md`.

## 1. Goal

A **Details** panel that follows the linked symbol like Quote does and shows, for any stock, ETF or index the warehouse holds: key facts, a performance table, a technicals gauge (Buy/Sell summary from oscillators and moving averages) and seasonals (year-on-year paths and average month returns). The watchlist gets an advanced view: market-wide fundamentals and return columns for every stock, not only the ones whose statements were fetched.

Out of scope: ATM IV term structure and volatility curve (need the option chain, batch F&O), news (batch B9), intraday technicals (the gauge uses daily, weekly and monthly bars), website/employees in the profile (no free source; ISIN and sector are shown).

## 2. Data, no new feeds

- **Bars:** `/api/bars?symbol=&tf=1D&limit=5000` (split-adjusted for stocks, index bars for indices). One fetch per symbol feeds performance, technicals and seasonals; kept 5 minutes in a module cache so a re-selected symbol is instant.
- **Market screen:** `/api/screen` already returns, for every stock and ETF, sector, market cap, P/E, P/B, ROE, net margin, debt/equity, dividend yield, revenue growth, RSI 14, 1M/3M/6M/1Y returns, distance from the 52-week high and low. A new `ScreenStore` loads it once (refreshed when 10 minutes old, retried after 30 s on failure) and answers `get(symbol)`. This is why Details and the advanced columns work for the whole market and need no fetch.
- **Live quote:** day range, volume, circuit band, 52-week range (feed); for indices the snapshot row (P/E, P/B, yield, advances/declines, 52-week range).
- **Name, kind, ISIN:** `/api/known` gains `isin` (it already reads the `universe` row).

No new route, no new job. Rust change: `known` returns `isin`.

## 3. Performance table

Rows: 1W, 1M, 3M, 6M, YTD, 1Y, 3Y, 5Y. Base price = close of the last bar on or before the calendar date `(last bar date − period)`; YTD base = last close of the previous calendar year. A period that starts before the first stored bar shows `—` (never a guess). Value = last close ÷ base − 1; the last close is the live quote when the market is open and the quote is fresh, else the last bar. Shown as a signed percent, coloured, with a bar scaled to the largest absolute value in the table.

## 4. Technicals gauge

The rules are written down here and unit-tested, because TradingView's own are only partly published; this is our reading of the commonly used definitions, and the panel says so ("rules: see TRD").

**Oscillators** (each gives buy, sell or neutral; `prev` = the previous bar):

| Indicator | Buy | Sell |
|---|---|---|
| RSI 14 | below 30 and rising | above 70 and falling |
| Stochastic 14,3,3 | %K and %D below 20 and %K above %D | both above 80 and %K below %D |
| CCI 20 | below −100 and rising | above 100 and falling |
| ADX 14 with ±DI | ADX above 20 and +DI above −DI | ADX above 20 and +DI below −DI |
| Awesome oscillator | above 0 and rising | below 0 and falling |
| Momentum 10 | above prev | below prev |
| MACD 12,26,9 | MACD above signal | below signal |
| Stochastic RSI 14,14,3,3 | %K below 20 and above %D | %K above 80 and below %D |
| Williams %R 14 | below −80 and rising | above −20 and falling |
| Bull Bear Power (EMA 13) | above 0 and rising | below 0 and falling |
| Ultimate oscillator 7,14,28 | above 70 | below 30 |

**Moving averages:** EMA and SMA of 10, 20, 30, 50, 100, 200, plus HMA 9 and VWMA 20: buy when close is above the average, sell when below. An average without enough history is left out of the count.

**Summary:** per group and overall, counts of buy, neutral, sell; score = (buy − sell) ÷ counted; label: score ≤ −0.5 Strong sell, < −0.1 Sell, ≤ 0.1 Neutral, < 0.5 Buy, otherwise Strong buy. Timeframe switch 1D / 1W / 1M (weekly and monthly bars are built from the daily bars with the existing `resampleDaily`). The gauge is drawn as a half-dial in SVG with the needle at the overall score, plus the three count rows and an expandable list of every indicator with its value and verdict.

## 5. Seasonals

For each calendar year with a prior year-end close, the path of cumulative return since that close, by day of year (x = 0..1 across the year). Up to the last 10 years drawn as thin lines, the current year bold, the average path dashed. Below it, 12 cells for January to December: average monthly return across all full years in the data and the share of years the month was up (hit rate), with the number of years used. Monthly return = month-end close ÷ previous month-end close − 1. Fewer than 2 full years: the chart is replaced by "needs at least 2 years of prices".

## 6. Key facts

Shown when known, `—` otherwise: name, kind, sector, ISIN; market cap, P/E, P/B, ROE, net margin, debt/equity, dividend yield, revenue growth (from the screen, so each says "from the stored statements"); day range, 52-week range with a position marker, average volume (20 days, from bars), circuit band. An index shows P/E, P/B, yield, advances/declines, 52-week range and the week/month/year-ago values from the snapshot. A stock whose statements are missing shows the market facts and a line "no statements stored — open Fundamentals to fetch" (the Fundamentals page already has the button).

## 7. Panel

Type `details`, title `Details · SYM`, linked by colour like Quote. Sections in order: header (name, price, change), Key facts, Performance, Technicals, Seasonals; each collapsible, state kept in the panel state. Tab of the Quote group in presets A and M, a normal panel elsewhere; `DETAILS [SYM]` and `DET` open it from the command line, and it is in the Add panel list. A symbol with fewer than 30 bars: facts only and "not enough history for performance, technicals and seasonals".

## 8. Advanced watchlist columns

New columns: Sector, Mkt cap (Rs cr), P/E, P/B, ROE, Div yield, RSI 14, 1M, 3M, 1Y (returns shown as percent). Values come from `ScreenStore`; indices show `—` except P/E, P/B, yield from the snapshot. Sorting works as for the other columns (numbers sort numerically, nulls last). The column menu gets two preset buttons: **Basic** (Last, Chg, Chg%) and **Advanced** (Last, Chg%, Mkt cap, P/E, ROE, 1Y). The list document's column validation accepts the new keys; old documents are unchanged.

## 9. Errors and edge cases

Bars unavailable (warehouse busy): the sections say "prices are not available right now" and retry on the next symbol change, nothing is drawn from nothing. Stale answers (a slow fetch for a symbol that is no longer shown) are dropped. Constant-price series (RSI/CCI divide by zero) give neutral. A screen row of nulls shows `—`. An unknown symbol shows "not in the market data".

## 10. Testing

- Vitest, pure functions: performance bases (leap years, month ends, YTD, too-short history), every gauge rule with a hand-built series that triggers buy, sell and neutral, summary thresholds, seasonal paths and month statistics on a synthetic two-year series with known answers, screen store (load, TTL, failure retry, lookup case-insensitive), watchlist new columns (validation, sort values).
- Rust: `known` returns the ISIN.
- Playwright (real warehouse): open Details for SBIN, see the four sections, performance rows have values, the gauge shows one of the five labels, switching 1D/1W/1M changes the counts' total, seasonals draw lines; the Advanced preset shows P/E and Mkt cap numbers in the watchlist for SBIN and sorts by P/E; no console errors; layout audit still passes with the new panel in preset A and M.
