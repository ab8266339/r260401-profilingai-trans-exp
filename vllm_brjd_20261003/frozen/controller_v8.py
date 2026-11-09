"""BRJD FIFO: user-priority R260401 while predecessor is paused or complete."""
import argparse, ast, csv, datetime, hashlib, json, os, subprocess, sys, time
from pathlib import Path
import psutil
from proxy_bridge import start_bridge

ROOT = Path(r'E:\r260401-brjd-20261003')
QUEUE_ID = 'r260401_brjd_20261003'
CONTROL = ROOT / 'control'
LINUX_ROOT = '/home/hxu/exp/r260401_brjd_20261003'
PRED = Path(r'E:\CodeBase-708a\061-working-paper\R260306-fractal-moe\notes\qwen_scale_20261003\results\queue_status.json')
SELF = str(ROOT / 'controller_v8.py').lower()
PROXY_URL = None
IDLE_MEMORY_LIMIT_MIB = 6000
IDLE_UTILIZATION_LIMIT = 5
IDLE_STABILITY_SECONDS = 30
REVIEW_STATES = frozenset(('running','interrupted','needs_review'))

class AlreadyRunning(RuntimeError): pass
class NeedsReview(RuntimeError): pass

# Exact task-owned CPU helpers. Never allow a script merely because it is in Temp.
_TASK_TEMP = Path(os.environ.get('LOCALAPPDATA',r'C:\Users\admin\AppData\Local'))/'Temp'
_CPU_HELPERS = {
    'r260401-brjd-20261003-control': {'transfer_remote.py','network_probe.py','finalize_control_v2.py',
        'finalize_control_v3.py','deploy_windows.py','deploy_linux.py'},
    'r260401-brjd-20261003-native': {'build_native.py','selfcheck_native.py'},
    'r260401-queue-20261003': {'remote_control.py','query_current_progress.py','rt_tail.py','status_full_queue.py'},
    'r260401-status-20261003': {'query_current_progress.py','rt_tail.py','status_full_queue.py'},
}
CPU_HELPER_PATHS = frozenset(os.path.normcase(str(_TASK_TEMP/folder/name))
    for folder,names in _CPU_HELPERS.items() for name in names)
CHAT_MONITOR_PATH = r'C:\Users\admin\Documents\Codex\2026-10-03\task\watch_codex_thread.py'
CHAT_MONITOR_SHA256 = '2805568814b3c1697edafbae320ff8223c4e0781dcdce26f5cfc46521b43d5e2'
CHAT_MONITOR_ARGV = [r'C:\Python313\python.exe','-u',CHAT_MONITOR_PATH,'--watch','--interval','30']

def chat_monitor_sha256():
    # Hash is read on every matching inventory check, never cached.
    return hashlib.sha256(Path(CHAT_MONITOR_PATH).read_bytes()).hexdigest()

def allowed_chat_monitor(argv):
    if argv != CHAT_MONITOR_ARGV:
        return False
    try:
        return chat_monitor_sha256()==CHAT_MONITOR_SHA256
    except OSError:
        return False

def allowed_python_check(info, now=None):
    """Limited CPU query exemptions; unknown Python remains conservatively blocked."""
    argv=info.get('cmdline') or []
    if allowed_chat_monitor(argv):
        return 'pinned_readonly_codex_thread_monitor'
    if len(argv)>1 and os.path.normcase(argv[1]) in CPU_HELPER_PATHS:
        return 'task_owned_cpu_helper'
    # The existing standalone relay is a dedicated non-GPU network service.
    if len(argv)>1 and Path(argv[1]).name.lower()=='proxy_relay_7891.py':
        return 'existing_non_gpu_proxy_relay'
    if '-c' not in argv or info.get('create_time') is None:
        return None
    age=(time.time() if now is None else now)-info['create_time']
    if not 0<=age<=30:
        return None
    try:
        source=argv[argv.index('-c')+1]
        tree=ast.parse(source)
    except (IndexError,SyntaxError):
        return None
    modules={'psutil','json','os','time','datetime'}
    imports=set()
    permitted_calls={'print','str','int','float','bool','len','list','dict','tuple','set','sorted',
        'sum','min','max','getattr','isinstance','enumerate','dumps','process_iter','pids',
        'Process','cpu_percent','virtual_memory','memory_percent','name','cmdline','create_time',
        'as_dict','getpid','time','isoformat','now','append','extend','get','items','values','keys'}
    for node in ast.walk(tree):
        if isinstance(node,(ast.Import,ast.ImportFrom)):
            names=[a.name.split('.')[0] for a in node.names] if isinstance(node,ast.Import) else [(node.module or '').split('.')[0]]
            if any(name not in modules for name in names): return None
            imports.update(names)
        elif isinstance(node,(ast.While,ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)):
            return None
        elif isinstance(node,ast.Call):
            name=node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else None
            if name not in permitted_calls: return None
    return 'short_readonly_psutil_query' if 'psutil' in imports else None

def read_json(p):
    return json.loads(p.read_text(encoding='utf-8'))

def atomic_json(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(p)

def timestamp():
    return datetime.datetime.now().astimezone().isoformat()

def inventory():
    foreign = []
    background_checks = []
    # Native tasklist reads the process snapshot in one OS query. Reading every
    # Windows process's remote cmdline/create_time through psutil was too slow.
    snapshot=subprocess.run(['tasklist.exe','/FO','CSV','/NH'],capture_output=True,
        timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if snapshot.returncode:
        raise RuntimeError('tasklist snapshot failed; refusing stale or slow fallback')
    text=snapshot.stdout.decode('utf-8','replace') if isinstance(snapshot.stdout,bytes) else snapshot.stdout
    rows=list(csv.reader(text.splitlines()))
    if not rows:raise RuntimeError('tasklist snapshot empty')
    for row in rows:
        if len(row)<2 or not row[0].strip() or not row[1].strip().isdigit():
            raise RuntimeError('tasklist CSV invalid; refusing incomplete process inventory')
        name=row[0].strip().lower();pid=int(row[1].strip())
        if pid == os.getpid():continue
        if name == 'llama-server.exe':
            foreign.append({'pid':pid,'kind':'model_server','name':name})
            continue
        if name not in ('python.exe','pythonw.exe'):continue
        try:
            p=psutil.Process(pid)
            try:argv=p.cmdline()
            except psutil.AccessDenied:argv=None
            try:created=p.create_time()
            except psutil.AccessDenied:created=None
            info={'pid':pid,'name':name,'cmdline':argv,'create_time':created}
            cmd=' '.join(argv or [])
            allowance=allowed_python_check(info)
            if allowance:
                background_checks.append({'pid':pid,'kind':allowance,'command':cmd[:600]})
                continue
            foreign.append({'pid':pid,'kind':'python_job','command':cmd[:600]})
        except psutil.NoSuchProcess:continue
        except psutil.AccessDenied:
            foreign.append({'pid':pid,'kind':'python_job','command':'','inventory_error':'AccessDenied'})
    s = subprocess.run(['nvidia-smi','--query-gpu=memory.total,memory.used,utilization.gpu',
                        '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=10)
    if s.returncode: raise RuntimeError('nvidia-smi failed: '+s.stderr[:250])
    total, used, util = map(float, s.stdout.strip().splitlines()[0].split(','))
    return {'foreign_processes':foreign,'background_python_checks':background_checks,
            'process_count':len(rows),'python_candidates':sum(r[0].strip().lower() in ('python.exe','pythonw.exe') for r in rows), 'gpu_total_mib':total,
            'gpu_used_mib':used, 'gpu_utilization_percent':util,
            'cpu_percent':psutil.cpu_percent(), 'available_ram_bytes':psutil.virtual_memory().available}

PRIORITY_RECORD = ROOT/'USER_PRIORITY_20261003.json'
PRIORITY_RECORD_SHA256 = '73c576d9c94c9040ef4b465fde72a3dd841ca2609aeeeef4fe092fc32a37af9f'
def priority_allowed(p,authorization):
    return (authorization.get('project_id')=='R260401'
        and authorization.get('machine')=='DESKTOP-BRJD313'
        and authorization.get('instruction')=='优先做你的这个'
        and p.get('state') in authorization.get('allowed_predecessor_states',[]))

def predecessor():
    try: p=read_json(PRED)
    except Exception as e: return False, {'state':'status_unavailable', 'error':str(e)}
    if p.get('state')=='complete':return True,p
    try:
        data=PRIORITY_RECORD.read_bytes()
        if hashlib.sha256(data).hexdigest()!=PRIORITY_RECORD_SHA256:
            raise RuntimeError('Priority authorization record SHA mismatch')
        authorization=json.loads(data.decode('utf-8'))
    except Exception as e:
        return False,{**p,'priority_error':str(e)}
    allowed=priority_allowed(p,authorization)
    return allowed,{**p,'r260401_schedule':{'priority_authorized':allowed,
        'instruction':authorization['instruction'],'predecessor_state_preserved':True}}

def heartbeat(status, active_job, inv):
    atomic_json(CONTROL/'windows_guard.json', {
        'schema_version':1,'queue_id':QUEUE_ID,'unix_time':time.time(),
        'status':status,'active_job':active_job, 'machine':os.environ.get('COMPUTERNAME'),
        'proxy_url':PROXY_URL,**inv})

def set_status(state, **extra):
    value={'schema_version':1,'queue_id':QUEUE_ID,'machine':os.environ.get('COMPUTERNAME'),
           'state':state,'time':timestamp(),'controller_pid':os.getpid(),**extra}
    atomic_json(CONTROL/'status.json',value)
    print(json.dumps(value,ensure_ascii=False),flush=True)

def verify(manifest):
    for relative, expected in manifest['files'].items():
        p=ROOT / relative
        assert p.is_file(), 'Missing frozen file: '+relative
        assert hashlib.sha256(p.read_bytes()).hexdigest()==expected, 'Frozen file changed: '+relative

def idle_resources(inv):
    return (not inv['foreign_processes'] and inv['gpu_used_mib']<=IDLE_MEMORY_LIMIT_MIB
            and inv['gpu_utilization_percent']<=IDLE_UTILIZATION_LIMIT)

def finished_tags(history):
    return {r['tag'] for r in history if r.get('returncode')==0 and not r.get('guard_violation')}

def require_previous_review(previous):
    if previous.get('state') in REVIEW_STATES:
        set_status('needs_review',active_job=previous.get('active_job'),
            reason='previous_job_requires_review',previous_state=previous.get('state'),
            previous_status=previous)
        raise NeedsReview('Previous job requires review; refusing automatic duplicate execution')

def wait_for_idle(active_job=None):
    idle_since=None; last_reason=None
    while True:
        if (CONTROL/'STOP_REQUESTED').exists():
            set_status('stopped_by_request'); return None
        done,pred=predecessor(); inv=inventory(); tick=time.monotonic()
        reason='waiting_for_R260306_pause_or_complete' if not done else 'waiting_for_exclusive_BRJD'
        if done and idle_resources(inv):
            if idle_since is None: idle_since=tick
            reason='checking_idle_stability'
        else: idle_since=None
        heartbeat('blocked',active_job,inv)
        if reason!=last_reason:
            set_status(reason,active_job=active_job,predecessor=pred,inventory=inv)
            last_reason=reason
        if idle_since is not None and tick-idle_since>=IDLE_STABILITY_SECONDS:
            return pred,inv
        time.sleep(15)

def record_exception(exc):
    # A refused duplicate/review attempt must not overwrite the live/sticky state.
    if isinstance(exc,(AlreadyRunning,NeedsReview)):
        print(json.dumps({'state':'already_running' if isinstance(exc,AlreadyRunning) else 'needs_review',
                         'error':str(exc)},ensure_ascii=False),flush=True)
        return
    previous=read_json(CONTROL/'status.json') if (CONTROL/'status.json').exists() else {}
    state='needs_review' if previous.get('state') in REVIEW_STATES else 'failed'
    set_status(state,active_job=previous.get('active_job'),error=type(exc).__name__+': '+str(exc))

def selfcheck(destination):
    """CPU-only checks; inventory OS/GPU calls are stubbed, with no real GPU tools."""
    checks=[]
    monitor_info={'cmdline':list(CHAT_MONITOR_ARGV)}
    assert allowed_python_check(monitor_info)=='pinned_readonly_codex_thread_monitor'
    changed_args=list(CHAT_MONITOR_ARGV);changed_args[-1]='15'
    assert allowed_python_check({'cmdline':changed_args}) is None
    changed_path=list(CHAT_MONITOR_ARGV);changed_path[2]=r'C:\Users\admin\Documents\other\watch_codex_thread.py'
    assert allowed_python_check({'cmdline':changed_path}) is None
    changed_executable=list(CHAT_MONITOR_ARGV);changed_executable[0]=r'C:\other\python.exe'
    assert allowed_python_check({'cmdline':changed_executable}) is None
    original_monitor_hash=chat_monitor_sha256
    try:
        globals()['chat_monitor_sha256']=lambda:'0'*64
        assert allowed_python_check(monitor_info) is None
        calls=[]
        def counted_hash(): calls.append(1);return CHAT_MONITOR_SHA256
        globals()['chat_monitor_sha256']=counted_hash
        assert allowed_python_check(monitor_info)=='pinned_readonly_codex_thread_monitor'
        assert allowed_python_check(monitor_info)=='pinned_readonly_codex_thread_monitor'
        assert len(calls)==2
    finally:
        globals()['chat_monitor_sha256']=original_monitor_hash
    checks.append('chat monitor exact executable/path/argv/SHA required; changed content/path/args rejected; hash rechecked every match')
    from types import SimpleNamespace
    own=os.getpid();cmdline_reads=[]
    fixtures=[
        {'pid':own,'name':'python.exe','cmdline':['python.exe','controller_v8.py'],'create_time':1000},
        {'pid':101,'name':'python.exe','cmdline':list(CHAT_MONITOR_ARGV),'create_time':1000},
        {'pid':102,'name':'python.exe','cmdline':['python.exe',r'E:\train_experiment.py'],'create_time':1000},
        {'pid':103,'name':'llama-server.exe','cmdline':['llama-server.exe','--model','x'],'create_time':1000},
        {'pid':104,'name':'chrome.exe','cmdline':['chrome.exe'],'create_time':1000},
        {'pid':105,'name':'python.exe','cmdline':None,'create_time':1000},
        {'pid':106,'name':'python.exe','cmdline':['python.exe',str(_TASK_TEMP/'r260401-brjd-20261003-control'/'transfer_remote.py')],'create_time':1000},
    ]
    old_foreign=[];old_candidates=set()
    for row in fixtures:
        if row['pid']==own:continue
        if row['name']=='llama-server.exe':old_foreign.append({'pid':row['pid'],'kind':'model_server','name':row['name']})
        elif row['name'] in ('python.exe','pythonw.exe'):
            old_candidates.add(row['pid'])
            if not allowed_python_check(row):old_foreign.append({'pid':row['pid'],'kind':'python_job','command':' '.join(row['cmdline'] or [])[:600]})
    class FakeProcess:
        def __init__(self,row):self.pid=row['pid'];self.row=row
        def create_time(self):return self.row['create_time']
        def cmdline(self):
            assert self.row['name'] in ('python.exe','pythonw.exe') and self.pid!=own
            cmdline_reads.append(self.pid)
            if self.row['cmdline'] is None:raise psutil.AccessDenied(self.pid)
            return self.row['cmdline']
    saved=(psutil.Process,psutil.cpu_percent,psutil.virtual_memory,subprocess.run)
    try:
        def fake_process(pid):
            return FakeProcess(next(row for row in fixtures if row['pid']==pid))
        def fake_gpu(argv,**kwargs):
            if argv[0]=='tasklist.exe':
                assert argv==['tasklist.exe','/FO','CSV','/NH'] and kwargs['timeout']==10
                return SimpleNamespace(returncode=0,stdout=''.join('"'+row['name']+'","'+str(row['pid'])+'"\n' for row in fixtures).encode(),stderr=b'')
            assert argv[0]=='nvidia-smi'
            return SimpleNamespace(returncode=0,stdout='32607, 3000, 0\n',stderr='')
        psutil.Process=fake_process;psutil.cpu_percent=lambda:0
        psutil.virtual_memory=lambda:SimpleNamespace(available=1024)
        subprocess.run=fake_gpu
        narrowed=inventory()
        subprocess.run=lambda *args,**kwargs:SimpleNamespace(returncode=1,stdout=b'',stderr=b'error')
        try:inventory()
        except RuntimeError as exc:assert 'tasklist snapshot failed' in str(exc)
        else:raise AssertionError('failed snapshot did not fail closed')
        subprocess.run=lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=b'invalid,csv\n',stderr=b'')
        try:inventory()
        except RuntimeError as exc:assert 'tasklist CSV invalid' in str(exc)
        else:raise AssertionError('incomplete snapshot did not fail closed')
    finally:
        psutil.Process,psutil.cpu_percent,psutil.virtual_memory,subprocess.run=saved
    assert narrowed['foreign_processes']==old_foreign
    assert set(cmdline_reads)==old_candidates and len(cmdline_reads)==len(old_candidates)
    assert {row['pid'] for row in narrowed['background_python_checks']}=={101,106}
    checks.append('synthetic full/tasklist candidate sets and foreign records identical; only Python details read; AccessDenied stays blocked; watch SHA allowance preserved')
    checks.append('tasklist timeout10 and CSV first2 columns; failed/malformed snapshot fails closed without slow fallback')
    assert idle_resources({'foreign_processes':[],'gpu_used_mib':6000,'gpu_utilization_percent':5})
    assert not idle_resources({'foreign_processes':[],'gpu_used_mib':6001,'gpu_utilization_percent':5})
    assert not idle_resources({'foreign_processes':[],'gpu_used_mib':5900,'gpu_utilization_percent':6})
    assert not idle_resources({'foreign_processes':[{'pid':1}],'gpu_used_mib':0,'gpu_utilization_percent':0})
    checks.append('6000 MiB inclusive and 5 percent inclusive; foreign process blocks')
    history=[{'tag':'clean','returncode':0,'guard_violation':None},
             {'tag':'badguard','returncode':0,'guard_violation':'foreign'},
             {'tag':'badrc','returncode':1,'guard_violation':None}]
    assert finished_tags(history)=={'clean'}
    checks.append('rc0 plus no guard violation required for successful history')
    helper=str(_TASK_TEMP/'r260401-brjd-20261003-control'/'transfer_remote.py')
    assert allowed_python_check({'cmdline':['python.exe',helper]})=='task_owned_cpu_helper'
    gpu_script=str(_TASK_TEMP/'r260401-brjd-20261003-control'/'preflight_brjd.py')
    assert allowed_python_check({'cmdline':['python.exe',gpu_script]}) is None
    assert allowed_python_check({'cmdline':['python.exe',r'E:\experiment.py']}) is None
    query='import psutil,json; print(json.dumps([p.info for p in psutil.process_iter(["pid","name"])]))'
    query_info={'cmdline':['python.exe','-c',query],'create_time':1000}
    assert allowed_python_check(query_info,now=1010)=='short_readonly_psutil_query'
    assert allowed_python_check(query_info,now=1031) is None
    for source in ('import psutil,torch; torch.cuda.init()',
                   'import psutil; psutil.Process(1).kill()',
                   'import psutil; exec("print(1)")'):
        assert allowed_python_check({'cmdline':['python.exe','-c',source],'create_time':1000},now=1010) is None
    checks.append('exact task CPU helpers and short readonly psutil allowed; GPU/temp unknown/mutation/old query blocked')
    original={name:globals()[name] for name in ('CONTROL','set_status','read_json','heartbeat','predecessor','inventory')}
    original_clock,original_sleep=time.monotonic,time.sleep
    class FakePath:
        def __init__(self,name): self.name=name
        def exists(self): return self.name=='status.json'
    class FakeControl:
        def __truediv__(self,name): return FakePath(name)
    captured=[]; fake_status={'state':'running','active_job':'r2'}
    def capture(state,**extra):
        captured.append({'state':state,**extra})
        fake_status.clear();fake_status.update(state=state,**extra)
    try:
        globals()['CONTROL']=FakeControl();globals()['set_status']=capture
        globals()['read_json']=lambda _:dict(fake_status)
        for state in REVIEW_STATES:
            before=len(captured)
            try: require_previous_review({'state':state,'active_job':'r2'})
            except NeedsReview as exc: record_exception(exc)
            else: raise AssertionError('review gate did not block')
            assert len(captured)==before+1 and fake_status['state']=='needs_review'
        before=len(captured);record_exception(AlreadyRunning('test'))
        assert len(captured)==before and fake_status['state']=='needs_review'
        fake_status.clear();fake_status.update(state='running',active_job='r3')
        record_exception(RuntimeError('unexpected controller failure'))
        assert fake_status['state']=='needs_review' and fake_status['active_job']=='r3'
        checks.append('running/interrupted/needs_review sticky; AlreadyRunning never overwrites; active crash needs review')
        globals()['heartbeat']=lambda *args:None
        # A failed predecessor, excess memory, and excess utilization each reset
        # the continuous stability interval. The real wait helper is exercised.
        samples=[(False,6000,0),(True,6000,0),(True,6001,0),(True,6000,0),
                 (True,6000,6),(True,6000,0),(True,6000,0),(True,6000,0)]
        position={'i':0}; sleeps=[]
        globals()['predecessor']=lambda:(samples[position['i']][0],{'state':'complete' if samples[position['i']][0] else 'running'})
        globals()['inventory']=lambda:{'foreign_processes':[],'gpu_used_mib':samples[position['i']][1],
                                      'gpu_utilization_percent':samples[position['i']][2]}
        time.monotonic=lambda:position['i']*15
        def advance(seconds): sleeps.append(seconds);position['i']+=1
        time.sleep=advance
        result=wait_for_idle('r2')
        assert result[0]['state']=='complete' and position['i']==7 and len(sleeps)==7
        # A fresh second job must independently wait all 30 seconds again.
        samples=[(True,5900,0)]*3;position['i']=0;sleeps.clear()
        wait_for_idle('r3')
        assert position['i']==2 and len(sleeps)==2
        checks.append('real wait helper resets on predecessor/resources and requires fresh continuous30 seconds per job')
    finally:
        globals().update(original);time.monotonic=original_clock;time.sleep=original_sleep
    source=Path(__file__).read_text(encoding='utf-8');tree=ast.parse(source)
    main_node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    job_loop=next(n for n in ast.walk(main_node) if isinstance(n,ast.For) and isinstance(n.target,ast.Name) and n.target.id=='job')
    assert any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='wait_for_idle' for n in ast.walk(job_loop))
    assert "FILE_MANIFEST_v8.json" in source and "queue_v8.json" in source and "scripts/common_guard_v8.py" in source
    checks.append('each real job loop invokes idle gate and v8 manifest/queue/guard routes')
    report={'status':'PASS','cpu_only':True,'gpu_accessed':False,'controller_started':False,
        'controller_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'checks':checks,
        'idle_memory_limit_mib':IDLE_MEMORY_LIMIT_MIB,'idle_utilization_percent':IDLE_UTILIZATION_LIMIT,
        'idle_stability_seconds':IDLE_STABILITY_SECONDS}
    Path(destination).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

def validate_startup_review(previous,history_bytes,failure_bytes):
    if previous.get('state')!='reviewed_no_measurement':return
    data=(ROOT/'R4_STARTUP_REVIEW_v8.json').read_bytes()
    proof=json.loads(data.decode('utf-8'))
    if previous.get('review_id')!=hashlib.sha256(data).hexdigest():
        raise NeedsReview('Startup review identity mismatch')
    if (proof.get('project_id')!='R260401' or proof.get('machine')!='DESKTOP-BRJD313'
        or proof.get('failed_tag')!='r260401_brjd_20261003_r4'
        or proof.get('science_workload_not_launched') is not True
        or hashlib.sha256(history_bytes).hexdigest()!=proof['history_sha256']
        or hashlib.sha256(failure_bytes).hexdigest()!=proof['failure_log_sha256']):
        raise NeedsReview('Startup review is stale or belongs to another failure')
    original=(ROOT/'control_validation/R4_v7_FAILED_STATUS.json').read_bytes()
    if hashlib.sha256(original).hexdigest()!=proof['original_status_sha256']:
        raise NeedsReview('Original failed status changed')
    rows=json.loads(history_bytes.decode('utf-8'))
    if (len(rows)!=2 or rows[-1]['tag']!=proof['failed_tag'] or rows[-1]['returncode']!=1):
        raise NeedsReview('Unexpected attempted measurement history')

def main():
    global PROXY_URL
    if os.environ.get('COMPUTERNAME')!='DESKTOP-BRJD313':
        raise RuntimeError('This queue is pinned to DESKTOP-BRJD313')
    CONTROL.mkdir(parents=True,exist_ok=True)
    lock=CONTROL/'controller.lock'
    if lock.exists():
        old=read_json(lock)
        try:
            p=psutil.Process(old['pid'])
            if abs(p.create_time()-old['create_time'])<1: raise AlreadyRunning('Queue controller already running')
        except psutil.NoSuchProcess: pass
        lock.unlink()
    try:
        with lock.open('x',encoding='utf-8') as f:
            json.dump({'pid':os.getpid(),'create_time':psutil.Process().create_time()},f)
    except FileExistsError as exc:
        raise AlreadyRunning('Another controller acquired the queue lock') from exc
    active=None; bridge=None
    try:
        manifest=read_json(ROOT/'FILE_MANIFEST_v8.json'); verify(manifest)
        queue=read_json(ROOT/'queue_v8.json')
        previous=read_json(CONTROL/'status.json') if (CONTROL/'status.json').exists() else {}
        validate_startup_review(previous,(CONTROL/'history.json').read_bytes() if (CONTROL/'history.json').exists() else b'[]',(ROOT/'logs/r260401_brjd_20261003_r4.log').read_bytes() if previous.get('state')=='reviewed_no_measurement' else b'')
        require_previous_review(previous)
        history=read_json(CONTROL/'history.json') if (CONTROL/'history.json').exists() else []
        finished=finished_tags(history)
        for job in queue['jobs']:
            if job['tag'] in finished: continue
            cleared=wait_for_idle(job['tag'])
            if cleared is None: return
            pred,inv=cleared
            verify(manifest)
            if bridge is None: bridge,PROXY_URL=start_bridge()
            heartbeat('ready',job['tag'],inv)
            set_status('running',active_job=job['tag'], predecessor=pred)
            log=ROOT/'logs'/f"{job['tag']}.log"; log.parent.mkdir(exist_ok=True)
            cmd=['wsl','-d','Ubuntu-22.04','--','/usr/bin/python3',LINUX_ROOT+'/scripts/common_guard_v8.py',
                 '--timeout',str(job['timeout_s']),'--','bash',LINUX_ROOT+'/'+job['script']]
            started=timestamp()
            with log.open('ab',buffering=0) as out:
                active=subprocess.Popen(cmd,stdout=out,stderr=subprocess.STDOUT,
                                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                violation=None
                while active.poll() is None:
                    try:
                        inv=inventory()
                        violation='foreign_process_started' if inv['foreign_processes'] else None
                        heartbeat('blocked' if violation else 'running',job['tag'],inv)
                    except Exception as e:
                        violation='guard_inventory_failed: '+str(e)
                        heartbeat('blocked',job['tag'],{'foreign_processes':[{'kind':'guard_error'}]})
                    if (CONTROL/'STOP_REQUESTED').exists():
                        violation='stop_requested'; heartbeat('blocked',job['tag'],inv)
                    if violation:
                        set_status('interrupted',active_job=job['tag'],reason=violation)
                        # Linux guard kills only its own newly created process group.
                        active.wait(timeout=150)
                        break
                    time.sleep(3)
                rc=active.returncode
            history.append({'tag':job['tag'],'started':started,'ended':timestamp(),
                            'returncode':rc,'guard_violation':violation,'log':str(log)})
            atomic_json(CONTROL/'history.json',history)
            active=None
            if rc or violation:
                set_status('needs_review',active_job=job['tag'],returncode=rc,reason=violation,
                           log=str(log)); return
        heartbeat('ready',None,inventory()); set_status('complete')
    finally:
        if active and active.poll() is None:
            heartbeat('blocked',None,{'foreign_processes':[{'kind':'controller_exit'}]})
        if lock.exists(): lock.unlink()
        if bridge: bridge.shutdown(); bridge.server_close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--selfcheck')
    args=parser.parse_args()
    if args.selfcheck:
        selfcheck(args.selfcheck)
    else:
        try: main()
        except Exception as e:
            record_exception(e); raise
