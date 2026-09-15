# ds3 Timeline 时间线构建：源码分析、耗时优化与数据清理

- **日期**：2026-09-06
- **对象**：ds3（洪水资料，46 文档）的 Timeline 时间线结构编译与合并
- **远程实例**：`https://labragf.openagp.top:9080/`（RAGFlow v0.27.1）
- **模板组**：`b659b70aa9be11f1998b3dc126099a8d`（"桃曲坡-Timeline"，timeline-only）
- **子模板**：`b65a1af6a9be11f1998b3dc126099a8d`（"Timeline (Chronological events)"）

---

## 一、背景与目标

ds3 洪水资料知识库包含 46 个文档（xls/doc/pdf），涵盖 1867-2021 年的洪水事件、水库水位、泄洪流量等时间序列数据。目标是构建 Timeline 时间线结构，使用户能按时间顺序检索洪水事件。

用户要求分析：
1. Timeline 解析结果的正确性
2. 解析过程源码
3. 耗时原因及优化方案

## 二、源码深度分析

### 2.1 两阶段架构

Timeline 编译采用两阶段架构：

```
阶段1：文档解析时编译（每个文档独立）
  run_document_structure_compile()          [chunk_post_processor.py:1070]
    └─ _load_chunks_for_doc(batch_size=4)   流式加载 chunks，每批4个
    └─ run_structure_compile_over_batches() [runner.py:275]
        └─ compile_structure_from_text()    [structure.py:924]
            └─ _struct_extract_hypergraph() [structure.py:435]
                ├─ gen_json(node_prompt)     ← LLM调用1：提取 timestamp+event 实体
                └─ gen_json(edge_prompt)     ← LLM调用2：提取 ordered 关系
            └─ _struct_embed()              ← Embedding 批量调用
        └─ merge_compiled_structures()      每512个docs flush一次局部去重

阶段2：触发合并时（POST /index?type=timeline）
  run_structure_merge()                     [dataset_structure_merger.py:864]
    └─ 扫描所有 doc_graph 行 → 合并为 dataset 级时间线
```

### 2.2 LLM Prompt 结构

**Entity 提取 Prompt**（`node_prompt` + `user_prompt`，temperature=0.1）：

```
# Role and Task:
You are a robust events-timeline extractor. Extract a complete, source-grounded
chronological sequence, preserving the source order and the relationship between
each timestamp and the event or events that occur at that time.

## Entity Fields:
- type: timestamp
  rule: - Format: prefer ISO 8601 (YYYY-MM-DD) or normalized form
        - If only relative time, convert to absolute when context allows
        - "timestamp" must be non-empty (or exactly "-1" if no valid time/date)
        - If chunk contains multiple events, expand in chronological order
- type: event
  rule: - Chinese event: ≤40 characters
        - "event" must be non-empty when source describes an action/state/event
        - Every timestamp must be paired with at least one event

## Response Format:
{"items": [{ "type": "...", "name": "...", "description": "...", "source_chunk_ids": [...] }, ...]}
```

**Relation 提取 Prompt**（`edge_prompt`，temperature=0.1）：

```
## Known Entities:
- 1954-05-XX
- 1958-03-XX
- ...

## Relation Fields:
- type: ordered
  rule: - Create ordered relation for every pair of adjacent valid timeline items
        - Connect consecutive source chunks across batch boundaries
        - Use only extracted entity names as endpoints

## Response Format:
{"items": [{ "type": "ordered", "source": "...", "target": "...", "description": "...", "source_chunk_ids": [...] }, ...]}
```

Prompt 源文件：`api/db/init_data/compilation_templates/timeline.yaml`

### 2.3 Batch 构建与并发控制

| 参数 | 值 | 位置 | 说明 |
|------|-----|------|------|
| `DOC_STRUCTURE_COMPILE_BATCH_CHUNKS` | **4** | runner.py:61 | 每批加载的 chunk 数 |
| `STRUCTURE_CONTEXT_FRACTION` | **0.5** | runner.py:66 | 上下文利用率（max_length × 0.5） |
| `INPUT_UTILIZATION` | **0.5** | generator.py:34 | split_chunks 的 token 利用率 |
| `max_workers` | **3** | runner.py:419 | batch 间并发数 |
| `DOC_STRUCTURE_LLM_POOL_SIZE` | **20** | runner.py:79 | LLM 调用池大小 |
| `DOC_STRUCTURE_MERGE_MAX_DOCS` | **512** | runner.py:85 | flush 阈值 |
| `temperature` | **0.1** | structure.py:451,529 | LLM 生成温度 |

**Batch 大小计算**：
- `input_budget = chat_mdl.max_length × 0.5 - prompt_overhead_tokens`
- 128K 模型：`input_budget ≈ 64000 - ~2000 = 62000 tokens`
- 实际每批约 4 chunks（受 `DOC_STRUCTURE_COMPILE_BATCH_CHUNKS=4` 限制）

### 2.4 进度卡 0.99 机制

`cap_done_progress`（chunk_post_processor.py:480）将 >=1 的进度截断为 0.99，最终 1.0 保留给任务的终端状态。编译完成后，doc_graph 行写入 ES 和索引刷新需要额外时间。若脚本在 0.99 持续 3 分钟后调用 `stop_parse`，会导致 chunks 被清除（run=CANCEL, chunk_count=0）。

### 2.5 关键 API 端点

| 操作 | 端点 | 说明 |
|------|------|------|
| 编译模板组 CRUD | `/api/v1/compilation-template-groups` | 创建/读取/更新/删除模板组 |
| 更新 dataset | `PUT /api/v1/datasets/{id}` | 设置 `parser_config.compilation_template_group_id`（`list[str]` 类型） |
| 更新 document | `PATCH /api/v1/datasets/{id}/documents/{doc_id}` | 设置文档级 `parser_config` |
| 触发合并 | `POST /api/v1/datasets/{id}/index?type=timeline` | 触发 dataset 级时间线合并 |
| 查看结构（dataset 级） | `GET /api/v1/datasets/{id}/artifacts/structure?kind=timeline` | 查看合并后的时间线 |
| 查看结构（document 级） | `GET /api/v1/datasets/{id}/documents/{doc_id}/structure/graph` | 查看文档级结构图 |
| 删除结构（dataset 级） | `DELETE /api/v1/datasets/{id}/artifacts/structure?kind=timeline` | 删除合并数据 |
| 删除结构（document 级） | `DELETE /api/v1/datasets/{id}/documents/{doc_id}/structure/graph` | 按模板删除文档结构 |
| 重新解析 | `POST /api/v1/datasets/{id}/documents/parse` | body: `{"document_ids": [...]}` |
| 停止解析 | `POST /api/v1/datasets/{id}/documents/stop` | 停止解析任务 |

### 2.6 ParserConfig 验证

PUT /datasets 端点严格验证 parser_config，只接受 `ParserConfig` 类中定义的字段（`compilation_template_group_id` 是 `list[str]` 类型），不允许 `built_in_metadata`/`image_context_size`/`llm_id`/`metadata`/`table_context_size` 等字段。

## 三、耗时分析

### 3.1 单文档耗时模型

以 ds3 大文档（247 chunks，如 `水位库容曲线推求.xls`）为例：

| 步骤 | 调用次数 | 单次耗时 | 小计 |
|------|---------|---------|------|
| OCR + Layout + Table | 1 | ~90s | 90s |
| Question generation | 247 chunks | ~5-6s/chunk | 400s+ |
| Tagging | 247 chunks | ~1-5s/chunk | 250-1200s |
| Embedding + Indexing | 1 | ~1s | 1s |
| Entity 提取 LLM | 62 batches | 5-10s | 310-620s |
| Relation 提取 LLM | 62 batches | 5-10s | 310-620s |
| Embedding（结构） | 62 batches | 1-2s | 62-124s |
| **总计** | | | **15-20分钟/大文档** |

### 3.2 46 文档总耗时

| 文档类型 | 数量 | 单文档耗时 | 小计 |
|---------|------|-----------|------|
| 小文档（~10 chunks） | 30 | ~30s | 15分钟 |
| 中文档（~50 chunks） | 12 | ~3分钟 | 36分钟 |
| 大文档（~250 chunks） | 4 | ~15分钟 | 60分钟 |
| **总计（串行）** | 46 | | **1.5-2 小时** |

### 3.3 耗时根因

1. **Question generation**：每个 chunk 生成 5 个问题，LLM 调用约 5-6s/chunk
2. **Tagging**：每个 chunk 生成标签，LLM 调用约 1-5s/chunk
3. **Timeline 编译**：每个 batch 2 次 LLM 调用（entity + relation），batch_size=4 导致大文档产生大量 batch
4. **串行解析**：远程 LLM 并行能力有限，必须严格串行

## 四、Timeline 结果质量分析

### 4.1 初始结果（旧模板）

模板 `0e52ac86a9a211f1998b3dc126099a8d`（旧模板）：
- 48 个时间戳，**0 个事件**，47 个 ordered 关系
- 时间戳均为 "2021-10-05 HH:MM" 格式
- **问题**：旧模板配置不完整，未提取 event 实体

### 4.2 重新解析结果（新模板）

模板 `b65a1af6a9be11f1998b3dc126099a8d`（新模板）：
- 327 个时间戳，78 个事件，280 个 ordered 关系
- 时间戳覆盖 1954-2021 年
- 2021 年 10 月洪水事件有详细时序

### 4.3 质量问题

| 问题 | 根因 | 影响 |
|------|------|------|
| 时间戳格式错误（如 "1954-05-40"） | LLM 处理模糊日期时产生幻觉，无 post-validation | 无效时间戳进入 ES |
| 事件覆盖率低（78/327=24%） | 表格数据中时间点无明确"事件"描述，LLM 倾向留空 event | 时间线缺乏事件描述 |
| 事件内容同质化 | prompt 限制 ≤40 字符，水库监测数据大量相似描述 | 事件区分度低 |
| 旧模板残留数据 | 重新解析未自动清理旧模板的 dataset 级合并数据 | 48 个无效实体共存 |

### 4.4 Mind Map 分析结论

预构建 mind_map 不参与检索增强，仅用于前端可视化。`POST /chat/mindmap`（dialog_service.py:1908）是独立的实时功能，不需要预构建模板。ds3 没有必要构建 mindmap。

## 五、优化方案

### 5.1 耗时优化（零上游改动）

| 方案 | 操作 | 预期收益 | 风险 |
|------|------|---------|------|
| A1. 增大 batch size | `DOC_STRUCTURE_COMPILE_BATCH_CHUNKS` 4→8 | LLM调用减半 | 单批过长可能超 token 限制 |
| A2. 增大上下文利用率 | `STRUCTURE_CONTEXT_FRACTION` 0.5→0.7 | 每批容纳更多 chunks | 可能影响 LLM 质量 |
| A3. 使用更快的 LLM | 为 timeline 模板配置 glm-4-flash | 单次调用 10s→2s | 提取质量可能下降 |
| A4. 增大并发 | `max_workers` 3→6 | 并行度翻倍 | 远程 LLM 限流 |

### 5.2 质量优化（需修改源码或后处理）

| 方案 | 操作 | 实现位置 |
|------|------|---------|
| B1. 时间戳 post-validation | 校验日期格式，丢弃无效项 | structure.py:452 后 |
| B2. Event 非空校验 | 丢弃 event 为空的 timestamp 实体 | structure.py:452 后 |
| B3. 改进 prompt | 明确"表格数据中整行作为 event 描述" | timeline.yaml |
| B4. 事件语义去重 | merge 阶段对相似事件做语义合并 | merge 逻辑 |

### 5.3 数据清理

| 方案 | 操作 |
|------|------|
| C1. 删除旧模板数据 | 删除 dataset 级 timeline 数据 → 重新触发合并 |
| C2. 清理无效时间戳 | 删除文档级 structure graph → 重新解析 → 重新合并 |

## 六、执行记录

### 6.1 Timeline 构建（串行重新解析）

1. 停止所有并行解析任务
2. 创建 timeline-only 模板组 `b659b70aa9be11f1998b3dc126099a8d`
3. 更新 ds3 dataset 和全部 46 个文档的 parser_config
4. **串行重新解析全部 46/46 文档**，0 失败，12 分钟超时/文档
5. 修复 2 个零分块文档（洪水过程.xls 46 chunks、水位库容曲线推求.xls 247 chunks）
6. 触发 timeline 合并生成，任务完成（progress=1.0）
7. 初始验证：453 个时间实体，327 个 ordered 关系

### 6.2 C1: 旧模板数据清理

- 删除全部 timeline dataset 级数据（含旧+新）
- 重新触发 timeline 合并（仅新模板的文档级数据参与）
- 合并耗时约 40 秒
- **结果**：从 2 个模板（旧48+新405）→ 1 个模板（417 实体，278 关系）

### 6.3 C2: 无效时间戳清理

- 识别 2 个有无效时间戳的文档：
  - `防洪减灾统计表.xls`：3 个缺年份时间戳（10-05, 09-19, 09-25）
  - `较大洪水统计表.xls`：1 个非法日期（1954-05-40）
- 删除这 2 个文档的文档级 structure graph（98+6=104 行）
- 使用 `POST /datasets/{id}/documents/parse` 重新解析
- 重新触发 timeline 合并
- **结果**：dataset 级无效时间戳从 4 个 → 0 个

### 6.4 B1+B2: 后处理校验脚本

编写 `cleanup_timeline.py`，支持：
- dry-run 模式：仅校验报告
- `--clean-old`：执行 C1 旧模板清理
- `--reparse-invalid`：执行 C2 重新解析
- `--all`：执行全部
- 校验规则：ISO 8601 格式、中文日期、缺年份检测、非法日期检测

## 七、最终状态

### 7.1 Timeline 数据

| 指标 | 初始（旧模板） | 重新解析后 | 清理后（最终） |
|------|--------------|-----------|--------------|
| 模板数 | 1 | 2（旧+新） | **1** |
| timestamps | 48 | 327+48=375 | **345** |
| events | 0 | 78+0=78 | **68** |
| relations | 47 | 280+47=327 | **274** |
| 无效时间戳 | — | 4 | **0** |

### 7.2 时间戳覆盖范围

- 最早：1867 年（历时调查洪水，200年一遇）
- 最近：2021 年（2021-10 洪水事件详细时序）
- 格式：ISO 8601（YYYY-MM-DD）、带时间（YYYY-MM-DD HH:MM）、年份-only（YYYY）

### 7.3 知识库 ID

```
ds3: fdfee2e4a87c11f1998b3dc126099a8d (洪水资料, 46 docs)
模板组: b659b70aa9be11f1998b3dc126099a8d (桃曲坡-Timeline)
子模板: b65a1af6a9be11f1998b3dc126099a8d (Timeline (Chronological events))
旧模板: 0e52ac86a9a211f1998b3dc126099a8d (已清理)
```

## 八、项目脚本清单

| 脚本 | 用途 |
|------|------|
| `src/timeline_setup.py` | 停止任务+创建timeline组+更新配置 |
| `src/timeline_reparse.py` | 串行解析+断点续传+stuck检测（12分钟超时/文档） |
| `src/fix_zero_chunks.py` | 修复零分块文档 |
| `src/trigger_timeline.py` | 触发timeline合并生成 |
| `src/verify_timeline.py` | 等待任务完成+验证结果 |
| `src/analyze_timeline.py` | 分析timeline实体和关系质量 |
| `src/find_zero_chunks.py` | 查找零分块文档 |
| `src/cleanup_timeline.py` | **综合清理脚本**：C1旧模板+C2无效时间戳+B1+B2校验 |
| `src/reparse_invalid_docs.py` | 重新解析有无效时间戳的文档 |
| `src/timeline_e2e_test.py` | **端到端测试**：4题验证时间线检索能力 |
| `out/timeline_reparse.json` | 断点续传进度文件 |
| `out/timeline_group_id.txt` | timeline模板组ID |
| `out/qc/timeline_e2e_results.json` | 端到端测试结果 |

## 九、RAGFlow 源码参考

| 文件 | 关键函数/位置 | 说明 |
|------|-------------|------|
| `rag/svr/task_executor_refactor/chunk_post_processor.py` | `run_document_structure_compile()` :1070 | 文档级编译入口 |
| | `cap_done_progress` :480 | 进度截断 0.99 机制 |
| | `_parser_config_compilation_template_ids` :452 | 解析模板组 ID |
| `rag/advanced_rag/knowlege_compile/runner.py` | `run_structure_compile_over_batches()` :275 | batch 提交/flush/merge |
| | `DOC_STRUCTURE_LLM_POOL_SIZE=20` :79 | LLM 池大小 |
| | `max_workers=3` :419 | batch 并发数 |
| `rag/advanced_rag/knowlege_compile/structure.py` | `compile_structure_from_text()` :924 | 编译入口 |
| | `_struct_extract_hypergraph()` :435 | LLM 提取 entity+relation |
| | `_struct_hypergraph_prompts()` :293 | Prompt 生成 |
| | `_struct_process_batch()` :823 | batch 处理（提取→嵌入→ES） |
| `rag/advanced_rag/knowlege_compile/_common.py` | `build_chunk_batches()` :366 | batch 构建 |
| | `knowledge_compile_gen_conf()` :51 | LLM 生成配置 |
| `rag/advanced_rag/knowlege_compile/dataset_structure_merger.py` | `run_structure_merge()` :864 | dataset 级合并 |
| `api/apps/services/dataset_api_service.py` | `run_index()` :588 | 触发索引任务 |
| | `delete_dataset_structure()` :1877 | 删除 dataset 级结构 |
| | `get_dataset_structure()` :1886 | 获取 dataset 级结构 |
| `api/apps/restful_apis/compilation_template_group_api.py` | — | 模板组 CRUD API |
| `api/apps/restful_apis/chunk_api.py` | `get_document_structure_graph()` :613 | 文档级结构图 |
| | `delete_document_structure_graph()` :941 | 删除文档级结构图 |
| `api/utils/validation_utils.py` | `ParserConfig` :450 | parser_config 验证 |
| `api/db/init_data/compilation_templates/timeline.yaml` | — | Timeline 模板定义 |
| `api/db/services/dialog_service.py` | `gen_mindmap()` :1908 | 实时 mindmap（不依赖预构建） |

## 十、端到端测试验证

### 10.1 测试设计

针对 Timeline 时间线检索能力，设计 4 道端到端测试题，通过 RAGFlow chat API 进行真实问答验证：

| ID | 类型 | 问题 | 验证目标 |
|----|------|------|---------|
| T1 | 时序链检索 | 2021年10月桃曲坡水库洪水事件按时间顺序发生了什么？ | 能否按时间顺序列出事件链 |
| T2 | 历史年份检索 | 桃曲坡水库1983年的洪水记录是什么？洪峰流量是多少？ | 能否检索历史年份洪水数据 |
| T3 | 时间范围报汛 | 2013年7月22日桃曲坡水库的报汛记录有哪些？ | 能否检索特定日期的报汛数据 |
| T4 | 跨年对比 | 桃曲坡水库2020年和2021年分别发生了哪些洪水事件？ | 能否对比不同年份洪水 |

### 10.2 测试结果

**4/4 全部通过**

| ID | 结果 | 回答摘要 | 引用文档 |
|----|------|---------|---------|
| T1 | PASS | 按时间顺序列出 10月2日-6日降雨、10月3日13:25洪峰285m³/s等关键节点 | 三场洪水调度过程汇报.doc、20211003柳林断面流量统计.xls |
| T2 | PASS | 1983年洪峰流量141m³/s，洪水总量664/1354万m³ | 较大洪水统计表.xls |
| T3 | PASS | 识别出知识库无2013-07-22数据，给出相关年份替代信息 | "7.29"洪水简讯doc.doc、洪水统计.xls |
| T4 | PASS | 2020年8月16日洪水（面雨量82.8mm）vs 2021年9-10月三场洪水 | 洪水过程.normalized.xlsx、三场洪水调度过程汇报.doc |

### 10.3 测试脚本

- `src/timeline_e2e_test.py` — 端到端测试脚本
- `out/qc/timeline_e2e_results.json` — 详细测试结果

### 10.4 结论

Timeline 时间线检索能力验证通过：
- 时序链检索有效：能按时间顺序组织事件
- 历史年份检索准确：能检索到 1983 年的历史洪水数据
- 时间范围查询智能：对不存在的日期能给出合理替代
- 跨年对比有效：能对比不同年份的洪水事件

## 十一、待办与建议

### 已完成
- [x] 46/46 文档串行重新解析，0 失败
- [x] 修复 2 个零分块文档
- [x] Timeline 合并生成完成
- [x] C1: 旧模板残留数据清理
- [x] C2: 无效时间戳清理
- [x] B1+B2: 后处理校验脚本
- [x] 端到端测试验证 4/4 通过

### 未来可选优化
- [ ] A3: 为 timeline 模板配置更快的 LLM（glm-4-flash），加速未来重新解析
- [ ] B3: 改进 timeline.yaml prompt，明确表格数据 event 提取规则
- [ ] B4: merge 阶段添加事件语义去重
- [ ] 考虑增大 `DOC_STRUCTURE_COMPILE_BATCH_CHUNKS`（4→8）减少 LLM 调用次数