#!/usr/bin/env bash
# Independent external baseline environments only. Never activates/modifies vllm env.
# Usage: bash external_setup.sh lmcache|sglang|all OUTPUT_DIR [INSTALL_TIMEOUT_S]
set -euo pipefail
ARM="${1:-all}"
OUT="${2:?Pass an output directory for installation evidence}"
LIMIT="${3:-1200}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="/home/hxu/exp/r260401_brjd_20261003"
export R260401_CACHE_DIR="$ROOT/cache" PYTHONIOENCODING=utf-8
mkdir -p "$OUT"
export PATH="$HOME/.local/bin:$PATH"
setup_finish() {
  local rc="$1" name
  for name in lmcache sglang; do
    if [ "$ARM" != all ] && [ "$ARM" != "$name" ]; then continue; fi
    mkdir -p "$OUT/$name"
    if [ "$rc" -ne 0 ] || [ ! -f "$OUT/$name/install_status.json" ]; then
      printf '{"engine":"%s","status":"UNMEASURED","phase":"setup_preflight","exit_code":%s,"note":"Consult software_sources.txt, venv.log, and setup task log; no baseline measurement was made."}\n' "$name" "$rc" > "$OUT/$name/install_status.json"
    fi
  done
  printf '{"status":"SETUP_STAGE_FINISHED_CHECK_EACH_ARM_STATUS","shell_exit_code":%s}\n' "$rc" > "$OUT/setup_status.json"
  # The queue must inspect arm status rather than treating a successful wrapper as measurement.
  exit 0
}
trap 'setup_finish "$?"' EXIT
command -v uv >/dev/null
command -v timeout >/dev/null
command -v curl >/dev/null
BASE_PY="/home/hxu/venvs/r260401_brjd_20261003/bin/python"
test -x "$BASE_PY"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 UV_HTTP_TIMEOUT=30 UV_HTTP_RETRIES=1 UV_LINK_MODE=copy
export UV_PYTHON_DOWNLOADS=never

probe_source() {
  local url="$1" tag="$2"
  timeout 25 curl -LfsS --connect-timeout 8 --max-time 20 -o /dev/null \
    -w "source=$tag http=%{http_code} seconds=%{time_total} bytes=%{size_download} bytes_per_s=%{speed_download}\n" \
    "$url" >> "$OUT/software_sources.txt" 2>&1
}
# Common runtime source; arm-specific source failures do not block the other engine.
probe_source https://download.pytorch.org/whl/cu130/torch/ pytorch_cu130
probe_arm_sources() {
  local name="$1" version="$2" wheel_url
  probe_source "https://pypi.org/simple/$name/" "pypi_$name" || return $?
  probe_source "https://pypi.org/pypi/$name/$version/json" "${name}_release_metadata" || return $?
  wheel_url="$(timeout 30 "$BASE_PY" - "$name" "$version" <<'PY'
import json,sys,urllib.request
from packaging.specifiers import SpecifierSet
name,version=sys.argv[1:]
with urllib.request.urlopen('https://pypi.org/pypi/%s/%s/json'%(name,version),timeout=20) as r:
    meta=json.load(r)
tag='cp%d%d'%sys.version_info[:2]
runtime='.'.join(map(str,sys.version_info[:3]))
matches=[x['url'] for x in meta['urls'] if x['filename'].endswith('.whl')
    and ((tag in x['filename'] and 'x86_64' in x['filename']) or x['filename'].endswith('py3-none-any.whl'))
    and runtime in SpecifierSet(x.get('requires_python') or '')]
if not matches: raise SystemExit('No prebuilt matching %s wheel'%name)
print(matches[0])
PY
)" || return $?
# Range download gives a nontrivial throughput sample without fetching a Torch wheel.
timeout 30 curl -LfsS --connect-timeout 8 --max-time 25 --range 0-4194303 \
  --max-filesize 35000000 -o /dev/null \
  -w "source=${name}_wheel_rate http=%{http_code} seconds=%{time_total} bytes=%{size_download} bytes_per_s=%{speed_download}\n" \
  "$wheel_url" >> "$OUT/software_sources.txt" 2>&1
}

install_arm() {
  local name="$1" env_path="/home/hxu/venvs/r260401_${1}_brjd_20261003"
  local arm_out="$OUT/$name"
  mkdir -p "$arm_out"
  local version=0.5.5
  if [ "$name" = sglang ]; then version=0.5.20; fi
  probe_arm_sources "$name" "$version" || {
    local source_rc=$?
    printf '{"engine":"%s","status":"UNMEASURED","phase":"software_sources","exit_code":%s}\n' "$name" "$source_rc" > "$arm_out/install_status.json"
    return 0
  }
  # Reusing only this task's independent directory is safe/resumable; no system-site-packages.
  if [ ! -x "$env_path/bin/python" ]; then
    timeout 60 uv venv --python "$BASE_PY" "$env_path" > "$arm_out/venv.log" 2>&1 || {
      local venv_rc=$?
      printf '{"engine":"%s","status":"UNMEASURED","phase":"venv","exit_code":%s}\n' "$name" "$venv_rc" > "$arm_out/install_status.json"
      return 0
    }
  fi
  local rc=0
  if [ "$name" = lmcache ]; then
    timeout --signal=TERM --kill-after=20 "$LIMIT" uv pip install --python "$env_path/bin/python" \
      --prerelease=allow --only-binary=:all: --index-strategy unsafe-best-match \
      --extra-index-url https://download.pytorch.org/whl/cu130 \
      'torch==2.13.0' 'vllm==0.30.0' 'lmcache==0.5.5' > "$arm_out/install.log" 2>&1 || rc=$?
  else
    timeout --signal=TERM --kill-after=20 "$LIMIT" uv pip install --python "$env_path/bin/python" \
      --prerelease=allow --only-binary=:all: --index-strategy unsafe-best-match \
      --extra-index-url https://download.pytorch.org/whl/cu130 \
      'torch==2.13.0' 'sglang==0.5.20' > "$arm_out/install.log" 2>&1 || rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    uv pip freeze --python "$env_path/bin/python" > "$arm_out/packages.freeze.txt"
    timeout 180 "$env_path/bin/python" "$HERE/external_probe.py" --engine "$name" \
      --inspect-only --out "$arm_out/import_probe.json" > "$arm_out/import_probe.log" 2>&1 || rc=$?
  fi
  "$BASE_PY" - "$arm_out/install_status.json" "$name" "$rc" "$env_path" "$LIMIT" <<'PY'
import datetime,json,pathlib,sys
p,name,rc,env_path,limit=sys.argv[1:]
pathlib.Path(p).write_text(json.dumps({"engine":name,"status":"INSTALLED_IMPORT_CHECKED" if rc=="0" else "UNMEASURED","exit_code":int(rc),"env_path":env_path,"time_limit_s":int(limit),"utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"note":"Installation/import is a compatibility gate, not a performance measurement. Consult install/import logs on failure."},indent=2),encoding="utf-8")
PY
  return 0
}
case "$ARM" in
  lmcache|sglang) install_arm "$ARM" ;;
  all) install_arm lmcache; install_arm sglang ;;
  *) echo 'arm must be lmcache, sglang, or all' >&2; exit 2 ;;
esac
