# Phase 2d Verdicts Implementation Plan

> **For agentic workers:** executed inline, task by task, tests first, a commit per task group.

**Goal:** Verdicts on demand and for changed stocks, from warehouse packets, with dampened conviction. Spec: [2026-10-12-phase-2d-verdicts-design.md](../specs/2026-10-12-phase-2d-verdicts-design.md).

**Architecture:** new package `athena.verdicts` (calibration, changes, runner, cli); one new warehouse table `verdicts`; the existing `Orchestrator` reused with packet builders that read the warehouse; a verdict panel on the Market tab.

**Tech Stack:** Python, DuckDB, Streamlit, pytest.

## Global Constraints

- Constants named, none tuned: UNCALIBRATED_CAP 50, UNMEASURED_TRUST 0.5, MIN_CASES 300, MIN_EDGE 0.05, MOVE_LIMIT 0.12, STALE_DAYS 30.
- The model key is read only by `build_router`; tests use fakes; no `.env` reads in code written here.
- A verdict's band is never changed by dampening.

## Tasks

1. **Calibration** (`src/athena/verdicts/calibration.py`, `tests/test_verdicts_calibration.py`): `Measure(cases, accuracy, best_fixed)`; `Calibration(measures: {analyst: {horizon: Measure}})` with `trust(analyst)`, `calibrated(analysts)`, `from_log_rows(rows)`, `save/load`; `dampen(verdict, taking_part, calibration)` returns the verdict with `raw_conviction`, shown `conviction`, `calibration` ("uncalibrated" | "calibrated"), `trust`, and a key risk line.
2. **Store** (`warehouse.py`, `tests/test_warehouse_verdicts.py`): table `verdicts(symbol, made_at, data_as_of, close, annual_period, quarter, payload)`; `put_verdict`, `latest_verdict`, `verdict_history`, `verdict_symbols`.
3. **Changes** (`src/athena/verdicts/changes.py`, tests): `Facts(data_as_of, close, annual_period, quarter)`; `facts_from(technical_stored, fundamentals_stored)`; `change_reasons(stored: Facts, now: Facts) -> list[str]`.
4. **Runner** (`src/athena/verdicts/runner.py`, tests with a fake client): `warehouse_builders(warehouse)`, `resolution_for(entry)`, `VerdictRunner(path, make_orchestrator, calibration, clock)` with `latest(symbol)`, `status(symbol)`, `make(symbol)`, `refresh_changed(budget)`.
5. **Command** (`src/athena/verdicts/cli.py`): `python -m athena.verdicts.cli --symbol X`, `--changed N`, `--calibrate [logs]`; writes `.athena/reports/verdicts_latest.md`.
6. **Dashboard** (`market.py`, `service.py`, `app.py`, tests): verdict panel under the chart; the service passes a runner.
7. **Live check and docs**: build calibration from the v2 logs, run one real verdict, TRD section, memory, graph, commit.
