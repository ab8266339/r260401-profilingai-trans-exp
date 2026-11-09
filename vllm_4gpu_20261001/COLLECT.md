# COLLECT：队列跑完后怎样取回 R260401 4卡机结果

> 2026-10-01 写。从 DESKTOP-EJKE5D9 取（WireGuard 身份 `jason.conf` 同一时间只能一台机器用）。别的机器上没有 `rsh.py` 的隧道，路径也不同。

## 1. 先确认跑完了

在 Git Bash 里：

```bash
# From the operator machine that can SSH to the four-GPU host:
ssh <four-gpu-host> 'grep r260401 ~/paperq/runner.log; grep r260401 ~/paperq/failed.txt; grep -c r260401 ~/paperq/jobs.txt; ls ~/exp/r260401/results/*/*/DONE'
```

- 跑完的标志：`runner.log` 里两条 `END rc=0`（`r260401_kv_main`、`r260401_kv_72b`），14 个 `DONE`（4 个单卡模型 × 3 个配置 + 72B × 2 个配置）。
- `rc` 非 0：看 `~/paperq/logs/r260401_kv_*.log`、`~/exp/r260401/logs/card{0..3}.log`、`logs/tp4.log`，以及出错配置目录里的 `server_info_error.json` 和 `server.log`。修好后**同一标签重新排队**即续跑（已完成的格子会跳过）：
  `printf '%s\t%s\t%s\n' X r260401_kv_main 'cd ~/exp/r260401 && ./run_main.sh' >> ~/paperq/jobs.txt`
- 在 4卡机上重算汇总（CPU，几十秒）：`python rsh.py 'cd ~/exp/r260401 && ~/venv/bin/python summarize.py ~/exp/r260401/results'`

## 2. 远端文件 → 本地位置

本地根：`D:\All Projects\063-submitted-paper\R260401-profilingai-trans\working\exp_4gpu_20261001\results_4gpu\`（DESKTOP-EJKE5D9；同步盘内）。只取 CSV/JSON 与日志，模型权重留在 4卡机。

| 远端（4卡机） | 本地 | 大小量级 |
|---|---|---|
| `~/exp/r260401/results/summary/*.csv`、`kv_bytes.json` | `results_4gpu/summary/` | 几 MB（`stage_records.csv` 最大） |
| `~/exp/r260401/results/<模型>/<配置>/server_info.json` | `results_4gpu/<模型>/<配置>/` | 每个几十 KB |
| `~/exp/r260401/results/<模型>/<配置>/cells/*.json` | `results_4gpu/raw_cells.tar.gz`（打包） | 原始逐格记录，含 0.2 s 指标采样，预计几十到一两百 MB |
| `~/exp/r260401/results/<模型>/<配置>/server.log` | 同上打包 | 每个几 MB |
| `~/exp/r260401/logs/{card0,card1,card2,card3,tp4}.log`、`~/paperq/logs/r260401_*.log`、`~/paperq/runner.log` 中 r260401 行 | `results_4gpu/logs/` | 小 |
| `~/exp/r260401/model_verify.json` | 已在 `setup_records/model_verify_4gpu.json` | — |

打包后用多路下载（隧道单连接只有约 0.4 MB/s）：

```bash
python rsh.py 'cd ~/exp/r260401 && tar czf /tmp/r260401_results.tgz results logs/card*.log logs/tp4.log && grep r260401 ~/paperq/runner.log > /tmp/r260401_runner.txt && cp ~/paperq/logs/r260401_* /tmp/ && ls -la /tmp/r260401_results.tgz && md5sum /tmp/r260401_results.tgz'
```

`4卡机` 目录下只有上传脚本 `pupload.py`，没有现成的多路下载脚本。包在 50 MB 以内时，直接用 paramiko SFTP 单连接取（约 2 分钟）；更大时，先在远端 `split -b 32M`，再并行取各段，本地合并后核对 md5。取回后解包到 `results_4gpu/`，`summary/` 与 `server_info.json` 平铺，`cells/` 与 `server.log` 保持在 `raw_cells.tar.gz` 里不解开，以免同步盘里出现上万个小文件。

## 3. 取回之后

1. 核 md5；在本地对解包后的结果再跑一次 `scripts/summarize.py results_4gpu`（需要 numpy/scipy），与远端汇总逐文件比对。
2. 按 PLAN.md 第 8 节的规则逐条判读（E1–E7），判读写进新笔记 `notes/R1_4GPU_KV_FINDINGS_<日期>.md`，不改 PLAN 里的规则。
3. 补全 DATA_PROVENANCE.md 第 5、6 节。进论文的数字只能出自 `results_4gpu/summary/`，并标明 4卡机、vLLM 0.30.0、模型与 KV 精度；与表 VI（DESKTOP-BRJD313，Ollama）分表。
4. 远端清理（用户同意后再做）：`~/exp/r260401/smoke_results/` 可删；`~/models/Qwen/*` 是共用缓存，**不要删**。
