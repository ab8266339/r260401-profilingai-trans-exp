"""Untimed hardware, weight-integrity, token API and cache-capacity gates."""
import hashlib, json, os, platform, subprocess, sys, time
from pathlib import Path
ROOT=Path('/home/hxu/exp/r260401_brjd_20261003')
MODEL=Path('/mnt/e/CodeBase-708a/061-working-paper/R260306-fractal-moe/.runtime-qwen-scale/8B')

def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()

def main():
    manifest=json.loads((MODEL/'pinned_manifest.json').read_text(encoding='utf-8'))
    assert manifest['repo']=='Qwen/Qwen3-8B'
    assert manifest['revision']=='b968826d9c46dd6066d109eabc6255188de91218'
    assert (MODEL/'DOWNLOAD_COMPLETE.json').is_file(), 'Predecessor model download incomplete'
    weights=[]
    for row in manifest['weights']:
        p=MODEL/row['file']; assert p.stat().st_size==row['bytes']
        assert digest(p)==row['sha256'], 'Weight hash mismatch: '+str(p)
        weights.append(row)
    for name in ('config.json','tokenizer.json','tokenizer_config.json'):
        assert (MODEL/name).is_file(), 'Required model metadata missing: '+name
    out=ROOT/'results/preflight';out.mkdir(parents=True,exist_ok=True)
    record={'project_id':'R260401','machine':'DESKTOP-BRJD313','linux_hostname':platform.node(),
            'model':str(MODEL),'revision':manifest['revision'],'weights':weights,
            'validated_epoch':time.time(),'scope':'untimed gates only'}
    (out/'model_integrity.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    probe=subprocess.run([sys.executable,'-c',
      'import torch,json; assert torch.cuda.is_available(); p=torch.cuda.get_device_properties(0); '
      'print(json.dumps(dict(torch=torch.__version__,cuda=torch.version.cuda,gpu=p.name,'
      'memory_bytes=p.total_memory,capability=torch.cuda.get_device_capability(0))))'],
      capture_output=True,text=True,check=True)
    hardware=json.loads(probe.stdout.strip());assert '5090' in hardware['gpu']
    (out/'hardware.json').write_text(json.dumps(hardware,indent=2),encoding='utf-8')
    os.environ['R260401_VLLM_BIN']='/home/hxu/venvs/r260401_brjd_20261003/bin/vllm'
    os.environ['R260401_CACHE_DIR']=str(ROOT/'cache')
    subprocess.run([sys.executable,str(ROOT/'scripts/kvbench.py'),'--model-key','qwen3_8b',
      '--model-path',str(MODEL),'--grid','capacity','--gpus','0','--port','8171',
      '--kv-dtype','auto','--gpu-util','0.90','--max-model-len','12288',
      '--out-root',str(out),'--ready-timeout','1200'],check=True)
    marker=out/'qwen3_8b/auto_capacity/DONE'
    assert marker.is_file(), 'Allocator/API gate incomplete'
    (out/'BRJD_PREFLIGHT_DONE.json').write_text(json.dumps({'project_id':'R260401',
       'machine':'DESKTOP-BRJD313','passed_epoch':time.time(),'allocator_marker':str(marker)},indent=2),encoding='utf-8')

if __name__=='__main__':main()
