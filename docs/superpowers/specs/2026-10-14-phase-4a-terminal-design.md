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
