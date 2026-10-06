# SSN Guard

Maintained source for the SSN Guard plugin for Codex and Claude Code.

The plugin checks prompts, masks command output, and checks tool results. Output
is withheld if command execution or masking fails. Windows commands use the
active PowerShell executable; POSIX commands retain the Bash wrapper.

Detected values in text tool results are redacted to `***-**-1234`, with a
warning that possible SSNs were redacted and false positives are possible.
Shell maskers warn on stderr while keeping stdout usable as JSON or other
machine-readable text. Both streams must be captured to retain the warning.

Codex post-tool hooks use `continue: false` and sanitized `stopReason` feedback
instead of `decision: "block"`. According to the
[Codex hook contract](https://learn.chatgpt.com/docs/hooks), this replaces the
model-visible result without rejecting nested JavaScript tool promises.
Structured results are serialized as text feedback; their original host-level
shape is not guaranteed. Claude Code keeps its existing output replacement.
Prompt checks, PDF/image policies and masking failures retain their current
behavior.

After refreshing and trusting the installed plugin, verify direct tool reads,
nested JavaScript calls that print their result, and nested calls that access
structured fields using invalid synthetic SSN-shaped fixtures. Check that each
redaction has a warning, calls are not rejected, and original content cannot
escape through the nested result. Unit tests verify the emitted hook contract;
they do not establish which value a live Codex nested promise receives.

## Layout

- `plugins/ssn-guard/scripts/`: guard, masker, and portable launchers.
- `plugins/ssn-guard/hooks/hooks.json`: lifecycle hook definitions.
- `plugins/ssn-guard/tests/test_windows_command.py`: harmless wrapper regressions.
- `plugins/ssn-guard/tests/test_launcher_portability.py`: launcher smoke checks.
- `.claude-plugin/marketplace.json`: local marketplace manifest.

## Test on Windows

```powershell
python -m pytest plugins/ssn-guard/tests/test_windows_command.py -q
python plugins/ssn-guard/tests/test_launcher_portability.py
python -m pytest plugins/ssn-guard/tests -q
```

No credentials, live client data, or full SSN fixtures are needed. Original
documentation examples and numeric fixtures are excluded from this maintained
copy. The masker's executable logic is preserved.

## Windows repair

The launcher resolves Python without relying on Windows Store aliases. The
Windows launcher probes candidates as they are discovered and stops on the first
working interpreter; installation-directory scans run only when PATH candidates
fail. It keeps the existing fallback order and caches the resolved executable in
`python-runtime-<selection-hash>.json` under the plugin data directory (or
`~/.ssn-guard`). Each project and interpreter-selection environment has its own
entry, so switching back can reuse a validated selection. The hash includes
PATH, virtual environments and the current project directory; environment
values are not stored in the cache. Missing, corrupt, changed or
older-than-24-hour entries trigger rediscovery. Upgrading from the legacy
`python-runtime.json` performs one normal discovery for each selection. Cache writes
are optional and atomic. Warm launches check Python 3.10+ and execute the guard
in one process, preserving stdin and guard exit codes. Unexpected Python startup
failures become blocking exit code 2. Guard failures are never retried.
The
command wrapper uses the active PowerShell engine, captures both output streams,
masks before emitting, preserves command exit status, and withholds output on
masking failures. Explicit unsupported Windows shells fail closed.

The installed cache and Downloads copy are snapshots. Make future changes here,
test them, commit them, and refresh the installed plugin from this source.
`commandWindows` encodes `scripts/bootstrap_windows.ps1`; regenerate it whenever
that bootstrap changes. This does not bypass PowerShell execution policy.

Sandbox restrictions still apply. A sandboxed Python launcher may report a
runtime path warning; this is separate from the repaired shell parsing failure.
