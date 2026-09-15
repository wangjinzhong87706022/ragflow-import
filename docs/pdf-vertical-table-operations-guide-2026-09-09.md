# PDF 竖排（旋转）表格处理操作手册（人工 / 代码双路径）

- 日期：2026-09-09
- 适用：扫描版 PDF 中横排（旋转 90°）表格的导入与质检
- 背景结论：见 `pdf-vertical-table-optimization-2026-09-09.md`（分析/评测）
  核心一句话：**不旋转 PDF，用 RAGFlow `table_vision_enhance` 原生解析**

## 0. 路径选择

| 场景 | 推荐路径 |
|---|---|
| 单份文档、临时处理、无开发环境 | **A 人工操作**（Web UI，约 1 小时含审查） |
| 批量文档、可复用流程、需要留痕 | **B 代码自动化**（脚本一条龙 + 质检脚本） |
| 两者通用原则 | 不旋转、不上传后立刻解析、解析完必须人工审块 |

---

## 1. 路径 A：人工操作（Web UI）

### 第 0 步：预检（5 分钟）

1. 鼠标在 PDF 中选中一段文字：**选不中 → 扫描件**（能选中复制则是文字版，走常规解析）
2. 快速翻页，记下所有"要把头侧过来看"的表格页码（仅用于第 4 步抽检，不做任何预处理）
3. 保持 PDF 原样，**不要旋转**

### 第 1 步：上传

知识库 → 目标数据集 → 「上传文件」→ 选 PDF → 等待完成。
**上传后先不要点解析**，先做第 2 步。

### 第 2 步：解析配置（关键）

打开文档的「配置 / 解析方法」对话框，按下表设置：

| UI 设置项 | 选择 | 对应 parser_config |
|---|---|---|
| 切片方法 / Chunk Method | **Naive（通用）** | `chunk_method: naive` |
| 版面解析 / Layout Recognition | **DeepDOC** | `layout_recognize: DeepDOC` |
| **表格视觉增强 / Table Vision Enhance** | **✅ 开启** | `table_vision_enhance: true`（关键开关） |
| 图像理解模型 / Img2txt | 选可用 VLM（如 Qwen3-27B） | `llm_id` |
| 切片 Token 数 | 512（默认） | `chunk_token_num` |
| 分隔符 | 默认 `\n!?;。；！？` | `delimiter` |
| HTML 转 Excel / html4excel | ❌ 不开 | `html4excel: false` |
| 页面范围 / Pages | 留空 | `pages` |
| RAPTOR / GraphRAG | ❌ 不开 | — |

注意事项：
- 表格视觉增强开关为灰时，先到「头像 → 系统模型设置」配置 Img2txt（图像理解）模型
- **不要选 Table 切片方法**（那是给 Excel/结构化表用的）；整份"正文+混合附录"用 Naive + DeepDOC + 视觉增强为实测最优
- 本开关保存后 `ext.table_vision_enhance=true`，服务端会回显到顶层（API 侧可验证）

### 第 3 步：触发解析

文档列表 → 勾选该文档 → 「解析」→ 等进度 100%。
扫描件耗时参考：128 页 ≈ 30 分钟（横排表格页越多越慢）。
注意：文档列表的 `chunk_num` 显示可能不可靠（曾显示 0 而实际有 128 块），以分块页面看到的实际数量为准。

### 第 4 步：人工审查分块（必做，10-20 分钟）

进入文档分块页面，对照原 PDF 检查：

**① 删垃圾块**：竖排表头残字（一行一两个字、语序颠倒）、正文碎片 → 勾选 → 删除。删除前确认没有连带注释等有效内容。

**② 核对表覆盖（重点看块尾）**：
- 一张附表常拆成多块、或**多张表合并进同一块**（如 p82 含 D.0.2+D.0.3、p94 含 D.0.18+D.0.19）——必须滚到**块末尾**确认有没有接着第二张表
- 逐个对照附表清单（D.0.1-1 ~ D.0.21 等）确认都是 Markdown 表格形态（`| 列头 |` 开头）
- 某表完全没成表 → 「新增分块」手工把表格抄成 Markdown 补建

**③ 校对题注与 OCR（点分块「编辑」）**：
- **补表号**：块最前面加一行 `表D.0.x 表名`（无题注表块都要补，直接影响检索命中率）
- **修错题注**：题注与表格内容不符的（如气象表被错标为经济社会表），改为正确表号
- **修 OCR 字**：`修田→梯田`、`经渍林→经济林`、`微地坝→淤地坝`、`农林牧油→农林牧渔`、`<`→`/`
- 空白模板表的数量列为空是**正常**（模板即无数据），不要补数值

### 第 5 步：问答验收

用绑定该知识库的助手提问，两类都问：

- **正例**：横排大表列名/分级，例如"表D.0.5 水土流失现状表有哪些列？""表D.0.11 坡度分几级？"→ 答案应引用具体表且列名正确
- **反幻觉**：问空白表数值（应回答"空模板需按实际工程填写"）、问无把握存在的表 → 不应编造
- 核对回答引用指向的文档；若串到旧文档 → 数据集有重复内容，见第 6 步

### 第 6 步：收尾

- 删除旧版/摘录/测试重复文档（避免检索双向命中）
- 未修好的表记录遗留清单
- 若有表块仍缺题注，后续可参照本目录 06 脚本思路批量补注（或继续手工编辑）

---

## 2. 路径 B：代码自动化

脚本目录：`code/pdf-vertical-table/`（Python ≥3.10，依赖 `pymupdf` `numpy` `requests`）

### 准备

```bash
pip install pymupdf numpy requests
# 各脚本头部常量替换：RAGFLOW_BASE / RAGFLOW_API_KEY / DATASET_ID / PDF_PATH
```

### 执行顺序

```bash
# ① 表格页检测（可选，输出缩略图人工确认横排表页码）
python 01_detect_table_pages.py

# ② 上传 + 配置(table_vision_enhance) + 触发解析 + 轮询（一条龙）
python 03_upload_configure_parse.py

# ③ 分页拉取全部 chunk + 表号映射清单
python 04_chunk_inventory.py

# ④ 质量评审（OCR 误读量化 + 异常块扫描 + 题注缺失统计）
python 05_quality_review.py

# ⑤ QA 检索复测（正例 + 反幻觉，含引用来源核验）
python 06_qa_retest.py
```

### 人工介入点（自动化无法替代）

- 01 输出缩略图后：**人工确认**哪些命中页是真正的横排表（竖向窄表不用管）
- 05 输出后：**人工决策**——重度损坏块删除、干净变体保留；中等/轻度误读逐个修复
- 修复方式：PATCH chunk 内容补题注 / 改 OCR 误读；垃圾块用批量删除：

```bash
# 删除文档（body 里带 ids）
curl -X DELETE "$BASE/api/v1/datasets/$DS/documents" \
     -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
     -d '{"ids":["<doc_id>"]}'

# 删除 chunk（body 里带 chunk_ids）
curl -X DELETE "$BASE/api/v1/datasets/$DS/documents/$DOC/chunks" \
     -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
     -d '{"chunk_ids":["<chunk_id>"]}'
```

### 已弃用

`02_rotate_pages_abandoned.py` 为 PDF 级物理旋转方案留档，**勿用于生产**（旋转破坏原文档、解析收益有限，见主文档 §1.4）。

---

## 3. 常见故障对照

| 症状 | 原因 | 处置 |
|---|---|---|
| 上传 413 | urllib 手拼 multipart 格式瑕疵 | 用 `requests` 库上传 |
| 一直 UNSTART | 未触发解析 | 调 `POST /datasets/{ds}/documents/parse` |
| PATCH 文档 405 | 用错方法 | 更新配置用 **PATCH** /documents/{doc_id}（PUT/POST 均 405） |
| PATCH 拒绝 table_vision_enhance | 写在 parser_config 顶层 | 写进 **ext**：`parser_config.ext.table_vision_enhance=true` |
| DELETE 单文档/单块 405 | 端点要求批量 | 一律批量接口 + body（ids / chunk_ids） |
| chunks page_size 报错 | 上限 100 | 分页拉取（04 脚本已处理） |
| 文档列表 chunk_num=0 | 字段不可靠 | 以 chunks 接口 `data.total` 为准 |
| 问答张冠李戴 | 表块缺题注 / 损坏块干扰 | 补"表D.0.x 表名"前缀、删损坏块 |
| 表页被拆成乱序文字 | 未开表格视觉增强 | 检查 `ext.table_vision_enhance=true`，重解析前先删旧 chunk |

## 4. 一页速查

```
【人工】 预检(选不中文字=扫描件, 记横排表页码) → 上传原样PDF(不旋转/不先解析)
         → 配置: Naive + DeepDOC + 表格视觉增强✓ + Img2txt模型
         → 点解析等100% → 审块(删垃圾块/块尾查合并表/补表号题注/修OCR字)
         → 问答验收(正例+反幻觉) → 删重复文档收尾

【代码】  01检测 → 03上传+配置+解析+轮询 → 04拉块建表清单 → 05质检
         → 人工决策(删坏块/修误读) → 06问答复测 → 批量API收尾
```
