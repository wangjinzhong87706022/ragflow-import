# 设计文档：桃曲坡知识库评测集 / 评测标准 / 评测任务（A+ 轻量混合形态）

- **日期**：2026-08-27
- **状态**：已与需求方逐节确认（五项决策点 + 两个设计批次均获批准）
- **定位**：全量导入完成后，为 5+1 知识库建立可重复执行的评测体系。既支撑当前达标验收（`docs/requirements.md` §7），又作为后续调参迭代（GraphRAG 提示词、切块参数、嵌入模型更换）的回归对比基座。
- 上位文档：`requirements.md`（验收口径）、`specs/`（导入设计 v2）；本方案承接 `run_qc.py` 六问 QC 的升级换代。

---

## 1. 已锁定的决策点

| # | 决策 | 结论 |
|---|---|---|
| 1 | 评测用途 | **两者兼具**：验收达标证明 + 长期回归基座 |
| 2 | 金标来源 | **人工核芯 + LLM 批量**：~30 验收题人工精写；LLM 读旧提取链批量出候选、人工抽验 ≥20% 后转正 |
| 3 | 任务范围 | **全景三层分两期**：retrieval / structured 先行，e2e 二期 |
| 4 | 判分标准 | **确定性为主 + LLM 参考**：能机判的全部机判且设门槛；Qwen 参考分明确标注"非判据" |
| 5 | 工程形态 | **A+ 轻量混合**：保持 run_qc.py 单脚本骨架精神；题库外置 jsonl、run 历史追加、--compare 两两对比 |

被否决的备选：完整独立 eval 子系统包（工程量不成比例）；外部框架 Ragas 等（判分黑盒 + 面向 GPT/英文语料，与本域和约束冲突）。

## 2. 关键设计原则

1. **零侵扰**：评测只读检索面与结果数据；不临时修改任何 dataset 的持久配置（GraphRAG 对照用请求级 `use_kg` 开关实现——接口已实证支持）。原设想"摘挂 tag_kb_ids 做标签对照"因需动生产 parser_config 且有忘恢复风险而**放弃开关对照**，降级为弱信号记分（见 §4.2）。
   **唯一例外（二期 e2e）**：chat 评测需要一个助手对象——按固定名（如 `tq-eval`）幂等创建并跨 run 复用（已存在即不重建），绝不触碰任何 dataset 配置；此例外在 e2e 落地时于 runner 日志中显式声明。
2. **避免循环论证**：LLM 批量出题的语料源是**上一轮提取链** `pdf_text_analysis/` 的派生 txt，与本次导入的切块产物来自不同加工链路——检索命中不是"出题源=答题源"的自证。
3. **判分可复现**：数值精确匹配 / 关键词锚点 / 引用锚定 / 集合比对均为纯函数，同一输入必得同一分数；LLM 只产参考分且永不做通过与否的裁决。
4. **配置漂移告警**：每次实跑前抓取 datasets 配置快照；对比两轮时快照不一致先警告"涨跌可能来自配置而非调参"。

## 3. 试题库

### 3.1 Case 模式（out/eval/cases.jsonl，每行一题）

```json
{
  "id": "AC-MH-01",
  "tier": "acceptance",            // acceptance | regression
  "layer": "structured",           // retrieval | structured | e2e（二期）
  "question": "溢洪道的设计泄量是多少？",
  "datasets": ["ds1"],             // 检索范围（ds 键列表）
  "use_kg": true,                  // 本 case 判据跑法（缺省 false）；ds1/ds3 的 structured 题额外自动补反向变体记增益
  "meta_filter": null,             // 结构化题填，如 {"flood_event":"2013-7"}
  "gold": {
    "numbers": [1454],             // 数值断言：top-k chunk 文本中精确匹配（归一后）
    "keywords": ["溢洪道", "百年一遇"],
    "answer": "1454 m³/s",         // 参考答案文本（仅 LLM 参考分使用）
    "source_rel": ["01-核心文档-四案/02-调度规程.pdf"] // 期望出处文档（必须是 mapping.csv 里的 rel）
  },
  "threshold": {"min_keywords": 2}, // 达到即该 case pass；缺省 min_keywords=1
  "note": "依据 requirements.md §7 条目1"
}
```

字段约束：
- `id` 全局唯一；前缀 `AC-`=验收档，`RG-`=回归档。
- `datasets` 引用 `setup_state.json` 的 ds 键。
- `use_kg`（缺省 false）声明该 case 的**判据跑法**；当 datasets ∩ {ds1, ds3} 非空且 layer=structured 时，runner 额外跑一次反向开关变体，仅用于记录 GraphRAG 增益，不计入判据。
- `gold.source_rel` 必须能在 mapping.csv 中找到（loader 校验，杜绝旧提取链路径混入）。
- `meta_filter` 仅 layer=structured 允许非空。

### 3.2 规模配比（可评审时调整）

| 档 | 数量 | 来源 | 质量门 |
|---|---|---|---|
| acceptance | ~30 题 | 人工精写：§7 四个多跳问题展开 + 每 dataset 抽 3~5 核心事实题，锚定 VLM 已批基准值（1454 / 2218 / 788.5 等） | 写入 cases.jsonl 即生效 |
| regression | ~150 题 | LLM 读 `pdf_text_analysis/` 批量出候选 → **人工抽验 ≥20%，未过抽验批次整体不入库** | 抽验记录留痕于 candidates/review_status.md |

受众配比（regression 档）：值班问答 40% / 工程统计 30% / 综合研判 30%（对应 requirements §1 三类受众）。

## 4. 判分标准（确定性优先）

### 4.1 retrieval 层指标（门槛）

| 指标 | 定义 |
|---|---|
| hit@k（k=5,10） | gold.numbers ∪ keywords 任一出现在 top-k chunk 文本（数值归一规则：去千分位逗号、全半角统一、大小写折叠后做包含匹配，不做单位换算） |
| MRR | 对所有 retrieval 层 case 取均值：第一个含 gold 锚点 chunk 的 1/rank（无命中记 0） |
| source_hit | top-10 中出现 gold.source_rel 对应文档：runner 启动时对各 ds 各取一次 `list_documents` 建 doc_id→name 映射，以 chunk 的 doc_id 反查文档名比对 |
| numeric_exact | gold.numbers 在 top-k 精确命中数 ≥ 1 |

case 通过条件（运算顺序显式化）：
```
pass = (numeric_exact OR hit@10) AND keyword_ok
其中：
- numeric_exact 仅当 gold.numbers 非空时参与判定（空集视为 false 且不适用）
- keyword_ok = 关键词命中数 ≥ threshold.min_keywords；
  gold.keywords 为空集时 keyword_ok 恒为 true（数值锚点独立承载该 case）
- threshold.min_keywords 缺省 = 1
```

### 4.2 structured 层（门槛）

- **meta_filter 题**：返回集合与期望子集逐一比对（doc 名单），精确一致才 pass。
- **GraphRAG 增益**：同题 `use_kg=true/false` 双跑（请求级参数），增益 = hit@5(kg) − hit@5(无kg)。仅记录与展示；验收门槛沿用 AC 类 multi_hop 题 hit 判定本身。
- **标签信号（弱信号，无门槛）**：命中块的 `important_keywords` 与预期标签词交集计数。

### 4.3 e2e 层（二期）

- **引用锚定率（门槛）**：**doc 级判定**——回答所引文档 ∈ gold.source_rel 对应文档集合；页级校验（引用页码落在该文档真实页内）作为 best-effort 记录项，不设门槛（页数信息 API 不保证暴露）。
- **参考分（不设门槛）**：忠实度 / 答案相关性由本地 Qwen 打分，报告单独分区并标注"Qwen Q4 参考值，幻觉风险隔离，非判据"。

### 4.4 汇总口径

- suite 总分 = 各层通过率汇总；报告按 tier × layer 分组列表。
- 回归对比以 **per-case diff** 为主视图（pass→fail 红、fail→pass 绿、新增/废弃），总分涨跌只是副视角。

## 5. 执行器（run_qc.py 改造）

### 5.1 CLI

```bash
python3 run_qc.py                          # dry-run：列出将执行的 case 清单
python3 run_qc.py --apply --tag v0-baseline   # 实跑；无 tag 则拒绝 apply（防误跑污染 runs）
python3 run_qc.py --suite acceptance       # 过滤：acceptance | regression | all（缺省 all）
python3 run_qc.py --layer retrieval        # 过滤：retrieval | structured | e2e | all（缺省 all）
python3 run_qc.py --compare last           # 不重跑，diff 最近两次运行记录
python3 run_qc.py --compare <runA> <runB>  # 指定两个 run_id 做 diff
```

原 QUESTIONS 六问整体迁移进首版 cases.jsonl（tier=acceptance），脚本不再内置题目。

### 5.2 运行流程

1. 读 `setup_state.json` 映射 ds→id；读 cases.jsonl（loader 校验 schema，见 §3.1）。
2. **apply 前**拉取 `list_datasets()` 全量快照（chunk_method/parser_config/graphrag/raptor/embedding）写入运行记录。
3. 按 suite/layer 过滤 case 逐条执行：构造 search_datasets 请求（use_kg、meta_filter）→ 取回 chunks → 判分函数打分。
4. 每轮 --apply 向 `out/eval/runs.jsonl` 追加一行：`{run_id, tag, timestamp, suite, snapshot, summary}`；`run_id` 取 `YYYYMMDD-HHMMSS` 时间戳格式。per-case 明细写 `runs/<run_id>/results.jsonl` + 人读 `report.md`。
5. `--compare` 读 runs.jsonl 最近两条（或指定 run_id），输出 delta 表；快照有 diff 时报告顶部红字提示配置漂移。

### 5.3 目录布局

```
src/gen_cases.py                # （二期）出题工具：pdf_text_analysis 文本 → LLM → 候选 jsonl
out/eval/
  cases.jsonl                   # 正式题库（过人工门的唯一入口）
  candidates/batch_YYYYMMDD.jsonl  # LLM 候选批次
  candidates/review_status.md   # 抽验记录（≥20% 门）
  runs.jsonl                    # 运行历史（追加式）
  runs/<run_id>/results.jsonl
  runs/<run_id>/report.md
```

遵循项目硬约束：全部运行产物入 out/，源码在 src/，凭据仅环境变量注入。

## 6. 分期计划

| 期 | 内容 | 就绪价值节点 |
|---|---|---|
| 一期 | cases.jsonl 骨架 + ~30 验收题入库 + retrieval/structured 两层 + 快照/runs/--compare/报告 + 全套离线测试 | 全量导入完成后立即可出正式 §7 验收证据 |
| 二期 | gen_cases.py 出题管线（含抽验工作流）+ chat 端到端接入（RAGFlow 对话 API：建助手/会话、带引用回答）+ 引用锚定率 + Qwen 参考分区 + e2e 层 case | 回归体系完整成形 |

## 7. 测试策略（全离线）

延续 `_isolate_out` 隔离模式，mock 掉 RAGFlowClient：

- loader：schema 校验每行参数化用例；source_rel 不在 mapping.csv 即报错。
- 判分函数：逐指标单测（hit@k 归一化边界、MRR 秩序、集合比对、阈值 pass/fail 边界）。
- 快照与对比：runs.jsonl 追加幂等性；--compare 对齐缺失 case / 新增 case / 快照 diff 警告三场景。
- dry-run 默认路径不动网络。

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 本地 Qwen 判分幻觉 | 参考分永不作为判据；报告强制分离展示 |
| 出题循环论证 | 出题源锁定 pdf_text_analysis 旧链；人工 ≥20% 抽验门 |
| 配置漂移污染对比 | 每次 apply 强制抓快照 + compare 漂移红字告警 |
| 共享网关弱并发拖慢跑批 | 复用导入侧串行经验：runner 内串行执行 per-case |
| acceptance 集体量小导致的偶发性 | 指标同时报 hit@5/@10/MRR 多口径；threshold 可按 case 校准 |
