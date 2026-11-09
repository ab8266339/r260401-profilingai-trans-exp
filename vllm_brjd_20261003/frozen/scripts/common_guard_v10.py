"""Fail closed on Windows background work; clean this job's Linux process group."""
import argparse, hashlib, json, os, signal, subprocess, time
from pathlib import Path

GUARD=Path('/mnt/e/r260401-brjd-20261003/control/windows_guard.json')
ROOT=Path('/home/hxu/exp/r260401_brjd_20261003')
QUEUE='r260401_brjd_20261003'
WINDOWS_ROOT=Path('/mnt/e/r260401-brjd-20261003')
MANIFEST_NAME='FILE_MANIFEST_v10.json'


def sha256_file(path):
    digest=hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(1<<20),b''):
            digest.update(block)
    return digest.hexdigest()


def verify_payload(linux_root=None,windows_root=None):
    linux_root=ROOT if linux_root is None else Path(linux_root)
    windows_root=WINDOWS_ROOT if windows_root is None else Path(windows_root)
    local=linux_root/MANIFEST_NAME
    mirrored=windows_root/MANIFEST_NAME
    local_bytes=local.read_bytes()
    mirrored_bytes=mirrored.read_bytes()
    if local_bytes!=mirrored_bytes:
        raise RuntimeError('Linux/Windows FILE_MANIFEST_v10.json differ; payload execution refused')
    manifest=json.loads(local_bytes.decode('utf-8'))
    if (manifest.get('project_id')!='R260401' or manifest.get('machine')!='DESKTOP-BRJD313'
        or manifest.get('control_version')!=10 or manifest.get('linux_root')!=str(linux_root)):
        raise RuntimeError('Wrong manifest project/machine/version/Linux-root identity')
    files=manifest.get('files')
    if not isinstance(files,dict) or not files:
        raise RuntimeError('Manifest has no bound Linux payload files')
    resolved_root=linux_root.resolve(strict=True)
    for relative,expected in files.items():
        if (not isinstance(relative,str) or not relative or '\\' in relative
            or Path(relative).is_absolute() or '..' in Path(relative).parts):
            raise RuntimeError('Unsafe manifest member path: '+str(relative))
        if (not isinstance(expected,str) or len(expected)!=64
            or any(ch not in '0123456789abcdef' for ch in expected.lower())):
            raise RuntimeError('Invalid SHA256 for manifest member: '+relative)
        payload=(linux_root/relative).resolve(strict=True)
        try: payload.relative_to(resolved_root)
        except ValueError:
            raise RuntimeError('Manifest member escapes Linux root: '+relative)
        if not payload.is_file():
            raise RuntimeError('Manifest member is not a file: '+relative)
        if sha256_file(payload)!=expected.lower():
            raise RuntimeError('Linux payload SHA256 mismatch: '+relative)
    return {'kind':'payload_manifest_verified','manifest_sha256':hashlib.sha256(local_bytes).hexdigest(),
        'manifest_name':MANIFEST_NAME,'linux_root':str(linux_root),'windows_root':str(windows_root),
        'files_verified':len(files),'unix_time':time.time(),
        'scope':'Windows/Linux manifests byte-identical and all bound Linux payloads SHA256-verified before Popen'}


SYSTEM_CPU_ARGV = [['/usr/bin/python3', '/usr/bin/networkd-dispatcher', '--run-startup-triggers'], ['/usr/bin/python3', '/usr/share/unattended-upgrades/unattended-upgrade-shutdown', '--wait-for-signal']]
SYSTEM_CPU_SCRIPT_SHA = {'/usr/bin/networkd-dispatcher': '72d276e06565baf94c5d1d7ba52083524386eeba733d671a868487b0e03abaee', '/usr/share/unattended-upgrades/unattended-upgrade-shutdown': '8fbf4e278c4ad457e82cc05586631b1a0550dd9d10892a70487ef74c623066e0'}
def _system_cpu_allowed(argv):
    if argv not in SYSTEM_CPU_ARGV:return False
    try:return hashlib.sha256(Path(argv[1]).read_bytes()).hexdigest()==SYSTEM_CPU_SCRIPT_SHA[argv[1]]
    except OSError:return False

_OWN_LINUX_BIRTHS={}
def _read_linux_process(item):
    try:
        first=(item/'stat').read_text()
        fields=first[first.rfind(')')+2:].split()
        if len(fields)<20:raise ValueError('Malformed process stat')
        parent=int(fields[1]);birth=fields[19]
        argv=(item/'cmdline').read_bytes().split(bytes([0]))
        argv=[x.decode('utf-8','replace') for x in argv if x]
        second=(item/'stat').read_text()
        again=second[second.rfind(')')+2:].split()
        if len(again)<20 or again[19]!=birth:
            raise RuntimeError('WSL PID identity changed during snapshot: '+str(item))
        return (parent,argv,birth)
    except (FileNotFoundError,ProcessLookupError):return None
    except (OSError,ValueError,IndexError) as exc:
        if isinstance(exc,OSError) and exc.errno in (2,3):return None
        raise RuntimeError('WSL process inventory failed for '+str(item)+': '+str(exc)) from exc

def _foreign_from_rows(rows,guard_pid,previous_births):
    if guard_pid not in rows:raise RuntimeError('Current Linux guard missing from process snapshot')
    owned={guard_pid}
    owned.update(pid for pid,birth in previous_births.items() if pid in rows and rows[pid][2]==birth)
    changed=True
    while changed:
        changed=False
        for pid,(parent,argv,birth) in rows.items():
            if parent in owned and pid not in owned:owned.add(pid);changed=True
    previous_births.clear();previous_births.update({pid:rows[pid][2] for pid in owned})
    markers=('vllm','sglang','llama-server','ollama','torchrun','tritonserver','inference-server')
    foreign=[]
    for pid,(parent,argv,birth) in rows.items():
        executable=Path(argv[0]).name.lower() if argv else ''
        candidate=executable.startswith('python') or any(k in executable for k in markers)
        if pid not in owned and candidate and not _system_cpu_allowed(argv):
            foreign.append({'pid':pid,'command':' '.join(argv)[:600],'start_ticks':birth})
    return foreign

def foreign_linux_processes():
    rows={}
    for item in Path('/proc').iterdir():
        if not item.name.isdigit():continue
        row=_read_linux_process(item)
        if row is not None:rows[int(item.name)]=row
    return _foreign_from_rows(rows,os.getpid(),_OWN_LINUX_BIRTHS)

def check(include_linux=True):
    p=json.loads(GUARD.read_text(encoding='utf-8'))
    assert p['schema_version']==1 and p['queue_id']==QUEUE, 'Wrong guard identity'
    assert -5<=time.time()-p['unix_time']<=20, 'Guard heartbeat stale'
    assert p['status'] in ('ready','running'), 'Windows host is blocked'
    assert not p['foreign_processes'], 'Foreign Windows task present'
    if include_linux:
        foreign=foreign_linux_processes()
        if foreign:raise RuntimeError('Foreign WSL experiment or model server present: '+json.dumps(foreign))
    return p

def descendants(rootpid):
    rows={}
    for item in Path('/proc').iterdir():
        if not item.name.isdigit(): continue
        try:
            raw=(item/'stat').read_text(); fields=raw[raw.rfind(')')+2:].split()
            rows[int(item.name)]=(int(fields[1]),int(fields[2]),fields[19])
        except (OSError,ValueError,IndexError): continue
    selected={rootpid};changed=True
    while changed:
        changed=False
        for pid,(ppid,pgid,start) in rows.items():
            if ppid in selected and pid not in selected:selected.add(pid);changed=True
    return {pid:rows[pid] for pid in selected if pid in rows}

def group_alive(pgid):
    try: os.killpg(pgid,0); return True
    except ProcessLookupError: return False

def cleanup(pgid):
    if not group_alive(pgid): return
    os.killpg(pgid,signal.SIGTERM)
    end=time.monotonic()+90
    while group_alive(pgid) and time.monotonic()<end: time.sleep(.5)
    if group_alive(pgid): os.killpg(pgid,signal.SIGKILL)

def wait_for_linux_startup():
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

def merge_known_owned(owned):
    # Reuse only identity-bound descendants already observed by the foreign gate.
    # A reparented child remains ours; a reused PID or unknown orphan never is.
    for pid,birth in list(_OWN_LINUX_BIRTHS.items()):
        if pid==os.getpid():continue
        try:
            raw=Path(f'/proc/{pid}/stat').read_text();fields=raw[raw.rfind(')')+2:].split()
            if fields[19]==birth and int(fields[2])!=os.getpgrp():
                owned.setdefault(pid,(int(fields[1]),int(fields[2]),birth))
        except (OSError,ValueError,IndexError):continue

def cleanup_owned(p,owned,wait_seconds=90,poll_seconds=.5):
    # Nested servers create their own sessions. Track only this job's descendants,
    # and verify PID birth times before cleaning surviving nested groups.
    owned.update(descendants(p.pid))
    merge_known_owned(owned)
    groups=set()
    for pid,(parent,pgid,birth) in owned.items():
        try:
            raw=Path(f'/proc/{pid}/stat').read_text(); fields=raw[raw.rfind(')')+2:].split()
            if fields[19]==birth and int(fields[2])==pgid and pgid!=os.getpgrp():groups.add(pgid)
        except (OSError,ValueError,IndexError):pass
    for pgid in groups:
        try:os.killpg(pgid,signal.SIGTERM)
        except ProcessLookupError:pass
    end=time.monotonic()+wait_seconds
    while any(group_alive(g) for g in groups) and time.monotonic()<end:
        p.poll();time.sleep(poll_seconds)
    for pgid in groups:
        if group_alive(pgid):
            try:os.killpg(pgid,signal.SIGKILL)
            except ProcessLookupError:pass
    p.wait(timeout=10)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--timeout',type=int,required=True)
    ap.add_argument('command',nargs=argparse.REMAINDER);args=ap.parse_args()
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    assert command; info=wait_for_linux_startup(); verified=verify_payload()
    log=ROOT/'logs'/(str(info.get('active_job','job'))+'_host_guard.jsonl')
    log.parent.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,CUDA_VISIBLE_DEVICES='0',PYTHONIOENCODING='utf-8',
             R260401_WINDOWS_GUARD_FILE=str(GUARD),
             R260401_VLLM_BIN='/home/hxu/venvs/r260401_brjd_20261003/bin/vllm',
             R260401_CACHE_DIR=str(ROOT/'cache'),OPENBLAS_NUM_THREADS='1')
    proxy=info.get('proxy_url')
    assert proxy and proxy.startswith('http://') and proxy.endswith(':27890'), 'Task proxy missing'
    for k in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy'):env[k]=proxy
    env['NO_PROXY']=env['no_proxy']='localhost,127.0.0.1,::1'
    env['UV_INDEX_URL']='https://pypi.tuna.tsinghua.edu.cn/simple'
    env.pop('ALL_PROXY',None);env.pop('all_proxy',None)
    p=subprocess.Popen(command,env=env,cwd=ROOT,start_new_session=True)
    started=time.monotonic(); rc=1; owned={}
    def stop(*_): raise InterruptedError('Queue job received termination')
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        with log.open('a',encoding='utf-8') as f:
            f.write(json.dumps(verified,ensure_ascii=False)+'\n');f.flush()
            while p.poll() is None:
                sample=check(); owned.update(descendants(p.pid)); f.write(json.dumps(sample,ensure_ascii=False)+'\n');f.flush()
                if time.monotonic()-started>args.timeout: raise TimeoutError('Job time limit exceeded')
                time.sleep(2)
        rc=p.returncode
    finally:
        cleanup_owned(p,owned)
    return rc

if __name__=='__main__': raise SystemExit(main())
