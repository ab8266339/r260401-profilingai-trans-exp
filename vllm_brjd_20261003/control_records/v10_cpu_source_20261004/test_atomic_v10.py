"""Native Windows CPU-only, fault-injected atomic replacement validation."""
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from unittest.mock import patch

STAGE = Path(__file__).resolve().parent
SOURCE = Path(r'E:\r260401-brjd-20261003')
assert os.name=='nt' and os.environ.get('COMPUTERNAME')=='DESKTOP-BRJD313'
assert not (SOURCE/'control/controller.lock').exists()
sys.dont_write_bytecode=True
proxy=ModuleType('proxy_bridge')
proxy.start_bridge=lambda: (_ for _ in ()).throw(AssertionError('No network bridge in CPU tests'))
sys.modules['proxy_bridge']=proxy


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


controller=load('controller10_atomic_cpu',STAGE/'controller_v10.py')
guard=load('guard10_atomic_cpu',STAGE/'scripts/common_guard_v10.py')
assert not controller.psutil.pid_exists(181076)
runs=STAGE/(sys.argv[1] if len(sys.argv)>1 else '_test_runs')
assert runs.resolve().is_relative_to(STAGE.resolve())
runs.mkdir(exist_ok=True)
checks=[]
old={'generation':'old','text':'原文件完整'}
new={'generation':'new','text':'共享锁重试后完整','items':list(range(100))}


def prohibited(*args,**kwargs):
    raise AssertionError('Actual subprocess/GPU/WSL calls forbidden')


class Clock:
    def __init__(self):self.tick=0;self.sleeps=[]
    def sleep(self,duration):
        assert 0<duration<=.05
        self.sleeps.append(duration);self.tick+=duration


def conflict(code):
    error=PermissionError('Fault-injected sharing conflict')
    if code is not None:error.winerror=code
    return error


def exercise(name,code=None,failures=0,persistent=False):
    folder=runs/name
    folder.mkdir()
    target=folder/'state.json'
    target.write_text(json.dumps(old,ensure_ascii=False),encoding='utf-8')
    prior=target.read_bytes()
    clock=Clock();attempts=[];real_replace=os.replace
    def fake_replace(source,destination):
        attempts.append(clock.tick)
        assert json.loads(Path(source).read_text(encoding='utf-8'))==new
        assert Path(destination).read_bytes()==prior
        if persistent or len(attempts)<=failures:raise conflict(code)
        return real_replace(source,destination)
    with patch.object(controller.os,'replace',fake_replace),\
         patch.object(controller.time,'monotonic',lambda:clock.tick),\
         patch.object(controller.time,'sleep',clock.sleep):
        if persistent:
            try:controller.atomic_json(target,new)
            except OSError as error:
                assert getattr(error,'winerror',None)==code
            else:raise AssertionError('Persistent fault must raise')
            assert target.read_bytes()==prior
        else:
            controller.atomic_json(target,new)
            assert json.loads(target.read_text(encoding='utf-8'))==new
            assert not target.with_suffix('.json.tmp').exists()
    assert clock.tick<=2.0 and len(attempts)<=41
    return clock,attempts


with patch.object(subprocess,'Popen',prohibited),patch.object(subprocess,'run',prohibited):
    for code in (5,32,33):
        clock,attempts=exercise('transient_'+str(code),code,failures=3)
        assert len(attempts)==4 and len(clock.sleeps)==3 and abs(clock.tick-.15)<1e-8
    checks.append('WinError5/32/33 transient faults retry every50ms and atomically publish complete UTF8 JSON after success')
    for code in (5,32,33):
        clock,attempts=exercise('persistent_'+str(code),code,persistent=True)
        assert abs(clock.tick-2.0)<1e-8 and len(attempts)==41
    checks.append('Persistent WinError5/32/33 raises after bounded2s/40 retries, preserves original target byte-for-byte and keeps complete temp JSON')
    for code in (2,87,112,None):
        clock,attempts=exercise('unknown_'+str(code),code,persistent=True)
        assert len(attempts)==1 and not clock.sleeps and clock.tick==0
    checks.append('WinError2/87/112 and PermissionError without recognized winerror raise immediately, with no retry or target truncation')

    malformed=runs/'serialization_error.json'
    malformed.write_text(json.dumps(old,ensure_ascii=False),encoding='utf-8')
    before=malformed.read_bytes();replace_calls=[]
    with patch.object(controller.os,'replace',lambda *args:replace_calls.append(args)):
        try:controller.atomic_json(malformed,{'bad':object()})
        except TypeError:pass
        else:raise AssertionError('Serialization error should raise')
    assert malformed.read_bytes()==before and not replace_calls
    checks.append('Serialization failure immediately raises before replacing or truncating the existing target')

    # Check the actual Linux guard freshness function with CPU-only fake file data.
    class GuardJSON:
        def __init__(self,age):self.age=age
        def read_text(self,encoding):
            assert encoding=='utf-8'
            return json.dumps({'schema_version':1,'queue_id':guard.QUEUE,
                'unix_time':100-self.age,'status':'running','foreign_processes':[]})
    with patch.object(guard.time,'time',lambda:100):
        for age in (-5,20):
            with patch.object(guard,'GUARD',GuardJSON(age)):guard.check(include_linux=False)
        for age in (-6,21):
            with patch.object(guard,'GUARD',GuardJSON(age)):
                try:guard.check(include_linux=False)
                except AssertionError as error:assert 'Guard heartbeat stale' in str(error)
                else:raise AssertionError('Out-of-bound heartbeat accepted')
    checks.append('Actual guard freshness boundaries unchanged: age20s and future5s accepted; age21s and future6s fail closed')

    builtin_path=runs/'controller_selfcheck.json'
    with contextlib.redirect_stdout(io.StringIO()):controller.selfcheck(str(builtin_path))
    builtin=json.loads(builtin_path.read_text(encoding='utf-8'))
    assert builtin['status']=='PASS'
    checks.extend(builtin['checks'])

old_guard=(SOURCE/'scripts/common_guard_v9.py').read_text(encoding='utf-8')
new_guard=(STAGE/'scripts/common_guard_v10.py').read_text(encoding='utf-8')
assert old_guard.replace('FILE_MANIFEST_v9.json','FILE_MANIFEST_v10.json').replace(
    "manifest.get('control_version')!=9","manifest.get('control_version')!=10")==new_guard
old_queue=json.loads((SOURCE/'queue_v9.json').read_text(encoding='utf-8'))
new_queue=json.loads((STAGE/'queue_v10.json').read_text(encoding='utf-8'))
assert {key:value for key,value in old_queue.items() if key not in ('control_version','protocol')}=={
    key:value for key,value in new_queue.items() if key not in ('control_version','protocol')}
assert new_queue['gpu_memory_utilization']==.8
checks.append('Guard changes only immutable manifest version routing; startup/runtime/cleanup unchanged; science queue/args and80% GPU unchanged')

report={'status':'PASS','project_id':'R260401','machine':'DESKTOP-BRJD313','control_version':10,
    'cpu_only':True,'gpu_accessed':False,'linux_python_probe':False,'controller_started':False,
    'no_actual_subprocess_launch':True,'native_windows_python':sys.executable,
    'replacement_retry_seconds':2,'replacement_retry_interval_seconds':.05,
    'replacement_retry_winerror':[5,32,33],'heartbeat_max_age_seconds':20,
    'controller_sha256':hashlib.sha256((STAGE/'controller_v10.py').read_bytes()).hexdigest(),
    'test_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'checks':checks}
path=runs/'ATOMIC_V10_VALIDATION.json'
with path.open('x',encoding='utf-8',newline='\n') as handle:
    handle.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':'PASS','checks':len(checks),'report':str(path),
    'report_sha256':hashlib.sha256(path.read_bytes()).hexdigest()},indent=2))
