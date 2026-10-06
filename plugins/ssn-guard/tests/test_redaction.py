"""Redaction contract tests using invalid synthetic numbers and public UUIDs."""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import ssn_guard as guard
import ssn_mask as masker

SYNTHETIC = "000" + "-00-" + "1234"  # Invalid SSN, deliberately shape-matched.
MASKED = "***-**-1234"
UUID_JSON = '"PhoneNumberKey": "879ce73d-476d-43b1-a344-372599986a11",'


@pytest.fixture(autouse=True)
def stable_environment(monkeypatch):
    monkeypatch.setenv("SSN_GUARD_AUDIT", "0")
    monkeypatch.delenv("SSN_GUARD_ALLOW", raising=False)


def post(response, platform="codex"):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = guard.on_post_tool(
            {"tool_name": "mcp__fixture__read", "tool_response": response}, platform
        )
    assert code == 0
    return json.loads(output.getvalue()) if output.getvalue() else None


@pytest.mark.parametrize("response", [
    "before " + SYNTHETIC + " after",
    {"content": [{"type": "text", "text": SYNTHETIC}], "ok": True},
])
def test_codex_redacts_without_block_decision(response):
    result = post(response)
    assert "decision" not in result
    assert result["continue"] is False
    assert MASKED in result["stopReason"]
    assert SYNTHETIC not in json.dumps(result)
    context = result["hookSpecificOutput"]
    assert context["hookEventName"] == "PostToolUse"
    assert "redacted 1" in context["additionalContext"]
    assert "False positives" in context["additionalContext"]


def test_codex_serialized_feedback_preserves_non_sensitive_fields():
    result = post({"sensitive": SYNTHETIC, "ok": True, "items": ["unchanged"]})
    # The feedback contains JSON, but does not promise host-level result shape.
    payload = json.loads(result["stopReason"].split("\n\n", 1)[1])
    assert payload == {"sensitive": MASKED, "ok": True, "items": ["unchanged"]}


def test_claude_preserves_result_shape_and_warns():
    result = post({"text": SYNTHETIC, "ok": True}, "claude")
    context = result["hookSpecificOutput"]
    assert context["updatedToolOutput"] == {"text": MASKED, "ok": True}
    assert "False positives" in context["additionalContext"]


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_clean_results_and_uuid_pass_unchanged(platform):
    assert post(UUID_JSON, platform) is None
    assert post({"PhoneNumberKey": json.loads("{" + UUID_JSON.rstrip(",") + "}")["PhoneNumberKey"]}, platform) is None
    assert masker.mask_text(UUID_JSON) == (UUID_JSON, 0)


def test_masker_cli_warns_without_corrupting_json_stdout():
    raw = json.dumps({"value": SYNTHETIC})
    result = subprocess.run([sys.executable, str(SCRIPTS / "ssn_mask.py")],
                            input=raw, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"value": MASKED}
    assert "redacted 1" in result.stderr
    assert "False positives" in result.stderr
    assert SYNTHETIC not in result.stdout + result.stderr


def test_masker_cli_clean_output_has_no_warning():
    result = subprocess.run([sys.executable, str(SCRIPTS / "ssn_mask.py")],
                            input=UUID_JSON, capture_output=True, text=True, timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (0, UUID_JSON, "")


def git_shell():
    found = shutil.which("sh")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles", ""), str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs")):
        candidate = Path(base) / "Git/usr/bin/sh.exe"
        if candidate.is_file():
            return str(candidate)
    return None


@pytest.mark.parametrize("ending", ["false", "exit 7"])
def test_posix_wrapper_redacts_and_warns_on_failure(ending):
    shell = git_shell()
    if not shell:
        pytest.skip("POSIX shell unavailable")
    temp_dir = Path(tempfile.gettempdir()).as_posix()
    with patch.object(guard.os, "name", "posix"), \
         patch.object(guard.tempfile, "gettempdir", return_value=temp_dir):
        command = guard.wrap_command("printf '%s' '" + SYNTHETIC + "'\n" + ending)
    shell_env = dict(os.environ)
    shell_env["PATH"] = str(Path(shell).parent) + os.pathsep + shell_env.get("PATH", "")
    result = subprocess.run([shell, "-c", command], capture_output=True,
                            text=True, timeout=15, env=shell_env)
    assert result.returncode == (7 if ending == "exit 7" else 1)
    assert MASKED in result.stdout
    assert "redacted 1" in result.stdout + result.stderr
    assert SYNTHETIC not in result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows wrapper")
@pytest.mark.parametrize("ending", ["", "; exit 7"])
def test_windows_wrapper_redacts_and_warns(ending):
    import windows_command
    shell = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    command = windows_command.wrap_windows_command("Write-Output '" + SYNTHETIC + "'" + ending)
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == (7 if ending else 0)
    assert MASKED in result.stdout
    assert "redacted 1" in result.stdout + result.stderr
    assert SYNTHETIC not in result.stdout + result.stderr
