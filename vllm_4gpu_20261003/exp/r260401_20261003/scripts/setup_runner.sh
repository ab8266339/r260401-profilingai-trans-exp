#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/host/exp/r260401_20261003
BASE=/home/host/venvs/vllm/bin/python
ENV=/home/host/venvs/r260401_runner_20261003
OUT="$ROOT/results/runtime_install"
export PATH="$HOME/.local/bin:$PATH" PYTHONIOENCODING=utf-8 UV_PYTHON_DOWNLOADS=never UV_HTTP_TIMEOUT=30 UV_HTTP_RETRIES=1
mkdir -p "$OUT"
if [ ! -x "$ENV/bin/python" ]; then
 timeout 60 uv venv --python "$BASE" "$ENV" > "$OUT/create.log" 2>&1
fi
# A new venv does not inherit another venv's libraries. Explicitly include only
# the existing unchanged vLLM site-packages via this task's own .pth file.
SITE="$($BASE -c 'import sysconfig;print(sysconfig.get_path("purelib"))')"
"$ENV/bin/python" - "$SITE" <<'PTH'
import pathlib,sys,sysconfig
p=pathlib.Path(sysconfig.get_path('purelib'))/'r260401_vllm_readonly.pth'
content=sys.argv[1]+'\n'
if p.exists(): assert p.read_text()==content, 'Runner base site changed'
else: p.write_text(content,encoding='utf-8')
PTH
if ! "$ENV/bin/python" -c 'import scipy; assert scipy.__version__=="1.16.2"' >/dev/null 2>&1; then
 "$BASE" - "$OUT" <<'META'
import hashlib,json,pathlib,sys,time,urllib.request
out=pathlib.Path(sys.argv[1]);t=time.monotonic()
raw=urllib.request.urlopen('https://pypi.org/pypi/scipy/1.16.2/json',timeout=25).read();m=json.loads(raw)
candidates=[x for x in m['urls'] if x['packagetype']=='bdist_wheel' and 'cp312-cp312-manylinux' in x['filename'] and 'x86_64' in x['filename']]
assert len(candidates)==1, [x['filename'] for x in candidates]
x=candidates[0];url=x['url']+'#sha256='+x['digests']['sha256']
(out/'scipy_wheel_url.txt').write_text(url,encoding='utf-8')
(out/'source_probe.json').write_text(json.dumps({'metadata_source':'https://pypi.org/pypi/scipy/1.16.2/json','metadata_sha256':hashlib.sha256(raw).hexdigest(),'seconds':time.monotonic()-t,'wheel_filename':x['filename'],'wheel_size':x['size'],'wheel_sha256':x['digests']['sha256'],'requires_python':m['info']['requires_python'],'requires_dist':m['info']['requires_dist']},indent=2),encoding='utf-8')
META
 timeout --signal=TERM --kill-after=30 480 uv pip install --python "$ENV/bin/python" --no-deps --no-index --only-binary=:all: "$(cat "$OUT/scipy_wheel_url.txt")" > "$OUT/install.log" 2>&1
fi
OPENBLAS_NUM_THREADS=1 "$ENV/bin/python" - "$OUT" <<'GATE'
import importlib.metadata as md,json,pathlib,sys,datetime
import numpy,scipy
from scipy.stats import t
versions={k:md.version(k) for k in ('vllm','torch','transformers','aiohttp','numpy','scipy')}
assert versions['vllm']=='0.30.0' and versions['torch'].split('+')[0]=='2.13.0'
assert versions['numpy']=='2.3.5' and versions['scipy']=='1.16.2'
assert abs(float(t.ppf(.975,9))-2.262157162798205)<1e-8
p=pathlib.Path(sys.argv[1]);p.joinpath('DONE.json').write_text(json.dumps({'status':'READY','versions':versions,'runner_python':sys.executable,'numpy_origin':numpy.__file__,'scipy_origin':scipy.__file__,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'base_environment_modified':False},indent=2),encoding='utf-8')
print(json.dumps({'runtime':'READY','versions':versions}))
GATE
