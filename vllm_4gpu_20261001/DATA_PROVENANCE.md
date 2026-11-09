# DATA_PROVENANCE：R260401 4卡机 vLLM 状态字节与钉住/重放实验

> 存根，建立于 2026-10-01（DESKTOP-EJKE5D9 会话）。结果取回后按 COLLECT.md 补全第 4 节以后各节。
> 这里的所有数字都只属于 **4卡机**（主机名 `host`，192.168.5.11），不得与 DESKTOP-BRJD313 的 Ollama 测量（表 VI，`analysis/pinned_kv_real_model_qwen3.json`）并表。

## 1. 机器与环境（4卡机）

| 项 | 值 |
|---|---|
| 硬件 | 4×RTX 5090 32 GB，2×Xeon Gold 6530（64C/128T），503 GB 内存，984 GB 单盘；Ubuntu 22.04.5 |
| 驱动 | 580.178.04（CUDA 13.0） |
| venv | `~/venvs/vllm`（4卡机）：Python 3.12.14（uv 管理），vLLM 0.30.0，torch 2.13.0+cu130；2026-09-30 用 uv 从清华 PyPI 源安装 |
| 汇总用解释器 | `~/venv`（4卡机）：Python 3.11.16，numpy 2.2.0，scipy 1.17.1 |
| 工作目录 | `~/exp/r260401/`（4卡机）；结果 `~/exp/r260401/results/`，汇总 `~/exp/r260401/results/summary/` |
| 模型缓存 | `~/models/Qwen/<名称>`（4卡机，与其他论文共用） |
| 队列 | paperq，`~/paperq/jobs.txt`；日志 `~/paperq/logs/<标签>.log` |

服务端环境变量：`VLLM_USE_FLASHINFER_SAMPLER=0`，`CUDA_HOME` 指向 venv 内 pip 装的 `nvidia/cu13`，`VLLM_SERVER_DEV_MODE=1`（开放 `/reset_prefix_cache`），离线模式。FP8 KV 配置加 `--attention-backend TRITON_ATTN`。原因见 PLAN.md 第 2 节。

## 2. 下载源测速（4卡机，2026-09-30 19:4x UTC）

模型（Qwen3-4B 第 1 个分片，取前 100 MB）：

| 源 | 结果 |
|---|---|
| ModelScope 直连 | 13.2 MB/s（采用） |
| hf-mirror.com | 10.0 MB/s |
| huggingface.co 直连 | 连不上（HTTP 000） |

pip（vllm-0.30.0 wheel 取前 64 MB）：中科大 27.6 MB/s，清华 23.3，上交 23.3，官方 3.6，阿里云失败。说明：vLLM 那时已用清华源装完（按 09-30 README 的先验，清华 33 MB/s），这次测速在安装之后，中科大略快，差别不影响安装结果。

## 3. 模型（ModelScope，`modelscope download --max-workers 8`）

| 模型 | 修订 | 字节 | 用时 | 逐文件 SHA-256 对 ModelScope 元数据 |
|---|---|---|---|---|
| Qwen/Qwen3-4B | 2c54d5a0 | 8,060,931,352 | 463 s | 14/14 一致 |
| Qwen/Qwen3-8B | 26028140 | 16,397,465,992 | 439 s | 16/16 一致 |
| Qwen/Qwen3-14B-AWQ | 5a6fa840 | 9,992,689,411 | 612 s | 13/13 一致 |
| Qwen/Qwen3-32B-AWQ | c1456858 | 19,341,530,260 | 725 s | 15/15 一致 |
| Qwen/Qwen2.5-72B-Instruct-AWQ | 409c86ad | 41,607,442,775 | 853 s | 22/22 一致（最后一个分片下载时断过一次，modelscope 自动重试，校验通过） |
| Qwen/Qwen3-0.6B | — | 约 1.5 GB | — | 仅用于冒烟，未校验 |

逐文件记录：`setup_records/model_verify_4gpu.json`。下载日志：4卡机 `~/exp/r260401/logs/dl_models.log`。

## 4. 冒烟（不作结果）

全部在 4卡机卡 3 上做，当时该卡同时在跑 WAIRC 训练（约 13.5 GB、75–90% 利用率），所以冒烟计时没有意义。每次 vLLM 实例上限 0.12 × 32 GB ≈ 3.9 GB、`--enforce-eager`，总时长不超过 3 分钟。

| 时间（UTC，09-30） | 内容 | 结果 |
|---|---|---|
| 19:57 | 0.6B，bf16 | 失败：FlashInfer 采样器 JIT 找不到 nvcc → 改用 PyTorch 采样器 |
| 20:00 | 同上 | 175 s 超时（服务就绪 99 s + 建词元池），实例由我手动结束 → 词元池改为预先在 CPU 上算好并缓存，冒烟输出改 16 token |
| 20:05 | 0.6B，bf16，smoke 网格 | 通过：盐隔离有效（同盐命中 240，异盐 0）；钉住各级命中整块前缀，重放 0；输出长度正确；分配器 114,967 B/token 对理论 114,688（比 1.0024） |
| 20:08 | 0.6B，bf16 + 2 GiB CPU 卸载 | 通过：清空 GPU 缓存后命中 512（探针）；存取臂各级从 CPU 层命中 336–480 token |
| 20:11 | 0.6B，FP8 KV | 失败：自动选的 FlashInfer 后端缺 ninja |
| 20:13 | 0.6B，FP8 KV，TRITON_ATTN | 通过：KV 容量 40,176 token（bf16 时 20,080），即每 token 字节减半 |

冒烟输出在 4卡机 `~/exp/r260401/smoke_results/`，不取回、不引用。

## 5. 正式运行（2026-10-02 由 DESKTOP-BRJD313-7 会话取回后填写）

- 排队：2026-10-01 约 13:00（北京时间），`r260401_kv_main`（X）、`r260401_kv_72b`（X），排在 `r260702_gpt-oss-120b` 之后。
- 实际起止（paperq `runner.log`，北京时间）：`r260401_kv_main` 10-01 13:28:47 → 20:16:47，rc=0；`r260401_kv_72b` 20:16:47 → 23:56:18，rc=0。`failed.txt` 为空。DONE 标记 14/14，无缺格。
- 汇总：4卡机 `summarize.py` 于 15:56:10 UTC（72B 结束时）生成，cells rows=63520、pairs=5800、contrasts=1240、configs=14。
- 取回（2026-10-02 01:16 北京时间，经 DESKTOP-BRJD313 常驻 wireproxy / jason.conf）：`r260401_small.tgz`（2,626,738 B，md5 `2c8e1450c7de00b828cc874715ff252a`）解到 `results_4gpu/`（summary/、各配置 server_info.json、logs/、paperq/、model_verify.json 与三个运行脚本）；`raw_cells.tar.gz`（9,296,907 B，md5 `3dce514b765287d12287197c37dd30be`，1100 个逐格 JSON + 14 份 server.log）原样保存不解开。两包取回后 md5 均一致。
- 本地复算：在 DESKTOP-BRJD313（系统 python 3.13.3）解开原始包到临时目录，用 `scripts/summarize.py` 重算，五个汇总文件与 4卡机版本逐行逐值一致（唯一差别是配置路径分隔符 `\` 与 `/`）。
- 判读：`scripts/judge_e1_e7.py` 按 PLAN.md 第 8 节规则产出 `results_4gpu/judge_e1_e7.json`，文字结论在 `notes/R1_4GPU_KV_FINDINGS_20261002.md`。
- **完整备份与远程清理（2026-10-02 01:4x，用户同意）**：4卡机 `~/exp/r260401/` 全目录（1268 个文件，含 cells、server.log、cache、smoke_results）与两份 paperq 日志，直接流式打包传回，备份到 NAS `/volume3/Warehouse/ML/ml-work/R260401-profilingai-trans/vllm_4gpu_20261001/`（= DESKTOP-BRJD313 的 `W:\ML\ml-work\R260401-profilingai-trans\vllm_4gpu_20261001\`）。在 NAS 上解包后，逐文件 SHA-256 与 4卡机上算的清单全部一致，说明见该目录 README.md。随后在 4卡机上再核一次数据未变，删除了 `~/exp/r260401/`、`/tmp/r260401_collect/` 和 `~/paperq/logs/r260401_kv_*.log`。共用的 `~/models/Qwen/*`、`~/venvs/vllm`、`~/paperq/runner.log` 以及别人的实验目录都未动。**4卡机上已经没有本实验的数据**，要重算就从 NAS 备份取。
- 每个配置的注意力后端、KV 容量、就绪用时：见各 `results_4gpu/<模型>/<配置>/server_info.json`。

## 6. 论文用到的每个数字（待补）

| 论文位置 | 数字 | 源文件与字段 | 配置 |
|---|---|---|---|
| — | — | — | — |
