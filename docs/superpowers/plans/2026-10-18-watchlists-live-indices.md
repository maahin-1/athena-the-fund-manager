# Watchlists and live indices (5e-1) Implementation Plan

> Spec: `docs/superpowers/specs/2026-10-18-watchlists-live-indices-design.md`. Executed inline, one commit per task.

**Goal:** many named watchlists (sections, flags, columns, sort, reorder, import/export) kept on the server, and live values for every NSE index.

**Architecture:** Rust `indices.rs` refreshes the exchange `allIndices` snapshot and serves `/api/indices`; a generic JSON document route stores `watchlists`; the web side has a pure model (`watchlists/model.ts`), a store with server sync (`watchlists/store.ts`) and a rewritten `panels/watchlist.ts`.

**Tech stack:** Rust (axum, reqwest, serde_json), TypeScript (vitest, Playwright).

## Global constraints
- Caveman replies; code, commits normal. Commit trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- No scheduled tasks; the index refresh runs only while the terminal runs.
- Symbols and list names are data: rendered with `textContent`, never HTML.
- The fake feed must serve a fake index snapshot so tests need no network.

### Task 1: Index snapshot (Rust)
**Files:** create `terminal/src/indices.rs`; modify `terminal/src/main.rs`, `terminal/src/server.rs`, `terminal/src/fake.rs`.
**Produces:** `indices::Snapshot { as_of, rows }`, `IndexStore` (shared, `latest()`), `parse(&Value) -> Option<Snapshot>`, `run_refresher(store, client, shutdown)`, route `GET /api/indices`.
- [ ] Test first: `parse` of a trimmed real-shape body gives the rows with short names; a body without `data` is `None`; strings like `"19.77"` parse as numbers and `"-"` as `None`.
- [ ] Implement parse + store + refresher (5 s open, 300 s shut; keep last snapshot on failure; `stale_s`).
- [ ] Route returns `{as_of, open, stale_s, rows}`; 503 `{error}` before the first snapshot.
- [ ] Fake feed: `--fake-feed` fills the store with Nifty 50 / Nifty Bank / India VIX that drift.
- [ ] `cargo test`, commit.

### Task 2: Watchlist document route (Rust)
**Files:** modify `terminal/src/layouts.rs` (reuse the store), `terminal/src/server.rs`, `terminal/src/main.rs`.
- [ ] A second `LayoutStore` rooted at `<layouts dir>/../lists`, fixed name `watchlists`.
- [ ] `GET /api/watchlists` (404 when none), `PUT /api/watchlists` (object, 200 KB).
- [ ] Tests: round trip, 404, too big, not an object. Commit.

### Task 3: Watchlist model (TS, pure)
**Files:** create `terminal/web/src/watchlists/model.ts`, `terminal/web/test/watchlists.test.ts`.
**Produces:** `Doc`, `List`, `Item`, `validate`, `migrate(layoutSymbols)`, `addSymbol`, `removeSymbol`, `moveItem`, `setFlag`, `addSection`, `toggleSection`, `renameList`, `newList`, `deleteList`, `sorted(list, quotes)`, `exportText`, `importText`.
- [ ] Tests for each (one-per-list symbol, unique list names, section moves, flag cycle, sort ignoring sections, export/import round trip with `NSE:` prefix and `###` headings, bad documents refused).
- [ ] Implement; commit.

### Task 4: Store with server sync (TS)
**Files:** create `terminal/web/src/watchlists/store.ts`, tests in `watchlists.test.ts`.
- [ ] `WatchlistsStore.load()` (server, else local copy, else migrated default), `save()` debounced PUT, local storage copy, re-read on focus, `subscribe`.
- [ ] Keep the old `WatchlistStore` API (`items`, `add`, `remove`, `set`) as a view of the active list so existing code and layouts work.
- [ ] Tests with a fake fetch: loads server copy, falls back offline, saves once for a burst of changes. Commit.

### Task 5: Index values in the page (TS)
**Files:** create `terminal/web/src/indices.ts`; modify `terminal/web/src/main.ts` (top-bar strip), `feed.ts`.
- [ ] `IndexFeed` polls `/api/indices` (5 s open, 60 s shut), exposes `get(name)` ignoring case, `subscribe`.
- [ ] Top-bar strip Nifty 50 / Nifty Bank / India VIX with change colour. Commit.

### Task 6: Watchlist panel
**Files:** rewrite `terminal/web/src/panels/watchlist.ts`; modify `style.css`, `command.ts`, `main.ts`.
- [ ] List switcher and menu, sections, flags, columns, sorting, drag reorder, import/export dialog, index rows from `IndexFeed`.
- [ ] Commands `ADD SYM TO name`, `LIST name`.
- [ ] Visible-rows feed request (open sections, up to 200). Commit.

### Task 7: End-to-end and docs
**Files:** create `terminal/web/e2e/watchlists.spec.ts`; modify TRD, master design, memory.
- [ ] Playwright: new list, section, flag, reorder, export text, reload keeps it; index rows have values; offline fallback.
- [ ] Rebuild exe + web, run `cargo test`, vitest, full Playwright; `graphify update .`; commit.
