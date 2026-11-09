"""Validate the final partial-resume gate and unchanged science skip logic, CPU only."""
import ast
import copy
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType,SimpleNamespace
from unittest.mock import patch

STAGE=Path(__file__).resolve().parent
SOURCE=Path(r'E:\r260401-brjd-20261003')
assert os.name=='nt' and os.environ.get('COMPUTERNAME')=='DESKTOP-BRJD313'
assert not (SOURCE/'control/controller.lock').exists()
sys.dont_write_bytecode=True
proxy=ModuleType('proxy_bridge')
proxy.start_bridge=lambda: (_ for _ in ()).throw(AssertionError('No bridge in CPU tests'))
sys.modules['proxy_bridge']=proxy
spec=importlib.util.spec_from_file_location('controller10_recovery_cpu',STAGE/'controller_v10.py')
controller=importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
assert not controller.psutil.pid_exists(181076)


def sha(data):return hashlib.sha256(data).hexdigest()
def enc(value):return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')


class MemoryPath:
    def __init__(self,relative,values,label,reads,stats):
        self.relative,self.values,self.label,self.reads,self.stats=relative,values,label,reads,stats
    def __truediv__(self,relative):
        return MemoryPath(self.relative+'/'+relative if self.relative else relative,
            self.values,self.label,self.reads,self.stats)
    def __str__(self):return self.label+('\\'+self.relative.replace('/','\\') if self.relative else '')
    def read_bytes(self):
        self.reads.append(self.relative)
        if self.relative not in self.values:raise FileNotFoundError(self.relative)
        return self.values[self.relative]
    def stat(self):
        self.stats.append(self.relative)
        if self.relative not in self.values:raise FileNotFoundError(self.relative)
        return SimpleNamespace(st_size=len(self.values[self.relative]))


files=['RECOVERY_REVIEW_v10.json','control_validation/R4_v9_FAILED_STATUS.json',
    'control_validation/R4_v9_CONTROLLER_ERRLOG.log','control_validation/R4_v9_PARTIAL_RECOVERY_EVIDENCE.json',
    'control_validation/R4_v9_PARTIAL_INVENTORY.json','control_validation/R4_v10_PARTIAL_REVIEW.json',
    'control_validation/WARMUP_RECOVERY_v10.json','control_validation/R4_v9_HOST_GUARD.jsonl']
base={relative:(STAGE/relative).read_bytes() for relative in files}
base['scripts/rplus_brjd.py']=(SOURCE/'scripts/rplus_brjd.py').read_bytes()
proof=json.loads(base['RECOVERY_REVIEW_v10.json'].decode('utf-8'))
warmup=json.loads(base['control_validation/WARMUP_RECOVERY_v10.json'].decode('utf-8'))
original_history=(STAGE/'_test_inputs/history.json').read_bytes()
original_error=base['control_validation/R4_v9_CONTROLLER_ERRLOG.log']
live={}
for relative in proof['partial_files']:
    frozen=(STAGE/'control_validation/R4_v9_PARTIAL_SNAPSHOT'/relative).read_bytes()
    base['control_validation/R4_v9_PARTIAL_SNAPSHOT/'+relative]=frozen
    live['results/8B/r4/'+relative]=frozen
for action in warmup['actions']:
    relative=action['relative']
    del live['results/8B/r4/'+relative]
    base['control/warmup_quarantine_v9/'+Path(relative).name]=base['control_validation/R4_v9_PARTIAL_SNAPSHOT/'+relative]
checks=[]


def prohibited(*args,**kwargs):raise AssertionError('Actual subprocess/GPU/WSL calls forbidden')


def exercise(values=None,live_values=None,history=None,error=None,previous=None,reject=True):
    values=base if values is None else values
    live_values=live if live_values is None else live_values
    history=original_history if history is None else history
    error=original_error if error is None else error
    previous={'state':'reviewed_partial_resume','review_id':sha(values['RECOVERY_REVIEW_v10.json'])} if previous is None else previous
    root_reads=[];root_stats=[];live_reads=[];live_stats=[]
    with patch.object(controller,'ROOT',MemoryPath('',values,str(SOURCE),root_reads,root_stats)),\
         patch.object(controller,'RECOVERY_LINUX_FILES_ROOT',MemoryPath('',live_values,'WSL_FIXTURE',live_reads,live_stats)):
        try:controller.validate_recovery_review(previous,history,error)
        except controller.NeedsReview:
            assert reject
            return
    assert not reject,'Invalid partial recovery was accepted'
    assert len(live_reads)==20 and len(live_stats)==3
    assert len([r for r in root_reads if r.startswith('control_validation/R4_v9_PARTIAL_SNAPSHOT/')])==23


def rebound_warmup(mutator):
    values=dict(base)
    changed=json.loads(values['control_validation/WARMUP_RECOVERY_v10.json'].decode('utf-8'))
    mutator(changed)
    values['control_validation/WARMUP_RECOVERY_v10.json']=enc(changed)
    p=json.loads(values['RECOVERY_REVIEW_v10.json'].decode('utf-8'))
    p['warmup_recovery_sha256']=sha(enc(changed))
    r=json.loads(values['control_validation/R4_v10_PARTIAL_REVIEW.json'].decode('utf-8'))
    r['warmup_recovery_sha256']=p['warmup_recovery_sha256']
    values['control_validation/R4_v10_PARTIAL_REVIEW.json']=enc(r)
    p['partial_review_sha256']=sha(enc(r))
    e=json.loads(values['control_validation/R4_v9_PARTIAL_RECOVERY_EVIDENCE.json'].decode('utf-8'))
    e['warmup_recovery_sha256']=p['warmup_recovery_sha256'];e['partial_review_sha256']=p['partial_review_sha256']
    values['control_validation/R4_v9_PARTIAL_RECOVERY_EVIDENCE.json']=enc(e)
    p['recovery_evidence_sha256']=sha(enc(e))
    values['RECOVERY_REVIEW_v10.json']=enc(p)
    return values


with patch.object(subprocess,'Popen',prohibited),patch.object(subprocess,'run',prohibited):
    exercise(reject=False)
    checks.append('Exact final recovery validates23 frozen snapshots,20 unchanged live files,3 absent old markers and3 SHA-matching fixed-folder quarantine files')
    exercise(previous={'state':'reviewed_no_measurement','review_id':'old-v9-proof'})
    exercise(previous={'state':'reviewed_partial_resume','review_id':'0'*64})
    exercise(history=original_history+b'\n')
    history=json.loads(original_history.decode('utf-8'));history.append(dict(history[-1],started='future'))
    exercise(history=enc(history))
    exercise(error=original_error+b'new controller failure')
    for name in ('control_validation/R4_v9_FAILED_STATUS.json',
                 'control_validation/R4_v10_PARTIAL_REVIEW.json',
                 'control_validation/R4_v9_PARTIAL_INVENTORY.json',
                 'control_validation/R4_v9_PARTIAL_RECOVERY_EVIDENCE.json',
                 'control_validation/R4_v9_HOST_GUARD.jsonl'):
        values=dict(base);values[name]+=b'\n';exercise(values=values)
    checks.append('Obsolete no-measurement state/proof and history, future-failure, controller-error, original-status, evidence, inventory, review or host-guard byte drift rejected')
    for relative in proof['complete_cells']+['capacity_freeze.json','repetitions/r00/default/protocol.json']:
        changed=dict(live);changed['results/8B/r4/'+relative]+=b'\n';exercise(live_values=changed)
        changed=dict(base);changed['control_validation/R4_v9_PARTIAL_SNAPSHOT/'+relative]+=b'\n';exercise(values=changed)
    checks.append('Either complete cell, capacity freeze or protocol changing even one byte in live data or frozen snapshot is rejected before science launch')
    for action in warmup['actions']:
        relative=action['relative']
        changed=dict(live);changed['results/8B/r4/'+relative]=base['control_validation/R4_v9_PARTIAL_SNAPSHOT/'+relative]
        exercise(live_values=changed)
        changed=dict(base);changed['control/warmup_quarantine_v9/'+Path(relative).name]+=b'new'
        exercise(values=changed)
    checks.append('Restoring any stale warmup marker or changing any quarantined marker byte is rejected; only exact old marker paths may be absent')
    for mutator in (lambda w:w['actions'][0].update(relative='../rogue.done'),
                    lambda w:w['actions'][0].update(quarantine_windows=r'C:\other\t3400_c4_d8_R.done'),
                    lambda w:w['actions'][0].update(sha256='0'*64),
                    lambda w:w['actions'][0].update(original_marker_absent=False),
                    lambda w:w.update(repeated_timing_measurements_authorized=True),
                    lambda w:w['actions'].__setitem__(1,dict(w['actions'][0]))):
        exercise(values=rebound_warmup(mutator))
    checks.append('Even correspondingly rehashed malformed quarantine records fail: rogue/duplicate path, wrong folder/hash, restored marker or repeated timing authorization')

    # Extract and execute the unchanged real scientific resume function on frozen data.
    science=(SOURCE/'scripts/rplus_brjd.py').read_text(encoding='utf-8')
    tree=ast.parse(science)
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='freeze_common_capacity')
    snapshot=STAGE/'control_validation/R4_v9_PARTIAL_SNAPSHOT'
    freeze=json.loads((snapshot/'capacity_freeze.json').read_text(encoding='utf-8'))
    configuration=freeze['configuration']
    science_namespace={'hashlib':hashlib,'json':json,'__file__':str(SOURCE/'scripts/rplus_brjd.py'),
        'SCHEMA':configuration['schema'],
        'sha256_file':lambda path:sha(Path(path).read_bytes()),
        'socket':SimpleNamespace(gethostname=lambda:configuration['host']),
        'runtime_versions':lambda:configuration['runtime_versions'],
        'calibration_capacity':prohibited,'atomic_json':prohibited}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),'science_freeze_cpu','exec'),science_namespace)
    args=SimpleNamespace(**{key:configuration[key] for key in
        ('model_path','kv_dtype','gpus','tp','gpu_util','max_model_len','block_size','attention_backend','uncap_limit')})
    args.kvbench_path=str(SOURCE/'scripts/kvbench.py');args.token_capacity=configuration['explicit_capacity']
    old=SimpleNamespace(model_fingerprint=lambda path:configuration['model_fingerprint'])
    prior_freeze=(snapshot/'capacity_freeze.json').read_bytes()
    capacity=science_namespace['freeze_common_capacity'](old,args,snapshot,configuration['definitions'])
    assert capacity==43328 and (snapshot/'capacity_freeze.json').read_bytes()==prior_freeze
    args.gpu_util=.79
    try:science_namespace['freeze_common_capacity'](old,args,snapshot,configuration['definitions'])
    except RuntimeError as error:assert 'configuration changed' in str(error)
    else:raise AssertionError('Changed capacity signature accepted')
    checks.append('Actual unchanged freeze_common_capacity reuses43328-token calibration without launch/write and rejects a changed80% capacity signature')

    measure=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='measure')
    skip=next(n for n in ast.walk(measure) if isinstance(n,ast.If)
        and isinstance(n.test,ast.Call) and isinstance(n.test.func,ast.Attribute)
        and isinstance(n.test.func.value,ast.Name) and n.test.func.value.id=='destination'
        and n.test.func.attr=='exists')
    template=ast.parse('def resume_decision(destination, info):\n    for fixture in [None]:\n        pass\n        return "measure_missing"\n    return "skip_existing_complete"\n')
    template.body[0].body[0].body[0]=copy.deepcopy(skip)
    decision_namespace={'json':json}
    exec(compile(ast.parse(ast.unparse(template)),'science_cell_resume_cpu','exec'),decision_namespace)
    decision=decision_namespace['resume_decision']
    for relative in proof['complete_cells']:
        destination=snapshot/relative;prior=destination.read_bytes()
        assert decision(destination,{'protocol_sha256':json.loads(prior.decode('utf-8'))['run_protocol_sha256']})=='skip_existing_complete'
        assert destination.read_bytes()==prior
        try:decision(destination,{'protocol_sha256':'0'*64})
        except RuntimeError as error:assert 'cannot be overwritten' in str(error)
        else:raise AssertionError('Wrong protocol permitted cell replacement')
        assert destination.read_bytes()==prior
    assert decision(STAGE/'_test_runs/ABSENT_CELL.json',{'protocol_sha256':'0'*64})=='measure_missing'
    checks.append('Actual scientific cell-resume conditional skips both audited complete cells without modifying bytes, rejects mismatched protocol overwrite and measures only missing destination')

    # Ordinary UNC read/stat errors must make review sticky instead of failed.
    for initial_state in ('reviewed_partial_resume','reviewed_no_measurement'):
        for error in (FileNotFoundError('UNC fixture missing'),PermissionError('UNC fixture access denied')):
            status={'state':initial_state,'active_job':'r260401_brjd_20261003_r4'}
            captured=[]
            class StatusPath:
                def exists(self):return True
            class StatusControl:
                def __truediv__(self,name):return StatusPath()
            def capture(state,**extra):
                captured.append(state);status.clear();status.update(state=state,**extra)
            with patch.object(controller,'CONTROL',StatusControl()),\
                 patch.object(controller,'read_json',lambda path:dict(status)),\
                 patch.object(controller,'set_status',capture):
                controller.record_exception(error)
                assert status['state']=='needs_review' and status['active_job']=='r260401_brjd_20261003_r4'
                controller.validate_recovery_review(status,original_history,original_error)
                try:controller.require_previous_review(dict(status))
                except controller.NeedsReview:pass
                else:raise AssertionError('Restart bypassed sticky recovery review')
                assert status['state']=='needs_review'
    checks.append('Missing/access-denied UNC errors from either reviewed_partial_resume or obsolete reviewed_no_measurement become sticky needs_review; subsequent restart cannot bypass review')
    main=next(n for n in ast.parse((STAGE/'controller_v10.py').read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef) and n.name=='main')
    gate_calls={n.func.id:n.lineno for n in ast.walk(main) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
        and n.func.id in ('validate_recovery_review','require_previous_review')}
    job_loop=next(n for n in ast.walk(main) if isinstance(n,ast.For) and isinstance(n.target,ast.Name) and n.target.id=='job')
    assert gate_calls['validate_recovery_review']<gate_calls['require_previous_review']<job_loop.lineno
    checks.append('Real controller main validates recovery and enforces sticky previous review before entering any job loop')

atomic=json.loads((STAGE/'_test_runs_final_atomic2/ATOMIC_V10_VALIDATION.json').read_text(encoding='utf-8'))
assert atomic['status']=='PASS'
assert atomic['controller_sha256']==sha((STAGE/'controller_v10.py').read_bytes())
all_checks=atomic['checks']+checks
deployment=['controller_v10.py','scripts/common_guard_v10.py','queue_v10.json',
    'BRJD_EXECUTION_PROTOCOL_v10.json','RECOVERY_REVIEW_v10.json',
    'control_validation/R4_v9_FAILED_STATUS.json','control_validation/R4_v9_CONTROLLER_ERRLOG.log',
    'control_validation/R4_v9_PARTIAL_RECOVERY_EVIDENCE.json',
    'control_validation/R4_v9_PARTIAL_INVENTORY.json','control_validation/R4_v10_PARTIAL_REVIEW.json',
    'control_validation/WARMUP_RECOVERY_v10.json','control_validation/R4_v9_HOST_GUARD.jsonl']
deployment.extend('control_validation/R4_v9_PARTIAL_SNAPSHOT/'+relative for relative in proof['partial_files'])
report={'status':'PASS','project_id':'R260401','machine':'DESKTOP-BRJD313','control_version':10,
    'cpu_only':True,'gpu_accessed':False,'linux_python_probe':False,'controller_started':False,
    'science_workload_started':False,'no_actual_subprocess_launch':True,
    'native_windows_python':sys.executable,'controller_pid181076_absent':True,'controller_lock_absent':True,
    'heartbeat_max_age_seconds':20,'atomic_retry_seconds':2,'atomic_retry_interval_seconds':.05,
    'atomic_retry_winerrors':[5,32,33],'partial_snapshot_files':23,'live_unchanged_files':20,
    'quarantined_old_warmup_markers':3,'complete_timing_cells_preserved':2,
    'repeated_timing_measurements_authorized':False,'frozen_capacity_tokens':43328,
    'science_source_sha256':sha((SOURCE/'scripts/rplus_brjd.py').read_bytes()),
    'source_files_sha256':{relative:sha((STAGE/relative).read_bytes()) for relative in deployment},
    'atomic_validation_sha256':sha((STAGE/'_test_runs_final_atomic2/ATOMIC_V10_VALIDATION.json').read_bytes()),
    'review_validation_exceptions_sticky':True,
    'recovery_test_source_sha256':sha(Path(__file__).read_bytes()),'checks':all_checks,
    'manifest_included_in_report':False,'report_does_not_bind_own_sha':True,
    'measurement_claim_limit':proof['measurement_claim_limit']}
report_path=STAGE/'control_validation/VALIDATION_CONTROLLER_v10.json'
with report_path.open('w',encoding='utf-8',newline='\n') as handle:handle.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
deployment.append('control_validation/VALIDATION_CONTROLLER_v10.json')
listing={'project_id':'R260401','control_version':10,'deployment_file_count':len(deployment),
    'files':{relative:sha((STAGE/relative).read_bytes()) for relative in deployment},
    'stage':str(STAGE),'scope':'Only listed immutable payloads; excludes build/test helpers, mutable CPU fixtures and obsolete pre-freeze audit draft'}
with (STAGE/'STAGED_FILES_v10.json').open('w',encoding='utf-8',newline='\n') as handle:handle.write(json.dumps(listing,ensure_ascii=False,indent=2)+'\n')
explicit_listing={'project_id':'R260401','machine':'DESKTOP-BRJD313','control_version':10,'files':deployment}
with (STAGE/'DEPLOYMENT_FILES_v10.json').open('x',encoding='utf-8',newline='\n') as handle:handle.write(json.dumps(explicit_listing,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':'PASS','checks':len(all_checks),'deployment_files':len(deployment),
    'report_sha256':sha(report_path.read_bytes()),'proof_sha256':sha((STAGE/'RECOVERY_REVIEW_v10.json').read_bytes()),
    'deployment_listing':str(STAGE/'STAGED_FILES_v10.json')},indent=2))
