# 任务要求：桃曲坡水库档案导入 RAGFlow 知识库

- **日期**：2026-08-26（依据 2026-08-25 设计文档 v2 整理）
- **定位**：本文是本次导入任务的**原始要求汇总**——为什么做、做成什么样、受什么约束、怎么算验收。设计细节见 `specs/`，执行步骤见 `src/README.md`。

---

## 1. 任务背景与总体目标

桃曲坡水库积累了约 **222 个运营档案文件**（调度规程、应急预案、历年洪水调度记录、降雨/洪水统计表、划界报告、管理资料等），目前以派生文本形式散落在文件系统中，无法被有效检索和关联。

**总体要求**：将其导入本地 RAGFlow 实例，建成一个**综合知识库**，同时支撑三类受众：

| 受众 | 典型诉求 | 支撑手段 |
|---|---|---|
| 值班/调度问答 | 快速查规程条款、责任人、应急响应级别 | 标签软重排 + 规程类 dataset |
| 工程分析/统计 | 按工程部位、按年份检索技术参数与历史数据 | 元数据硬过滤（location/year） |
| 汇报/综合研判 | 跨文档多跳关联（洪水事件↔站点↔建筑物↔规程条款）、相似量级洪水比对 | GraphRAG 图谱 + flood_event 过滤 |

## 2. 目标环境

- 本地 RAGFlow **v0.27.0**（docker 预构建镜像，不可改源码）：Web `http://localhost:8080`，API `http://localhost:9380`（前缀 `/api/v1`）
- LLM：`https://llm.openagp.top:9080/v1`，模型 `Qwen3.8-27B-Q4_K_M.gguf`（具备读图能力，用于离线 VLM 识别；注意 Q4 量化数值精度有限且无图时会幻觉）
- 建库前置条件：租户已配置 embedding 模型

## 3. 语料范围要求

> **2026-08-26 语料源切换（覆盖下表旧要求）**：导入源由 `pdf_text_analysis/`
> 派生 txt 改为 **`pdfs/` 整理后的原始文件库直导**——RAGFlow 的引用溯源需在
> 原文页面锚定展示。本节已按切换后口径改写。

| 范围 | 处理要求 |
|---|---|
| `pdfs/` 下约 70 个 pdf/doc/xls（09-图像与多媒体、10-压缩包待处理除外） | 唯一可导入语料，全部入映射表；引用锚定原文页 |
| `05-基础数据与曲线` 等 12 张参数/管理图表 jpg/png | 不入扫描范围：经离线 VLM 识别 + 数值交叉校验 + 人工批准后文本化导入 |
| `video_analysis/`（81 jpg + 28 wav + 3 json） | **不导入** |
| `pdf_text_analysis/`（上一轮派生 txt 与 OCR 产物） | 遗留目录：仅 tables.py 兼容读取，不再作为导入源 |
| `_dupx`/`x` 后缀重复文件 | 内容哈希去重，仅保留一份并记录 duplicate_of |
| 无前缀哈希名等未匹配文件 | 映射表显式指派；脚本不得猜测 |

## 4. 功能要求

### 4.1 知识库结构（5 + 1）

| # | 名称 | chunk 方法 | GraphRAG | Raptor |
|---|---|---|---|---|
| ds0 | 桃曲坡标签库 | tag | ❌ | ❌ |
| ds1 | 规程与预案 | laws（512 tok） | ✅ light+resolution | ✅ |
| ds2 | 基础数据 | naive（256 tok） | ❌ | ❌ |
| ds3 | 历年洪水资料 | naive（256 tok） | ✅ light+resolution | ❌ |
| ds4 | 组织与管理 | naive（256 tok） | ❌ | ❌ |
| ds5 | 洪水事件报告 | paper（512 tok） | ❌ | ✅ |

### 4.2 GraphRAG 要求（仅 ds1/ds3，控制成本）

- method=`light`；显式开启 `resolution`（合并同名变体实体，如 柳林站 vs 柳林雨量站）
- 本体限定 6 类实体：`FloodEvent / Station / Structure / Person / Regulation / Parameter`
- **配置优先**：仅通过 parser_config 注入，不改任何上游代码；提示词定制只留作验收不达标后的二阶段

### 4.3 标签系统要求

- 标签库词表为受控两层共 **12 行**：5 个知识类型（规程预案/基础数据/洪水资料/组织管理/工程资料）+ 7 个洪水事件（2021-10、2020-8、2019-7、2013-7、2008-8、其他、历年统计）；TAB 分隔 UTF-8 两列格式
- 标签库必须**先建先 parse**，再在 ds1–ds5 配置 `tag_kb_ids`、`topn_tags=3`（ds4 为 2）
- 定性：标签只是检索**软重排信号**，禁止当硬过滤用

### 4.4 元数据要求（11 字段硬过滤通道）

每个文档注入 11 个元数据字段并注册为 dataset 元数据 schema（含 type/description/enum）：
`doc_category / sub_category / flood_event / doc_type / year / source_format / quality / responsible_dept / doc_nature / location / flood_magnitude`

- 字段值由映射表推导，空值省略；`year` 为整数
- 精确筛选一律走 `meta_data_filter`（manual/semi_auto/auto），支持按事件、部位、年份过滤

### 4.5 表格与多模态预处理要求

- 洪水统计类关键表：能在 `pdfs/` 找到原生 xlsx 的优先以原生文件 + `table` 方法导入；找不到的从解压文本生成 Markdown 表
- 高频参考表叠加 Q&A 行抽取（`.qa.md`）
- 库容水位对照表 / 泄流曲线：**离线 VLM 识别 → 与规程文字化值交叉校验（百年一遇泄量 1454 m³/s、千年一遇 2218 m³/s、汛限水位 788.5 m）→ 人工复核 → `--approve` 后才允许导入**；VLM 失败重试 ≤2 次，仍失败回退 OCR 文本并标 quality=low 待人工补录
- OCR 质量分级入库：high=原生 word/excel/xlsx；medium=可读 OCR/PDF 抽取；low=数字错乱或曲线图

## 5. 流程与顺序要求

1. **阶段0** 全量扫描生成映射表 → 人工审查后方可进入后续阶段
2. **阶段1** 表格/QA/VLM 预处理（VLM 产物须过人工批准门）
3. **阶段2** 幂等建库：标签库先行 parse → 注入 tag_kb_ids → 注册元数据 schema（KB 级配置必须在上传前一次配齐）
4. **阶段3** 三步式导入（upload → 写元数据 → parse），顺序 ds1→ds2→ds3→ds4→ds5；**全量前必须先用 ds3 抽 5 文件 pilot 实测 GraphRAG 成本**
5. **阶段4** 质检验收（见下节）
6. 导入过程断点可续：状态持久化，重跑跳过已完成条目；默认 dry-run，显式 `--apply` 才写库

## 6. 硬性约束

| 约束 | 内容 |
|---|---|
| 零上游改动 | 不修改 `/opt/git/ragflow` 任何文件；唯一引用为其 `conf/public.pem` 做登录密码 RSA 加密 |
| 语料只读 | 两个语料根目录禁止写入 |
| 凭据安全 | 仅经环境变量 `RAGFLOW_EMAIL`/`RAGFLOW_PASSWORD` 注入，不入库不入 git |
| 产物隔离 | 所有运行产物写本项目 `out/`，不污染源码目录 |
| 人工门槛 | mapping.csv 审查、VLM approve、10% OCR 抽样、pilot 通过——四道门缺一不可 |

## 7. 验收标准

1. **图谱多跳**（use_kg=true）能回答：
   - 溢洪道设计泄量（期望命中 1454）
   - 2021 年洪水次数与时序
   - 10·3 洪水调度依据的规程条款及涉及站点
   - 安芳东担任指挥的洪水事件
2. **元数据硬过滤**：`flood_event=2021-10`、`flood_event=2013-7` 条件查询返回正确子集
3. **参数快查**：汛限水位 788.5 可直接命中
4. **resolution 生效**：同名实体变体在图中合并
5. 六问 QC 报告（`out/qc/report_*.md`）整体通过率达标，未通过项有归因

## 8. 交付物清单

| 交付物 | 位置 |
|---|---|
| 全套工具链代码 + 单元测试 | `src/`（46 用例全绿） |
| 映射表 | `out/mapping.csv`（222 文件逐行） |
| VLM 识别产物与校验记录 | `out/vision/`（raw/cross_check/compare_report/approved.flag） |
| 建库状态 | `out/setup_state.json` |
| 导入状态（断点续命） | `out/import_state.json` |
| QC 报告 | `out/qc/results_*.json` + `report_*.md` |
