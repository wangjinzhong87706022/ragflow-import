# SDD ledger — plan: docs/plans/2026-08-27-eval-suite-phase1.md

环境适配裁定（无 git 仓库）：
- Ruling: 本目录非 git 仓库 —— 无 commit/BASE；以任务前文件快照 + `diff -ruN`（或 git diff --no-index）生成评审包；sdd-workspace/task-brief/review-package 三脚本若因 git 失败则手工等价替代。代价：无提交历史可回溯，回滚靠快照目录。
- 工作区：/opt/wangjz/ragflow-import/.superpowers/sdd/2026-08-27-eval-suite-phase1/
- 后台全量导入（bi0f386gm）与本期并行运行中；只读 out/import_state.json 观察进度，不得干扰。

预检冲突扫描表（计划 v3，已含三处计划内修正：decide_pass 门控、T9 校验次序、T9 返回码语义）：
| 任务对 | 生产 vs 消费 | 结论/裁定 |
|---|---|---|
| T1→T2 | decide_pass 被 score_retrieval 以 kw_needed=0 调用（gold.keywords 空）| 初版 max(1,·) 钳位会让空关键词恒判 False，与 T2 测试冲突 → 已改计划为 min_keywords<=0 恒真。Ruling 关闭 |
| T2→T6/T7 | metrics 键 hit5/hit10/mrr_rank/numeric_exact/kw_hits/kw_needed/passed | 键名逐字核对一致 ✓ |
| T4→T10 | loader 默认补齐 use_kg/meta_filter/threshold.min_keywords | 16 行题库均有合法 rel（取自 mapping.csv 实测清单）✓ |
| T5→T9 | snapshot_datasets 用 config.DATASETS 模块属性；main 组装 record{run_id,tag,timestamp,suite,layer,snapshot,summary} | 一致 ✓ |
| T6→T8 | structured 结果键 filter/kg_gain/expected_docs/returned_docs 与 compare/report 消费一致 ✓；e2e=跳过语义三处对齐 ✓ |
| T9 内部 | 新 main 早于 _valid_rel_set 做 argv/tag 校验；失败路径 return 码而非 SystemExit → 两个旧测试按计划内改造说明重写 ✓ |
| T10→T11 | README 命令样例与 CLI 六形态一致 ✓ |

分派批次（模型选择依据：代码在简报里完整的用最低档）：
- D1 = Tasks 1–3 合并派发（纯追加+完整代码）→ haiku
- D2 = Task 4 → haiku；D3 = Task 5 → sonnet（mock 模块属性细节）；D4 = Task 6 → sonnet；
  D5 = Task 7 → haiku；D6 = Task 8 → haiku；D7 = Task 9 → sonnet（动旧代码与旧测试需判断）；D8 = Tasks 10–11 → haiku
- 最终全分支评审 → opus

状态日志：
- [setup] 预检完成，待派发 D1。

[import-watch] 16:00 观测：total51 = done18 / failed30(ds3 段) / wait_timeout2 / parse_requested1。

[import-watch] 17:30 根因修正：上段"间歇性失败"初判已被 RAGFlow 日志推翻。
实证链：ragflow_doc_meta_72fc3f62a01f11f19d7235ad4ea699d4 映射中 meta_fields.flood_event 被动态映射锁为 date
（strict_date_optional_time||epoch_millis，首批入库的 2021-10/2021-09 决定的）；此后所有一位月
（2020-8/2013-7/2019-7/2008-8）与非日期值（其他/历年统计）patch_document 必然 document_parsing_exception → code=102。
系统性而非偶发，重跑无法复活。year 已是 long 同类地雷。当前 done23/failed30/wait_timeout2。
裁定的修法（ES 运行时对象改动，须用户批准）：停导入(PID 4169802) → _search 备份26行 →
删索引按显式映射重建（全部 schema 键 keyword + 字符串→keyword 动态模板兜底）→ _bulk 回灌原文 → 续跑 --apply。
否决的替代方案：改客户端 flood_event 取值为日期兼容串 —— 破坏 requirements §7 过滤口径与评测 ST-* 题。
用户已看到解释，尚未批复方案选择；等待期间 SDD 循环暂停派发。

[D1 Tasks1-3] 实现完成（haiku，DONE_WITH_CONCERNS，0 删除行/112 插入行）→ 评审 **Approved**：
规格全项交付、四条全局约束逐条实测通过、终态回归 148 passed。
Ruling: 简报测试缺陷两处由实现者修正并经评审采信——(1) test:16 断言 `"," not in`→`"," in`
（原断言与简报自身实现契约矛盾、恒假）；(2) test:51 fixture 补入「百年一遇」使 kw_hits==2 可达。
实现代码保持与简报逐字一致。
开放项 Important I-1：numeric_hits 纯包含匹配无边界保护（"788" 可误中 "1788"）——spec §4.1 明文包含匹配，
本批不返工；裁定折入 Task 4 派发简报新增题库编写规范条目（gold.numbers 一律写全精度形态，禁可作子串的短数）。
Minor×4 备查不阻断（千分位去逗号宽泛/score_retrieval 冗余and/测试中段import/报告转写省略）。
状态：Tasks 1–3 complete。

Ruling(17:40 用户裁定)：ES 手术延后到 run_import 自然结束后执行，不中途停进程。
理由：failed 行均"已上传、仅元数据写入被拒"，无半成品状态；跑完后做
备份→删重建→回灌→再一轮 --apply（状态机只重驱非 done 行）即全部复活，且手术期间零并发写者更干净。
当前分布 done23/failed30/wait_timeout4/parse_requested1，共58行。
Ruling(I-1 落点)：采评审"或列入后续任务"分支——loader 不加校验；规则进 Task 11 README bullet
（计划文本已增补）+ Task 10 内容控制器自检。Task4 简报已附裁定说明。
SDD 恢复派发：D2 = Task 4 → haiku。
