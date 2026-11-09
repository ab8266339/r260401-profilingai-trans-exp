from pathlib import Path
import hashlib,json,subprocess
source=Path('/mnt/e/r260401-brjd-20261003');root=Path('/home/hxu/exp/r260401_brjd_20261003')
manifest=json.loads((source/'FILE_MANIFEST_v7.json').read_text(encoding='utf-8'))
assert not list((root/'results').rglob('DONE')) if (root/'results').exists() else True
assert not any((root/'logs').glob('r260401_brjd_20261003_*')) if (root/'logs').exists() else True
for rel,sha in manifest['files'].items():
    data=(source/rel).read_bytes();assert hashlib.sha256(data).hexdigest()==sha,rel
    p=root/rel;p.parent.mkdir(parents=True,exist_ok=True)
    if p.exists():assert p.read_bytes()==data,rel
    else:p.write_bytes(data)
    if p.suffix=='.sh':subprocess.run(['bash','-n',str(p)],check=True)
    if p.suffix=='.py':compile(data,str(p),'exec')
(root/'FILE_MANIFEST_v7.json').write_bytes((source/'FILE_MANIFEST_v7.json').read_bytes())
import sys;sys.path.insert(0,str(root/'scripts'));import common_guard_v7 as guard
for argv in guard.SYSTEM_CPU_ARGV:
    assert guard._system_cpu_allowed(argv)
    assert not guard._system_cpu_allowed(argv+['unexpected'])
    assert not guard._system_cpu_allowed(['/tmp/python3']+argv[1:])
original=dict(guard.SYSTEM_CPU_SCRIPT_SHA)
for key in guard.SYSTEM_CPU_SCRIPT_SHA:guard.SYSTEM_CPU_SCRIPT_SHA[key]='0'*64
assert not any(guard._system_cpu_allowed(argv) for argv in guard.SYSTEM_CPU_ARGV)
guard.SYSTEM_CPU_SCRIPT_SHA.clear();guard.SYSTEM_CPU_SCRIPT_SHA.update(original)
assert not guard.foreign_linux_processes(),guard.foreign_linux_processes()
print(json.dumps({'no_local_measurement':True,'verified':guard.verify_payload()}))
