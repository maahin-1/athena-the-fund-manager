# Phase 2i: does Kronos forecast Indian stocks better than a naive rule?

Parent: TRD open decision 3 (Kronos validation plan), [2e](2026-10-12-phase-2e-technical-test-design.md) (the harness and cases reused here).

## Question

Do Kronos's forecasts of a stock's next 63 and 126 trading days rank Indian stocks by what they then did against the NIFTY 50 better than (a) chance and (b) plain past momentum? Kronos may influence no verdict until it does.

## What Kronos is

A pretrained candlestick model (Kronos-small, 24.7M parameters, 512-bar context, MIT licence) that reads open, high, low, close and volume and samples a future path. Third-party code, cloned to `D:\projects\learn-project\kronos`, reviewed (model code makes no network calls and loads no pickles; weights are safetensors from `NeoQuasar/Kronos-small` on Hugging Face), run in its own virtual environment with PyTorch, never in Athena's. Training data is mostly crypto and global exchanges, with its cut-off unstated, so the later cases (2025 on) are the cleaner ones.

## Method

- **Cases.** The 2e cases: the same 40 rule-chosen stocks and month ends from 2024-01-31 to 2026-07-31, packets and outcomes from the warehouse (adjusted bars up to the date; outcome from later bars against the NIFTY 50).
- **Input.** The last `lookback` adjusted bars up to the date (open, high, low, close, volume), `lookback` = 400, or the shortest history among the stocks on that date when that is less (the earliest dates have about 253). Future timestamps are the next business days. Nothing after the date reaches Kronos.
- **Forecast.** One path of 126 steps per stock and date, 5 sampled paths averaged (`sample_count` 5, temperature 1.0, top-p 0.9), with a fixed random seed per date. Predicted return at 63 and 126 steps is predicted close over the last close, less one. 252 days is not forecast (the model is not meant for it and the cost grows).
- **From forecast to call.** Kronos forecasts a stock, not its excess over the index, so on each date the stocks are ranked by predicted return: top third bullish, bottom third bearish, the rest neutral. The outcome labels are about a third each, so a ranking is the matching question. Confidence is 0.
- **Baselines on the same cases.** `rank_momentum`: the same ranking by the stock's own past 63 or 126-day return. `rule_trend` (2e) is read from the 2e log. The best fixed answer comes from the scorer.
- **Scoring.** The 2e scorer for accuracy and directional accuracy, plus the rank correlation (Spearman) between predicted return and realised excess return on each date, its mean over dates, and the share of dates it is positive. Dates overlap in time and the stocks share market moves, so no significance beyond that is claimed.
- **Seed and repeats.** Fixed seeds; the run is repeated once on a different seed for the 2025 to 2026 dates to show how much the sampling alone moves the result.

## Rules

1. Forecasts are made in the Kronos environment from an export of bars up to each date; the export is built by Athena and holds no later bar.
2. The results are described as measured on these stocks and dates, not as a property of the model.
3. Nothing from Kronos enters a verdict or the calibration in this phase.

## Not in 2i

Other Kronos sizes or fine-tuning, forecasting the index, bonds or fund prices, 252-day forecasts, and using Kronos inside any analyst.
