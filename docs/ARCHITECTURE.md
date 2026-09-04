# 导入架构总览

- **日期**：2026-09-04（F1 全批收口后整理）
- **定位**：本文档是整个导入系统的**完整技术参考**——覆盖所有文档类型的预处理路径、导入机制、评测方法和关键机制发现。源码级细节指向 `src/` 或 `out/` 具体文件。
- **关联**：`requirements.md`（原始任务要求）、`docs/plans/`（设计文档）、`docs/specs/`（规格说明）

---

## 一、文档类型与导入路径总表

RAGFlow 对不同类型文档有根本不同的处理能力，选择正确的导入路径是质量的关键。

| 文档类型 | 原生解析质量 | 导入路径 | 关键文件 |
|---|---|---|---|
| PDF | ✅ 好（书签/标题/正文分层） | `run_import.py` 直导 naive/laws/paper | `src/run_import.py` |
| Word (.docx) | ✅ 好 | `run_import.py` 直导 | `src/run_import.py` |
| Excel 真 `.xlsx` | ✅ 好（openpyxl 正常路径） | `run_import.py` 直导 | `src/run_import.py` |
| 图片（jpg/png） | ❌ 无法解析 | VLM 文本化 → 壳模式导入 | `src/vision_extract.py` + `src/shell_import.py` |
| 政府 BIFF `.xls` | ❌ 100% 垃圾（Unnamed:/nan） | 壳模式（融合文本替换） | `src/shell_import.py` + `out/xls_fusion/` |

### 1.1 病情定性：F1 是格式病，不是个案病

凡 `.xls`（BIFF 格式）必垃圾——RAGFlow 的 openpyxl 失败后降级到 pandas（header=0），
产生大量 `Unnamed: N`、`nan`、合并单元格日期断裂的行。`.xlsx` 走 openpyxl 正常路径，零垃圾。

**实测（2026-09-03，`out/xls_fusion/TRIAGE_DECISION.md`）**：
- 17 份 BIFF `.xls`：789 块，**100% 垃圾化**
- 6 份真 `.xlsx`（ds3×3 + ds4×3）：105 块，**0% 垃圾**

---

## 二、图片文本化导入（VLM → 壳模式）

### 2.1 完整流程

```
语料 (jpg/png)
    ↓ vision_extract.py  --dry-run / --approve
中间产物 out/vision/raw_*.txt（三方 VLM 输出）
    ↓ cross_check + 人工批准 (approved.flag)
融合文本 fusion/*.md（5 段式：头部 / 数值行 / 关键数值速查 / 常问速查 / 仲裁留痕）
    ↓ import_fusion.py --apply（图片 UNSTART + 元数据 + 手动挂片）
RAGFlow 检索 / chat 引用锚定到原始 .jpg/.png
```

### 2.2 VLM 三方对拍机制

12 张参数/管理图表（`config.py` 的 `VISION_SOURCES`）经三方独立解析：
- **Qwen3.8-27B**（openagp 网关）：密集表格精确率最高
- **mimo-v2.5**（tokenharbor）：诚实、会标注截断、曲线取点能力
- **MiniMax-M3**（api.minimaxi.com）：最快、转写质量高，但倾斜图会领域替换幻觉

判级：T1（三方一致）→ 采用；T2（两源+语料锚点）→ 人工仲裁；T3（单源）→ 人工看原图。

### 2.3 融合文本 5 段式格式

```markdown
> 来源：语料 <rel>（KB 名 <kb_name>）
> 鉴定：calamine 离线读数 + 人工表头校对（<date>，<wave>）

## 数值行（双词形 + 事件前缀 + 日期 forward-fill）
...

## 关键数值速查（双词形）
...

## 常问速查（≤8 条）
问：...?答：...

## 仲裁留痕
（多表拆分/口径分歧/幻觉剔除留痕）
```

**关键工程经验**：
1. **双词形**：每行带 `米（m）`、`万立方米（万m³）`、`立方米每秒（m³/s）`——解决查询词形与索引词形失配
2. **长表拆行**：markdown 大表被 naive 解析器渲染成单体 `<table>` chunk 向量化截断，必须拆逐行 bullet
3. **逐值 QA 行**：`问：水位 786.00 米时库容？答：…` 使自然问句近乎逐字命中
4. **≤8 条常问**：239 行 QA 会把 PDF chunk 挤出 chat top-n，必须控量

### 2.4 PNG/JPG 壳模式机制

**核心发现（v0.27.1）**：
- `PUT /documents/{id}` 传 `status:"0"` **静默不生效**，下架只能删除
- 手动挂载切片**无需 parse 即入检索索引**（UNSTART 壳免解析）
- UNSTART 壳**不过 tag 阶段**，KB 级 `tag_kb_ids` 闸门无需动

**导入步骤**：
1. 上传原图 → 保持 UNSTART（不 parse，无 OCR 分片）
2. `PATCH /documents/{id}` 写 11 字段规范元数据
3. `POST /documents/{id}/chunks` 手动挂融合文本切片
4. `DELETE /documents/{doc_id}/chunks` 删同名 md 文档（防止同文 rank 竞争）

**回滚**：`import_fusion.py --apply` 幂等重导 md（与壳并存）。

---

## 三、新图片资产导入（照片壳）

### 3.1 识别与分类

`src/photo_triage.py` 对 `09-图像与多媒体/01-洪水现场照片` 262 张全量分类：
- **文件扫描件**（conf ≥0.95）：长江委通报、水情通报等 → 进入 VLM 转写管线
- **图表类截图**（conf ≥0.95）：Excel 过程线截图等 → 同上
- **现场照片**（含手写标注）：SKIP_DIRS 排除，不入 KB
- 低置信度：人工仲裁

### 3.2 完整流程

```
照片（文件扫描件/图表截图）
    ↓ M3 全文转写 + 逐句人工放大仲裁（修正 M3 幻觉：郧合→部分河段 等）
fusion/*.md（融合文本，p1/p2 含【原文划线】标注 + 低置信批示块）
    ↓ rollout_photo_shell.py --apply（**新文档流**，非替换流）
    ↓ preflight（语料在 / 融合文本在 / 题库锚点全覆盖）
    ↓ upload 原图 UNSTART（不 parse，无 OCR 分片）
    ↓ PATCH 元数据（10 schema 字段 + rel）
    ↓ POST /chunks 手动挂融合切片
RAGFlow
```

**关键约束**：
- 原图 11–16MB → `prepare_image_bytes` 阈值 8MB，半幅降采样重编码（避免 base64 超限）
- 逐图 json 断点续跑 + `force` 全量重跑
- **不删任何东西**（与 xls 替换流不同）

### 3.3 三件资产状态（2026-09-03）

| 资产 | 融合文本 | 状态 |
|---|---|---|
| 汛旱情通报第27期_长江委_20211005_p1.png | ✅ 含手写批示块 | 待 approved.flag |
| 汛旱情通报第27期_长江委_20211005_p2.png | ✅ 同上 | 待 approved.flag |
| 水库出入库水量过程线_2021年9-10月.jpg | ✅ 含图表 14 日逐值 | 待 approved.flag |

---

## 四、BIFF xls 导入（壳模式）

### 4.1 什么时候走壳模式

**必须走**：17 份 BIFF `.xls` 产生的垃圾块已严重污染 ds3 检索（场次链不可达、数值不可查）。

**判定流程**：
1. `extract_xls.py`（calamine 读数）检查 sheet 结构
2. 如果是 BIFF（openpyxl 失败→pandas 降级路径）且有 `Unnamed:`/`nan`/`——Data` 指纹 → 走壳模式
3. 如果是 `.xlsx` 且内容干净 → 直接 `run_import.py`

### 4.2 完整流程

```
语料 .xls
    ↓ extract_xls.py（docker calamine stdin 通道）
survey/extract_*.json（原始网格）
    ↓ 人工表头校对 + 事件归因 + 双口径处理
fusion/*.md（5 段式）
    ↓ questions_xls.jsonl（锚点题库，preflight 自检）
rollout_xls_shell*.py --apply
    ↓ preflight（语料在 / 融合文本在 / 题库锚点全覆盖）
    ↓ xls_chunks_archive.json（删前全量块存档）
    ↓ DELETE /documents/{id}（删旧 naive 文档）
    ↓ upload 同名 xls UNSTART + PATCH 元数据
    ↓ POST /chunks 手动挂融合切片
    ↓ tag_feas ES 回填（{知识类型:N, 事件维:M} 两维词表）
    ↓ 探针验证（FAIL 即停）
RAGFlow
```

### 4.3 融合文本撰写规范

**6 类结构病灶及处理**：

| 病灶 | 处理方式 |
|---|---|
| 左右双表同 sheet（列 0-14 雨量 / 15-21 弃水） | 仲裁留痕写清楚，数值行注明来源表 |
| 上下双表同 sheet | 同上 |
| 日期纵向承袭（合并单元格 forward-fill 断裂） | 手动 forward-fill 补全 |
| 裸 Excel 序列日期（43333=2018-08-21） | origin 1899-12-30 换算，注名原文 |
| 表头位置不固定（2 级合并） | 人工表头，用实际列名重建 |
| 浮点噪声（7.4879999999999） | `round(val, 2)` |

**元数据 patch 规则**：
- `doc_category` = 洪水资料
- `doc_type` = 表格
- `source_format` = excel
- `flood_event` / `year` / `doc_nature` 按内容裁定（**不走目录名归因**）
- `year=None` 改为实际年份（如 `2008`）需在存档快照注明 `meta_amended`

### 4.4 tag_feas 回填（关键）

壳块 UNSTART 免 parse，不过 tag 阶段，导致**无查询标签**、检索打分 tag_fea=0、竞品 naive 块带 LLM 自由标签系统性胜出。

**修复**：ES `POST /{index}/_update_by_query?refresh=true` 写 `tag_feas` 字段：
```json
{
  "query": {"bool": {"filter": [{"term": {"docnm_kwd": "KB名"}}]}},
  "script": {"source": "ctx._source.tag_feas = params.m", "params": {"m": {"洪水资料": 10, "基础数据": 8}}}
}
```

**词表维度规则（P2-9b）**：两维 `{知识类型, 事件维}` 通用；仅当文档**全部问句面**激活同一查询标签（如防洪减灾统计表激活 `2021-10`）且语义为真时，才补第三维——否则词表模长碰撞（-0.53）会挤掉同类行切片。

---

## 五、预处理模块详解

### 5.1 `src/vision_extract.py`

三方 VLM 对拍 + 交叉校验 + 人工 gate。

```bash
python3 vision_extract.py --limit 2    # dry-run
python3 vision_extract.py --approve    # 人工批准后写 approved.flag
```

关键逻辑：
- `TEXTUALIZED_VALUES` 交叉校验（如"百年一遇泄量 1454 m³/s"）
- 幻觉一律剔除留痕（T3 判级后才可引入）
- 密集表只有 Qwen 可靠（mimo 等差数列幻觉、M3 跨列串行漂移）

### 5.2 `out/xls_fusion/extract_xls.py`

离线读数器，优先本机 `python_calamine`，否则走 docker stdin 通道：

```bash
cat <file.xls> | docker exec -i docker-ragflow-cpu-1 \
  python3 -c 'import pandas as pd, sys; df=pd.read_excel(io.BytesIO(b), engine="calamine", header=None)'
```

产物：`survey/extract_*.json`（原始网格）供逐值复核。

### 5.3 `src/corpus.py`

阶段0 扫描，生成 `out/mapping.csv`：
- SHA-256 + `_dup` 名字双重去重
- 按一级目录指派 `dataset_key`
- 未知目录 abort 并收集列表（**不猜测**）
- `flood_event` 由 `FLOOD_EVENT_BY_SUBDIR` 映射推导（**已知错误需后续纠偏**）

### 5.4 `src/tag_vocab.py` + `src/run_setup.py`

标签库建设（先建先 parse）：
1. `run_setup.py` 创建 tag KB → parse → 等待 DONE
2. 读取 `vocab.txt`（两列：知识类型 + 洪水事件）→ 逐行 `POST /chunks` 手挂标签块
3. ds1–ds5 配置 `tag_kb_ids` + `topn_tags`

---

## 六、导入执行引擎

### 6.1 `src/shell_import.py`（统一壳引擎）

图片与 xls 共用，是所有 wave 脚本的核心：

```python
from shell_import import process, verify, rollback

# 单目标流程
process(client, ds_id, target, corpus_root, questions, apply, state, manifest, archive, state_path, manifest_path)
verify(client, ds_id, kb_name, probe_question, probe_anchor)  # → (passed, rank)
rollback(client, ds_id, kb_name, archive, ...)  # 删现文档→重传原件→naive重解析
```

**preflight 三断言**（任一不满足拒删）：
1. 语料文件存在于 `CORPUS_ROOT`
2. 融合文本 `.md` 存在
3. 题库锚点（`all_keywords` 并集）**全部出现在构建切片**里

**探针验证门**：
- 全返回集（v0.27.1 恒返 30 条，top_k 只影响排序）中目标文档切片含锚点
- 两次机会（`VERIFY_SLEEPS = (5, 8)`）容排序漂移
- **必须用 QA 原句**（自然问句常 >30 名）

### 6.2 wave 脚本模式（`out/xls_fusion/rollout_xls_shell*.py`）

每个 wave 独立台账：
- `TARGETS`：目标列表（KB 名、语料路径、融合文本路径、probe）
- `shell_state*.json`：幂等状态（已完成的跳过）
- `xls_chunks_archive*.json`：删前全量块存档（含原 chunk_method/parser_config/meta）
- `approved*.flag`：人工批准门

```bash
python3 rollout_xls_shell*.py --apply   # 需 approved*.flag
python3 rollout_xls_shell*.py --rollback "KB名" --apply  # 回滚
```

### 6.3 回滚机制

三层护栏：
1. 删前全量块存档（JSON，含原解析配置快照）
2. 回滚路径精确还原（重传 + naive 重解析可精确还原块数）
3. 存档快照 `meta_amended` 字段使 `--rollback` 不倒退裁定

---

## 七、评测体系

### 7.1 检索层评测（`out/xls_fusion/run_eval_xls.py`）

```bash
python3 run_eval_xls.py --phase before   # 基线
python3 run_eval_xls.py --phase after    # 导入后
```

- `search_datasets([ds3], q, top_k=10)` × **3 次取多数**（v0.27.1 单次检索向量噪声漂移）
- 判分：all_keywords 全命中 **且** any_keywords 任一命中 → PASS
- `DS_IDS = [ds3]`，候选池维持服务端默认 64（256 池在 F1 重灾区放进更多高复合分垃圾块）

### 7.2 QC 六问（`src/run_qc.py`）

| Q | 类别 | 口径 |
|---|---|---|
| Q1 | 单跳参数 | `use_kg=true`，期望 1454/m³/s/百年 |
| Q2 | 多跳时序 | `use_kg=true`，期望 2021/洪水 |
| Q3 | 实体召回 | `use_kg=true`，期望 柳林/瑶曲 |
| Q4 | 实体关联 | `use_kg=true`，期望 安芳东/8·16 |
| Q5 | 元数据过滤 | `use_kg=false`，meta_data_filter=2013-7，期望 2013/降雨 |
| Q6 | 快速参数 | `use_kg=false`，期望 788.5/汛限水位 |

**判分**：`decide_pass(numeric_exact, hit10, kw_hits, min_keywords)`
- `numeric_exact`：numbers 任一命中前 10 条 → 通过
- `hit10`：numbers 或 keywords 任一命中前 10 条
- `min_keywords` > 0 时 keywords 命中数须达标

### 7.3 26 题检索/问答（`out/retrieval_tuning/probe_p29.py` 等）

- 检索层：×3 取多数，all_keywords 全命中
- 问答层：`chat` 助手引用锚点检测

### 7.4 pytest（`src/tests/`）

全绿：**206 passed, 1 skipped**。覆盖 config/ragflow_client/shell_import/run_qc 等核心模块。

---

## 八、关键机制发现（v0.27.1）

以下均为**试点换来的实战发现**，代码中无明确文档：

### 8.1 tag 阶段闸门只读 KB 级 `tag_kb_ids`

v0.27.1 的 tag 阶段在 `task_executor.py:576` 调用 `content_tagging`，
**仅读取 KB 级 `parser_config.tag_kb_ids`**。doc 级置 `[]` 无效，空数组被 deep-merge 丢弃。
xls 行块几乎全落 LLM 兜底（12 块小表 200+ 次调用）。

### 8.2 Excel 加载三层降级

openpyxl（失败）→ pandas（失败）→ **calamine**（成功）。只有 calamine 能读这批损坏 BIFF。

### 8.3 Redis 队列消息重启后被重新领走执行

在途 parse 与人工重发任务并发双跑（表现：双任务接连完成 + chunk_num 虚增）。
重启前必须确认队列清空。

### 8.4 `PATCH /documents/{id}` 传 `status:"0"` 静默不生效

下架只能删除，**无 stop-parse 端点**（DELETE parse → 405）。

### 8.5 content_ltks 分词器将完整词拆分

ES `content_ltks` 字段使用ik_max_word分词，`汛限水位` 被拆为 `汛/限/水位` 三个 token，
导致 match 完整词"汛限"零命中。**tag_feas 回填后 rerank 将 carrier chunk 推入 top10**。

### 8.6 meta_data_filter 静默失效的双层根因

**层一**：REST 契约——v0.27.1 的 `apply_meta_data_filter` 的 manual 分支读
`meta_data_filter["manual"]` 而非 `"conditions"`。客户端 `_normalize_meta_data_filter()` 已修复。

**层二**：doc_meta 索引 mapping——v0.27.0 时代建的索引 `meta_fields.*` 是裸 keyword，
无 `.keyword` 子字段，pushdown `_term_or_match` 对字符串硬编码查 `.keyword` → term 落不存在字段恒 0。
修复：11 个 schema 字段 PUT `_mapping` 补 `.keyword` 多字段 + `_update_by_query` 回填。

### 8.7 `questions` 字段是最强检索加权通道

打分层 `tks=content+title×2+important_kwd×5+question_tks×6`（search.py:485），
ES 层 `question_tks^20 boost`（query.py:37）。经 `patch_chunk(questions=[...])` 写入块后，
目标块从 rank 30+ 直接 rank1×3——"QA 原句 rank1 规律"的机制化实现。

### 8.8 v0.27.1 retrieval 实际返回 30 条

`top_k=10` 只影响排序，不影响条数；评测脚本必须切片 `[:10]`。

### 8.9 无 chunk 级 DELETE 端点

只有 document 级 DELETE。chunk 级修正（790.30→790.5）只能走 `PATCH /chunks` 就地修改。

---

## 九、目录结构速查

```
opt/wangjz/ragflow-import/
├── src/
│   ├── config.py          # 唯一配置源（DATASETS/ENUM/METADATA_SCHEMA/FLOOD_EVENT_BY_SUBDIR）
│   ├── corpus.py          # 阶段0 扫描 → mapping.csv
│   ├── run_import.py      # 阶段3 主导入（upload→meta→parse，幂等断点续）
│   ├── run_setup.py       # 阶段2 建库（tag KB→parse→inject tag_kb_ids→schema）
│   ├── run_qc.py          # 阶段4 QC 六问验收
│   ├── shell_import.py    # 统一壳引擎（preflight/verify/process/rollback）
│   ├── vision_extract.py  # VLM 图表文本化（三方对拍+人工gate）
│   ├── photo_triage.py    # 照片分类（VLM 批量 + 低置信度人工仲裁）
│   ├── ragflow_client.py  # RAGFlow API 封装（Bearer 免登录/RSA 登录）
│   └── tests/             # pytest 全绿（206 passed, 1 skipped）
├── out/
│   ├── xls_fusion/        # xls 壳迁移全波次资产
│   │   ├── fusion/        # 融合文本（.md，5 段式）
│   │   ├── survey/        # calamine 读数原始网格
│   │   ├── rollout_xls_shell*.py  # 各 wave 入口（TARGETS/CLI/approved*.flag）
│   │   ├── driver_wave*.py        # 引擎+ES回填+探针门
│   │   ├── extract_xls*.py        # 各 wave 读数器
│   │   ├── questions_xls.jsonl    # 锚点题库（锚点 preflight 自检）
│   │   ├── run_eval_xls.py        # before/after 检索评测
│   │   └── xls_chunks_archive*.json  # 删前块存档
│   ├── vision_fusion/     # 图表融合文本（12/12 完成）
│   ├── photo_triage/      # 照片分类结果+extract
│   ├── retrieval_tuning/  # P1-4/P2-9/P1-5 等检索层补丁
│   └── qc/                # QC 六问报告（6/6）
├── docs/
│   ├── requirements.md    # 原始任务要求
│   ├── ARCHITECTURE.md    # 本文档
│   ├── archive-feedback-2026-09-03.md  # 档案级错误反馈清单
│   └── plans/specs/       # 设计文档
└── .env                   # 凭据（RAGFLOW_API_KEY/LLM_API_KEY，已 gitignore）
```

---

## 十、常见问题速查

| 问题 | 原因 | 解决方案 |
|---|---|---|
| Q6 "汛限水位" FAIL | content_ltks 分词器将完整词拆分 | tag_feas 回填 carrier chunk 入 top10 |
| Q5 过滤返回 0 条 | meta_data_filter 键名错误（conditions→manual） | `_normalize_meta_data_filter()` 修复 |
| 壳块检索竞品弱 | UNSTART 不过 tag 阶段，无 tag_feas | ES update_by_query 回填词表 |
| xls 读数失败 | 本机无 calamine | docker stdin 通道（docker exec -i docker-ragflow-cpu-1） |
| 文档名重复 RAGFlow 自动加后缀 | 同名上传自动 (1)(2) | KB 名不可反推语料路径，用 import_state 对齐 |
| 解析卡 0.0083 不动 | Redis 在途任务挂起 | 重启容器前确认队列清空 |
| P2-9b 词表碰撞 | 三维词表模长 -0.53 挤掉同类行切片 | 补第三维前先确认文档全部问句面激活同一标签 |
