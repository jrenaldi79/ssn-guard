"""Working Python candidates must not depend on later discovery paths."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
@pytest.mark.parametrize("source", ["path", "user_install"])
def test_working_candidate_skips_later_discovery(tmp_path, source):
    package = tmp_path / "package with spaces"
    scripts = package / "scripts"
    scripts.mkdir(parents=True)
    local_data = tmp_path / "local app data"
    (local_data / "Programs/Python").mkdir(parents=True)
    shutil.copyfile(SCRIPTS / "launch_windows.ps1", scripts / "launch_windows.ps1")
    (scripts / "ssn_guard.py").write_text(
        "import json,sys\ndata=json.load(sys.stdin)\n"
        "print(json.dumps({'event':data,'python':sys.executable}))\n", encoding="utf-8"
    )
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    python_dir = str(Path(sys.executable).parent)
    # Discovery functions model hostile/unavailable *later* locations. The
    # actual Python probe and guard process still run, including stdin handling.
    setup = "$ErrorActionPreference = 'Stop'\n$ProgressPreference = 'SilentlyContinue'\n"
    if source == "path":
        setup += (
            "function Get-Command { param($Name, $CommandType, [switch]$All, $ErrorAction)\n"
            " if ($Name -eq 'py.exe') { return }\n"
            " if ($Name -eq 'python.exe') { [pscustomobject]@{Source=" + quote(sys.executable) + "}; return }\n"
            " throw 'later PATH search must not run'\n}\n"
            "function Get-ChildItem { throw 'installation search must not run' }\n"
        )
    else:
        setup += (
            "function Get-Command { return }\n"
            "function Get-ChildItem { param($LiteralPath, $Path, [switch]$Directory, $ErrorAction)\n"
            " if ($LiteralPath) { [pscustomobject]@{FullName=" + quote(python_dir) + "}; return }\n"
            " throw 'later system install search must not run'\n}\n"
        )
    setup += "& " + quote(scripts / "launch_windows.ps1") + "\nexit $LASTEXITCODE"
    encoded = base64.b64encode(setup.encode("utf-16le")).decode()
    shell = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    payload = {"hook_event_name": "PostToolUse", "tool_response": "Harmless café"}
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
                            env=dict(os.environ, PLUGIN_ROOT=str(package), LOCALAPPDATA=str(local_data),
                                     PLUGIN_DATA=str(tmp_path / "data"), CLAUDE_PLUGIN_DATA=str(tmp_path / "data")), timeout=20)
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["event"] == payload
    assert Path(response["python"]).resolve() == Path(sys.executable).resolve()
