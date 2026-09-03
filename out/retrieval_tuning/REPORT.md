# P2-9 检索层专项报告

- **日期**：2026-09-03
- **范围**：修复 xls 12 题 4 FAIL（X5/X6/X7/X9）与 26 题 4 FAIL（W5/W6/G3/G4）中的排序层缺口
- **前置**：本轮同时完成模型本地化（embedding bge-m3 + rerank bge-reranker-v2-m3 → 私有 labxinf.openagp.top:9080，对话/检索全链路验证通过）

## 一、根因（两个，均已实证）

### 1. 壳块无 tag_feas —— 打分结构性吃亏

v0.27.1 最终分 = `(1−vsw)·tksim + vsw·vtsim + rank_fea`，其中
`tag_fea = cos(查询标签分布, 块标签) × 10.0`（`rag/nlp/search.py:389`）。
壳（UNSTART 不 parse）跳过了 tag 阶段 → 无 tag_feas；解析件（尤其 ds1 规程预案类）
携带解析期标签 → 稳吃 ≤10 分加成。混合检索时壳被结构性压到 10 名开外。

**修复**：对 15 份壳共 148 块按其 11 字段元数据写入方向正确的词表标签
（ES `_update_by_query`，逐批核验零污染）：

| 壳 | 块数 | 写入向量 |
|---|---|---|
| 3 份 xls（ds3） | 28 | 洪水资料:10, 基础数据:8（历年统计类另加 历年统计:5） |
| 8 份 PNG（ds2） | 102 | 基础数据:10, 历年统计:5（镜像元数据 doc_category+flood_event） |
| 4 份 PNG（ds4） | 18 | 组织管理:10 |

关键事实：ds2/ds4 解析件携带的是 LLM 自由标签（hydrology 等），与 13 词表查询向量
cosine≈0 —— 库内竞争者本无标签加成，壳只在**跨库**竞争时才吃亏；补词表标签即对等修正。
**效果**：X1 rank 17→5→2；X5/X6/X7/X9 全部 3/3 稳定 PASS（rank 1/5/5/3）。

### 2. 候选池 64 预截断 —— 多库场景目标块进不了池

v0.27.1 服务端默认 `rerank_candidates_count=64`：在最终打分**之前**先按 ES 预分截断候选。
G4 的目标块全库向量相似度最高（0.464）却进不了 64 池 → 直接从返回集中消失，
rerank/权重/查询改写都救不回（够不着）。池扩到 256 后该块 rank 1。

**但 256 不是全局正解**：对 F1 垃圾重灾区 ds3，更大赛池放进更多高复合分垃圾块
（整篇 .doc 关键词密度 + 标签加成），反而挤掉行切片 —— 实测 12 题回退 8/12、
QC Q4 PASS→FAIL。**结论：pool 是分场景旋钮**：

| 场景 | 配置 | 依据 |
|---|---|---|
| 多库（ds1+ds2+ds4，26 题） | `rerank_candidates_count=256` | G4/W6 修复，其余 22 题零回归 |
| 单库 ds3（12 题、QC 六问） | 服务端默认（不传） | 64 池 + 打标后 12/12、QC 6/6；256 实害 |

### 3. 未采纳项（实测无收益）

- **rerank（vsw 0.3/0.5）**：对 8 个 FAIL 题零额外收益，反引 labxinf 依赖与延迟 → 不用。
  对话层助手原有 rerank 配置不动（chat 探针 3/3 PASS）。
- **keyword=true（LLM 查询改写）**：能修 G4 但每题一次 LLM 调用（慢、网关抖动敏感），
  且 256 池已确定性覆盖同需求 → 不用。

## 二、回归结果

| 套件 | before | after | 配置 |
|---|---|---|---|
| xls 12 题（ds3，×3 多数） | 8/12 | **12/12** | 默认池 + 壳打标 |
| 26 题检索（ds1+ds2+ds4，×3 多数） | 22/26 | **24/26** | 池 256 + 壳打标 |
| QC 六问 | 6/6 | **6/6** | 默认池（Q4 曾被 256 误伤，回退后恢复） |
| pytest 全套 | — | **195 passed, 1 skipped** | 离线 |
| chat 层探针（G4/W6/R2 经 labxinf rerank） | — | **3/3**，引用锚定 .png/.jpg 壳 | — |

**剩余 2 FAIL（W5/G3）为内容层缺口，非排序层**：目标块（铁锹 315 把行 / 所属企业行）
在单文档内仅 rank 4-5/5（sim 0.29-0.31），全球排名 100 开外，任何排序旋钮够不着。
正解 = P1-4 就地补"常问速查"行（xls 里程碑已验证 QA 原句 rank1 规律），属下一任务。

## 三、代码与产物

- `src/ragflow_client.py`：`search_datasets` 新增 5 个可选参数（rerank_id /
  vector_similarity_weight / keyword / page_size / rerank_candidates_count），
  缺省一律不进 payload，行为与旧版完全一致（含测试）。
- `src/run_qc.py`：`RERANK_CANDIDATES_COUNT = None`（含 ds3 误伤实证注释）。
- `qa_eval/run_eval8.py`：`POOL = 256` 接线；`questions.jsonl` fusion_doc 改指壳文件名
  （PNG 壳迁移后原 .md 名使"目标在召回"遥测失真，判分不受影响）。
- `run_eval_xls.py`：保持默认池（含分场景结论注释）。
- 探针：`probe_p29.py`（三配置 × 8 题 × 3 次）+ `probe_p29_results.json`。
- 台账：`tag_shells_experiment.json`（15 壳打标明细与回滚语句）。
- 回归报告：`out/xls_fusion/report_xls_after_20260903_165723.md`（12/12）、
  `out/vision_8chart_test/qa_eval/report_20260903_172453.md`（24/26）、
  `out/qc/report_20260903_173631.md`（6/6）。

## 四、回滚

- 壳标签：对 `tag_shells_experiment.json` 各批 query 执行
  `ctx._source.remove('tag_feas')` 的 `_update_by_query`。
- 池参数：run_eval8 的 `POOL` 改回 None；其余本就为默认。
- 模型：`out/upgrade-v0.27.1/model_tables_pre_labxinf.sql`（切换前 mysqldump）。
