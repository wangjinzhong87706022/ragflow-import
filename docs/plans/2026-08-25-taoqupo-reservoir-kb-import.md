# 桃曲坡水库知识库 RAGFlow 导入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将桃曲坡水库 222 个运营档案文件（110 个可导入 txt）分 5 个 dataset + 1 个标签库导入本地 RAGFlow v0.27.0 实例，支持值班问答、工程统计、综合研判三类场景。

**Architecture:** Python CLI 工具链写入 `/home/scada/SmartTwinRes-skills/ragflow_import/`，所有输出写 `out/`；`/opt/git/ragflow` 只读（仅引用 `conf/public.pem` 做 RSA 登录加密）；提交在 `SmartTwinRes-skills` 仓库。

**Tech Stack:** Python 3.11+ / requests / pycryptodome/pytest；RAGFlow v1 REST API（session cookie 认证，前缀 `/api/v1`，复数 `datasets`）；RSA PKCS1_v1_5 + base64 密码加密（镜像 `crypt.py:27-36`）。

**Spec:** `docs/superpowers/specs/2026-08-25-taoqupo-reservoir-kb-design.md`（v2，2026-08-25）

---

## Global Constraints

- **代码只写入** `/home/scada/SmartTwinRes-skills/ragflow_import/`（不在 `/opt/git/ragflow`）
- **RAGFlow 只读**：`/opt/git/ragflow/conf/public.pem` 仅作 RSA 加密引用；`/opt/git/ragflow` 不修改
- **提交在** `SmartTwinRes-skills` 仓库（`cd /home/scada/SmartTwinRes-skills && git add ragflow_import/... && git commit`）
- **语料根只读**：`/home/scada/SmartTwinRes-skills/pdf_text_analysis/`（派生文本，222 文件）、`/home/scada/SmartTwinRes-skills/pdfs/`（原始资料，仅读）
- **产物写** `out/`（映射 CSV、预处理产物、导入状态文件、QC 报告）
- **凭据**：经环境变量 `RAGFLOW_EMAIL`/`RAGFLOW_PASSWORD` 注入，不入库
- **venv 依赖**：`requests` `pycryptodome` `pytest`
- **API 端点**：`http://localhost:9380/api/v1`，session cookie 认证
- **零上游改动**：提示词定制为可选二阶段，本计划不含
- **导入顺序**：标签库(ds0) → dataset 1规程 → dataset 2基础 → dataset 3洪水 → dataset 4管理 → dataset 5报告

---

## 文件结构

```
/home/scada/SmartTwinRes-skills/ragflow_import/
├── config.py              # 所有常量：DATASETS、TOP_LEVEL_ASSIGNMENTS、FLOOD_EVENT_BY_SUBDIR、METADATA_SCHEMA
├── corpus.py              # Entry dataclass、classify()、scan()、dedupe、native xlsx 匹配、写 mapping CSV
├── tables.py              # lines_to_markdown()、generate_all() 输出 .md 和 .qa.md
├── tag_vocab.py            # 标签库词表生成（TAB 分隔 txt）+ 解析验证
├── vision_extract.py      # 离线 VLM 识别 + 交叉校验 + approved.flag 机制
├── ragflow_client.py      # RSA 登录、Dataset CRUD、文档上传/元数据/解析、状态轮询
├── run_setup.py           # 幂等建库（ds0 标签库 + ds1-5 + metadata schema）
├── run_import.py          # 三步导入（upload → metadata → parse），断点续命
├── run_qc.py              # 人工验收问题集 + datasets/search 检索验证
├── requirements.txt
└── tests/
    ├── test_config.py
    ├── test_corpus.py
    ├── test_tables.py
    ├── test_tag_vocab.py
    └── test_ragflow_client.py

out/                       # 运行时产物（脚本自行创建）
├── mapping.csv            # 222 文件映射表（含 native_xlsx_path、skip_reason）
├── generated_tables/      # tables.py 产物
├── vision/                # vision_extract.py 产物 + approved.flag
└── import_state.json      # run_import.py 断点状态
```

---

## 任务详解

### Task 1: config.py — 全局常量与配置字典

**Files:**
- Create: `/home/scada/SmartTwinRes-skills/ragflow_import/config.py`
- Test: `/home/scada/SmartTwinRes-skills/ragflow_import/tests/test_config.py`

**Interfaces:**
- Produces: `DATASETS`（5 项 dict，含 name/chunk_method/parser_config/graphrag_config/raptor_config）、`TAG_KB_NAME`、`METADATA_SCHEMA`（11 字段 list）、`TOP_LEVEL_ASSIGNMENTS`（dict：11 个顶层文件名 → ds_key）、`FLOOD_EVENT_BY_SUBDIR`（dict：子目录名 → flood_event 字符串）、`QA_TABLE_KEYWORDS`（list）、`SKIP_DIRS`（set）、`VISION_SOURCES`（list of tuples）、`TEXTUALIZED_VALUES`（dict）、`LOCATION_KEYWORDS`（longest-first sorted list）、`DEPT_KEYWORDS`（list）、`IMPORT_COLS`（list）

**Constants that must be exact (copied from spec):**

```python
# API
API_BASE = "http://localhost:9380/api/v1"
RAGFLOW_EMAIL = os.environ["RAGFLOW_EMAIL"]
RAGFLOW_PASSWORD = os.environ["RAGFLOW_PASSWORD"]
PUBLIC_PEM = "/opt/git/ragflow/conf/public.pem"

# Corpus roots (read-only)
DERIVED_ROOT = Path("/home/scada/SmartTwinRes-skills/pdf_text_analysis")
ORIGINALS_ROOT = Path("/home/scada/SmartTwinRes-skills/pdfs")

# Output root
OUT_DIR = Path("/home/scada/SmartTwinRes-skills/ragflow_import/out")

# Datasets (spec §2.1 + §2.2)
DATASETS = [
    {
        "key": "ds1",
        "name": "规程与预案",
        "chunk_method": "laws",
        "parser_config": {
            "chunk_token_num": 512,
            "auto_keywords": 10,
            "auto_questions": 3,
            "topn_tags": 3,
            "tag_kb_ids": [],   # filled by run_setup.py after tag KB is created
        },
        "graphrag_config": {
            "use_graphrag": True,
            "method": "light",
            "entity_types": ["FloodEvent", "Station", "Structure", "Person", "Regulation", "Parameter"],
            "resolution": True,
        },
        "raptor_config": {"use_raptor": True, "max_leaf_nodes": 10},
    },
    # ... ds2-ds4 without graphrag, ds5 with raptor only (see spec §2.2)
]
TAG_KB = {"key": "ds0", "name": "桃曲坡标签库", "chunk_method": "tag"}

# 11 metadata fields (spec §4.1)
METADATA_SCHEMA = [
    {"key": "doc_category",   "type": "string",  "description": "文档大类",           "enum": ["规程预案","基础数据","洪水资料","组织管理","工程资料"]},
    {"key": "sub_category",   "type": "string",  "description": "子类",               "enum": None},
    {"key": "flood_event",    "type": "string",  "description": "关联洪水事件",        "enum": ["2021-10","2020-8","2019-7","2013-7","2008-8","其他","历年统计"]},
    {"key": "doc_type",       "type": "string",  "description": "文档形态",           "enum": ["文本","表格","图片","图纸"]},
    {"key": "year",           "type": "number",  "description": "年份",               "enum": None},
    {"key": "source_format",  "type": "string",  "description": "来源格式",           "enum": ["pdf","word","excel","ocr_jpg","ocr_png","native_xlsx"]},
    {"key": "quality",        "type": "string",  "description": "OCR质量分级",         "enum": ["high","medium","low"]},
    {"key": "responsible_dept","type": "string",  "description": "责任/发文部门",        "enum": None},
    {"key": "doc_nature",     "type": "string",  "description": "文件性质",           "enum": ["法规","技术","管理","统计"]},
    {"key": "location",       "type": "string",  "description": "工程部位",            "enum": ["主坝","副坝一","副坝二","溢洪道","高洞","低洞","放水塔","库区","全库"]},
    {"key": "flood_magnitude","type": "string",  "description": "洪水量级",            "enum": ["百年一遇","千年一遇","一般洪水","不适用"]},
]

# Top-level file assignments (spec §6 阶段0, 11 files)
TOP_LEVEL_ASSIGNMENTS = {
    "2026年桃曲坡水库调度规程_1__1_.pdf.txt":          "ds1",
    "2026年桃曲坡水库大坝安全管理应急预案_1_.pdf.txt":  "ds1",
    "2026年度桃曲坡水库汛期调度运用计划_2_.pdf.txt":    "ds1",
    "2026年桃曲坡水库防洪抢险应急预案_1_.pdf.txt":      "ds1",
    "_桃曲坡等三座大坝安全评价报告_.pdf.txt":           "ds1",
    "桃曲坡水库大坝安全鉴定_OCR.txt":                   "ds1",
    "关于转发_水利部办公厅关于切实做好渭河流域暴雨洪水防御工作的通知_的通知_陕水防明电_2021_7.pdf.txt": "ds1",
    "陕西省桃曲坡灌区水利工程管理范围及保护范围划界报告.pdf.txt": "ds2",
    "水情通报第161期.pdf.txt":                         "ds3",
    "重要水情快报_第121期_-总结预测.pdf.txt":           "ds3",
    "汛情专报_20211006_OCR.txt":                       "ds3",
}

# Flood event by subdir (spec §6 阶段0, flood_event field derivation)
FLOOD_EVENT_BY_SUBDIR = {
    "02-2021年洪水调度": "2021-10",   # 10-3 flood is dominant; 9-25 captured via filename
    "03-2020年洪水(8-16)": "2020-8",
    "04-2019年洪水(9-14)": "2019-7",  # filename indicates Sept (9-14)
    "05-2013年洪水(7-22)": "2013-7",
    "06-2008年洪水(8-22)": "2008-8",
    "07-其他洪水事件":       "其他",
    "08-历年洪水统计":      "历年统计",
}

# QA table detection keywords (spec §6 阶段1, for .qa.md generation)
QA_TABLE_KEYWORDS = [
    "降雨量统计", "水位库容曲线推求", "防洪调度效益统计", "洪水统计",
    "洪水过程", "受损统计", "弃水计算", "下泄水量统计",
]

# VLM vision sources (spec §6 阶段1, offline multimodal)
# Format: (relative_path_under ORIGINALS_ROOT, descriptive_name)
VISION_SOURCES = [
    ("05-基础数据与曲线/05-库容水位对照表.jpg", "库容水位对照表"),
    ("05-基础数据与曲线/06-泄流曲线.jpg",       "泄流曲线"),
]

# Textualized known values for cross-check (spec §6 阶段1)
TEXTUALIZED_VALUES = {
    "百年一遇泄量": "1454 m³/s",
    "千年一遇泄量": "2218 m³/s",
    "汛限水位":     "788.5 m",
}

# Longest-first for multi-pattern extraction
LOCATION_KEYWORDS = sorted([
    "主坝","副坝一","副坝二","溢洪道","高洞","低洞","放水塔","库区","全库",
], key=len, reverse=True)

DEPT_KEYWORDS = [
    "防汛办","管理局","设计院","水务局","应急管理局","水利局",
    "桃曲坡水库管理局","陕西省桃曲坡灌区",
]

SKIP_DIRS = {"video_analysis", "extracted", "repaired"}
```

- [ ] **Step 1: Write the failing test — all constants exist and have correct types**

```python
# tests/test_config.py
import sys; sys.path.insert(0, "..")
from config import (
    DATASETS, TAG_KB, METADATA_SCHEMA, TOP_LEVEL_ASSIGNMENTS,
    FLOOD_EVENT_BY_SUBDIR, QA_TABLE_KEYWORDS, VISION_SOURCES,
    TEXTUALIZED_VALUES, LOCATION_KEYWORDS, SKIP_DIRS, IMPORT_COLS,
    DERIVED_ROOT, ORIGINALS_ROOT, OUT_DIR, API_BASE,
)

def test_datasets_have_required_keys():
    for ds in DATASETS:
        assert "key" in ds and "parser_config" in ds
    tag = TAG_KB
    assert tag["chunk_method"] == "tag"

def test_metadata_schema_11_fields():
    assert len(METADATA_SCHEMA) == 11
    keys = {f["key"] for f in METADATA_SCHEMA}
    assert "flood_event" in keys
    assert "location" in keys

def test_top_level_assignments_11_files():
    assert len(TOP_LEVEL_ASSIGNMENTS) == 11
    for fname, ds_key in TOP_LEVEL_ASSIGNMENTS.items():
        assert ds_key in {"ds1","ds2","ds3"}

def test_flood_event_subdirs():
    assert FLOOD_EVENT_BY_SUBDIR["02-2021年洪水调度"] == "2021-10"
    assert "video_analysis" in SKIP_DIRS

def test_vision_sources_exist():
    for path, name in VISION_SOURCES:
        assert (ORIGINALS_ROOT / path).exists(), f"{path} not found"

def test_import_cols():
    assert "rel" in IMPORT_COLS
    assert "dataset_key" in IMPORT_COLS
```

- [ ] **Step 2: Run test to verify it fails (module doesn't exist yet)**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_config.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation — config.py with all constants above**

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/scada/SmartTwinRes-skills
git add ragflow_import/config.py ragflow_import/tests/test_config.py
git commit -m "feat(taoqupo): add config.py with all constants per spec v2"
```

---

### Task 2: corpus.py — 语料扫描、去重、映射表生成

**Files:**
- Create: `/home/scada/SmartTwinRes-skills/ragflow_import/corpus.py`
- Test: `/home/scada/SmartTwinRes-skills/ragflow_import/tests/test_corpus.py`

**Interfaces:**
- Consumes: `config.DERIVED_ROOT`, `config.ORIGINALS_ROOT`, `config.TOP_LEVEL_ASSIGNMENTS`, `config.FLOOD_EVENT_BY_SUBDIR`, `config.QA_TABLE_KEYWORDS`, `config.SKIP_DIRS`
- Produces: `Entry` dataclass, `classify(rel_path) → str` (returns ds_key), `scan() → list[Entry]`, `write_mapping_csv(entries, path)`, `read_mapping_csv(path) → list[dict]`, `build_manifest(entries) → list[dict]`

```python
# Entry dataclass (one row in mapping CSV)
@dataclass
class Entry:
    rel: str                      # relative path under DERIVED_ROOT
    native_xlsx_path: str | None   # absolute path to pdfs/ native xlsx, or None
    dataset_key: str               # ds1..ds5
    doc_category: str
    sub_category: str
    flood_event: str | None
    doc_type: str                  # 文本/表格/图片/图纸
    year: int | None
    source_format: str
    quality: str
    responsible_dept: str | None
    doc_nature: str
    location: str | None
    flood_magnitude: str
    skip_reason: str | None        # None = importable; set if skipped
    duplicate_of: str | None      # rel of canonical file if this is a dup
    sha256: str                    # hex digest of file content

IMPORT_COLS = [
    "rel","native_xlsx_path","dataset_key","doc_category","sub_category",
    "flood_event","doc_type","year","source_format","quality",
    "responsible_dept","doc_nature","location","flood_magnitude",
    "skip_reason","duplicate_of","sha256",
]
```

**classify(rel) rules (in priority order):**
1. `rel` is a known top-level file in `TOP_LEVEL_ASSIGNMENTS` → return that ds_key
2. `"/"` in `rel` and `rel.split("/")[0]` matches a top-level category prefix:
   - `03-施工图纸与设计_*` → `ds2`
   - `04-确权划界_*` → `ds2`
   - `05-基础数据与曲线_*` → `ds2`
   - `06-` subdir starts with `FLOOD_EVENT_BY_SUBDIR` key → `ds3`
   - `07-管理资料_*` → `ds4`
   - `08-政策文件_*` → `ds4`
3. `rel` matches `_dupx/`/`x` suffix → mark `skip_reason="duplicate"` after dedupe
4. `rel` in `video_analysis/` → mark `skip_reason="video_analysis"`
5. Any other file → raise `ValueError(f"Unknown file: {rel}")` (脚本遇未匹配文件必须报错终止，不得猜测)

**Dedup strategy (spec §6 阶段0):**
- Group by `sha256 + dataset_key`
- Within each group keep the entry with the shortest `rel`; mark others as `duplicate_of=<shortest_rel>`
- The `_dupx`/`x` suffix files must be grouped with their non-dup counterpart

**native_xlsx_for(rel, ds_key) — progressive stem-suffix match:**
Given `rel = "06-历年洪水资料_08-历年洪水统计_弃水量统计表（含生态）.txt"`:
1. Try `ORIGINALS_ROOT/06-历年洪水资料/08-历年洪水统计/弃水量统计表（含生态）.xlsx`
2. Try `ORIGINALS_ROOT/06-历年洪水资料/08-历年洪水统计/弃水量统计表.xlsx`
3. Try `ORIGINALS_ROOT/06-历年洪水资料/08-历年洪水统计/弃水量统计表.xls`
4. Try the `xlsx` / `xls` variants without `(含生态)`
5. None found → return `None`

If native xlsx is found AND `QA_TABLE_KEYWORDS` any kw in `rel`:
- Set `skip_reason = None` (import the xlsx instead via a separate synthetic Entry)
- The synthetic Entry has `dataset_key=ds3`, `doc_type="表格"`, `source_format="native_xlsx"`, `native_xlsx_path = found_path`

**Quality assignment:**
- `source_format` contains `ocr_jpg` or `ocr_png` → `quality = "low"`
- `source_format` in `{"pdf","word","excel","native_xlsx"}` → `quality = "high"`
- otherwise → `quality = "medium"`

**Flood event derivation from rel:**
- If top-level file: `flood_event` stays `None` unless it's a flood-related file in TOP_LEVEL_ASSIGNMENTS (e.g. 汛情专报 → "2021-10" hardcoded)
- If in `06-` subdir: look up parent subdir in `FLOOD_EVENT_BY_SUBDIR`
- For `09-25洪水`/`10-3洪水` sub-subdirs in `02-2021年洪水调度`: the subdir name takes precedence, so `09-25洪水` → `"2021-9"`, `10-3洪水` → `"2021-10"`

**Implementation note:** When `rel` contains both a `FLOOD_EVENT_BY_SUBDIR` subdir key AND a sub-subdir like `9-25洪水` or `10-3洪水`, parse the sub-subdir first to get the correct flood_event. Use regex to detect `(\d+)-(\d+)洪水` pattern.

**write_mapping_csv(entries, path):** write `out/mapping.csv` with `IMPORT_COLS` header + one row per Entry (including synthetic ones). Use csv.DictWriter.

**read_mapping_csv(path):** parse back to list[dict] for run_import.py consumption.

**build_manifest(entries):** return all entries where `skip_reason is None` (i.e. files to actually import).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_corpus.py
import sys; sys.path.insert(0, "..")
from corpus import Entry, classify, scan, write_mapping_csv, read_mapping_csv, build_manifest
import tempfile, csv

def test_classify_top_level():
    assert classify("2026年桃曲坡水库调度规程_1__1_.pdf.txt") == "ds1"
    assert classify("陕西省桃曲坡灌区水利工程管理范围及保护范围划界报告.pdf.txt") == "ds2"
    assert classify("水情通报第161期.pdf.txt") == "ds3"

def test_classify_subdir():
    assert classify("03-施工图纸与设计_05-数字孪生项目建设方案x.txt") == "ds2"
    assert classify("07-管理资料_04-设备管理_桃曲坡平台设备管理x.txt") == "ds4"

def test_classify_unknown_raises():
    import pytest
    with pytest.raises(ValueError, match="Unknown file"):
        classify("03-施工图纸与设计_05-数字孪生项目建设方案x.txt".replace("03-","99-"))

def test_flood_event_2021_subdirs():
    # 9-25 subdir maps to 2021-9
    entry_9 = next(e for e in scan() if "/9-25洪水/" in e.rel)
    assert entry_9.flood_event == "2021-9"
    # 10-3 subdir maps to 2021-10
    entry_10 = next(e for e in scan() if "/10-3洪水/" in e.rel)
    assert entry_10.flood_event == "2021-10"

def test_scan_smoke():
    entries = scan()
    rels = [e.rel for e in entries]
    # all video_analysis skipped
    assert not any("video_analysis" in r for r in rels)
    # 110 importable txt files (excluding video_analysis/dupx/swap files)
    importable = [e for e in entries if e.skip_reason is None]
    assert len(importable) == 110, f"Expected 110, got {len(importable)}"

def test_dedupe_same_content():
    # Two files with same sha256 in same ds should mark one as duplicate_of
    from collections import Counter
    from corpus import scan
    entries = scan()
    by_sha = {}
    for e in entries:
        if e.sha256 not in by_sha:
            by_sha[e.sha256] = []
        by_sha[e.sha256].append(e)
    for sha, group in by_sha.items():
        if len(group) > 1:
            non_dup = [e for e in group if e.duplicate_of is None]
            assert len(non_dup) == 1, f"sha {sha}: expected 1 canonical, got {len(non_dup)}"
            for e in group:
                if e.duplicate_of:
                    assert e.duplicate_of == non_dup[0].rel

def test_mapping_csv_roundtrip():
    entries = scan()[:5]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        write_mapping_csv(entries, f.name)
    loaded = read_mapping_csv(f.name)
    assert len(loaded) == 5
    assert loaded[0]["rel"] == entries[0].rel
    import os; os.unlink(f.name)

def test_build_manifest_excludes_skipped():
    entries = scan()
    manifest = build_manifest(entries)
    assert all(e.skip_reason is None for e in manifest)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_corpus.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement corpus.py**

Include:
- `sha256_hex(path)` — read file bytes, hash, return hex string
- `strip_dup_suffix(name)` — remove `_dupx`/`x` suffix before hashing for grouping
- `find_native_xlsx(rel)` — progressive stem-suffix match logic above
- `parse_flood_event(rel)` — regex for sub-subdir pattern `(\d+)-(\d+)洪水` then FLOOD_EVENT_BY_SUBDIR lookup
- `parse_year(rel)` — first 4-digit consecutive number in filename
- `Entry` dataclass with all fields
- `classify(rel)` — the priority-order rules above
- `scan()` — walk DERIVED_ROOT, skip SKIP_DIRS, build Entry list, dedupe, add synthetic xlsx entries
- `write_mapping_csv` / `read_mapping_csv`
- `build_manifest`

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_corpus.py -v`
Expected: PASS (smoke test may need adjustment if corpus counts differ)

- [ ] **Step 5: Commit**

```bash
cd /home/scada/SmartTwinRes-skills
git add ragflow_import/corpus.py ragflow_import/tests/test_corpus.py
git commit -m "feat(taoqupo): add corpus.py scan/dedupe/mapping per spec §6 阶段0"
```

---

### Task 3: tables.py — 表格 Markdown 生成与 Q/A 行抽取

**Files:**
- Create: `/home/scada/SmartTwinRes-skills/ragflow_import/tables.py`
- Test: `/home/scada/SmartTwinRes-skills/ragflow_import/tests/test_tables.py`

**Interfaces:**
- Consumes: `config.OUT_DIR`, entries from `corpus.py`
- Produces: `lines_to_markdown(lines: list[str]) → str`, `markdown_table_doc(title: str, headers: list[str], rows: list[list[str]]) → str`, `qa_rows(title: str, headers: list[str], rows: list[list[str]]) → list[tuple[str,str]]`, `generate_all(entries: list[Entry], out_dir: Path)`

**lines_to_markdown(lines) rules:**
1. Split each line on `\t` first; if splits to exactly 2+ cols → Markdown table row
2. Else split on 2+ consecutive spaces; if splits to exactly 2+ cols → Markdown table row
3. Else → verbatim line in a fenced code block (preserve all digits/symbols)
4. Detect table header: first row with more numeric cells than text cells → use as header
5. Strip trailing whitespace from each cell

**markdown_table_doc(title, headers, rows):**
```markdown
# 表：{title}

| {' | '.join(headers)} |
| {' | '.join(['---'] * len(headers))} |
{foreach row: | {' | '.join(row)} |}
```

**qa_rows(title, headers, rows) → list[(question, answer)]:**
For each numeric row (rows where >50% cells are numeric), generate:
- Question: `{title}中，某行的第{col_idx}列数值是多少？` (actually use meaningful col names if headers present)
- Answer: the full row as formatted string

Example (降雨量统计):
```
Q: 桃曲坡水库2021年10月3日洪水期间，各站降雨量统计结果是多少？
A: 柳林站: 52.3mm | 瑶曲站: 38.7mm | 庙湾站: 45.1mm | 马栏站: 41.2mm
```

**generate_all(entries, out_dir):**
1. Filter entries to those with `native_xlsx_path is None` AND `doc_type == "文本"` AND any QA_TABLE_KEYWORD in rel
2. For each: read lines from `DERIVED_ROOT / rel`, call `lines_to_markdown`, save to `out_dir/generated_tables/{stem}.md`
3. If QA keyword in rel: also call `qa_rows`, save `.qa.md` alongside
4. Filter entries with `native_xlsx_path not None`: copy the xlsx to `out_dir/generated_tables/{stem}.xlsx`
5. Create `out_dir/generated_tables/README.md` listing all outputs

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tables.py
import sys; sys.path.insert(0, "..")
from tables import lines_to_markdown, markdown_table_doc, qa_rows
import tempfile, pathlib

def test_tab_separated():
    lines = ["站名\t降雨量mm", "柳林\t52.3", "瑶曲\t38.7"]
    md = lines_to_markdown(lines)
    assert "站名" in md and "柳林" in md and "52.3" in md

def test_space_separated():
    lines = ["站名  降雨量mm", "柳林  52.3"]
    md = lines_to_markdown(lines)
    assert "柳林" in md

def test_fenced_block():
    lines = ["这是一段普通文本", "包含数字123和456"]
    md = lines_to_markdown(lines)
    assert "```" in md

def test_markdown_table_doc():
    doc = markdown_table_doc("柳林站降雨量", ["站名","雨量"], [["柳林","52.3"]])
    assert "# 表：柳林站降雨量" in doc
    assert "| 站名 | 雨量 |" in doc

def test_qa_rows_numeric():
    headers = ["站名","降雨量mm"]
    rows = [["柳林","52.3"], ["瑶曲","38.7"]]
    qas = qa_rows("降雨量统计", headers, rows)
    assert len(qas) >= 1
    q, a = qas[0]
    assert "柳林" in a or "52.3" in a
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_tables.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement tables.py**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_tables.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/scada/SmartTwinRes-skills
git add ragflow_import/tables.py ragflow_import/tests/test_tables.py
git commit -m "feat(taoqupo): add tables.py for markdown and Q/A generation"
```

---

### Task 4: tag_vocab.py — 标签库词表生成与解析

**Files:**
- Create: `/home/scada/SmartTwinRes-skills/ragflow_import/tag_vocab.py`
- Test: `/home/scada/SmartTwinRes-skills/ragflow_import/tests/test_tag_vocab.py`

**Interfaces:**
- Consumes: `config.TAG_KB["name"]`, `config.OUT_DIR`
- Produces: `write_vocab_txt(path: Path)`, `parse_vocab_txt(path: Path) → list[tuple[desc, tags]]`

**VOCAB_ROWS (13 rows, TAB-separated, spec §5.1):**

```
规程预案	规程预案
基础数据	基础数据
洪水资料	洪水资料
组织管理	组织管理
工程资料	工程资料
2021-10	2021-10
2020-8	2020-8
2019-7	2019-7
2013-7	2013-7
2008-8	2008-8
其他	其他
历年统计	历年统计
(以上知识类型 + 洪水事件层，以下为 filler 示例行)
```

**write_vocab_txt(path):**
Write 13 rows as UTF-8 TAB-separated. The tag in column 2 replaces `.` with `_` per spec §5.1 (tag.py:31 — but our tags contain no dots, so no-op).

**parse_vocab_txt(path) → list[(description: str, tags: str)]:**
Parse TAB/comma delimited rows (mirroring tag.py chunk() logic: `tab >= comma` → use TAB delimiter). Return list of (description, tags) tuples. Used by tests to verify roundtrip.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tag_vocab.py
import sys; sys.path.insert(0, "..")
from tag_vocab import write_vocab_txt, parse_vocab_txt, VOCAB_ROWS
import tempfile, pathlib

def test_vocab_rows_count():
    assert len(VOCAB_ROWS) == 13

def test_write_and_parse_roundtrip():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        write_vocab_txt(pathlib.Path(f.name))
    rows = parse_vocab_txt(pathlib.Path(f.name))
    assert len(rows) == 13
    assert rows[0][0] == "规程预案"
    assert rows[0][1] == "规程预案"
    import os; os.unlink(f.name)

def test_knowledge_type_rows():
    knowledge_rows = [r for r in VOCAB_ROWS if r[1] in {"规程预案","基础数据","洪水资料","组织管理","工程资料"}]
    assert len(knowledge_rows) == 5

def test_flood_event_rows():
    flood_rows = [r for r in VOCAB_ROWS if r[1] in {"2021-10","2020-8","2019-7","2013-7","2008-8","其他","历年统计"}]
    assert len(flood_rows) == 7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_tag_vocab.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement tag_vocab.py**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_tag_vocab.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/scada/SmartTwinRes-skills
git add ragflow_import/tag_vocab.py ragflow_import/tests/test_tag_vocab.py
git commit -m "feat(taoqupo): add tag_vocab.py with 13-row controlled vocabulary"
```

---

### Task 5: vision_extract.py — 离线 VLM 识别与交叉校验

**Files:**
- Create: `/home/scada/SmartTwinRes-skills/ragflow_import/vision_extract.py`
- Test: `/home/scada/SmartTwinRes-skills/ragflow_import/tests/test_vision_extract.py` (mock-only; no real VLM calls)

**Interfaces:**
- Consumes: `config.VISION_SOURCES`, `config.ORIGINALS_ROOT`, `config.TEXTUALIZED_VALUES`, `config.OUT_DIR`
- Produces: `build_payload(image_bytes: bytes, prompt: str) → dict` (OpenAI vision `image_url` format with base64), `call_vision(payload: dict, endpoint: str, api_key: str) → str`, `cross_check(vlm_text: str) → dict`, `main()` (CLI), `--approve` flag

**build_payload(image_bytes, prompt):**
Return OpenAI-compatible `{"role":"user","content":[{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}},{"type":"text","text":prompt}]}`.

**call_vision(payload, endpoint, api_key):**
`POST endpoint + "/chat/completions"`, headers `Authorization: Bearer {api_key}`, timeout 120s. Return `response["choices"][0]["message"]["content"]`. Transport injectable (default `requests.post`) for test mocking.

**cross_check(vlm_text: str) → dict:**
Returns `{"found": {key: bool}, "values": {key: str}}`:
- Check each key in `TEXTUALIZED_VALUES` (e.g. `"1454"`, `"2218"`, `"788.5"`) appears in `vlm_text`
- Also detect if the value appears in wrong context (e.g. wrong units)

**main() — CLI:**
```
python vision_extract.py [--approve] [--limit N]
```
Steps:
1. Read VISION_SOURCES; for each (relative_path, name):
   - Read image bytes from `ORIGINALS_ROOT / relative_path`
   - Call VLM with prompt: "请识别图中所有数值，包括：水位(m)、库容(万m³)、泄量(m³/s)等"
   - Save raw VLM output to `out/vision/{name}_raw.txt`
   - Run `cross_check()` → save to `out/vision/{name}_cross_check.json`
2. Write `out/vision/compare_report.md` summarizing cross-check results
3. If `--approve`: create `out/vision/approved.flag` (empty file); exit 0
4. If `out/vision/approved.flag` exists: skip all VLM calls, use existing outputs
5. Print summary and next steps (human review + --approve)

**Error handling:** VLM call fails → retry ≤ 2 times → still fails → write `out/vision/{name}_failed.txt` and continue with other images. Exit code 0 (partial failure OK, human will review).

- [ ] **Step 1: Write failing tests (mock transport, no real VLM calls)**

```python
# tests/test_vision_extract.py
import sys; sys.path.insert(0, "..")
from vision_extract import build_payload, cross_check
import base64, json

def test_build_payload_structure():
    fake_bytes = b"\xff\xd8\xff\xe0fake image data"
    payload = build_payload(fake_bytes, "识别数值")
    content = payload["content"]
    img_part = next(p for p in content if p.get("type") == "image_url")
    b64_data = img_part["image_url"]["url"].split(",")[1]
    assert base64.b64decode(b64_data) == fake_bytes

def test_cross_check_found():
    text = "百年一遇泄量1454 m³/s，千年一遇泄量2218 m³/s，汛限水位788.5m"
    result = cross_check(text)
    assert result["found"]["1454"] is True
    assert result["found"]["2218"] is True
    assert result["found"]["788.5"] is True

def test_cross_check_missing():
    text = "汛限水位为788米"
    result = cross_check(text)
    assert result["found"]["1454"] is False
    assert result["found"]["788.5"] is False   # "788" alone doesn't match "788.5"

def test_cross_check_output_shape():
    text = "test"
    result = cross_check(text)
    assert "found" in result and "values" in result
    assert isinstance(result["found"], dict)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_vision_extract.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement vision_extract.py**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_vision_extract.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/scada/SmartTwinRes-skills
git add ragflow_import/vision_extract.py ragflow_import/tests/test_vision_extract.py
git commit -m "feat(taoqupo): add vision_extract.py for offline VLM cross-check"
```

---

### Task 6: ragflow_client.py — RAGFlow API 客户端（RSA 登录、Dataset、文档）

**Files:**
- Create: `/home/scada/SmartTwinRes-skills/ragflow_import/ragflow_client.py`
- Test: `/home/scada/SmartTwinRes-skills/ragflow_import/tests/test_ragflow_client.py`

**Interfaces:**
- Consumes: `config.API_BASE`, `config.PUBLIC_PEM`, env vars `RAGFLOW_EMAIL`/`RAGFLOW_PASSWORD`
- Produces: `encrypt_password(password: str, public_pem_path: str) → str` (base64 PKCS1_v1_5), `RAGFlowClient` class with methods below

**encrypt_password(password, public_pem_path) → str:**
Mirrors `api/utils/crypt.py:27-36`:
1. Read public key from PEM file (unencrypted 2048-bit RSA)
2. Encrypt password bytes with `padding.PKCS1v15()` 
3. Return `base64.b64encode(ciphertext).decode()`

**RAGFlowClient methods:**

```python
class RAGFlowClient:
    def __init__(self, email: str, password: str, public_pem_path: str):
        self.session = requests.Session()
        self.csrf_token: str | None = None
        # Login: POST /auth/login with encrypted password → set session cookie
        self.login()

    # Dataset CRUD
    def list_datasets(self) → list[dict]
    def create_dataset(self, name: str, **kwargs) → dict  # POST /datasets
    def get_dataset(self, dataset_id: str) → dict
    def update_dataset(self, dataset_id: str, parser_config: dict) → dict  # PUT /datasets/{id}

    # Metadata schema
    def put_metadata_config(self, dataset_id: str, metadata: list[dict]) → dict  # PUT /datasets/{id}/metadata/config

    # Document operations
    def upload_document(self, dataset_id: str, file_path: Path, filename: str | None = None) → dict  # POST /datasets/{id}/documents (multipart)
    def patch_document(self, dataset_id: str, doc_id: str, meta_fields: dict) → dict  # PATCH /datasets/{id}/documents/{doc_id}
    def list_documents(self, dataset_id: str) → list[dict]  # GET /datasets/{id}/documents
    def parse_documents(self, dataset_id: str, document_ids: list[str]) → dict  # POST /datasets/{id}/documents/parse {"document_ids": [...]}
    def wait_document(self, dataset_id: str, doc_id: str, timeout: float = 300) → dict  # poll list_documents until done/fail

    # Search (QC)
    def search_datasets(self, dataset_ids: list[str], question: str, top_k: int = 10, use_kg: bool = False, meta_data_filter: dict | None = None) → dict  # POST /datasets/search

    # Tag KB
    def upload_tag_vocab(self, dataset_id: str, vocab_file_path: Path) → dict
    def wait_parse(self, dataset_id: str, timeout: float = 300) → dict
```

**Key implementation notes:**
- Login: `POST {API_BASE}/auth/login`, body `{"email":email,"password": encrypted_password}` (encrypted_password is the base64 output from `encrypt_password`). Response sets session cookie automatically.
- After login, all authenticated requests use `self.session.get/post/...` (carries cookie)
- `list_datasets` uses `{"offset":0,"limit":100}` query params
- `create_dataset`: body `{"name":name}` (chunk_method added by caller if needed)
- `update_dataset`: `PUT {API_BASE}/datasets/{dataset_id}`, body `{"parser_config": parser_config}`
- `put_metadata_config`: body `{"metadata": [...], "built_in_metadata": []}`
- `upload_document`: `requests.post(url, files={"file":(filename, open(path,"rb"),"application/octet-stream")})` 
- `patch_document`: `PATCH {API_BASE}/datasets/{dataset_id}/documents/{doc_id}`, body `{"meta_fields": meta_fields}`
- `parse_documents`: `POST {API_BASE}/datasets/{dataset_id}/documents/parse`, body `{"document_ids": document_ids}`
- `wait_document`: poll `list_documents` every 5s, done if `run == "3"` or `progress >= 1`, fail if `run == "4"` or `progress < 0`, timeout if exceeded
- `search_datasets`: `POST {API_BASE}/datasets/search`, body `{"dataset_ids":dataset_ids,"question":question,"top_k":top_k,"use_kg":use_kg,"meta_data_filter":meta_data_filter}`

- [ ] **Step 1: Write failing tests (mock HTTP, smoke test reads env)**

```python
# tests/test_ragflow_client.py
import sys; sys.path.insert(0, "..")
from ragflow_client import encrypt_password, RAGFlowClient
from unittest.mock import patch, MagicMock
import os

def test_encrypt_password_format():
    # Should return a non-empty base64 string
    import tempfile
    from Crypto.PublicKey import RSA
    key = RSA.generate(2048)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
        f.write(key.publickey().export_key().decode())
        pem_path = f.name
    result = encrypt_password("testpass", pem_path)
    import base64, json
    data = base64.b64decode(result)
    assert len(data) > 0
    os.unlink(pem_path)

def test_encrypt_deterministic_envelope():
    # Same password → same format (256-byte b64), not same ciphertext (non-deterministic PKCS1)
    from Crypto.PublicKey import RSA
    import tempfile
    key = RSA.generate(2048)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
        f.write(key.publickey().export_key().decode())
        pem_path = f.name
    r1 = encrypt_password("testpass", pem_path)
    r2 = encrypt_password("testpass", pem_path)
    assert r1 != r2  # non-deterministic
    import base64
    d1 = base64.b64decode(r1)
    d2 = base64.b64decode(r2)
    assert len(d1) == len(d2) == 256  # 2048-bit
    os.unlink(pem_path)

@patch("ragflow_client.requests.Session")
def test_client_login_called(mock_session_class):
    mock_session = MagicMock()
    mock_session_class.return_value = mock_session
    mock_session.post.return_value.status_code = 200
    with patch.dict(os.environ, {"RAGFLOW_EMAIL":"test@test.com","RAGFLOW_PASSWORD":"pass"}):
        with patch("ragflow_client.encrypt_password", return_value="encrypted"):
            client = RAGFlowClient("test@test.com", "pass", "/fake/pem")
    mock_session.post.assert_called_once()
    call_args = mock_session.post.call_args
    assert "/auth/login" in call_args[0][0]

def test_search_payload_structure():
    with patch("ragflow_client.requests.Session") as mock_sc:
        mock_session = MagicMock()
        mock_sc.return_value = mock_session
        mock_session.post.return_value.status_code = 200
        mock_session.post.return_value.json.return_value = {"data": {"chunks":[],"total":0}}
        with patch.dict(os.environ, {"RAGFLOW_EMAIL":"t@t.com","RAGFLOW_PASSWORD":"p"}):
            with patch("ragflow_client.encrypt_password", return_value="e"):
                c = RAGFlowClient("t@t.com","p","/fake/pem")
        c.search_datasets(["ds1","ds2"], "溢洪道设计泄量", use_kg=True, meta_data_filter={"method":"manual","logic":"and","conditions":[]})
        payload = mock_session.post.call_args[1]["json"]
        assert "dataset_ids" in payload
        assert payload["use_kg"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_ragflow_client.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement ragflow_client.py**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_ragflow_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/scada/SmartTwinRes-skills
git add ragflow_import/ragflow_client.py ragflow_import/tests/test_ragflow_client.py
git commit -m "feat(taoqupo): add ragflow_client.py with RSA login and full API surface"
```

---

### Task 7: run_setup.py — 幂等建库（标签库 + 5 dataset + metadata schema）

**Files:**
- Create: `/home/scada/SmartTwinRes-skills/ragflow_import/run_setup.py`
- Test: `/home/scada/SmartTwinRes-skills/ragflow_import/tests/test_run_setup.py` (mock RAGFlowClient)

**Interfaces:**
- Consumes: `config.DATASETS`, `config.TAG_KB`, `config.METADATA_SCHEMA`, `config.OUT_DIR`, `ragflow_client.RAGFlowClient`, `tag_vocab.write_vocab_txt`
- Produces: `out/setup_state.json` (maps ds_key → dataset_id), creates tag KB vocab file in `out/tag_vocab/`

**run_setup(dry_run: bool = False) → dict[ds_key, dataset_id]:**
1. `client = RAGFlowClient(email, password, PUBLIC_PEM)`
2. List existing datasets → build `name → id` lookup
3. For each ds in `DATASETS` (ds1..ds5) + TAG_KB (ds0):
   - If `name` already in existing: use existing id (idempotent)
   - Else: `POST /datasets` → capture returned `data"]["id"]
4. Tag KB special handling:
   - Write vocab to `out/tag_vocab/taoqupo_vocab.txt` using `tag_vocab.write_vocab_txt()`
   - `upload_tag_vocab(tag_kb_id, vocab_path)` → wait parse
   - Update DATASETS[ds1-5] `parser_config["tag_kb_ids"] = [tag_kb_id]`
5. For each ds1..ds5: `update_dataset(ds_id, parser_config)` with the merged parser_config
6. For each ds: `put_metadata_config(ds_id, METADATA_SCHEMA)`
7. Write `out/setup_state.json`: `{"ds0":{"name":"桃曲坡标签库","id":...}, "ds1":{"name":"规程与预案","id":...}, ...}`
8. Print summary: dataset names + IDs, which were created vs reused

**Precondition:** Emits a `SystemExit` with a clear message if the RAGFlow tenant has no embedding model configured (知识库需要 embedding 模型才能建库；请先在 RAGFlow Web UI 配置租户模型). The embedding model requirement causes `create_dataset` to fail with a non-obvious error if not pre-configured.

- [ ] **Step 1: Write failing tests (mock RAGFlowClient)**

```python
# tests/test_run_setup.py
import sys; sys.path.insert(0, "..")
from unittest.mock import patch, MagicMock
from run_setup import run_setup
import json, pathlib, tempfile

@patch("run_setup.RAGFlowClient")
def test_idempotent_lookup_existing(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    # Simulate ds1 already exists
    mock_client.list_datasets.return_value = [
        {"id": "abc123", "name": "规程与预案"},
    ]
    mock_client.create_dataset.return_value = {"data": {"id": "new"}}
    mock_client.update_dataset.return_value = {}
    mock_client.put_metadata_config.return_value = {}
    with patch("run_setup.write_vocab_txt"):
        with patch("run_setup.tag_vocab"):
            state = run_setup(dry_run=True)
    # ds1 should reuse abc123, not create new
    existing_ids = [v["id"] for v in state.values() if v.get("id") == "abc123"]
    assert "abc123" in existing_ids

@patch("run_setup.RAGFlowClient")
def test_dry_run_does_not_create(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_datasets.return_value = []
    with patch("run_setup.write_vocab_txt"):
        with patch("run_setup.tag_vocab"):
            run_setup(dry_run=True)
    mock_client.create_dataset.assert_not_called()

def test_setup_state_schema():
    # If out/setup_state.json exists, validate shape
    state_path = pathlib.Path("/home/scada/SmartTwinRes-skills/ragflow_import/out/setup_state.json")
    if state_path.exists():
        with open(state_path) as f:
            state = json.load(f)
        for ds_key in ["ds0","ds1","ds2","ds3","ds4","ds5"]:
            assert ds_key in state, f"{ds_key} missing"
            assert "id" in state[ds_key]
            assert "name" in state[ds_key]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_run_setup.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement run_setup.py**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_run_setup.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/scada/SmartTwinRes-skills
git add ragflow_import/run_setup.py ragflow_import/tests/test_run_setup.py
git commit -m "feat(taoqupo): add run_setup.py for idempotent dataset creation"
```

---

### Task 8: run_import.py — 三步导入（upload → metadata → parse）+ 断点续命

**Files:**
- Create: `/home/scada/SmartTwinRes-skills/ragflow_import/run_import.py`
- Test: `/home/scada/SmartTwinRes-skills/ragflow_import/tests/test_run_import.py` (mock RAGFlowClient + filesystem)

**Interfaces:**
- Consumes: `ragflow_client.RAGFlowClient`, `corpus.read_mapping_csv`, `config.OUT_DIR`, `out/setup_state.json` (from Task 7)
- Produces: `out/import_state.json` (keyed by `rel`, each with `status: pending|uploaded|meta_done|parse_requested|done|failed`)

**row_to_meta_fields(row: dict) → dict:**
Convert a mapping CSV row dict to the `meta_fields` payload for `patch_document`:
- Map CSV column names to the 11 metadata fields
- Coerce `year` to `int` if non-empty
- Omit fields with empty/None values
- Return `{"meta_fields": {...}}` shape

**ImportStateMachine:**
```python
@dataclass
class DocImportState:
    rel: str
    status: str  # pending → uploaded → meta_done → parse_requested → done/failed
    dataset_key: str
    doc_id: str | None = None
    error: str | None = None
```

**run_import(apply: bool, dataset_key: str | None, limit: int | None, state_path: Path) → dict:**

`--apply` flag required to actually import; without it runs dry.
`--dataset KEY` limits to one dataset (e.g. `--dataset ds3` for a pilot).
`--limit N` caps total files imported for quick smoke test.

Procedure:
1. Load `out/setup_state.json` → ds_key → dataset_id mapping
2. Load `out/mapping.csv` via `read_mapping_csv()`
3. Load `out/import_state.json` if exists (resume support)
4. Filter entries:
   - If `dataset_key` specified → filter to that ds_key
   - If `limit` specified → take first N
   - If already `done` → skip
5. Group by `dataset_key`; import in order ds1..ds5 (ds0 tag KB already done)
6. For each file in group:
   - `status = "pending"` → `upload_document(dataset_id, file_path, filename)`
   - On success: extract `doc_id` from response; `status = "uploaded"`
   - Persist state after each step
   - `patch_document(dataset_id, doc_id, row_to_meta_fields(row))`
   - `status = "meta_done"`
   - `parse_documents(dataset_id, [doc_id])`
   - `status = "parse_requested"`
   - `wait_document(dataset_id, doc_id, timeout=600)` (up to 10 min per doc)
   - `status = "done"` or `status = "failed"` with error message
7. Write final `import_state.json`
8. Print summary: N done, M failed, elapsed time

**Pilot mode (--limit 5 --dataset ds3):**
Use first to validate GraphRAG parsing on ds3 flood data before triggering full ds1+ds3 graphrag runs (expensive LLM calls).

- [ ] **Step 1: Write failing tests (mock client + filesystem)**

```python
# tests/test_run_import.py
import sys; sys.path.insert(0, "..")
from run_import import row_to_meta_fields, DocImportState, ImportStateMachine
from unittest.mock import MagicMock

def test_row_to_meta_fields_year_int():
    row = {"rel":"test.txt","year":"2021","flood_event":"2021-10","doc_type":"文本"}
    mf = row_to_meta_fields(row)
    assert mf["meta_fields"]["year"] == 2021  # coerced to int

def test_row_to_meta_fields_omits_empty():
    row = {"rel":"test.txt","year":"","flood_event":"","doc_type":"文本"}
    mf = row_to_meta_fields(row)
    assert "year" not in mf["meta_fields"]
    assert "flood_event" not in mf["meta_fields"]

def test_doc_import_state_defaults():
    s = DocImportState(rel="test.txt", status="pending", dataset_key="ds1")
    assert s.doc_id is None
    assert s.error is None

def test_state_machine_transitions():
    sm = ImportStateMachine()
    sm.set("test.txt", "uploaded")
    assert sm.get("test.txt") == "uploaded"
    sm.set("test.txt", "done")
    assert sm.get("test.txt") == "done"

@patch("run_import.RAGFlowClient")
@patch("run_import.read_mapping_csv")
def test_dry_run_does_not_upload(mock_read, mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_read.return_value = [{"rel":"a.txt","dataset_key":"ds1","year":"2021","flood_event":"","doc_type":"文本","source_format":"pdf","quality":"high","responsible_dept":"","doc_nature":"技术","location":"","flood_magnitude":"不适用"}]
    with patch("builtins.open", MagicMock()):
        with patch("pathlib.Path.exists", return_value=True):
            run_import(apply=False, dataset_key=None, limit=None)
    mock_client.upload_document.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_run_import.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement run_import.py**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_run_import.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/scada/SmartTwinRes-skills
git add ragflow_import/run_import.py ragflow_import/tests/test_run_import.py
git commit -m "feat(taoqupo): add run_import.py with three-step import and resume support"
```

---

### Task 9: run_qc.py — 人工验收问题集 + datasets/search 检索验证

**Files:**
- Create: `/home/scada/SmartTwinRes-skills/ragflow_import/run_qc.py`
- Test: `/home/scada/SmartTwinRes-skills/ragflow_import/tests/test_run_qc.py`

**Interfaces:**
- Consumes: `ragflow_client.RAGFlowClient`, `out/setup_state.json`
- Produces: `out/qc/report_{timestamp}.md` + `out/qc/results_{timestamp}.json`

**QUESTIONS (spec §3.5 + §6 阶段4, 6 questions):**

```python
QUESTIONS = [
    {
        "id": "Q1",
        "text": "桃曲坡水库溢洪道的设计泄量是多少？",
        "dataset_ids": ["ds1", "ds2"],   # which datasets to search
        "use_kg": True,
        "meta_data_filter": None,
        "expected_keywords": ["1454", "m³/s", "百年"],
        "category": "单跳参数",
    },
    {
        "id": "Q2", 
        "text": "2021年共发生几次洪水？时序如何？",
        "dataset_ids": ["ds3"],
        "use_kg": True,
        "meta_data_filter": {"method":"manual","logic":"and","conditions":[{"key":"flood_event","op":"=","value":"2021-10"}]},
        "expected_keywords": ["2021", "洪水"],
        "category": "多跳时序",
    },
    {
        "id": "Q3",
        "text": "10·3洪水调度依据规程哪条？涉及哪些站点？",
        "dataset_ids": ["ds1", "ds3"],
        "use_kg": True,
        "meta_data_filter": None,
        "expected_keywords": ["规程", "柳林", "瑶曲"],
        "category": "跨文档多跳",
    },
    {
        "id": "Q4",
        "text": "安芳东在哪些洪水事件中担任指挥？",
        "dataset_ids": ["ds3", "ds4"],
        "use_kg": True,
        "meta_data_filter": None,
        "expected_keywords": ["安芳东", "2021"],
        "category": "实体关联",
    },
    {
        "id": "Q5",
        "text": "2013年7月洪水的降雨量统计结果如何？",
        "dataset_ids": ["ds3"],
        "use_kg": False,
        "meta_data_filter": {"method":"manual","logic":"and","conditions":[{"key":"flood_event","op":"=","value":"2013-7"}]},
        "expected_keywords": ["2013", "降雨"],
        "category": "元数据过滤",
    },
    {
        "id": "Q6",
        "text": "桃曲坡水库汛限水位是多少？",
        "dataset_ids": ["ds1", "ds2"],
        "use_kg": False,
        "meta_data_filter": None,
        "expected_keywords": ["788.5", "汛限水位"],
        "category": "快速参数",
    },
]
```

**run_qc() procedure:**
1. Load `out/setup_state.json` → dataset IDs for each ds_key
2. For each question:
   - `search_datasets(dataset_ids, question_text, top_k=10, use_kg=use_kg, meta_data_filter=meta_data_filter)`
   - Extract `chunks` + `total` from response
   - Check `expected_keywords` presence in chunk texts
   - Save result dict to `out/qc/results_{timestamp}.json`
3. Write `out/qc/report_{timestamp}.md`:
   - Table: Q_ID | Category | Keywords Found | Chunks Returned | Pass/Fail
   - For each question: the question text + top-2 chunk snippets
4. Print: overall pass rate, which questions failed

**meta_data_filter runtime validation:** Before the first real call, do a test call with an invalid filter shape and print the error response body so the operator can debug if the API rejects the filter.

- [ ] **Step 1: Write failing tests (mock client)**

```python
# tests/test_run_qc.py
import sys; sys.path.insert(0, "..")
from run_qc import QUESTIONS
from unittest.mock import patch, MagicMock

def test_questions_have_required_fields():
    for q in QUESTIONS:
        assert "id" in q and "text" in q and "dataset_ids" in q
        assert "use_kg" in q and "expected_keywords" in q
        assert isinstance(q["dataset_ids"], list)

def test_question_count():
    assert len(QUESTIONS) == 6

def test_q1_uses_kg():
    q1 = next(q for q in QUESTIONS if q["id"] == "Q1")
    assert q1["use_kg"] is True
    assert "1454" in q1["expected_keywords"]

def test_meta_filter_structure():
    for q in QUESTIONS:
        mf = q.get("meta_data_filter")
        if mf is not None:
            assert "method" in mf
            assert mf["method"] in {"manual","semi_auto","auto"}

@patch("run_qc.RAGFlowClient")
def test_run_qc_produces_report(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.search_datasets.return_value = {
        "data": {"chunks": [{"content_with_weight": "溢洪道设计泄量1454 m³/s"}], "total": 1}
    }
    with patch("run_qc.pathlib.Path.exists", return_value=True):
        with patch("builtins.open", MagicMock()):
            with patch("json.load", return_value={"ds1":{"id":"1","name":"规程与预案"}}):
                from run_qc import run_qc
                run_qc()
    mock_client.search_datasets.assert_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_run_qc.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement run_qc.py**

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/scada/SmartTwinRes-skills/ragflow_import && python -m pytest tests/test_run_qc.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/scada/SmartTwinRes-skills
git add ragflow_import/run_qc.py ragflow_import/tests/test_run_qc.py
git commit -m "feat(taoqupo): add run_qc.py with 6 QC questions per spec §3.5"
```

---

### Task 10: README + requirements.txt — 操作手册

**Files:**
- Create: `/home/scada/SmartTwinRes-skills/ragflow_import/README.md`
- Create: `/home/scada/SmartTwinRes-skills/ragflow_import/requirements.txt`
- Modify: `/home/scada/SmartTwinRes-skills/ragflow_import/tests/test_config.py` (add smoke test for env vars)

**README structure:**

```markdown
# 桃曲坡水库知识库 RAGFlow 导入工具

## 环境准备

```bash
# 1. 创建 venv（Python 3.11+）
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 确认 RAGFlow 运行中
curl http://localhost:9380/api/v1/datasets  # 应返回 JSON

# 3. 设置凭据（环境变量，不入库）
export RAGFLOW_EMAIL="736599796@qq.com"
export RAGFLOW_PASSWORD="你的密码"
```

## 命令序列

### 阶段 0 — 扫描 + 映射表

```bash
# 扫描语料，生成 out/mapping.csv
python -m corpus
# 人工审查 mapping.csv（重点：flood_event 列、skip_reason 列）
# 确认后继续
```

### 阶段 1 — 表格预处理（可选）

```bash
# 生成 .md / .qa.md 表格式产物
python -m tables

# 离线 VLM 识别（需人工复核）
python vision_extract.py --limit 2   # dry-run，先看两幅图
# 人工审查 out/vision/compare_report.md
# 确认数值正确后批准
python vision_extract.py --approve
```

### 阶段 2 — 建库（幂等）

```bash
# dry-run 检查
python run_setup.py --dry-run

# 正式建库（创建 dataset + 标签库 + metadata schema）
python run_setup.py

# 确认 out/setup_state.json 生成
```

### 阶段 3 — 导入（先导，后全量）

```bash
# 导入门槛：标签库已完成 parse（run_setup.py 最后一步）
# 先导：ds3 抽 5 个文件，验证 GraphRAG
python run_import.py --dataset ds3 --limit 5
# 人工抽查 RAGFlow Web UI：检索"2021年10月3日洪水"
# 确认 chunks 有图谱关系后继续

# 全量导入（无 --limit）
python run_import.py --apply --dataset ds3   # ds3 全量（含 GraphRAG，较慢）
python run_import.py --apply --dataset ds1   # ds1（含 GraphRAG + Raptor）
python run_import.py --apply --dataset ds2   # ds2
python run_import.py --apply --dataset ds4   # ds4
python run_import.py --apply --dataset ds5   # ds5（含 Raptor）
```

### 阶段 4 — 人工验收

```bash
python run_qc.py
# 审查 out/qc/report_*.md
# Q1-Q4 使用 use_kg=true，需图谱
# Q5-Q6 验证 meta_data_filter 硬过滤
```

## 人工门槛

| 门槛 | 位置 | 通过标准 |
|---|---|---|
| mapping.csv 审查 | `out/mapping.csv` | flood_event 列正确；skip_reason 无误 |
| VLM 批准 | `out/vision/approved.flag` | 人工确认库容表/泄流曲线数值正确 |
| 10% OCR 抽样 | `pdf_text_analysis/ocr_new/` | 随机抽 5 个文本，肉眼确认 quality 标注 |
| 导入门槛 | ds3 5 文件 pilot | 检索"2021年10月3日洪水"有图谱关系返回 |

## 故障排查

- **Login 失败 (401)**: 密码错误或 RSA 加密格式不匹配；`python -c "from ragflow_client import encrypt_password; print(encrypt_password('pass','/opt/git/ragflow/conf/public.pem'))"` 验证
- **create_dataset 400**: tenant 未配置 embedding 模型 → RAGFlow Web UI → 模型配置
- **parse 一直 running**: 重启 RAGFlow 容器；检查 `docker compose -f docker/docker-compose-base.yml logs deepdoc`
- **meta_data_filter 无效**: 运行 `python run_qc.py` 时观察错误信息；filter shape 见 spec §4.3
- **导入状态卡住**: 检查 `out/import_state.json` 的 `failed` 条目；删除对应 doc 后重跑
```

**requirements.txt:**
```
requests>=2.31.0
pycryptodome>=3.19.0
pytest>=8.0.0
```

- [ ] **Step 1: Verify requirements and README content**

Read the README and requirements, check all referenced files in command sequences exist or are reachable.

- [ ] **Step 2: Commit**

```bash
cd /home/scada/SmartTwinRes-skills
git add ragflow_import/README.md ragflow_import/requirements.txt
git commit -m "docs(taoqupo): add README operation manual and requirements.txt"
```

---

## 自检清单（写完 plan 后自查）

### 1. Spec 覆盖检查

| spec § | 要求 | 对应任务 |
|---|---|---|
| §1.3 | 离线 VLM 路线 | Task 5 |
| §1.3 | 原生 xlsx priority | Task 2 (native_xlsx_for) |
| §2.1 | 5 个 dataset 划分 | Task 1 (DATASETS), Task 7 |
| §2.2 | parser_config 含 graphrag/raptor/tag_kb_ids | Task 1, Task 7 |
| §2.2 | graphrag entity_types 6 类 | Task 1 |
| §3.2 | 本体 6 类实体 + 7 类关系 | Task 1 (entity_types) |
| §3.3 | 配置优先 + 容器生效方式 | Task 7 (PUT parser_config) |
| §4.1 | 11 个元数据字段 | Task 1 (METADATA_SCHEMA) |
| §4.1 | flood_event 枚举 7 值 | Task 1 |
| §5.1 | 标签库格式 + 13 行词表 | Task 4 |
| §5.2 | 软标签重排（不用标签做硬过滤） | spec 设计，代码不涉及 |
| §5.3 | 精确过滤走 meta_fields | Task 8 (row_to_meta_fields) |
| §6 阶段0 | 顶层 11 文件逐一映射 | Task 2 (TOP_LEVEL_ASSIGNMENTS) |
| §6 阶段0 | video_analysis 不导入 | Task 2 (SKIP_DIRS) |
| §6 阶段0 | dedup _dupx 策略 | Task 2 (dedupe by sha256) |
| §6 阶段1 | 文字化值 1454/2218/788.5 | Task 5 (TEXTUALIZED_VALUES) |
| §6 阶段2 | KB 级 parser_config 先于上传 | Task 7 |
| §6 阶段3 | upload → metadata → parse 三步 | Task 8 |
| §6 阶段3 | 标签库先 parse 再设 tag_kb_ids | Task 7 |
| §6 阶段4 | QC 4 个代表问题 | Task 9 |
| §7 | VLM 失败重试 ≤2 | Task 5 |
| §7 | resolution 合并同名变体 | spec 配置，代码不涉及 |

### 2. 占位符扫描

- [ ] 无 `TODO`、`TBD`、`implement later`
- [ ] 无 `// 类似 Task X`（每个任务重复完整代码/步骤）
- [ ] 无未定义类型/函数/常量（所有符号在 config.py / 对应任务的 interfaces 中定义）
- [ ] 无泛泛的"添加适当错误处理"（给出具体处理逻辑）

### 3. 类型一致性

- [ ] `Entry.sha256` 在 Task 2 定义 → Task 2 测试用例 `sha256` 字段存在
- [ ] `METADATA_SCHEMA` 字段名与 `row_to_meta_fields` 列名完全一致
- [ ] `FLOOD_EVENT_BY_SUBDIR` 的 key 匹配实际子目录名（如 `02-2021年洪水调度`）
- [ ] `DATASETS` 的 `graphrag_config.entity_types` 列表 = 6 项（spec §3.2）
- [ ] `TAG_KB_NAME` = `"桃曲坡标签库"`（与 ragflow_client 上传标签库后的等待逻辑一致）

### 4. 产物文件路径自洽

- [ ] `out/setup_state.json` 被 Task 7 写出、被 Task 8/9 读取
- [ ] `out/mapping.csv` 被 Task 2 写出、被 Task 8 读取
- [ ] `out/vision/approved.flag` 由 `vision_extract.py --approve` 创建
- [ ] `out/qc/report_*.md` 由 `run_qc.py` 生成

---

**Plan complete and saved to `docs/superpowers/plans/2026-08-25-taoqupo-reservoir-kb-import.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints

Which approach?
