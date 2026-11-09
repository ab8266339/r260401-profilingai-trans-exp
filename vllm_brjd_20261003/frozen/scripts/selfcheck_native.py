"""CPU-only checks: no vLLM/torch import, no CUDA API, no subprocess launch."""
import ast, contextlib, importlib.util, io, json, os, socket, sys, tempfile, time
import hashlib
from pathlib import Path
from types import SimpleNamespace

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('brjd_native',(HERE/'rplus_brjd.py'))
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert 'torch' not in sys.modules and 'vllm' not in sys.modules
kv_ast=ast.parse((HERE/'kvbench.py').read_text(encoding='utf-8'))
cleanup_ast=ast.Module(body=[next(n for n in kv_ast.body if isinstance(n,ast.FunctionDef) and n.name=='stop_server')],type_ignores=[])
class FakeProcess:
    pid=4321
    _r260401_owned_pgid=4321
    def __init__(self): self.polled=0; self.waited=0
    def poll(self): self.polled+=1; return 17  # parent already exited
    def wait(self,timeout): self.waited+=1; return 17
class FakeOS:
    def __init__(self,exists): self.calls=[]; self.exists=exists
    def killpg(self,pgid,sig):
        self.calls.append((pgid,sig))
        if sig==0 and not self.exists: raise ProcessLookupError()
class FakeTime:
    def __init__(self,values): self.values=iter(values)
    def monotonic(self): return next(self.values)
    def sleep(self,seconds): pass
def cleanup_check(fakeos,faketime,process):
    namespace={'os':fakeos,'signal':SimpleNamespace(SIGINT=2,SIGKILL=9),'time':faketime,
               'subprocess':SimpleNamespace(TimeoutExpired=TimeoutError)}
    exec(compile(cleanup_ast,'owned_cleanup_only','exec'),namespace)
    namespace['stop_server'](process)
    return fakeos.calls
proc=FakeProcess()
assert cleanup_check(FakeOS(False),FakeTime([0,1]),proc)==[(4321,2),(4321,0)]
assert proc.polled==1  # child group cleared despite already exited parent
proc=FakeProcess()
assert cleanup_check(FakeOS(True),FakeTime([0,61]),proc)==[(4321,2),(4321,9)]
assert proc.waited==1
proc=FakeProcess(); proc._r260401_owned_pgid=999
try:
    cleanup_check(FakeOS(False),FakeTime([]),proc)
    raise AssertionError('unowned pgid accepted')
except RuntimeError as exc:
    assert 'unowned' in str(exc)
for name in ('rplus_brjd.py','kvbench.py'):
    ast.parse((HERE/name).read_text(encoding='utf-8'))

good={'schema_version':1,'queue_id':'r260401_brjd_20261003','unix_time':time.time(),
      'status':'running','active_job':'r260401_r2','foreign_processes':[],'gpu_used_mib':18000,'cpu_percent':10}
snap={'observed_unix':good['unix_time'],'record':good}
assert m.windows_guard_errors(snap)==[]
for change in ({'unix_time':good['unix_time']-21},{'status':'blocked'},
               {'foreign_processes':[{'pid':12}]},{'queue_id':'other'}, {'schema_version':0}):
    assert m.windows_guard_errors({'observed_unix':good['unix_time'],'record':dict(good,**change)})
assert m.windows_guard_errors(None)
inventory={'devices':[{'index':0,'uuid':'gpu0','utilization_percent':50}], 'processes':[], 'windows_guard':snap}
assert m.exclusive_errors(inventory,{0})==[]
assert m.exclusive_errors(dict(inventory,devices=inventory['devices']*4),{0})

stderr=io.StringIO()
with contextlib.redirect_stderr(stderr):
    try:
        m.parser().parse_args(['--task','r1','--model-key','8B','--model-path','x','--gpus','0','--out-root','x'])
        raise AssertionError('old host R1 incorrectly accepted')
    except SystemExit as exc:
        assert exc.code==2
    try:
        m.parser().parse_args(['--task','r2','--model-key','8B','--model-path','x','--gpus','0','--out-root','x','--parallel-reference','oldhost.json'])
        raise AssertionError('old host reference incorrectly accepted')
    except SystemExit as exc:
        assert exc.code==2

with tempfile.TemporaryDirectory(prefix='r260401-brjd-selfcheck-') as scratch:
    root=Path(scratch)
    m.atomic_json(root/'capacity_freeze.json',{'token_capacity':80000})
    args=SimpleNamespace(rep_start=0,reps=10,out_tokens=128,concurrency=4)
    def cell(rep,arm,natural=False):
        latency=(8 if arm=='A0' else 9) if natural else (10 if arm=='A0' else 12)
        return {'rep':rep,'arm':arm,'host':socket.gethostname(),'gpus':'0','observed_KV_capacity_tokens':80000,
                'status':'complete','pipeline_mean_seconds':latency+rep*.01,
                'ttft_mean_seconds':1.,'decode_mean_seconds':latency-1.,
                'sessions':[{'sess':s,'stages':[{'stage':k+1,'completion_tokens':64 if natural else 128,
                    'finish_reason':'stop' if natural else 'length','prompt_sha256':f'initial-{s}' if k==0 else f'{natural}-{s}-{k}'} for k in range(8)]} for s in range(4)]}
    for mode,natural in (('fixed',False),('natural',True)):
        for rep in range(10):
            for arm in ('A0','R'):
                m.atomic_json(root/mode/'cells'/f'{rep}_{arm}.json',cell(rep,arm,natural))
    report=m.fixed_natural_report(args,root,root/'fixed',root/'natural')
    assert report['generation_modes']['fixed']['A0_minus_R_seconds']['mean']==-2
    assert report['generation_modes']['natural']['A0_minus_R_seconds']['mean']==-1
    assert report['cache_contrast_change_natural_minus_fixed']['mean']==1
    assert report['comparisons']['A0']['pipeline_mean_seconds']['mean']==-2
    assert report['comparisons']['R']['pipeline_mean_seconds']['mean']==-3
    assert report['generation_modes']['natural']['EOS_or_other_stop_observed']
    assert not report['old_host_parallel_reference_used']
    broken=root/'natural'/'cells'/'0_A0.json'
    obj=json.loads(broken.read_text(encoding='utf-8')); obj['host']='foreign-host'; m.atomic_json(broken,obj)
    try:
        m.fixed_natural_report(args,root,root/'fixed',root/'natural')
        raise AssertionError('cross-host pair accepted')
    except RuntimeError as exc:
        assert 'cross-host' in str(exc)
    obj['host']=socket.gethostname(); obj['observed_KV_capacity_tokens']=70000; m.atomic_json(broken,obj)
    try:
        m.fixed_natural_report(args,root,root/'fixed',root/'natural')
        raise AssertionError('unequal capacity accepted')
    except RuntimeError as exc:
        assert 'capacities differ' in str(exc)
assert 'torch' not in sys.modules and 'vllm' not in sys.modules
validation={'status':'PASS','unix_time':time.time(),'gpu_launched':False,'torch_vllm_imported':False,
    'checks':['python AST syntax','Windows guard fail closed','old-host CLI forbidden','synthetic fixed/natural paired report',
        'cross-host/capacity pair rejection','owned orphan server group cleanup, graceful/escalated/unowned rejection'],
    'sha256':{name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in ('kvbench.py','rplus_brjd.py','run_r2_brjd.sh','run_r3_brjd.sh','run_r4_brjd.sh','BRJD_NATIVE_PROTOCOL.json')}}
(HERE/'NATIVE_CPU_VALIDATION.json').write_text(json.dumps(validation,indent=2),encoding='utf-8')
print('PASS CPU-only syntax, guard fail-closed, no old-host CLI, paired EOS report, cross-host/capacity rejection, owned orphan cleanup; no torch/vllm import')
