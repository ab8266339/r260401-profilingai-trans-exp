#!/usr/bin/env python3
"""R260401 external-engine compatibility gate and HTTP adapters.

LMCache P-pin here is explicit LocalCPUBackend retention, never GPU APC pinning.
An installed/running server is insufficient: pin must complete, an unpinned control
must be evicted under actual pressure, and the pinned prefix must survive.
SGLang is S-radix, with default RadixAttention and no claim of explicit pinning.
No model weights are fetched. All launches are local processes in a new task env.
"""
from __future__ import annotations
BRJD_TASK_ROOT = "/home/hxu/exp/r260401_brjd_20261003"
BRJD_MACHINE_LABEL = "DESKTOP-BRJD313"
import argparse, datetime, hashlib, importlib, importlib.metadata, inspect
import json, os, pathlib, random, signal, socket, subprocess, sys, time, traceback
import urllib.error, urllib.request

SOURCES = {
    "pin_api": "https://docs.lmcache.ai/kv_cache_management/pin.html",
    "lmcache_abi": "https://github.com/LMCache/LMCache/blob/v0.5.5/pyproject.toml",
    "compatibility": "https://docs.lmcache.ai/getting_started/compatibility.html",
    "sglang_abi": "https://github.com/sgl-project/sglang/blob/v0.5.20/python/pyproject.toml",
    "sglang_attention": "https://github.com/sgl-project/sglang/blob/main/docs/docs/advanced_features/attention_backend.mdx",
    "vllm_external_hit_usage": "https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/v1/metrics/stats.py",
    "sglang_flush_response": "https://github.com/sgl-project/sglang/blob/v0.5.20/python/sglang/srt/entrypoints/http_server.py",
}

def atomic_json(path, data):
    p = pathlib.Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)

def sha256_file(path):
    with pathlib.Path(path).open("rb") as handle:
        digest=hashlib.file_digest(handle,"sha256")
    return digest.hexdigest()

def model_identity(model):
    """Fingerprint cached config/tokenizer plus weight identity without rereading 16GB."""
    root=pathlib.Path(model).resolve()
    if not (root/"config.json").is_file(): raise RuntimeError("Cached local model config absent; downloads are disabled")
    small_files={}
    for path in sorted(root.iterdir()):
        if path.is_file() and (path.suffix==".json" or path.name in {"tokenizer.model","merges.txt","vocab.txt"}):
            small_files[path.name]=sha256_file(path)
    weights={}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in {".safetensors",".bin",".pt"}:
            stat=path.stat()
            weights[str(path.relative_to(root))]={"bytes":stat.st_size,"mtime_ns":stat.st_mtime_ns}
    if not weights: raise RuntimeError("Cached local model weights absent; downloads are disabled")
    return {"path":str(root),"metadata_sha256":small_files,"weights_file_identity":weights,
        "weights_identity_method":"resolved-path + file names/sizes/mtime_ns; config/tokenizer/index SHA256"}

def json_request(base, route, body=None, timeout=60):
    request = urllib.request.Request(base.rstrip("/") + route,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Content-Type":"application/json"}, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw=response.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{route}: HTTP {exc.code}: {exc.read().decode('utf-8',errors='replace')[:2000]}") from exc

class ExternalClient:
    """Small adapter reusable by the common RD-R+ driver; input is exact token IDs."""
    def __init__(self, engine, base, model, controller=None, instance_id="r260401_pin",
                 pin_sync_ack=False,check_finish_unimplemented=False):
        self.engine, self.base, self.model = engine, base, model
        self.controller, self.instance_id = controller, instance_id
        self.pin_location = "LocalCPUBackend" if engine == "lmcache" else None
        self.pin_sync_ack=pin_sync_ack
        self.check_finish_unimplemented=check_finish_unimplemented

    def generate(self, ids, output_tokens=1, timeout=180, stream=False):
        if self.engine == "sglang":
            route="/generate"
            body={"input_ids":list(ids), "sampling_params":{"temperature":0,
                  "max_new_tokens":output_tokens,"min_new_tokens":output_tokens,"ignore_eos":True},
                  "stream":stream}
        else:
            route="/v1/completions"
            body={"model":self.model,"prompt":list(ids),"temperature":0,"max_tokens":output_tokens,
                  "min_tokens":output_tokens,"ignore_eos":True,"stream":stream,"return_token_ids":True}
            if stream: body["stream_options"]={"include_usage":True}
        begin=time.perf_counter()
        if not stream:
            result=json_request(self.base,route,body,timeout)
            return {"elapsed_s":time.perf_counter()-begin,"ttft_s":None,"raw":result,
                    "cached_tokens":self.cached_tokens(result)}
        request=urllib.request.Request(self.base+route,data=json.dumps(body).encode(),
                                      headers={"Content-Type":"application/json"})
        first=None; events=[]; output_ids=[]; meta={}
        with urllib.request.urlopen(request,timeout=timeout) as response:
            for line in response:
                line=line.decode("utf-8").strip()
                if not line.startswith("data:"): continue
                data=line[5:].strip()
                if data=="[DONE]": break
                event=json.loads(data); events.append(event)
                if self.engine=="sglang":
                    token=event.get("output_ids") or []
                    if token: output_ids=list(token)
                    meta=event.get("meta_info",meta)
                else:
                    choice=next(iter(event.get("choices",[]) or [{}]),{})
                    token=choice.get("token_ids") or choice.get("text","")
                if first is None and token: first=time.perf_counter()-begin
        hit_counts=[self.cached_tokens(e) for e in events]
        hit_counts=[value for value in hit_counts if value is not None]
        if self.engine=="sglang" and (len(output_ids)!=output_tokens or meta.get("completion_tokens")!=output_tokens or meta.get("prompt_tokens")!=len(ids) or first is None):
            raise RuntimeError(f"Native SGLang exact-ID gate failed: output_ids={len(output_ids)}, expected={output_tokens}, meta={meta}, first={first}")
        return {"elapsed_s":time.perf_counter()-begin,"ttft_s":first,"raw":events,
                "cached_tokens":max(hit_counts) if hit_counts else None,
                "native_output_ids_count":len(output_ids) if self.engine=="sglang" else None,
                "exact_native_output_ids_verified":True if self.engine=="sglang" else None}

    @staticmethod
    def cached_tokens(result):
        if isinstance(result,list):
            counts=[ExternalClient.cached_tokens(x) for x in result]
            counts=[value for value in counts if value is not None]
            return max(counts) if counts else None
        if "meta_info" in result: return result["meta_info"].get("cached_tokens")
        details=result.get("usage",{}).get("prompt_tokens_details",{}) or {}
        return details.get("cached_tokens")

    def flush_gpu(self):
        if self.engine=="sglang":
            # SGLang's successful /flush_cache response is plain text, not JSON.
            request=urllib.request.Request(self.base+"/flush_cache",method="GET")
            try:
                with urllib.request.urlopen(request,timeout=60) as response:
                    text=response.read().decode("utf-8",errors="replace")
                    status=response.status
                if status!=200 or not text.startswith("Cache flushed."):
                    raise RuntimeError(f"Unrecognized SGLang flush acknowledgement: HTTP {status}: {text[:2000]}")
                return {"http_status":status,"body":text}
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f"/flush_cache: HTTP {exc.code}: {exc.read().decode('utf-8',errors='replace')[:2000]}") from exc
        return json_request(self.base,"/reset_prefix_cache?reset_running_requests=false&reset_external=false",{},60)

    def lookup(self,ids):
        if self.engine!="lmcache": raise RuntimeError("S-radix has no LMCache controller lookup")
        raw=json_request(self.controller,"/lookup",{"tokens":list(ids)},30)
        layout=raw.get("layout_info",raw)
        match=layout.get(self.instance_id,[]) if isinstance(layout,dict) else []
        hit=int(match[1]) if isinstance(match,(list,tuple)) and len(match)>=2 and match[0]==self.pin_location else 0
        return {"tokens":hit,"raw":raw}

    def pin(self,ids):
        if self.engine!="lmcache": raise RuntimeError("S-radix is an eviction cache, not an explicit pin API")
        request={"instance_id":self.instance_id,"location":self.pin_location,"tokens":list(ids)}
        raw=json_request(self.controller,"/pin",request,60)
        if int(raw.get("num_tokens",0)) <= 0: raise RuntimeError(f"Pin scheduled no tokens: {raw}")
        event=raw.get("event_id")
        if not event: raise RuntimeError(f"Pin returned no event id: {raw}")
        if self.pin_sync_ack and self.check_finish_unimplemented:
            # Actual installed executor pin awaits each worker return; its optional
            # check_finish endpoint is only an unimplemented shell in these releases.
            return {"request":request,"pin_response":raw,"completion_mode":"synchronous_worker_ack",
                    "check_finish":"unimplemented_in_installed_source","polls":[]}
        deadline=time.monotonic()+60; statuses=[]
        while time.monotonic()<deadline:
            finish=json_request(self.controller,"/check_finish",{"event_id":event},30)
            statuses.append(finish)
            status=str(finish.get("status","")).lower()
            if status in {"done","finished","complete","completed","success","succeeded"}:
                return {"request":request,"pin_response":raw,"finish":finish,"polls":statuses}
            if any(w in status for w in ("fail","error","not_found","unknown")):
                raise RuntimeError(f"Pin event did not complete: {finish}")
            time.sleep(0.2)
        raise RuntimeError(f"Pin completion timeout: {statuses[-5:]}")

def inspected_environment(engine):
    packages=["torch","vllm","lmcache"] if engine=="lmcache" else ["torch","sglang","sglang-kernel","transformers"]
    result={"python":sys.version,"executable":sys.executable,"packages":{},
        "machine":socket.gethostname(),"machine_label":BRJD_MACHINE_LABEL,"task_root":BRJD_TASK_ROOT}
    for package in packages:
        try: result["packages"][package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError: result["packages"][package]=None
    import torch
    result["torch_cuda"]=torch.version.cuda; result["torch_arch_list"]=torch.cuda.get_arch_list()
    result["cxx11_abi"]=torch._C._GLIBCXX_USE_CXX11_ABI
    if engine=="lmcache":
        importlib.import_module("lmcache.cuda_ops")
        module=importlib.import_module("lmcache.integration.vllm.lmcache_connector_v1")
        result["connector_classes"]=[name for name in dir(module) if name.startswith("LMCacheConnector")]
        if not result["connector_classes"]: raise RuntimeError("LMCache in-process connector class absent")
        result["connector_name"]="LMCacheConnectorV1" if "LMCacheConnectorV1" in result["connector_classes"] else "LMCacheConnectorV1Dynamic" if "LMCacheConnectorV1Dynamic" in result["connector_classes"] else "LMCacheConnectorV1Dy" if "LMCacheConnectorV1Dy" in result["connector_classes"] else None
        if result["connector_name"] is None: raise RuntimeError(f"Unknown connector class: {result['connector_classes']}")
        importlib.import_module("lmcache.v1.api_server.__main__")
        cpu=importlib.import_module("lmcache.v1.storage_backend.local_cpu_backend")
        pin_src=inspect.getsource(cpu.LocalCPUBackend.pin)
        result["cpu_pin_source_sha256"]=hashlib.sha256(pin_src.encode()).hexdigest()
        result["cpu_pin_calls_memory_obj_pin"]=".pin(" in pin_src
        if not result["cpu_pin_calls_memory_obj_pin"]: raise RuntimeError("LocalCPUBackend.pin no longer calls MemoryObj.pin; review API before measuring")
        executor=importlib.import_module("lmcache.v1.cache_controller.executor")
        executor_pin_src=inspect.getsource(executor.LMCacheClusterExecutor.pin)
        finish_src=inspect.getsource(executor.LMCacheClusterExecutor.check_finish)
        result["controller_pin_source_sha256"]=hashlib.sha256(executor_pin_src.encode()).hexdigest()
        result["controller_check_finish_source_sha256"]=hashlib.sha256(finish_src.encode()).hexdigest()
        result["controller_pin_waits_worker_ack"]="await self.execute_workers(" in executor_pin_src
        result["controller_check_finish_unimplemented"]="raise NotImplementedError" in finish_src
    else:
        importlib.import_module("sglang.srt.server_args")
    return result

def choose_free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0)); return sock.getsockname()[1]

class LocalServer:
    def __init__(self,args,evidence):
        self.args=args; self.evidence=evidence; self.procs=[]; self.handles=[]
        self.dir=pathlib.Path(args.out).parent; self.dir.mkdir(parents=True,exist_ok=True)
        self.port=choose_free_port(); self.base=f"http://127.0.0.1:{self.port}"
        self.env=os.environ.copy(); self.env.update({"CUDA_VISIBLE_DEVICES":str(args.gpu),
            "HF_HUB_OFFLINE":"1","TRANSFORMERS_OFFLINE":"1","PYTHONHASHSEED":"123"})
        self.controller=None

    def launch(self,command,name):
        log=self.dir/f"{self.args.engine}_{name}.log"
        handle=log.open("w",encoding="utf-8"); self.handles.append(handle)
        proc=subprocess.Popen(command,stdout=handle,stderr=subprocess.STDOUT,env=self.env,start_new_session=True)
        self.procs.append(proc); self.evidence.setdefault("launches",[]).append({"command":command,"pid":proc.pid,"log":str(log)})
        return proc

    def wait_ready(self,base,path,proc,limit):
        end=time.monotonic()+limit; last=None
        while time.monotonic()<end:
            if proc.poll() is not None: raise RuntimeError(f"Server exited ({proc.returncode}); see launch log")
            try: return json_request(base,path,None,4)
            except Exception as exc: last=str(exc)
            time.sleep(1)
        raise RuntimeError(f"Server startup timed out at {limit}s: {last}")

    def start(self):
        a=self.args
        if a.engine=="sglang":
            command=[sys.executable,"-m","sglang.launch_server","--model-path",a.model,
                "--host","127.0.0.1","--port",str(self.port),"--dtype","bfloat16",
                "--attention-backend","triton","--disable-cuda-graph","--skip-tokenizer-init","--context-length","8192",
                "--mem-fraction-static",str(a.gpu_memory_utilization),"--max-running-requests","16"]
        else:
            controller_port=choose_free_port(); pull=choose_free_port(); reply=choose_free_port()
            worker=choose_free_port(); peer=choose_free_port()
            self.controller=f"http://127.0.0.1:{controller_port}"
            controller_cmd=[sys.executable,"-m","lmcache.v1.api_server","--host","127.0.0.1",
                "--port",str(controller_port),"--monitor-ports",json.dumps({"pull":pull,"reply":reply})]
            ctl=self.launch(controller_cmd,"controller")
            self.wait_ready(self.controller,"/openapi.json",ctl,60)
            config={"chunk_size":256,"local_cpu":True,"max_local_cpu_size":a.cpu_cache_gb,
                "enable_controller":True,"lmcache_instance_id":a.instance_id,
                "controller_pull_url":f"127.0.0.1:{pull}","controller_reply_url":f"127.0.0.1:{reply}",
                "lmcache_worker_ports":[worker],"p2p_host":"127.0.0.1","p2p_init_ports":[peer]}
            import yaml
            config_path=self.dir/"lmcache_cpu_pin.yaml"
            config_path.write_text(yaml.safe_dump(config),encoding="utf-8")
            self.env["LMCACHE_CONFIG_FILE"]=str(config_path)
            self.evidence["lmcache_configuration"]=config
            connector={"kv_connector":self.evidence["environment"]["connector_name"],
                "kv_connector_module_path":"lmcache.integration.vllm.lmcache_connector_v1","kv_role":"kv_both"}
            command=[sys.executable,"-m","vllm.entrypoints.cli.main","serve",a.model,
                "--served-model-name","r260401_external","--host","127.0.0.1","--port",str(self.port),
                "--dtype","bfloat16","--enforce-eager","--max-model-len","8192",
                "--gpu-memory-utilization",str(a.gpu_memory_utilization),"--max-num-seqs","16",
                "--no-enable-prefix-caching","--enable-prompt-tokens-details",
                "--kv-transfer-config",json.dumps(connector)]
        proc=self.launch(command,"server")
        self.wait_ready(self.base,"/v1/models",proc,a.startup_timeout)
        inspected=self.evidence["environment"]
        return ExternalClient(a.engine,self.base,"r260401_external",self.controller,a.instance_id,
            inspected.get("controller_pin_waits_worker_ack",False),
            inspected.get("controller_check_finish_unimplemented",False))

    def stop(self):
        for proc in reversed(self.procs):
            if proc.poll() is None:
                try: os.killpg(proc.pid,signal.SIGTERM)
                except ProcessLookupError: pass
        for proc in reversed(self.procs):
            try: proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                try: os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                proc.wait(timeout=10)
        for handle in self.handles: handle.close()

def prefix_trace(seed,length):
    rng=random.Random(seed)
    return [rng.randrange(1000,20000) for _ in range(length)]

def handle_termination(signum,frame):
    # timeout/queue termination must execute finally and stop only our own launch groups.
    raise SystemExit(128+signum)

def perform_gate(client,args,evidence):
    prefix=prefix_trace(20261001,1024); control=prefix_trace(20261002,1024)
    if args.engine=="sglang": evidence["initial_flush"]=client.flush_gpu()
    evidence["smoke_prefix_sha256"]=hashlib.sha256(json.dumps(prefix).encode()).hexdigest()
    smoke_output=128 if args.engine=="sglang" else 1
    evidence["cold_request"]=client.generate(prefix,smoke_output,stream=True)
    evidence["warm_request"]=client.generate(prefix,smoke_output,stream=True)
    if args.engine=="sglang":
        cached=evidence["warm_request"]["cached_tokens"]
        if not cached or cached<512: raise RuntimeError(f"Radix reuse not evidenced; warm cached_tokens={cached}")
        evidence["gate"]={"status":"RADIX_REUSE_VERIFIED","explicit_pin":False,"cached_tokens":cached,
            "exact_native_output_ids_verified":True,"output_tokens":128}
        return
    # Newer controller releases advertise pending events; allow registry admit batching to settle.
    deadline=time.monotonic()+30; before=None
    while time.monotonic()<deadline:
        before=client.lookup(prefix)
        if before["tokens"]>=1024: break
        time.sleep(.5)
    evidence["before_pin_lookup"]=before
    if not before or before["tokens"]<1024: raise RuntimeError("Warm prefix absent from LocalCPUBackend controller registry")
    evidence["pin_operation"]=client.pin(prefix)
    if int(evidence["pin_operation"]["pin_response"].get("num_tokens",0))<1024:
        raise RuntimeError("Pin did not cover all smoke prefix tokens")
    evidence["control_request"]=client.generate(control,1)
    time.sleep(1)
    evidence["control_before_pressure"]=client.lookup(control)
    if evidence["control_before_pressure"]["tokens"]<1024: raise RuntimeError("Unpinned control was not admitted")
    config=json.loads((pathlib.Path(args.model)/"config.json").read_text(encoding="utf-8"))
    heads=config.get("num_key_value_heads",config["num_attention_heads"])
    dim=config.get("head_dim",config["hidden_size"]//config["num_attention_heads"])
    bytes_per_token=2*config["num_hidden_layers"]*heads*dim*2
    nominal_tokens=int(args.cpu_cache_gb*(1024**3)/bytes_per_token)
    churn_length=2048
    churn_requests=max(8,(3*nominal_tokens+churn_length-1)//churn_length)
    evidence["pressure_plan"]={"cpu_budget_gb":args.cpu_cache_gb,"nominal_bf16_kv_bytes_per_token":bytes_per_token,
        "nominal_capacity_tokens":nominal_tokens,"churn_requests":churn_requests,"churn_prompt_tokens":churn_length}
    for index in range(churn_requests):
        client.generate(prefix_trace(310000+index,churn_length),1)
    time.sleep(2)
    evidence["control_after_pressure"]=client.lookup(control)
    evidence["pinned_after_pressure"]=client.lookup(prefix)
    if evidence["control_after_pressure"]["tokens"]>=1024:
        raise RuntimeError("Pin survival is inconclusive: unpinned control did not evict under pressure")
    if evidence["pinned_after_pressure"]["tokens"]<1024:
        raise RuntimeError("Explicitly pinned LocalCPUBackend prefix did not survive observed eviction")
    # One new prompt token leaves all 1024 pinned tokens available as external
    # hits without the full-hit last-token recomputation/truncation corner case.
    evidence["after_pressure_request"]=client.generate(prefix+[20001],1,stream=True)
    cached=evidence["after_pressure_request"]["cached_tokens"]
    if cached is None or cached<1024:
        raise RuntimeError(f"Pinned CPU retention did not yield actual external KV retrieval: cached_tokens={cached}")
    evidence["gate"]={"status":"CPU_PIN_RETENTION_AND_RETRIEVAL_VERIFIED","explicit_pin":True,"pin_location":"LocalCPUBackend",
        "gpu_pin":False,"unpinned_control_evicted":True,"pinned_cpu_prefix_survived":True,
        "actual_external_cached_tokens":cached,"minimum_required_external_cached_tokens":1024,
        "gpu_prefix_caching_enabled":False}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--engine",choices=["lmcache","sglang"],required=True)
    parser.add_argument("--inspect-only",action="store_true")
    parser.add_argument("--gpu",type=int,default=0)
    parser.add_argument("--model",default="/mnt/e/CodeBase-708a/061-working-paper/R260306-fractal-moe/.runtime-qwen-scale/8B")
    parser.add_argument("--out",required=True)
    parser.add_argument("--instance-id",default="r260401_pin")
    parser.add_argument("--cpu-cache-gb",type=float,default=1.0)
    parser.add_argument("--gpu-memory-utilization",type=float,default=.80)
    parser.add_argument("--startup-timeout",type=int,default=600)
    args=parser.parse_args(); server=None
    signal.signal(signal.SIGTERM,handle_termination)
    if not args.inspect_only: os.environ["CUDA_VISIBLE_DEVICES"]=str(args.gpu)
    evidence={"engine":args.engine,"status":"UNMEASURED","kind":"compatibility_and_semantics_gate",
        "machine":socket.gethostname(),"machine_label":BRJD_MACHINE_LABEL,"task_root":BRJD_TASK_ROOT,
        "sources":SOURCES,"utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "note":"Gate timings are diagnostic only; statistical RD-R+ results require common workload and repetitions."}
    try:
        evidence["environment"]=inspected_environment(args.engine)
        if args.inspect_only:
            evidence["status"]="IMPORT_CHECKED_NOT_MEASURED"; atomic_json(args.out,evidence); return 0
        evidence["model_identity"]=model_identity(args.model)
        evidence["probe_code_sha256"]=sha256_file(__file__)
        evidence["probe_arguments"]={key:value for key,value in vars(args).items() if key not in {"out","inspect_only"}}
        import torch
        torch.cuda.set_device(0)
        evidence["gpu"]={"physical_index":args.gpu,"visible_index":0,"name":torch.cuda.get_device_name(),"capability":torch.cuda.get_device_capability()}
        torch.ones((16,),device="cuda").sum().item(); torch.cuda.synchronize()
        server=LocalServer(args,evidence)
        client=server.start(); perform_gate(client,args,evidence)
        evidence["status"]="COMPATIBILITY_GATE_PASSED_NOT_MEASURED"
        atomic_json(args.out,evidence); print(json.dumps({"status":evidence["status"],"gate":evidence["gate"]})); return 0
    except Exception as exc:
        evidence["error"]=repr(exc); evidence["traceback"]=traceback.format_exc()
        atomic_json(args.out,evidence); print(json.dumps({"status":"UNMEASURED","error":repr(exc)})); return 3
    finally:
        if server: server.stop()

if __name__=="__main__": raise SystemExit(main())
