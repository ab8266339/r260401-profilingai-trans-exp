"""BRJD FIFO: await the entire R260306 sweep, then run R260401 jobs."""
import argparse, ast, datetime, hashlib, json, os, subprocess, sys, time
from pathlib import Path
import psutil
from proxy_bridge import start_bridge

ROOT = Path(r'E:\r260401-brjd-20261003')
QUEUE_ID = 'r260401_brjd_20261003'
CONTROL = ROOT / 'control'
LINUX_ROOT = '/home/hxu/exp/r260401_brjd_20261003'
PRED = Path(r'E:\CodeBase-708a\061-working-paper\R260306-fractal-moe\notes\qwen_scale_20261003\results\queue_status.json')
SELF = str(ROOT / 'controller_v3.py').lower()
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

def allowed_python_check(info, now=None):
    """Limited CPU query exemptions; unknown Python remains conservatively blocked."""
    argv=info.get('cmdline') or []
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
    for p in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
        try:
            name = (p.info['name'] or '').lower()
            cmd = ' '.join(p.info['cmdline'] or [])
            if p.pid == os.getpid(): continue
            if name == 'llama-server.exe':
                foreign.append({'pid':p.pid, 'kind':'model_server', 'name':name})
            elif name in ('python.exe', 'pythonw.exe'):
                allowance=allowed_python_check(p.info)
                if allowance:
                    background_checks.append({'pid':p.pid,'kind':allowance,'command':cmd[:600]})
                    continue
                foreign.append({'pid':p.pid, 'kind':'python_job', 'command':cmd[:600]})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    s = subprocess.run(['nvidia-smi','--query-gpu=memory.total,memory.used,utilization.gpu',
                        '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=10)
    if s.returncode: raise RuntimeError('nvidia-smi failed: '+s.stderr[:250])
    total, used, util = map(float, s.stdout.strip().splitlines()[0].split(','))
    return {'foreign_processes':foreign,'background_python_checks':background_checks, 'gpu_total_mib':total,
            'gpu_used_mib':used, 'gpu_utilization_percent':util,
            'cpu_percent':psutil.cpu_percent(), 'available_ram_bytes':psutil.virtual_memory().available}

def predecessor():
    try: p=read_json(PRED)
    except Exception as e: return False, {'state':'status_unavailable', 'error':str(e)}
    return p.get('state')=='complete', p

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
        reason='waiting_for_R260306_complete' if not done else 'waiting_for_exclusive_BRJD'
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
    """CPU-only semantic checks. Never calls inventory, WSL, bridge, or GPU tools."""
    checks=[]
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
    assert "FILE_MANIFEST_v3.json" in source and "queue_v3.json" in source and "scripts/common_guard_v3.py" in source
    checks.append('each real job loop invokes idle gate and v3 manifest/queue/guard routes')
    report={'status':'PASS','cpu_only':True,'gpu_accessed':False,'controller_started':False,
        'controller_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'checks':checks,
        'idle_memory_limit_mib':IDLE_MEMORY_LIMIT_MIB,'idle_utilization_percent':IDLE_UTILIZATION_LIMIT,
        'idle_stability_seconds':IDLE_STABILITY_SECONDS}
    Path(destination).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

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
        manifest=read_json(ROOT/'FILE_MANIFEST_v3.json'); verify(manifest)
        queue=read_json(ROOT/'queue_v3.json')
        previous=read_json(CONTROL/'status.json') if (CONTROL/'status.json').exists() else {}
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
            cmd=['wsl','-d','Ubuntu-22.04','--','/usr/bin/python3',LINUX_ROOT+'/scripts/common_guard_v3.py',
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
