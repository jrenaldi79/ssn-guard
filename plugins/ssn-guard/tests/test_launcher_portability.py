"""Harmless launch and fail-closed tests; no SSNs, credentials or live data."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
import sys
import base64

STAGE = Path(__file__).resolve().parents[1] / "scripts"
PLUGIN = Path(__file__).resolve().parents[1]
POWERSHELL = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
SH = shutil.which("sh") or next((str(Path(base) / "Git/usr/bin/sh.exe") for base in (str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"), os.environ.get("ProgramFiles", "")) if (Path(base) / "Git/usr/bin/sh.exe").is_file()), "")
EVENTS = {
    'UserPromptSubmit': {'prompt': 'Harmless documentation smoke test.'},
    'PreToolUse': {'tool_name': 'Read', 'tool_input': {}},
    'PostToolUse': {'tool_name': 'synthetic_tool', 'tool_response': 'Harmless synthetic output.'},
}
WINDOWS_COMMAND = 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + base64.b64encode((STAGE / 'bootstrap_windows.ps1').read_text(encoding='utf-8').encode('utf-16le')).decode('ascii')
POSIX_COMMAND = "ssn_guard_root=\"${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}\"; if [ -r \"$ssn_guard_root/scripts/launch_posix.sh\" ]; then . \"$ssn_guard_root/scripts/launch_posix.sh\"; else echo 'ssn-guard: launcher unavailable; blocked for safety' >&2; exit 2; fi"

def run(command, payload, env):
    return subprocess.run(command, input=payload, capture_output=True, text=True, encoding='utf-8', env=env, timeout=35)

def main():
    windows = STAGE / 'launch_windows.ps1'
    posix = STAGE / 'launch_posix.sh'
    assert windows.is_file() and posix.is_file(), 'launchers not implemented yet'
    fixture = Path(tempfile.gettempdir()) / ('ssn-launch-check-' + uuid.uuid4().hex)
    fixture.mkdir()
    try:
        package = fixture / 'package with spaces'
        scripts = package / 'scripts'
        scripts.mkdir(parents=True)
        for name in ('ssn_guard.py', 'ssn_mask.py'):
            shutil.copyfile(PLUGIN / 'scripts' / name, scripts / name)
        for name in ('launch_windows.ps1', 'launch_posix.sh'):
            shutil.copyfile(STAGE / name, scripts / name)
        env = dict(os.environ, PLUGIN_ROOT=str(package), CLAUDE_PLUGIN_ROOT=str(package))
        commands = [
            [POWERSHELL, '-NoProfile', '-NonInteractive', '-File', str(windows)],
            [SH, str(posix)],
        ]
        checks = 0
        for command in commands:
            for event, fields in EVENTS.items():
                data = dict(hook_event_name=event, turn_id='synthetic', **fields)
                result = run(command, json.dumps(data), env)
                assert result.returncode == 0 and not result.stderr.strip(), 'benign event failed'
                checks += 1
            result = run(command, '{', env)
            assert result.returncode == 2 and 'blocked for safety' in result.stderr, 'guard blocking code was not preserved'
            checks += 1
        windows_path = str(Path(os.environ['SystemRoot']) / 'System32') + ';' + os.environ['SystemRoot']
        fallback_env = dict(env, PATH=windows_path, LOCALAPPDATA=os.environ['LOCALAPPDATA'])
        result = run(commands[0], json.dumps(dict(hook_event_name='PostToolUse', turn_id='synthetic', tool_name='synthetic_tool', tool_response='Unicode check: café')), fallback_env)
        assert result.returncode == 0 and not result.stderr.strip(), 'version-independent Python fallback failed'
        checks += 1
        missing_env = dict(env, PLUGIN_ROOT=str(fixture / 'missing'), CLAUDE_PLUGIN_ROOT=str(fixture / 'missing'))
        for command in commands:
            result = run(command, '{}', missing_env)
            assert result.returncode == 2 and 'blocked for safety' in result.stderr, 'missing guard did not fail closed'
            checks += 1
        unavailable_env = dict(env, PATH=windows_path, LOCALAPPDATA=str(fixture / 'empty'), ProgramFiles=str(fixture / 'empty'), **{'ProgramFiles(x86)': str(fixture / 'empty')})
        result = run(commands[0], '{}', unavailable_env)
        assert result.returncode == 2 and 'Python' in result.stderr, 'missing Python did not fail closed'
        checks += 1
        # Test exact manifest commands, not just the launcher files.
        manifest_commands = [
            str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe") + " /d /s /c " + WINDOWS_COMMAND,
            [SH, '-c', POSIX_COMMAND],
        ]
        for position, command in enumerate(manifest_commands):
            result = run(command, json.dumps(dict(hook_event_name='UserPromptSubmit', turn_id='synthetic', prompt='Harmless smoke test.')), env)
            assert result.returncode == 0 and not result.stderr.strip(), ('manifest command failed', position, result.returncode, result.stderr)
            checks += 1
            result = run(command, '{', env)
            assert result.returncode == 2 and 'blocked for safety' in result.stderr, 'manifest command lost blocking exit code'
            checks += 1
        # Prove probing has not swallowed stdin and Unicode survives the launcher.
        (scripts / 'ssn_guard.py').write_text('import json, sys\ndata=json.load(sys.stdin)\nprint(json.dumps(data))\n', encoding='utf-8')
        payload = json.dumps(dict(hook_event_name='PostToolUse', turn_id='synthetic', tool_response='café'))
        for command in manifest_commands:
            result = run(command, payload, env)
            assert result.returncode == 0 and json.loads(result.stdout)['tool_response'] == 'café', 'stdin or Unicode damaged'
            checks += 1
        print(f'{checks} launcher checks passed: events, blocking codes, spaces, fallback, missing files and missing Python.')
    finally:
        assert fixture.resolve().parent == Path(tempfile.gettempdir()).resolve()
        shutil.rmtree(fixture)

if __name__ == '__main__':
    main()
