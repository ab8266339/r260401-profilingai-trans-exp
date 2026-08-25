param(
    [int[]]$Seeds = @(1, 2, 3, 4, 5),
    [int]$UesPerGnb = 2,
    [int]$TrafficScenario = 3,
    [int]$ApplicationSeconds = 3
)

$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
$resultRoot = Join-Path $PSScriptRoot 'results\5glena\raw'
[System.IO.Directory]::CreateDirectory($resultRoot) | Out-Null

$patterns = [ordered]@{
    ul4_spread = 'DL|DL|DL|DL|UL|DL|DL|DL|DL|UL|DL|DL|DL|DL|UL|DL|DL|DL|F|UL|'
    ul11_spread = 'DL|UL|DL|UL|DL|UL|DL|UL|DL|UL|DL|UL|DL|UL|DL|UL|F|UL|UL|UL|'
    ul4_contiguous = 'DL|DL|DL|DL|DL|DL|DL|DL|DL|DL|DL|DL|DL|DL|DL|F|UL|UL|UL|UL|'
}

$common = @(
    'cttc-nr-3gpp-calibration-user'
    '--configurationType=customConf'
    '--scenario=UMi'
    '--numRings=0'
    "--ueNumPergNb=$UesPerGnb"
    "--appGenerationTime=+$($ApplicationSeconds)s"
    '--appStopWindow=+1s'
    '--numerologyBwp=1'
    '--direction=UL'
    '--simulator=5GLENA'
    '--technology=NR'
    '--operationMode=TDD'
    '--errorModelType=ns3::NrEesmIrT2'
    '--enableFading=0'
    '--enableShadowing=0'
    '--bfMethod=FixedBeam'
    "--trafficScenario=$TrafficScenario"
    '--scheduler=PF'
    '--bandwidth=20'
    '--startingFreq=3.5e9'
    '--gnbTxPower=40'
    '--ueTxPower=23'
    '--gnbNumRows=1'
    '--gnbNumColumns=1'
    '--ueNumRows=1'
    '--ueNumColumns=1'
    '--basicTraces=0'
    '--extendedTraces=0'
    '--outputDir=/tmp'
)

foreach ($seed in $Seeds) {
    foreach ($entry in $patterns.GetEnumerator()) {
        $tag = '{0}_seed{1:D4}' -f $entry.Key, $seed
        $arguments = @($common) + @(
            "--pattern=$($entry.Value)"
            "--simTag=$tag"
            "--dbName=$tag.db"
            "--RngRun=$seed"
        )
        $program = $arguments -join ' '
        $wslCommand = "cd /home/hxu/ran-sims/ns-3-lena && ./ns3 run '$program'"
        $output = & wsl -d Ubuntu-22.04 -- bash -lc $wslCommand 2>&1
        $exitCode = $LASTEXITCODE
        $logPath = Join-Path $resultRoot "$tag.log"
        [System.IO.File]::WriteAllLines($logPath, [string[]]$output, $utf8)
        if ($exitCode -ne 0) {
            throw "5G-LENA run failed for $tag; inspect $logPath"
        }
    }
}

