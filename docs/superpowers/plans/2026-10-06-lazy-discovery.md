# Lazy Windows Python discovery

Goal: stop interpreter discovery as soon as a usable candidate is found, preserving priority, Python 3.10+ checks, stdin, quoting, execution policy and fail-closed exits.

- [x] Add regressions where later discovery paths fail but an earlier candidate works; run them against the original launcher and confirm failure.
- [x] Extract candidate validation and probe PATH candidates immediately, then per-user installations, then system installations only as needed.
- [x] Run the regression suite and launcher smoke checks.
- [x] Compare original and candidate launchers with interleaved runs, checking output, return codes, stderr and the selected interpreter. Keep raw timings outside the repo.
- [x] Review the final diff and report the measured change. Do not refresh installed hooks or introduce interpreter caching in this step.

Validation: 24 tests and 4 subtests passed; 18 launcher smoke checks passed. Astra reviewed candidate priority, stdin, version checks and failure behavior. Its portability correction to the test setup was applied and both discovery tests passed again.

Eight samples per variant per case, with alternating execution order, measured prompt medians of 1973.43 ms (baseline) and 1648.08 ms (lazy), and 100 KB result medians of 1961.67 ms and 1811.40 ms. Outputs, exits and stderr were validated, and both variants selected Python 3.13 from the same executable. These are synthetic local benchmark results, not live host end-to-end timings.
