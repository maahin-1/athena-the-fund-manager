# Phase 2e Technical Test Implementation Plan

> **For agentic workers:** executed inline, tests first, one commit.

**Goal:** Measure the technical analyst against the best fixed answer and a plain trend rule, on stocks and dates chosen by rule, from the warehouse alone. Spec: [2026-10-12-phase-2e-technical-test-design.md](../specs/2026-10-12-phase-2e-technical-test-design.md).

**Architecture:** the Phase 1k harness (`PitCaseBuilder`, `run_cases`, scorer) reused unchanged except that `run_cases` takes its analyst-to-packet map; a new `athena.evaluation.pit_market` loads bars, actions and the benchmark from the warehouse once, closes the file, and runs the model and the rule on the same cases.

**Tech Stack:** Python, DuckDB, pytest.

## Global Constraints

- Packets are built from bars up to the as-of date only; outcomes only from bars after it.
- Stocks chosen by the rule in the spec (traded-value rank, seed 11); no hand picking.
- The warehouse file is opened only to load, never held while the model runs.
- The calibration file is not changed by this run.

## Tasks

1. **Backfill** the warehouse to 2023-01-02 (`python -m athena.warehouse.build --since 2023-01-02 --statements 0`); `ingest_actions` gets `full=True` so the actions reach back too; test added.
2. **Harness** (`src/athena/evaluation/pit_market.py`, `tests/test_pit_market.py`): `Market`, `choose_stocks`, `rule_trend`, CLI; `run_cases(packet_for=...)`.
3. **Smoke run** on two stocks and two dates; then the full run in the background with a progress watcher.
4. **Report**, calibration rebuild from both logs, TRD section and memory, commit.
