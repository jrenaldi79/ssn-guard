"""Exercise Windows runtime caching using isolated plugin data and real Python."""
import base64
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@dataclass
class LaunchFixture:
    root: Path
    data: Path
    environment: dict = field(repr=False)

    def __getitem__(self, index):
        return (self.root, self.data, self.environment)[index]


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / "plugin with spaces"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(SCRIPTS / "launch_windows.ps1", scripts / "launch_windows.ps1")
    (scripts / "ssn_guard.py").write_text(
        "import json,sys\ndata=json.load(sys.stdin)\n"
        "print(json.dumps({'event':data,'python':sys.executable}))\n", encoding="utf-8")
    data = tmp_path / "isolated data"
    environment = dict(os.environ, PLUGIN_ROOT=str(root), PLUGIN_DATA=str(data), CLAUDE_PLUGIN_DATA=str(data))
    return LaunchFixture(root, data, environment)


def invoke(fixture, no_discovery=False, payload=None, cwd=None):
    root, environment = fixture.root, fixture.environment
    shell = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    script = "$ProgressPreference = 'SilentlyContinue'\n"
    if no_discovery:
        script += "function Get-Command { throw 'warm launch must skip discovery' }\n"
        script += "function Get-ChildItem { throw 'warm launch must skip install scans' }\n"
    path = str(root / "scripts/launch_windows.ps1").replace("'", "''")
    script += "& '" + path + "'\nexit $LASTEXITCODE"
    encoded = base64.b64encode(script.encode("utf-16le")).decode()
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                          input=payload if payload is not None else json.dumps({"text": "Harmless café"}),
                          text=True, encoding="utf-8", capture_output=True, env=environment,
                          cwd=cwd or root, timeout=20)


def prime(fixture):
    result = invoke(fixture)
    assert result.returncode == 0, result.stderr
    cache = single_cache(fixture)
    assert cache.is_file()
    return cache, json.loads(cache.read_text(encoding="utf-8-sig"))


def single_cache(fixture):
    caches = list(fixture.data.glob("python-runtime-*.json"))
    assert len(caches) == 1
    return caches[0]


def test_warm_launch_skips_discovery_and_preserves_stdin(fixture):
    prime(fixture)
    result = invoke(fixture, no_discovery=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["event"] == {"text": "Harmless café"}
    assert not result.stderr.strip()


@pytest.mark.parametrize("damage", ["malformed", "missing", "modified", "expired", "relative"])
def test_invalid_cache_rediscovers_python(fixture, damage):
    cache, record = prime(fixture)
    if damage == "malformed":
        cache.write_text("{", encoding="utf-8")
    else:
        if damage == "missing":
            record["Executable"] = str(fixture[0] / "missing/python.exe")
        elif damage == "modified":
            record["Length"] = "0"
        elif damage == "expired":
            record["WrittenTicks"] = "0"
        else:
            record["Executable"] = "python.exe"
        cache.write_text(json.dumps(record), encoding="utf-8")
    result = invoke(fixture)
    assert result.returncode == 0, result.stderr
    repaired = json.loads(cache.read_text(encoding="utf-8-sig"))
    assert repaired["Executable"] == json.loads(result.stdout)["python"]
    assert invoke(fixture, no_discovery=True).returncode == 0


def test_unwritable_cache_is_optional(fixture):
    # A directory at the cache filename prevents atomic replacement on Windows.
    cache, _ = prime(fixture)
    cache.unlink()
    cache.mkdir()
    result = invoke(fixture)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["event"]["text"] == "Harmless café"


def test_concurrent_cold_launches_leave_valid_cache(fixture):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: invoke(fixture), range(4)))
    assert all(result.returncode == 0 for result in results)
    cache = single_cache(fixture)
    record = json.loads(cache.read_text(encoding="utf-8-sig"))
    assert record["Executable"] in {json.loads(result.stdout)["python"] for result in results}
    assert invoke(fixture, no_discovery=True).returncode == 0
    assert not list(fixture[1].glob("*.tmp"))


def test_guard_failure_is_not_retried(fixture):
    prime(fixture)
    guard = fixture[0] / "scripts/ssn_guard.py"
    marker = fixture[1] / "guard-runs.txt"
    guard.write_text("from pathlib import Path\nimport sys\n"
                     + "with Path(" + repr(str(marker)) + ").open('a') as f: f.write('run\\n')\n"
                     + "sys.stderr.write('synthetic denial\\n')\nsys.exit(2)\n", encoding="utf-8")
    result = invoke(fixture, no_discovery=True)
    assert result.returncode == 2
    assert "synthetic denial" in result.stderr
    assert marker.read_text() == "run\n"


def test_bootstrap_failure_withholds_exception_details(fixture):
    prime(fixture)
    guard = fixture.root / "scripts/ssn_guard.py"
    guard.write_text("raise RuntimeError('private-marker')\n", encoding="utf-8")
    result = invoke(fixture, no_discovery=True)
    assert result.returncode == 2
    assert not result.stdout.strip()
    assert "bootstrap failed" in result.stderr
    assert "private-marker" not in result.stderr


@pytest.mark.parametrize("variable", ["PATH", "VIRTUAL_ENV", "PY_PYTHON3"])
def test_selection_environment_change_forces_discovery(fixture, variable):
    prime(fixture)
    fixture.environment[variable] = fixture.environment.get(variable, "") + ";changed-for-test"
    # Blocking discovery proves the old interpreter cannot be silently reused
    # after another project's interpreter-selection environment takes effect.
    result = invoke(fixture, no_discovery=True)
    assert result.returncode == 2


def test_primed_projects_reuse_selections_after_switching(fixture):
    project_a = fixture.root / "project A"
    project_b = fixture.root / "project B"
    project_a.mkdir()
    project_b.mkdir()
    interpreters = {}
    for project in (project_a, project_b):
        result = invoke(fixture, cwd=project)
        assert result.returncode == 0, result.stderr
        interpreters[project] = json.loads(result.stdout)["python"]
    # Returning A/B/A must not evict either previously validated selection.
    for project in (project_a, project_b, project_a):
        result = invoke(fixture, cwd=project, no_discovery=True)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["event"] == {"text": "Harmless café"}
        assert json.loads(result.stdout)["python"] == interpreters[project]
        assert not result.stderr.strip()


def test_primed_environments_reuse_selections_after_switching(fixture):
    original = fixture.environment.get("VIRTUAL_ENV")
    alternatives = (str(fixture.root / "environment A"), str(fixture.root / "environment B"))
    interpreters = {}
    try:
        for environment in alternatives:
            fixture.environment["VIRTUAL_ENV"] = environment
            result = invoke(fixture)
            assert result.returncode == 0, result.stderr
            interpreters[environment] = json.loads(result.stdout)["python"]
        for environment in (*alternatives, alternatives[0]):
            fixture.environment["VIRTUAL_ENV"] = environment
            result = invoke(fixture, no_discovery=True)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)["event"] == {"text": "Harmless café"}
            assert json.loads(result.stdout)["python"] == interpreters[environment]
            assert not result.stderr.strip()
    finally:
        if original is None:
            fixture.environment.pop("VIRTUAL_ENV", None)
        else:
            fixture.environment["VIRTUAL_ENV"] = original


def test_corrupt_project_cache_does_not_evict_another_project(fixture):
    cache_a, _ = prime(fixture)
    project_b = fixture.root / "project B"
    project_b.mkdir()
    result = invoke(fixture, cwd=project_b)
    assert result.returncode == 0, result.stderr
    assert len(list(fixture.data.glob("python-runtime-*.json"))) == 2
    cache_a.write_text("{", encoding="utf-8")
    result = invoke(fixture, cwd=project_b, no_discovery=True)
    assert result.returncode == 0, result.stderr
    assert invoke(fixture, no_discovery=True).returncode == 2
    repaired = invoke(fixture)
    assert repaired.returncode == 0, repaired.stderr
    assert invoke(fixture, no_discovery=True).returncode == 0
    assert invoke(fixture, cwd=project_b, no_discovery=True).returncode == 0


def test_legacy_single_entry_cache_is_ignored(fixture):
    cache, record = prime(fixture)
    legacy = fixture.data / "python-runtime.json"
    legacy.write_text(json.dumps(record), encoding="utf-8")
    cache.unlink()
    assert invoke(fixture, no_discovery=True).returncode == 2
    result = invoke(fixture)
    assert result.returncode == 0, result.stderr
    assert cache.is_file()
    assert json.loads(legacy.read_text(encoding="utf-8")) == record
    assert invoke(fixture, no_discovery=True).returncode == 0


def test_project_modules_do_not_shadow_bootstrap_imports(fixture):
    marker = fixture.root / "shadow-ran.txt"
    shadow = "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('bad')\nraise RuntimeError('shadow import')\n"
    for module in ("json.py", "runpy.py"):
        (fixture.root / module).write_text(shadow, encoding="utf-8")
    result = invoke(fixture)
    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert invoke(fixture, no_discovery=True).returncode == 0


def test_cached_runtime_startup_failure_blocks(fixture):
    cache, record = prime(fixture)
    # A real native executable returning 1 simulates Python failing before
    # bootstrap execution, without touching an installed Python runtime.
    native = fixture.root / "native-failure.exe"
    shell = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    definition = "public class NativeFailure { public static int Main() { return 1; } }"
    command = "Add-Type -TypeDefinition '" + definition + "' -OutputType ConsoleApplication -OutputAssembly '" + str(native).replace("'", "''") + "'"
    compile_result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command],
                                    capture_output=True, text=True, timeout=20)
    assert compile_result.returncode == 0, compile_result.stderr
    stat = native.stat()
    record.update(Executable=str(native), Length=str(stat.st_size),
                  ModifiedTicks=str(stat.st_mtime_ns // 100 + 621355968000000000))
    cache.write_text(json.dumps(record), encoding="utf-8")
    result = invoke(fixture, no_discovery=True)
    assert result.returncode == 2
    assert "runtime failed" in result.stderr
