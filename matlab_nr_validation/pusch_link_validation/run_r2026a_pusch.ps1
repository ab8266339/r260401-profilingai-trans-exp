param(
    [ValidateSet('smoke', 'formal')]
    [string]$Profile = 'smoke'
)

$ErrorActionPreference = 'Stop'
$uninstallRoots = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$entry = Get-ItemProperty $uninstallRoots -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -eq 'MATLAB R2026a' } |
    Select-Object -First 1
if (-not $entry -or -not $entry.InstallLocation) {
    throw 'MATLAB R2026a is not registered on this machine.'
}

$matlab = Join-Path $entry.InstallLocation 'bin\matlab.exe'
if (-not (Test-Path -LiteralPath $matlab)) {
    throw "MATLAB R2026a executable not found at $matlab"
}

$sourceDir = $PSScriptRoot.Replace("'", "''")
$command = "cd('$sourceDir'); addpath(pwd); run_pusch_link_validation('$Profile');"

Write-Host "Using $matlab"
& $matlab -batch $command
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
