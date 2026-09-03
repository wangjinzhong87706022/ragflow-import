# 桃曲坡水库知识库 RAGFlow 导入设计

- **日期**：2026-08-25
- **状态**：v2——已按同日源码评审意见修订（多模态路径、GraphRAG 提示词策略、标签定性、导入流程），待用户复审
  - **2026-08-27 注**：本文涉及 RAGFlow API 契约的描述（登录加密、列表响应形状、分页参数、run 状态值、parser_config 中 graphrag/raptor 的嵌套位置等）**以 `docs/review-2026-08-26-deep.md` §一 的容器实证结论为准**；语料源条款以 requirements §3 的切换说明为准
- **语料源**（2026-08-26 切换）：`/home/scada/SmartTwinRes-skills/pdfs/` 整理后的原始文件库直导；`pdf_text_analysis/` 为上一轮遗留，不再作导入源
- **目标实例**：本地 RAGFlow（Web `http://localhost:8080`，API `http://localhost:9380`，前缀 `/api/v1`；docker 预构建镜像 `infiniflow/ragflow:v0.27.0`）
- **用途**：综合知识库，服务三类受众——值班/调度问答、工程分析/统计、汇报/综合研判

---

## 1. 背景与目标

### 1.1 语料概况

桃曲坡水库运营档案派生文本库共 **222 个文件，约 23 MB**：

- 110 个 txt ——**唯一可导入语料**（规程/预案 PDF 抽取文本、OCR 产物、word/excel 解压文本）
- 81 jpg + 28 wav + 3 json —— 全部为 `video_analysis/` 视频分析产物，**不导入**。注意：扫描件/图纸的图像原件不在本目录，而在原始资料库 `pdfs/` 中
- 目录结构：顶层 11 个 txt（**无前缀编码**）；`excel_extracted/`(27)、`ocr_new/`(48)、`word_extracted/`(24)、`video_analysis/`(112)、`extracted/`、`repaired/`(均空)

文件名采用前缀编码 taxonomy（仅子目录派生文件名带前缀）：

| 前缀 | 内容 |
|---|---|
| `03-施工图纸与设计` | 设计图、施工图 |
| `04-确权划界` | 划界报告 |
| `05-基础数据与曲线` | 库容水位对照表、泄流曲线 |
| `06-历年洪水资料` | 历年洪水调度记录（最大，45 文件） |
| `07-管理资料` | 制度、机构、人员 |
| `08-政策文件` | 政策转发件 |

`06-历年洪水资料` 子分类：`02-2021年洪水调度`(15)、`03-2020年洪水`(4)、`04-2019年洪水`(2)、`05-2013年洪水`(5)、`06-2008年洪水`(5)、`07-其他洪水事件`(1)、`08-历年洪水统计`(13)。

文件名另有两个事实需在预处理中处理：

- 存在少量**无前缀哈希名文件**（如 `ocr_new/1FE9C75D…_OCR.txt`）与 `.png.txt`
- `word_extracted/` 等目录存在 `_dupx`/`x` 后缀的**重复文件**

### 1.2 设计目标

构建一个**综合知识库**，能同时支撑：

1. **值班/调度问答**——快速查规程条款、责任人、应急响应级别
2. **工程分析/统计**——按工程部位、按年份检索技术参数与历史数据
3. **汇报/综合研判**——跨文档多跳关联（洪水事件↔站点↔建筑物↔规程条款）、相似量级洪水案例比对

### 1.3 关键技术决策（已确认）

| 决策项 | 结论 | 依据 |
|---|---|---|
| 知识图谱（GraphRAG） | **启用，方案 B**；dataset 1/3 显式设 `resolution: true` | 洪水场景实体强关联，向量检索无法做多跳；跨文档同名变体实体需去重合并 |
| 视频素材 | **不导入** | 用户决策 |
| 表格处理 | 洪水统计类关键表优先关联 `pdfs/` 原生 xlsx 以 `table` 方法导入；其余生成 Markdown 表（方案 A）；5-6 张关键参考表叠加 Q&A 行抽取（方案 B） | 原生 xlsx 数值保真优于解压文本 |
| 视觉模型 | 使用 `Qwen3.8-27B-Q4_K_M.gguf`（端点 `https://llm.openagp.top:9080/v1`），**在预处理脚本中离线调用**，不经 RAGFlow picture 通道 | 用户决策（模型具备读图能力）；RAGFlow picture 解析器对 OCR 文本 >32 字符的图片跳过视觉模型（rag/app/picture.py:99-103），库容表必然命中该分支 |
| 库容水位对照表 | **离线 VLM 识别 + 规程文字化值交叉校验 + 人工复核 → 产出修正 Markdown 导入** | OCR 数字严重错乱；产品内无识别-校准闭环 |
| 泄流曲线 | **文字化值 + 离线 VLM 校准**，校准产物为修正值清单文件 | 曲线 OCR 不可恢复 |

---

## 2. 数据集（Dataset）划分

### 2.1 五个数据集

| # | 名称 | chunk 方法 | 主要内容 | 来源前缀/目录 |
|---|---|---|---|---|
| 1 | 规程与预案 | `laws` | 调度规程、应急预案、安全评价、汛期计划、政策转发件 | 顶层规程/预案/评价类文件 |
| 2 | 基础数据与工程资料 | `naive` | 设计/施工图、划界报告、库容表、泄流曲线（后两者为预处理修正产物） | `03-*`、`04-*`、`05-*`、顶层划界报告 |
| 3 | 历年洪水资料 | `naive`，关键统计表用 `table`（原生 xlsx） | 各年洪水调度记录、降雨统计、洪水特征值 | `06-*`、顶层水情快报类 |
| 4 | 组织与管理 | `naive` | 制度、机构、人员档案、政策转发件 | `07-*`、`08-*` |
| 5 | 洪水事件报告 | `paper` | 综合性洪水分析报告 | `word_extracted/` 中报告类 |

> v2 变更：取消"库容表/泄流曲线用 `picture`"与"人员卡片用 `resume`"两个文档级叠加方案。原因：① 多模态识别改走预处理离线路线（见 1.3），且可导入语料中没有任何图片；② v1 API 的文档更新接口不允许把 chunk_method 改为 `resume`（validation_utils.py:536 合法集不含 resume），而人员卡片是短 OCR 文本而非完整简历，resume 解析器收益低。

### 2.2 各 dataset 的 parser_config

| # | chunk 方法 | parser_config 关键项 | GraphRAG | Raptor |
|---|---|---|---|---|
| 1 | `laws` | `chunk_token_num=512`，`auto_keywords=10`，`auto_questions=3`，`topn_tags=3`，`tag_kb_ids=[标签库]`；`graphrag={use_graphrag:true, method:"light", entity_types:[本体6类], resolution:true}` | ✅ light+resolution | ✅ |
| 2 | `naive` | `chunk_token_num=256`，`auto_keywords=8`，`topn_tags=3`，`tag_kb_ids=[标签库]` | ❌ | ❌ |
| 3 | `naive`/`table` | `chunk_token_num=256`，`auto_questions=5`，`topn_tags=3`，`tag_kb_ids=[标签库]`；`graphrag` 同 #1 | ✅ light+resolution | ❌ |
| 4 | `naive` | `chunk_token_num=256`，`auto_keywords=8`，`topn_tags=2`，`tag_kb_ids=[标签库]` | ❌ | ❌ |
| 5 | `paper` | `chunk_token_num=512`，`auto_keywords=8`，`topn_tags=3`，`tag_kb_ids=[标签库]` | ❌ | ✅ |

parser_config 字段存在性与取值范围均经源码核对：

- `auto_keywords`（task_executor.py:446，≤32）、`auto_questions`（task_executor.py:483，≤10）、`chunk_token_num`（≤2048）、`topn_tags`（默认 1，范围 1-10）（validation_utils.py:453-465）
- `tag_kb_ids`/`topn_tags`（task_executor.py:581-634）、`raptor.use_raptor`（task_executor.py:1476）、`graphrag.use_graphrag`（task_executor.py:1523）
- `method` 支持 `"light"`(默认)/`"general"`/`"ner"`（rag/graphrag/general/index.py:118-138）；`entity_types` 默认 `["organization","person","geo","event","category"]`（extractor.py:46）；`resolution` 默认 false（validation_utils.py:411），须显式开启
- **配置层级注意**：`auto_keywords`/`auto_questions` 读自**文档级** parser_config（上传时快照继承 KB 配置，file_service.py:552-556）；`tag_kb_ids`/`topn_tags`/`raptor`/`graphrag` 读自 **KB 级实时配置**。因此所有 KB 级配置必须在**上传前**一次配齐，之后修改不会回溯已有文档。

---

## 3. 知识图谱设计（GraphRAG）

### 3.1 为什么建图谱

一场洪水牵涉雨量站、水工建筑物、责任人、规程条款、调度指令，信息散落在 dataset 1-5。向量检索只能按语义相似召回片段，**无法跨文档多跳**。例如：

> "2021年10月3日洪水调度时，依据了规程的哪一条？涉及哪些责任人？各雨量站降雨多少？"

需把"洪水-2021-10·3"同时连到规程条款、责任人卡片、降雨统计表——只有图谱能做。

### 3.2 本体（6 类实体 + 7 类关系）

**实体类型**

| 实体类型 | 标识 | 实例 | 主要来源 dataset |
|---|---|---|---|
| `FloodEvent` 洪水事件 | `洪水-{年}-{月日}` | 洪水-2021-10·3 | 3, 5 |
| `Station` 雨量站/水文站 | 站名 | 柳林、瑶曲、庙湾、马栏、枢纽、红星、尚书 | 3 |
| `Structure` 水工建筑物 | 建筑物名 | 主坝、溢洪道、高洞、低洞、放水塔 | 1, 2 |
| `Person` 责任人 | 姓名+职务 | 安芳东(局长)、党九社(副局长)… | 4 |
| `Regulation` 规程条款 | `规程-{章节号}` | 规程-3.1(防洪调度任务) | 1 |
| `Parameter` 关键参数 | 参数名=值 | 汛限水位=788.5m、总库容=5720万方、百年一遇泄量=1454m³/s | 1, 2 |

**关系类型**

| 关系 | 含义 | 示例 |
|---|---|---|
| `occurred_at` | 洪水流经站点 | (洪水-2021-10·3)-[occurred_at]->(柳林站) |
| `measured` | 站点测得数值 | (柳林站)-[measured:降雨量=21.7mm]->(洪水-2013-7·22) |
| `discharged_via` | 洪水经建筑物泄洪 | (洪水-2021-10·3)-[discharged_via]->(溢洪道) |
| `governed_by` | 调度依据条款 | (洪水-2021-10·3)-[governed_by]->(规程-3.3) |
| `responsible_for` | 责任人分管 | (安芳东)-[responsible_for:防汛行政]->(桃曲坡水库) |
| `has_parameter` | 建筑物/水库的参数 | (溢洪道)-[has_parameter]->(百年泄量=1454m³/s) |
| `preceded_by` | 洪水时序 | (洪水-2021-9·25)-[preceded_by]->(洪水-2021-10·3) |

### 3.3 实现路径（两步走：先配置，后定制）

RAGFlow 的 GraphRAG 是**文档级自动抽取**——对每个 chunk 跑 LLM 抽取实体和关系（每 chunk 至少 1+2 次 gleaning 调用，extractor.py:47），文档级 subgraph 合并进 KB 级图谱索引（`knowledge_graph_kwd` 字段标记 entity/relation/graph/subgraph/community_report）。不需要手写三元组。

**第一步（必做，纯配置、零代码改动）**——dataset 1、3 的 parser_config 写入：

```json
"graphrag": { "use_graphrag": true, "method": "light", "entity_types": ["FloodEvent","Station","Structure","Person","Regulation","Parameter"], "resolution": true }
```

`entity_types` 会被直接注入抽取提示词的类型约束（light/graph_extractor.py:52,63 的 `entity_types=",".join(...)`；general/graph_prompt.py:15 占位符 `{entity_types}`），即**实体类型限定无需改任何代码**。`method`/`entity_types`/`resolution` 均为合法校验项（validation_utils.py:404-421）。默认 entity_types 为 organization/person/geo/event/category（extractor.py:46），由上述本体覆盖。

**第二步（可选，仅当 3.5 验收不达标）**——关系类型限定与实体命名规范（如统一为"洪水-2021-10·3"）不在配置覆盖范围内，才需要定制提示词模板 `rag/graphrag/{general,light}/graph_prompt.py`。**注意：当前实例为预构建镜像 `infiniflow/ragflow:v0.27.0`（docker/.env:239），直接修改宿主机 repo 对运行实例无效**（compose 仅挂载 logs/service_conf/entrypoint，docker-compose.yml:57-63），必须任选其一：

- bind-mount 进运行容器：给实际运行的 `ragflow-cpu`（或 `ragflow-gpu`）服务 volumes 增加如 `- ../rag/graphrag/light/graph_prompt.py:/ragflow/rag/graphrag/light/graph_prompt.py` 后重启；
- 或基于源码重建镜像。

定制的提示词单独存档并标注所基于版本，便于随时摘除回到纯配置。另注意 repo HEAD 与 v0.27.0 镜像可能存在代码差异，实施前应 diff 容器内对应文件。

### 3.4 跨 dataset 取舍（方案 B）

GraphRAG 在 RAGFlow 里按 dataset 独立构建。6 类实体跨 5 个 dataset。采用**方案 B**：

- **只在 dataset 1（规程预案）+ dataset 3（洪水资料）开启 GraphRAG**（light 方法 + resolution）
- 洪水事件↔站点↔建筑物↔规程条款是图谱高价值骨架，集中在这两个库
- 责任人（dataset 4）通过 `responsible_for` 关系在洪水报告里被提及，会在 dataset 3 抽取时自然带出
- dataset 2/4/5 不开 GraphRAG，省 token 成本

### 3.5 验收用代表问题

建成后应能回答：

1. "桃曲坡水库溢洪道的设计泄量是多少？" → `溢洪道 has_parameter 百年泄量=1454m³/s`（单跳）
2. "2021年共发生几次洪水？时序如何？" → `preceded_by` 链（多跳）
3. "10·3洪水调度依据规程哪条？涉及哪些站？" → `governed_by` + `occurred_at`（跨文档多跳）
4. "安芳东在哪些洪水事件中担任指挥？" → `responsible_for` 反向（跨文档）

> **2026-09-01 语料审计修正**（全库 2687 分片实证，Q3/Q4 由此落地为语料可支撑的口径）：
> - 问 3 的"依据规程哪条"在语料中**无现成条款引用**（10·3 汇报仅引省防总
>   会商精神+水利部通知+Ⅳ级预案）；原问法"10·3"措辞还导致目标分片排序出榜。
>   QC 题改为"桃曲坡水库10月3日至6日洪水调度涉及哪些站点？"，判分锚定
>   `["柳林","瑶曲"]`（答案分片单跳可召回）。
> - 问 4 全库仅 2 个含"安芳东"的分片，**同属 2020 年"8·16"洪水**
>   （洪水调度报告.doc 落款 2020-8-20），原期望"2021"系笔误；且事件名仅存在于
>   同文档标题分片，"哪些事件"问法下无法召回。QC 题改为
>   "安芳东在8·16洪水调度中担任什么角色？"，判分 `["安芳东","8·16"]`。
>   若未来建图补出 `responsible_for` 边（需 Knowledge Compilation 管线），
>   可再恢复"跨事件枚举"口径。

---

## 4. 元数据 Schema（11 字段）

每个文档注入 11 个元数据字段（document 级 `meta_fields`），支撑三类受众的过滤检索。

### 4.1 字段定义

| 字段 | 含义 | 取值示例 | 来源 |
|---|---|---|---|
| `doc_category` | 文档大类（受控） | 规程预案/基础数据/洪水资料/组织管理/工程资料 | 文件名前缀映射 |
| `sub_category` | 子类 | 库容曲线、泄流曲线、降雨统计、洪水特征值… | 文件名前缀映射 |
| `flood_event` | 关联洪水事件 | 2021-10、2020-8、2019-7、2013-7、2008-8、其他、历年统计、（空） | 文件名前缀映射 |
| `doc_type` | 文档形态 | 文本/表格/图片/图纸 | 文件后缀（含 `.png.txt`） |
| `year` | 年份 | 1997、2008、2013、2019、2020、2021、2026 | 文件名/内容 |
| `source_format` | 来源格式 | pdf、word、excel、ocr_jpg/png、native_xlsx | 文件路径 |
| `quality` | OCR 质量分级 | high/medium/low | 质量判定规则 |
| `responsible_dept` | 责任/发文部门 | 防汛办、管理局、设计院 | 内容/文件名 |
| `doc_nature` | 文件性质 | 法规/技术/管理/统计 | 内容判定 |
| `location` | 工程部位 | 主坝、副坝一、副坝二、溢洪道、高洞、低洞、放水塔、库区、全库 | 内容/文件名 |
| `flood_magnitude` | 洪水量级 | 百年一遇、千年一遇、一般洪水、不适用 | 内容判定 |

### 4.2 设计说明

- `dam_name`（大坝名称）**并入 `location`**：主坝/副坝一/副坝二本就是部位取值，不单列
- `urgency_level`（应急响应级别）**不单列**：只对预案类有意义，用 `auto_keywords` 软标签即可
- `version`（版本号）**不单列**：只对规程类有意义，并入 `year` + 文件名即可
- `flood_event` 命名粒度为"年-月"（2021-10、2020-8），与 GraphRAG 的 `FloodEvent` 命名格式（洪水-2021-10·3）在月份上一致，便于关联
- `quality` 判定规则：`high`=word/excel 原生数字文本或原生 xlsx；`medium`=ocr_new 文本可读数字基本正确；`low`=ocr_new 数字严重错乱（库容表）或曲线图（泄流曲线）
- 11 字段将注册为各 dataset 的元数据 schema（key/type/description/enum），供 UI 过滤器与 auto 过滤模式的 LLM 使用（见 6 阶段 2）

### 4.3 字段与受众的对应

| 受众 | 高频使用字段 | 过滤方式 |
|---|---|---|
| 值班/调度问答 | `responsible_dept`、`doc_nature`（找部门、找现行法规） | chat `meta_data_filter`（manual/auto）硬过滤 |
| 工程分析/统计 | `location`、`year`（按部位、按年份） | `meta_data_filter` 条件过滤（支持数值比较） |
| 汇报/综合研判 | `flood_event`、`flood_magnitude`（按事件、按量级筛选相似案例） | `meta_data_filter`（semi_auto：LLM 从查询生成条件） |

---

## 5. 标签系统（三层）

### 5.1 独立标签库

新建 dataset `桃曲坡标签库`，chunk 方法 `tag`。词表文件必须符合 RAGFlow 标签库解析格式（rag/app/tag.py）：**xlsx 两列，或 UTF-8 csv/txt 两列（TAB/逗号分隔）——第 1 列描述文本、第 2 列逗号分隔标签**；标签中的 `.` 会被替换为 `_`（tag.py:31、search.py:848）。录入受控词表（两层）：

- **知识类型层**：规程预案、基础数据、洪水资料、组织管理、工程资料
- **洪水事件层**：2021-10、2020-8、2019-7、2013-7、2008-8、其他、历年统计

在 dataset 1-5 的 parser_config 设 `tag_kb_ids=[标签库ID]`、`topn_tags=3`。**标签库必须先建并完成 parse**：其余 dataset 打标时从其索引聚合读取词表（task_executor.py:589-594），未解析的标签库会使词表聚合为空、打标无产出（且白耗兜底 LLM 调用）。

### 5.2 三层标签（v2 定性修正）

| 层 | 机制 | 性质 |
|---|---|---|
| 第 1 层 知识类型 | 受控词表（标签库） | **软信号**：检索重排加权，不能单独做精确过滤 |
| 第 2 层 洪水事件 | 受控词表（标签库） | 同上 |
| 第 3 层 自动关键词/问题 | `auto_keywords`（LLM 抽词）+ `auto_questions`（LLM 生成问题） | 软信号，语义增强 |

机制说明：parse 时每个 chunk 先按全文匹配从标签库聚合取 top-n 标签（search.py:839-849 `tag_content`），匹配不中的 chunk 由 LLM 从词表中选标签兜底（task_executor.py:606-634）；查询时给问题打同样的标签（`tag_query`），检索阶段按标签重合度对候选 chunk 重排加权（search.py:344-374 `_tag_feature_scores`，权重 ×10）。

### 5.3 精确过滤通道（v2 新增）

受控的硬过滤不经标签，而经文档元数据：11 字段写入 document `meta_fields` 后，对话侧用 `meta_data_filter` 过滤（common/metadata_utils.py:153，支持 `manual` 手工条件 / `semi_auto` 限定字段由 LLM 生成条件 / `auto` 全自动三种模式，支持 ES push-down）。三类受众的"按事件筛选""按部位/年份统计"等精确诉求全部落在这里；标签层定位为语义增强。

---

## 6. 完整数据预处理与导入流水线

### 阶段 0：源语料盘点与映射表

建立映射表（CSV 或脚本内置 dict），每行：

```
源路径 | 原生文件路径(pdfs/对应原件,可空) | 目标dataset | doc_category | sub_category | flood_event | doc_type | year | source_format | quality | responsible_dept | doc_nature | location | flood_magnitude | duplicate_of(可空)
```

**映射规则（v2 修订）：**

| 源目录/前缀 | → dataset | 说明 |
|---|---|---|
| 顶层 11 个 txt | **逐一指派** | 无前缀编码，不整批归入 dataset 1：调度规程/应急预案×2/汛期计划/安全评价/安全鉴定/政策转发件→1；划界报告→2；水情通报/水情快报/汛情专报→3 |
| `03-施工图纸与设计_*` | 2 | 工程资料 |
| `04-确权划界_*` | 2 | 工程资料 |
| `05-基础数据与曲线_*` | 2 | 基础数据（库容表/泄流曲线导入的是预处理修正产物，见阶段 1） |
| `06-.../02~07 子分类_*` | 3 | 洪水资料 |
| `06-.../08-历年洪水统计_*` | 3 | 洪水统计；优先关联 `pdfs/` 原生 xlsx |
| `07-管理资料_*` | 4 | 组织管理 |
| `08-政策文件_*` | 4 | 政策转发件（doc_nature=法规；个别偏技术的可按内容调整） |
| `word_extracted/`、`excel_extracted/` | 按文件名前缀归入对应 dataset | excel_extracted 行须记录 `pdfs/` 对应原生文件路径 |
| 无前缀哈希名文件 | 映射表显式指派 | 脚本遇未匹配文件必须报错终止，不得猜测 |
| `_dupx`/`x` 后缀重复文件 | — | 标记 duplicate_of，导入前去重 |
| `video_analysis/` | **不导入** | 含全部 81 jpg / 28 wav / 3 json |

### 阶段 1：文件预处理（Python 脚本，导入前在本地完成）

1. **文件名规范化**：保留前缀编码（用于元数据解析），生成干净可读名
2. **去重**：识别 `_dupx`/`x` 后缀重复文件，内容哈希比对确认后仅保留一份，记录 duplicate_of
3. **OCR 质量复核**：对 `ocr_new/` 按 10% 抽样人工复核，确认 `quality` 标注
4. **表格预处理**：
   - 洪水统计类关键表：若在 `pdfs/` 找到原生 xlsx → 直接上传原生文件并将该文档 chunk 方法设为 `table`（v1 API 允许，validation_utils.py:536 含 table）；找不到原生文件的才从解压文本生成 `.md`（方案 A：`# 表：XX站XX年降雨统计` + Markdown 表）
   - 5-6 张高频参考表叠加方案 B：生成 `.qa.md`（每行转"问：XX年XX站总降雨量？答：XXXmm"）
5. **多模态补救（离线 VLM，不经 RAGFlow picture 通道）**：
   - 取材：库容水位对照表/泄流曲线原图位于 `/home/scada/SmartTwinRes-skills/pdfs/05-基础数据与曲线/`（语料目录内没有图片）
   - 流程：原图直调 `https://llm.openagp.top:9080/v1` 视觉接口识别 → 与 OCR 文本及规程文字化值（百年一遇泄量 1454 m³/s、千年一遇 2218 m³/s、汛限水位 788.5m 等）交叉比对 → 错行人工复核 → 产出修正后的 `库容水位对照表.md`、`泄流曲线_文字化值.md` → 以 naive 导入 dataset 2；比对记录存档
   - 失败处理：VLM 调用失败重试 ≤2 次，仍失败则回退使用 OCR 文本并标 `quality=low` 待人工补录

### 阶段 2：在 RAGFlow 中建库与配置

1. 建 5 个 dataset（配置见第 2.2 节；KB 级 parser_config 必须在此步一次配齐——上传后修改不回溯已有文档）
2. 建标签库 `桃曲坡标签库` 并录入词表（格式见 5.1），**先完成 parse**；再在 dataset 1-5 设 `tag_kb_ids`
3. 为各 dataset 注册元数据 schema：`PUT /api/v1/datasets/{id}/metadata/config`，11 字段含 type/description/enum（供 UI 过滤与 auto 过滤模式的 LLM 使用）
4. GraphRAG 本体注入按 3.3 第一步纯配置（`entity_types` + `resolution`）；提示词定制留作验收不达标后的第二步，届时按 3.3 的容器生效方式操作

### 阶段 3：批量导入与元数据注入

每个文件三步（v2：上传接口不接收元数据，需单独写入）：

1. `POST /api/v1/datasets/{id}/documents` 上传
2. 写入元数据：批量 `POST /api/v1/datasets/{id}/metadata/update`（selector/updates/deletes），或单文档 `PATCH /api/v1/datasets/{id}/documents/{doc_id}` 带 `meta_fields`
3. `POST /api/v1/datasets/{id}/documents/parse` 触发解析

**导入顺序**（有依赖，保持不变）：

1. 标签库 + 受控词表（dataset 1-5 依赖，先 parse）
2. dataset 1（规程预案）→ 触发 Raptor + GraphRAG
3. dataset 2（基础数据，含多模态修正产物 md）
4. dataset 3（洪水资料）→ 触发 GraphRAG
5. dataset 4（组织管理）
6. dataset 5（洪水报告）

**成本试跑**：全量触发前，先对 dataset 3 抽 3-5 个文档开 GraphRAG 试跑，实测每 chunk LLM 调用量与耗时（每 chunk ≥3 次 LLM 调用，另有 merge/resolution 开销），据此估算总量；必要时调 `batch_chunk_token_size`/retry/timeout 旋钮（validation_utils.py:412-421）。

### 阶段 4：GraphRAG 本体约束与质检

1. 抽查抽取实体名是否规范（统一为"洪水-2021-10·3"而非变体）
2. **resolution 效果检查**（v2 新增）：同名变体实体节点是否合并（柳林站 vs 柳林雨量站、同一责任人多处出现）
3. 验证多跳问题（3.5 节 4 个代表问题）
4. 验证标签生效：对比开关 `tag_kb_ids` 的检索排序差异观察加权效果（标签是软重排信号，不以"过滤命中"为验收标准）
5. 多模态产物人工复核：库容表数字是否正确、泄流曲线校准值是否合理
6. 元数据过滤演练（v2 新增）：按三类受众各跑一条 `meta_data_filter` 查询，验证硬过滤通路

### 阶段 5：持续优化（导入后）

1. 检索调优：按三类受众实际提问调整 `similarity_threshold`、`topn_tags`、raptor 层级
2. 补录：对多模态仍错的库容表行人工补录正确值
3. 图谱扩展：若 light+resolution 多跳效果不足，dataset 1/3 升级为 `general` 方法重建图，或按 3.3 第二步定制提示词

---

## 7. 风险与对策（v2 修订）

| 风险 | 影响 | 对策 |
|---|---|---|
| Qwen3.8-27B Q4 量化降低数值精度 | 库容表/泄流曲线离线识别数字可能出错 | 与 OCR/规程文字化值交叉校验；错行人工补录 |
| 模型幻觉（测试已确认无图时会编造） | 离线识别引入错误数值 | 仅随图调用；识别结果必须经交叉比对与人工复核后才入库 |
| GraphRAG light 方法多跳能力有限 | 复杂跨文档问题可能答不全 | 先验证 resolution 效果；仍不足升级 `general` 或定制提示词（按 3.3 两步走） |
| GraphRAG token/时长超预算 | 导入窗口不可控 | 阶段 3 试跑估算；调 batch_chunk_token_size/retry/timeout；必要时缩小开启范围 |
| 提示词定制偏离 RAGFlow 默认 | 升级 RAGFlow 时可能冲突 | 定制文件单独存档、标注版本；bind-mount 方案便于随时摘除回到纯配置 |
| 镜像(v0.27.0)与 repo HEAD 代码差异 | 源码引用/行为偏差 | 实施前 diff 容器内关键文件（task_executor.py、rag/graphrag/*） |
| OCR 低质量文件占比 | 部分历史洪水资料数字不可靠 | `quality` 字段标注；关键数值优先用原生 xlsx |

---

## 8. 实现产物清单（v2 修订）

本设计落地后将产出：

1. **预处理脚本** `scripts/taoqupo_preprocess.py`——生成映射表、规范化文件名、去重、表格 Markdown/Q&A 生成、原生 xlsx 关联、离线 VLM 识别与交叉校验
2. **导入脚本** `scripts/taoqupo_import.py`——三步式调 RAGFlow API（上传 → 批量元数据 → parse）
3. **（可选，二阶段）GraphRAG 提示词定制文件**——存档于 docker/ 挂载目录，标注基于 v0.27.0；默认不改上游代码
4. **配置清单**——5 个 dataset + 1 个标签库的 parser_config、各 dataset 元数据 schema
5. **映射表** `scripts/taoqupo_mapping.csv`——222 个文件的元数据映射（含原生文件路径与去重标记列）

> 注：脚本与映射表路径为计划值，实现阶段以 writing-plans 产出的实施计划为准。

---

## 修订记录

- **v2（2026-08-25）**：按源码评审修正——① 多模态识别改为预处理离线 VLM 路线（picture 解析器对 OCR>32 字符跳过视觉模型，picture.py:99-103）；② GraphRAG 采用"配置优先、提示词定制为二阶段"策略，补充容器生效方式（bind-mount/重建镜像）；③ 标签定性由"硬过滤"改为软重排（search.py:344-374），精确过滤迁移至 meta_fields + meta_data_filter；④ graphrag 显式开启 `resolution`；⑤ 取消 picture/resume 文档级叠加方案（API 校验限制，validation_utils.py:536）；⑥ 导入改为上传→元数据→parse 三步，路由更正为 `datasets` 复数；⑦ 语料源声明补充 `pdfs/` 原始资料库，修正 81 jpg 性质描述（均为视频帧）；⑧ 顶层 11 文件逐一映射、补 `08-政策文件` 映射行、新增去重步骤与哈希名兜底规则。
