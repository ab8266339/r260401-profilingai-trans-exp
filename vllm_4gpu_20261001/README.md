# R260401 4卡机 vLLM 状态字节与钉住/重放实验：完整备份

- 来源：4卡机（主机名 `host`，192.168.5.11，4×RTX 5090，vLLM 0.30.0）`~/exp/r260401/` 全目录，外加 paperq 队列日志 `~/paperq/logs/r260401_kv_main.log`、`r260401_kv_72b.log`。
- 运行：2026-10-01 13:28–23:56（北京时间），两条队列任务 rc=0，14/14 配置完成。方案与判读见同步盘 `063-submitted-paper/R260401-profilingai-trans/working/exp_4gpu_20261001/`（PLAN.md、DATA_PROVENANCE.md）与 `working/notes/R1_4GPU_KV_FINDINGS_20261002.md`。
- 备份：2026-10-02 01:4x（北京时间），DESKTOP-BRJD313-7 会话经 DESKTOP-BRJD313 常驻 wireproxy（jason.conf）从 4卡机直接流式打包传回，未在 4卡机落临时文件。
- 校验：`_archive/SHA256SUMS_remote.txt` 是在 4卡机上对 `exp/r260401` 逐文件计算的 SHA-256（1268 个文件）；解包后在 NAS 上 `sha256sum -c` 全部通过。`_archive/paperq_sha.txt` 同理（2 个文件，通过）。压缩包 `_archive/r260401_4gpu_full.tar.gz` md5 `2d95fb4f72ba82cfc20adaf08bfecb2c`，传输前后一致。
- 目录：`exp/r260401/`（脚本、results/ 含逐格 cells 与 server.log、summary/、logs/、cache/、smoke_results/、model_verify.json）；`paperq/logs/`。
- 所有绝对数只属于 4卡机这一配置，不与 DESKTOP-BRJD313 的 Ollama 测量并表。
- 本目录尚未提交到该 git 工作区（`r260401-profilingai-trans-exp`）。
