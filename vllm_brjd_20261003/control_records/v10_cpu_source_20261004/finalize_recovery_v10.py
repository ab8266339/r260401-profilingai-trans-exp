"""Freeze parent-audited partial results and create one-use partial resume proof."""
import hashlib
import json
from pathlib import Path

STAGE=Path(__file__).resolve().parent
PARTIAL=Path(r'C:\Users\admin\AppData\Local\Temp\r260401-brjd-20261004-partial-v9')
INVENTORY_SHA='68faa9f34bd3ab1051b76dc238b158f4f4ccac9102e044fbaa6ab06c9df83de3'
REVIEW_SHA='75ff86ed254f7ca2861189c99f3e0465ad733262b9068bb980d421ffb8f8b5fb'


def sha(data):return hashlib.sha256(data).hexdigest()


def encoded(value):return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')


def create(relative,data):
    path=STAGE/relative
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as handle:handle.write(data)


inventory_bytes=(PARTIAL/'partial_inventory_v9.json').read_bytes()
review_bytes=(PARTIAL/'PARTIAL_REVIEW_v9.json').read_bytes()
assert sha(inventory_bytes)==INVENTORY_SHA and sha(review_bytes)==REVIEW_SHA
inventory=json.loads(inventory_bytes.decode('utf-8'))
review=json.loads(review_bytes.decode('utf-8'))
assert inventory['file_count']==23 and len(inventory['files'])==23
assert review['resume_authorized'] is True and review['retain_complete_cells'] is True
assert review['scientific_workload_did_run'] is True
assert review['partial_inventory_sha256']==INVENTORY_SHA
assert review['resume_basis'] and len(review['complete_cells'])==2
assert sha((STAGE/'control_validation/R4_v9_FAILED_STATUS.json').read_bytes())==inventory['original_status_sha256']
assert sha((STAGE/'control_validation/R4_v9_CONTROLLER_ERRLOG.log').read_bytes())==inventory['controller_error_sha256']
assert sha((STAGE/'_test_inputs/history.json').read_bytes())==inventory['history_sha256']
assert inventory['original_status_sha256']==review['original_status_sha256']
assert inventory['controller_error_sha256']==review['controller_error_sha256']
assert inventory['history_sha256']==review['history_sha256']
files={}
for relative,metadata in inventory['files'].items():
    assert not Path(relative).is_absolute() and '..' not in Path(relative).parts and '\\' not in relative
    data=(PARTIAL/'R4_snapshot'/relative).read_bytes()
    assert len(data)==metadata['bytes'] and sha(data)==metadata['sha256']
    create('control_validation/R4_v9_PARTIAL_SNAPSHOT/'+relative,data)
    files[relative]=metadata['sha256']
for relative in review['complete_cells']:
    assert files[relative]==review['complete_cells_sha256'][relative]
    cell=json.loads((PARTIAL/'R4_snapshot'/relative).read_text(encoding='utf-8'))
    assert cell['status']=='complete' and cell['run_protocol_sha256']==review['run_protocol_sha256']

create('control_validation/R4_v9_PARTIAL_INVENTORY.json',inventory_bytes)
create('control_validation/R4_v9_PARTIAL_REVIEW.json',review_bytes)
guard_bytes=(PARTIAL/'r4_v9_host_guard.jsonl').read_bytes()
create('control_validation/R4_v9_HOST_GUARD.jsonl',guard_bytes)
evidence={'project_id':'R260401','machine':'DESKTOP-BRJD313','failed_control_version':9,
    'science_workload_was_launched':True,'partial_resume':True,
    'resume_root':inventory['resume_root'],'partial_files':files,
    'complete_cells':review['complete_cells'],'partial_inventory_sha256':INVENTORY_SHA,
    'partial_review_sha256':REVIEW_SHA,'host_guard_sha256':sha(guard_bytes),
    'preflight_reused':True,'calibration_reused':True,
    'existing_complete_cells_preserved':True,'only_missing_cells_measured':True,
    'science_source_unchanged':True,'scientific_parameters_changed':False,
    'failure_reason':review['failure_reason'],
    'measurement_claim_limit':review['audit']['token_drift'],
    'resume_basis':review['resume_basis']}
evidence_bytes=encoded(evidence)
create('control_validation/R4_v9_PARTIAL_RECOVERY_EVIDENCE.json',evidence_bytes)
proof={'project_id':'R260401','machine':'DESKTOP-BRJD313','control_version':10,
    'reviewed_failed_control_version':9,'failed_tag':'r260401_brjd_20261003_r4',
    'scope':'One v10 resume of the reviewed v9 Windows sharing-lock crash; preserve audited partial science and measure only missing cells',
    'science_workload_was_launched':True,'partial_resume':True,
    'preflight_reused':True,'calibration_reused':True,'resume_root':inventory['resume_root'],
    'history_row_count':3,'history_sha256':inventory['history_sha256'],
    'original_status_sha256':inventory['original_status_sha256'],
    'controller_error_log_sha256':inventory['controller_error_sha256'],
    'partial_inventory_sha256':INVENTORY_SHA,'partial_review_sha256':REVIEW_SHA,
    'recovery_evidence_sha256':sha(evidence_bytes),'host_guard_sha256':sha(guard_bytes),
    'partial_files':files,'complete_cells':review['complete_cells'],
    'snapshot_root':'control_validation/R4_v9_PARTIAL_SNAPSHOT',
    'scientific_parameters_changed':False,'science_source_sha256':review['source_science_sha256'],
    'measurement_claim_limit':review['audit']['token_drift'],
    'resume_basis':review['resume_basis']}
create('RECOVERY_REVIEW_v10.json',encoded(proof))
print(json.dumps({'status':'CREATED','partial_snapshot_files':len(files),
    'proof_sha256':sha((STAGE/'RECOVERY_REVIEW_v10.json').read_bytes())},indent=2))
