# Hook launcher portability

The hooks retain the existing SSN guard and masker. Only startup differs.

- POSIX hosts use a built-in shell bootstrap and discover python3 or python.
- Codex Windows hosts use commandWindows to start the native PowerShell launcher.
- PLUGIN_ROOT or CLAUDE_PLUGIN_ROOT locates the package; no user or version path is embedded.
- Windows discovers py/python/python3 on PATH, skips WindowsApps Store aliases,
  and falls back to standard per-user or all-users Python installations.
- Candidates must run Python 3.10+. Missing Python or guard scripts exit 2.
- The Windows launcher captures stdin before probes, preserves Unicode, and
  propagates the guard exit code. It never writes the input to a temporary file.
- Execution policy, sandbox settings and SSN checks are not relaxed.
- The Windows bootstrap is encoded into the manifest to avoid nested quoting.
  Its readable source is scripts/bootstrap_windows.ps1.

Run python tests/test_launcher_portability.py on a Windows host with Python and
Git for Windows to verify startup with harmless inputs. These tests do not use
SSNs or credentials and do not establish a full human-managed SSN preflight.

The installed cache was refreshed locally. Reinstall/update from this source to
preserve the launchers. Changed Codex hook definitions may require fresh trust.
The backup hooks/hooks.json.pre-portability.bak retains the previous definition.
