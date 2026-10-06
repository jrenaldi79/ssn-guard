# Redaction warnings implementation plan

**Goal:** Redact possible SSNs without rejecting completed Codex tool calls, and warn the model whenever content is redacted.

**Architecture:** Keep the existing detector and Claude output replacement. For Codex, return `continue: false` with sanitized feedback in `stopReason` and a warning in `PostToolUse.additionalContext`. Share warning wording across hooks and shell maskers; shell warnings use stderr so stdout remains machine readable.

**Tech stack:** Python standard library, pytest, PowerShell, POSIX shell.

## Constraints

- Preserve current detection, last-four masking, command exit codes and fail-closed error behavior.
- Leave the supplied UUID unchanged; add regression coverage without relaxing detection.
- Do not claim that Codex preserves structured result shape or sanitizes the value inside nested JavaScript without a live integration check.

## Steps

- [x] Add failing hook tests for text and structured redaction, warning context, clean output, Claude compatibility and the UUID.
- [x] Add failing CLI and Windows runner tests for warnings, clean stdout and preserved exit status; exercise the POSIX wrapper with Git's shell when available.
- [x] Replace Codex's blocking decision with sanitized `stopReason` and `continue: false`; share a count-based warning that acknowledges possible false positives.
- [x] Emit the shared warning on stderr from both shell masking paths, and route POSIX masker stderr to the original tool output descriptor.
- [x] Update README with the new behavior and the live Codex verification procedure.
- [x] Run `python -m pytest plugins/ssn-guard/tests -q`, `python plugins/ssn-guard/tests/test_launcher_portability.py` and `git diff --check`. Inspect the final diff.

Verification: 22 tests and 4 subtests passed; 18 launcher checks passed. Astra's final review found no actionable issues. Live Codex verification remains pending.

## Live Codex verification

The running chat's installed hooks cannot be replaced by unit tests. After refreshing/trusting the plugin, verify a direct tool result, a nested JavaScript call that prints its result, and a nested call accessing structured fields using only invalid synthetic SSN-shaped data. Confirm no rejection, a visible warning, and no original content escaping through JavaScript. This remains an integration limitation if no disposable host session is available.
