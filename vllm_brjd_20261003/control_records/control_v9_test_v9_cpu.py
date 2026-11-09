"""Windows CPU-only validation: fake clocks, guard JSON and Linux inventory."""
import ast
import contextlib
import copy
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
assert os.name == 'nt', 'These tests must run in native Windows Python'
assert os.environ.get('COMPUTERNAME') == 'DESKTOP-BRJD313'
assert not (SOURCE/'control/controller.lock').exists(), 'Controller must be stopped'
sys.dont_write_bytecode = True
proxy_stub = ModuleType('proxy_bridge')
proxy_stub.start_bridge = lambda: (_ for _ in ()).throw(AssertionError('No bridge start in CPU tests'))
sys.modules['proxy_bridge'] = proxy_stub


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


controller = load('controller_v9_cpu', STAGE/'controller_v9.py')
guard = load('guard_v9_cpu', STAGE/'scripts/common_guard_v9.py')
assert not controller.psutil.pid_exists(210828), 'Reviewed controller PID must remain stopped'
checks = []
prohibited_calls = []


def prohibited(*args, **kwargs):
    prohibited_calls.append(str(args[:1]))
    raise AssertionError('Real subprocess/GPU/WSL calls are forbidden in CPU tests')


class FakeClock:
    def __init__(self):
        self.tick = 0
        self.sleeps = []

    def sleep(self, seconds):
        assert seconds == 2, 'Startup guard must sample every2 seconds'
        self.sleeps.append(seconds)
        self.tick += seconds
        assert self.tick <= 1200, 'Fixture did not terminate'


class GuardReader:
    def __init__(self, clock, resource, stale_after=None):
        self.clock, self.resource, self.stale_after = clock, resource, stale_after
        self.reads = []

    def read_text(self, encoding):
        assert encoding == 'utf-8'
        tick = self.clock.tick
        self.reads.append(tick)
        used, utilization = self.resource(tick)
        stale = self.stale_after is not None and tick >= self.stale_after
        return json.dumps({'schema_version':1,'queue_id':guard.QUEUE,
            'unix_time':10000+tick-(21 if stale else 0),
            'status':'running','foreign_processes':[],
            'gpu_used_mib':used,'gpu_utilization_percent':utilization,
            'active_job':'r260401_brjd_20261003_r4',
            'proxy_url':'http://127.0.0.1:27890'})


def gate_case(resource, foreign, stale_after=None, main=False):
    clock = FakeClock()
    reader = GuardReader(clock, resource, stale_after)
    starts = []
    result = None

    class GateComplete(Exception):
        pass

    def fake_popen(argv, **kwargs):
        assert argv == ['science-fixture']
        assert clock.tick >= 30
        starts.append(clock.tick)
        raise GateComplete()

    with contextlib.ExitStack() as stack:
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stack.enter_context(patch.object(guard, 'GUARD', reader))
        stack.enter_context(patch.object(guard, 'foreign_linux_processes', lambda: foreign(clock.tick)))
        stack.enter_context(patch.object(guard.time, 'monotonic', lambda: clock.tick))
        stack.enter_context(patch.object(guard.time, 'time', lambda: 10000+clock.tick))
        stack.enter_context(patch.object(guard.time, 'sleep', clock.sleep))
        if main:
            stack.enter_context(patch.object(guard, 'ROOT', STAGE/'_test_runs/main_gate'))
            stack.enter_context(patch.object(guard, 'verify_payload', lambda: {'cpu_fixture':True}))
            stack.enter_context(patch.object(sys, 'argv', ['common_guard_v9.py','--timeout','22000','--','science-fixture']))
            stack.enter_context(patch.object(subprocess, 'Popen', fake_popen))
            try:
                guard.main()
            except GateComplete:
                result = 'gate_complete'
        else:
            result = guard.wait_for_linux_startup()
    assert all(right-left <= 2 for left,right in zip(reader.reads, reader.reads[1:]))
    return clock, reader, result, starts


class FakePath:
    def __init__(self, relative, values):
        self.relative, self.values = relative, values

    def read_bytes(self):
        return self.values[self.relative]


class FakeRoot:
    def __init__(self, values):
        self.values = values

    def __truediv__(self, relative):
        return FakePath(relative, self.values)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')


def review_case(history_bytes=None, failure_bytes=None, proof=None, previous=None,
                original=None, evidence=None, rejection=True):
    history_bytes = (STAGE/'_test_inputs/history.json').read_bytes() if history_bytes is None else history_bytes
    failure_bytes = (STAGE/'_test_inputs/r4_failure.log').read_bytes() if failure_bytes is None else failure_bytes
    proof_bytes = (STAGE/'R4_STARTUP_REVIEW_v9.json').read_bytes() if proof is None else encoded(proof)
    original = (STAGE/'control_validation/R4_v8_FAILED_STATUS.json').read_bytes() if original is None else original
    evidence = (STAGE/'control_validation/R4_v8_STARTUP_REVIEW_EVIDENCE.json').read_bytes() if evidence is None else evidence
    values = {'R4_STARTUP_REVIEW_v9.json':proof_bytes,
        'control_validation/R4_v8_FAILED_STATUS.json':original,
        'control_validation/R4_v8_STARTUP_REVIEW_EVIDENCE.json':evidence}
    previous = {'state':'reviewed_no_measurement','review_id':sha(proof_bytes)} if previous is None else previous
    with patch.object(controller, 'ROOT', FakeRoot(values)):
        try:
            controller.validate_startup_review(previous, history_bytes, failure_bytes)
        except controller.NeedsReview:
            assert rejection
            return
    assert not rejection, 'Invalid or stale proof was accepted'


with patch.object(subprocess, 'Popen', prohibited), patch.object(subprocess, 'run', prohibited):
    # Existing controller selfchecks remain real function tests with OS/GPU stubs.
    builtin_path = STAGE/'_test_runs/controller_selfcheck.json'
    builtin_path.parent.mkdir(parents=True,exist_ok=True)
    assert not builtin_path.exists()
    with contextlib.redirect_stdout(io.StringIO()):
        controller.selfcheck(str(builtin_path))
    builtin = json.loads(builtin_path.read_text(encoding='utf-8'))
    assert builtin['status'] == 'PASS' and builtin['cpu_only']
    checks.extend(builtin['checks'])

    clock, reader, result, starts = gate_case(
        lambda tick: (4000,8) if tick < 400 else (4000,5), lambda tick: [])
    assert clock.tick == 430 and not starts and result['gpu_utilization_percent'] == 5
    assert len(reader.reads) == 217
    checks.append('GPU busy for400s remains pending, then continuous30s idle passes; fresh Windows guard read every2s')

    clock, reader, result, starts = gate_case(
        lambda tick: (6001,0) if tick < 240 else (6000,0), lambda tick: [])
    assert clock.tick == 270 and result['gpu_used_mib'] == 6000
    checks.append('Memory above6000MiB for240s remains pending, then continuous30s at inclusive6000MiB passes')

    foreign_job = [{'pid':999,'command':'foreign-cpu-fixture','start_ticks':'1'}]
    observed = []
    def continuous_foreign(tick):
        observed.append(tick)
        return foreign_job
    try:
        gate_case(lambda tick:(4000,0),continuous_foreign)
    except RuntimeError as exc:
        assert 'WSL startup foreign isolation timeout' in str(exc)
        assert 'foreign-cpu-fixture' in str(exc)
    else:
        raise AssertionError('Continuous foreign work did not fail closed')
    assert observed[-1] == 180
    checks.append('Continuous foreign Linux work fails at180s before science Popen with full foreign diagnostics')

    clock, reader, result, starts = gate_case(
        lambda tick:(4000,8 if tick < 330 else 0),
        lambda tick:foreign_job if tick < 100 or 220 <= tick < 320 else [])
    assert clock.tick == 360
    checks.append('Two separate100s foreign bursts do not accumulate; clear samples reset foreign_since even while GPU remains busy')

    reads_at_30 = []
    def late_foreign(tick):
        if tick == 30:
            reads_at_30.append(tick)
            if len(reads_at_30) == 2:
                return foreign_job
        return []
    clock, reader, result, starts = gate_case(lambda tick:(4000,0),late_foreign)
    assert clock.tick == 62
    checks.append('Foreign work first seen during final startup confirmation resets the idle gate; short late burst can clear without a startup failure')

    try:
        gate_case(lambda tick:(4000,8),lambda tick:[],stale_after=192)
    except AssertionError as exc:
        assert 'Guard heartbeat stale' in str(exc)
    else:
        raise AssertionError('Stale heartbeat did not fail during prolonged GPU wait')
    checks.append('A21s-stale Windows heartbeat fails closed during GPU wait beyond180s')

    clock, reader, result, starts = gate_case(
        lambda tick:(4000,8 if tick < 400 else 0),lambda tick:[],main=True)
    assert starts == [430] and result == 'gate_complete'
    checks.append('Real guard main reaches a stub science Popen only at430s after400s busy plus30s idle; no actual child launch')

    # Exact current proof succeeds; stale history/log/status and old v8 proof fail.
    review_case(rejection=False)
    history_bytes = (STAGE/'_test_inputs/history.json').read_bytes()
    failure_bytes = (STAGE/'_test_inputs/r4_failure.log').read_bytes()
    proof = json.loads((STAGE/'R4_STARTUP_REVIEW_v9.json').read_text(encoding='utf-8'))
    history = json.loads(history_bytes.decode('utf-8'))
    future_history = copy.deepcopy(history)
    future_history.append(dict(history[-1],started='future',ended='future'))
    review_case(history_bytes=encoded(future_history))
    review_case(history_bytes=history_bytes+b'\n')
    review_case(failure_bytes=failure_bytes+b'new failure')
    review_case(original=(STAGE/'control_validation/R4_v8_FAILED_STATUS.json').read_bytes()+b'\n')
    review_case(evidence=(STAGE/'control_validation/R4_v8_STARTUP_REVIEW_EVIDENCE.json').read_bytes()+b'\n')
    old_proof = json.loads((STAGE/'_test_inputs/R4_STARTUP_REVIEW_v8.json').read_text(encoding='utf-8'))
    review_case(proof=old_proof)
    review_case(previous={'state':'reviewed_no_measurement',
        'review_id':sha((STAGE/'_test_inputs/R4_STARTUP_REVIEW_v8.json').read_bytes())})
    checks.append('Exact v9 proof accepted; old v8 proof/review ID and any future failure, history/log/status/evidence byte drift rejected')

    for bad_rows in ([history[0],history[2]],
                     [dict(history[0],returncode=1),history[1],history[2]],
                     [history[0],dict(history[1],returncode=0),history[2]],
                     [history[0],history[1],dict(history[2],returncode=0)],
                     [history[0],dict(history[1],guard_violation='foreign'),history[2]],
                     [history[0],history[1],dict(history[2],tag='wrong')]):
        changed_bytes = encoded(bad_rows)
        changed_proof = dict(proof,history_sha256=sha(changed_bytes))
        review_case(history_bytes=changed_bytes,proof=changed_proof)
    checks.append('Even a correspondingly rehashed proof requires exactly3 rows, clean successful preflight and both failed R4rc1 rows with no guard violation')

assert not prohibited_calls
v8_guard = (SOURCE/'scripts/common_guard_v8.py').read_text(encoding='utf-8')
v9_guard = (STAGE/'scripts/common_guard_v9.py').read_text(encoding='utf-8')
assert v8_guard[v8_guard.index('def merge_known_owned('):] == v9_guard[v9_guard.index('def merge_known_owned('):]
for name in ('check','foreign_linux_processes','cleanup_owned'):
    old_function = next(node for node in ast.parse(v8_guard).body if isinstance(node,ast.FunctionDef) and node.name==name)
    new_function = next(node for node in ast.parse(v9_guard).body if isinstance(node,ast.FunctionDef) and node.name==name)
    assert ast.dump(old_function,include_attributes=False) == ast.dump(new_function,include_attributes=False)
v8_queue = json.loads((SOURCE/'queue_v8.json').read_text(encoding='utf-8'))
v9_queue = json.loads((STAGE/'queue_v9.json').read_text(encoding='utf-8'))
assert {key:value for key,value in v8_queue.items() if key not in ('control_version','protocol')} == {
    key:value for key,value in v9_queue.items() if key not in ('control_version','protocol')}
assert v9_queue['gpu_memory_utilization'] == 0.8
checks.append('Post-Popen runtime and cleanup source byte-identical; check/Linux inventory unchanged; queue science jobs/args,6000MiB/5%/30s and80% GPU unchanged')

stage_files = ('controller_v9.py','scripts/common_guard_v9.py','queue_v9.json',
    'BRJD_EXECUTION_PROTOCOL_v9.json','R4_STARTUP_REVIEW_v9.json',
    'control_validation/R4_v8_FAILED_STATUS.json',
    'control_validation/R4_v8_STARTUP_REVIEW_EVIDENCE.json')
report = {'project_id':'R260401','machine':'DESKTOP-BRJD313','control_version':9,
    'status':'PASS','cpu_only':True,'native_windows_python':sys.executable,
    'gpu_accessed':False,'linux_python_probe':False,'controller_started':False,
    'science_workload_started':False,'controller_pid210828_absent':True,
    'controller_lock_absent':True,'no_actual_subprocess_launch':True,
    'idle_memory_limit_mib':6000,'idle_utilization_percent':5,'idle_stability_seconds':30,
    'startup_poll_seconds':2,'continuous_foreign_timeout_seconds':180,
    'gpu_or_memory_wait_timeout_seconds':None,'vllm_gpu_memory_utilization':0.8,
    'source_files_sha256':{relative:sha((STAGE/relative).read_bytes()) for relative in stage_files},
    'test_source_sha256':sha(Path(__file__).read_bytes()),'checks':checks,
    'manifest_included_in_report':False,'report_does_not_bind_own_sha':True}
destination = STAGE/'control_validation/VALIDATION_CONTROLLER_v9.json'
with destination.open('x',encoding='utf-8',newline='\n') as handle:
    handle.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':report['status'],'checks_passed':len(checks),
    'report':str(destination),'report_sha256':sha(destination.read_bytes()),
    'files_sha256':report['source_files_sha256']},ensure_ascii=False,indent=2))
