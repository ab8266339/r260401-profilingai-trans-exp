# RD-M2 执行协议：calibration → pilot → 冻结 → main（2026-10-03）

本轮脚本是可执行真实 vLLM runner。其主机必须为四卡机；本文方法定义、正文与历史台账不在本脚本里修改。pilot 是追加工作负载，不套用原 v2 的 M1/M2 通过条件。

## 轨迹、后端与三个臂

复用同目录已校验的 kvbench.py，只有旧脚本 word_pool 缓存目录按新实验树设置。调用 launch_server / wait_ready / stop_server、word_pool / session_trace、stage_request / reset_prefix_cache / Poller / parse_server_log。

模型 Qwen3-8B，bf16/auto KV，单卡；GPU-memory-utilization=0.90；TP=1；max-model-len=12288；默认图编译策略、prefix caching开、不做CPU offload。校准先分别启动A0和A-uncap，记录各自真实自然KV预算与scheduler默认。然后在看到任何pilot端点之前，把两种自然预算中较小的整块容量冻结为共同可行池，所有臂使用同一个num-gpu-blocks-override，并验证实际allocator token budget严格相等。这是明确标注的matched-capacity附加实验，不能冒充原始自然池；共同容量由机器可行性决定，不为制造N≈8选择。自然池测量保留在capacity_before_pilot.json，正式结果必须写明配置改变。

- A0：旧 launch_server 默认服务配置，不覆盖 max_num_seqs。
- A-uncap：只把 max_num_seqs 显式提高到2048；记录实际值，正式 main 需要证明它高于 A0 默认值。
- P-admit：服务端与 A-uncap 完全相同；在首级 HTTP 提交前，用单一事件循环原子保留整块对齐的完整会话峰值 token 预算，已保留之和不得超过 0.95×实际 allocator token budget。失败当场计 blocked miss。
- P-pin：本 runner **未测**。没有经过安装、API调用及保护效力探针验证的 physical pin 接口，不可冒充已实现；P-admit 使用仍可被驱逐的 APC，不提供 pin 保证。

严格保留 legacy 轨迹含义：tau0=3400 是初始 ctx；每一级（包括第一级）都追加50 token新指令，输出固定128 token、ignore_eos=true、temperature=0。所以首次送入3450 token，八级末次送入4696 token，最终上下文4824 token。每会话独立盐；同会话跨级复用盐；每臂相同随机流及会话ID语义。首级 cached_tokens 必须为0，后续级缓存命中以 cached_tokens/上一级上下文token为分母。

记录补充（仅增加证据，不改变生成策略、期限、样本量）：逐会话保存初始ctx及八级instruction轨迹SHA，逐级保存实际prompt/output token IDs的SHA（canonical JSON integer array）。temperature=0并不保证不同并发、scheduler配置的实际生成tokens相同。pilot/main额外输出token_trajectory_audit.json，按point、rep、每个arm pair比较共同cohort且已admit的session IDs及共同已观察级的哈希；每sid保留双方完整已观察轨迹摘要hash、缺级情况和漂移级。共同样本的同轨迹核验不删除blocked/unfinished，原端点分母和同时区间完全不变。开环只保证预生成到达/保持目标及初始/instruction随机流相同；闭环同sid不保证到达时刻相同。发现漂移就标DRIFT_DETECTED，不宣称identical realized token workload或把政策效果解释为严格相同prompt的纯时延效果；未完成轨迹标partial/unverified，不用缺失证据判相同。


## calibration

先验证token ID返回、固定128输出、同盐命中、异盐0命中、各臂自然KV容量与共同池实际一致性。有效max_num_seqs优先来自server_info/日志；缺失时用同一已安装vLLM的EngineArgs.create_engine_config(OPENAI_API_SERVER)配置构建核实，并记录源码hash。两种办法都失败就在pilot之前停，不能跑数小时后才发现scheduler不可验证。30秒非计量预热，然后默认3次单流完整八级流水线，重复间清空缓存，保留逐级原始测量与代码/模型哈希。

截止期限**不由新结果调整**：读取 rd_r0_baseline.json 的 single_flow_A0_d8_median_s=11.40360071050236秒（同四卡机既有RD-R0，源40行哈希随文件保存），使用既定1.5/2/3倍，即17.1054011/22.8072014/34.2108021秒。统一drain=ceil(2×最大截止)=69秒。新calibration的单流时间只记录baseline drift，不偷偷重设期限。

calibration输出 calibration.json，包括实测容量、API探针、当前服务配置、baseline drift、逐级计量及供后续开环到达负载使用的 token×秒近似。

## pilot：真实54格，不是stub

默认3次独立重复，每次轮换三个臂顺序；每臂启动一次服务后跑所有负载点，重复之间不混表。每格30秒预热、120秒观测、69秒共同drain；清缓存和证明running/waiting=0是计量前条件。HTTP错误/盐隔离失败使该格无效并保留数据，整个阶段失败，不能自动重跑到显著。

闭环只用于描述：实际容量对应峰值可装会话数 N_peak=floor(0.95*C_tokens/4824)；pilot取 round({0.7,1,1.3}×N_peak)，去重并限在1..128。各slot每次完成或拒绝后思考1秒。这是相对原v2“无思考时间”的**显式实验变更**，不是原M2判据的实现。闭环绝对到达随策略改变；blocked仍是生成队列miss；不得用阻塞率推断固定外生需求，λh=N只作恒等描述。

主方向是独立开环：在任何计量臂开跑之前，用独立随机流预生成完整0..150秒Poisson到达时刻与应用保持目标，并存文件/hash。到达率取校准占用近似阈值的0.7/1/1.3倍。保持目标从既有deadline基准的{1,3,6}倍独立抽样；它不从在册槽位数倒推。实际完成晚于目标时保持到完成；记录延长/右删失，不能把目标h当实际h。保持期间已完成KV仍是evictable APC；应用租约与物理缓存驻留明确分开。开环所有臂使用完全相同预生成时刻；cohort归属和期限从计划生成时刻计算，dispatch lag另记，不能用客户端排队改写外生到达。

生成时刻在[30,150)的会话全部进入分母。blocked和drain未完成均为miss；端点为三个既定截止期限的attainment、完成会话/s、完成者时延、后续级命中率、抢占、逻辑预算最大值。原始逐会话、逐级、metrics时间线保存。性能失败与执行错误分开：合法unfinished是miss，HTTP/固定输出断言失败是无效格。

pilot数据仅用于方差、运行时长、校准漂移和冻结可行性。不能把这54格加进正式均值、p值或Holm族，也不能写成第四坐标已被独立验证。阶段总结提供逐重复配对差、sample SD和实测wall time，不自行捏造功效保证。

## 自动pipeline、冻结与main

main已有真实执行路径，但在冻结文件不足时fail。需要 --freeze 与 --freeze-sha256，且文件 status=FROZEN，包含：

- calibration/pilot summary/m2.py/kvbench.py 的SHA-256，冻结UTC时间晚于pilot结束；
- 明确的每个N/开环率/保持策略点和连续index；正式rep_seeds必须为不与pilot(0..99)重叠的整数>=10000；
- 完整workload、期限、69秒共同drain，以及matched-capacity配置与自然池记录；
- 主要端点、对比、预声明同时区间族成员、所需精度、pilot SD来源、精度预测、required_reps与feasible；
- 实测工时估计；open_loop_primary=true、closed_loop_independent_prediction=false。

pipeline在calibration后、pilot任何端点出现前保存sampling_plan_before_pilot.json，固定同一六点范围、样本量规则和精度目标。pilot结束后仅用开环P-admit−A-uncap重复间方差，在10..20中选Bonferroni paired-t预计同时区间半宽≤5个百分点的最小n；三档既定截止×三个开环率构成9成员主族。找不到时n=20且underpowered=true，不能声称功效保证。POWER_WORKTIME.json记录每个候选n的预计半宽及实测启动/每格工时。这是精度预测，不能称达到某个显著性检验功效。正式使用独立10000起的seeds，pilot不并入。main始终为interval_estimation_only，输出主族Bonferroni paired-t同时区间，并披露独立重复差近似正态假设；underpowered时仍可收集20个正式重复，但不作原M1/M2通过声明，也不自动改贡献或正文。不是所有科学门都因执行完成而通过。

## 工时

每格已知最小壁钟219秒：30+120+69，不含reset/metrics settle。若六个点、三臂、三rep未因去重减少，54格约3.285小时，另加九次**实测**服务启动和清理时间。实际KV容量决定pilot N，不跑128点全扫描。正式10rep同网格至少180格×219秒≈10.95小时，另加30次实测启动；若精扫34N等，则重新按实测成本计算。旧设计“最多30–45h/四卡8–12h”不再引用。尚未做R+1时，本脚本单卡串行，不把四卡时间除以4。

## 队列命令（主笔负责上传/排队）

先完成capacity/API预检及R+1；独占时运行：
    python scripts/m2.py --phase calibration --model-path ~/models/Qwen/Qwen3-8B --gpus 0 --out results/m2_calibration --prior-baseline rd_r0_baseline.json
    python scripts/m2.py --phase pilot --model-path ~/models/Qwen/Qwen3-8B --gpus 0 --calibration results/m2_calibration/calibration.json --out results/m2_pilot
实际python用现有~/venvs/vllm/bin/python，工作目录为新实验root，legacy默认同scripts目录。两命令之间检查退出码，失败不能继续。直接main需要pilot后的完整冻结文件与SHA。自动全路线可排：python scripts/m2.py --phase pipeline --model-path ~/models/Qwen/Qwen3-8B --gpus 0 --out results/m2_pipeline --prior-baseline rd_r0_baseline.json。pipeline真实执行calibration→pilot→自动冻结/精度及工时记录→独立正式区间估计；若API/配置/容量/数据门失败，保留pipeline_state.json并停止，不假称正式阶段完成。
