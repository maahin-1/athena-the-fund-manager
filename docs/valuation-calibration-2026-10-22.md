# Valuation calibration study — 22 Oct 2026

Written for the owner. It records what was tried on the valuation model, what the data can and cannot tell us, and what to do about each limit. Code: `src/athena/valuation/calibrate.py`, `study.py`; raw results in `.athena/reports/calibration-study-*.json`.

## What was done

The model was run 21 ways across the stored universe (one change at a time against the live rules, then three packages), at seven past dates (30 Dec 2022 to 31 Dec 2025) on only the statements filed by each date, and scored on the 6- and 12-month price return that followed. Four dates were the development set and three the holdout. The same score was taken for earnings yield and book-to-price, so the model has to beat a one-line value factor to justify its weight.

## 1. A data defect found on the way (fixed)

Stored share counts were wrong for most of the universe: 1,451 of 2,334 changed. Causes: the count was overwritten by whichever filing was read last (so pre-bonus and pre-split counts survived), and some filings state paid-up capital at the wrong scale (one is 100,000 times too large). The counts are now chosen from every filing's own record, checked against that filing's profit over EPS (TRD 2.53). This also corrects market cap, P/E and P/B on the Fundamentals page. A copy of the old store is at `.athena/fundamentals.before-shares.duckdb`.

It made the model's gap to the market larger, not smaller: the old counts had overstated value per share.

## 2. The data cannot choose between rules

| 6-month rank score (IC), all names ≥ ₹500 cr | development | holdout | all dates |
|---|---|---|---|
| Model, live rules | +0.027 | +0.008 | +0.019 |
| Earnings yield | −0.007 | +0.056 | +0.020 |
| Book to price | +0.045 | −0.007 | +0.023 |

At 12 months the model scores +0.06 and earnings yield +0.05 (overlapping windows, so the t-statistics there are overstated). Every variant sits inside that noise: the best and worst differ by less than their own standard error. Over 2023–2026 in India, none of the value signals, including ours, ranked later returns reliably. So nothing was tuned to the data, and the study should not be read as proving or disproving any assumption.

Variants that made the model kinder to quality companies (margins persist, excess returns last) scored no better, and their top-minus-bottom spread was slightly negative. That is not significant either.

## 3. How far the price sits above the model, and why

Today, with corrected counts: median price is 2.3 times the base value, market-cap weighted 1.9 times. Effect of each change on the median value (one date, 1,661 companies):

| Change | Value effect (median) | Rank agreement with live rules |
|---|---|---|
| Live rules | — | 1.00 |
| No rupee conversion (dollar rates used as rupee rates) | +33% | 0.96 |
| Equity premium = mature market only | +13% | 0.98 |
| Equity premium = mature + half the country premium | +6% | 1.00 |
| Rupee inflation 5% instead of 4% | −13% | 0.99 |
| Industry betas shrunk a third toward the average | −1% | 1.00 |
| Growth from company only / industry only | ≈0 | 0.98–0.99 |
| Margin held at the company's own / moved to the industry's | ≈0 | 0.84–0.86 |
| Excess returns last halfway / fully (cap 30% return on capital) | median price/base 2.28 → 2.12 / 2.02 | 0.98 |
| Package: half country premium, Blume betas, margins persist, half the excess lasts, lenders blend, minorities at market | +23% | 0.91 |

Reading it:

- The equity premium is not the cause. Setting the near-term premium to zero still leaves the median price above the model's value. The gap sits in the ten-year growth and in the terminal rule that a company's return on capital falls to its cost of capital after year 10.
- Changing the rules shifts the level but hardly changes the ordering (rank agreement ≥ 0.9, except the margin rules). So the Screen's ranking is not hostage to these choices; the displayed value is.
- Banks: the three large lenders come out at 0.63–0.68 times book against about 1.5–2.3 in the market. The cost of equity (about 15.5% in rupees) is above their return on equity. Moving the premium or beta narrows it but does not close it.

## 4. What the price implies instead

The single discount rate at which the base-case cash flows are worth today's price:

| Company | Price | Base | With half the excess returns lasting | Rate the price implies | Required (model) |
|---|---|---|---|---|---|
| TCS | 2,529 | 1,692 | 2,240 | 9.5% | 12.2% |
| Infosys | 1,168 | 917 | 1,217 | 10.2% | 12.2% |
| Hindustan Unilever | 1,850 | 530 | 670 | 7.9% | 10.6% |
| Asian Paints | 2,319 | 462 | 558 | 7.6% | 11.5% |
| Titan | 4,381 | 750 | 1,166 | 7.8% | 10.5% |
| ITC | 262 | 339 | 468 | 11.7% | 8.1% |
| Maruti | 11,200 | 4,890 | 4,890 | 9.0% | 14.8% |

Large Indian companies are priced to return about 8–10% a year if the model's cash flows come true, against a rupee risk-free rate near 7%. That is a statement that does not depend on which equity premium is right. A rupee risk-free rate of 6.98% and an implied 7.6–10% is a premium of roughly 1–3 points.

## 5. What to do about each limit

| Limit | Handling | Status |
|---|---|---|
| Wrong share counts | Chosen from filings, checked against profit/EPS | Done |
| Equity premium, beta, rupee conversion | Keep Damodaran's method as the strict case; do not tune to price. Make each an owner-editable dated input with a visible sensitivity line; show the implied return beside the required one | Done: implied return and a sensitivity block on the Valuation section; the inputs stay as dated, editable reference inputs |
| Quality companies valued at 8× EBIT | Put "excess returns last halfway" in the bull case only, and only for a company whose return on capital beat its cost of capital by 5 points in each of the last three years | Done: bull case only, gated on three years (TRD 2.55) |
| Banks below book | Compute return on equity on average equity (a merger or raise depresses it on closing equity); flag a year with equity growth over 25% | Done: average-equity return on equity (HDFC Bank 13.4% to 14.0%: the model's cost of equity against a 14% return is the real driver) |
| Reliance, L&T, conglomerates benchmarked as one industry | NSE filings carry segment revenue and profit; parse them, map each segment to an industry (rules, then owner-editable), weight the benchmarks by segment; value a financial arm by book separately | Done: segment parsing, business-mix benchmarks, financial-arm carve-out (TRD 2.56); approximate |
| Minorities, investments at book | Minorities at book × industry P/B (no effect on the median in the study); listed investments at market need a holdings table | Open: minorities at book x price to book is a policy switch, off; listed investments at market need a holdings table |
| Leases, employee options | Lease liabilities are not in the stored lines (parse them from the filings); option count is an owner input, default 0 and flagged | Phase 5 |
| Tax 25.17%, inflation 4% and 2% | Owner to confirm; the inflation gap is the biggest single lever | Owner |
| No way to validate | Record each month's range as a point-in-time ledger and score it after 12 and 24 months (needs approval to schedule) | Done: ledger and first snapshot 2026-10-22; the monthly scheduled task is saved in scripts/ and needs the owner to register it (TRD 2.57) |

## 6. What this study cannot say

Raw closes were adjusted by listed splits and bonuses only; dividends are left out; companies no longer listed are absent; a restated period replaces the original in the store, so some early dates are thin (30 Dec 2022 values only 86 companies for want of balance sheets); industry tables are the 2026 ones and market inputs are today's at every date. One regime, three and a half years.
