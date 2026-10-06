# Cached Windows Python runtime

Goal: avoid interpreter discovery and a separate version-probe process on warm hooks.

Store only a resolved absolute Python executable, file fingerprint, interpreter-selection environment hash and timestamp in the per-user plugin data directory. Validate the metadata before use; rediscover for malformed, missing, changed or expired entries. Cache writes are optional and atomic. A small inline Python bootstrap checks Python 3.10+ and compiles and executes the guard in the same process, preserving stdin and exits. Exclude the working directory from bootstrap imports. Normalize unexpected native startup failures to exit code 2. Never retry a guard rejection or masking failure. Keep synchronous hooks and existing PowerShell execution-policy behavior.

- [x] Add real-process tests for warm startup, damaged and unavailable cache, concurrency, stdin and no retry after guard failure; confirm failures before implementation.
- [x] Resolve actual Python paths during cold discovery; add optional cache validation and atomic writes.
- [x] Execute the version check and guard together on warm launches.
- [x] Isolate test and benchmark cache data from the installed plugin; run all regression and launcher checks.
- [x] Interleave baseline, lazy-only and cached launches, validating outputs and selected interpreter; record cold startup separately.
- [x] Review with Astra and document measured results and remaining limits.
- [x] Refresh the globally enabled Codex plugin from this source, compare installed script and hook hashes, and run the installed launcher checks.

Verification: 39 pytest tests and four subtests passed; all 18 standalone launcher checks passed for both the source and installed plugin. Astra approved the final implementation after module-shadowing, native startup failure and interpreter-selection invalidation regressions were added. All seven installed script and hook files match the tested source.

The final timing report uses eight warm samples per case and variant, with rotated and reversed variant ordering. Every invocation validates its exit status, output and actual Python interpreter. Cache-empty startup is recorded separately as one sample per variant. These local measurements do not establish full Codex turn latency or live nested-tool redaction behavior.

Final warm medians on this machine:

| Case | Original launcher | Lazy discovery | Cached launcher | Cached change |
| --- | ---: | ---: | ---: | ---: |
| Prompt | 3182 ms | 2578 ms | 2465 ms | 22.5% faster |
| 100 KB tool result | 3291 ms | 3519 ms | 3452 ms | 4.9% slower |

The large-result case has no demonstrated improvement in this final run. Earlier exploratory runs were faster but are not used as the final performance claim. Cache-empty startup single samples were 3423, 1549 and 1382 ms respectively; a single sample does not establish a cold-start performance distribution. The raw report is stored in the current chat's diagnostics directory as `cached-launcher-final.json`.
