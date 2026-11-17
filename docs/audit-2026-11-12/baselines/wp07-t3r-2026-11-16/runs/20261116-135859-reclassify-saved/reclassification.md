# Reclassification of saved mutation runs (mutate.py v2 rules, from the saved logs)

## 20261116-131652-mutation-web (vitest; saved baseline: True)

| new outcome / status | count |
|---|---|
| SURVIVED / confirmed | 1 |
| caught / confirmed | 36 |

| mutation | old | new | status | detail |
|---|---|---|---|---|
| ts-implausible-time-accepted | caught | caught | confirmed | 1 assertion failures |
| ts-future-boundary | caught | caught | confirmed | 3 assertion failures |
| ts-future-never-invalid | caught | caught | confirmed | 8 assertion failures |
| ts-receipt-tolerance-wider | caught | caught | confirmed | 2 assertion failures |
| ts-exchange-tolerance-wider | caught | caught | confirmed | 2 assertion failures |
| ts-invalid-book-time-not-flagged | caught | caught | confirmed | 7 assertion failures |
| ts-invalid-trade-time-not-flagged | caught | caught | confirmed | 4 assertion failures |
| ts-server-refusals-ignored | caught | caught | confirmed | 2 assertion failures |
| ts-negative-age-passes | caught | caught | confirmed | 1 assertion failures |
| ts-newest-age-counts-future | caught | caught | confirmed | 1 assertion failures |
| ts-book-fallback-on-fault | caught | caught | confirmed | 1 assertion failures |
| ts-forward-labels-dropped | caught | caught | confirmed | 3 assertion failures |
| ts-forward-inputflags-not-collected | caught | caught | confirmed | 3 assertion failures |
| ts-forward-span-unchecked | caught | caught | confirmed | 4 assertion failures |
| ts-forward-span-boundary | caught | caught | confirmed | 1 assertion failures |
| ts-forward-span-zero | caught | caught | confirmed | 4 assertion failures |
| ts-forward-time-invalid-not-flagged | caught | caught | confirmed | 1 assertion failures |
| ts-forward-unfit-input-ignored | SURVIVED | SURVIVED | confirmed | every test passed |
| ts-absent-side-is-no-atm | caught | caught | confirmed | 3 assertion failures |
| ts-missing-put-not-listed | caught | caught | confirmed | 5 assertion failures |
| ts-no-quote-is-no-book | caught | caught | confirmed | 2 assertion failures |
| ts-absent-side-withholds | caught | caught | confirmed | 3 assertion failures |
| ts-invalid-book-time-labels | caught | caught | confirmed | 3 assertion failures |
| ts-policy-version-stale | caught | caught | confirmed | 5 assertion failures |
| ts-session-recency-ignored | caught | caught | confirmed | 6 assertion failures |
| ts-session-event-lag-ignored | caught | caught | confirmed | 5 assertion failures |
| ts-session-future-receipt-counts | caught | caught | confirmed | 1 assertion failures |
| ts-session-no-event-time-is-replay | caught | caught | confirmed | 1 assertion failures |
| ts-session-clock-is-the-pages | caught | caught | confirmed | 2 assertion failures |
| ts-session-from-trade-time | caught | caught | confirmed | 2 assertion failures |
| ts-trade-time-from-packet-time | caught | caught | confirmed | 4 assertion failures |
| ts-badge-ignores-session | caught | caught | confirmed | 11 assertion failures |
| ts-observe-ignores-receipt | caught | caught | confirmed | 2 assertion failures |
| ts-excluded-not-recorded | caught | caught | confirmed | 1 assertion failures |
| ts-far-from-spot-not-recorded | caught | caught | confirmed | 1 assertion failures |
| ts-atm-not-passed-to-quality | caught | caught | confirmed | 3 assertion failures |
| ts-future-candidate-not-recorded | caught | caught | confirmed | 1 assertion failures |

## 20261116-133324-mutation-web (vitest; saved baseline: True)

| new outcome / status | count |
|---|---|
| caught / confirmed | 6 |

| mutation | old | new | status | detail |
|---|---|---|---|---|
| ts-forward-unfit-input-ignored | caught | caught | confirmed | 1 assertion failures |
| ts-forward-unfit-not-computed | caught | caught | confirmed | 1 assertion failures |
| ts-forward-stale-ignored | caught | caught | confirmed | 1 assertion failures |
| ts-forward-age-unknown-ignored | caught | caught | confirmed | 1 assertion failures |
| ts-forward-time-is-newest | caught | caught | confirmed | 1 assertion failures |
| ts-labels-session-observation-carried | caught | caught | confirmed | 2 assertion failures |

## 20261116-133401-mutation-web (vitest; saved baseline: True)

| new outcome / status | count |
|---|---|
| SURVIVED / confirmed | 1 |
| caught / confirmed | 7 |

| mutation | old | new | status | detail |
|---|---|---|---|---|
| ts-missing-recent-read-as-recent | caught | caught | confirmed | 1 assertion failures |
| ts-old-failures-current | caught | caught | confirmed | 5 assertion failures |
| ts-retained-claim-without-served-answer | caught | caught | confirmed | 4 assertion failures |
| ts-served-reader-not-matched | caught | caught | confirmed | 3 assertion failures |
| ts-answer-before-failure-counts | caught | caught | confirmed | 1 assertion failures |
| ts-reader-key-fallback-removed | caught | caught | confirmed | 1 assertion failures |
| ts-failure-age-not-read | caught | caught | confirmed | 1 assertion failures |
| ts-retained-data-not-described | SURVIVED | SURVIVED | confirmed | every test passed |

## 20261116-133413-mutation-web (vitest; saved baseline: True)

| new outcome / status | count |
|---|---|
| caught / confirmed | 11 |

| mutation | old | new | status | detail |
|---|---|---|---|---|
| ts-object-dates-ignored | caught | caught | confirmed | 3 assertion failures |
| ts-no-date-recorded-hidden | caught | caught | confirmed | 1 assertion failures |
| ts-updating-for-every-reason | caught | caught | confirmed | 4 assertion failures |
| ts-unreadable-said-as-updating | caught | caught | confirmed | 4 assertion failures |
| ts-worst-reason-not-chosen | caught | caught | confirmed | 1 assertion failures |
| ts-failed-publication-hidden | caught | caught | confirmed | 2 assertion failures |
| ts-newer-failed-never-said | caught | caught | confirmed | 3 assertion failures |
| ts-refresh-failure-hidden | caught | caught | confirmed | 10 assertion failures |
| ts-stale-entries-described | caught | caught | confirmed | 3 assertion failures |
| ts-chart-lab-drops-provenance | caught | caught | confirmed | 3 assertion failures |
| ts-fetcher-drops-provenance | caught | caught | confirmed | 1 assertion failures |

## 20261116-133456-mutation-web (vitest; saved baseline: True)

| new outcome / status | count |
|---|---|
| caught / confirmed | 20 |
| pattern_error / confirmed | 6 |

| mutation | old | new | status | detail |
|---|---|---|---|---|
| ts-book-age-from-receipt | pattern_error | pattern_error | confirmed | the pattern occurs 0 times, not once |
| ts-book-age-from-trade | pattern_error | pattern_error | confirmed | the pattern occurs 0 times, not once |
| ts-old-trade-marks-stale | caught | caught | confirmed | 3 assertion failures |
| ts-book-age-boundary | caught | caught | confirmed | 1 assertion failures |
| ts-crossed-not-detected | caught | caught | confirmed | 2 assertion failures |
| ts-wide-not-flagged | caught | caught | confirmed | 3 assertion failures |
| ts-last-trade-used-despite-policy | caught | caught | confirmed | 1 assertion failures |
| ts-sync-not-checked | caught | caught | confirmed | 3 assertion failures |
| ts-sync-boundary | caught | caught | confirmed | 1 assertion failures |
| ts-session-ages-from-now | caught | caught | confirmed | 3 assertion failures |
| ts-session-not-labelled | caught | caught | confirmed | 1 assertion failures |
| ts-every-flag-withholds | caught | caught | confirmed | 13 assertion failures |
| ts-delay-not-flagged | caught | caught | confirmed | 2 assertion failures |
| ts-out-of-order-not-flagged | caught | caught | confirmed | 2 assertion failures |
| ts-forward-time-is-newest-input | pattern_error | pattern_error | confirmed | the pattern occurs 0 times, not once |
| ts-chain-status-always-ok | caught | caught | confirmed | 8 assertion failures |
| ts-policy-version-not-recorded | caught | caught | confirmed | 2 assertion failures |
| ts-stale-future-used | caught | caught | confirmed | 3 assertion failures |
| ts-parity-from-unfiltered-quotes | caught | caught | confirmed | 3 assertion failures |
| ts-withheld-iv-still-computed | caught | caught | confirmed | 5 assertion failures |
| ts-smile-from-unfiltered | caught | caught | confirmed | 1 assertion failures |
| ts-atm-iv-from-unfiltered | caught | caught | confirmed | 1 assertion failures |
| ts-server-clock-ignored | pattern_error | pattern_error | confirmed | the pattern occurs 0 times, not once |
| ts-session-always-live | pattern_error | pattern_error | confirmed | the pattern occurs 0 times, not once |
| ts-book-time-not-mapped | pattern_error | pattern_error | confirmed | the pattern occurs 0 times, not once |
| ts-badge-stays-live | caught | caught | confirmed | 2 assertion failures |

## 20261116-130157-mutation-rust (cargo; saved baseline: False; problems: no baseline log was saved)

| new outcome / status | count |
|---|---|
| SURVIVED / unconfirmed | 1 |
| invalid / unconfirmed | 19 |

| mutation | old | new | status | detail |
|---|---|---|---|---|
| rs-trade-time-from-packet-field | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-sequence-not-read | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-seconds-read-as-ms | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-epoch-floor-removed | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-watermark-is-the-payload-time | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-future-not-refused | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-future-limit-off-by-one | SURVIVED | SURVIVED | unconfirmed (no usable baseline log): rerun | every test passed |
| rs-future-time-kept | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-future-fault-not-recorded | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-same-time-sequence-ignored | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-timeless-sequence-ignored | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-sequence-not-remembered | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-reset-forgets-nothing | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-stream-does-not-reset | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-absent-trade-time-always-kept | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-absent-trade-time-is-packet-time | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-delay-not-measured | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-discard-not-counted | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-duplicate-is-a-change | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-fault-order-unsorted | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |

## 20261116-131205-mutation-rust-r2 (cargo; saved baseline: False; problems: no baseline log was saved)

| new outcome / status | count |
|---|---|
| invalid / unconfirmed | 4 |

| mutation | old | new | status | detail |
|---|---|---|---|---|
| rs-future-limit-off-by-one | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-future-limit-one-wider | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-future-comparison-boundary | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
| rs-future-trade-comparison-boundary | caught | invalid | unconfirmed (no usable baseline log): rerun | build_error: could not compile |
