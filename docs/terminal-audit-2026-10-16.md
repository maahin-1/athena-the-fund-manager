# Terminal audit, 2026-10-16

Method: the real-feed terminal (port 8700, market shut) driven in a browser (Jev chose the first action through `jev choose`; the rest followed its browser-use loop), a read of the Streamlit app for what it showed, and Codex reviews (`codex review --base b57f430~1` over the whole terminal). Status: **F** fixed, **O** open.

## A. Chart

| # | Finding | Evidence | Status |
|---|---|---|---|
| A1 | Scrolling right goes past the last candle into empty time; live ticks could then start candles that are not the stock's | A snapshot stamped 16:08 IST (after the close) would open a new 1-minute bar; a fake or wrong price (2230 against 962) drew a jump candle | **F** the time axis cannot scroll past the last bar; ticks outside 09:15 to 15:30 IST or 25% from the last close are ignored; 2 unit tests |
| A2 | Daily bars are unadjusted: splits and bonuses show as cliffs. Streamlit adjusted them | KOTAKBANK closes 2132.6 then 421.0 on 2026-01-14 (5:1 split), a drop of 80% | O |
| A3 | 1M uses 5 days of 30-minute bars (Yahoo's limit), so the label lies; 5Y and All show 3.8 years with no note | 1M legend and note; warehouse starts 2023-01-02 | O |
| A4 | Indicator picker is a toggle with no list of what is on, so an indicator cannot be seen or removed except by choosing it again | RSI, MACD, Bollinger all on, no chips | O |
| A5 | Intraday 1D chart ends on Yahoo's flat auction bar (O=H=L=C, volume 3.03L) | `/api/bars?symbol=SBIN&tf=1m` last bar | O |
| A6 | An unknown symbol shows "loading intraday bars…" and "waiting for a quote…" forever and is added to the watchlist | `add zzzfake` | O |

## B. Layout and window

| # | Finding | Status |
|---|---|---|
| B1 | Page wider than the screen on a scaled laptop (top bar minimum width) | **F** (53a9194) |
| B2 | No in-app panel controls | **F** (maximise, float, add panel, reset) |
| B3 | Watchlist cramped on a 1024 px window: horizontal scrollbar, wrapped symbols, uneven rows | O |
| B4 | Each panel has two title rows (tab and the panel header); wastes about 25 px of height per panel | O |
| B5 | F5 is captured for the Research view, so the browser's reload key no longer works | O |
| B6 | Delete layout uses a browser `prompt()` | O |
| B7 | Light theme: chart pane, tab bars and teal accents were not tuned; unchecked in detail | O |

## C. Data shown

| # | Finding | Status |
|---|---|---|
| C1 | Packet warnings (unexplained price breaks) are not shown: the server never reads `packets.flags` (38 packets have flags) | O (Codex, confirmed) |
| C2 | A symbol beyond the 50 streamed gets no poll either, so its panels stay empty | O (Codex) |
| C3 | Top bar says "Angel live" while a symbol is on the polled fallback | O (Codex) |
| C4 | Changing a panel's link colour does not move it to that colour's symbol | O (Codex) |
| C5 | Index rows (NIFTY 50, NIFTY BANK) show the last daily close, not a live price | O (known) |
| C6 | Depth shows only the ask side after the close | O (cosmetic: say "market closed") |

## D. Missing against Streamlit

| # | Streamlit had | Terminal has | Status |
|---|---|---|---|
| D1 | Analyse a stock: type a ticker, ISIN or name, resolve ambiguity, run all five analysts live with the risk overlay (held back by risk limits), news analyst, at-a-glance panels, key risks, notes | Stored verdicts of four analysts; button makes one | O |
| D2 | Market screen: six presets (value, quality compounders, momentum, beaten down, dividend payers, liquid large caps), numeric filters, sorting, chosen columns, chart, verdict | nothing | O |
| D3 | Backtest rules and strategies (8 years, blinding check, curves) | nothing (release 4c) | O |
| D4 | Risk profile form | nothing | O |
| D5 | Live price block | covered by Watchlist, Quote, Depth, Tape | done |

## E. Tooling

| # | Finding | Status |
|---|---|---|
| E1 | `jev-delegate` skill: choose the executor by use case and make reviews cross-family, fresh and read-only | **F** installed for Claude Code, Hermes and Codex |
| E2 | `~/.codex/hooks.json` fails to parse (`unknown field SessionStart`) and 324 Codex skills exceed its context budget (descriptions dropped) | O, not touched |
