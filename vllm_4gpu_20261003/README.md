# R260401 四卡机追加执行包：2026-10-03

控制机DESKTOP-BRJD313，实验机器host /192.168.5.11 /四张RTX5090；NAS是存档位置，不是这次测量机器。用户已授权直接安排任务。最新队列采样 2026-10-03T06:13:37.039479+08:00：预检正常结束，六项X正式任务仍在jobs.txt，等RT B15 PID603735退出。

镜像 `exp/r260401_20261003/` 对应四卡机 `/home/host/exp/r260401_20261003/`。当前43个冻结源码/协议/已完成预检证据见FILE_MANIFEST.json。完整正式结果尚未产生，远端树保留。

执行顺序：r1 → r2 → r3 → r4 → external → m2_pipeline。当前原生源码rplus_95d81299.py；M2为m2_c034f74b.py及run_m2_v3.sh；旧R12/M2_67bf保留审计，未用旧版本启动正式任务。共享kvbench.py只改本轮缓存路径，原vLLM环境和模型不覆盖。独立runner环境于X任务内补SciPy1.16.2；外部LMCache0.5.5/SGLang0.5.20各独立环境，有真实安装/API/缓存语义门。

本轮是补充测量：R1固定旧同机71840token容量；R4是低压native基准；LMCache测CPU pin而不是GPU pin；SGLang仅与自己的零命中臂比较；M2采用共同可行容量、独立开环及预声明精度规则，闭环披露1s思考，pilot不合并正式。不能自动宣布原M1/M2、贡献或GPU pin通过。

control_records目录保存最后队列快照与传输SHA记录。paperq runner中的START/END、各任务真实DONE/FAILED/UNMEASURED及raw cells为后续状态依据；wrapper完成不代表测量成功。论文当前操作记录位于 `D:\CodeBase-708a\063-submitted-paper\R260401-profilingai-trans\working\notes\design_review_20261001\FOUR_GPU_QUEUE_20261003.md`。
