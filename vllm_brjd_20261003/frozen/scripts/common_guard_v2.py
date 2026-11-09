"""Fail closed on Windows background work; clean this job's Linux process group."""
import argparse, json, os, signal, subprocess, time
from pathlib import Path

GUARD=Path('/mnt/e/r260401-brjd-20261003/control/windows_guard.json')
ROOT=Path('/home/hxu/exp/r260401_brjd_20261003')
QUEUE='r260401_brjd_20261003'

def check():
    p=json.loads(GUARD.read_text(encoding='utf-8'))
    assert p['schema_version']==1 and p['queue_id']==QUEUE, 'Wrong guard identity'
    assert -5<=time.time()-p['unix_time']<=20, 'Guard heartbeat stale'
    assert p['status'] in ('ready','running'), 'Windows host is blocked'
    assert not p['foreign_processes'], 'Foreign Windows task present'
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

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--timeout',type=int,required=True)
    ap.add_argument('command',nargs=argparse.REMAINDER);args=ap.parse_args()
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    assert command; info=check()
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
            while p.poll() is None:
                sample=check(); owned.update(descendants(p.pid)); f.write(json.dumps(sample,ensure_ascii=False)+'\n');f.flush()
                if time.monotonic()-started>args.timeout: raise TimeoutError('Job time limit exceeded')
                time.sleep(2)
        rc=p.returncode
    finally:
        # Nested servers create their own sessions. Track only this job's descendants,
        # and verify PID birth times before cleaning surviving nested groups.
        owned.update(descendants(p.pid))
        groups={p.pid}
        for pid,(parent,pgid,birth) in owned.items():
            try:
                raw=Path(f'/proc/{pid}/stat').read_text(); fields=raw[raw.rfind(')')+2:].split()
                if fields[19]==birth and int(fields[2])==pgid and pgid!=os.getpgrp():groups.add(pgid)
            except (OSError,ValueError,IndexError):pass
        for pgid in groups:
            try:os.killpg(pgid,signal.SIGTERM)
            except ProcessLookupError:pass
        end=time.monotonic()+90
        while any(group_alive(g) for g in groups) and time.monotonic()<end:
            p.poll();time.sleep(.5)
        for pgid in groups:
            if group_alive(pgid):
                try:os.killpg(pgid,signal.SIGKILL)
                except ProcessLookupError:pass
        p.wait(timeout=10)
    return rc

if __name__=='__main__': raise SystemExit(main())
