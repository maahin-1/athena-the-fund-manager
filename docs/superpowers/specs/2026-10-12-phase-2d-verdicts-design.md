# Phase 2d: verdicts on demand and for changed stocks

Parent: [market warehouse design](2026-10-12-market-warehouse-design.md), stage 2d. Decided there: charts, screens and tables need no model call; a model verdict is made on demand or for stocks whose data changed; the confidence number is dampened until it has been calibrated.

## What the person gets

- On the Market screen, click a stock: below the chart, its latest stored verdict (band, shown conviction, each analyst's signal, age) or "no verdict yet".
- A button "Get a fresh verdict" runs the analysts on that stock's warehouse packets (about 30 s) and stores the result.
- A stored verdict says plainly when it is out of date and why: new results since, or a large price move since.
- A command that refreshes only stocks that already have a verdict and have changed, within a budget. It is not scheduled.

## Rules (all constants named in code, none tuned yet)

1. **Inputs are the warehouse packets.** The analysts read the stored technical and fundamentals packets, the same ones the screen is built from. No per-request download. The news analyst has no warehouse source and is listed as not run.
2. **A verdict is stored with what it was made from**: data day, close, latest annual period and latest quarter of the statements, the analysts' raw outputs, the blend before dampening, the dampened result, the calibration used, the model name. Every verdict is kept (history), the latest is shown.
3. **Changed** means, since the stored verdict: (a) the statements' latest annual period or latest quarter is newer; or (b) the adjusted close moved by 12% or more from the close the verdict used; or (c) the price data day is more than 30 days past the verdict's. Nothing else triggers a refresh. No verdict is made for a stock that has none unless asked.
4. **Dampening.** Until calibrated, the conviction shown is `min(raw, 50)` times the mean trust of the analysts that took part. Trust of an analyst is its measured accuracy divided by the accuracy of the best fixed answer, averaged over the horizons measured (Phase 1k log), capped at 1; an analyst with no measurement has trust 0.5. A verdict is `calibrated` only when every participating analyst has at least 300 measured cases per horizon and beats the best fixed answer by 5 points at each; today none does, so every verdict says "uncalibrated". The raw conviction is kept beside the shown one. The verdict band is not changed by dampening.
5. **Measured, not guessed.** The calibration is a JSON file built from the point-in-time run logs by a command; the verdict code only reads it. A missing file means every analyst is unmeasured (trust 0.5).
6. **Failure is visible.** An analyst that fails or has too little data is listed as skipped with the reason. When no analyst forms a view, or the stock has no price packet, no verdict is stored and the reason is raised; the earlier verdict stands. Never a silent default.
7. **The model's key is read only by the existing router**; tests use a fake client.

## Not in 2d

- Switching the Analyse tab to warehouse packets.
- Testing verdict quality from the database. The statements are as-fetched today, not as they were on past dates, so a historical test of the fundamentals analysts from the warehouse would look ahead. Only the technical analyst can be tested cleanly from stored prices; that is stage 2e.
- Scheduling, and the nightly model budget beyond the explicit `--changed N`.
