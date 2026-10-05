# Locate Python at runtime; preserve hook input and fail-closed exit codes.
$ErrorActionPreference = 'Stop'
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
    $pythonCandidates = @()
    foreach ($name in @('py.exe', 'python.exe', 'python3.exe')) {
        foreach ($application in @(Get-Command -Name $name -CommandType Application -All -ErrorAction SilentlyContinue)) {
            # WindowsApps aliases can open the Store rather than run Python.
            if ($application.Source -match '[\\/]WindowsApps[\\/]') { continue }
            $prefix = @()
            if ($name -eq 'py.exe') { $prefix = @('-3') }
            $pythonCandidates += [pscustomobject]@{ Executable = $application.Source; Prefix = $prefix }
        }
    }
    # Version-independent fallback for normal per-user / all-users installs.
    $installBases = @()
    if ($env:LOCALAPPDATA) { $installBases += Join-Path $env:LOCALAPPDATA 'Programs/Python' }
    foreach ($base in $installBases) {
        if (Test-Path -LiteralPath $base -PathType Container) {
            foreach ($directory in @(Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue)) {
                $pythonCandidates += [pscustomobject]@{ Executable = (Join-Path $directory.FullName 'python.exe'); Prefix = @() }
            }
        }
    }
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($base -and (Test-Path -LiteralPath $base -PathType Container)) {
            foreach ($directory in @(Get-ChildItem -Path (Join-Path $base 'Python*') -Directory -ErrorAction SilentlyContinue)) {
                $pythonCandidates += [pscustomobject]@{ Executable = (Join-Path $directory.FullName 'python.exe'); Prefix = @() }
            }
        }
    }
    $selectedPython = $null
    foreach ($candidate in $pythonCandidates) {
        if (-not (Test-Path -LiteralPath $candidate.Executable -PathType Leaf)) { continue }
        try {
            $executable = $candidate.Executable
            $prefix = $candidate.Prefix
            & $executable @prefix -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' > $null 2> $null
            if ($LASTEXITCODE -eq 0) { $selectedPython = $candidate; break }
        } catch { continue }
    }
    if ($null -eq $selectedPython) {
        [Console]::Error.WriteLine('ssn-guard: working Python 3.10+ unavailable; blocked for safety')
        exit 2
    }
    $executable = $selectedPython.Executable
    $prefix = $selectedPython.Prefix
    $hookInput | & $executable @prefix $guardPath
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine('ssn-guard: hook launcher failed; blocked for safety')
    exit 2
}
