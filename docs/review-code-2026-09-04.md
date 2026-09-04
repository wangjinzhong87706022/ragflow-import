# 两天代码评审与后期实施优化方案（图表 / xls 处理专项）

- **日期**：2026-09-04
- **评审范围**：2026-09-03 09:16 – 09-04 08:56 共 7 个提交（`9577709…759a1a9`，即项目 git 化后的全部历史）
- **评审对象**：src/ 引擎层（`shell_import.py`、`photo_triage.py`、`ragflow_client.py`、`config.py`/`tag_vocab.py`/`run_qc.py` 及 5 个测试文件）+ out/ 波次脚本（`out/xls_fusion/` 4 个、`out/photo_triage/rollout_photo_shell.py`、`out/retrieval_tuning/probe_p29.py`、`out/vision_8chart_test/qa_eval/run_eval8.py`）
- **方法**：双通道——逐文件人工评审 + 独立 code-review（8 角度 finder → 4 verifier 交叉验证，仅收 CONFIRMED）；关键发现均经本人对源码二次复核
- **边界声明**：本轮只评审与制定方案，**未改任何代码、未动库内状态**
- **关联文档**：`docs/review-vision-xls-2026-09-03.md`（上一轮评审，P0/P1/P2 定调源头）、`out/xls_fusion/TRIAGE_DECISION.md`（D1–D8 待裁定）、`docs/evaluation-f1-triage-2026-09-04.md`

---

## 一、范围与总体结论

两天 7 提交两条主线：①壳模式从 PNG 试点收编为通用引擎（`src/shell_import.py`）并完成 xls 三壳迁移（140→28 块）；②检索/元数据三个专项（P2-9 tag_feas+候选池、P1-4 chunk 就地修正、P1-5 2018 纠偏+meta_data_filter 双层修复）+ F1 收口 triage。

**总体结论：架构与安全设计经受住了三波 18 个目标（12 PNG + 3 xls + 3 照片预检）的实测，无阻断性缺陷；发现 5 个 P2 级（元数据漂移、rollback 无门、评测口径漂移、重建陷阱、明文 key）与若干 P3 级小疵，全部修复合计 ≈1.5 人时。** 主要风险形态不是"算错"，而是**状态漂移**：库内已纠偏的值与仓内 spec/存档/CSV 各自停留在不同时间点，重跑、回滚、重建三条路径都会静默回退修复。

### 肯定项（评审中验证过设计有效性的部分）

| 机制 | 实证 |
|---|---|
| preflight 锚点拒删 + 删前全量存档 + 串行 FAIL 即停 | 三波 18 目标零"已删未传"窟窿、零误删 |
| verify 门从 top10 改全 30 返回集 + QA 原句探针 | 88c54d2 三连 FAIL 教训的机制化，后续波次探针全过 |
| `search_datasets` 5 个可选参数缺省不进 payload | 行为向后兼容，有测试锁定 |
| `_normalize_meta_data_filter` 出口归一 | QC Q5 假阳性的根治点（conditions→manual） |
| photo_triage 断点续跑 + 解析失败不猜 + 大图阈值实测校准 | 262 张零丢失、3 漏网资产全捞出 |
| 枚举/config/词表三处一致性 pin 测试 | P1-5 扩 2018-8 时测试即时锁定三处对齐 |

## 二、发现清单（双通道合并去重，按严重度）

### P2 级（应修；不阻断当前使用）

| # | 位置 | 发现 | 后果场景 |
|---|---|---|---|
| 1 | `out/xls_fusion/rollout_xls_shell.py:41` | **TARGETS 元数据双重漂移**：spec 里 洪水统计(1).xls `flood_event="其他"`（P1-5 前的裁定值）；且 `--rollback` 恢复的是迁移时存档快照，其 meta 是更老的 `2008-8` | 重跑 `--apply`（清 state 后）或 `--rollback` 都会把 P1-5 纠偏值打回 → `meta_data_filter flood_event=2018-8` 钻取集缺文档（P1-5 验证过的"2008-8 残留=0"不变量静默破坏） |
| 2 | 同文件 `:91` | **`--rollback` 无任何门**：不 dry-run、不查 approved.flag，直接删活文档→重传→patch→重解析；而 `--apply` 路径双重门 | 一次误敲 rollback（或对着 finding 1 的陈旧存档 rollback）即破坏性变更且无人工门——违反本项目"mutating 脚本默认 dry-run、--apply 显式"铁律 |
| 3 | `out/vision_8chart_test/qa_eval/run_eval8.py:58` | **评测口径漂移**：判分用全返回集（v0.27.1 恒返 ~30 条）不切 `[:10]`，但报告头写"锚点全命中判分（run_qc 口径）"（run_qc / run_eval_xls 均切 top10）；`POOL=256` 系 P2-9 有意配置，与"口径一致"说法矛盾 | **"26 题检索 26/26"是全返回集口径的成绩，不能当 top10 证据引用**；rank 8→25 的回归或 64 池预截断复发时该 harness 仍显绿 |
| 4 | 三处 | **重建陷阱三连**（重导/重建后修复静默丢失）：① `out/mapping.csv` 五行仍 `2008-8`，`run_import.py` 重建导入会按 CSV 打回；② `run_setup.py:101` 标签库已有文档即跳过词表上传 → 全新环境 2018-8 标签块缺失（活库已手工补，仓内不可复现）；③ P2-9 tag_feas 回填 / P1-5 doc_meta `.keyword` mapping / P1-4、P1-5 一次性 patch 均无幂等脚本，仅存台账 json + 报告内嵌语句 | 从本仓重建 RAGFlow（或全量重导）会拿到**纠偏前**的元数据、缺标签块、缺 tag_feas——且全程无报错（2008-8 仍在枚举内，schema 合法） |
| 5 | `run_eval8.py:30` 等 ~5 个已提交脚本 | **明文 Bearer key + 双轨凭据**：chat 侧用硬编码 `KEY="ragflow-3NZD…"`，检索侧同文件却走 `RAGFLOW_API_KEY` 环境变量 | key 轮换后检索半边正常、chat 半边 401 → 问答层 0/8 的假回归信号；明文 key 已入 git 历史（已知问题，本地库风险可控，加 remote 前必须轮换） |

### P3 级（小疵；潜伏或影响台账语义）

| # | 位置 | 发现 |
|---|---|---|
| 6 | `src/shell_import.py:202` | `process()` 存档 print 引 `Path(archive_path).name` 未护 None——引擎默认参数组合（`archive_path=None`）直接 TypeError（现调用方都传路径，属潜伏崩溃点） |
| 7 | `src/shell_import.py:266-283` | `rollback()` 的 PUT chunk_method/parser_config 与 POST parse 不查响应 code（模块内其余 API 全查）；且轮询完成条件 `run in ("DONE","FAIL")` 把 **FAIL 也当"回滚完成"**返回 |
| 8 | `src/photo_triage.py:133` | 降采样守卫 `while … and img.width > 800`：窄长条（如 700×5000 传真件）或高熵图重编码后仍 >8MB 时**原样超限返回** → 网关 400 三连 → 永久"失败"记录且断点续跑（非 --force）永不重试——恰恰系统性损失要找的大尺寸文件扫描件 |
| 9 | `out/xls_fusion/extract_xls.py:60` | 本机通道探测 `import calamine` 模块名错误（pip 包 `python-calamine` 的模块名是 `python_calamine`，且未探测 pandas）→ local 分支永不激活，"自动降级"是死代码假象（docker 通道 27/27 通，无实害） |
| 10 | `src/requirements.txt` | photo_triage 惰性 `from PIL import Image`，requirements 无 Pillow（系统 python 恰有 PIL 12.3.0 掩盖）→ 新环境按 requirements 装完遇 >8MB 图 ImportError |
| 11 | `out/photo_triage/rollout_photo_shell.py:128` | `ok, sim = eng.verify(...)` 命名过期（verify 已改返 rank）；manifest 字段名仍写 `"similarity"`——台账语义错位（纯遥测，无读取方） |
| 12 | `src/shell_import.py:173` | `process()` dry-run 在 preflight 之前 return（photo 流 `process_new` 相反：先 preflight 后 dry-run）——dry-run 不校验前置条件，当时靠单独离线预检补位 |
| 13 | `src/run_qc.py:143` 注释；`shell_import.verify()` | 注释称"六问均单库"实际 Q1/Q3/Q4/Q6 多库；verify 失去早退后每文件恒烧第二次 ~8s 等待+检索（分钟级浪费，换取 best-rank 遥测） |

### 观察（不单独修，归 P2-10 harness 固化）

- **判分/锚点全 substring**：数字锚点无边界（`"148"` 可命中 `"1148"` 假阳性）；反向词形假阴性（B4"立方米/秒"判负，已知）。三套评测脚手架（run_qc / run_eval8 / run_eval_xls）copy-paste 且判分口径漂移——finding 3 的根源。
- `chunk_texts`/`score`/`run_retrieval`/`load_questions` 四件套在 3 个脚本间重复，每次改口径要改三处（这次就漏了一处）。

## 三、后期实施与优化方案

四条线，互相独立可穿插；**全线严格串行**（LLM 服务并发能力有限）。

### 线 0 · R 批代码卫生（≈1.5h，不动库内状态，建议即刻做）

| 项 | 内容 | 治愈 |
|---|---|---|
| R1 | TARGETS[洪水统计(1)] meta 同步 P1-5 真值（`flood_event=2018-8`）；rollback 恢复元数据改为"存档快照 ∪ config 推导值取新"或直接刷新存档 | F1 |
| R2 | `--rollback` 加 `--apply` 同款门（必须显式确认 + approved.flag） | F2 |
| R3 | `shell_import`：存档 print 护 None；rollback 查 PUT/POST 响应、run=FAIL 报错不算完成；dry-run 先跑 preflight（对齐 photo 流） | F6/F7/F12 |
| R4 | `photo_triage` 降采样去掉 width 守卫（保留质量地板，循环到 ≤limit 为止）；失败记录在 `--force` 外允许 N 天后重试或提供 `--retry-failed` | F8 |
| R5 | `extract_xls` 探测改 `python_calamine`+pandas（或删 local 死分支）；requirements 补 Pillow；photo rollout `sim→rank` 命名对齐；run_qc 注释修正 | F9/F10/F11/F13 |
| R6 | run_eval8 判分切 `[:10]` 或报告头如实标注"全返回集口径"（根治归 P2-10，先止血口径声明） | F3 止血 |

验收：`python3 -m pytest -q` 全绿；不触碰任何库内状态。

### 线 1 · 主线：F1 收口执行（卡 D1–D8 用户裁定）

R1/R2 完成后再动工（防批次执行中踩 rollback 漂移坑）。批次一（D1+D5，S 级 5 份+dup 并入）→ 二（D2+D7）→ 三（D3+D4）→ 四（D6），每批后 xls 12 题 + QC 六问 + 26 题回归。全批 13–19 人时 / 只批 S 级 6–9 人时（详见 `out/xls_fusion/TRIAGE_DECISION.md` §八）。

### 线 2 · 质量基建（与线 1 可穿插）

- **P2-10 评测 harness 固化**（SDD 任务 #21–#27 清单现成）：三套评测统一 CLI 与判分函数；显式口径声明（top10 vs 全返回集、×3 取多数）；B4 词形归一（m³/s ↔ 立方米/秒 ↔ 立米秒）+ 数字锚点边界匹配。这是 F3/F14 的根治。
- **P2-11 运维护栏**：①壳禁 parse 巡检断言（15+3 壳 run=UNSTART、chunk_count 对台账，可定时跑）；②重建陷阱三连收编——mapping.csv 重生成提示、run_setup 词表增量上传（按行对比而非"有文档就跳过"）、三类一次性维护操作（tag_feas 回填/doc_meta mapping/元数据 patch）脚本化为幂等工具；③key 轮换：5 个脚本明文 key 改 env 化 + git 历史处置决策、MiniMax key 用毕轮换。

### 线 3 · 外部依赖线（卡外部输入，随时可插）

- **照片壳导入**：3 资产融合文本+预检全就绪，卡 RAGFLOW_API_KEY 导出，到位后 0.5h。
- **档案方反馈**：错误①②③ + 存疑①②已成文（`docs/archive-feedback-2026-09-03.md`），交用户转达；核实结果回流 D4/D8/存疑④裁定。
- **P2-12 高清重扫**：剖面图 788.50/788.54 像素级重叠、库容表密集小字——需扫描资源，用户排期。

### 估时与建议顺序

| 顺序 | 内容 | 估时 | 依赖 |
|---|---|---|---|
| 1 | R 批代码卫生 | 1.5h | 无 |
| 2 | F1 批次一（D1+D5） | 6–9h | D 裁定 + R1/R2 |
| 3 | F1 批次二/三/四 | 8–12h | 批次一 |
| 4 | P2-10 harness | 4–6h | 可与线 1 穿插 |
| 5 | P2-11 护栏 | 2–3h | 可与线 1 穿插 |
| 6 | 照片壳导入 | 0.5h | key |

**验收红线**：pytest 全绿；xls 12 题 12/12、QC 六问 6/6、26 题不回退（引用数字须注明口径）；库内 2018-8 钻取集完整（2008-8 残留=0）。

## 附：证据索引

- 提交范围：`git log 9577709..759a1a9`（7 提交）
- code-review 通道：8 角度 finder → 4 verifier 全 CONFIRMED（本会话后台任务，结论已并入上表）
- 关键复核：run_eval8.py:30/58、rollout_xls_shell.py:41/91、shell_import.py:173/202/266-283、photo_triage.py:133、extract_xls.py:60、requirements.txt、run_setup.py:101、out/mapping.csv
- 上轮评审：`docs/review-vision-xls-2026-09-03.md`；F1 决策：`out/xls_fusion/TRIAGE_DECISION.md`
