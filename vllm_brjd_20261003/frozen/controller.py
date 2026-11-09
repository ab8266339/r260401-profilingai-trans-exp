"""BRJD FIFO: await the entire R260306 sweep, then run R260401 jobs."""
import argparse, datetime, hashlib, json, os, subprocess, sys, time
from pathlib import Path
import psutil
from proxy_bridge import start_bridge

ROOT = Path(r'E:\r260401-brjd-20261003')
QUEUE_ID = 'r260401_brjd_20261003'
CONTROL = ROOT / 'control'
LINUX_ROOT = '/home/hxu/exp/r260401_brjd_20261003'
PRED = Path(r'E:\CodeBase-708a\061-working-paper\R260306-fractal-moe\notes\qwen_scale_20261003\results\queue_status.json')
SELF = str(ROOT / 'controller.py').lower()
PROXY_URL = None

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
    for p in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
        try:
            name = (p.info['name'] or '').lower()
            cmd = ' '.join(p.info['cmdline'] or [])
            if p.pid == os.getpid(): continue
            if name == 'llama-server.exe':
                foreign.append({'pid':p.pid, 'kind':'model_server', 'name':name})
            elif name in ('python.exe', 'pythonw.exe'):
                # This dedicated network relay is a continuously running non-GPU service.
                if 'proxy_relay_7891.py' in cmd: continue
                foreign.append({'pid':p.pid, 'kind':'python_job', 'command':cmd[:600]})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    s = subprocess.run(['nvidia-smi','--query-gpu=memory.total,memory.used,utilization.gpu',
                        '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=10)
    if s.returncode: raise RuntimeError('nvidia-smi failed: '+s.stderr[:250])
    total, used, util = map(float, s.stdout.strip().splitlines()[0].split(','))
    return {'foreign_processes':foreign, 'gpu_total_mib':total,
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
            if abs(p.create_time()-old['create_time'])<1: raise RuntimeError('Queue controller already running')
        except psutil.NoSuchProcess: pass
        lock.unlink()
    with lock.open('x',encoding='utf-8') as f:
        json.dump({'pid':os.getpid(),'create_time':psutil.Process().create_time()},f)
    active=None; bridge=None
    try:
        manifest=read_json(ROOT/'FILE_MANIFEST.json'); verify(manifest)
        queue=read_json(ROOT/'queue.json')
        previous=read_json(CONTROL/'status.json') if (CONTROL/'status.json').exists() else {}
        if previous.get('state') in ('running','interrupted'):
            raise RuntimeError('Previous job requires review; refusing automatic duplicate execution')
        idle=0; last_reason=None
        while True:
            if (CONTROL/'STOP_REQUESTED').exists(): set_status('stopped_by_request'); return
            done,pred=predecessor(); inv=inventory()
            reason='waiting_for_R260306_complete' if not done else 'waiting_for_exclusive_BRJD'
            if done and not inv['foreign_processes'] and inv['gpu_used_mib']<=2048 and inv['gpu_utilization_percent']<=5:
                idle+=1; reason='checking_idle_stability'
            else: idle=0
            heartbeat('blocked',None,inv)
            if reason!=last_reason:
                set_status(reason, predecessor=pred, inventory=inv); last_reason=reason
            if idle>=3: break
            time.sleep(15)
        verify(manifest)
        bridge,PROXY_URL=start_bridge()
        history=read_json(CONTROL/'history.json') if (CONTROL/'history.json').exists() else []
        finished={r['tag'] for r in history if r.get('returncode')==0}
        for job in queue['jobs']:
            if job['tag'] in finished: continue
            inv=inventory()
            if inv['foreign_processes']: raise RuntimeError('New foreign task before job start')
            heartbeat('ready',job['tag'],inv)
            set_status('running',active_job=job['tag'], predecessor=pred)
            log=ROOT/'logs'/f"{job['tag']}.log"; log.parent.mkdir(exist_ok=True)
            cmd=['wsl','-d','Ubuntu-22.04','--','/usr/bin/python3',LINUX_ROOT+'/scripts/common_guard.py',
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
                set_status('failed',active_job=job['tag'],returncode=rc,reason=violation,
                           log=str(log)); return
        heartbeat('ready',None,inventory()); set_status('complete')
    finally:
        if active and active.poll() is None:
            heartbeat('blocked',None,{'foreign_processes':[{'kind':'controller_exit'}]})
        if lock.exists(): lock.unlink()
        if bridge: bridge.shutdown(); bridge.server_close()

if __name__=='__main__':
    try: main()
    except Exception as e:
        set_status('failed',error=type(e).__name__+': '+str(e)); raise
