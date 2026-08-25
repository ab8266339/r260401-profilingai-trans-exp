param(
    [Parameter(Mandatory = $true)]
    [string]$SystemCsv,
    [string]$PUSCHCsv = '',
    [string]$OutputDirectory = '',
    [string]$BaseName = '',
    [string]$TotalUEs = '',
    [switch]$Preview,
    [switch]$Visible
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
if (-not (Test-Path -LiteralPath $SystemCsv)) {
    throw "System CSV not found: $SystemCsv"
}

$sourceDir = $PSScriptRoot.Replace("'", "''")
$systemPath = (Resolve-Path -LiteralPath $SystemCsv).Path.Replace("'", "''")
$options = [System.Collections.Generic.List[string]]::new()
if ($PUSCHCsv.Trim()) {
    if (-not (Test-Path -LiteralPath $PUSCHCsv)) {
        throw "PUSCH CSV not found: $PUSCHCsv"
    }
    $puschPath = (Resolve-Path -LiteralPath $PUSCHCsv).Path.Replace("'", "''")
    $options.Add("PUSCHCsvPath='$puschPath'")
}
if ($OutputDirectory.Trim()) {
    $fullOutput = [System.IO.Path]::GetFullPath($OutputDirectory).Replace("'", "''")
    $options.Add("OutputDirectory='$fullOutput'")
}
if ($BaseName.Trim()) {
    if ($BaseName -notmatch '^[A-Za-z0-9_-]+$') {
        throw 'BaseName may contain only letters, digits, underscores, and hyphens.'
    }
    $options.Add("BaseName='$BaseName'")
}
if ($TotalUEs.Trim()) {
    $loads = ($TotalUEs -split ',') | ForEach-Object {
        $value = 0
        if (-not [int]::TryParse($_.Trim(), [ref]$value) -or $value -lt 1) {
            throw "Invalid total-UE load: $_"
        }
        $value
    }
    if ($loads.Count -gt 2) {
        throw 'At most two total-UE loads can be plotted.'
    }
    $options.Add('TotalUEs=[' + ($loads -join ' ') + ']')
}
if ($Preview) {
    $options.Add('ForcePreview=true')
}
if ($Visible) {
    $options.Add('Visible=true')
}

$optionText = if ($options.Count) { ', ' + ($options -join ', ') } else { '' }
$command = "cd('$sourceDir'); addpath(pwd); o=plot_validation_figure('$systemPath'$optionText); [outDir,outStem]=fileparts(o.PDF); verify_validation_figure(outDir,BaseName=outStem);"

Write-Host "Using $matlab"
& $matlab -batch $command
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
