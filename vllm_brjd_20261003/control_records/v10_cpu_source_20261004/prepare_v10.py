"""Stage control-only v10; no deployment, controller launch or science edits."""
import hashlib
import json
from pathlib import Path

SOURCE = Path(r'E:\r260401-brjd-20261003')
STAGE = Path(__file__).resolve().parent


def create(relative, data):
    path = STAGE/relative
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as handle:
        handle.write(data)


def encoded(value):
    return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')


controller = (SOURCE/'controller_v9.py').read_text(encoding='utf-8')
begin = controller.index('def atomic_json(')
end = controller.index('def timestamp(',begin)
controller = controller[:begin] + '''ATOMIC_REPLACE_RETRY_SECONDS = 2.0
ATOMIC_REPLACE_RETRY_INTERVAL_SECONDS = .05
ATOMIC_REPLACE_RETRY_WINERRORS = frozenset((5,32,33))

def atomic_json(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    deadline=time.monotonic()+ATOMIC_REPLACE_RETRY_SECONDS
    retries=0
    while True:
        try:
            os.replace(tmp,p)
            return
        except OSError as exc:
            # Only transient Windows sharing/access conflicts are retryable.
            # Keep the old complete target until one atomic replacement succeeds.
            if os.name!='nt' or getattr(exc,'winerror',None) not in ATOMIC_REPLACE_RETRY_WINERRORS:
                raise
            remaining=deadline-time.monotonic()
            if remaining<=0 or retries>=40:
                raise
            retries+=1
            time.sleep(min(ATOMIC_REPLACE_RETRY_INTERVAL_SECONDS,remaining))

''' + controller[end:]

begin = controller.index('def validate_startup_review(')
end = controller.index('def main():',begin)
controller = controller[:begin] + '''RECOVERY_LINUX_FILES_ROOT=Path(r'\\\\wsl.localhost\\Ubuntu-22.04\\home\\hxu\\exp\\r260401_brjd_20261003')

def validate_recovery_review(previous,history_bytes,error_bytes):
    if previous.get('state')=='reviewed_no_measurement':
        raise NeedsReview('Obsolete no-measurement review cannot authorize partial resume')
    if previous.get('state')!='reviewed_partial_resume':return
    data=(ROOT/'RECOVERY_REVIEW_v10.json').read_bytes()
    proof=json.loads(data.decode('utf-8'))
    if previous.get('review_id')!=hashlib.sha256(data).hexdigest():
        raise NeedsReview('Recovery review identity mismatch')
    if (proof.get('project_id')!='R260401' or proof.get('machine')!='DESKTOP-BRJD313'
        or proof.get('control_version')!=10 or proof.get('reviewed_failed_control_version')!=9
        or proof.get('failed_tag')!='r260401_brjd_20261003_r4'
        or proof.get('science_workload_was_launched') is not True
        or proof.get('partial_resume') is not True or proof.get('preflight_reused') is not True
        or proof.get('resume_root')!='results/8B/r4'
        or hashlib.sha256(history_bytes).hexdigest()!=proof.get('history_sha256')
        or hashlib.sha256(error_bytes).hexdigest()!=proof.get('controller_error_log_sha256')):
        raise NeedsReview('Recovery review is stale or belongs to another failure')
    original=(ROOT/'control_validation/R4_v9_FAILED_STATUS.json').read_bytes()
    frozen_error=(ROOT/'control_validation/R4_v9_CONTROLLER_ERRLOG.log').read_bytes()
    if (hashlib.sha256(original).hexdigest()!=proof.get('original_status_sha256')
        or hashlib.sha256(frozen_error).hexdigest()!=proof.get('controller_error_log_sha256')
        or frozen_error!=error_bytes):
        raise NeedsReview('Original v9 failure status/error changed')
    original_state=json.loads(original.decode('utf-8'))
    if (original_state.get('state')!='needs_review' or original_state.get('queue_id')!=QUEUE_ID
        or original_state.get('machine')!='DESKTOP-BRJD313'
        or original_state.get('active_job')!=proof['failed_tag']
        or 'PermissionError' not in original_state.get('error','')
        or '[WinError 5]' not in original_state.get('error','')):
        raise NeedsReview('Original v9 sharing-lock failure has the wrong identity')
    evidence_bytes=(ROOT/'control_validation/R4_v9_PARTIAL_RECOVERY_EVIDENCE.json').read_bytes()
    if hashlib.sha256(evidence_bytes).hexdigest()!=proof.get('recovery_evidence_sha256'):
        raise NeedsReview('Partial recovery evidence changed')
    evidence=json.loads(evidence_bytes.decode('utf-8'))
    if (evidence.get('project_id')!='R260401' or evidence.get('machine')!='DESKTOP-BRJD313'
        or evidence.get('science_workload_was_launched') is not True
        or evidence.get('partial_resume') is not True
        or evidence.get('partial_files')!=proof.get('partial_files')
        or evidence.get('complete_cells')!=proof.get('complete_cells')):
        raise NeedsReview('Recovery evidence does not support the partial resume')
    inventory_bytes=(ROOT/'control_validation/R4_v9_PARTIAL_INVENTORY.json').read_bytes()
    review_bytes=(ROOT/'control_validation/R4_v10_PARTIAL_REVIEW.json').read_bytes()
    if (hashlib.sha256(inventory_bytes).hexdigest()!=proof.get('partial_inventory_sha256')
        or hashlib.sha256(review_bytes).hexdigest()!=proof.get('partial_review_sha256')):
        raise NeedsReview('Partial inventory or its validity review changed')
    inventory=json.loads(inventory_bytes.decode('utf-8'));review=json.loads(review_bytes.decode('utf-8'))
    if (inventory.get('project_id')!='R260401' or inventory.get('machine')!='DESKTOP-BRJD313'
        or inventory.get('resume_root')!=proof['resume_root']
        or inventory.get('scientific_workload_did_run') is not True
        or inventory.get('controller_exited') is not True or inventory.get('lock_absent') is not True
        or inventory.get('history_sha256')!=proof['history_sha256']
        or inventory.get('original_status_sha256')!=proof['original_status_sha256']
        or inventory.get('controller_error_sha256')!=proof['controller_error_log_sha256']
        or {relative:value.get('sha256') for relative,value in inventory.get('files',{}).items()}!=proof['partial_files']
        or review.get('project_id')!='R260401' or review.get('machine')!='DESKTOP-BRJD313'
        or review.get('failed_control_version')!=9
        or review.get('resume_authorized') is not True or review.get('retain_complete_cells') is not True
        or review.get('scientific_workload_did_run') is not True
        or review.get('partial_inventory_sha256')!=proof['partial_inventory_sha256']
        or review.get('complete_cells')!=proof['complete_cells']
        or review.get('complete_cells_sha256')!={relative:proof['partial_files'].get(relative) for relative in proof['complete_cells']}
        or hashlib.sha256((ROOT/'scripts/rplus_brjd.py').read_bytes()).hexdigest()!=review.get('source_science_sha256')):
        raise NeedsReview('Partial inventory/review does not authorize preserved-cell resume')
    warmup_bytes=(ROOT/'control_validation/WARMUP_RECOVERY_v10.json').read_bytes()
    host_guard_bytes=(ROOT/'control_validation/R4_v9_HOST_GUARD.jsonl').read_bytes()
    if (hashlib.sha256(warmup_bytes).hexdigest()!=proof.get('warmup_recovery_sha256')
        or review.get('warmup_recovery_sha256')!=proof.get('warmup_recovery_sha256')
        or hashlib.sha256(host_guard_bytes).hexdigest()!=proof.get('host_guard_sha256')):
        raise NeedsReview('Warmup recovery or original host guard evidence changed')
    warmup=json.loads(warmup_bytes.decode('utf-8'))
    allowed_warmup={
        'repetitions/r00/default/warmup/t3400_c4_d8_R.done',
        'repetitions/r00/default/warmup/t3400_c4_d8_A0.done',
        'repetitions/r00/default/warmup/t900_c16_d8_R.done'}
    actions=warmup.get('actions');quarantined={}
    if (warmup.get('project_id')!='R260401' or warmup.get('machine')!='DESKTOP-BRJD313'
        or warmup.get('controller_exited') is not True
        or warmup.get('partial_inventory_sha256')!=proof['partial_inventory_sha256']
        or warmup.get('marker_count')!=3 or not isinstance(actions,list) or len(actions)!=3
        or warmup.get('timing_cells_retained_unchanged') is not True
        or warmup.get('repeated_timing_measurements_authorized') is not False
        or warmup.get('science_script_parameters_changed') is not False
        or warmup.get('new_engine_warmup_required') is not True
        or warmup.get('original_23_file_snapshot_preserved') is not True):
        raise NeedsReview('Warmup recovery does not preserve frozen timing measurements')
    for action in actions:
        relative=action.get('relative')
        if (relative not in allowed_warmup or relative in quarantined
            or action.get('original_marker_absent') is not True
            or action.get('sha256')!=proof['partial_files'].get(relative)):
            raise NeedsReview('Malformed warmup quarantine identity')
        quarantine=ROOT/'control/warmup_quarantine_v9'/Path(relative).name
        if os.path.normcase(action.get('quarantine_windows',''))!=os.path.normcase(str(quarantine)):
            raise NeedsReview('Warmup quarantine path differs from its fixed task folder')
        if hashlib.sha256(quarantine.read_bytes()).hexdigest()!=action['sha256']:
            raise NeedsReview('Quarantined warmup bytes changed')
        quarantined[relative]=action['sha256']
    if set(quarantined)!=allowed_warmup:
        raise NeedsReview('Warmup quarantine set is incomplete')
    rows=json.loads(history_bytes.decode('utf-8'))
    if (not isinstance(rows,list) or len(rows)!=3 or proof.get('history_row_count')!=3
        or rows[0].get('tag')!='r260401_brjd_20261003_preflight'
        or rows[0].get('returncode')!=0 or rows[0].get('guard_violation')
        or any(row.get('tag')!=proof['failed_tag'] or row.get('returncode')!=1
               or row.get('guard_violation') for row in rows[1:])):
        raise NeedsReview('Unexpected partial recovery history')
    partial_files=proof.get('partial_files');complete_cells=proof.get('complete_cells')
    if not isinstance(partial_files,dict) or len(partial_files)!=23 or not isinstance(complete_cells,list) or len(complete_cells)!=2:
        raise NeedsReview('Partial recovery is missing the frozen two-cell inventory')
    snapshots={}
    for relative,expected in partial_files.items():
        if (not isinstance(relative,str) or not relative or '\\\\' in relative
            or Path(relative).is_absolute() or '..' in Path(relative).parts
            or not isinstance(expected,str)
            or len(expected)!=64 or any(ch not in '0123456789abcdef' for ch in expected)):
            raise NeedsReview('Unsafe partial recovery member identity')
        frozen=(ROOT/'control_validation/R4_v9_PARTIAL_SNAPSHOT'/relative).read_bytes()
        live_path=RECOVERY_LINUX_FILES_ROOT/proof['resume_root']/relative
        if hashlib.sha256(frozen).hexdigest()!=expected:
            raise NeedsReview('Frozen/live partial result changed: '+relative)
        if relative in quarantined:
            try:live_path.stat()
            except FileNotFoundError:pass
            else:raise NeedsReview('Old warmup marker was restored before new-engine warmup')
        elif hashlib.sha256(live_path.read_bytes()).hexdigest()!=expected:
            raise NeedsReview('Frozen/live partial result changed: '+relative)
        snapshots[relative]=frozen
    if 'capacity_freeze.json' not in partial_files:
        raise NeedsReview('Partial recovery has no capacity freeze')
    for relative in complete_cells:
        if relative not in snapshots or '/cells/' not in relative:
            raise NeedsReview('Complete cell is absent from partial inventory')
        cell=json.loads(snapshots[relative].decode('utf-8'))
        protocol_relative=relative.rsplit('/cells/',1)[0]+'/protocol.json'
        if protocol_relative not in snapshots:
            raise NeedsReview('Complete cell protocol is absent from partial inventory')
        protocol=json.loads(snapshots[protocol_relative].decode('utf-8'))
        if cell.get('status')!='complete' or cell.get('run_protocol_sha256')!=protocol.get('sha256'):
            raise NeedsReview('Complete cell does not match its frozen run protocol')

''' + controller[end:]
old_call = "validate_startup_review(previous,(CONTROL/'history.json').read_bytes() if (CONTROL/'history.json').exists() else b'[]',(ROOT/'logs/r260401_brjd_20261003_r4.log').read_bytes() if previous.get('state')=='reviewed_no_measurement' else b'')"
new_call = "validate_recovery_review(previous,(CONTROL/'history.json').read_bytes() if (CONTROL/'history.json').exists() else b'[]',(ROOT/'logs/controller_v9.err.log').read_bytes() if previous.get('state')=='reviewed_partial_resume' else b'')"
assert old_call in controller
controller = controller.replace(old_call,new_call)
controller = controller.replace(
    "state='needs_review' if previous.get('state') in REVIEW_STATES else 'failed'",
    "state='needs_review' if (previous.get('state') in REVIEW_STATES or previous.get('state') in ('reviewed_partial_resume','reviewed_no_measurement')) else 'failed'")
for old,new in (('controller_v9.py','controller_v10.py'),('FILE_MANIFEST_v9.json','FILE_MANIFEST_v10.json'),
                ('queue_v9.json','queue_v10.json'),('common_guard_v9.py','common_guard_v10.py'),
                ('v9 manifest/queue/guard routes','v10 manifest/queue/guard routes')):
    controller = controller.replace(old,new)
guard = (SOURCE/'scripts/common_guard_v9.py').read_text(encoding='utf-8')
guard = guard.replace('FILE_MANIFEST_v9.json','FILE_MANIFEST_v10.json')
guard = guard.replace("manifest.get('control_version')!=9", "manifest.get('control_version')!=10")
queue = json.loads((SOURCE/'queue_v9.json').read_text(encoding='utf-8'))
queue['control_version'] = 10
queue['protocol'] = 'BRJD_EXECUTION_PROTOCOL_v10.json'
protocol = json.loads((SOURCE/'BRJD_EXECUTION_PROTOCOL_v9.json').read_text(encoding='utf-8'))
protocol['control_version'] = 10
protocol['maintenance'] = ('v9 controller failed on Windows atomic guard replacement WinError5 during partial R4. '
    'v10 retries only os.replace WinError5/32/33 for at most2s at50ms intervals. Persistent conflicts '
    'still fail closed; heartbeat max age remains20s. Existing science strict capacity/protocol/cell '
    'resume preserves preflight, calibration and the two audited complete cells.')
protocol.pop('startup_review',None)
protocol['atomic_json_policy'] = {'operation':'os.replace','retry_winerror':[5,32,33],
    'max_retry_seconds':2,'retry_interval_seconds':0.05,'max_retries':40,
    'other_errors_immediately_raise':True,'target_never_truncated':True,
    'persistent_failure_still_fail_closed':True,'heartbeat_max_age_seconds':20}
protocol['partial_recovery_review'] = {'proof':'RECOVERY_REVIEW_v10.json',
    'original_failed_status':'control_validation/R4_v9_FAILED_STATUS.json',
    'controller_error_log':'control_validation/R4_v9_CONTROLLER_ERRLOG.log',
    'evidence':'control_validation/R4_v9_PARTIAL_RECOVERY_EVIDENCE.json',
    'snapshot_root':'control_validation/R4_v9_PARTIAL_SNAPSHOT',
    'status':'reviewed_partial_resume','science_workload_was_launched':True,
    'complete_cells_preserved':2,'exact_history_rows':3,
    'linux_live_bytes_verified_via_windows_unc':True,
    'future_failure_or_partial_byte_change_invalidates_review':True}
create('controller_v10.py',controller.encode('utf-8'))
create('scripts/common_guard_v10.py',guard.encode('utf-8'))
create('queue_v10.json',encoded(queue))
create('BRJD_EXECUTION_PROTOCOL_v10.json',encoded(protocol))
create('control_validation/R4_v9_FAILED_STATUS.json',(SOURCE/'control/status.json').read_bytes())
create('control_validation/R4_v9_CONTROLLER_ERRLOG.log',(SOURCE/'logs/controller_v9.err.log').read_bytes())
create('_test_inputs/history.json',(SOURCE/'control/history.json').read_bytes())
print(json.dumps({'status':'CREATED','stage':str(STAGE)},ensure_ascii=False))
