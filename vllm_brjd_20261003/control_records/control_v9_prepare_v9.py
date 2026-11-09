"""Read v8 and create new v9 sources in this Temp stage only, never deploy."""
import hashlib
import json
from pathlib import Path

SOURCE = Path(r'E:\r260401-brjd-20261003')
STAGE = Path(__file__).resolve().parent
EVIDENCE = Path(r'W:\ML\ml-work\R260401-profilingai-trans\vllm_brjd_20261003\control_records\r4_v8_startup_review_evidence_20261004.json')
EXPECTED_EVIDENCE_SHA = 'bc03e9cf138f7f197442c5d40a0ecd2131f49fe926536aaa536d8fb843302803'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def create(relative, data):
    target = STAGE / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as handle:
        handle.write(data)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


controller = (SOURCE / 'controller_v8.py').read_text(encoding='utf-8')
guard = (SOURCE / 'scripts/common_guard_v8.py').read_text(encoding='utf-8')
history_bytes = (SOURCE / 'control/history.json').read_bytes()
status_bytes = (SOURCE / 'control/status.json').read_bytes()
failure_bytes = (SOURCE / 'logs/r260401_brjd_20261003_r4.log').read_bytes()
evidence_bytes = EVIDENCE.read_bytes()
assert sha(evidence_bytes) == EXPECTED_EVIDENCE_SHA
history = json.loads(history_bytes.decode('utf-8'))
status = json.loads(status_bytes.decode('utf-8'))
evidence = json.loads(evidence_bytes.decode('utf-8'))
assert len(history) == 3
assert history[0]['tag'] == 'r260401_brjd_20261003_preflight'
assert history[0]['returncode'] == 0 and not history[0].get('guard_violation')
assert all(row['tag'] == 'r260401_brjd_20261003_r4' and row['returncode'] == 1
           and not row.get('guard_violation') for row in history[1:])
assert status['state'] == 'needs_review' and status['returncode'] == 1
assert status['active_job'] == 'r260401_brjd_20261003_r4'
assert evidence['state'] == status
assert evidence['failure_log_sha256'] == sha(failure_bytes)
assert evidence['controller_exited'] and evidence['controller_lock_absent']
assert evidence['scientific_workload_not_launched']
assert evidence['r4_output_directory_absent'] and evidence['r4_linux_log_count'] == 0

startup_begin = guard.index('def wait_for_linux_startup():')
startup_end = guard.index('def merge_known_owned(', startup_begin)
guard = guard[:startup_begin] + '''def wait_for_linux_startup():
    # Desktop GPU/memory activity remains pending. Foreign Linux work alone has
    # a continuous180-second timeout; every sample checks a fresh Windows guard.
    idle_since=None;foreign_since=None
    while True:
        info=check(include_linux=False)
        foreign=foreign_linux_processes();tick=time.monotonic()
        if foreign:
            if foreign_since is None:foreign_since=tick
        else:foreign_since=None
        resources_idle=(info['gpu_used_mib']<=6000 and info['gpu_utilization_percent']<=5)
        if foreign or not resources_idle:
            idle_since=None
            print(json.dumps({'kind':'wsl_startup_wait','foreign_processes':foreign,
                'gpu_used_mib':info['gpu_used_mib'],
                'gpu_utilization_percent':info['gpu_utilization_percent'],
                'wait_reason':'foreign_linux_work' if foreign else 'gpu_or_memory_busy',
                'foreign_wait_seconds':tick-foreign_since if foreign_since is not None else 0,
                'unix_time':time.time()}),flush=True)
        elif idle_since is None:idle_since=tick
        if idle_since is not None and tick-idle_since>=30:
            confirmed=check(include_linux=False)
            final_foreign=foreign_linux_processes();confirmed_tick=time.monotonic()
            if (not final_foreign and confirmed['gpu_used_mib']<=6000
                and confirmed['gpu_utilization_percent']<=5):
                return confirmed
            idle_since=None
            if final_foreign:
                if foreign_since is None:foreign_since=confirmed_tick
            else:foreign_since=None
        if foreign_since is not None and tick-foreign_since>=180:
            raise RuntimeError('WSL startup foreign isolation timeout; foreign='+json.dumps(foreign))
        time.sleep(2)

''' + guard[startup_end:]
guard = guard.replace('FILE_MANIFEST_v8.json', 'FILE_MANIFEST_v9.json')
guard = guard.replace("manifest.get('control_version')!=8", "manifest.get('control_version')!=9")

review_begin = controller.index('def validate_startup_review(')
review_end = controller.index('def main():', review_begin)
controller = controller[:review_begin] + '''def validate_startup_review(previous,history_bytes,failure_bytes):
    if previous.get('state')!='reviewed_no_measurement':return
    data=(ROOT/'R4_STARTUP_REVIEW_v9.json').read_bytes()
    proof=json.loads(data.decode('utf-8'))
    if previous.get('review_id')!=hashlib.sha256(data).hexdigest():
        raise NeedsReview('Startup review identity mismatch')
    if (proof.get('project_id')!='R260401' or proof.get('machine')!='DESKTOP-BRJD313'
        or proof.get('control_version')!=9 or proof.get('reviewed_failed_control_version')!=8
        or proof.get('failed_tag')!='r260401_brjd_20261003_r4'
        or proof.get('science_workload_not_launched') is not True
        or proof.get('preflight_reused') is not True
        or hashlib.sha256(history_bytes).hexdigest()!=proof.get('history_sha256')
        or hashlib.sha256(failure_bytes).hexdigest()!=proof.get('failure_log_sha256')):
        raise NeedsReview('Startup review is stale or belongs to another failure')
    original=(ROOT/'control_validation/R4_v8_FAILED_STATUS.json').read_bytes()
    if hashlib.sha256(original).hexdigest()!=proof.get('original_status_sha256'):
        raise NeedsReview('Original failed status changed')
    original_state=json.loads(original.decode('utf-8'))
    if (original_state.get('state')!='needs_review'
        or original_state.get('queue_id')!=QUEUE_ID
        or original_state.get('machine')!='DESKTOP-BRJD313'
        or original_state.get('active_job')!=proof['failed_tag']
        or original_state.get('returncode')!=1):
        raise NeedsReview('Original v8 failed status has the wrong identity')
    evidence_bytes=(ROOT/'control_validation/R4_v8_STARTUP_REVIEW_EVIDENCE.json').read_bytes()
    if hashlib.sha256(evidence_bytes).hexdigest()!=proof.get('review_evidence_sha256'):
        raise NeedsReview('Startup review evidence changed')
    evidence=json.loads(evidence_bytes.decode('utf-8'))
    if (evidence.get('project_id')!='R260401' or evidence.get('machine')!='DESKTOP-BRJD313'
        or evidence.get('state')!=original_state
        or evidence.get('controller_exited') is not True
        or evidence.get('controller_lock_absent') is not True
        or evidence.get('scientific_workload_not_launched') is not True
        or evidence.get('r4_output_directory_absent') is not True
        or evidence.get('r4_linux_log_count')!=0
        or evidence.get('failure_log_sha256')!=proof['failure_log_sha256']):
        raise NeedsReview('Startup review does not support an unlaunched workload')
    rows=json.loads(history_bytes.decode('utf-8'))
    if (not isinstance(rows,list) or len(rows)!=3 or proof.get('history_row_count')!=3
        or rows[0].get('tag')!='r260401_brjd_20261003_preflight'
        or rows[0].get('returncode')!=0 or rows[0].get('guard_violation')
        or any(row.get('tag')!=proof['failed_tag'] or row.get('returncode')!=1
               or row.get('guard_violation') for row in rows[1:])):
        raise NeedsReview('Unexpected attempted measurement history')

''' + controller[review_end:]
for old, new in (('controller_v8.py','controller_v9.py'),
                 ('FILE_MANIFEST_v8.json','FILE_MANIFEST_v9.json'),
                 ('queue_v8.json','queue_v9.json'),
                 ('common_guard_v8.py','common_guard_v9.py'),
                 ('v8 manifest/queue/guard routes','v9 manifest/queue/guard routes')):
    controller = controller.replace(old, new)

queue = json.loads((SOURCE / 'queue_v8.json').read_text(encoding='utf-8'))
queue['control_version'] = 9
queue['protocol'] = 'BRJD_EXECUTION_PROTOCOL_v9.json'
assert queue['gpu_memory_utilization'] == 0.8
protocol = json.loads((SOURCE / 'BRJD_EXECUTION_PROTOCOL_v8.json').read_text(encoding='utf-8'))
protocol['control_version'] = 9
protocol['maintenance'] = ('v7 and v8 R4 failed before scientific workload Popen; v9 keeps '
    'GPU/memory startup wait pending without a total deadline, while continuous foreign Linux '
    'work still fails after180s and resets its timer when clear. Fresh Windows guard checked '
    'every2s; workload starts only after continuous30s idle. Post-Popen runtime unchanged.')
protocol['startup_gate'] = {'poll_seconds':2,'fresh_windows_heartbeat_max_age_seconds':20,
    'idle_stability_seconds':30,'gpu_or_memory_wait_timeout_seconds':None,
    'continuous_foreign_linux_timeout_seconds':180,'foreign_timer_reset_when_clear':True,
    'post_popen_runtime_unchanged':True}
protocol['startup_review'] = {'proof':'R4_STARTUP_REVIEW_v9.json',
    'original_failed_status':'control_validation/R4_v8_FAILED_STATUS.json',
    'evidence':'control_validation/R4_v8_STARTUP_REVIEW_EVIDENCE.json',
    'exact_history_rows':3,'successful_preflight_rows':1,'failed_r4_rows':2,
    'future_failure_invalidates_review':True,'no_traceback_claim':True}

proof = {'project_id':'R260401','machine':'DESKTOP-BRJD313','control_version':9,
    'reviewed_failed_control_version':8,'failed_tag':'r260401_brjd_20261003_r4',
    'science_workload_not_launched':True,
    'scope':'One v9 retry of the reviewed v8 startup failure before science Popen; exact three-row history retains the earlier failed v7 attempt and successful preflight',
    'history_row_count':3,'history_sha256':sha(history_bytes),
    'original_status_sha256':sha(status_bytes),'failure_log_sha256':sha(failure_bytes),
    'review_evidence_sha256':sha(evidence_bytes),
    'review_evidence_local':'control_validation/R4_v8_STARTUP_REVIEW_EVIDENCE.json',
    'review_evidence_archive':str(EVIDENCE),
    'cause_evidence':evidence['cause_evidence'],
    'timeout_diagnosis':'Supported by source and timing; no traceback was captured',
    'preflight_reused':True,'scientific_parameters_changed':False}

create('controller_v9.py', controller.encode('utf-8'))
create('scripts/common_guard_v9.py', guard.encode('utf-8'))
create('queue_v9.json', json_bytes(queue))
create('BRJD_EXECUTION_PROTOCOL_v9.json', json_bytes(protocol))
create('R4_STARTUP_REVIEW_v9.json', json_bytes(proof))
create('control_validation/R4_v8_FAILED_STATUS.json', status_bytes)
create('control_validation/R4_v8_STARTUP_REVIEW_EVIDENCE.json', evidence_bytes)
create('_test_inputs/history.json', history_bytes)
create('_test_inputs/r4_failure.log', failure_bytes)
create('_test_inputs/R4_STARTUP_REVIEW_v8.json', (SOURCE/'R4_STARTUP_REVIEW_v8.json').read_bytes())
print(json.dumps({'stage':str(STAGE),'status':'CREATED',
    'files':{relative:sha((STAGE/relative).read_bytes()) for relative in
        ('controller_v9.py','scripts/common_guard_v9.py','queue_v9.json',
         'BRJD_EXECUTION_PROTOCOL_v9.json','R4_STARTUP_REVIEW_v9.json',
         'control_validation/R4_v8_FAILED_STATUS.json',
         'control_validation/R4_v8_STARTUP_REVIEW_EVIDENCE.json')}}))
