# Locate Python at runtime; preserve hook input and fail-closed exit codes.
$ErrorActionPreference = 'Stop'

function Test-PythonCandidate {
    param([string]$Executable, [string[]]$Prefix = @())
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return }
    try {
        $resolved = & $Executable @Prefix -c 'import sys; sys.exit(1) if sys.version_info < (3, 10) else print(sys.executable)' 2> $null
        if ($LASTEXITCODE -eq 0) {
            $actual = [string]$resolved
            if ([IO.Path]::IsPathRooted($actual) -and (Test-Path -LiteralPath $actual -PathType Leaf)) {
                return [pscustomobject]@{ Executable = $actual }
            }
        }
    } catch { return }
}

function Find-WorkingPython {
    # Probe as we discover: a working PATH candidate avoids all fallback scans.
    foreach ($name in @('py.exe', 'python.exe', 'python3.exe')) {
        foreach ($application in @(Get-Command -Name $name -CommandType Application -All -ErrorAction SilentlyContinue)) {
            # WindowsApps aliases can open the Store rather than run Python.
            if ($application.Source -match '[\\/]WindowsApps[\\/]') { continue }
            $prefix = @()
            if ($name -eq 'py.exe') { $prefix = @('-3') }
            $candidate = Test-PythonCandidate -Executable $application.Source -Prefix $prefix
            if ($null -ne $candidate) { return $candidate }
        }
    }
    # Version-independent fallback for normal per-user / all-users installs.
    if ($env:LOCALAPPDATA) {
        $base = Join-Path $env:LOCALAPPDATA 'Programs/Python'
        if (Test-Path -LiteralPath $base -PathType Container) {
            foreach ($directory in @(Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue)) {
                $candidate = Test-PythonCandidate -Executable (Join-Path $directory.FullName 'python.exe')
                if ($null -ne $candidate) { return $candidate }
            }
        }
    }
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($base -and (Test-Path -LiteralPath $base -PathType Container)) {
            foreach ($directory in @(Get-ChildItem -Path (Join-Path $base 'Python*') -Directory -ErrorAction SilentlyContinue)) {
                $candidate = Test-PythonCandidate -Executable (Join-Path $directory.FullName 'python.exe')
                if ($null -ne $candidate) { return $candidate }
            }
        }
    }
}

function Get-PythonSelectionKey {
    # Preserve per-project interpreter selection without storing environment
    # values. Cwd covers relative PATH entries; roots cover fallback discovery.
    $values = @($env:PATH, $env:VIRTUAL_ENV, $env:PYTHONHOME, $env:PYTHONPATH,
        $env:PY_PYTHON, $env:PY_PYTHON3, $env:LOCALAPPDATA, $env:ProgramFiles,
        ${env:ProgramFiles(x86)}, [Environment]::CurrentDirectory) | ConvertTo-Json -Compress
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($values))).Replace('-', '')
    } finally { $hash.Dispose() }
}

function Read-PythonCache {
    param([string]$Path, [string]$SelectionKey)
    try {
        $record = [IO.File]::ReadAllText($Path) | ConvertFrom-Json
        if ($record.Version -ne 1 -or -not ($record.Executable -is [string])) { return }
        if ($record.SelectionKey -ne $SelectionKey) { return }
        $executable = $record.Executable
        if (-not [IO.Path]::IsPathRooted($executable) -or [IO.Path]::GetFullPath($executable) -ne $executable) { return }
        if ($executable -match '[\\/]WindowsApps[\\/]') { return }
        $age = [DateTime]::UtcNow.Ticks - [long]$record.WrittenTicks
        if ($age -lt 0 -or $age -gt [TimeSpan]::FromHours(24).Ticks) { return }
        $file = Get-Item -LiteralPath $executable -ErrorAction Stop
        if ($file.PSIsContainer -or $file.Length.ToString() -ne $record.Length -or
            $file.LastWriteTimeUtc.Ticks.ToString() -ne $record.ModifiedTicks) { return }
        return [pscustomobject]@{ Executable = $executable }
    } catch { return }
}

function Write-PythonCache {
    param([string]$Path, [string]$Executable, [string]$SelectionKey)
    $temporary = $null
    try {
        $file = Get-Item -LiteralPath $Executable -ErrorAction Stop
        $record = @{ Version = 1; Executable = $Executable; SelectionKey = $SelectionKey; Length = $file.Length.ToString();
            ModifiedTicks = $file.LastWriteTimeUtc.Ticks.ToString(); WrittenTicks = [DateTime]::UtcNow.Ticks.ToString() }
        $directory = [IO.Path]::GetDirectoryName($Path)
        $null = [IO.Directory]::CreateDirectory($directory)
        $temporary = Join-Path $directory ([Guid]::NewGuid().ToString('N') + '.tmp')
        [IO.File]::WriteAllText($temporary, ($record | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
        if ([IO.File]::Exists($Path)) {
            [IO.File]::Replace($temporary, $Path, [NullString]::Value)
        } else {
            try { [IO.File]::Move($temporary, $Path) }
            catch {
                # Another cold hook may have created the destination meanwhile.
                [IO.File]::Replace($temporary, $Path, [NullString]::Value)
            }
        }
    } catch {
        # Cache persistence is optional; never affect the guard decision.
    } finally {
        if ($temporary -and [IO.File]::Exists($temporary)) {
            try { [IO.File]::Delete($temporary) } catch { }
        }
    }
}

try {
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [Console]::InputEncoding = $utf8
    [Console]::OutputEncoding = $utf8
    $OutputEncoding = $utf8
    $env:PYTHONIOENCODING = 'utf-8'
    # Capture stdin before interpreter probes so no probe can consume the event.
    $hookInput = [Console]::In.ReadToEnd()
    $pluginRoot = $env:PLUGIN_ROOT
    if ([string]::IsNullOrWhiteSpace($pluginRoot)) { $pluginRoot = $env:CLAUDE_PLUGIN_ROOT }
    if ([string]::IsNullOrWhiteSpace($pluginRoot)) { $pluginRoot = Split-Path -Parent $PSScriptRoot }
    $guardPath = Join-Path $pluginRoot 'scripts/ssn_guard.py'
    if (-not (Test-Path -LiteralPath $guardPath -PathType Leaf)) {
        [Console]::Error.WriteLine('ssn-guard: guard script unavailable; blocked for safety')
        exit 2
    }
    $dataDirectory = $env:CLAUDE_PLUGIN_DATA
    if ([string]::IsNullOrWhiteSpace($dataDirectory)) { $dataDirectory = $env:PLUGIN_DATA }
    if ([string]::IsNullOrWhiteSpace($dataDirectory)) {
        $dataDirectory = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.ssn-guard'
    }
    $selectionKey = Get-PythonSelectionKey
    $cachePath = Join-Path $dataDirectory ('python-runtime-' + $selectionKey + '.json')
    $selectedPython = Read-PythonCache -Path $cachePath -SelectionKey $selectionKey
    if ($null -eq $selectedPython) {
        $selectedPython = Find-WorkingPython
        if ($null -ne $selectedPython) {
            Write-PythonCache -Path $cachePath -Executable $selectedPython.Executable -SelectionKey $selectionKey
        }
    }
    if ($null -eq $selectedPython) {
        [Console]::Error.WriteLine('ssn-guard: working Python 3.10+ unavailable; blocked for safety')
        exit 2
    }
    $executable = $selectedPython.Executable
    # One Python process checks its runtime and executes the guard. Preserve
    # SystemExit (including a denial); never rediscover/retry after guard runs.
    $bootstrap = @'
import sys
if sys.version_info < (3, 10):
    sys.stderr.write('ssn-guard: Python 3.10+ required; blocked for safety\n')
    sys.exit(2)
try:
    # Match direct script execution before importing the guard's modules.
    guard_path = sys.argv[1]
    guard_dir = guard_path[:max(guard_path.rfind('/'), guard_path.rfind(chr(92)))]
    if sys.path and sys.path[0] == '':
        sys.path.pop(0)
    sys.path.insert(0, guard_dir)
    sys.argv = sys.argv[1:]
    with open(guard_path, 'rb') as source:
        code = compile(source.read(), guard_path, 'exec')
    exec(code, {'__name__': '__main__', '__file__': guard_path, '__package__': None, '__cached__': None})
except Exception:
    sys.stderr.write('ssn-guard: Python bootstrap failed; blocked for safety\n')
    sys.exit(2)
'@
    $hookInput | & $executable -c $bootstrap $guardPath
    $resultCode = $LASTEXITCODE
    if ($resultCode -ne 0 -and $resultCode -ne 2) {
        [Console]::Error.WriteLine('ssn-guard: Python runtime failed; blocked for safety')
        exit 2
    }
    exit $resultCode
} catch {
    [Console]::Error.WriteLine('ssn-guard: hook launcher failed; blocked for safety')
    exit 2
}
