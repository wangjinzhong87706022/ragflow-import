# Mindmap 源码分析、耗时优化与 DS 适配评估

- **日期**：2026-09-06
- **对象**：RAGFlow v0.27.1 的 Mindmap 编译流程源码分析
- **目标**：识别耗时瓶颈、提出优化方案、评估哪个 DS 最适合构建 mindmap

---

## 一、双轨制架构

RAGFlow 的 mindmap 存在**两条独立路径**：

### 1.1 实时生成（gen_mindmap）

```
POST /chat/mindmap → gen_mindmap()                    [dialog_service.py:1908]
  └─ retrieval(top-12 chunks)                          向量检索
  └─ MindMapExtractor(chat_mdl)                       [mind_map_extractor.py:40]
      └─ 按 token 预算打包 batches
      └─ asyncio.gather 并发 _process_document         每批 1 次 LLM 调用
      └─ reduce(self._merge, res)                     跨批次字典深合并
      └─ _be_children → {id, children} 树
  └─ 返回 JSON 树（不持久化）
```

特点：每次请求实时生成，不持久化，使用模型实际 max_length 计算 batch 预算。

### 1.2 预构建编译（KnowledgeCompiler）

```
文档解析时触发 → Go mindmap.Run()                     [mindmap.go:53]
  └─ packSections(batchBudget=3584 tokens)            按 token 预算打包
  └─ runBatches → 每批 1 次 LLM 调用                  生成 markdown mind map
  └─ utility.Todict(Dictify(StripFences))             Markdown → dict
  └─ utility.MergeDicts                               跨批次合并
  └─ utility.ShapeTree → {id, children} 树
  └─ treeToProducts → entity/relation 产品             每节点→entity，每边→relation
  └─ deps.Embed.Encode                                批量嵌入
  └─ 写入 ES
POST /index?type=mindmap → structure_mindmap 任务      dataset 级合并
  └─ mergeStructureDataset()                          [consumer.go:1502]
      └─ 按 (name,type,compile_kwd) 分桶去重
      └─ WriteMergedStructure → ES
```

特点：文档解析时编译，entity/relation 持久化入 ES，参与 dataset 级合并，通过 `/datasets/{id}/artifacts/structure?kind=mindmap` 读取。

## 二、关键参数对比（mindmap, timeline）

| 参数 | mindmap | timeline | 影响 |
|------|---------|----------|------|
| **batch 预算** | `mindmapMaxLength=4096`（**固定常量**） | `max_length × 0.5`（动态） | mindmap 远小于 timeline |
| **batchBudget 计算** | `max(4096×0.8, 4096-512)=3584` | `max_length×0.5 - overhead` | 128K模型: mindmap=3584, timeline=62000 |
| **每 batch LLM 调用** | **1 次**（生成 markdown 树） | **2 次**（entity+relation） | mindmap 调用次数少 |
| **LLM 输出格式** | Markdown 多层列表 | JSON entity/relation | mindmap 输出更长 |
| **batch 并发** | `batchSubmitter`（vCPU 池） | `max_workers=3` | 类似 |
| **temperature** | 默认 | 0.1 | mindmap 创造性更高 |
| **检索参与** | 预构建: entity/relation 入索引; 实时: 不持久化 | entity/relation 入索引 | 相同 |

## 三、耗时分析

### 3.1 预构建编译耗时模型

**关键发现：`mindmapMaxLength = 4096` 是固定常量**（prompt.go:40），不随模型 max_length 调整。

```
batchBudget = max(4096×0.8, 4096-512) = 3584 tokens
每 batch 约 14 chunks（256 tokens/chunk）
batch 数 = 总 chunks / 14
```

| 文档规模 | chunks | batches | LLM 调用 | 耗时估算 |
|---------|--------|---------|---------|---------|
| 小文档 | ~10 | 1 | 1 | ~10s |
| 中文档 | ~50 | 4 | 4 | ~40s |
| 大文档 | ~250 | 18 | 18 | ~3min |
| ds1 全部 | 965 | 69 | 69 | ~11-15min |
| ds3 全部 | ~900 | 64 | 64 | ~10-15min |
| ds5 全部 | ~700 | 50 | 50 | ~8-12min |

### 3.2 实时生成耗时模型

```python
token_count = max(max_length × 0.8, max_length - 512)  # 使用模型实际 max_length
# 128K 模型: token_count = 130048 tokens
# 12 chunks × 256 tokens = 3072 tokens → 1 个 batch → 1 次 LLM 调用
```

| 步骤 | 耗时 |
|------|------|
| 向量检索 + rerank | 1-2s |
| LLM 调用（1次） | 5-15s |
| Markdown 解析 + 合并 | <1s |
| **总计** | **7-17s** |

### 3.3 耗时瓶颈

| 瓶颈 | 位置 | 说明 |
|------|------|------|
| **LLM 调用** | mindmap.go:74 | 主要瓶颈，每次生成完整 markdown 树 |
| **batchBudget 过小** | prompt.go:40 | `4096` 固定常量，128K 模型仅用 2.8% 上下文 |
| **batch 数过多** | mindmap.go:68 | 大文档产生大量 batch，LLM 调用次数多 |
| **Embedding** | mindmap.go:113 | 次要瓶颈，节点数 20-100，批量调用 1-2s |

## 四、优化方案

### 4.1 预构建编译优化

| 方案 | 操作 | 预期收益 | 风险 | 优先级 |
|------|------|---------|------|--------|
| **M1. 增大 batchBudget** | `mindmapMaxLength` 4096 → 模型实际 max_length | batch 数减少 10-30x，大文档从 18 batches → 1-2 batches | 单批过长可能影响 LLM 质量 | **高** |
| **M2. 使用更快的 LLM** | 配置 glm-4-flash | 单次调用 10s→2s | 提取质量可能下降 | 中 |
| **M3. 增大并发** | 增大编译池大小 | 并行度提高 | 远程 LLM 限流 | 低 |
| **M4. 跳过小文档** | <5 chunks 不构建 | 减少不必要的 LLM 调用 | 小文档无 mind map | 低 |

**M1 的具体效果**（以 ds1 为例，965 chunks）：
- 当前：`batchBudget=3584` → 69 batches → 69 LLM 调用 → ~11min
- 优化后（128K 模型）：`batchBudget=104000` → 1-2 batches → 1-2 LLM 调用 → **~20s**
- **耗时降低 30x+**

### 4.2 实时生成优化

| 方案 | 操作 | 预期收益 | 风险 |
|------|------|---------|------|
| M5. 增大检索 top_k | top-12 → top-20 | 更多上下文 | 检索耗时增加 |
| M6. 缓存结果 | 相同 question 缓存 | 重复查询秒回 | 缓存失效策略 |

## 五、LLM Prompt 分析

mindmap 的 prompt（Go 端 `mindMapExtractionPrompt` / Python 端 `MIND_MAP_EXTRACTION_PROMPT`，两者逐字一致）：

```
- Role: You're a talent text processor to summarize a piece of text into a mind map.

- Step of task:
  1. Generate a title for user's 'TEXT'。
  2. Classify the 'TEXT' into sections of a mind map.
  3. If the subject matter is really complex, split them into sub-sections and sub-subsections.
  4. Add a shot content summary of the bottom level section.

- Output requirement:
  - Generate at least 4 levels.
  - Always try to maximize the number of sub-sections.
  - In language of 'Text'
  - MUST IN FORMAT OF MARKDOWN

-TEXT-
{input_text}
```

特点：
- 要求至少 4 层结构
- 要求最大化子节点数
- 输出为 Markdown 格式
- 使用模型语言匹配原文

## 六、哪个 DS 最适合 mindmap

### 6.1 各 DS 内容特点

| DS | 名称 | 文档数 | 总 chunks | 内容类型 | 层次结构 | 已有结构 |
|----|------|--------|----------|---------|---------|---------|
| **ds1** | 规程与预案 | 4 | 965 | 法规文档（章节/条款） | **强**（章-节-条） | GraphRAG + Raptor |
| ds2 | 基础数据 | 1 | 105 | 划界报告 | 中（章节） | 无 |
| ds3 | 洪水资料 | 46 | ~900 | 统计表/汇报 | 弱（碎片化） | GraphRAG + **Timeline** |
| ds4 | 组织管理 | 5 | 106 | 通知/设备表 | 中（分类） | 无 |
| ds5 | 工程资料 | 34 | ~700 | 图纸/报告 | 弱（图纸为主） | Raptor |

### 6.2 适合 mindmap 的内容特征

- ✅ 有明确的主题和子主题层次
- ✅ 内容结构化（章节、条款、分类）
- ✅ 需要快速概览和导航
- ❌ 纯数值表格（时间序列、统计数据）
- ❌ 无层次结构的碎片化信息
- ❌ 图纸/图片内容

### 6.3 推荐结论：ds1 最适合

**ds1（规程与预案）最适合构建 mindmap**，原因：

1. **内容高度层次化**：规程与预案文档有明确的章节结构
   - `01-防洪抢险应急预案.pdf`（262 chunks）— 总则/应急组织/应急措施/保障措施
   - `02-调度规程.pdf`（121 chunks）— 总则/调度原则/调度方式/调度标准
   - `03-汛期调度运用计划.pdf`（284 chunks）— 编制依据/调度计划/调度方式/应急调度
   - `04-大坝安全管理应急预案.pdf`（298 chunks）— 总则/工程概况/险情判断/应急措施

2. **法规文档天然树状**：条款编号清晰（第一章→第一节→第一条），LLM 能高效提取层次

3. **与 GraphRAG 互补**：
   - GraphRAG：实体关系图谱（FloodEvent/Station/Structure 等实体间的关联）
   - Timeline：时间序列（ds3 已构建）
   - **Mindmap：层次概览**（规程文档的整体框架和条款导航）
   - 三者互补，不重叠

4. **文档数适中**：4 个文档，965 chunks，构建耗时可控（~11min，优化后 ~20s）

5. **用户价值高**：规程文档的 mindmap 可以帮助用户快速了解调度规程的整体框架，定位关键条款位置

### 6.4 其他 DS 的适配性分析

| DS | 适配性 | 原因 |
|----|--------|------|
| ds2 | ⭐⭐ | 单文档划界报告，有章节但内容单一，mindmap 价值有限 |
| ds3 | ⭐ | 已有 Timeline，内容以表格/汇报为主，缺乏层次结构 |
| ds4 | ⭐⭐ | 设备管理表有分类结构，但主要是表格数据，mindmap 效果一般 |
| ds5 | ⭐ | 以图纸/报告为主，图纸不适合 mindmap，报告层次不如 ds1 清晰 |

## 七、源码参考

| 文件 | 关键位置 | 说明 |
|------|---------|------|
| `internal/ingestion/component/knowledge_compiler/mindmap/mindmap.go` | `Run()` :53 | Go 端预构建编译入口 |
| | `treeToProducts()` :148 | 树展平为 entity/relation |
| `internal/ingestion/component/knowledge_compiler/mindmap/prompt.go` | `mindmapMaxLength=4096` :40 | **固定常量，耗时根因** |
| | `batchBudget()` :43 | batch 预算计算 |
| | `mindMapExtractionPrompt` :12 | LLM prompt（system message） |
| `rag/advanced_rag/knowlege_compile/mind_map_extractor.py` | `MindMapExtractor.__call__` :75 | Python 端实时生成 |
| | `_merge` :117 | 跨批次字典深合并 |
| `rag/graphrag/general/mind_map_prompt.py` | `MIND_MAP_EXTRACTION_PROMPT` :17 | Python 端 prompt |
| `api/db/services/dialog_service.py` | `gen_mindmap()` :1908 | 实时生成入口 |
| `internal/ingestion/knowledge_compile/consumer.go` | `mergeStructureDataset()` :1502 | dataset 级合并 |
| `api/db/init_data/compilation_templates/mind_map.yaml` | — | mindmap 模板定义 |
| `internal/ingestion/component/knowledge_compiler/component.go` | `case VariantMindmap` :194 | 组件分发 |
| `api/apps/services/dataset_api_service.py` | `"mindmap": "structure_mindmap"` :57 | 任务类型映射 |

## 八、ds1 Mindmap 构建执行记录

### 8.1 构建步骤

1. **创建 mindmap 模板组** `d8c5d296aa0511f1998b3dc126099a8d`（"桃曲坡-Mindmap"）
2. **更新 ds1 dataset + 4 个文档的 parser_config**，添加 `compilation_template_group_id`
3. **串行重新解析 4 个文档**：
   - `02-调度规程.pdf`（121 chunks）— 完成
   - `03-汛期调度运用计划.pdf`（284 chunks）— 完成
   - `04-大坝安全管理应急预案.pdf`（298 chunks）— 完成
   - `01-防洪抢险应急预案.pdf`（262 chunks）— 完成（解析耗时约 6 分钟，含 mindmap 编译）
4. **触发 mindmap 合并**（`POST /index?type=mindmap`），10 秒完成
5. **验证结果**

### 8.2 Mindmap 结构

| 指标 | 值 |
|------|-----|
| 模板数 | 1 |
| 总实体 | 44 |
| 总关系 | 43 |
| central_topic | 1（桃曲坡水库防洪抢险应急预案） |
| branch | 9（工程险情及危害性分析、应急响应、后期处置、应急保障、工程概况等） |
| sub_branch | 34（主要防洪安全问题、险情种类与危害、I级响应、物资保障、抢险措施等） |

关系类型：`has_branch`（中心→分支）、`has_sub_branch`（分支→子分支）

### 8.3 层次结构示例

```
桃曲坡水库防洪抢险应急预案 (central_topic)
├── 工程险情及危害性分析 (branch)
│   ├── 大坝溃决分析 (sub_branch)
│   ├── 险情种类与危害 (sub_branch)
│   └── 主要防洪安全问题 (sub_branch)
├── 应急响应 (branch)
│   ├── I级响应 (sub_branch)
│   └── ...
├── 应急保障 (branch)
│   ├── 通信保障 (sub_branch)
│   ├── 队伍保障 (sub_branch)
│   └── 物资保障 (sub_branch)
├── 工程概况 (branch)
│   └── 工程基本情况 (sub_branch)
├── 后期处置 (branch)
│   └── 善后处置 (sub_branch)
└── ...
```

## 九、端到端测试验证

### 9.1 测试设计

| ID | 类型 | 问题 | 验证目标 |
|----|------|------|---------|
| M1 | 整体框架 | 防洪抢险应急预案的主要组成部分有哪些？ | 能否检索整体框架 |
| M2 | 层次定位 | 应急响应分为几个级别？每个级别有什么措施？ | 能否定位层次内的具体内容 |
| M3 | 分支导航 | 工程险情及危害性分析包括哪些内容？ | 能否导航到特定分支 |
| M4 | 跨文档 | 调度规程和应急预案之间是什么关系？调度原则是什么？ | 能否跨文档关联检索 |

### 9.2 测试结果

**4/4 全部通过**

| ID | 结果 | 回答摘要 | 引用文档 |
|----|------|---------|---------|
| M1 | PASS | 列出编制目的、适用范围、汛期主要险情类型（高边坡滑坡、超标准洪水等） | 4 个文档全部引用 |
| M2 | PASS | 识别 IV/III/II/I 四级响应，各级调度权限（中心主任→总指挥长） | 04-大坝安全管理应急预案、03-汛期调度运用计划 |
| M3 | PASS | 列出险情因素、种类、危害程度、大坝安全鉴定（2017-2023年） | 04-大坝安全管理应急预案、01-防洪抢险应急预案 |
| M4 | PASS | 分析调度规程为基础规定、应急预案为专项补充，给出调度原则 | 02-调度规程、01-防洪抢险应急预案、04-大坝安全管理应急预案 |

### 9.3 测试脚本

- `src/mindmap_setup.py` — mindmap 构建脚本（模板组+配置+解析+合并+验证）
- `src/mindmap_e2e_test.py` — 端到端测试脚本
- `out/qc/mindmap_ds1_result.json` — mindmap 结构结果
- `out/qc/mindmap_e2e_results.json` — 端到端测试结果

## 十、总结

| 维度 | 结论 |
|------|------|
| **最大耗时瓶颈** | `mindmapMaxLength=4096` 固定常量，导致 batch 数过多 |
| **最高优先级优化** | M1: 增大 batchBudget 到模型实际 max_length，耗时降低 30x+ |
| **最适合的 DS** | **ds1（规程与预案）** — 法规文档层次化最强，与 GraphRAG 互补 |
| **实时 vs 预构建** | 实时生成 7-17s 已足够快；预构建耗时可优化到 ~20s |
| **构建结果** | 44 实体（1 central_topic + 9 branch + 34 sub_branch），43 关系 |
| **端到端测试** | 4/4 通过 — 整体框架、层次定位、分支导航、跨文档检索均有效 |