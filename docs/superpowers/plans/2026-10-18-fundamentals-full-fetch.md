# Full-market fundamentals fetch: run book (started 2026-10-18)

Owner decision 2026-10-18: run the full fetch in the background; check it every 30 to 45 minutes; then run the two follow-up stages; work on other things meanwhile.

## State

| Stage | Command | Status | Expected time |
|---|---|---|---|
| 1. Statements | `.venv/Scripts/python.exe -u -m athena.fundamentals.ingest --universe all --resume` (log `logs/fund-ingest-all.log`) | **running** since 2026-10-18; 3,103 companies in scope (2,605 listed stocks plus old symbols of renamed ones); ~54 were done before | measured after 21 minutes: 24 s a company, so the rest of the Nifty 500 about 3 to 4 h and everything about 20 h (my earlier 8 to 12 h came from a first sample that held many companies with nothing to fetch) |
| 2. Shareholding | `.venv/Scripts/python.exe -u -m athena.fundamentals.holdings --universe nifty500 --resume` then `--universe all --resume` | not started; run after stage 1 ends (one writer on the store) | about 20 quarterly files per company (0.3 to 1.3 MB each, 1.5 s apart): roughly 40 to 60 s a company, so Nifty 500 about 6 to 8 h, all about 35 h or more; Nifty 500 first, `all` only if wanted |
| 3. Filing documents | `.venv/Scripts/python.exe -u -m athena.fundamentals.documents --universe all --resume` | not started; run after stage 2 | two requests per company, about 4 s: about 3 h for all |

All three are resumable (`--resume`), throttled, stop cleanly if the exchange blocks them (the block and its retry time are recorded), and are started by hand. Only one of them may run at a time: the store has one writer. The Fundamentals tab's per-company fetch button uses the same store, so it must not be used while one runs.

## The check (every 30 to 45 minutes)

1. Is it alive? The log's modified time should be seconds old (`logs/fund-ingest-all.log`) and a `python.exe` process present. A background command may be stopped by the sandbox after about 10 minutes; if it is dead and the log's last line is not the closing summary, start it again with the same command (`--resume` carries on).
2. Progress: companies fetched this run (lines `SYMBOL fetched N skipped N failed N Ns`), companies with statements in `.athena/fundamentals.duckdb` (`select count(*) from companies`), the last symbol reached.
3. Trouble: lines with `failed` above 0 for many companies in a row, or a recorded block (`blocked` / retry time in the log or `ingest_runs`): report it and do not start it again before the recorded time.
4. When stage 1's log ends with its summary line, start stage 2 (Nifty 500), then stage 3. Report each start and each finish with counts.
5. When stage 3 ends: spot-check the Fundamentals tab for three companies that were not stored before (a bank, a manufacturer, a renamed company), record the counts (companies, periods, shareholding quarters, documents) in the TRD and the master design, `graphify update .`, commit.

## Safe to build meanwhile

Anything that does not write `.athena/fundamentals.duckdb`: web and Rust work (chart types, screens and peers that read the warehouse's `screen` table, alerts), tests, docs. Rebuilding the terminal server does not disturb the fetch.
