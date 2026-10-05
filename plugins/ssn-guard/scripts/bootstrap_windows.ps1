# This bootstrap is encoded into commandWindows to avoid nested shell quoting.
# It still invokes the .ps1 normally; it does not change execution policy.
$ProgressPreference = 'SilentlyContinue'
try {
    $pluginRoot = [Environment]::GetEnvironmentVariable('PLUGIN_ROOT')
    if ([string]::IsNullOrWhiteSpace($pluginRoot)) {
        $pluginRoot = [Environment]::GetEnvironmentVariable('CLAUDE_PLUGIN_ROOT')
    }
    if ([string]::IsNullOrWhiteSpace($pluginRoot)) {
        [Console]::Error.WriteLine('ssn-guard: plugin root unavailable; blocked for safety')
        exit 2
    }
    & (Join-Path $pluginRoot 'scripts/launch_windows.ps1')
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine('ssn-guard: launcher unavailable; blocked for safety')
    exit 2
}
