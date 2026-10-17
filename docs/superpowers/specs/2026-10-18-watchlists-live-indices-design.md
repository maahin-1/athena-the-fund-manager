# Watchlists and live indices (5e, first slice) — design

Date: 2026-10-18. Part of batch B3 in the master design (`2026-10-17-parity-master-design.md`). Source of requirements: `2026-10-17-tradingview-breakdown.md` section 4.4. Premium-tier limits are not copied: lists and symbols are uncapped here except where the data feed forces a cap (stated below).

## 1. What exists

The Terminal's watchlist panel shows one list of symbols (kept inside the saved layout), two index rows (Nifty 50, Nifty Bank) filled from the last stored daily close, and Symbol / Last / Change / Change% columns. Live prices come from the Angel One stream (50 symbols at once) and the exchange quote poll for the rest.

## 2. Goal

Many named lists with sections, flags, column choice, sorting, drag reordering and text import/export; all NSE indices with live values; the lists kept on the server so they survive layout changes and browser changes.

Out of scope for this slice (next slice, 5e-2): the symbol details panel (performance, seasonals, technicals gauge, key facts), advanced view with fundamentals columns, and alerts on a list (5g). BSE indices (Sensex) have no NSE source and wait for the BSE/F&O batch.

## 3. Live indices

**Source.** The exchange's `allIndices` snapshot (`https://www.nseindia.com/api/allIndices`, same cookie priming as the quote poll). One request returns about 139 indices with last, change, change %, open, high, low, previous close, 52-week high and low, P/E, P/B, dividend yield, advances, declines, and the values a week, month and year ago. Verified 2026-10-18 (market closed: it returns the 16 Oct close).

**Server.** A new module `indices.rs` holds the latest snapshot and refreshes it every 5 s while the market is open and every 5 minutes when shut (the same open/closed rule as `poll::market_open`). `GET /api/indices` returns `{ "as_of": "<exchange timestamp>", "open": bool, "rows": [...] }` with the fields above, short names (`s`, `l`, `pc`, `ch`, `pct`, `o`, `h`, `lo`, `h52`, `l52`, `pe`, `pb`, `dy`, `w1`, `m1`, `y1`, `adv`, `dec`). A failed refresh keeps the last snapshot and marks it stale (`stale_s` seconds). The page polls this route with the same cadence; no new socket.

**Page.** Index symbols (any name the snapshot holds, matched ignoring case) show live values in any list, in the details of the Chart Lab symbol box, and in a top-bar strip of Nifty 50, Nifty Bank, India VIX. Where the snapshot has no row the old behaviour stays (last stored daily close).

## 4. Watchlist data model

One document, `watchlists`, stored by the server in `.athena/terminal/lists/watchlists.json` and served by `GET /api/watchlists` (404 when none) and `PUT /api/watchlists` (an object up to 200 KB, same rule as layouts).

```
{ "version": 1, "active": "<list id>", "lists": [
  { "id": "l1", "name": "Main", "columns": ["last","chg","chgpct"], "sort": {"key": null, "dir": 1},
    "items": [ {"t":"section","title":"Banks","color":"blue","closed":false}, {"t":"sym","s":"SBIN","flag":"red"}, ... ] } ] }
```

- A list is an ordered array of items: a `section` (a coloured, collapsible heading) or a `sym` (symbol plus optional flag colour). A symbol appears once per list.
- Flags: none, red, orange, yellow, green, blue, purple, pink (the same eight as TradingView's flag lists). A flag shows as a coloured dot; the list can be filtered to one flag.
- Columns offered: Last, Chg, Chg%, Open, High, Low, Prev close, Volume, 52w High, 52w Low, Source. Order and choice are per list. Sorting by a column ignores section order for the sort and shows a flat list; clearing the sort restores sections.
- Index rows have the columns they have (Volume is empty).
- Export: one line `NSE:SBIN,NSE:TCS,...` per section with `###Section` headings, as TradingView writes; import accepts that, plain comma/newline separated symbols, and ignores the `NSE:` or `BSE:` prefix. Unknown symbols are listed in a message and skipped (checked with the existing `/api/known` lookup).
- Migration: if no document exists, list "Main" is created from the layout's `watchlist` array (default eight symbols) with sections Indices (Nifty 50, Nifty Bank) and Stocks. The layout's own `watchlist` field stays for old saved layouts and is written as the active list's symbols so older layouts still open.

## 5. Feed cost

The hub streams at most 50 symbols (Angel limit) and polls the rest. The panel asks the feed for the symbols that are visible (the open sections, up to 200). Long lists scroll; hidden rows get quotes when they scroll in.

## 6. Panel behaviour

- Header: list switcher (select), New, Rename, Delete (asks), Import, Export, column menu.
- Row click moves linked panels (unchanged); remove with the × button; flag via a small dot button that cycles through colours; drag a row (or a section with its rows) to reorder; a drop between sections moves it into that section.
- Section header: click to collapse, colour dot, rename on double-click, × removes the heading (keeps its symbols).
- Command line: `ADD SYM` adds to the active list (end), `ADD SYM TO <list>` to a named one, `LIST <name>` switches, `REMOVE SYM` removes from the active list.

## 7. Errors and edge cases

- The server being offline: lists load from the last copy kept in the page's local storage, and a change is saved when the server returns. A document that fails validation is refused with a message and the previous list kept.
- Two pages open: last write wins; the page re-reads the document on focus.
- The exchange snapshot refusing: the index rows keep the last values with a "stale" mark; nothing is guessed.
- Same symbol typed twice, an empty list, a list name already taken (a number is appended), a name longer than 40 characters: handled in the model with tests.

## 8. Testing

- Rust: parse the snapshot (real shape, trimmed fixture), refresh keeps the last snapshot on failure, `/api/indices` and `/api/watchlists` routes (round trip, size limit, bad JSON).
- Web model (pure functions, vitest): add/remove/move/sort/filter, flags, sections, import/export round trip, migration from a layout.
- Panel: Playwright — create a list, add a section, flag, reorder, export text, reload keeps it; index rows show values from the fake snapshot.
- The fake feed (`--fake-feed`) also serves a fake `allIndices` so tests do not need the exchange.
