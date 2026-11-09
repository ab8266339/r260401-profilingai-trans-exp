# R260401 · BRJD 本机 M2 执行协议（2026-10-03）

此包只排在 R260306 后面执行；排队和 Windows/WSL 资源守卫由主任务统一负责。本包没有启动 GPU，没有安装环境，也没有修改四卡机/NAS 冻结源。

与四卡机 c034f74b 的变化是主机、模型路径、Python环境和绝对期限基准：单卡 TP=1，实际自然 KV 容量先分别测 A0 与 A-uncap，再用共同可行最小整数块池，所有计量臂实际 allocator token budget 必须严格相等。本机结果独立成表，不混合四卡机绝对时延。

30秒预热后执行预声明的3次本机单流 A0 完整八级会话，取全部三次的中位数，写一次性的 local_baseline_before_pilot.json。期限固定为本机中位数的1.5/2/3倍，应用保持目标为1/3/6倍，drain=ceil(6×本机中位数)。四卡机旧11.40360071050236秒及其69秒drain不传入本机。任何 pilot 端点产生前，sampling_plan_before_pilot.json 冻结真实绝对期限、保持目标、tau0=3400、首次3450、每级50+128、八级4824、容量推导的N与开环率；后续不再校准或移动期限。直接重新校准不能覆盖既有freeze；续跑校验代码、模型、主机/GPU身份及本机基线证据hash。

闭环仍用1秒思考时间，仅作描述。开环Poisson到达与保持目标从固定本机校准导出，在所有臂之前生成并保存hash；不能由 active slots 反推到达。A0、A-uncap、P-admit完整在本机同一卡上重跑。P-admit是逻辑峰值预算+可驱逐APC，不冒充物理pin。

pilot默认3次，只提供方差和实测工时，不能并入正式结果。预声明规则在10..20中选主族同时区间预计半宽≤5个百分点的最小n；不足则20次并标underpowered。主族是3个开环率×3个固定期限的P-admit−A-uncap，Bonferroni paired-t，披露重复间差近似正态假设。正式seed从10000起独立，保持 interval_estimation_only，不自动宣布原作者M1/M2或贡献通过。

cohort [30,150) 每个生成会话进入分母；blocked、合法unfinished是miss，HTTP/API执行错误使格无效并停止。逐级prompt/output SHA和共同cohort已admit SID轨迹审计保持不变，漂移不能支撑identical realized token workload。

修复源pipeline里的前置NameError：原sampling_plan引用了尚未创建的audit_path/drift；本包前置计划只写审计规则，完成pilot/main后才将真实审计路径/hash/漂移写入summary。另从持久化server JSON读取服务启动时间，使恢复已有格时工时证据不丢。

CPU_SELFCHECK.json 验证真实校准和pipeline控制流（硬件/服务器I/O仅作stub）：本机样例中位11秒得到期限16.5/22/33、保持11/33/66、drain66；旧机期限和硬件变化被拒；直接再校准被拒；前置冻结、9成员族、独立10次formal和resume三份freeze逐字节一致均通过。这里11秒只是CPU自检样例，不是本机GPU测量结果。
