# Clean output and project cache evaluation

> **For agentic workers:** Use superpowers:subagent-driven-development to implement the two independent tasks in parallel, as authorized by the user.

**Goal:** Retain only optimizations that demonstrate at least 50 ms of useful savings, as clarified by the user during measurement.

**Final architecture:** Name each optional Python runtime cache file using the existing SHA256 interpreter-selection key. Preserve the record validation, 24-hour expiry, atomic writes, guard failure handling and warnings. Legacy single-entry caches are ignored; the first launch creates the keyed entry through normal discovery. Deferred clean-result serialization was prototyped, measured and reverted because it did not meet the user's threshold.

**Tech stack:** Python 3.10+, Windows PowerShell, pytest, existing plugin hooks.

## Task 1: Clean structured output — evaluated and reverted

Files: `plugins/ssn-guard/scripts/ssn_guard.py`, `plugins/ssn-guard/tests/test_redaction.py`.

- [x] Add a clean structured-result test that makes JSON serialization raise and expects silent success.
- [x] Run the test and confirm it fails because the handler serializes the clean result.
- [x] Preserve masking, check the count, then serialize only structured results containing detections.
- [x] Run the redaction contract tests, including structured redactions and warnings.

Direct handler timing showed savings of 0.9 ms for approximately 110 KB and 8.2 ms for approximately 1.1 MB of clean structured output. Both the production change and its optimization-specific regression were removed. The final guard exactly matches the before-pass snapshot; all 12 existing redaction tests passed after restoration.

## Task 2: Reusable project interpreter cache

Files: `plugins/ssn-guard/scripts/launch_windows.ps1`, `plugins/ssn-guard/tests/test_python_cache.py`, `README.md`.

- [x] Add actual-process A/B/A project and environment switching tests; forbid discovery after both selections have been primed.
- [x] Confirm those tests fail with the single-entry cache.
- [x] Compute the existing selection key before the cache path; use `python-runtime-<key>.json` in the existing plugin data directory.
- [x] Update cache test path helpers and keep malformed, missing, changed, expired, unavailable and concurrent-cache coverage.
- [x] Document the keyed filenames and one-time rediscovery after upgrading from the legacy cache.

## Verification and installation

- [x] Run the full pytest suite and standalone launcher smoke checks.
- [x] Compare before/after using synthetic structured output and interleaved projects; validate outputs and selected interpreters.
- [x] Review the changes with Astra.
- [x] Refresh the globally enabled plugin, compare installed/source hashes and run installed launcher checks.

Benchmarks will report local measurements without promising lower latency for every full Codex turn. Existing uncommitted changes are preserved; this pass does not publish the branch.

## Results and retention decision

| Local project-switching run | Samples per variant | Previous median | Keyed-cache median | Median difference |
| --- | ---: | ---: | ---: | ---: |
| Initial comparison | 24 | 2458 ms | 2040 ms | 418 ms faster |
| Confirmation with interleaved warm control | 16 | 2368 ms | 2011 ms | 357 ms faster |

Keep the keyed cache: both switching runs exceeded 50 ms, and the existing tests establish that warmed selections avoid rediscovery. The confirmation validated 52 successful launches, the same actual interpreter and two unchanged primed entries. Staying in one project showed no reliable improvement. Timing noise was substantial; only 10 of 16 switching pairs improved in confirmation. A descriptive per-round adjustment against warm-control timing still showed a median saving of 316 ms; this is not a guaranteed per-launch saving or a confidence interval.

The original full suite passed 44 tests and four subtests while both prototypes were present. All 18 source launcher checks passed. After removing the low-value serialization prototype, the final 12 redaction tests passed; the retained cache implementation was unchanged from the full passing suite. One earlier cache-only run encountered an existing 20-second subprocess timeout; that case passed on retry and in the full suite without altering timeouts or production code.

Raw reports are in the current chat's diagnostics directory: `clean-project-cache-timings.json` and `project-cache-confirmation-final.json`. The earlier `project-cache-confirmation.json` was interrupted and is excluded from conclusions. All seven installed script and hook hashes match the retained source after refreshing the global plugin.

The refreshed installed plugin passed all 18 launcher smoke checks.
