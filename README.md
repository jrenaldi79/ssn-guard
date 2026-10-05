# SSN Guard

Maintained source for the SSN Guard plugin for Codex and Claude Code.

The plugin checks prompts, masks command output, and checks tool results. Output
is withheld if command execution or masking fails. Windows commands use the
active PowerShell executable; POSIX commands retain the Bash wrapper.

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
```

No credentials, live client data, or full SSN fixtures are needed. Original
documentation examples and numeric fixtures are excluded from this maintained
copy. The masker's executable logic is preserved.

## Windows repair

The launcher resolves Python without relying on Windows Store aliases. The
command wrapper uses the active PowerShell engine, captures both output streams,
masks before emitting, preserves command exit status, and withholds output on
masking failures. Explicit unsupported Windows shells fail closed.

The installed cache and Downloads copy are snapshots. Make future changes here,
test them, commit them, and refresh the installed plugin from this source.
`commandWindows` encodes `scripts/bootstrap_windows.ps1`; regenerate it whenever
that bootstrap changes. This does not bypass PowerShell execution policy.

Sandbox restrictions still apply. A sandboxed Python launcher may report a
runtime path warning; this is separate from the repaired shell parsing failure.
