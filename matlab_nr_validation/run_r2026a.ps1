param(
    [ValidateSet('smoke', 'quick', 'medium', 'formal')]
    [string]$Profile = 'smoke',
    [string]$Seeds = '',
    [string]$BackgroundUEs = '',
    [string]$OutputRoot = ''
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
$options = [System.Collections.Generic.List[string]]::new()
if ($Seeds.Trim()) {
    $seedValues = ($Seeds -split ',') | ForEach-Object {
        $value = 0
        if (-not [int]::TryParse($_.Trim(), [ref]$value) -or $value -lt 1) {
            throw "Invalid seed: $_"
        }
        $value
    }
    $options.Add('Seeds=[' + ($seedValues -join ' ') + ']')
}
if ($BackgroundUEs.Trim()) {
    $loadValues = ($BackgroundUEs -split ',') | ForEach-Object {
        $value = 0
        if (-not [int]::TryParse($_.Trim(), [ref]$value) -or $value -lt 1) {
            throw "Invalid background-UE load: $_"
        }
        $value
    }
    $options.Add('BackgroundUEs=[' + ($loadValues -join ' ') + ']')
}
if ($OutputRoot.Trim()) {
    $escapedOutputRoot = $OutputRoot.Replace("'", "''")
    $options.Add("OutputRoot='$escapedOutputRoot'")
}

$optionText = if ($options.Count) { ', ' + ($options -join ', ') } else { '' }
$command = "cd('$sourceDir'); addpath(pwd); run_nr_validation('$Profile'$optionText);"

Write-Host "Using $matlab"
& $matlab -batch $command
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
