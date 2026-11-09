#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/hxu/exp/r260401_brjd_20261003
ENV=/home/hxu/venvs/r260401_brjd_20261003
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"
# Outer guard supplies this task's WSL-adapter bridge to Windows' loopback proxy.
test -n "${HTTPS_PROXY:-}"
export UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
BASE=/home/hxu/.local/share/uv/python/cpython-3.12.13-linux-x86_64-gnu/bin/python3.12
test -x "$BASE"
if ! test -x "$ENV/bin/python"; then uv venv --python "$BASE" "$ENV"; fi
timeout --signal=TERM --kill-after=30s 2400s uv pip install --python "$ENV/bin/python" \
  'vllm==0.30.0' 'torch==2.13.0' 'scipy==1.16.2' 'aiohttp==3.14.3'
"$ENV/bin/python" - "$ROOT" <<'PY'
import importlib.metadata as m,json,platform,sys
from pathlib import Path
root=Path(sys.argv[1]); data={'machine':'DESKTOP-BRJD313','linux_hostname':platform.node(),
 'python':sys.version,'packages':{k:m.version(k) for k in ['vllm','torch','scipy','aiohttp','transformers']}}
for k,v in [('vllm','0.30.0'),('torch','2.13.0'),('scipy','1.16.2')]: assert data['packages'][k]==v
(root/'results/preflight').mkdir(parents=True,exist_ok=True)
(root/'results/preflight/environment.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
print(json.dumps(data,indent=2))
PY
exec "$ENV/bin/python" "$ROOT/scripts/preflight_brjd.py"
