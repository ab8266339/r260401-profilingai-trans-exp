# R260401 实验队列与 BRJD 接续（2026-10-03）

> 最新状态（2026-10-04 01:18，DESKTOP-BRJD313）：R260401优先策略生效，R260306保持stopped_by_request，未恢复或修改。预检及R4容量校准已完成并全SHA归档，共同容量43328 tokens / 2708 blocks。v9因Windows心跳共享锁退出后，两格完整测量已审核保留；v10修复并于01:13:31通过123文件SHA门实际接续R4，重新完成未计时暖机，已有两格跳过，只补未完成部分。控制器PID60624（01:06:34.9299305创建），当前守卫正常。后续external→M2；四卡机R1/R2/R3不重复执行。



## 2026-10-04 v10 心跳修复与部分结果保留

00:39:46，v9在原子替换Windows心跳文件时遇到WinError5共享锁，控制器退出，Linux守卫随后清理本任务进程。没有外部实验进入；旧失败status、errlog、history、host_guard与R4原始23文件快照完整保留。两格t3400×4×8、r00、default模式R/A0均在最后正常守卫心跳之前完成，API、128输出、容量与监视门通过，原SHA保留；配对token路径有漂移，不能声称相同token的纯cache因果时延。

v10仅修控制：WinError5/32/33的原子替换按50ms间隔重试、最多2秒；未知或持续错误仍失败关闭，20秒heartbeat规则不变。一次性RECOVERY_REVIEW_v10.json绑定原失败、3条history、23快照、20个原文件与3个暖机标记隔离记录。旧引擎的3个done标记已逐SHA移动至control/warmup_quarantine_v9；原科学脚本按同参数重新执行未计时暖机，严格复用capacity_freeze并跳过两格complete cells，不重复计时。新审核后的读取错误保持needs_review，不能降为failed绕过审核。24项CPU故障/恢复检查与独立审计PASS，科学源码、10rep与80%显存预算未改。

当前ACTIVE_CONTROL.json指向controller_v10.py / queue_v10.json / FILE_MANIFEST_v10.json，manifest SHA 55f330523968388e427e042a759cf07055373db3aac25cb92722d9802e05036f；123项冻结payload在E/WSL/NAS逐文件一致。控制器PID60624，出生时间1791047194.9299304，2026-10-04T01:06:34.9299305+08:00启动。01:13:31.754新Linux host_guard写入FILE_MANIFEST_v10的payload_manifest_verified并实际Popen；01:16:18/01:16:28/01:16:37重建三个未计时暖机标记。已有两格文件时间和SHA不变，R4整项尚未完成。

NAS原始快照results/interrupted_r4_v9_20261004，审核control_records/partial_review_v10_20261004.json与warmup_recovery_v10_20261004.json，部署control_records/controller_v10_start_20261004.json；6个CPU冻结构建/验证脚本另归档control_records/v10_cpu_source_20261004。两组成功容量校准及预检均复用，不重跑。读取运行中的Windows控制JSON/log使用UTF8并共享ReadWrite/Delete，避免读句柄阻碍原子替换。自动跟进已更新v10，正常状态不重复提醒；继续R4→external→M2，逐引擎核验external，结果与SHA归档后才计完成。此处只更新实验操作状态，未改论文源或关闭comments。

## 2026-10-04 v9 启动等待修复

BRJD独立环境预检已于2026-10-03 21:44:10结束rc0；Python3.12.13、vLLM0.30.0、torch2.13.0+cu130 / CUDA13，本机RTX5090D / WSL2，权重完整SHA、token-ID API及allocator门通过。实际KV池52160 tokens / 7.17 GiB，仅作为BRJD同机容量依据。7个原始预检文件全SHA归档NAS results/preflight_brjd_v7，索引control_records/preflight_brjd_v7_sha256.json，后续复用，不能重跑。

v7首次R4在初始foreign检查失败；v8 R4于00:09:21进入启动守卫，00:12:25退出rc1。最后00:12:22仍为GPU8%的startup_wait，没有R4 Linux host_guard日志或结果，正式wrapper未启动。约180秒退出与源码启动等待上限相符；没有捕获traceback，超时诊断由源码与时长支持。Windows/WSL库存未见其他实验或模型服务；GPU负载主要来自桌面图形活动。失败log/history/status/guard及审核证据保存在NAS control_records/r4_v8_startup_review_evidence_20261004.json等文件，失败历史未删除。

新冻结v9只修控制：GPU或显存忙时继续等待，每2秒核新鲜Windowsheartbeat；显存≤6000MiB、GPU利用率≤5%、无foreign实验连续30秒才允许科学Popen。Linux foreign持续180秒仍失败，清空重置计时。Popen后的运行守卫、科学timeout与按出生时间只清理本任务进程的逻辑不变。科学脚本、任务参数及80%显存预算均未改。19项CPU模拟PASS，包括GPU忙400秒、显存忙240秒后的30秒稳定门，foreign连续180秒失败与短暂恢复、过期心跳拒绝，以及旧/未来失败审核拒绝。

v9部署时ACTIVE_CONTROL.json指向controller_v9.py / queue_v9.json / FILE_MANIFEST_v9.json（DESKTOP-BRJD313，E:\r260401-brjd-20261003）；87项payload在E/WSL/NAS逐文件SHA一致，manifest SHA 705f6501b8026a0f7c4cd33d3bf8d13d0014c47714ceb01cd54649c172539949。一次性R4_STARTUP_REVIEW_v9.json绑定当前3条history、原v8失败status、累计failurelog及冻结证据，review SHA b79e98786d152e33be6085d4dfed7de8f6be1032efaf9131c0faf80849297e07。后续新增失败不能复用此次审核。

v9控制器PID181076，出生时间1791044883.9047177，00:28:03后台启动并核对一致；成功预检已跳过，接续R4→external→M2。status=running可能只是启动守卫运行，须核Linux最新v9 payload_manifest_verified和wrapper结果后才称科学任务实际启动。自动跟进已更新v9，资源等待状态不变时保持安静。external逐引擎MEASURED/UNMEASURED需单独核验，完成结果及逐文件SHA归档NAS。本文仅更新操作状态，未改主稿、回复信或关闭comments。

## BRJD 接续更新（2026-10-03）

用户指示单卡任务改用本机 BRJD，并“跟在他后面跑”。四卡机连接恢复后实查：R1 于 07:01:04 START、07:09:34 END rc=0；R2 于 07:09:34 START、07:17:34 END rc=0；R3 于 07:17:34 START，迁队时仍在运行。**这三项继续使用远端结果，不在 BRJD 重跑**；R1/R2 已核验 DONE、API盐/ID门、20 cells/640 stages、monitor无错与 invalid attempts=0。完整结果60文件（5,034,005字节）逐文件 SHA 一致，归档四卡机 NAS `vllm_4gpu_20261003/results/completed_r1_r2_20261003_072856`，审核 JSON `control_records/completed_r1_r2_audit_20261003_072856.json`。R1 两臂±2%时序复用门通过；两项配对token轨迹均有漂移，不能声称实际工作负载完全相同。R2 的640次输出仍全部128 token / finish length，EOS stop=0，未形成短输出EOS稳健性的实证。

07:24:59 已只从远端待跑 jobs.txt 移出 R4、external、M2，迁移前后内容及 SHA 保存在 NAS `vllm_brjd_20261003/control_records/remote_transfer_v3.json`；没有终止或移动已经 START 的实验，没有改其他项目任务。

| 顺序 | 项目与标签 | 机器 | 当前状态 |
|---|---|---|---|
| 前序 | R260306 整轮 Qwen scale sweep | DESKTOP-BRJD313 | 保持已暂停；用户优先指令允许本项目在其暂停期间执行 |
| 0 | R260401 / r260401_brjd_20261003_preflight | BRJD 单卡 | 完成rc0；7文件全SHA归档，环境/权重/API/容量门通过，后续跳过 |
| 1 | R260401 / r260401_brjd_20261003_r4 | BRJD 单卡 | v10已实际接续；共同容量43328，保留2格有效测量，补其余未完成格 |
| 2 | R260401 / r260401_brjd_20261003_external | BRJD 单卡 | R4 后；逐引擎核验 MEASURED/UNMEASURED |
| 3 | R260401 / r260401_brjd_20261003_m2_pipeline | BRJD 单卡 | external 后；本机重新校准、pilot、正式 rep |

以下为07:24初始控制记录（历史；现行入口以上方v10指针为准）：本机控制器 `E:\r260401-brjd-20261003\controller_v3.py` 已后台启动（PID 59220，创建时间 07:24:43）；状态 `control/status.json`，心跳 `control/windows_guard.json`，执行计划 `queue_v3.json`，当前版本指针 `ACTIVE_CONTROL.json`。逐项任务前要求完整前序结束、无其他 Python 实验或 llama 模型服务、GPU 利用率≤5%、已用显存≤6000MiB，连续稳定30秒；普通桌面占用会留在记录中。前序暂停或瞬时空闲不作为完成证据，不会停止前序来腾卡。

BRJD 上 vLLM 显存预算预先固定80%，留桌面余量；实际池容量和时延基线在本机重新测量。同一格各臂仍用共同可行容量。本机结果与四卡机结果明确分开，不能拼接绝对计时表。科学解释边界保留原记录，尚未因此关闭任何 comments。

冻结包在 NAS `W:\ML\ml-work\R260401-profilingai-trans\vllm_brjd_20261003\frozen`，49个 payload 文件 SHA 由 `FILE_MANIFEST_v3.json` 绑定（清单 SHA `65295827c057aa0206fe6c8020c26a9b023c0167c4fb3307f275fabe6a362c16`）；E盘和 WSL `/home/hxu/exp/r260401_brjd_20261003` 已逐文件一致并通过 Python/shell 语法检查。控制器/守卫 CPU 故障门测试通过，包括心跳过期拒绝、清单漂移拒绝、只清理本任务进程组。旧 v1/v2 包保留审计。

已启用当前聊天的自动跟进 `r260401-brjd`，每30分钟检查；状态不变保持安静，开始、完成、失败或需要用户处理时通知。需保持 BRJD 开机且 Codex 应用运行。自动任务会核验逐引擎状态、归档完成结果和 SHA 到 NAS，并更新小型状态摘要。中断/失败先复核历史与现存进程，不盲目重复实验。


### 07:36 自动跟进修正

07:31 跟进确认队列控制器及心跳正常、尚无本机任务尝试，四卡机 R3 仍在运行。发现一个长期只读聊天监视器被 Python 守卫误报：代码仅做 `mode=ro` / `query_only` SQLite 查询，不使用 GPU、不启动子进程，累计 CPU 约0.23秒。已用新冻结控制 v4 修正；只对完整解释器路径、绝对脚本路径、完整参数及固定SHA同时匹配的该服务豁免，每次检查重新核SHA。其他实验进程门和前序完整完成门不变。

原等待控制器正常退出后，新控制器 `controller_v4.py`（PID 164028，07:36:19 创建）已后台接管；未停止 R260306 或任何模型服务，未运行/重复本项目 GPU 实验。当前指针 `ACTIVE_CONTROL.json` 对应 `queue_v4.json`、`FILE_MANIFEST_v4.json`。54个冻结payload文件E/WSL/NAS一致，清单SHA `d6643976bddacb504a668eec09cd916300529499d91feb95cc9cb9f37a14b884`。原 v3及旧记录保留审计，实验脚本和科学参数没有变化。自动跟进已更新为读取v4指针；当前仍等 R260306 整轮完成。


### 08:01 自动跟进：R3 完成与心跳修复

四卡机 R3 于08:06:35 END rc=0，bf16/FP8各20格、80会话、2560级；实际池均71856 tokens / 4491 blocks，共同实际 TRITON 后端、ID盐门通过，无作废格/FAILED/测量错误。65个原始及冻结输入文件（18,689,809字节）双端前后 SHA 一致，完整归档 `vllm_4gpu_20261003/results/completed_r3_20261003_081006`，审核摘要 `control_records/completed_r3_audit_20261003_081006.json`。FP8尾日志8条ERROR都发生于明确shutdown/SIGINT之后，计量期间ERROR=0，原文已保留。跨精度20个配对token轨迹全部漂移：只能报告同容量/后端下配置端到端差，不能声称严格相同token的纯精度时延归因。

本机仍无本项目 GPU 任务尝试。进程守卫旧版逐进程读取所有cmdline/create_time，实测59.09秒；简单筛选仍27.67秒，会超过20秒心跳门。新冻结v5使用原生Windows tasklist快照后只读取Python候选详情；与CIM/psutil对照候选PID完全相同，完整inventory含nvidia-smi只读查询3.34秒、CPU0.14秒。故障/CSV不完整仍拒绝放行，无缓慢fallback；Python实验、llama服务、固定SHA聊天监视器和空闲/复核规则均通过CPU自检。只改变守卫观察成本，不改实验代码或参数。

08:15:30新控制器 `controller_v5.py`（PID194660）已接管；原等待控制器正常退出，未停止其他实验或模型服务。当前 `queue_v5.json`、`FILE_MANIFEST_v5.json` 绑定60个payload，E/WSL/NAS一致，清单SHA `d57e54eb7d230416bb985709fb518864c0c78e0629df3b0907a59318145ce5ca`；旧v1-v4保留。状态及迁移证据存NAS `vllm_brjd_20261003/control_records`，当前自动跟进已更新指针。仍仅在R260306整轮complete、其他实验退出及GPU稳定空闲后，执行预检→R4→external→M2。


### 08:31 前序暂停待处理

核实 R260306 的 `STOP_REQUESTED` 于08:15:15创建，原文记录外部 Qwen3-1.7B workers 占用所需显存且没有停止权限；状态08:15:29 `stopped_by_request`，coordinator及相关worker已退出，暂无恢复迹象。该项目原聊天已说明14B全驻留/32B大缓存对照尚需空闲显存，等待用户处理两个外部模型服务的授权边界。当前 R260309 FSB仍在执行，不能将暂停或暂时空闲当作完整完成，也不能由本监视器改前序/停其他服务。

R260401 controller_v5 PID194660及创建时间核对一致，60-file冻结清单E/NAS一致，心跳正常、无错误及执行历史。保持自动等待；前序原任务恢复并整轮complete、所有其他实验/模型服务退出、GPU稳定空闲后，才启动本项目。此暂停原因已通知一次，状态不变时后续自动跟进保持安静。检查证据 `vllm_brjd_20261003/control_records/heartbeat_20261003_0831.json`。

以下四卡机六项表是 **06:13 历史快照**，当前分工以上表及 ACTIVE_CONTROL.json 实际指针为准。

本轮控制机为 DESKTOP-BRJD313，经既有 jason.conf / wireproxy 转发连接四卡机 `host`（192.168.5.11）。用户指示“直接用这个四卡机开始排任务”，本轮已上传冻结执行包并实际追加 paperq 队列。以下时刻均为北京时间；最后实查 **2026-10-03T06:13:37.039479+08:00**。

## 四卡机历史状态（06:13 快照）

预检已于 **05:48:03 START → 05:52:04 END rc=0** 完成。Qwen3-8B / vLLM 0.30.0 / torch 2.13.0，实际自动后端 KV 池 **87,152 tokens / 11.97 GiB**；token-ID API可用，固定128输出，同盐命中240、异盐命中0。

截至最后实查，下面**六项均真实在 jobs.txt 中，尚未 START**，failed.txt为空。全部为X整机独占，按表顺序追加；原在跑的 `r260818rt_B15_r34_L35-39`（PID603735，GPU2）仍未退出。06:08:44日志继续推进到layout38 / v10 / r4 / 500/601。GPU瞬时0%不能当作退出证据。调度器会在前序任务及所有GPU计算进程退出后自行启动X任务。本轮没有终止前序任务或调整其他任务顺序。

| 序 | 队列标签 | 可执行内容 | 状态 | 超时保护上限 |
|---|---|---|---|---|
| 1 | r260401_20261003_r1 | 8B、3400×4×8、A0/R、10配对rep；旧同机并行记录对整机独占复测；固定旧容量71840 tokens | 待独占窗口 | 2h |
| 2 | r260401_20261003_r2 | 同一网格和容量；取消强制EOS延长，记录实际输出/上下文轨迹，10rep | 已排在R1后 | 2h |
| 3 | r260401_20261003_r3 | 8B、900×4×32；共同TRITON_ATTN，先读bf16实际容量，再给FP8同block数，10rep | 已排在R2后 | 6h |
| 4 | r260401_20261003_r4 | 原生R / A0 / A-uncap / P-admit；3400×4×8与900×16×8；10rep，配置顺序轮换 | 已排在R3后 | 6h |
| 5 | r260401_20261003_external | 独立LMCache/SGLang环境：安装→真实语义/128-ID门→各栈内10rep对照 | 已排在R4后 | 12h |
| 6 | r260401_20261003_m2_pipeline | 实际校准→3rep pilot→预声明精度/工时冻结→10–20个独立正式rep的同时区间估计 | 已排在external后 | 48h |

超时上限是故障保护，**不是预计用时**。M2单格30s预热+120s观测+69s drain=219s；六点三臂三rep的54格pilot最少3.285h，另加启动/清理。正式10–20rep同网格最少10.95–21.9h，另加30–60次启动。主实验会按实测pilot写POWER_WORKTIME，不把整机四张卡简单当作四倍计时加速。

## 已核验与解释边界

- 远端执行脚本py_compile、shell bash -n均通过；远端EngineArgs API-server配置读取通过，实测默认max_num_seqs=256，未留下自己的CUDA计算进程。M2接纳边界、可复现外生到达/保持和blocked/unfinished分母检查通过。
- 冻结后文件不覆盖；若代码变更用新文件和新结果根。R1/R2在尚未START时只更新了自己的待跑命令，原FIFO位置保留；旧版本作为审计材料留存。启动前逐项核SHA。
- 原vLLM环境缺SciPy；首项X任务在独立runner环境补齐固定SciPy1.16.2，通过本环境.pth读取原vLLM包，不修改共享环境。官方元数据和1MiB wheel来源预检通过（HTTP206，约1.57MB/s）；后续仍以真实安装门为准。
- R1旧并行容量71840、本轮自然预检87152不同，复测用旧容量；其差异只作同机时序复用判断，不宣称干扰因果或缓存机制等价。R2/R3保存实际token路径，路径漂移时不能声称同一实际工作负载。
- R4当前两个网格为低压基准，默认scheduler不绑定时A0/A-uncap可以相同；没有承诺接纳率差异。P-admit是APC上的逻辑峰值预接纳，不调用physical GPU pin。
- LMCache的pin保护LocalCPUBackend CPU块，不保证GPU KV驻留；用实际反淘汰及external cached-token命中作门。SGLang只与自己的清缓存零命中臂配对，跨栈绝对时延不混表。外部多个95%区间是未经多重校正的探索性边际区间，依赖重复差近似正态；缺统计依赖时明确CI unavailable。安装/兼容/计量失败写UNMEASURED，不能把wrapper退出当作测量成功。
- M2明确是**matched-capacity补充实验**：先量自然A0/uncap池，再冻结共同可行整块容量；闭环披露1s思考，主方向为预生成独立开环到达/保持。pilot仅用方差/工时，按事前规则选10–20rep；精度不足记underpowered。保留逐级token轨迹审计，漂移/缺级单独标记。原v2无思考M1/M2、独立第四坐标、GPU pin及作者贡献裁决不因这次排队而通过，主稿/回复信未改。

## 文件与接手入口

远端实验根：`/home/host/exp/r260401_20261003`（四卡机）；队列 `/home/host/paperq/jobs.txt`，逐任务日志 `/home/host/paperq/logs/<标签>.log`。

完整冻结源码、协议和已完成预检原始证据存于 `W:\ML\ml-work\R260401-profilingai-trans\vllm_4gpu_20261003`（NAS，通过DESKTOP-BRJD313访问；NAS内部 `/volume3/Warehouse/ML/ml-work/R260401-profilingai-trans/vllm_4gpu_20261003`）。镜像 `exp/r260401_20261003` 有43个已校验文件，FILE_MANIFEST.json记录逐文件SHA。`control_records/queue_snapshot_final.json`、`remaining_enqueue.json`、`native_enqueue_v2.json`保存追加与最终状态证据。本轮只归档已完成预检；后续正式结果尚未产生，未清理远端运行树。

- 原生R1–R4冻结源码SHA：`95d812990a140f078a6edcb8185a2b359301c7fe9864ccd844948fdae1e85b66`。
- M2当前入队源码SHA：`c034f74b9c9eca4de8defb45c3ad1a99820861afb125cce923d61d52642dfce8`。
- EXECUTION_PROTOCOL_v2、EXTERNAL_EXECUTION_PROTOCOL、M2_EXECUTION_PROTOCOL_v3分别记录对应依赖、命令及科学解释边界。旧M2_67bf/旧R12包保留审计，当前队列不执行它们。

本页更新“是否排队”的操作状态；[COMMENTS_UPDATE_20261003.md](COMMENTS_UPDATE_20261003.md)仍负责03:20活动稿的逐条comments核对。删除旧段、提交任务、测量完成和审稿关闭是四种不同状态。

## 用户优先调度（2026-10-03）

本聊天用户先说“优先做你的这个”，随后授予完全权限。旧的“必须等R260306整轮complete”排序已被本次指令替代；冻结 USER_PRIORITY_20261003.json 记录授权，前序真实状态必须仍为 stopped_by_request 或 complete，不能把暂停伪造成完成。R260306 的文件、结果和暂停状态均保留。

DESKTOP-BRJD313 21:36启用的优先控制版本为v7（历史）：E:\r260401-brjd-20261003\ACTIVE_CONTROL.json 指向 controller_v7.py / queue_v7.json / FILE_MANIFEST_v7.json；72项 payload 在 E/WSL/NAS逐文件SHA一致，manifest SHA为70242a08c0a3bce39302d291d3647d7f68bcdde523e83d84931a21ed515823ac。v6没有执行测量，部署预检误报两个WSL系统维护服务的证据保留。v7对其采用完整参数和脚本固定SHA，每次核验；新增WSL foreign进程检查绑定PID出生时间并失败关闭，防止外部实验混跑。CPU验证通过，科学脚本和80%显存参数不变。

已仅释放5个失去父任务的空闲 llama-server（PID132336/203092/209020/146964/194140）；释放前核对精确PID/出生时间/启动参数、父进程退出，连续30秒三次 slots 均空闲且无 established 客户端，记录启动参数及测量快照。NAS证据 control_records/priority_idle_orphan_release_20261003.json。共享代理未停止。资源释放后显存约3105MiB（DESKTOP-BRJD313）。新控制器后台执行每项前的30秒排他门；正式队列仍为独立环境预检→R4→external→M2。

启动、部署、源码校验与controller切换证据均在 NAS W:\ML\ml-work\R260401-profilingai-trans\vllm_brjd_20261003\control_records。自动跟进已更新为优先策略，未完成结果不宣称MEASURED；未经新指令不改论文源或关闭审稿comments。
2026-10-04 00:36（DESKTOP-BRJD313）：R4 default/uncapped两组容量校准结束，各为calibration_only_no_timing_cells；共同容量已写capacity_freeze.json，进入repetitions。14个校准与冻结文件逐文件双端SHA核验归档NAS results/r4_calibration_v9_20261004，索引control_records/r4_calibration_v9_sha256_20261004.json。尚未将R4正式计时标为完成。

2026-10-04 01:27（DESKTOP-BRJD313）：v10控制器PID60624及出生时间一致，心跳running/无foreign/err空；E与NAS的123项冻结SHA全部通过。R4首轮r00的8格、640级输出已通过完成状态/128输出/提示计数/43328容量/监视守卫中间检查，原2格SHA保持。当前r01继续，整项10rep及配对token路径最终审核尚未完成；external/M2待接续。r00全树28文件双端前后SHA一致，归档results/progress_r4_r00_v10_20261004_012756，检查点control_records/heartbeat_r4_progress_v10_20261004_012756.json。主阶段正常，无需重复通知。

2026-10-04 01:55（DESKTOP-BRJD313）：R4持续推进，r00-r03共32格已写完，当前观察40/80格；v10控制器及出生时间一致，Windows心跳running/无foreign，前序仍stopped_by_request。新增r01-r03的24格、1920级通过DONE/API盐与ID/提示和128输出/43328容量/监视守卫中间检查，无invalid attempts；两格v9保留SHA不变。新增三轮全树84文件双端前后SHA归档results/progress_r4_r01_r03_v10_20261004_015544，索引control_records/heartbeat_r4_progress_v10_20261004_015544.json。R4最终配对token路径和统计审核待完整10轮；external/M2尚未开始。主阶段正常，保持安静。
