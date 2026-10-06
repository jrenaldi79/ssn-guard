"""Windows wrapper regressions using harmless output and mock masking."""
import base64
import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import windows_command as runner
import ssn_guard as guard


@unittest.skipUnless(os.name == "nt", "Windows wrapper")
class WindowsCommandTests(unittest.TestCase):
    def invoke(self, command, shell):
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command",
             runner.wrap_windows_command(command)],
            capture_output=True, text=True, encoding="utf-8", timeout=35,
        )
        return result

    def shells(self):
        windows = str(Path(os.environ["SystemRoot"]) /
                      "System32/WindowsPowerShell/v1.0/powershell.exe")
        return list(dict.fromkeys([windows, shutil.which("pwsh") or windows]))

    def test_location_in_selected_powershell(self):
        for shell in self.shells():
            with self.subTest(shell=shell):
                result = self.invoke("Get-Location", shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(str(Path.cwd()), result.stdout)

    def test_output_unicode_and_native_failure_status(self):
        for shell in self.shells():
            with self.subTest(shell=shell):
                result = self.invoke(
                    "Write-Output 'harmless caf\u00e9'; "
                    "[Console]::Error.WriteLine('harmless stderr'); cmd.exe /c exit 7",
                    shell,
                )
                self.assertEqual(result.returncode, 7)
                self.assertIn("harmless caf\u00e9", result.stdout)
                self.assertIn("harmless stderr", result.stdout)

    def test_explicit_exit(self):
        result = self.invoke("Write-Output 'before exit'; exit 9", self.shells()[0])
        self.assertEqual(result.returncode, 9)
        self.assertIn("before exit", result.stdout)

    def test_cmdlet_failure_status(self):
        result = self.invoke("Write-Error 'harmless error'", self.shells()[0])
        self.assertNotEqual(result.returncode, 0)

    def test_pretool_preserves_options_and_selects_wrapper(self):
        data = {"tool_name": "Bash", "tool_input": {
            "command": "Get-Location", "shell": "powershell", "login": False}}
        out = io.StringIO()
        with patch.object(guard, "cleanup_stale_temp_files"), contextlib.redirect_stdout(out):
            self.assertEqual(guard.on_pre_tool(data, "codex"), 0)
        import json
        updated = json.loads(out.getvalue())["hookSpecificOutput"]["updatedInput"]
        self.assertFalse(updated["login"])
        self.assertEqual(updated["shell"], "powershell")
        self.assertIn("windows_command.py", updated["command"])
        self.assertNotIn("mktemp", updated["command"])

    def test_unsupported_shell_fails_closed(self):
        with self.assertRaises(ValueError):
            guard.wrap_command("Get-Location", "cmd.exe")

    def test_quotes_in_python_path(self):
        with patch.object(runner.sys, "executable", "C:/folder's name/python.exe"):
            wrapped = runner.wrap_windows_command("Write-Output 'hello'")
        self.assertIn("'C:/folder''s name/python.exe'", wrapped)


class MaskingBoundaryTests(unittest.TestCase):
    def run_mock(self, mask, child=None):
        args = ["runner", base64.b64encode(b"harmless command").decode(),
                "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", args), patch.object(runner, "mask_text", mask), \
             patch.object(runner.subprocess, "run", return_value=child), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = runner.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_masks_before_emitting_and_preserves_status(self):
        # A mock marker proves the boundary without creating any SSN.
        child = subprocess.CompletedProcess([], 7, stdout=b"private-marker")
        from unittest.mock import Mock
        mask = Mock(return_value=("[masked]", 1))
        code, out, err = self.run_mock(mask, child)
        self.assertEqual((code, out), (7, "[masked]"))
        self.assertIn("redacted 1", err)
        self.assertIn("False positives", err)
        self.assertNotIn("private-marker", err)
        mask.assert_called_once_with("private-marker")

    def test_mask_failure_withholds_all_output(self):
        def broken(_):
            raise RuntimeError("private-marker")
        child = subprocess.CompletedProcess([], 0, stdout=b"private-marker")
        code, out, err = self.run_mock(broken, child)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("output withheld", err)
        self.assertNotIn("private-marker", err)

    def test_spawn_failure_withholds_exception(self):
        out, err = io.StringIO(), io.StringIO()
        args = ["runner", base64.b64encode(b"harmless").decode(), "pwsh.exe"]
        with patch.object(sys, "argv", args), \
             patch.object(runner.subprocess, "run", side_effect=OSError("private-marker")), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(runner.main(), 2)
        self.assertEqual(out.getvalue(), "")
        self.assertNotIn("private-marker", err.getvalue())


if __name__ == "__main__":
    unittest.main()
