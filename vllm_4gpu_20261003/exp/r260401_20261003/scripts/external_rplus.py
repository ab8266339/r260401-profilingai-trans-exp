#!/usr/bin/env python3
"""Same fixed token traces, separate within-engine RD-R+ companions.

SGLang: S-radix versus whole-stage-barrier R-self (flush, then concurrent group).
LMCache: P-pin-CPU versus CPU-LRU; fixed 1.5x CPU-cache churn before final stage.
CPU pin is not GPU pin. External engine absolute latencies are never pooled with
the native vLLM arms. Each completed cell is immutable/resumable JSON evidence.
"""
from __future__ import annotations
import argparse, asyncio, datetime, hashlib, importlib.metadata, json, math, os, pathlib, signal, statistics, sys, time
import aiohttp
import numpy as np
from external_probe import LocalServer, inspected_environment, atomic_json, handle_termination, model_identity, sha256_file
from kvbench import session_trace, word_pool, WORKLOAD_SEED

GRIDS=[(3400,4,8),(900,16,8)]
OUT_TOK=128

def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()

def require_final_external_hit(arm,result,required,stage,session):
    # With vLLM APC disabled, cached_tokens is the external KV count. vLLM
    # PrefillStats sums local+external hits; prompt-token-details exposes it.
    cached=result["cached_tokens"]
    if cached is None or not isinstance(cached,int):
        raise RuntimeError(f"LMCache final request has no explicit cached-token usage: stage={stage}, sess={session}, cached={cached}")
    if arm=="P-pin-CPU" and cached<required:
        raise RuntimeError(f"Retained pinned CPU prefix was not actually retrieved: stage={stage}, sess={session}, cached={cached}, required={required}")
    if arm=="CPU-LRU" and cached!=0:
        raise RuntimeError(f"Evicted CPU-LRU control has unexpected final retrieval: stage={stage}, sess={session}, cached={cached}")
    result["required_previous_prefill_chunks_tokens"]=required
    result["actual_external_hit_verified"]=True

async def request_stage(http, client, prompt, output_tokens=OUT_TOK):
    if client.engine=="sglang":
        route="/generate"
        body={"input_ids":prompt,"sampling_params":{"temperature":0,"max_new_tokens":output_tokens,
            "min_new_tokens":output_tokens,"ignore_eos":True},"stream":True}
    else:
        route="/v1/completions"
        body={"model":"r260401_external","prompt":prompt,"temperature":0,"max_tokens":output_tokens,
            "min_tokens":output_tokens,"ignore_eos":True,"stream":True,
            "stream_options":{"include_usage":True},"return_token_ids":True}
    begin=time.perf_counter(); first=None; output_ids=[]; usage={}; meta={}; finish=None
    buf=b""; stream_events=0
    async with http.post(client.base+route,json=body,timeout=aiohttp.ClientTimeout(total=1800)) as response:
        if response.status!=200: raise RuntimeError(f"HTTP {response.status}: {(await response.text())[:1500]}")
        async for chunk in response.content.iter_any():
            buf+=chunk
            while b"\n\n" in buf:
                event,buf=buf.split(b"\n\n",1)
                for line in event.splitlines():
                    if not line.startswith(b"data:"): continue
                    raw=line[5:].strip()
                    if raw==b"[DONE]": continue
                    data=json.loads(raw); stream_events+=1
                    if client.engine=="sglang":
                        ids=data.get("output_ids") or []
                        # Native /generate with default cumulative streaming sends total output IDs.
                        if ids: output_ids=list(ids)
                        if first is None and ids: first=time.perf_counter()-begin
                        meta=data.get("meta_info",meta); finish=meta.get("finish_reason",finish)
                    else:
                        usage=data.get("usage") or usage
                        for choice in data.get("choices") or []:
                            ids=choice.get("token_ids") or []
                            output_ids.extend(ids)
                            if first is None and (ids or choice.get("text")): first=time.perf_counter()-begin
                            finish=choice.get("finish_reason") or finish
    end=time.perf_counter()
    if client.engine=="sglang":
        completion=meta.get("completion_tokens"); cached=meta.get("cached_tokens"); prompt_count=meta.get("prompt_tokens")
    else:
        completion=usage.get("completion_tokens"); cached=(usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        prompt_count=usage.get("prompt_tokens")
    if completion!=output_tokens or len(output_ids)!=output_tokens or prompt_count!=len(prompt) or first is None:
        raise RuntimeError(f"Token/stream gate failed: sent={len(prompt)}, prompt={prompt_count}, completion={completion}, ids={len(output_ids)}, first={first}")
    return {"ttft":first,"e2e":end-begin,"decode_seconds":end-begin-first,"t_start":begin,"t_end":end,
        "prompt_tokens":prompt_count,"completion_tokens":completion,"cached_tokens":cached,"out_ids":output_ids,
        "out_sha256":hashlib.sha256(np.asarray(output_ids,dtype=np.int64).tobytes()).hexdigest(),
        "finish_reason":finish,"stream_events":stream_events}

async def cell(http,client,pool,tau0,concurrency,depth,rep,arm,cpu_gb,model_config):
    sessions=[session_trace(pool,tau0,depth,rep,s) for s in range(concurrency)]
    ctx=[list(pair[0]) for pair in sessions]; records=[[] for _ in sessions]
    workflow_begin=time.perf_counter(); flush_seconds=0; flush_count=0
    if client.engine=="sglang":
        flush_begin=time.perf_counter(); await asyncio.to_thread(client.flush_gpu)
        flush_seconds+=time.perf_counter()-flush_begin; flush_count+=1
    # LMCache gets a fresh worker+server for every cell: its clear API skips pinned
    # objects, and silently retaining another trial's pins would corrupt the comparison.
    pressure=None; stage_http_seconds=0
    for stage in range(depth):
        if client.engine=="sglang" and arm=="R-self":
            # All previous requests have finished. A single group flush precedes this stage.
            flush_begin=time.perf_counter(); await asyncio.to_thread(client.flush_gpu)
            flush_seconds+=time.perf_counter()-flush_begin; flush_count+=1
        if client.engine=="lmcache" and stage==depth-1:
            heads=model_config.get("num_key_value_heads",model_config["num_attention_heads"])
            dim=model_config.get("head_dim",model_config["hidden_size"]//model_config["num_attention_heads"])
            bpt=2*model_config["num_hidden_layers"]*heads*dim*2
            nominal=int(cpu_gb*1024**3/bpt); churn_len=2048
            churn_count=math.ceil(1.5*nominal/churn_len)
            pressure={"cpu_cache_gb":cpu_gb,"nominal_capacity_tokens":nominal,"churn_requests":churn_count,
                "churn_prompt_tokens":churn_len,"nominal_pressure_factor":1.5,"chunk_size":256}
            pressure_begin=time.perf_counter()
            for index in range(churn_count):
                # Fixed, disjoint synthetic token trace; identical across pin/unpin trial pair.
                churn=np.random.default_rng([WORKLOAD_SEED,999,tau0,rep+1000,index]).choice(pool,churn_len).tolist()
                await request_stage(http,client,churn,1)
            pressure["churn_wall_seconds"]=time.perf_counter()-pressure_begin
            await asyncio.sleep(2)
            pressure["session_prefix_lookup"]=[]
            for s,context in enumerate(ctx):
                chunks=context[:len(context)//256*256]
                observed=await asyncio.to_thread(client.lookup,chunks)
                required=records[s][-1]["sent_prompt_len"]//256*256
                observed["required_previous_prefill_chunks_tokens"]=required
                pressure["session_prefix_lookup"].append(observed)
                if arm=="P-pin-CPU" and observed["tokens"]<required:
                    raise RuntimeError(f"Pinned CPU prefix lost under pressure in measured cell: sess={s} {observed}")
                if arm=="CPU-LRU" and observed["tokens"]>0:
                    raise RuntimeError(f"Unpinned CPU control was not evicted under fixed pressure: sess={s} {observed}")
        prompts=[context+sessions[s][1][stage] for s,context in enumerate(ctx)]
        stage_begin=time.perf_counter()
        batch=await asyncio.gather(*(request_stage(http,client,prompt) for prompt in prompts))
        stage_end=time.perf_counter()
        stage_http_seconds+=stage_end-stage_begin
        for s,result in enumerate(batch):
            if client.engine=="sglang" and arm=="R-self" and result["cached_tokens"]!=0:
                raise RuntimeError(f"R-self group flush did not yield zero-hit replay: stage={stage+1}, sess={s}, cached={result['cached_tokens']}")
            if client.engine=="sglang" and arm=="S-radix" and stage>0 and (result["cached_tokens"] or 0)<256:
                raise RuntimeError(f"S-radix stage has no substantive prefix-reuse evidence: stage={stage+1}, sess={s}, cached={result['cached_tokens']}")
            if client.engine=="lmcache" and stage==depth-1:
                required=pressure["session_prefix_lookup"][s]["required_previous_prefill_chunks_tokens"]
                require_final_external_hit(arm,result,required,stage+1,s)
            ids=result.pop("out_ids"); ctx[s]=prompts[s]+ids
            result.update({"stage":stage+1,"sess":s,"sent_prompt_len":len(prompts[s]),
                "input_sha256":hashlib.sha256(np.asarray(prompts[s],dtype=np.int64).tobytes()).hexdigest(),
                "stage_group_seconds":stage_end-stage_begin})
            records[s].append(result)
        if client.engine=="lmcache" and arm=="P-pin-CPU" and stage<depth-1:
            # Pin only complete prefill chunks that were admitted; generated tails may not be stored yet.
            for s,prompt in enumerate(prompts):
                chunks=prompt[:len(prompt)//256*256]
                deadline=time.monotonic()+20; lookup=None
                while time.monotonic()<deadline:
                    lookup=await asyncio.to_thread(client.lookup,chunks)
                    if lookup["tokens"]>=len(chunks): break
                    await asyncio.sleep(.25)
                if lookup is None or lookup["tokens"]<len(chunks): raise RuntimeError(f"CPU prefix not admitted before pin: session={s} {lookup}")
                pin_begin=time.perf_counter()
                operation=await asyncio.to_thread(client.pin,chunks)
                records[s][-1]["cpu_pin_seconds"]=time.perf_counter()-pin_begin
                records[s][-1]["pin_operation"]=operation
                if int(operation["pin_response"]["num_tokens"])<len(chunks): raise RuntimeError("Partial CPU pin, no valid pin measurement")
    metrics={"workflow_wall_seconds_including_pin_pressure_barriers":time.perf_counter()-workflow_begin,
        "http_stage_group_wall_seconds_sum":stage_http_seconds,
        "http_request_e2e_seconds_sum":sum(result["e2e"] for session in records for result in session),
        "per_session_http_e2e_seconds_sum_mean":statistics.mean(sum(result["e2e"] for result in session) for session in records),
        "per_session_http_ttft_seconds_sum_mean":statistics.mean(sum(result["ttft"] for result in session) for session in records),
        "per_session_http_decode_seconds_sum_mean":statistics.mean(sum(result["decode_seconds"] for result in session) for session in records),
        "final_stage_retrieval_ttft_seconds_mean":statistics.mean(session[-1]["ttft"] for session in records),
        "controller_pin_seconds_sum":sum(result.get("cpu_pin_seconds",0) for session in records for result in session),
        "pressure_churn_wall_seconds":pressure["churn_wall_seconds"] if pressure else 0,
        "gpu_flush_wall_seconds_sum":flush_seconds,"gpu_flush_count":flush_count}
    return {"engine":client.engine,"arm":arm,"tau0":tau0,"concurrency":concurrency,"depth":depth,"rep":rep,
        "sessions":records,"pressure_before_final_stage":pressure,"status":"MEASURED",
        "pin_location":"LocalCPUBackend" if arm=="P-pin-CPU" else None,"explicit_gpu_pin":False,
        "within_engine_only":True,"metrics":metrics}

def statistical_summary(results,arms,reps):
    try:
        from scipy.stats import t
    except ImportError:
        t=None
    metrics=[name for name in results[0]["metrics"] if name!="gpu_flush_count"]
    summaries=[]
    for tau0,concurrency,depth in GRIDS:
        cells=[result for result in results if result["tau0"]==tau0 and result["concurrency"]==concurrency
            and result["depth"]==depth and result["rep"]>=0]
        by_arm={arm:{result["rep"]:result for result in cells if result["arm"]==arm} for arm in arms}
        if any(set(by_arm[arm])!=set(range(reps)) for arm in arms):
            raise RuntimeError("Incomplete measured repetitions; no paired statistical summary permitted")
        means={arm:{metric:statistics.mean(by_arm[arm][rep]["metrics"][metric] for rep in range(reps))
            for metric in metrics} for arm in arms}
        paired={}
        for metric in metrics:
            values=[by_arm[arms[0]][rep]["metrics"][metric]-by_arm[arms[1]][rep]["metrics"][metric] for rep in range(reps)]
            average=statistics.mean(values); ci=None
            if t is not None and reps>=2:
                half=float(t.ppf(.975,reps-1))*statistics.stdev(values)/math.sqrt(reps)
                ci=[average-half,average+half]
            paired[metric]={"n_pairs":reps,"mean_difference_seconds":average,"ci95_seconds":ci,
                "ci_method":"paired Student t over independent repetition differences" if ci is not None else "UNAVAILABLE: scipy absent or fewer than 2 repetitions"}
        summaries.append({"tau0":tau0,"concurrency":concurrency,"depth":depth,"arm_means_seconds":means,
            "paired_difference":f"{arms[0]} minus {arms[1]}","negative_difference_favors":arms[0],
            "paired_metrics":paired})
    return {"warmups_excluded":True,"within_engine_only":True,"grids":summaries,
        "intervals_are_unadjusted_marginal_not_simultaneous":True,
        "interval_assumptions":"Independent repetition differences are approximately normal; each interval is marginal 95%, with no family-wise coverage claim.",
        "decision_status":"DESCRIPTIVE_EXTERNAL_ARMS_ONLY_NO_M1_M2_DECISION",
        "workflow_wall_definition":"Cell wall time includes initial/stage flush barriers, CPU controller lookup/pin, pre-final fixed pressure, and HTTP requests; server startup is excluded. This is not ordinary pipeline latency.",
        "http_stage_group_definition":"Sum of concurrent session stage batch wall times; excludes explicit flush, pin/control, and pressure phases.",
        "http_request_sum_definition":"Sum of per-request elapsed service times; concurrent requests overlap, so this is not wall-clock time.",
        "final_stage_ttft_definition":"Mean final-stage request TTFT across sessions; LMCache final requests separately prove pinned retrieval or zero-hit evicted control."}

async def run_cells(args,client,evidence):
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(args.model,local_files_only=True)
    pool=word_pool(tokenizer,args.model)
    if len(pool)==0: raise RuntimeError("Common word token pool is empty")
    config=json.loads((pathlib.Path(args.model)/"config.json").read_text(encoding="utf-8"))
    evidence["word_pool_sha256"]=hashlib.sha256(np.asarray(pool,dtype=np.int64).tobytes()).hexdigest()
    evidence["workload_seed"]=WORKLOAD_SEED
    cells_dir=pathlib.Path(args.out).parent/"cells"; cells_dir.mkdir(exist_ok=True)
    arms=["S-radix","R-self"] if args.engine=="sglang" else ["P-pin-CPU","CPU-LRU"]
    scripts={path.name:sha256_file(path) for path in [pathlib.Path(__file__),
        pathlib.Path(__file__).with_name("external_probe.py"),pathlib.Path(sys.modules[session_trace.__module__].__file__)]}
    packages={dist.metadata["Name"]:dist.version for dist in importlib.metadata.distributions() if dist.metadata["Name"]}
    manifest={"schema":"r260401-external-rplus-v2","engine":args.engine,"arms":arms,"grids":GRIDS,
        "reps":args.reps,"warmup_rep":-1,"output_tokens":OUT_TOK,"workload_seed":WORKLOAD_SEED,
        "word_pool_sha256":evidence["word_pool_sha256"],"model_identity":evidence["model_identity"],
        "scripts_sha256":scripts,"environment":evidence["environment"],"all_package_versions":packages,
        "arguments":{key:value for key,value in vars(args).items() if key not in {"out","gate"}},
        "gate_semantics":evidence["gate_semantics"],"cpu_chunk_size":256,
        "pressure_factor":1.5,"pressure_churn_prompt_tokens":2048,
        "launch_protocol":{"dtype":"bfloat16","max_model_len":8192,"max_concurrency":16,
            "vllm_gpu_prefix_caching":False,"vllm_prompt_token_details":True,
            "sglang_attention_backend":"triton","sglang_cuda_graph":False,"sglang_skip_tokenizer":True}}
    manifest_sha=canonical_sha256(manifest)
    manifest_path=cells_dir.parent/"run_manifest.json"
    if manifest_path.exists():
        prior_manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
        if prior_manifest.get("sha256")!=manifest_sha or canonical_sha256(prior_manifest.get("manifest"))!=manifest_sha:
            raise RuntimeError("Resume rejected: model/config/token pool/code/packages/gate/arguments differ from existing run manifest; use a fresh output directory")
    else:
        if any(json.loads(path.read_text(encoding="utf-8")).get("status")=="MEASURED" for path in cells_dir.glob("*.json")):
            raise RuntimeError("Resume rejected: legacy measured cells have no bound run manifest; use a fresh output directory")
        atomic_json(manifest_path,{"sha256":manifest_sha,"manifest":manifest})
    evidence["run_manifest_sha256"]=manifest_sha; evidence["run_manifest_path"]=str(manifest_path)
    connector=aiohttp.TCPConnector(limit=64)
    async with aiohttp.ClientSession(connector=connector) as http:
        # Entire trace including model-generated appends must match within an engine pair.
        pair_fingerprints={}; results=[]
        for tau0,concurrency,depth in GRIDS:
            for rep in range(-1,args.reps):
                order=arms if rep%2==0 else list(reversed(arms))
                for arm in order:
                    name=f"{args.engine}_tau{tau0}_c{concurrency}_d{depth}_rep{rep}_{arm}.json"
                    path=cells_dir/name
                    if path.exists():
                        prior=json.loads(path.read_text(encoding="utf-8"))
                        if prior.get("status")=="MEASURED":
                            if prior.get("run_manifest_sha256")!=manifest_sha:
                                raise RuntimeError(f"Resume rejected: cell {name} does not match this run manifest")
                            result=prior
                        else: result=None
                    else: result=None
                    if result is None:
                        isolated=None; cell_client=client; cell_evidence={"environment":evidence["environment"]}
                        if args.engine=="lmcache":
                            cell_args=argparse.Namespace(**vars(args))
                            cell_args.out=str(cells_dir/path.stem/"launch_metadata.json")
                            isolated=LocalServer(cell_args,cell_evidence)
                            try: cell_client=await asyncio.to_thread(isolated.start)
                            except BaseException:
                                await asyncio.to_thread(isolated.stop)
                                raise
                        try:
                            result=await cell(http,cell_client,pool,tau0,concurrency,depth,rep,arm,args.cpu_cache_gb,config)
                            result["run_manifest_sha256"]=manifest_sha
                            result["warmup_excluded"]=rep<0
                            if isolated:
                                result["launches"]=cell_evidence["launches"]
                                result["fresh_worker_instance"]=True
                            atomic_json(path,result)
                        finally:
                            if isolated: await asyncio.to_thread(isolated.stop)
                    key=(tau0,concurrency,depth,rep)
                    hashes=[r["input_sha256"] for session in result["sessions"] for r in session]
                    if key in pair_fingerprints and pair_fingerprints[key]!=hashes:
                        raise RuntimeError("Within-engine paired input trace mismatch; cells retained, no paired summary permitted")
                    pair_fingerprints[key]=hashes
                    results.append(result)
                    print(json.dumps({"cell":name,"status":result["status"],"warmup_excluded":rep<0}),flush=True)
    evidence["arms"]=arms; evidence["grid"]=GRIDS; evidence["repetitions"]=args.reps
    evidence["paired_inputs_verified"]=True
    evidence["statistics"]=statistical_summary(results,arms,args.reps)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--engine",required=True,choices=["lmcache","sglang"])
    parser.add_argument("--gpu",type=int,required=True)
    parser.add_argument("--model",default=os.path.expanduser("~/models/Qwen/Qwen3-8B"))
    parser.add_argument("--out",required=True)
    parser.add_argument("--gate",required=True,help="Passed external_probe JSON for the same engine/environment")
    parser.add_argument("--reps",type=int,default=10)
    parser.add_argument("--cpu-cache-gb",type=float,default=8)
    parser.add_argument("--gpu-memory-utilization",type=float,default=.80)
    parser.add_argument("--startup-timeout",type=int,default=600)
    parser.add_argument("--instance-id",default="r260401_pin")
    args=parser.parse_args(); server=None
    signal.signal(signal.SIGTERM,handle_termination)
    os.environ["CUDA_VISIBLE_DEVICES"]=str(args.gpu)
    evidence={"engine":args.engine,"status":"UNMEASURED","within_engine_only":True,
        "utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "note":"These external results must not be reported as native GPU explicit pin. LMCache measures LocalCPUBackend pin versus CPU-LRU after fixed pressure; SGLang measures radix versus stage-barrier forced replay."}
    try:
        if args.reps<1: raise RuntimeError("At least one measured repetition is required")
        gate=json.loads(pathlib.Path(args.gate).read_text(encoding="utf-8"))
        if gate.get("status")!="COMPATIBILITY_GATE_PASSED_NOT_MEASURED" or gate.get("engine")!=args.engine:
            raise RuntimeError("Compatibility/semantics gate absent or did not pass")
        evidence["gate_path"]=args.gate; evidence["environment"]=inspected_environment(args.engine)
        if gate["environment"]!=evidence["environment"]:
            raise RuntimeError("Installed runtime/connector changed after gate; rerun compatibility gate")
        evidence["model_identity"]=model_identity(args.model)
        if gate.get("model_identity")!=evidence["model_identity"] or gate.get("probe_code_sha256")!=sha256_file(pathlib.Path(__file__).with_name("external_probe.py")):
            raise RuntimeError("Model identity or probe code changed after gate; rerun compatibility gate")
        if gate.get("probe_arguments",{}).get("gpu")!=args.gpu:
            raise RuntimeError("Physical GPU differs from compatibility gate; rerun gate for this GPU")
        expected="RADIX_REUSE_VERIFIED" if args.engine=="sglang" else "CPU_PIN_RETENTION_AND_RETRIEVAL_VERIFIED"
        if gate.get("gate",{}).get("status")!=expected:
            raise RuntimeError("Required eviction/retrieval semantics gate did not pass")
        evidence["gate_file_sha256"]=sha256_file(args.gate)
        evidence["gate_semantics"]={key:gate[key] for key in ["engine","status","environment","gpu",
            "model_identity","probe_code_sha256","probe_arguments","gate"]}
        client=None
        if args.engine=="sglang":
            server=LocalServer(args,evidence); client=server.start()
        asyncio.run(run_cells(args,client,evidence))
        evidence["status"]="MEASURED"; atomic_json(args.out,evidence); return 0
    except Exception as exc:
        evidence["error"]=repr(exc); atomic_json(args.out,evidence)
        print(json.dumps({"status":"UNMEASURED","error":repr(exc)}),flush=True); return 3
    finally:
        if server: server.stop()

if __name__=="__main__": raise SystemExit(main())
