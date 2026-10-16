# Phase 4a: the terminal (release 1, market panels)

Parent: the live price panel and stream ([2j](2026-10-13-phase-2j-live-stream-design.md)). The Streamlit dashboard reruns its script on every refresh, so it cannot show ticks as they arrive or give a docked, customizable workspace. The terminal is a second, separate app beside it. Streamlit stays as it is and keeps Analyse, Backtest and Verdicts until the terminal replaces them in later releases.

## Goal

A fast local trading-terminal page (TradingView and Bloomberg style): docked panels the user arranges, saves and links, driven by a live price feed pushed to the page, with the exchange quote page as the fallback.

## Releases

1. **4a (this spec): shell and market panels.** Server, docking, saved layouts and presets, symbol links, command line, watchlist, chart with indicators, depth and ticks, quote, tape, index tabs.
2. **4b: analyst panels.** Verdicts with trust, fundamentals, risk overlay (reads stored results; the Python side stays, called by the server).
3. **4c: backtest panel.**

## Design

**Server (Rust, one program, Windows).** Logs in to Angel One (API key, client code, PIN and TOTP from the settings file), downloads and caches the instrument file, holds the one market-data WebSocket in snap-quote mode, decodes ticks (prices in paise) and keeps the latest quote per symbol. Up to about 50 symbols stream at once: every symbol visible in any open panel; Angel One's forum and WebSocket 2.0 announcement give 1000 token subscriptions per session (counted per token and mode) and 3 connections per client code, so a cap of 50 in one connection and one mode is far inside the limit (found 2026-10-14 from forum posts, not the formal docs; recheck). Serves the page, the WebSocket for the page, and a small HTTP API. Reads the DuckDB warehouse read-only for daily bars. Fetches Yahoo 1-minute bars in the background and caches them. Saves layouts as JSON files under `.athena/terminal/`.

**Page (TypeScript, bundled once with Vite).** dockview for drag, resize, dock and tabs; Lightweight Charts for candles, volume and indicators; the Streamlit chart's indicator set is reused (same names and parameters). Plain DOM, no UI framework.

**Push.** One WebSocket per page. The page sends the symbols its panels show; the server sends, every 16 ms, only the quotes that changed since the last frame (merged per symbol). JSON first; binary only if a measurement shows the need. The page updates the last candle in place and flashes the price on each tick.

**Customizing.** Drag, resize, dock and tab every panel. Save, load and delete named layouts. Layouts A (one stock), B (chart grid with a tape) and C (dense) ship as presets. Each panel carries a link colour: panels with the same colour follow one symbol, unlinked panels stay put.

**Command line and hotkeys.** A symbol (`SBIN`), `SBIN CHART`, a layout name, `ADD sym` and `REMOVE sym` for the watchlist. F-keys focus panel groups. More commands come later.

**Bars.** Daily bars from the warehouse; 1, 5 and 15 minute bars from Yahoo, cached; live ticks update the last bar.

## Design references

Studied for behaviour and layout ideas only. No code, asset, icon, name or data is copied; both products' terms forbid it. TradingView was observed with REA on its public chart page (viewport capture and accessibility tree of a signed-out page, no script sources read). Bloomberg's terminal cannot be opened by us, so its conventions below come from public descriptions, not observation.

**From TradingView (observed):**
- Top toolbar order: menu, symbol box, add-compare, interval, chart type, indicators, layout setup, alert, replay, undo and redo, then layout save and manager on the right, with settings, fullscreen and snapshot. Controls are icon-first with a text label only for the main actions.
- Chart header: name, interval and exchange, then an OHLC legend that updates with the crosshair; volume as a coloured histogram at the foot of the same pane (not a separate panel); a live last-price tag on the price axis with a dotted line across the chart; a second tag for pre/post-market.
- Range row under the chart: 1D 5D 1M 3M 6M YTD 1Y 5Y All, each tied to a sensible bar size (1D in 1-minute bars, 5D in 5-minute, 1M in 30-minute and so on), plus a clock with the exchange timezone and an adjusted-prices toggle.
- Right side: a watchlist with columns Symbol, Last, Change, Change%, collapsible sections (Indices, Stocks), a coloured flag per row for link groups, and the selected row outlined. Below it a symbol detail block: large price, "last update" time, then facts.
- A narrow icon rail on the far right opens widgets (watchlist, alerts, object tree, calendars); a slim left rail holds chart tools.
- Light and dark themes; red and green candles.

**From Bloomberg (public descriptions):**
- A command line is the main way to move: type a symbol, then a function code (for example a quote, chart or fundamentals screen) and press enter. The function keys select panel groups.
- Dense, four-panel screens on a black background with amber text for labels and green and red for moves; numbers dominate and chrome is minimal.
- Panels are linked by colour group (red, yellow, green, blue): a symbol change in one panel moves every panel in the same group.

**What 4a takes from each.**
- TradingView: toolbar order, OHLC legend, volume inside the chart pane, last-price tag and line, range row with matching bar sizes, watchlist columns with collapsible sections and flags, light and dark themes.
- Bloomberg: command line first, function keys, colour link groups, dense dark default.
- Left out of 4a: the drawing tools rail, replay, buy and sell tiles (read-only), alerts, the right-hand widget rail beyond the panels already listed.

## Rules

1. **Read-only.** No order, position or funds call exists in the program. It subscribes to prices only.
2. **Secrets.** Keys, PIN, TOTP secret and tokens are never printed, logged, put in an error or sent to the page. The page cannot read them.
3. **Local only.** The server listens on 127.0.0.1. Every WebSocket and API request must carry a Host header naming this server (blocks DNS-rebinding pages); a WebSocket must also carry this server's own Origin, and an API request must carry it or none (a same-origin GET sends none). Anything else is refused with 403.
4. **Fresh or fall back.** The stream is used while its last tick for a symbol is at most 15 seconds old by arrival time; otherwise, while connecting, or if login or the stream fails, the exchange's polled quote is used and the top bar says so. A broken feed never stops the page opening.
5. **Same quote shape and rules as 2j** for series fallback (`-EQ`, `-BE`, `-BZ`, `-SM`, `-ST`), circuit limits and market-closed behaviour.
6. **Not in the verdict.** Nothing from the stream reaches an analyst or a verdict.
7. **Measured, not claimed.** The top bar shows the measured delay from the server receiving a tick to the page drawing it. The real tick rate is checked in market hours before the work counts as done.

## Testing

Rust unit tests: TOTP code, tick decoding (the same sample packets the Python tests use), the per-frame merge, stale detection and fallback choice, symbol resolution. TypeScript unit tests: the command parser, layout save and load, link behaviour. A Playwright smoke test opens the page against a fake feed and checks panels, a layout save and a symbol link. A market-hours check against the real feed.

## Not in 4a

Orders, portfolio, alerts, storing ticks, news, a heatmap, and the analyst and backtest panels (4b and 4c).

## Addendum, 2026-10-16: 4b (analyst panels) and working with the market shut

The user asked that charts, analysis and everything stay usable when the market is off. Added to release 1 so it holds together:

- **Analysis panel** (tabs Overview, Technicals, Fundamentals) and **Verdict panel**, both reading only stored data from the warehouse through `GET /api/analysis?symbol=` (screen row, technical and fundamentals packets, newest verdict, last loaded day). No market data is needed.
- **Make a verdict** button: `POST /api/verdict/<symbol>` runs the project's Python command (`python -m athena.verdicts.cli --symbol X`) one at a time with a five-minute limit; `GET /api/verdict-job` reports idle, running, done or failed with a fixed message (nothing from the command's output reaches the page). It calls the language model (about 45 seconds measured for INFY, stored as Overweight, conviction 8, raw 23).
- **Layout D (research):** watchlist, chart (6M daily with EMA and SMA), analysis, verdict, tape.
- **Market closed:** the top bar says so and when the market opens (weekday 09:15 to 15:30 IST; holidays are not known). The feed snapshot, the exchange poll (every 5 minutes when shut) and Yahoo's last session keep the watchlist, depth, quote and chart populated; if no intraday bars exist, the chart shows six months of daily bars with a note.
- Verdict panel shows the calibration note, each analyst's call (click for its reasoning), risks, skipped analysts and how many days of newer prices exist since it was made.

## Addendum, 2026-10-16 (2): fit, layout controls and the Research page

The user reported that the app is wider than the screen (sides and bottom cut off), cannot be resized from the app, hides the volume, and shows the analysts' reasoning too small.

- **Fit.** Cause: the top bar could not shrink below about 1,070 CSS px, so on a laptop at 125 to 150% display scaling the page was wider than the window. The bar wraps; the market-closed text shares the status area with action messages (a message stays 10 seconds); `html` and `body` never scroll. Tested at 1280x720, 1024x640, 900x560 and 700x600 on layouts A to D and the Research page: no horizontal or vertical overflow.
- **Layout controls in the app.** Maximise and Float buttons on every group's tab bar (Esc restores a maximised panel; the float button becomes a dock-back button); a "+ Panel" menu adds a chart, watchlist, depth, quote, analysis, verdict or tape beside the focused panel; Reset restores the last preset; the drag handles between panels are 9 px wide with a visible line; tabs still drag to dock anywhere.
- **Volume.** Candles keep the top 70% of the pane; volume has its own scale in the bottom 18%, so the two never overlap.
- **Research page.** The top bar has two views, Terminal and Research (F5 toggles; commands `RESEARCH [SYM]`, `SYM RESEARCH`, `TERMINAL`; a saved layout named Research still wins a bare `research`). One stock per page: header with live price (when the feed has it), a sticky section bar (Verdict, Analysts, Overview, Fundamentals, Technicals), the verdict (band, conviction, raw, trust and calibration note, the date it was made and how many newer days exist, which sides disagree, risks, notes, analysts not run, Make a verdict button), one card per analyst (signal, confidence bar, data coverage, the full reasoning, missing inputs), and the stored overview, fundamentals (grouped) and technicals. Stored data only, so it works with the market shut. A watchlist column picks the stock (a drop-down below 760 px); it follows and drives the blue link group, so picking in either view moves the other. The terminal stays alive underneath.
- Not in this change: moving the Research page's sections into dockable panels, a backtest view (4c), printing or exporting a report.

## Addendum, 2026-10-16 (3): what the Streamlit app had that the terminal lacked

From the audit in docs/terminal-audit-2026-10-16.md.

- **Adjusted prices.** Daily bars come from `python -m athena.warehouse.chart_bars SYM` (the same split and bonus adjustment the Streamlit charts used, read-only on the warehouse), kept by the server for 30 minutes; the first request for a stock takes about two seconds. If it fails the raw bars are shown with a note. Indices are not adjusted. Unexplained price breaks are listed in the chart's note.
- **Chart behaviour.** The time axis cannot scroll past the last bar. Ticks move the intraday chart only during 09:15 to 15:30 IST and only when within 25% of the last close (a snapshot stamped after the close, or a price that is not this stock's, drew phantom candles). 1M is 22 daily bars. Indicators show as removable chips.
- **One title row per panel.** The tab names the panel (`Chart · SBIN · 1D`); the link-colour dot sits on the group's tab bar; joining a colour takes that colour's symbol. F6 cycles the views (F5 is the browser's reload).
- **Symbols are checked** against the warehouse (`/api/known`): a typo is refused; `NIFTY 50` and `NIFTY BANK` can be typed.
- **Screen view.** The Streamlit Market screen: `/api/screen` returns the stored screen table once; the page filters and sorts it (search, stocks or ETFs, trend, sector, no data flags, nine numeric ranges) with the six presets (Value, Quality compounders, Momentum, Beaten down, Dividend payers, Liquid large caps). A stock with no figure in a limited column never passes the limit; missing figures sort last. Row actions: Chart, Research, + Watchlist.
- **Live analysis.** The Research page has an "Analyse live" box (ticker, ISIN or name). `POST /api/analyse` talks to one long-running Python worker (`athena.dashboard.worker`, started on the first request, about 15 seconds to build the service) that runs the same `DashboardService.view` the Analyse tab used: all five specialists including news, the technical, risk, valuation, quality and earnings panels, key risks and notes. One request at a time, 5 minute limit, a dead worker is replaced on the next request. Only the project's own error messages reach the page. The page lists candidates when a name is ambiguous, and shows the analysis above the stored verdict. Measured: TCS took 107 seconds. Not yet: the risk-profile form (the analysis runs with no risk limits and says so) and the backtest.
- Still open: live index prices, the backtest view (4c), the risk-profile form, light-theme polish.
