# Phase 2f: does the technical packet carry a signal? (finding, no analyst change)

Follows [2e](2026-10-12-phase-2e-technical-test-design.md), which showed the model neutral on 87 to 91% of cases and no better than a plain rule.

## Question

Is "never bullish" a prompt fault to fix, or is there nothing in the packet to be bullish about?

## Method

The same 40 stocks and month ends as 2e (1,200 cases at 63 days, 1,080 at 126), packets built point in time from the warehouse, outcomes against the NIFTY 50. For each packet feature the share of outperform, inline and underperform outcomes in each bucket. No model call. (A throwaway script; the numbers are below.)

## Result

Overall mix at 63 days: outperform 35%, inline 30%, underperform 35%. At 126 days: 35%, 33%, 33%.

| 63 days | n | outperform | inline | underperform |
|---|---|---|---|---|
| aligned up (the rule's bullish) | 290 | 31% | 34% | 34% |
| aligned down | 151 | 38% | 30% | 32% |
| not aligned | 679 | 35% | 29% | 36% |
| reward/risk at least 2 | 523 | 34% | 31% | 35% |
| reward/risk under 2 | 677 | 35% | 30% | 35% |
| volume ratio over 1 | 558 | 37% | 30% | 33% |
| RSI over 70 | 75 | 31% | 32% | 37% |
| price above 200-day average | 676 | 34% | 30% | 36% |

At 126 days it is the same: every bucket sits within a few points of the overall mix. The stocks that were "aligned up" did no better than the rest, and "aligned down" slightly better, which is the wrong way round for a trend call and inside noise at these sample sizes.

## Reading

- In this sample the packet's features do not separate outcomes, so there is nothing for a better prompt to find. Making the model say bullish more often would only add wrong calls.
- The model's neutral stance is the right answer to a packet with no signal. Its confidence is not: it shows 70 and over on calls no more accurate than the rest.
- The trust figure (accuracy over best fixed accuracy, at most 1) gives 0.95 to an analyst that only matches the most common outcome. Equal to the baseline means no skill, not 95% trust.

## Limits

One window (2024 to 2026), 40 current listings (survivorship; mean excess return is positive in every bucket), overlapping cases, monthly sampling. A different window or longer history could differ.

## Decision

The analyst's prompt and packet are not changed. Open for the user: change the trust figure so that matching the baseline scores near zero.
