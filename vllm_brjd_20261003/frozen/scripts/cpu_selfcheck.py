#!/usr/bin/env python3
"""CPU-only lifecycle test: no nvidia-smi, no server, no package installation."""
from pathlib import Path
import asyncio,copy,datetime,importlib.util,json,sys,tempfile,types
ROOT=Path(__file__).resolve().parent
TEST_SOURCE=next(iter(sorted(ROOT.glob('m2_brjd_*.py'))),ROOT/'m2_brjd.py')
spec=importlib.util.spec_from_file_location('m2_under_test',TEST_SOURCE)
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
checks=[]
def checked(name): checks.append(name)
m.selftest(); checked('original reservation/arrival/hold/cohort selftest')
# Real baseline/calibration/freeze/pipeline code runs; only hardware/tokenizer/server I/O is stubbed.
m.WARMUP=0.0
m.hardware_identity=lambda:'FAKE-CPU-TEST-GPU, 5090 D, 32607 MiB, driver-test'
class HTTP:
    async def __aenter__(self): return self
    async def __aexit__(self,*args): return False
class Tokenizer:
    @staticmethod
    def from_pretrained(*args,**kwargs): return object()
sys.modules['transformers']=types.SimpleNamespace(AutoTokenizer=Tokenizer)
class Legacy:
    aiohttp=types.SimpleNamespace(ClientSession=lambda **kwargs:HTTP(),TCPConnector=lambda **kwargs:None)
    def now_iso(self): return datetime.datetime.now(datetime.timezone.utc).isoformat()
    def model_fingerprint(self,path): return {'config_sha256':'fake-model-hash','files':{'weights':123}}
    def word_pool(self,*args): return [1,2,3]
    def launch_server(self,args,log):
        self.launches+=1
        Path(log).write_text('fake server, no GPU launched\n',encoding='utf-8')
        return object(),['fake-server-never-executed']
    def stop_server(self,process): self.stops+=1
    async def wait_ready(self,*args): return 1.0
    async def api_probe(self,*args): return {'salt_isolates':True,'token_ids_returned':True}
    async def reset_prefix_cache(self,*args,**kwargs): return 1
legacy=Legacy(); legacy.launches=legacy.stops=0
async def cap(args,legacy,out,pool):
    result={'common_tokens':87680,'common_blocks':5480,'block_size':16,'matched_A-uncap_verification':{'verified':True}}
    m.write_json(out/'capacity_before_pilot.json',result)
    args.pool_blocks=result['common_blocks']; return result
async def desc(*args): return {'kv_cache_tokens':87680,'block_size':16,'effective_max_num_seqs':256}
async def idle(*args): return None
async def session(http,base,legacy,pool,tag,rep,sid,generated,t0,target_hold,records,reservation):
    latency={600:12.0,601:10.0,602:11.0}[rep]
    records.append({'status':'completed','latency_s':latency,'stages':[{'sent_prompt_tokens':3450+i*178,'context_tokens_after':3578+i*178,'e2e':latency/8} for i in range(8)]})
m.capacity_preflight=cap; m.server_description=desc; m.wait_idle=idle; m.session=session
m.package_version=lambda package:'test'
async def exercise():
    with tempfile.TemporaryDirectory(prefix='r260401-m2-cpu-') as tmp:
        root=Path(tmp); out=root/'pipeline'; cal_dir=out/'calibration'; cal_dir.mkdir(parents=True)
        legacy_file=root/'kvbench_fake.py'; legacy_file.write_text('# CPU test only\n',encoding='utf-8')
        args=types.SimpleNamespace(phase='pipeline',legacy=str(legacy_file),model_path='/fake/local/Qwen3-8B',gpus='0',port=8241,
            calibration_reps=3,pilot_reps=3,uncap=2048,ready_timeout=1800,freeze=None,freeze_sha256=None)
        await m.calibration(args,legacy,cal_dir)
        cal_file=cal_dir/'calibration.json'; cal=json.loads(cal_file.read_text(encoding='utf-8'))
        m.validate_local_calibration(cal,args,legacy)
        assert cal['baseline_A0_s']==11.0 and cal['deadline_seconds']==[16.5,22.0,33.0]
        assert cal['hold_target_seconds']==[11.0,33.0,66.0] and cal['drain_s']==66
        assert 'prior-baseline' not in TEST_SOURCE.read_text(encoding='utf-8')
        checked('real calibration freezes local median 11 seconds, deadlines 16.5/22/33, holds 11/33/66, drain66')
        assert legacy.launches==legacy.stops==1
        try: await m.calibration(args,legacy,cal_dir)
        except RuntimeError as e: assert 'already frozen' in str(e)
        else: raise AssertionError('Recalibration unexpectedly overwrote frozen baseline')
        checked('direct recalibration cannot overwrite prior freeze')
        altered=copy.deepcopy(cal); altered['baseline_A0_s']=11.40360071050236
        try: m.validate_local_calibration(altered,args,legacy)
        except RuntimeError: pass
        else: raise AssertionError('Old-host deadline was accepted')
        checked('four-GPU prior deadline rejected')
        original_hw=m.hardware_identity; m.hardware_identity=lambda:'different device'
        try: m.validate_local_calibration(cal,args,legacy)
        except RuntimeError: pass
        else: raise AssertionError('Different device accepted')
        m.hardware_identity=original_hw; checked('hardware identity reuse gate')
        calls=[]
        async def fake_experiment(exp_args,legacy,phase_out):
            phase_out.mkdir(parents=True,exist_ok=True); calls.append(exp_args.phase)
            current=json.loads(Path(exp_args.calibration).read_text(encoding='utf-8'))
            m.validate_local_calibration(current,exp_args,legacy)
            preplan=json.loads((out/'sampling_plan_before_pilot.json').read_text(encoding='utf-8'))
            rules=preplan['fixed_rules']
            assert rules['baseline_A0_s']==11 and rules['deadline_seconds']==[16.5,22,33]
            assert rules['hold_target_seconds']==[11,33,66] and rules['drain_s']==66
            assert rules['workload']['tau0']==3400 and rules['workload']['first_prompt_tokens']==3450
            assert 'token_generation_drift_observed' not in rules
            if exp_args.phase=='pilot':
                assert preplan['status']=='FROZEN_BEFORE_PILOT'
                points=exp_args.predetermined_points
                for p in points:
                    if p['kind']=='open':
                        assert p['hold_factors']==[1,3,6]
                        trace=m.pregen_open(p,0,11,__import__('numpy'))
                        assert all(a['target_hold_s'] in [11,33,66] for a in trace)
                variances=[{'point_index':p['index'],'contrast':'P-admit-A-uncap','deadline_factor':f,'sample_sd':.01}
                           for p in points if p['kind']=='open' for f in m.DEADLINE_FACTORS]
                m.write_json(phase_out/'pilot_summary.json',{'status':'COMPLETE','pilot_only':True,'points':points,
                    'finished_utc':legacy.now_iso(),'paired_variances':variances,'median_observed_startup_s':2,
                    'median_observed_cell_wall_s':216})
            else:
                frozen=json.loads(Path(exp_args.freeze).read_text(encoding='utf-8'))
                m.validate_freeze(frozen,exp_args,current)
                assert frozen['workload']['deadline_seconds']==[16.5,22,33]
                assert frozen['rep_seeds']==list(range(10000,10010))
                assert len(frozen['power_plan']['family_members'])==9
                assert frozen['inference_mode']=='interval_estimation_only'
                m.write_json(phase_out/'main_summary.json',{'status':'COMPLETE','test_stub_no_measurements':True})
        m.experiment=fake_experiment
        await m.pipeline(args,legacy,out)
        assert calls==['pilot','main'] and json.loads((out/'pipeline_state.json').read_text(encoding='utf-8'))['status']=='COMPLETE'
        freeze_hash=m.sha(out/'formal_freeze.json'); baseline_hash=m.sha(cal['deadline_source']); cal_hash=m.sha(cal_file)
        checked('real pipeline writes pre-pilot plan without NameError, selects10 independent repeats and9-member family')
        await m.pipeline(args,legacy,out)
        assert calls==['pilot','main','main']
        assert m.sha(out/'formal_freeze.json')==freeze_hash and m.sha(cal['deadline_source'])==baseline_hash and m.sha(cal_file)==cal_hash
        checked('pipeline resume preserves calibration/baseline/formal freezes byte-for-byte')
        assert legacy.launches==legacy.stops==1
asyncio.run(exercise())
report={'status':'PASS','cpu_only':True,'gpu_launched':False,'source_sha256':m.sha(TEST_SOURCE),'checks':checks}
(ROOT/'CPU_SELFCHECK.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))