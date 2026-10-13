# Phase 4a: the terminal (release 1, market panels)

Parent: the live price panel and stream ([2j](2026-10-13-phase-2j-live-stream-design.md)). The Streamlit dashboard reruns its script on every refresh, so it cannot show ticks as they arrive or give a docked, customizable workspace. The terminal is a second, separate app beside it. Streamlit stays as it is and keeps Analyse, Backtest and Verdicts until the terminal replaces them in later releases.

## Goal

A fast local trading-terminal page (TradingView and Bloomberg style): docked panels the user arranges, saves and links, driven by a live price feed pushed to the page, with the exchange quote page as the fallback.

## Releases

1. **4a (this spec): shell and market panels.** Server, docking, saved layouts and presets, symbol links, command line, watchlist, chart with indicators, depth and ticks, quote, tape, index tabs.
2. **4b: analyst panels.** Verdicts with trust, fundamentals, risk overlay (reads stored results; the Python side stays, called by the server).
3. **4c: backtest panel.**

## Design

**Server (Rust, one program, Windows).** Logs in to Angel One (API key, client code, PIN and TOTP from the settings file), downloads and caches the instrument file, holds the one market-data WebSocket in snap-quote mode, decodes ticks (prices in paise) and keeps the latest quote per symbol. Up to about 50 symbols stream at once: every symbol visible in any open panel; the exact Angel limit is checked against its documentation before the build and the cap is set below it. Serves the page, the WebSocket for the page, and a small HTTP API. Reads the DuckDB warehouse read-only for daily bars. Fetches Yahoo 1-minute bars in the background and caches them. Saves layouts as JSON files under `.athena/terminal/`.

**Page (TypeScript, bundled once with Vite).** dockview for drag, resize, dock and tabs; Lightweight Charts for candles, volume and indicators; the Streamlit chart's indicator set is reused (same names and parameters). Plain DOM, no UI framework.

**Push.** One WebSocket per page. The page sends the symbols its panels show; the server sends, every 16 ms, only the quotes that changed since the last frame (merged per symbol). JSON first; binary only if a measurement shows the need. The page updates the last candle in place and flashes the price on each tick.

**Customizing.** Drag, resize, dock and tab every panel. Save, load and delete named layouts. Layouts A (one stock), B (chart grid with a tape) and C (dense) ship as presets. Each panel carries a link colour: panels with the same colour follow one symbol, unlinked panels stay put.

**Command line and hotkeys.** A symbol (`SBIN`), `SBIN CHART`, a layout name, `ADD sym` and `REMOVE sym` for the watchlist. F-keys focus panel groups. More commands come later.

**Bars.** Daily bars from the warehouse; 1, 5 and 15 minute bars from Yahoo, cached; live ticks update the last bar.

## Rules

1. **Read-only.** No order, position or funds call exists in the program. It subscribes to prices only.
2. **Secrets.** Keys, PIN, TOTP secret and tokens are never printed, logged, put in an error or sent to the page. The page cannot read them.
3. **Local only.** The server listens on 127.0.0.1 and rejects WebSocket and API requests from any other origin.
4. **Fresh or fall back.** The stream is used while its last tick for a symbol is at most 15 seconds old by the exchange's timestamp; otherwise, while connecting, or if login or the stream fails, the exchange's polled quote is used and the top bar says so. A broken feed never stops the page opening.
5. **Same quote shape and rules as 2j** for series fallback (`-EQ`, `-BE`, `-BZ`, `-SM`, `-ST`), circuit limits and market-closed behaviour.
6. **Not in the verdict.** Nothing from the stream reaches an analyst or a verdict.
7. **Measured, not claimed.** The top bar shows the measured delay from the server receiving a tick to the page drawing it. The real tick rate is checked in market hours before the work counts as done.

## Testing

Rust unit tests: TOTP code, tick decoding (the same sample packets the Python tests use), the per-frame merge, stale detection and fallback choice, symbol resolution. TypeScript unit tests: the command parser, layout save and load, link behaviour. A Playwright smoke test opens the page against a fake feed and checks panels, a layout save and a symbol link. A market-hours check against the real feed.

## Not in 4a

Orders, portfolio, alerts, storing ticks, news, a heatmap, and the analyst and backtest panels (4b and 4c).
