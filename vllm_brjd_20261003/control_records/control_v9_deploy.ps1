$ErrorActionPreference='Stop'
$taskRoot='E:\r260401-brjd-20261003'
$stageRoot='C:\Users\admin\AppData\Local\Temp\r260401-brjd-20261004-stage-v9'
$nasRoot='W:\ML\ml-work\R260401-profilingai-trans\vllm_brjd_20261003'
$linuxRoot='\\wsl.localhost\Ubuntu-22.04\home\hxu\exp\r260401_brjd_20261003'
$utf8=[Text.UTF8Encoding]::new($false)
function Sha([string]$p){return (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower()}
function PutImmutable([string]$p,[byte[]]$bytes){
 if(Test-Path -LiteralPath $p){
  $existing=[IO.File]::ReadAllBytes($p)
  if($existing.Length -ne $bytes.Length -or [Convert]::ToBase64String($existing) -ne [Convert]::ToBase64String($bytes)){throw "Immutable conflict: $p"}
 }else{
  [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($p))|Out-Null
  [IO.File]::WriteAllBytes($p,$bytes)
 }
}
function AtomicJson([string]$p,$object){
 $tmp=$p+'.v9.tmp'
 $content=$utf8.GetBytes(($object|ConvertTo-Json -Depth 40))
 if(Test-Path -LiteralPath $tmp){
  if([Convert]::ToBase64String([IO.File]::ReadAllBytes($tmp)) -ne [Convert]::ToBase64String($content)){throw "Unexpected temporary content: $tmp"}
 }else{[IO.File]::WriteAllBytes($tmp,$content)}
 if(Test-Path -LiteralPath $p){
  $backup=$p+'.before-v9'
  if(Test-Path -LiteralPath $backup){throw "Unexpected backup exists: $backup"}
  [IO.File]::Replace($tmp,$p,$backup)
 }else{[IO.File]::Move($tmp,$p)}
}
$active=ConvertFrom-Json ([IO.File]::ReadAllText((Join-Path $taskRoot 'ACTIVE_CONTROL.json'),$utf8))
if($active.controller -ne 'controller_v8.py'){throw 'Active version changed'}
$oldManifestPath=Join-Path $taskRoot $active.manifest
if((Sha $oldManifestPath) -ne $active.manifest_sha256){throw 'Old manifest identity mismatch'}
$manifest=ConvertFrom-Json ([IO.File]::ReadAllText($oldManifestPath,$utf8))
foreach($entry in $manifest.files.PSObject.Properties){if((Sha (Join-Path $taskRoot $entry.Name)) -ne $entry.Value){throw "Old frozen file changed: $($entry.Name)"}}
$controlPath=Join-Path $taskRoot 'control'
$statusPath=Join-Path $controlPath 'status.json'
$oldStatus=ConvertFrom-Json ([IO.File]::ReadAllText($statusPath,$utf8))
if($oldStatus.state -ne 'needs_review' -or $oldStatus.controller_pid -ne 210828 -or $oldStatus.returncode -ne 1){throw 'Unexpected failed state'}
if(Test-Path -LiteralPath (Join-Path $controlPath 'controller.lock')){throw 'Controller lock still exists'}
if(Get-Process -Id 210828 -ErrorAction SilentlyContinue){throw 'Old controller PID exists'}
$review=ConvertFrom-Json ([IO.File]::ReadAllText((Join-Path $stageRoot 'R4_STARTUP_REVIEW_v9.json'),$utf8))
if((Sha (Join-Path $controlPath 'history.json')) -ne $review.history_sha256 -or (Sha $statusPath) -ne $review.original_status_sha256 -or (Sha (Join-Path $taskRoot 'logs\r260401_brjd_20261003_r4.log')) -ne $review.failure_log_sha256){throw 'Recovery proof no longer matches'}
if($review.project_id -ne 'R260401' -or $review.machine -ne 'DESKTOP-BRJD313' -or -not $review.science_workload_not_launched){throw 'Wrong review identity'}
$validation=ConvertFrom-Json ([IO.File]::ReadAllText((Join-Path $stageRoot 'control_validation\VALIDATION_CONTROLLER_v9.json'),$utf8))
if($validation.status -ne 'PASS' -or -not $validation.cpu_only){throw 'Validation did not pass'}
foreach($entry in $validation.source_files_sha256.PSObject.Properties){if((Sha (Join-Path $stageRoot $entry.Name)) -ne $entry.Value){throw 'CPU validation source hash changed'}}
if(Test-Path -LiteralPath (Join-Path $linuxRoot 'results\8B\r4')){throw 'R4 output exists; needs review'}
if(@(Get-ChildItem -LiteralPath (Join-Path $linuxRoot 'logs') -File | Where-Object {$_.Name -match 'r4'}).Count -ne 0){throw 'R4 science log exists; needs review'}
$newFiles=@('controller_v9.py','scripts/common_guard_v9.py','queue_v9.json','BRJD_EXECUTION_PROTOCOL_v9.json','R4_STARTUP_REVIEW_v9.json','control_validation/R4_v8_FAILED_STATUS.json','control_validation/R4_v8_STARTUP_REVIEW_EVIDENCE.json','control_validation/VALIDATION_CONTROLLER_v9.json')
foreach($rel in $newFiles){
 $source=Join-Path $stageRoot $rel
 $bytes=[IO.File]::ReadAllBytes($source)
 foreach($base in @($taskRoot,$linuxRoot,(Join-Path $nasRoot 'frozen'))){PutImmutable (Join-Path $base $rel) $bytes}
 $hash=Sha $source
 $manifest.files|Add-Member -NotePropertyName $rel -NotePropertyValue $hash
}
$manifest.control_version=9
$manifestBytes=$utf8.GetBytes(($manifest|ConvertTo-Json -Depth 40))
foreach($base in @($taskRoot,$linuxRoot,(Join-Path $nasRoot 'frozen'))){PutImmutable (Join-Path $base 'FILE_MANIFEST_v9.json') $manifestBytes}
foreach($entry in $manifest.files.PSObject.Properties){
 foreach($base in @($taskRoot,$linuxRoot,(Join-Path $nasRoot 'frozen'))){
  if((Sha (Join-Path $base $entry.Name)) -ne $entry.Value){throw "Deployment SHA mismatch: $base / $($entry.Name)"}
 }
}
$newActive=[ordered]@{controller='controller_v9.py';queue='queue_v9.json';manifest='FILE_MANIFEST_v9.json';manifest_sha256=(Sha (Join-Path $taskRoot 'FILE_MANIFEST_v9.json'))}
$reviewId=Sha (Join-Path $taskRoot 'R4_STARTUP_REVIEW_v9.json')
$records=Join-Path $nasRoot 'control_records'
PutImmutable (Join-Path $records 'ACTIVE_CONTROL_v9.json') $utf8.GetBytes(($newActive|ConvertTo-Json -Depth 10))
AtomicJson (Join-Path $taskRoot 'ACTIVE_CONTROL.json') $newActive
AtomicJson $statusPath ([ordered]@{project_id='R260401';machine='DESKTOP-BRJD313';state='reviewed_no_measurement';time=[DateTimeOffset]::Now.ToString('o');active_job='r260401_brjd_20261003_r4';previous_status=$oldStatus;review_id=$reviewId;review_record=(Join-Path $records 'r4_v8_startup_review_evidence_20261004.json')})
[ordered]@{status='PASS';control_version=9;manifest_sha256=$newActive.manifest_sha256;files_verified=($manifest.files.PSObject.Properties|Measure-Object).Count;preflight_reused=$true;science_parameters_changed=$false;review_id=$reviewId}|ConvertTo-Json
