#!/usr/bin/env python3
"""BRJD RD-R+ R2/R3/R4 runner; R means forced misses, A0 automatic prefix reuse.

The queue must freeze a content-addressed copy before execution. No installation,
model download, or modifications to the existing vLLM environment are performed.
"""
import argparse
import asyncio
import csv
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import signal
import socket
import statistics
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace

SCHEMA = 'r260401-brjd-native-v1'


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(tmp, path)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def token_hash(ids):
    return hashlib.sha256(json.dumps(ids, separators=(',', ':')).encode('utf-8')).hexdigest()


def load_old(path):
    spec = importlib.util.spec_from_file_location('r260401_kvbench', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(argv, timeout=30):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, encoding='utf-8', errors='replace')
    if p.returncode:
        raise RuntimeError(f'command rc={p.returncode}: {argv!r}: {p.stderr[:600]}')
    return p.stdout


def runtime_versions():
    from importlib.metadata import version,PackageNotFoundError
    versions={}
    for package in ('vllm','torch','transformers','numpy','aiohttp','scipy'):
        try:
            versions[package]=version(package)
        except PackageNotFoundError:
            versions[package]=None
    return versions


def read_windows_guard():
    path=os.environ.get('R260401_WINDOWS_GUARD','/mnt/e/r260401-brjd-20261003/control/windows_guard.json')
    try:
        obj=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return {'path':path,'record':obj,'observed_unix':time.time()}
    except Exception as exc:
        return {'path':path,'error':repr(exc),'observed_unix':time.time()}


def windows_guard_errors(snapshot):
    if not snapshot or snapshot.get('error'):
        return ['Windows load guard missing or unreadable; WSL NVML alone cannot prove isolation']
    obj=snapshot.get('record') or {}
    errors=[]
    if obj.get('schema_version')!=1 or obj.get('queue_id')!='r260401_brjd_20261003':
        errors.append('Windows guard schema/queue identity mismatch')
    stamp=obj.get('unix_time')
    if not isinstance(stamp,(float,int)) or not -5<=snapshot['observed_unix']-stamp<=20:
        errors.append('Windows guard heartbeat stale or clock skew exceeds tolerance')
    if obj.get('status') not in ('ready','running'):
        errors.append(f"Windows guard status not clear: {obj.get('status')}")
    if not isinstance(obj.get('foreign_processes'),list) or obj['foreign_processes']:
        errors.append('Windows guard reports unknown/foreign experiment processes')
    return errors


def gpu_inventory():
    rows = csv.reader(command(['nvidia-smi', '--query-gpu=index,uuid,utilization.gpu,memory.used,name',
                               '--format=csv,noheader,nounits']).splitlines())
    devices = [{'index': int(r[0].strip()), 'uuid': r[1].strip(),
                'utilization_percent': float(r[2].strip()), 'memory_mib': float(r[3].strip()),
                'name': r[4].strip()} for r in rows if len(r) >= 5]
    text = command(['nvidia-smi', '--query-compute-apps=gpu_uuid,pid,used_gpu_memory',
                    '--format=csv,noheader,nounits'])
    processes = []
    for row in csv.reader(text.splitlines()):
        if len(row) >= 3:
            processes.append({'gpu_uuid': row[0].strip(), 'pid': int(row[1].strip()),
                              'memory_mib': row[2].strip()})
    return {'wall_unix': time.time(), 'devices': devices, 'processes': processes, 'windows_guard': read_windows_guard()}


def is_descendant(pid, ancestor):
    seen = set()
    while pid > 1 and pid not in seen:
        if pid == ancestor:
            return True
        seen.add(pid)
        try:
            status = Path(f'/proc/{pid}/status').read_text(encoding='utf-8')
            pid = int(next(line.split()[1] for line in status.splitlines() if line.startswith('PPid:')))
        except (OSError, StopIteration, ValueError):
            return False
    return False


def exclusive_errors(snapshot, selected, server_pid=None, idle_limit=5):
    errors = windows_guard_errors(snapshot.get('windows_guard'))
    by_uuid = {d['uuid']: d['index'] for d in snapshot['devices']}
    for d in snapshot['devices']:
        if d['index'] not in selected and d['utilization_percent'] > idle_limit:
            errors.append(f"other card {d['index']} utilization {d['utilization_percent']}%")
    for p in snapshot['processes']:
        own = server_pid is not None and is_descendant(p['pid'], server_pid)
        if not own:
            errors.append(f"foreign compute process pid={p['pid']} card={by_uuid.get(p['gpu_uuid'])}")
    if len(snapshot['devices']) != 1 or selected != {0}:
        errors.append(f"BRJD requires single-card inventory and GPU 0, observed {len(snapshot['devices'])}, selected={selected}")
    return errors


async def stage_request(http, base, prompt_ids, salt, out_tokens=128, natural=False, model='m'):
    """Natural EOS omits min_tokens/ignore_eos; preserve actual generated IDs."""
    import aiohttp
    body = {'model': model, 'prompt': prompt_ids, 'max_tokens': out_tokens, 'temperature': 0.0,
            'stream': True, 'stream_options': {'include_usage': True}, 'return_token_ids': True}
    if not natural:
        body.update(min_tokens=out_tokens, ignore_eos=True)
    if salt is not None:
        body['cache_salt'] = salt
    t0 = time.perf_counter()
    ttft, ids, usage, finish_reason, chunks = None, [], None, None, 0
    async with http.post(base + '/v1/completions', json=body,
                         timeout=aiohttp.ClientTimeout(total=1800)) as response:
        if response.status != 200:
            raise RuntimeError(f'HTTP {response.status}: {(await response.text())[:800]}')
        buf = b''
        async for chunk in response.content.iter_any():
            buf += chunk.replace(b'\r\n', b'\n')
            while b'\n\n' in buf:
                event, buf = buf.split(b'\n\n', 1)
                for line in event.split(b'\n'):
                    if not line.startswith(b'data:'):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == b'[DONE]':
                        continue
                    obj = json.loads(raw)
                    if obj.get('usage'):
                        usage = obj['usage']
                    for choice in obj.get('choices') or []:
                        output = choice.get('token_ids') or []
                        if output or choice.get('text'):
                            chunks += 1
                            if ttft is None:
                                ttft = time.perf_counter() - t0
                        ids.extend(output)
                        if choice.get('finish_reason') is not None:
                            finish_reason = choice['finish_reason']
    elapsed = time.perf_counter() - t0
    if usage is None or usage.get('completion_tokens') is None:
        raise RuntimeError('missing usage; output token lengths cannot be verified')
    if len(ids) != int(usage['completion_tokens']):
        raise RuntimeError(f"token-ID count {len(ids)} != completion_tokens {usage['completion_tokens']}")
    if not natural and len(ids) != out_tokens:
        raise RuntimeError(f'fixed-length output {len(ids)} != {out_tokens}')
    if not 0 <= len(ids) <= out_tokens or usage.get('prompt_tokens') != len(prompt_ids):
        raise RuntimeError('token-ID input/output length accounting failed')
    details = usage.get('prompt_tokens_details') or {}
    return {'t_start': t0, 't_end': t0 + elapsed, 'ttft': ttft, 'e2e': elapsed,
            'decode_seconds': elapsed - ttft if ttft is not None else None,
            'prompt_tokens': usage['prompt_tokens'], 'completion_tokens': usage['completion_tokens'],
            'cached_tokens': details.get('cached_tokens'), 'out_ids': ids,
            'finish_reason': finish_reason, 'n_chunks': chunks, 'natural_eos': natural,
            'timing_scope': 'client TTFT includes queue/network; decode = completion minus TTFT'}


async def reset(old, http, base):
    tries = await old.reset_prefix_cache(http, base, reset_external=True, tries=200)
    if tries < 0:
        raise RuntimeError('prefix-cache reset failed; cell must not be timed')
    return tries


async def api_probe(old, http, base, pool, out_tokens):
    await reset(old, http, base)
    rng = old.np.random.default_rng([old.WORKLOAD_SEED, 7, 7])
    prompt = rng.choice(pool, 256).tolist()
    salt = f'rplus-probe|{time.time_ns()}'
    first = await stage_request(http, base, prompt, salt, out_tokens=min(16, out_tokens))
    repeated = await stage_request(http, base, prompt, salt, out_tokens=min(16, out_tokens))
    isolated = await stage_request(http, base, prompt, salt + '|other', out_tokens=min(16, out_tokens))
    natural = await stage_request(http, base, prompt, salt + '|eos', out_tokens=min(16, out_tokens), natural=True)
    report = {'first_cached_tokens': first['cached_tokens'], 'same_salt_cached_tokens': repeated['cached_tokens'],
              'other_salt_cached_tokens': isolated['cached_tokens'], 'natural_eos_actual_tokens': natural['completion_tokens'],
              'natural_eos_finish_reason': natural['finish_reason'], 'token_ids_returned': True}
    report['salt_isolates'] = (first['cached_tokens'] == 0 and isolated['cached_tokens'] == 0
                               and repeated['cached_tokens'] is not None and repeated['cached_tokens'] > 0)
    if not report['salt_isolates']:
        raise RuntimeError(f'cache-salt compatibility probe failed: {report}')
    await reset(old, http, base)
    return report


async def run_session(old, http, base, tok, pool, tag, tau0, depth, arm, rep, sess,
                      out_tokens, natural, kv_bytes_per_token, block_size):
    ctx, instructions = old.session_trace(pool, tau0, depth, rep, sess)
    records = []
    for stage in range(depth):
        prompt = ctx + instructions[stage]  # RD-R0 first stage: tau0+50, not tau0.
        salt = (f'R|{tag}|{tau0}|{rep}|{sess}|{stage}' if arm == 'R'
                else f'prefix|{tag}|{arm}|{tau0}|{rep}|{sess}')
        result = await stage_request(http, base, prompt, salt, out_tokens, natural)
        output = result.pop('out_ids')
        if arm == 'R' and result['cached_tokens'] != 0:
            raise RuntimeError(f"R zero-hit assertion failed rep={rep} session={sess} stage={stage+1}: {result['cached_tokens']}")
        ctx = prompt + output
        metadata = {'session_id': sess, 'rep': rep, 'stage': stage + 1, 'tokens': len(ctx)}
        text_bytes = len(tok.decode(ctx).encode('utf-8'))
        meta_bytes = len(json.dumps(metadata, sort_keys=True, separators=(',', ':')).encode('utf-8'))
        blocks = math.ceil(len(ctx) / block_size)
        result.update(stage=stage + 1, sent_prompt_len=len(prompt), prompt_sha256=token_hash(prompt),
                      out_sha256=token_hash(output), cache_salt=salt,
                      application_context_tokens=len(ctx), application_text_utf8_bytes=text_bytes,
                      application_metadata_utf8_bytes=meta_bytes, application_total_utf8_bytes=text_bytes + meta_bytes,
                      kv_context_bytes_allocator_derived=blocks * block_size * kv_bytes_per_token
                      if kv_bytes_per_token is not None else None,
                      kv_byte_basis='allocator-derived block estimate, not per-session measured allocation')
        records.append(result)
    end = records[-1]['t_end']
    for i, r in enumerate(records):
        r['application_context_hold_seconds_to_release'] = max(0, end - r['t_end'])
        r['kv_minimum_hold_seconds_until_session_completion'] = max(0, end - r['t_end'])
        r['kv_release_observed'] = False
        r['kv_lifecycle_limit'] = 'APC may retain blocks after session completion until reset/eviction; no pin or per-session release is asserted'
        r['interval_until_next_context_update_seconds'] = records[i+1]['t_end']-r['t_end'] if i+1<len(records) else 0
    return {'sess': sess, 'admitted': True, 'pipeline_seconds': end-records[0]['t_start'],
            'stages': records, 'state_released_at': end, 'pinning_guaranteed': False,
            'session_salt_mechanism': 'automatic prefix reuse' if arm != 'R' else 'forced miss'}


class Monitor:
    def __init__(self, old, http, base, exclusive, selected, pid, idle_limit):
        self.old, self.http, self.base = old, http, base
        self.exclusive, self.selected, self.pid, self.idle_limit = exclusive, selected, pid, idle_limit
        self.metrics, self.gpus, self.errors = [], [], []
        self.stop_requested = False

    async def run(self):
        import aiohttp
        gpu_at = 0.0
        while not self.stop_requested:
            tick = time.perf_counter()
            try:
                async with self.http.get(self.base + '/metrics',timeout=aiohttp.ClientTimeout(total=5)) as response:
                    self.metrics.append({'t': tick, 'metrics': self.old.parse_metrics(await response.text())})
            except Exception as exc:
                self.errors.append(f'metrics: {exc!r}')
            if self.exclusive and tick >= gpu_at:
                try:
                    snapshot = await asyncio.to_thread(gpu_inventory)
                    self.gpus.append(snapshot)
                    self.errors.extend(exclusive_errors(snapshot, self.selected, self.pid, self.idle_limit))
                except Exception as exc:
                    self.errors.append(f'GPU exclusivity probe unavailable: {exc!r}')
                gpu_at = tick + 2.0
            await asyncio.sleep(0.2)


def paired_summary(values):
    n = len(values)
    out = {'n': n, 'mean': statistics.fmean(values) if values else None,
           'ci95': None, 'status': 'unmeasured' if not values else 'insufficient_repetitions'}
    if n >= 2:
        sd = statistics.stdev(values)
        try:
            from scipy.stats import t, wilcoxon
            q = float(t.ppf(0.975, n-1)); half = q*sd/math.sqrt(n)
            out['ci95'] = [out['mean']-half, out['mean']+half]
            out['status'] = 'unresolved' if out['ci95'][0] <= 0 <= out['ci95'][1] else 'resolved_difference'
            out['wilcoxon_two_sided_p'] = float(wilcoxon(values).pvalue) if any(values) else 1.0
        except ImportError:
            out['status'] = 'CI_unavailable_scipy_missing'
    out['claim_limit'] = 'A CI containing zero is unresolved, not proof of equivalence or mechanism identity.'
    return out


def summarize_cells(directory):
    rows = []
    for path in sorted(Path(directory).glob('cells/*.json')):
        row = json.loads(path.read_text(encoding='utf-8'))
        if row.get('status') == 'complete':
            rows.append(row)
    result = {'schema': SCHEMA, 'cells': len(rows), 'comparisons': {},
              'headline': 'P-admit minus A0; P-pin minus A0 only when actually measured',
              'mechanism_equivalence_claimed': False}
    by_key = {(r['tau0'], r['concurrency'], r['depth'], r['rep'], r['arm']): r for r in rows}
    grids = sorted({(r['tau0'], r['concurrency'], r['depth']) for r in rows})
    for grid in grids:
        for lhs, rhs in (('P-admit', 'A0'), ('A0', 'R'), ('A-uncap', 'A0'), ('P-admit', 'R')):
            pairs = []
            for rep in sorted({r['rep'] for r in rows}):
                left = by_key.get((*grid, rep, lhs)); right = by_key.get((*grid, rep, rhs))
                if left and right:
                    pairs.append((left, right))
            if not pairs:
                continue
            entry = {key: paired_summary([left[key]-right[key] for left,right in pairs])
                     for key in ('pipeline_mean_seconds', 'admitted_fraction', 'ttft_mean_seconds', 'decode_mean_seconds')}
            exact = True
            for left,right in pairs:
                lt = {(s['sess'],t['stage']):(t['prompt_sha256'],t['out_sha256']) for s in left['sessions'] if s['admitted'] for t in s['stages']}
                rt = {(s['sess'],t['stage']):(t['prompt_sha256'],t['out_sha256']) for s in right['sessions'] if s['admitted'] for t in s['stages']}
                exact = exact and all(lt[k]==rt[k] for k in lt.keys() & rt.keys())
            entry['paired_token_traces_match_for_common_admitted_sessions'] = exact
            entry['same_workload_claim_permitted'] = exact
            result['comparisons'][f't{grid[0]}_c{grid[1]}_d{grid[2]}:{lhs}-{rhs}'] = entry
    atomic_json(Path(directory)/'summary.json', result)
    return result


async def measure(old, a, directory, info, process, grids, arms):
    import aiohttp
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(a.model_path, local_files_only=True)
    pool = old.word_pool(tokenizer, a.model_path)
    if not len(pool):
        raise RuntimeError('empty RD-R0 word pool')
    base = f'http://127.0.0.1:{a.port}'
    selected = {int(v) for v in a.gpus.split(',')}
    capacity = info['server_log'].get('kv_cache_tokens')
    gib = info['server_log'].get('kv_cache_gib')
    available_pool_bytes=gib*2**30*a.tp if gib is not None else None
    # "Available KV cache memory" is not actual allocation under a blocks
    # override. Do not divide the unused FP8 budget by the reduced token count.
    if a.expected_capacity is None:
        allocator_bpt=available_pool_bytes/capacity if available_pool_bytes is not None and capacity else None
        kv_basis='allocator-derived natural capacity; available memory rounded in startup log'
    else:
        allocator_bpt=info['theoretical_kv']['bytes_per_token']
        kv_basis='config-derived KV tensor size because block override leaves part of available memory unused'
    info['allocator_bytes_per_token_derived'] = allocator_bpt
    info['allocator_capacity_tokens_observed'] = capacity
    info['kv_byte_estimate_basis']=kv_basis
    info['available_KV_memory_bytes_startup']=available_pool_bytes
    info['configured_KV_pool_bytes_estimate']=capacity*allocator_bpt if capacity and allocator_bpt else None
    info['block_size_basis'] = 'explicit common server flag' if a.task=='r3' else 'requested explicit flag, verify startup log'
    if a.expected_capacity is not None and capacity != a.expected_capacity:
        raise RuntimeError(f'actual token capacity {capacity} != frozen {a.expected_capacity}')
    if 'P-admit' in arms and (not capacity or allocator_bpt is None):
        raise RuntimeError('P-admit requires observed allocator token capacity and memory')
    async with aiohttp.ClientSession() as http:
        info['api_probe'] = await api_probe(old,http,base,pool,a.out_tokens)
        atomic_json(directory/'server_info.json', info)
        for tau0, concurrency, depth in grids:
            if tau0+depth*(old.INSTR_TOK+a.out_tokens) > a.max_model_len:
                raise RuntimeError('token path exceeds max-model-len')
            for arm in arms:
                marker = directory/'warmup'/f't{tau0}_c{concurrency}_d{depth}_{arm}.done'
                if not marker.exists():
                    await reset(old,http,base)
                    await asyncio.gather(*[run_session(old,http,base,tokenizer,pool,info['run_tag'],tau0,min(depth,4),arm,-1,s,
                        a.out_tokens,a.natural,allocator_bpt,a.block_size) for s in range(min(concurrency,4))])
                    marker.parent.mkdir(parents=True,exist_ok=True)
                    marker.write_text(old.now_iso(),encoding='utf-8')
            for rep in range(a.rep_start,a.rep_start+a.reps):
                shift = rep % len(arms)
                order = list(arms[shift:]+arms[:shift])
                for position,arm in enumerate(order):
                    cid = f't{tau0}_c{concurrency}_d{depth}_{arm}_r{rep:02d}'
                    destination = directory/'cells'/f'{cid}.json'
                    if destination.exists():
                        cached = json.loads(destination.read_text(encoding='utf-8'))
                        if cached.get('status')=='complete' and cached.get('run_protocol_sha256')==info['protocol_sha256']:
                            continue
                        raise RuntimeError(f'nonmatching existing cell cannot be overwritten: {destination}')
                    pre_reset = await reset(old,http,base)
                    for attempt in range(a.max_cell_attempts):
                        if a.exclusive:
                            snap = await asyncio.to_thread(gpu_inventory)
                            errors = exclusive_errors(snap,selected,process.pid,a.idle_limit)
                            if errors:
                                atomic_json(directory/'exclusive_blocked.json', {'snapshot':snap,'errors':errors,'status':'blocked_exclusive_window'})
                                raise RuntimeError(f'BRJD exclusivity violated: {errors}')
                        peak = tau0+depth*(old.INSTR_TOK+a.out_tokens)
                        admitted = concurrency
                        if arm=='P-admit':
                            aligned = math.ceil(peak/a.block_size)*a.block_size
                            admitted = min(concurrency,math.floor((1-a.safety_margin)*capacity/aligned))
                        if not admitted:
                            raise RuntimeError('P-admit rejects all sessions; no pipeline endpoint')
                        monitor = Monitor(old,http,base,a.exclusive,selected,process.pid,a.idle_limit)
                        monitor_task = asyncio.create_task(monitor.run())
                        start_utc = old.now_iso()
                        try:
                            outcomes = await asyncio.gather(*[run_session(old,http,base,tokenizer,pool,info['run_tag'],tau0,depth,arm,rep,s,
                                a.out_tokens,a.natural,allocator_bpt,a.block_size) for s in range(admitted)],return_exceptions=True)
                            failures=[result for result in outcomes if isinstance(result,BaseException)]
                            if failures:
                                raise failures[0]
                            sessions=outcomes
                        except Exception as exc:
                            monitor.stop_requested=True
                            await monitor_task
                            atomic_json(directory/'invalid_attempts'/f'{cid}_a{attempt}.json',{'error':repr(exc),'monitor_errors':monitor.errors})
                            if 'R zero-hit assertion' in str(exc) and attempt+1<a.max_cell_attempts:
                                await reset(old,http,base)
                                continue
                            raise
                        finally:
                            monitor.stop_requested=True
                            if not monitor_task.done():
                                await monitor_task
                        if a.exclusive and monitor.errors:
                            atomic_json(directory/'invalid_attempts'/f'{cid}_exclusive.json',{'errors':monitor.errors,'gpu_samples':monitor.gpus,'status':'invalid_interference'})
                            raise RuntimeError('BRJD monitor invalidated timed cell')
                        sessions += [{'sess':s,'admitted':False,'stages':[],'reason':'capacity_admission'} for s in range(admitted,concurrency)]
                        stages = [t for s in sessions if s['admitted'] for t in s['stages']]
                        kv_usage=[sample['metrics'].get('vllm:kv_cache_usage_perc') for sample in monitor.metrics]
                        kv_usage=[value for value in kv_usage if value is not None]
                        pool_estimate=capacity*allocator_bpt if capacity and allocator_bpt else None
                        cell = {'schema':SCHEMA,'status':'complete','task':a.task,'model_key':a.model_key,'kv_dtype':a.kv_dtype,
                            'cell':cid,'arm':arm,'rep':rep,'arm_position':position,'full_arm_rotation_order':order,
                            'tau0':tau0,'concurrency':concurrency,'depth':depth,'workload_seed':old.WORKLOAD_SEED,
                            'instr_tok':old.INSTR_TOK,'out_tok_max':a.out_tokens,'natural_eos':a.natural,
                            'run_protocol_sha256':info['protocol_sha256'],'host':socket.gethostname(),
                            'utc_start':start_utc,'utc_end':old.now_iso(),'cache_reset_tries':pre_reset,
                            'admission_budget_tokens':(1-a.safety_margin)*capacity if capacity else None,
                            'peak_tokens_conservative_upper_bound':peak,'admitted_fraction':admitted/concurrency,
                            'pipeline_mean_seconds':statistics.fmean(s['pipeline_seconds'] for s in sessions if s['admitted']),
                            'ttft_mean_seconds':statistics.fmean(t['ttft'] for t in stages if t['ttft'] is not None),
                            'decode_mean_seconds':statistics.fmean(t['decode_seconds'] for t in stages if t['decode_seconds'] is not None),
                            'sessions':sessions,'metric_samples':monitor.metrics,'gpu_samples':monitor.gpus,'monitor_errors':monitor.errors,
                            'KV_pool_sampled_peak_fraction':max(kv_usage) if kv_usage else None,
                            'KV_pool_sampled_peak_bytes_estimate':max(kv_usage)*pool_estimate if kv_usage and pool_estimate else None,
                            'KV_byte_estimate_basis':kv_basis,
                            'application_byte_scope':'serialized canonical context text and metadata, not observed edge DB allocation',
                            'machine_label':'DESKTOP-BRJD313 / WSL2 / RTX 5090 D','gpus':a.gpus,
                            'observed_KV_capacity_tokens':capacity,
                            'capacity_control':'client rejection before execution; server remains automatic prefix cache/LRU',
                            'peak_budget_basis':'allocator-derived B/token times token-path maximum, not per-session measured peak'}
                        atomic_json(destination,cell)
                        print(f"{old.now_iso()} {a.task} {cid} seconds={cell['pipeline_mean_seconds']:.3f}",flush=True)
                        break
                    else:
                        raise RuntimeError(f'no valid attempt for {cid}')
    return summarize_cells(directory)


def args_for_server(a,dtype,configuration,cap=None,uncap=False):
    server = SimpleNamespace(**vars(a))
    server.kv_dtype=dtype; server.offload_gib=0; server.enforce_eager=False
    server.extra=['--block-size',str(a.block_size)]
    if a.attention_backend:
        server.extra += ['--attention-backend',a.attention_backend]
    if cap is not None:
        if cap % a.block_size:
            raise RuntimeError('token capacity must be a multiple of frozen block size')
        server.extra += ['--num-gpu-blocks-override',str(cap//a.block_size)]
    if uncap:
        # Never lower the stack's own default limit: removing admission caps is
        # a max(), not merely setting the small benchmark concurrency.
        server.extra += ['--max-num-seqs',str(a.uncap_limit)]
    server.run_tag=f'rplus|{a.model_key}|{configuration}'
    server.expected_capacity=cap
    return server


def run_config(old,a,directory,dtype,configuration,arms,cap=None,uncap=False):
    directory.mkdir(parents=True,exist_ok=True)
    server=args_for_server(a,dtype,configuration,cap,uncap)
    protocol={'schema':SCHEMA,'script_sha256':sha256_file(__file__),'kvbench_sha256':sha256_file(a.kvbench_path),
              'arguments':vars(server),'arms':arms,'grids':a.grids,'reps':a.reps}
    digest=hashlib.sha256(json.dumps(protocol,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
    protocol_file=directory/'protocol.json'
    if protocol_file.exists() and json.loads(protocol_file.read_text(encoding='utf-8'))['sha256']!=digest:
        raise RuntimeError(f'protocol/hash changed; use a new output root: {directory}')
    atomic_json(protocol_file,{'sha256':digest,'protocol':protocol})
    if a.exclusive:
        snap=gpu_inventory()
        errors=exclusive_errors(snap,{int(g) for g in a.gpus.split(',')},idle_limit=a.idle_limit)
        atomic_json(directory/'exclusive_before_launch.json',{'snapshot':snap,'errors':errors})
        if errors:
            raise RuntimeError(f'exclusive pre-launch check failed: {errors}')
    process,argv=old.launch_server(server,str(directory/'server.log'))
    info={'schema':SCHEMA,'protocol_sha256':digest,'run_tag':server.run_tag,'host':socket.gethostname(),
          'python':platform.python_version(),'package_versions':runtime_versions(),'started_utc':old.now_iso(),'serve_cmd':argv,
          'model_fingerprint':old.model_fingerprint(a.model_path),'gpus':a.gpus,'kv_dtype':dtype,'tp':a.tp,
          'block_size_requested':a.block_size,'backend_requested':a.attention_backend,
          'theoretical_kv':old.theoretical_kv(a.model_path,dtype)}
    try:
        info['ready_seconds']=asyncio.run(old.wait_ready(f'http://127.0.0.1:{a.port}',process,a.ready_timeout))
        info['server_log']=old.parse_server_log(str(directory/'server.log'))
        startup=Path(directory/'server.log').read_text(encoding='utf-8',errors='replace')
        startup=startup[startup.rfind('==== '):]
        info['attention_backend_log_evidence']=[line for line in startup.splitlines()
             if 'attention' in line.lower() or 'TRITON' in line][:100]
        atomic_json(directory/'server_info.json',info)
        asyncio.run(measure(old,server,directory,info,process,a.grids,tuple(arms)))
        atomic_json(directory/'DONE.json',{'status':'calibration_only_no_timing_cells' if getattr(a,'calibration_only',False) else 'measured','protocol_sha256':digest,'finished_utc':old.now_iso()})
        return info
    except Exception as exc:
        atomic_json(directory/'FAILED.json',{'status':'failed','error':repr(exc),'traceback':traceback.format_exc(),'protocol_sha256':digest})
        raise
    finally:
        old.stop_server(process)


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task',choices=('r2','r3','r4'),required=True)
    for key in ('model-key','model-path','gpus','out-root'):
        p.add_argument('--'+key,required=True)
    p.add_argument('--port',type=int,default=8180)
    p.add_argument('--tp',type=int,default=1)
    p.add_argument('--kv-dtype',default='auto')
    p.add_argument('--gpu-util',type=float,default=.90)
    p.add_argument('--max-model-len',type=int,default=12288)
    p.add_argument('--kvbench-path',default=str(Path(__file__).with_name('kvbench.py')))
    p.add_argument('--tau0',type=int,default=3400)
    p.add_argument('--depth',type=int,default=8)
    p.add_argument('--concurrency',type=int,default=4)
    p.add_argument('--reps',type=int,default=10)
    p.add_argument('--rep-start',type=int,default=0,help='resume or predeclared repeat block start index')
    p.add_argument('--out-tokens',type=int,default=128)
    p.add_argument('--block-size',type=int,default=16)
    p.add_argument('--safety-margin',type=float,default=.05)
    p.add_argument('--attention-backend',default='')
    p.add_argument('--token-capacity',default='auto',help='R3: auto reads bf16/TRITON budget first; or explicit token count')
    p.add_argument('--uncap-limit',type=int,default=0,
                   help='R4: must be >= installed default max_num_seqs and benchmark concurrency')
    p.add_argument('--ready-timeout',type=int,default=1800)
    p.add_argument('--idle-limit',type=float,default=5)
    p.add_argument('--allow-shared',action='store_true',help='explicit diagnostic opt-out; wrappers never use this flag')
    p.add_argument('--max-cell-attempts',type=int,default=3)
    return p


def main():
    a=parser().parse_args()
    if a.reps<10:
        raise SystemExit('BRJD formal comparisons require at least ten predeclared paired repetitions')
    if a.tp != 1 or a.gpus != '0':
        raise SystemExit('BRJD native jobs require GPU 0 with TP=1')
    a.grids=[(a.tau0,a.concurrency,a.depth)]
    a.natural=False; a.exclusive=not a.allow_shared; a.expected_capacity=None
    old=load_old(a.kvbench_path)
    if not Path(a.model_path,'config.json').is_file():
        raise SystemExit('model config absent; no download attempted')
    root=Path(a.out_root)/a.model_key/a.task
    root.mkdir(parents=True,exist_ok=True)
    if a.task=='r2':
        run_natural_fixed_pair(old,a,root)
    elif a.task=='r3':
        run_precision(old,a,root)
    else:
        run_native_arms(old,a,root)
    return 0


def precision_summary(root):
    """Paired precision contrast; trajectories that diverge are separately flagged."""
    comparison={}
    for arm in ('A0','R'):
        pairs=[]
        for fp in (root/'fp8'/'cells').glob(f'*_{arm}_r*.json'):
            other=root/'auto'/'cells'/fp.name
            if other.is_file():
                pairs.append((json.loads(fp.read_text(encoding='utf-8')),
                              json.loads(other.read_text(encoding='utf-8'))))
        if not pairs:
            continue
        entry={key:paired_summary([fp[key]-bf[key] for fp,bf in pairs])
               for key in ('pipeline_mean_seconds','ttft_mean_seconds','decode_mean_seconds')}
        same=True
        for fp,bf in pairs:
            left={(s['sess'],t['stage']):(t['prompt_sha256'],t['out_sha256']) for s in fp['sessions'] for t in s['stages']}
            right={(s['sess'],t['stage']):(t['prompt_sha256'],t['out_sha256']) for s in bf['sessions'] for t in s['stages']}
            same=same and left==right
        entry['identical_generated_token_paths']=same
        entry['interpretation']='FP8-minus-bf16; same capacity/backend configuration; output drift prevents identical-trace latency attribution'
        comparison[arm]=entry
    atomic_json(root/'precision_summary.json',{'status':'measured_paired_precision','comparisons':comparison})


def calibration_capacity(old,a,root,configuration,uncap=False):
    """Untimed startup/API probe. No formal data or old-host results are consumed."""
    cal=SimpleNamespace(**vars(a))
    cal.grids=[]; cal.reps=0; cal.rep_start=0; cal.natural=False; cal.calibration_only=True
    info=run_config(old,cal,root,a.kv_dtype,configuration,('A0','R'),uncap=uncap)
    capacity=info['server_log'].get('kv_cache_tokens')
    if not isinstance(capacity,int) or capacity<1 or capacity%a.block_size:
        raise RuntimeError('BRJD natural capacity absent or not whole frozen blocks')
    return capacity,info


def freeze_common_capacity(old,a,root,definitions):
    """Calibration happens before any formal cells; prior freeze is strict resume."""
    signature={'schema':SCHEMA,'script_sha256':sha256_file(__file__),
        'kvbench_sha256':sha256_file(a.kvbench_path),'model_fingerprint':old.model_fingerprint(a.model_path),
        'model_path':a.model_path,'kv_dtype':a.kv_dtype,'gpus':a.gpus,'tp':a.tp,
        'gpu_util':a.gpu_util,'max_model_len':a.max_model_len,'block_size':a.block_size,
        'attention_backend':a.attention_backend,'uncap_limit':a.uncap_limit,
        'explicit_capacity':a.token_capacity,'definitions':definitions,'host':socket.gethostname(),
        'runtime_versions':runtime_versions()}
    sig=hashlib.sha256(json.dumps(signature,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    freeze=root/'capacity_freeze.json'
    if freeze.exists():
        obj=json.loads(freeze.read_text(encoding='utf-8'))
        if obj.get('signature_sha256')!=sig:
            raise RuntimeError('BRJD capacity calibration configuration changed; use a new output root')
        return obj['token_capacity']
    observed={}
    for label,uncap in definitions:
        n,info=calibration_capacity(old,a,root/'calibration'/label,'calibration-'+label,uncap)
        observed[label]={'natural_capacity_tokens':n,'server_info_sha256':sha256_file(root/'calibration'/label/'server_info.json')}
    capacity=min(x['natural_capacity_tokens'] for x in observed.values())
    if a.token_capacity!='auto':
        explicit=int(a.token_capacity)
        if explicit<1 or explicit%a.block_size or explicit>capacity:
            raise RuntimeError('explicit BRJD capacity is not whole blocks or exceeds a measured natural capacity')
        capacity=explicit
    atomic_json(freeze,{'signature_sha256':sig,'configuration':signature,'observed':observed,
        'token_capacity':capacity,'num_gpu_blocks':capacity//a.block_size,'block_size':a.block_size,
        'source':'minimum independently observed BRJD capacities, frozen before formal cells',
        'scope':'matched-capacity supplemental comparison; no old host 71840-token assumption',
        'frozen_utc':old.now_iso()})
    return capacity


def collect_mode_cells(root,mode):
    dest=root/mode/'combined'
    for path in (root/'repetitions').glob(f'r*/{mode}/cells/*.json'):
        row=json.loads(path.read_text(encoding='utf-8'))
        if row.get('status')!='complete':
            continue
        row['source_cell_sha256']=sha256_file(path)
        atomic_json(dest/'cells'/path.name,row)
    summarize_cells(dest)
    return dest


def fixed_natural_report(a,root,fixed_dir,natural_dir):
    """Same-host repeated contrasts. Length changes are part of the EOS policy."""
    def read_mode(directory):
        return {(r['rep'],r['arm']):r for p in (directory/'cells').glob('*.json')
                if (r:=json.loads(p.read_text(encoding='utf-8'))).get('status')=='complete'}
    fixed,natural=read_mode(fixed_dir),read_mode(natural_dir)
    expected={(rep,arm) for rep in range(a.rep_start,a.rep_start+a.reps) for arm in ('A0','R')}
    if set(fixed)!=expected or set(natural)!=expected:
        raise RuntimeError('fixed/natural paired cohort is incomplete; no complete report')
    report={'schema':SCHEMA,'status':'measured_same_host_fixed_vs_natural','host':socket.gethostname(),
        'capacity_freeze_sha256':sha256_file(root/'capacity_freeze.json'),'comparisons':{},
        'interpretation':'EOS policy changes token lengths and later prompts; timing differences are descriptive policy effects, not isolated cache effects',
        'old_host_parallel_reference_used':False,'mechanism_equivalence_claimed':False,
        'formal_reps':a.reps,'generation_modes':{}}
    for mode,rows in (('fixed',fixed),('natural',natural)):
        stages=[st for row in rows.values() for session in row['sessions'] for st in session['stages']]
        lengths=[st['completion_tokens'] for st in stages]
        if mode=='fixed' and any(n!=a.out_tokens for n in lengths):
            raise RuntimeError('fixed reference contains non-fixed output')
        if mode=='natural' and any(n<0 or n>a.out_tokens for n in lengths):
            raise RuntimeError('natural output length exceeds frozen maximum')
        report['generation_modes'][mode]={'actual_output_tokens':{'n':len(lengths),'min':min(lengths),'max':max(lengths),'mean':statistics.fmean(lengths)},
            'finish_reasons':{reason:sum(str(s['finish_reason'])==reason for s in stages) for reason in sorted({str(s['finish_reason']) for s in stages})},
            'EOS_or_other_stop_observed':any(s['finish_reason']=='stop' for s in stages),
            'shorter_than_max_output_fraction':sum(n<a.out_tokens for n in lengths)/len(lengths),
            'A0_minus_R_seconds':paired_summary([rows[(rep,'A0')]['pipeline_mean_seconds']-rows[(rep,'R')]['pipeline_mean_seconds']
                for rep in range(a.rep_start,a.rep_start+a.reps)])}
    for arm in ('A0','R'):
        pairs=[(natural[(rep,arm)],fixed[(rep,arm)]) for rep in range(a.rep_start,a.rep_start+a.reps)]
        for n,f in pairs:
            if n['host']!=f['host'] or n['host']!=socket.gethostname() or n['gpus']!=f['gpus']:
                raise RuntimeError('cross-host or cross-GPU fixed/natural pairing forbidden')
            if n['observed_KV_capacity_tokens']!=f['observed_KV_capacity_tokens']:
                raise RuntimeError('fixed/natural actual KV capacities differ')
            nt={(s['sess'],st['stage']):st['prompt_sha256'] for s in n['sessions'] for st in s['stages']}
            ft={(s['sess'],st['stage']):st['prompt_sha256'] for s in f['sessions'] for st in s['stages']}
            if any(nt.get((s,1))!=ft.get((s,1)) for s in range(a.concurrency)):
                raise RuntimeError('initial fixed/natural prompts do not match')
        report['comparisons'][arm]={key:paired_summary([n[key]-f[key] for n,f in pairs])
            for key in ('pipeline_mean_seconds','ttft_mean_seconds','decode_mean_seconds')}
        report['comparisons'][arm]['direction']='natural minus fixed'
        report['comparisons'][arm]['initial_prompt_hashes_verified']=True
    difference=[(natural[(rep,'A0')]['pipeline_mean_seconds']-natural[(rep,'R')]['pipeline_mean_seconds'])
                -(fixed[(rep,'A0')]['pipeline_mean_seconds']-fixed[(rep,'R')]['pipeline_mean_seconds'])
                for rep in range(a.rep_start,a.rep_start+a.reps)]
    report['cache_contrast_change_natural_minus_fixed']=paired_summary(difference)
    fm=report['generation_modes']['fixed']['A0_minus_R_seconds']['mean']
    nm=report['generation_modes']['natural']['A0_minus_R_seconds']['mean']
    report['descriptive_mean_sign_reversed']=fm*nm<0
    report['sign_limit']='mean sign check is descriptive; confidence intervals do not establish equivalence'
    report['EOS_robustness_limit']='No EOS robustness conclusion if all natural requests reach max_tokens.'
    atomic_json(root/'fixed_vs_natural_report.json',report)
    return report


def run_natural_fixed_pair(old,a,root):
    a.grids=[(3400,4,8)]
    capacity=freeze_common_capacity(old,a,root,[('default',False)])
    for rep in range(a.rep_start,a.rep_start+a.reps):
        order=('fixed','natural') if rep%2==0 else ('natural','fixed')
        for position,mode in enumerate(order):
            block=SimpleNamespace(**vars(a))
            block.reps=1; block.rep_start=rep; block.natural=mode=='natural'
            block.group_rotation_order=order; block.group_rotation_position=position
            run_config(old,block,root/'repetitions'/f'r{rep:02d}'/mode,a.kv_dtype,'r2-'+mode,('A0','R'),cap=capacity)
    fixed_dir=collect_mode_cells(root,'fixed')
    natural_dir=collect_mode_cells(root,'natural')
    fixed_natural_report(a,root,fixed_dir,natural_dir)


def run_precision(old,a,root):
    if not a.attention_backend:
        a.attention_backend='TRITON_ATTN'
    a.grids=[(900,4,32)]
    help_text=command([old.VLLM_BIN,'serve','--help=all'],timeout=60)
    cli={'attention_backend_flag':'--attention-backend' in help_text,
         'gpu_blocks_override_flag':'--num-gpu-blocks-override' in help_text,
         'block_size_flag':'--block-size' in help_text}
    atomic_json(root/'cli_probe.json',cli)
    if not all(cli.values()):
        atomic_json(root/'UNMEASURED.json',{'status':'unmeasured','reason':'required same-backend/capacity CLI unavailable','cli_probe':cli})
        return
    frozen=None if a.token_capacity=='auto' else int(a.token_capacity)
    try:
        bf16=run_config(old,a,root/'auto','auto','r3-auto',('A0','R'),cap=frozen)
        capacity=bf16['server_log'].get('kv_cache_tokens')
        if not capacity or capacity%a.block_size:
            raise RuntimeError('observed bf16/TRITON token capacity absent or not whole frozen blocks')
        atomic_json(root/'capacity_freeze.json',{'token_capacity':capacity,'num_gpu_blocks':capacity//a.block_size,
             'block_size':a.block_size,'backend':a.attention_backend,'source':'actual bf16 startup under common backend',
             'bf16_server_info_sha256':sha256_file(root/'auto'/'server_info.json')})
        fp8=run_config(old,a,root/'fp8','fp8','r3-fp8',('A0','R'),cap=capacity)
        actual=fp8['server_log'].get('kv_cache_tokens')
        if actual!=capacity:
            raise RuntimeError(f'FP8 actual token capacity {actual} != bf16 {capacity}')
        atomic_json(root/'precision_pair.json',{'status':'measured','auto_tokens':capacity,'fp8_tokens':actual,
            'same_backend_requested':a.attention_backend,'same_token_capacity_verified':True,
            'fp8_num_gpu_blocks_override':capacity//a.block_size,
            'claim_limit':'output hashes must match for identical-token-trace timing attribution'})
        precision_summary(root)
    except Exception as exc:
        atomic_json(root/'UNMEASURED_PRECISION_PAIR.json',{'status':'unmeasured_precision_pair','reason':repr(exc),
            'retain_partial_measurements':True,'no_environment_mutation_attempted':True})
        raise


def installed_default_max_num_seqs(a):
    # EngineArgs applies API-server/hardware defaults, which may differ from the
    # raw SchedulerConfig dataclass defaults. No engine or weights are allocated.
    from vllm.engine.arg_utils import EngineArgs
    from vllm.usage.usage_lib import UsageContext
    previous=os.environ.get('CUDA_VISIBLE_DEVICES')
    os.environ['CUDA_VISIBLE_DEVICES']=a.gpus
    try:
        args=EngineArgs(model=a.model_path,max_model_len=a.max_model_len,
                       tensor_parallel_size=a.tp,gpu_memory_utilization=a.gpu_util,
                       kv_cache_dtype=a.kv_dtype,enable_prefix_caching=True)
        configuration=args.create_engine_config(usage_context=UsageContext.OPENAI_API_SERVER)
    finally:
        if previous is None:
            os.environ.pop('CUDA_VISIBLE_DEVICES',None)
        else:
            os.environ['CUDA_VISIBLE_DEVICES']=previous
    value=getattr(configuration.scheduler_config,'max_num_seqs',None)
    if not isinstance(value,int) or value<1:
        raise RuntimeError('installed scheduler default max_num_seqs cannot be established')
    return value


def run_native_arms(old,a,root):
    a.grids=[(3400,4,8),(900,16,8)]
    default_limit=installed_default_max_num_seqs(a)
    a.uncap_limit=max(a.uncap_limit,default_limit,max(c for _,c,_ in a.grids))
    atomic_json(root/'admission_definition.json',{'stack_default_max_num_seqs':default_limit,
        'A_uncap_max_num_seqs':a.uncap_limit,'delta':a.safety_margin,
        'rule':'pre-admit sum block-rounded token-path peak <= (1-delta) observed KV token budget',
        'allocation_basis':'allocator-derived B/token times conservative fixed token-path maximum',
        'P_admit_calls_pin':False,'P_pin_status':'separate external task, unmeasured in native runner',
        'S_radix_status':'separate external task, unmeasured in native runner'})
    capacity=freeze_common_capacity(old,a,root,[('default',False),('uncapped',True)])
    # Rotate configuration groups as well as arms; a single default-server block
    # followed by one uncapped block would confound the contrast with wall time.
    for rep in range(a.rep_start,a.rep_start+a.reps):
        order=('default','uncapped') if rep%2==0 else ('uncapped','default')
        for position,configuration in enumerate(order):
            block=SimpleNamespace(**vars(a))
            block.reps=1
            block.rep_start=rep
            block.group_rotation_order=order
            block.group_rotation_position=position
            directory=root/'repetitions'/f'r{rep:02d}'/configuration
            arms=('R','A0') if configuration=='default' else ('A-uncap','P-admit')
            run_config(old,block,directory,a.kv_dtype,f'r4-{configuration}',arms,cap=capacity,uncap=configuration=='uncapped')
    merged=root/'combined'
    for path in (root/'repetitions').glob('r*/*/cells/*.json'):
        row=json.loads(path.read_text(encoding='utf-8'))
        row['source_configuration']=path.parent.parent.name
        row['source_cell_sha256']=sha256_file(path)
        atomic_json(merged/'cells'/path.name,row)
    summarize_cells(merged)
    atomic_json(root/'external_arm_status.json',{'P-pin':{'status':'unmeasured','reason':'native runner implements no explicit pin'},
        'S-radix':{'status':'unmeasured','reason':'external SGLang task owns radix-self-R comparison'}})


if __name__=='__main__':
    def terminate(signum, frame):
        raise SystemExit(128+signum)
    signal.signal(signal.SIGTERM,terminate)
    sys.exit(main())
