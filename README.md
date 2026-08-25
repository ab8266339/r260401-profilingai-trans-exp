# r260401-profilingai-trans-exp

R260401 ProfilingAI-Trans（Agent Functional Splits）的完整实验工作区，包含实验代码、配置、运行记录和结果。

## 关联

| 项 | 位置 |
|---|---|
| 论文与维护文档 | `D:\CodeBase-708a\063-submitted-paper\R260401-profilingai-trans\working\` |
| 本机实验工作区 | `E:\ml-work\R260401-profilingai-trans\`（`DESKTOP-BRJD313`） |
| NAS Git 仓库 | `ssh://nas-git/volume1/Git/r260401-profilingai-trans-exp.git` |

## 目录

- `afs_path_validation/`：AFS 路径验证。
- `cross_backend_validation/`：跨后端实验及结果。
- `matlab_nr_validation/`：MATLAB NR 系统级与 PUSCH 链路级验证。
- `ns3_ul_validation/`、`ns3-oran/`：ns-3 验证与 O-RAN 集成占位。
- `ollama_measure/`：本地模型测量。
- `testbed_hil/`：HIL/测试床实验。
- `tier_b_simpy/`：离散事件实验。
- `tier_c_oran/`：O-RAN 闭环实验及完整日志。
- `flexric/`、`integration/`、`open5gs/`：集成占位目录。

## 管理约定

本仓库按 `023-project-managent/DEV-DISCIPLINE.md` 的实验数据仓库规则管理：实验工作区位于非同步的 E 盘，仓库名使用 `-exp` 后缀；实验数据、检查点、运行日志和结果允许全量版本化。Synology 元数据、依赖缓存、虚拟环境、构建目录和 SQLite 事务临时文件不提交。

2026-08-25 从 Synology Drive 中的原 `working/simulation/` 迁入。迁移校验覆盖 506 个文件、359,079,826 字节，源与目标逐文件 SHA-256 一致。
