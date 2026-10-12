# Phase 2i Kronos Validation Implementation Plan

> **For agentic workers:** executed inline, tests first, one commit. Spec: [2026-10-13-phase-2i-kronos-validation-design.md](../specs/2026-10-13-phase-2i-kronos-validation-design.md).

**Goal:** Measure whether Kronos's forecasts rank Indian stocks by what they then did better than chance and than a momentum ranking, on the 2e stocks and dates.

**Architecture:** three steps with files between them, so PyTorch never enters Athena's environment: (1) `athena.evaluation.kronos_test export` writes each date's bars up to that date; (2) `scripts/kronos_forecast.py`, run with the Kronos environment's Python, writes the predicted returns; (3) `athena.evaluation.kronos_test score` ranks, builds log rows in the 2e format, and prints the report and rank correlations.

**Tech Stack:** Python, DuckDB, pytest; Kronos (MIT, third-party, cloned and reviewed) with PyTorch in `D:\projects\learn-project\kronos\.venv`.

## Global Constraints

- Nothing after the as-of date reaches Kronos; the export is checked by test.
- The Kronos code and its environment live outside this repository; the weights come from Hugging Face as safetensors.
- No Kronos output enters a verdict or the calibration in this phase.

## Tasks

1. **Clone and review Kronos**, create its environment (torch, numpy, pandas, einops, safetensors, huggingface_hub).
2. **Export and scoring** (`kronos_test.py`, `tests/test_kronos_test.py`): `export_row`, `thirds`, `spearman`, `score_rows`, CLI.
3. **Forecast script** (`scripts/kronos_forecast.py`): batches of stocks per date, sampled paths averaged, resumable, fixed seeds.
4. **Smoke run** on one date, then the full run in the background with a progress watcher.
5. **Report**, then the TRD section, memory and commit.
