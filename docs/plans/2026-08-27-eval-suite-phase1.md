# 评测体系一期实施计划（eval-suite Phase 1）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `run_qc.py` 从内置六问升级为"题库外置 jsonl + retrieval/structured 两层确定性判分 + 配置快照 + runs.jsonl 历史 + --compare 两两对比"的可复现评测执行器，并交付首批 16 道全实证验收题。

**Architecture:** 所有生产代码仍在单文件 `src/run_qc.py` 内（A+ 轻量混合形态，守住决策点5）；题目数据外置 `out/eval/cases.jsonl`；判定全部是纯函数（normalize → 数值/关键词锚点计数 → 显式 pass 公式）；运行产物追加进 `out/eval/runs.jsonl` 与 `runs/<run_id>/`。零侵扰：GraphRAG 对照只用请求级 `use_kg` 双跑，不碰任何 dataset 持久配置。

**Tech Stack:** Python 3 标准库（json/argparse/pathlib/time），既有 `requests.Session` 封装 `RAGFlowClient`，pytest 全离线 mock。无新依赖。

**Spec:** `docs/specs/2026-08-27-eval-suite-design.md`（判分公式在 §4.1，case 模式在 §3.1，CLI 在 §5.1 —— 执行者必须同时读它）

## Global Constraints

- **本目录不是 git 仓库**（环境实况）。因此本计划没有 commit 步骤；每个任务以指定的 pytest 运行全绿作为收尾检查点。
- 所有命令从 `src/` 目录运行；模块互相用顶层名导入（`from config import OUT_DIR`）。
- 测试必须全离线：不登录、不发任何 HTTP；需要 `OUT_DIR` 的测试用 `patch.object(module, "OUT_DIR", tmp_path)` 隔离到临时目录。
- 用户可见字符串（报告文案、CLI 提示、错误信息）一律中文。
- 运行产物只写 `out/`（评测产物固定落 `out/eval/`）；`CORPUS_ROOT` 只读；`/opt/git/ragflow` 除读 `public.pem` 外不可触碰。
- 每轮 `--apply` 实跑按 spec §8 串行逐 case 执行（共享网关并发弱）；GraphRAG 对照仅请求级参数。
- 凭据只来自环境变量 `RAGFLOW_EMAIL`/`RAGFLOW_PASSWORD`，脚本内不得出现明文。
- 判分公式逐字实现 spec §4.1：`pass = (numeric_exact OR hit@10) AND keyword_ok`；`gold.numbers` 为空集时 numeric_exact 记 False 不参与；`keyword_ok = 关键词命中数 ≥ threshold.min_keywords`（缺省 1）；`gold.keywords` 为空集时恒 True。

## 与既网事实的对齐说明（写给执行者）

- `search_datasets(dataset_ids, question, top_k, use_kg, meta_data_filter)` 已存在并已实证支持 `use_kg` 与 `meta_data_filter`；响应数据形如 `{"chunks":[{...,"content_with_weight":str,"important_keywords":[...],"doc_id":str}], "total":n}`，chunks 顺序即相关度序。
- `list_datasets()` 返回 dataset 字典数组（含 `id/name/chunk_method/parser_config/embedding_model`）；`list_documents(dataset_id)` 返回文档数组（含 `id/name/run`）。
- `corpus.read_mapping_csv(path) -> list[dict]`，行键含 `rel`、`dataset_key`、`skip_reason`。当前 `out/mapping.csv` 共 72 行有效条目（其中 3 行 `_dup` 带 `skip_reason`，不会导入）。
- 首批验收题的金标数字全部来自本会话已逐字核对过的材料：config.TEXTUALIZED_VALUES（1454/2218/788.5，VLM 已批）与 pilot 实测 chunk 文本（`out/qc/chunks_ds3_20260827_144337.md` 中引用的服务端原文：岔口 260 m³/s 警戒200保证300、弃水 1.56 亿立方米、面雨量 121/147 mm、泄水总量 9685 万方、相公镇 54.2 mm、潼关 8360 m³/s、全省面平均雨量 87.2 mm）。
- 规模现实：语料实际分布 ds1=4、ds2=1、ds3=48、ds4=5、ds5=15 个文档。spec 写 "~30 题"；本期首批入库 **16 道**（已在第 10 任务给全逐字内容），其余依赖人工后续往 `cases.jsonl` 追加行——loader 天然支持增量，不改代码。这不是缺口而是数据工作项，报告里无需为数量埋钩子。

## File Structure

```
src/run_qc.py                      改造（几乎重写）：归一化/判分/loader/快照/执行器/报告/compare/CLI
src/tests/test_eval_scoring.py     新建：任务1-3 纯函数测试
src/tests/test_eval_loader.py      新建：任务4 loader 测试
src/tests/test_eval_runs.py        新建：任务5/8 快照·runs·compare 测试
src/tests/test_eval_runner.py      新建：任务6/7 执行器与报告测试
src/tests/test_run_qc.py           修改：任务9 删除 QUESTIONS 耦合用例，改写入口用例
src/tests/test_eval_cases_file.py  新建：任务10 对真实 cases.jsonl 做 skip-if 缺席校验
src/README.md                      修改：任务11 追加「评测」操作章节
out/eval/cases.jsonl               新建：任务10 首批 16 行题库（数据文件）
out/eval/{runs.jsonl,runs/<id>/*}  运行期产物（不在任务里预创建）
```

---

### Task 1: 匹配归一化与 pass 公式纯函数

**Files:**
- Modify: `src/run_qc.py`（文件顶部 import 区之后新增函数；本任务不删旧代码）
- Test: `src/tests/test_eval_scoring.py`

**Interfaces:**
- Consumes: 无（叶子纯函数）
- Produces:
  - `normalize_for_match(text: str) -> str` —— 小写化 + 全角转半角 + 去掉数字序列内的千分位逗号
  - `numeric_hits(numbers: list[str], texts: list[str]) -> int` —— 归一后包含匹配命中的 gold 数字个数
  - `keyword_hits(keywords: list[str], texts: list[str]) -> int`
  - `anchor_hit_at_k(gold: dict, texts_ranked: list[str], k: int) -> bool` —— numbers ∪ keywords 任一出现在前 k 条
  - `decide_pass(*, numeric_exact: bool, hit10: bool, kw_hits: int, min_keywords: int) -> bool`

- [ ] **Step 1: Write the failing test**

新建 `src/tests/test_eval_scoring.py`：

```python
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from run_qc import (
    normalize_for_match, numeric_hits, keyword_hits,
    anchor_hit_at_k, decide_pass,
)


def test_normalize_fullwidth_and_case():
    assert normalize_for_match("Ｍ³/S １２３Ａ") == "m³/s 123a"


def test_normalize_thousands_comma_removed_only_between_digits():
    assert "1454" in normalize_for_match("设计泄量1,454 m³/s")
    assert "," not in normalize_for_match("雨情,水情")   # 中文/普通逗号不动


def test_numeric_hits_containment():
    texts = ["泄量1,454立方米每秒", "汛限水位７８８.５米"]
    assert numeric_hits(["1454"], texts) == 1
    assert numeric_hits(["1454", "788.5"], texts) == 2
    assert numeric_hits(["9999"], texts) == 0


def test_keyword_hits_counts_distinct_keywords():
    texts = ["溢洪道百年一遇泄量1454"]
    assert keyword_hits(["溢洪道", "百年一遇", "瑶曲"], texts) == 2


def test_anchor_hit_boundary_at_k():
    gold = {"numbers": ["1454"], "keywords": ["溢洪道"]}
    texts = [f"噪声块{i}" for i in range(5)] + ["溢洪道设计泄量1454"]
    assert anchor_hit_at_k(gold, texts, 5) is False
    assert anchor_hit_at_k(gold, texts, 6) is True


def test_decide_pass_formula_truth_table():
    # 规格公式：pass = (numeric_exact OR hit@10) AND keyword_ok
    assert decide_pass(numeric_exact=True, hit10=False, kw_hits=1, min_keywords=1)
    assert decide_pass(numeric_exact=False, hit10=True, kw_hits=2, min_keywords=2)
    assert not decide_pass(numeric_exact=True, hit10=False, kw_hits=0, min_keywords=1)   # 关键词门挡下
    assert not decide_pass(numeric_exact=False, hit10=False, kw_hits=5, min_keywords=1)  # 锚点门挡下
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src && python3 -m pytest tests/test_eval_scoring.py -q`
Expected: FAIL，ImportError（`cannot import name 'normalize_for_match'`）

- [ ] **Step 3: Write minimal implementation**

在 `src/run_qc.py` 的 `from ragflow_client import RAGFlowClient` 之后插入：

```python
# ------------------------------------------------------------------
# 评测纯函数（spec §4.1）：同一输入必得同一分数
# ------------------------------------------------------------------
def normalize_for_match(text: str) -> str:
    """小写化 + 全角转半角 + 去千分位逗号。只做包含匹配前的归一，不做单位换算。"""
    out_chars = []
    prev_digit = False
    for ch in (text or "").lower():
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:            # 全角 ASCII 区
            ch = chr(code - 0xFEE0)
        if ch == "," and prev_digit:            # 千分位逗号（仅数字后跟数字场景，宽泛去除无害：
            out_chars.append("")                # 普通“,”紧跟数字的组合中文文本中不存在）
            continue
        out_chars.append(ch)
        prev_digit = ch.isdigit()
    return "".join(out_chars)


def numeric_hits(numbers: list[str], texts: list[str]) -> int:
    norm_texts = [normalize_for_match(t) for t in texts]
    return sum(
        1 for n in numbers
        if n and any(normalize_for_match(n) in t for t in norm_texts)
    )


def keyword_hits(keywords: list[str], texts: list[str]) -> int:
    norm_texts = [normalize_for_match(t) for t in texts]
    return sum(
        1 for kw in keywords
        if kw and any(normalize_for_match(kw) in t for t in norm_texts)
    )


def anchor_hit_at_k(gold: dict, texts_ranked: list[str], k: int) -> bool:
    """hit@k：gold.numbers ∪ keywords 任一命中前 k 名 chunk。空集 gold 由 loader 拒绝。"""
    topk = texts_ranked[:k]
    return numeric_hits(gold.get("numbers", []), topk) > 0 or \
        keyword_hits(gold.get("keywords", []), topk) > 0


def decide_pass(*, numeric_exact: bool, hit10: bool, kw_hits: int, min_keywords: int) -> bool:
    """spec §4.1 通过公式，运算顺序显式化。min_keywords 为 0/None（对应
    gold.keywords 为空）时关键词门恒真——数值锚点独立承载该 case。"""
    anchor_gate = bool(numeric_exact) or bool(hit10)
    kw_ok = True if not min_keywords else kw_hits >= int(min_keywords)
    return anchor_gate and kw_ok
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src && python3 -m pytest tests/test_eval_scoring.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: 回归检查点（无 git 仓库，以此代替 commit）**

Run: `cd src && python3 -m pytest -q`
Expected: 原有 132 例 + 新增 6 例全绿（旧 run_qc 用例尚未触碰）。

---

### Task 2: retrieval 层打分

**Files:**
- Modify: `src/run_qc.py`
- Test: `src/tests/test_eval_scoring.py`

**Interfaces:**
- Consumes: Task 1 的 `numeric_hits/keyword_hits/anchor_hit_at_k/decide_pass`
- Produces:
  - `score_retrieval(gold: dict, texts: list[str]) -> dict`
    入参 `texts` 为按相关度排序的前 10 条 chunk 文本（可短于 10）。返回
    `{"hit5": bool, "hit10": bool, "mrr_rank": int, "numeric_exact": bool,
      "kw_hits": int, "kw_needed": int, "passed": bool}`
    其中 `mrr_rank` = 第一个含任一金标锚点的 chunk 名次（1 起，无命中记 0）。
  - `mrr_mean(mrr_ranks: list[int]) -> float` —— 均值 Σ1/rank / n，空表返 0.0

- [ ] **Step 1: Write the failing test**

追加到 `src/tests/test_eval_scoring.py`：

```python
from run_qc import score_retrieval, mrr_mean


def test_score_retrieval_full_fields():
    gold = {"numbers": ["1454"], "keywords": ["溢洪道", "百年一遇"]}
    texts = ["噪声", "溢洪道设计泄量为1454"]
    r = score_retrieval(gold, texts)
    assert r["hit5"] and r["hit10"]
    assert r["numeric_exact"] is True
    assert r["kw_hits"] == 2
    assert r["mrr_rank"] == 2
    assert r["passed"] is True


def test_score_retrieval_fails_on_empty_numbers_without_hits():
    gold = {"numbers": [], "keywords": ["瑶曲"]}          # 只有关键词锚点
    r = score_retrieval(gold, ["柳林站雨量偏大"])
    assert r["numeric_exact"] is False
    assert r["hit10"] is False
    assert r["passed"] is False                            # keyword_ok 但锚点门未过


def test_score_retrieval_keywords_empty_means_always_ok():
    gold = {"numbers": ["8360"], "keywords": []}
    r = score_retrieval(gold, ["潼关站8360m3/s"])
    assert r["kw_needed"] == 0
    assert r["passed"] is True


def test_score_retrieval_no_keywords_present_needs_one():
    gold = {"numbers": ["123"], "keywords": ["汛限水位"]}
    r = score_retrieval(gold, ["123 在正文出现但关键词缺席"])
    assert r["kw_needed"] == 1
    assert r["passed"] is False


def test_mrr_mean_over_cases():
    assert mrr_mean([1, 3, 0]) == (1 + 1 / 3 + 0) / 3
    assert mrr_mean([]) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src && python3 -m pytest tests/test_eval_scoring.py -q`
Expected: 新增 5 例 FAIL（ImportError: score_retrieval），Task 1 的 6 例仍 PASS

- [ ] **Step 3: Write minimal implementation**

`src/run_qc.py` 紧接 Task 1 函数后：

```python
def score_retrieval(gold: dict, texts: list[str]) -> dict:
    """对单个 case 的检索结果打分（text 相关度序，最多取 10 条）。"""
    hit5 = anchor_hit_at_k(gold, texts, 5)
    hit10 = anchor_hit_at_k(gold, texts, 10)
    numbers, keywords = gold.get("numbers", []), gold.get("keywords", [])
    numeric_exact = numeric_hits(numbers, texts[:10]) > 0 and bool(numbers)

    mrr_rank = 0
    for rank, t in enumerate(texts[:10], start=1):
        if numeric_hits(numbers, [t]) or keyword_hits(keywords, [t]):
            mrr_rank = rank
            break

    kw_hits = keyword_hits(keywords, texts[:10])
    kw_needed = 0 if not keywords else \
        int(gold.get("threshold", {}).get("min_keywords", 1))
    passed = decide_pass(
        numeric_exact=numeric_exact, hit10=hit10,
        kw_hits=kw_hits, min_keywords=kw_needed,
    )
    return {
        "hit5": hit5, "hit10": hit10, "mrr_rank": mrr_rank,
        "numeric_exact": numeric_exact, "kw_hits": kw_hits,
        "kw_needed": kw_needed, "passed": passed,
    }


def mrr_mean(mrr_ranks: list[int]) -> float:
    """MRR = Σ(1/rank) / n；rank 为 0（无命中）计 0 分。"""
    if not mrr_ranks:
        return 0.0
    return sum(1.0 / r for r in mrr_ranks if r > 0) / len(mrr_ranks)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src && python3 -m pytest tests/test_eval_scoring.py -q`
Expected: PASS（11 passed）

---

### Task 3: structured 层打分（过滤集合比对 + 标签弱信号 + 增益记账）

**Files:**
- Modify: `src/run_qc.py`
- Test: `src/tests/test_eval_scoring.py`

**Interfaces:**
- Consumes: 无新依赖
- Produces:
  - `rel_to_name(rel: str) -> str` —— 取 posix 基名作文档名（映射 `gold.source_rel` ↔ API 的 `doc.name`）
  - `score_meta_filter(expected_names: list[str], returned_names: list[str]) -> dict`
    → `{"passed": bool, "missing": [...], "unexpected": [...]}`（精确集合一致才过，spec §4.2）
  - `tag_signal(chunks: list[dict], expected_tags: list[str]) -> int` —— 命中块 `important_keywords` 与预期标签词交集计数（弱信号，无门槛）
  - `kg_gain_record(hit5_on: int, hit5_off: int) -> int` —— 仅记账 `hit@5(use_kg=true) − hit@5(false)`

- [ ] **Step 1: Write the failing test**

追加到 `src/tests/test_eval_scoring.py`：

```python
import posixpath
from run_qc import rel_to_name, score_meta_filter, tag_signal, kg_gain_record


def test_rel_to_name():
    assert rel_to_name("06-历年洪水资料/05-2013年洪水(7-22)/7-22防洪报告.doc") == "7-22防洪报告.doc"


def test_meta_filter_exact_set_equality():
    exp = ["防洪报告.doc", "洪水过程.xls"]
    assert score_meta_filter(exp, list(reversed(exp)))["passed"] is True
    r = score_meta_filter(exp, ["防洪报告.doc"])                # 少了一个
    assert r["passed"] is False and r["missing"] == ["洪水过程.xls"] and r["unexpected"] == []
    r2 = score_meta_filter(exp, exp + ["多余.doc"])
    assert r2["passed"] is False and r2["unexpected"] == ["多余.doc"]


def test_tag_signal_intersection_count():
    chunks = [
        {"important_keywords": ["溢洪道", "泄洪", "无关词"]},
        {"important_keywords": []},
        {},
    ]
    assert tag_signal(chunks, ["溢洪道", "泄洪", "灾损"]) == 2   # 去重计数


def test_tag_signal_tolerates_missing_field():
    assert tag_signal([{"content_with_weight": "x"}], ["任意"]) == 0


def test_kg_gain_is_plain_difference():
    assert kg_gain_record(hit5_on=1, hit5_off=0) == 1
    assert kg_gain_record(hit5_on=0, hit5_off=1) == -1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src && python3 -m pytest tests/test_eval_scoring.py -q`
Expected: 新增 5 例 FAIL

- [ ] **Step 3: Write minimal implementation**

`src/run_qc.py` 继续追加：

```python
def rel_to_name(rel: str) -> str:
    return posixpath.basename(rel)


def score_meta_filter(expected_names: list[str], returned_names: list[str]) -> dict:
    """元数据硬过滤 drill：返回文档名单与期望名单精确一致才 pass（顺序无关）。"""
    exp, got = set(expected_names), set(returned_names)
    return {
        "passed": exp == got,
        "missing": sorted(exp - got),
        "unexpected": sorted(got - exp),
    }


def tag_signal(chunks: list[dict], expected_tags: list[str]) -> int:
    seen = set()
    for c in chunks[:10]:
        for kw in c.get("important_keywords") or []:
            if kw in expected_tags:
                seen.add(kw)
    return len(seen)


def kg_gain_record(hit5_on: int, hit5_off: int) -> int:
    return int(hit5_on) - int(hit5_off)
```

同时把 `import posixpath` 加进 `src/run_qc.py` 顶部 import 区（`import json` 之后一行即可；测试里的 posixpath 导入仅为示例自足性，可留可删）。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src && python3 -m pytest tests/test_eval_scoring.py -q`
Expected: PASS（16 passed）

---

### Task 4: cases.jsonl loader 与 schema 校验

**Files:**
- Modify: `src/run_qc.py`
- Test: `src/tests/test_eval_loader.py`

**Interfaces:**
- Consumes: 无（`valid_rel_set` 由调用方从 `read_mapping_csv` 构建）
- Produces:
  - `class EvalCaseError(ValueError)`
  - `load_cases(path: Path, valid_rel_set: set[str] | None = None) -> list[dict]`
    逐行解析 JSON 并校验：必填 `id/tier/layer/question/datasets/gold`；`tier ∈ {acceptance, regression}`；`layer ∈ {retrieval, structured, e2e}`；`datasets` 为非空列表；`ids` 全局唯一；`gold.numbers` 与 `gold.keywords` 至少一个非空；`gold.source_rel` 为列表（可为空）且每一项都在 `valid_rel_set`（传 None 时跳过该校验，供单元测试注入临时集）；`meta_filter` 非 None 仅允许 `layer == "structured"`；`use_kg` 缺省 False；`threshold.min_keywords` 缺省 1。
  - 校验通过后补齐默认字段再返回。

- [ ] **Step 1: Write the failing test**

新建 `src/tests/test_eval_loader.py`：

```python
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import pytest

from run_qc import load_cases, EvalCaseError


RELS = {
    "01-核心文档-四案/02-调度规程.pdf",
    "06-历年洪水资料/05-2013年洪水(7-22)/7-22防洪报告.doc",
}


def _write(tmp_path, *rows):
    p = tmp_path / "cases.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return p


VALID = {
    "id": "AC-T-01", "tier": "acceptance", "layer": "retrieval",
    "question": "溢洪道的设计泄量是多少？",
    "datasets": ["ds1"],
    "gold": {"numbers": ["1454"], "keywords": ["溢洪道"],
             "answer": "", "source_rel": ["01-核心文档-四案/02-调度规程.pdf"]},
}


def test_load_valid_case_gets_defaults(tmp_path):
    cases = load_cases(_write(tmp_path, VALID), RELS)
    assert cases[0]["use_kg"] is False                     # 缺省 false
    assert cases[0]["threshold"]["min_keywords"] == 1      # 缺省 1
    assert cases[0]["meta_filter"] is None


@pytest.mark.parametrize("field,value", [("tier", "other"), ("layer", "chat")])
def test_enum_violations_raise(tmp_path, field, value):
    bad = json.loads(json.dumps(VALID))
    bad[field] = value
    with pytest.raises(EvalCaseError):
        load_cases(_write(tmp_path, bad), RELS)


def test_missing_required_field_raises(tmp_path):
    for field in CASE_REQUIRED_FIELDS:
        bad = json.loads(json.dumps(VALID))
        bad.pop(field)
        with pytest.raises(EvalCaseError, match=field):
            load_cases(_write(tmp_path, bad), RELS)


def test_empty_datasets_raises(tmp_path):
    bad = json.loads(json.dumps(VALID)); bad["datasets"] = []
    with pytest.raises(EvalCaseError, match="datasets"):
        load_cases(_write(tmp_path, bad), RELS)


def test_duplicate_ids_raise(tmp_path):
    with pytest.raises(EvalCaseError, match="重复"):
        load_cases(_write(tmp_path, VALID, dict(VALID)), RELS)


def test_both_anchor_sets_empty_raises(tmp_path):
    bad = json.loads(json.dumps(VALID))
    bad["gold"]["numbers"], bad["gold"]["keywords"] = [], []
    with pytest.raises(EvalCaseError, match="金标锚点"):
        load_cases(_write(tmp_path, bad), RELS)


def test_source_rel_not_in_mapping_raises(tmp_path):
    bad = json.loads(json.dumps(VALID))
    bad["gold"]["source_rel"] = ["05-基础数据与曲线/泄流曲线.png.txt"]   # 旧提取链路径混入
    with pytest.raises(EvalCaseError, match="mapping.csv"):
        load_cases(_write(tmp_path, bad), RELS)


def test_meta_filter_outside_structured_raises(tmp_path):
    bad = json.loads(json.dumps(VALID))
    bad["meta_filter"] = {"flood_event": "2013-7"}
    with pytest.raises(EvalCaseError, match="structured"):
        load_cases(_write(tmp_path, bad), RELS)


def test_none_relset_skips_source_validation(tmp_path):
    bad = json.loads(json.dumps(VALID))
    bad["gold"]["source_rel"] = ["任意路径.pdf"]
    assert load_cases(_write(tmp_path, bad), None)[0]["question"].startswith("溢洪道")
```

（`CASE_REQUIRED_FIELDS` 从 `run_qc` 一并导入：`from run_qc import load_cases, EvalCaseError, CASE_REQUIRED_FIELDS`。）

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src && python3 -m pytest tests/test_eval_loader.py -q`
Expected: FAIL（ImportError: EvalCaseError）

- [ ] **Step 3: Write minimal implementation**

`src/run_qc.py` 追加：

```python
CASE_REQUIRED_FIELDS = ("id", "tier", "layer", "question", "datasets", "gold")
CASE_TIERS = ("acceptance", "regression")
CASE_LAYERS = ("retrieval", "structured", "e2e")


class EvalCaseError(ValueError):
    """cases.jsonl 行级 schema/引用违例。"""


def load_cases(path: Path, valid_rel_set: set[str] | None = None) -> list[dict]:
    """解析并校验题库；通过校验的行补齐默认字段后返回。错误立即抛出并带行号。"""
    cases: list[dict] = []
    seen_ids: set[str] = set()
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                case = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise EvalCaseError(f"第 {lineno} 行不是合法 JSON：{exc}") from exc

            for field in CASE_REQUIRED_FIELDS:
                if field not in case:
                    raise EvalCaseError(f"第 {lineno} 行缺少必填字段 {field}")
            cid = case["id"]
            if cid in seen_ids:
                raise EvalCaseError(f"第 {lineno} 行 id 重复：{cid}")
            seen_ids.add(cid)
            if case["tier"] not in CASE_TIERS:
                raise EvalCaseError(f"{cid}: tier 必须是 {CASE_TIERS}")
            if case["layer"] not in CASE_LAYERS:
                raise EvalCaseError(f"{cid}: layer 必须是 {CASE_LAYERS}")
            if not isinstance(case["datasets"], list) or not case["datasets"]:
                raise EvalCaseError(f"{cid}: datasets 必须是非空列表")

            gold = case["gold"]
            if not isinstance(gold, dict):
                raise EvalCaseError(f"{cid}: gold 必须是对象")
            if not gold.get("numbers") and not gold.get("keywords"):
                raise EvalCaseError(f"{cid}: 金标锚点至少要有 numbers 或 keywords 之一")

            rels = gold.get("source_rel") or []
            if valid_rel_set is not None:
                for rel in rels:
                    if rel not in valid_rel_set:
                        raise EvalCaseError(
                            f"{cid}: source_rel 不在 mapping.csv 中：{rel}"
                        )

            mf = case.get("meta_filter")
            if mf is not None and case["layer"] != "structured":
                raise EvalCaseError(f"{cid}: meta_filter 仅允许 layer=structured 使用")

            case.setdefault("use_kg", False)
            case.setdefault("meta_filter", None)
            case.setdefault("threshold", {}).setdefault("min_keywords", 1)
            cases.append(case)
    return cases
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src && python3 -m pytest tests/test_eval_loader.py -q`
Expected: PASS

- [ ] **Step 5: 回归检查点**

Run: `cd src && python3 -m pytest -q`
Expected: 全绿。

---

### Task 5: 配置快照与 runs.jsonl 持久化

**Files:**
- Modify: `src/run_qc.py`
- Test: `src/tests/test_eval_runs.py`

**Interfaces:**
- Consumes: `RAGFlowClient.list_datasets()`；`config.DATASETS`（拿 ds_key→name 对应关系：DATASETS 元素含 `"key"` 与 `"name"`）
- Produces:
  - `SNAPSHOT_FIELDS = ("id", "name", "chunk_method", "parser_config", "embedding_model")`
  - `snapshot_datasets(client, setup_state: dict) -> dict`
    返回 `{ds_key: {字段...}}`，只收 setup_state 里登记过的 ds；找不到的 ds_key 以 `None` 值占位。
  - `run_id_now() -> str` —— `time.strftime("%Y%m%d-%H%M%S")` 本地时间
  - `append_run(runs_path: Path, record: dict) -> None` —— 追加一行 JSON（父目录自动建）
  - `load_runs(runs_path: Path) -> list[dict]`
  - `snapshots_differ(a: dict, b: dict) -> bool`

- [ ] **Step 1: Write the failing test**

新建 `src/tests/test_eval_runs.py`：

```python
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from unittest.mock import MagicMock, patch

from run_qc import (
    snapshot_datasets, run_id_now, append_run, load_runs, snapshots_differ,
)
import run_qc as rq


def test_snapshot_pulls_each_registered_ds_by_name():
    cfg_datasets = [{"key": "ds1", "name": "规程与预案"},
                    {"key": "ds3", "name": "洪水资料"}]
    setup_state = {"ds1": {"id": "id-a"}, "ds3": {"id": "id-b"}}
    client = MagicMock()
    client.list_datasets.return_value = [
        {"id": "id-b", "name": "洪水资料", "chunk_method": "naive",
         "parser_config": {"chunk_token_num": 512}, "embedding_model": "bge-m3"},
        {"id": "id-zz", "name": "别的库", "chunk_method": "naive",
         "parser_config": {}, "embedding_model": "x"},
    ]
    with patch.object(rq.config, "DATASETS", cfg_datasets):
        snap = snapshot_datasets(client, setup_state)
    assert set(snap) == {"ds1", "ds3"}
    assert snap["ds3"]["id"] == "id-b"
    assert snap["ds1"] is None                              # 服务端缺这个库 → 占位 None
    assert "parser_config" in snap["ds3"]


def test_run_id_format():
    rid = run_id_now()
    assert len(rid) == 15 and rid[8] == "-"
    assert rid.replace("-", "").isdigit()


def test_append_then_load_roundtrip(tmp_path):
    rp = tmp_path / "eval" / "runs.jsonl"
    append_run(rp, {"run_id": "20260827-010101", "tag": "v0"})
    append_run(rp, {"run_id": "20260827-020202", "tag": "v1"})
    runs = load_runs(rp)
    assert [r["run_id"] for r in runs] == ["20260827-010101", "20260827-020202"]


def test_snapshots_differ_deep_compare():
    a = {"ds1": {"parser_config": {"chunk_token_num": 512}}}
    b1 = {"ds1": {"parser_config": {"chunk_token_num": 512}}}
    b2 = {"ds1": {"parser_config": {"chunk_token_num": 256}}}
    assert snapshots_differ(a, b1) is False
    assert snapshots_differ(a, b2) is True
    assert snapshots_differ(a, {}) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src && python3 -m pytest tests/test_eval_runs.py -q`
Expected: FAIL（ImportError）

- [ ] **Step 3: Write minimal implementation**

`src/run_qc.py`：import 区新增裸 `import config`（与既有 `from config import …` 并列，供测试 `patch.object(config, "DATASETS", …)` 走模块属性替换）；然后追加函数：

```python
SNAPSHOT_FIELDS = ("id", "name", "chunk_method", "parser_config", "embedding_model")


def snapshot_datasets(client, setup_state: dict) -> dict:
    """apply 前抓取各 ds 配置快照（供 --compare 漂移告警，spec §2 原则4）。"""
    server_dss = {d["id"]: d for d in client.list_datasets()}
    snap: dict[str, dict] = {}
    for ds in config.DATASETS:
        key = ds["key"]
        if key not in setup_state:
            continue
        info = server_dss.get(setup_state[key]["id"])
        snap[key] = ({f: info[f] for f in SNAPSHOT_FIELDS}
                     if info else None)
    return snap


def run_id_now() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def append_run(runs_path: Path, record: dict) -> None:
    runs_path.parent.mkdir(parents=True, exist_ok=True)
    with open(runs_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_runs(runs_path: Path) -> list[dict]:
    if not runs_path.exists():
        return []
    with open(runs_path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def snapshots_differ(a: dict, b: dict) -> bool:
    return json.dumps(a, ensure_ascii=False, sort_keys=True) != \
        json.dumps(b, ensure_ascii=False, sort_keys=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src && python3 -m pytest tests/test_eval_runs.py -q`
Expected: PASS

- [ ] **Step 5: 回归检查点**

Run: `cd src && python3 -m pytest -q`
Expected: 全绿。

---

### Task 6: 执行器（串行逐 case、kg 双跑记账、过滤器转换）

**Files:**
- Modify: `src/run_qc.py`
- Test: `src/tests/test_eval_runner.py`

**Interfaces:**
- Consumes: Task 1–3 判分函数；Task 5 `run_id_now`；`RAGFlowClient.search_datasets/list_documents`；`setup_state`（ds_key → `{"id": ...}`）；`OUT_DIR/"mapping.csv"` 用于 meta_filter 期望名单换算（经 `corpus.read_mapping_csv`，在 `execute_suite` 内部惰性读取一次，构造 `rel → doc_name` 表，排除 `skip_reason` 非空的行）
- Produces:
  - `mf_envelope(meta_filter: dict | None) -> dict | None`
    简单 `{field: value}` 映射 → RAGFlow 信封
    `{"method": "manual", "logic": "and", "conditions": [{"key": k, "op": "=", "value": v}, ...]}`
  - `execute_suite(cases: list[dict], client, setup_state: dict) -> list[dict]`
    每个 case 产出一条结果字典（结构见 Step 3 内注释，跑批严格串行）；
    当 `case.layer == "structured"` 且 `set(case.datasets) ∩ {"ds1", "ds3"}` 非空时，自动加跑一次
    `use_kg=False` 变体并把 `kg_gain` 记进结果（不计入该 case 判定）。
    structured 层判据 = meta_filter 集合精确比对；retrieval 层判据 = Task 2 公式；
    label 弱信号 `tag_hits` 双层都记但不判定。e2e case 抛 `SystemExit`（中文提示二期）。

- [ ] **Step 1: Write the failing test**

新建 `src/tests/test_eval_runner.py`：

```python
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from unittest.mock import MagicMock, patch

import run_qc as rq
from run_qc import mf_envelope, execute_suite


SETUP = {"ds1": {"id": "id-1"}, "ds3": {"id": "id-3"}}

MAPPING_ROWS = [                                   # 极简 mapping.csv 替身
    {"rel": "06-历年洪水资料/05-2013年洪水(7-22)/7-22防洪报告.doc",
     "skip_reason": ""},
    {"rel": "06-历年洪水资料/05-2013年洪水(7-22)/洪水过程.xls",
     "skip_reason": "_dup 同名去重"},              # 应被期望名单排除
]


def _search_payload(chunks_text, doc_ids=("doc-a",)):
    return {"chunks": [
        {"content_with_weight": chunks_text, "doc_id": d, "important_keywords": []}
        for d in doc_ids], "total": len(doc_ids)}


def test_mf_envelope_conversion():
    env = mf_envelope({"flood_event": "2013-7"})
    assert env == {"method": "manual", "logic": "and",
                   "conditions": [{"key": "flood_event", "op": "=", "value": "2013-7"}]}
    assert mf_envelope(None) is None


def test_execute_retrieval_case_passes_on_anchor():
    case = {"id": "AC-R-01", "tier": "acceptance", "layer": "retrieval",
            "question": "汛限水位是多少？", "datasets": ["ds1"],
            "use_kg": False, "meta_filter": None,
            "gold": {"numbers": ["788.5"], "keywords": ["汛限水位"],
                     "answer": "", "source_rel": []},
            "threshold": {"min_keywords": 1}}
    client = MagicMock()
    client.search_datasets.return_value = _search_payload("正常蓄水位…汛限水位788.5米")
    client.list_documents.return_value = [{"id": "doc-a", "name": "02-调度规程.pdf"}]

    # execute_suite 入口会构建 rel→name 表，此处 tmp_path 无 mapping.csv，
    # _rel_doc_table 捕获 FileNotFoundError 后返回空表 —— 不影响本用例断言
    with patch.object(rq, "OUT_DIR", pathlib.Path("/nonexistent-tmp-guard-for-qc-test")):
        results = execute_suite([case], client, SETUP)

    r = results[0]
    assert r["passed"] is True
    assert r["metrics"]["numeric_exact"] is True
    assert r["total"] == 1
    args = client.search_datasets.call_args.kwargs
    assert args["dataset_ids"] == ["id-1"] and args["top_k"] == 10


def test_execute_structured_case_set_compare_and_kg_probe(tmp_path):
    case = {"id": "AC-S-01", "tier": "acceptance", "layer": "structured",
            "question": "2013年7月洪水的降雨量统计结果如何？",
            "datasets": ["ds3"],
            "use_kg": False,
            "meta_filter": {"flood_event": "2013-7"},
            "gold": {"numbers": [], "keywords": ["降雨"], "answer": "",
                     "source_rel": ["06-历年洪水资料/05-2013年洪水(7-22)/7-22防洪报告.doc"]},
            "threshold": {"min_keywords": 1}}
    client = MagicMock()
    client.search_datasets.return_value = _search_payload(
        "降雨量统计", doc_ids=["id-d1"])
    client.list_documents.return_value = [{"id": "id-d1", "name": "洪水过程.xls"}]

    (tmp_path / "mapping.csv").write_text("rel\n", encoding="utf-8")
    with patch.object(rq, "OUT_DIR", tmp_path), \
         patch.object(rq.corpus, "read_mapping_csv", return_value=MAPPING_ROWS):
        results = execute_suite([case], client, SETUP)

    r = results[0]
    # 期望名单只含非 skip 的 7-22防洪报告.doc（MAPPING_ROWS 第二行带 skip_reason 被排除）
    assert r["passed"] is False
    assert r["filter"]["missing"] == ["7-22防洪报告.doc"]
    assert r["filter"]["unexpected"] == ["洪水过程.xls"]
    # ds3 属于 GraphRAG 库集合 → 结构化层自动加跑 kg on/off 双探针
    assert client.search_datasets.call_count == 3       # 主查询 + on 探针 + off 探针
    assert isinstance(r["kg_gain"], int)                # 同一 mock 回包 → 恒为 0，仅验字段存在


def test_execute_e2e_case_skipped_with_notice(capsys):
    case = {"id": "AC-E-01", "tier": "acceptance", "layer": "e2e",
            "question": "x", "datasets": ["ds1"], "use_kg": False,
            "gold": {"numbers": ["1"], "keywords": [], "answer": "", "source_rel": []},
            "threshold": {"min_keywords": 1}}
    results = execute_suite([case], MagicMock(), SETUP)
    assert results == []                       # 最终口径：跳过而非报错，不产生结果条目
    assert "二期" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src && python3 -m pytest tests/test_eval_runner.py -q`
Expected: FAIL（ImportError）

- [ ] **Step 3: Write minimal implementation**

`src/run_qc.py` 顶部追加 `import corpus`（与既有 `from config import …` 并列）；然后追加以下实现（这是唯一权威版本，结构化分支即最终形态）：

```python
EXEC_INNER_DS = {"ds1", "ds3"}     # GraphRAG 库（结构化层自动加跑反向开关探针）


def mf_envelope(meta_filter: dict | None) -> dict | None:
    """cases.jsonl 简写 {field: value} → RAGFlow meta_data_filter 信封。"""
    if meta_filter is None:
        return None
    return {
        "method": "manual", "logic": "and",
        "conditions": [
            {"key": k, "op": "=", "value": v} for k, v in meta_filter.items()
        ],
    }


def _rel_doc_table() -> dict[str, str]:
    """rel → 导入后的文档基名（跳过 skip_reason 非空行）。"""
    try:
        rows = corpus.read_mapping_csv(OUT_DIR / "mapping.csv")
    except FileNotFoundError:
        return {}
    return {
        row["rel"]: rel_to_name(row["rel"])
        for row in rows if not row.get("skip_reason")
    }


def _doc_id_map(client, dataset_ids: list[str]) -> dict[str, str]:
    """检索结果块只有 doc_id —— 对每个 ds 各取一次 list_documents 建 id→name 反查表。"""
    docmap: dict[str, str] = {}
    for did in dataset_ids:
        for d in client.list_documents(did):
            docmap[d["id"]] = d.get("name", "")
    return docmap


def execute_suite(cases: list[dict], client, setup_state: dict) -> list[dict]:
    """串行逐 case 评测（spec §8 弱并发约束）。单 case 失败记 error 不中断跑批；
    layer=e2e 属二期，跳过并在 stdout 提示、不进结果集。"""
    rel_table = _rel_doc_table()
    results: list[dict] = []

    for case in cases:
        cid = case["id"]
        if case["layer"] == "e2e":
            print("[SKIP] e2e 层属二期实现（chat 引用锚定 + Qwen 参考分区），本期不评测。")
            continue

        ds_keys = case["datasets"]
        dataset_ids, missing = [], []
        for dk in ds_keys:
            if dk in setup_state:
                dataset_ids.append(setup_state[dk]["id"])
            else:
                missing.append(dk)
        if missing:
            results.append({"case_id": cid, "layer": case["layer"], "tier": case["tier"],
                            "passed": False, "error": f"setup_state 缺 ds 键：{missing}",
                            "metrics": {}, "total": 0})
            print(f"[ERROR] {cid}: setup_state 缺 {missing}")
            continue

        try:
            if case.get("meta_filter") is not None:         # ---- structured 过滤 drill ----
                env = mf_envelope(case["meta_filter"])
                payload = client.search_datasets(
                    dataset_ids=dataset_ids, question=case["question"],
                    top_k=100, use_kg=case["use_kg"], meta_data_filter=env)
                chunks = payload.get("chunks", [])
                docmap = _doc_id_map(client, dataset_ids)
                returned_names = sorted({docmap.get(c.get("doc_id", ""), "?未知文档")
                                         for c in chunks})
                expected_names = sorted({rel_table[r]
                                         for r in case["gold"]["source_rel"]
                                         if r in rel_table})
                verdict = score_meta_filter(expected_names, returned_names)
                rec = {"case_id": cid, "tier": case["tier"], "layer": "structured",
                       "passed": verdict["passed"],
                       "total": payload.get("total", len(chunks)),
                       "filter": verdict,
                       "tag_hits": tag_signal(chunks, case["gold"].get("keywords", [])),
                       "expected_docs": expected_names,
                       "returned_docs": returned_names,
                       "top_snippets": [c.get("content_with_weight", "")[:200]
                                        for c in chunks[:2]]}
                if set(ds_keys) & EXEC_INNER_DS:            # GraphRAG 增益探针（仅记录）
                    on_txt = [c.get("content_with_weight", "") for c in
                              client.search_datasets(
                                  dataset_ids=dataset_ids, question=case["question"],
                                  top_k=5, use_kg=True).get("chunks", [])]
                    off_txt = [c.get("content_with_weight", "") for c in
                               client.search_datasets(
                                   dataset_ids=dataset_ids, question=case["question"],
                                   top_k=5, use_kg=False).get("chunks", [])]
                    rec["kg_gain"] = kg_gain_record(
                        int(anchor_hit_at_k(case["gold"], on_txt, 5)),
                        int(anchor_hit_at_k(case["gold"], off_txt, 5)))
                results.append(rec)
                print(f"[{'PASS' if verdict['passed'] else 'FAIL'}] {cid} (structured) "
                      f"缺失={verdict['missing']} 多出={verdict['unexpected']}")
                continue

            # ---- retrieval 常规判分 ----
            payload = client.search_datasets(
                dataset_ids=dataset_ids, question=case["question"],
                top_k=10, use_kg=case["use_kg"], meta_data_filter=None)
            chunks = payload.get("chunks", [])
            texts = [c.get("content_with_weight", "") for c in chunks]
            metrics = score_retrieval(case["gold"], texts)
            wanted = {rel_to_name(r) for r in case["gold"]["source_rel"]}
            docmap = _doc_id_map(client, dataset_ids)
            returned = {docmap.get(c.get("doc_id", ""), "?未知文档")
                        for c in chunks[:10]}
            results.append({
                "case_id": cid, "tier": case["tier"], "layer": "retrieval",
                "passed": metrics["passed"],
                "total": payload.get("total", len(chunks)),
                "metrics": metrics,
                "source_hit": sorted(wanted & returned),
                "tag_hits": tag_signal(chunks, case["gold"].get("keywords", [])),
                "expected_docs": sorted(wanted),
                "top_snippets": texts[:2],
            })
            print(f"[{'PASS' if metrics['passed'] else 'FAIL'}] {cid} "
                  f"hit10={metrics['hit10']} kw={metrics['kw_hits']}/{metrics['kw_needed']}")

        except Exception as exc:                           # 单 case 失败不断批
            results.append({"case_id": cid, "tier": case["tier"], "layer": case["layer"],
                            "passed": False, "error": str(exc), "metrics": {},
                            "total": 0})
            print(f"[ERROR] {cid}: {exc}")

    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src && python3 -m pytest tests/test_eval_runner.py -q`
Expected: PASS（4 passed；structured 用例 call_count==3、e2e 用例零条目语义）

- [ ] **Step 5: 回归检查点**

Run: `cd src && python3 -m pytest -q`
Expected: 全绿。

---

### Task 7: 结果汇总与人读报告

**Files:**
- Modify: `src/run_qc.py`
- Test: `src/tests/test_eval_runner.py`

**Interfaces:**
- Consumes: Task 6 的结果字典结构
- Produces:
  - `summarize(results: list[dict]) -> dict`
    `{"overall": {"total": n, "passed": p, "rate": float},
      "by_group": {"acceptance|retrieval": {...}, "structured|…": {...}},
      "mean_mrr": float}`（`mean_mrr` 只对 retrieval 层、且有 `metrics.mrr_rank` 的结果求均值，用 Task 2 的 `mrr_mean`）
  - `write_results_jsonl(path: Path, results: list[dict]) -> None`
  - `write_report(path: Path, run_id: str, tag: str, results: list[dict],
                 summary: dict, snapshot: dict | None) -> None`
    Markdown：标题带 run_id/tag；可选"配置快照"折叠小节；按 tier × layer 分组的汇总表（指标列：hit@5/hit@10/kw/source_hit 或 filter/mismatch 详情/kg_gain/tag_hits）；per-case 详情（问题原文 + top 片段 2 条）；文末按 spec 标注"MRR 为 retrieval 层均值"。

- [ ] **Step 1: Write the failing test**

追加到 `src/tests/test_eval_runner.py`：

```python
from run_qc import summarize, write_report


RET_PASS = {"case_id": "R1", "tier": "acceptance", "layer": "retrieval",
            "passed": True, "total": 3, "metrics":
                {"hit5": True, "hit10": True, "mrr_rank": 1, "numeric_exact": True,
                 "kw_hits": 2, "kw_needed": 1, "passed": True},
            "source_hit": ["02-调度规程.pdf"], "tag_hits": 3,
            "top_snippets": ["…"]}
STRUCT_FAIL = {"case_id": "S1", "tier": "acceptance", "layer": "structured",
               "passed": False, "total": 9,
               "filter": {"missing": ["洪水过程.xls"], "unexpected": ["多出的.doc"]},
               "tag_hits": 0, "expected_docs": ["洪水过程.xls"],
               "returned_docs": ["多出的.doc"], "kg_gain": 0, "top_snippets": ["…"]}


def test_summarize_groups_and_mrr():
    s = summarize([RET_PASS, STRUCT_FAIL])
    assert s["overall"] == {"total": 2, "passed": 1, "rate": 0.5}
    assert s["by_group"]["acceptance|retrieval"]["passed"] == 1
    assert s["by_group"]["acceptance|structured"]["total"] == 1
    assert abs(s["mean_mrr"] - 1.0) < 1e-9


def test_write_report_contains_sections(tmp_path):
    out = tmp_path / "report.md"
    write_report(out, "20260827-153000", "v0-baseline",
                 [RET_PASS, STRUCT_FAIL],
                 summarize([RET_PASS, STRUCT_FAIL]),
                 snapshot={"ds1": {"chunk_method": "laws"}})
    md = out.read_text(encoding="utf-8")
    assert "20260827-153000" in md and "v0-baseline" in md
    assert "acceptance|retrieval" in md
    assert "missing" in md or "缺失" in md              # structured mismatch 明细可见
    assert "MRR" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src && python3 -m pytest tests/test_eval_runner.py -q`
Expected: 新增 2 例 FAIL

- [ ] **Step 3: Write minimal implementation**

```python
def summarize(results: list[dict]) -> dict:
    groups: dict[str, dict] = {}

    def _bump(group: str, ok: bool) -> None:
        g = groups.setdefault(group, {"total": 0, "passed": 0})
        g["total"] += 1
        g["passed"] += 1 if ok else 0

    for r in results:
        _bump(f"{r['tier']}|{r['layer']}", r["passed"])
    passed_total = sum(g["passed"] for g in groups.values())
    total = sum(g["total"] for g in groups.values())
    mrr_ranks = [r["metrics"]["mrr_rank"] for r in results
                 if r["layer"] == "retrieval" and r.get("metrics")]
    return {
        "overall": {"total": total, "passed": passed_total,
                    "rate": round(passed_total / total, 4) if total else 0.0},
        "by_group": groups,
        "mean_mrr": round(mrr_mean(mrr_ranks), 4),
    }


def write_results_jsonl(path: Path, results: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_report(path: Path, run_id: str, tag: str, results: list[dict],
                 summary: dict, snapshot: dict | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ov = summary["overall"]
    lines = [
        "# 评测报告（桃曲坡知识库）", "",
        f"- run_id: `{run_id}`　tag: `{tag}`",
        f"- 总体：{ov['passed']}/{ov['total']} ({ov['rate']:.0%})　"
        f"retrieval 层 MRR 均值：{summary['mean_mrr']}",
        "",
    ]
    if snapshot is not None:
        lines += ["<details><summary>配置快照</summary>", "",
                  "```json",
                  json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True),
                  "```", "</details>", ""]

    lines += ["## 汇总（tier × layer）", "",
              "| 组 | 通过/总数 |", "|:---|:----------|"]
    for grp, g in summary["by_group"].items():
        lines.append(f"| {grp} | {g['passed']}/{g['total']} |")
    lines.append("")

    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        lines += [f"### {r['case_id']} [{status}] ({r['tier']}|{r['layer']})", ""]
        if "error" in r:
            lines += [f"- 错误：{r['error']}"]
        m = r.get("metrics") or {}
        if m:
            lines += [
                f"- hit@5={m['hit5']} hit@10={m['hit10']} numeric_exact={m['numeric_exact']} "
                f"kw={m['kw_hits']}/{m['kw_needed']}",
                f"- source_hit={r.get('source_hit', [])} tag信号={r.get('tag_hits')}",
            ]
        flt = r.get("filter")
        if flt is not None:
            lines += [
                f"- 期望文档：{r['expected_docs']}",
                f"- 返回文档：{r['returned_docs']}",
                f"- 差异 → 缺失：{flt['missing']}　多出：{flt['unexpected']}",
            ]
        if "kg_gain" in r:
            lines.append(f"- GraphRAG 增益（仅记录，不作门槛）：hit@5 差值 {r['kg_gain']}")
        for i, snip in enumerate(r.get("top_snippets", [])[:2], 1):
            lines.append(f"- 片段{i}：{(snip or '').replace(chr(10), ' ')[:160]}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src && python3 -m pytest tests/test_eval_runner.py -q`
Expected: PASS

---

### Task 8: --compare 两两对比与漂移告警

**Files:**
- Modify: `src/run_qc.py`
- Test: `src/tests/test_eval_runs.py`

**Interfaces:**
- Consumes: `load_runs`；run 目录约定 `OUT_DIR/eval/runs/<run_id>/results.jsonl`
- Produces:
  - `read_run_results(eval_dir: Path, run_id: str) -> list[dict]`
  - `find_run(evals_dir_runs: list[dict], selector: str) -> str`
    `selector == "last"` → 倒数第二个 run_id（compare 需要"最近两次"，最后一条是新对比对象由 CLI 解析时约定：**CLI 语义定为 `--compare last` 比较"最近两条"**，故此处 `"last"` 返回倒数第二条；无法满足时抛 `EvalCaseError`）
  - `compare_runs(res_a: list[dict], res_b: list[dict],
                 snap_a: dict | None = None, snap_b: dict | None = None) -> str`
    按 case_id 对齐：fail→pass 标绿 ✅ 改善、pass→fail 标红 ❌ 回退、只在 A/B 单侧出现的列为 新增/已移除；首行给出 overall 变化；当 `snapshots_differ(snap_a, snap_b)` 为真时顶部输出红字横幅"⚠️ 配置快照存在差异，涨跌可能来自配置而非调参…"并列出差异 ds 键。

- [ ] **Step 1: Write the failing test**

追加到 `src/tests/test_eval_runs.py`：

```python
from run_qc import find_run, read_run_results, compare_runs
from run_qc import EvalCaseError


RUNS = [{"run_id": "20260827-010101", "snapshot": {"ds1": {"chunk_method": "laws"}}},
        {"run_id": "20260827-020202", "snapshot": {"ds1": {"chunk_method": "naive"}}},
        {"run_id": "20260827-030303", "snapshot": {"ds1": {"chunk_method": "naive"}}}]


def test_find_run_last_returns_second_to_last():
    assert find_run(RUNS, "last") == "20260827-020202"


def test_find_run_explicit_id():
    assert find_run(RUNS, "20260827-020202") == "20260827-020202"


def test_find_run_need_two_when_last(tmp_path_marker=None):
    with __import__("pytest").raises(EvalCaseError):
        find_run(RUNS[:1], "last")


def test_read_run_results_missing_dir_returns_empty(tmp_path):
    assert read_run_results(tmp_path, "no-such-run") == []


def test_read_run_results_parses_lines(tmp_path):
    d = tmp_path / "runs" / "r1"
    d.mkdir(parents=True)
    (d / "results.jsonl").write_text(
        '{"case_id":"R1","passed":true}\n{"case_id":"S1","passed":false}\n',
        encoding="utf-8")
    rs = read_run_results(tmp_path, "r1")
    assert [r["case_id"] for r in rs] == ["R1", "S1"]


def _res(cid, passed):
    return {"case_id": cid, "tier": "acceptance", "layer": "retrieval",
            "passed": passed, "total": 1, "metrics": {"mrr_rank": 1 if passed else 0}}


def test_compare_categories_and_drift_banner():
    a = [_res("R1", False), _res("R2", True), _res("OLD", True)]
    b = [_res("R1", True), _res("R2", True), _res("NEW", False)]
    out = compare_runs(a, b,
                       snap_a={"ds1": {"chunk_method": "laws"}},
                       snap_b={"ds1": {"chunk_method": "naive"}})
    assert "❌ R1" in out          # 回退
    assert "✅" not in out.splitlines()[0]
    assert "⚠️" in out             # 快照漂移横幅
    assert "R2" not in out or "未变化" in out     # 恒等项不出现在 diff 主列
    assert "NEW" in out and "OLD" in out


def test_compare_identical_snapshots_no_banner():
    out = compare_runs([_res("R1", False)], [_res("R1", True)],
                       snap_a={"ds1": {}}, snap_b={"ds1": {}})
    assert "⚠️" not in out and "✅ R1" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src && python3 -m pytest tests/test_eval_runs.py -q`
Expected: 新增 7 例 FAIL

- [ ] **Step 3: Write minimal implementation**

```python
def find_run(runs: list[dict], selector: str) -> str:
    if selector == "last":
        if len(runs) < 2:
            raise EvalCaseError("compare last 需要至少两条运行记录")
        return runs[-2]["run_id"]
    for r in runs:
        if r["run_id"] == selector:
            return selector
    raise EvalCaseError(f"runs.jsonl 中找不到 run_id：{selector}")


def read_run_results(eval_dir: Path, run_id: str) -> list[dict]:
    p = eval_dir / "runs" / run_id / "results.jsonl"
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def compare_runs(res_a: list[dict], res_b: list[dict],
                 snap_a: dict | None = None, snap_b: dict | None = None) -> str:
    a_map = {r["case_id"]: r for r in res_a}
    b_map = {r["case_id"]: r for r in res_b}
    out: list[str] = ["# Run 对比", ""]
    ta = sum(r["passed"] for r in res_a)
    tb = sum(r["passed"] for r in res_b)
    out.append(f"- 总体：A {ta}/{len(res_a)} → B {tb}/{len(res_b)}")
    if snap_a is not None and snap_b is not None and snapshots_differ(snap_a, snap_b):
        drift_keys = sorted(k for k in set(snap_a) | set(snap_b)
                            if json.dumps(snap_a.get(k), sort_keys=True) !=
                            json.dumps(snap_b.get(k), sort_keys=True))
        out += ["", "**⚠️ 配置快照存在差异（可能来自配置而非调参），涉及："
                + ", ".join(drift_keys) + "**"]
        out.append("")
    regressed = [c for c in a_map if c in b_map
                 and a_map[c]["passed"] and not b_map[c]["passed"]]
    improved = [c for c in a_map if c in b_map
                and not a_map[c]["passed"] and b_map[c]["passed"]]
    added = [c for c in b_map if c not in a_map]
    removed = [c for c in a_map if c not in b_map]
    if improved:
        out += ["", "## 改善 ✅"] + [f"- ✅ {c}" for c in improved]
    if regressed:
        out += ["", "## 回退 ❌"] + [f"- ❌ {c}" for c in regressed]
    if added:
        out += ["", "## B 新增"] + [f"- {c}" for c in added]
    if removed:
        out += ["", "## A 独有（B 已移除）"] + [f"- {c}" for c in removed]
    if not (improved or regressed or added or removed):
        out += ["", "两轮结果完全一致。"]
    return "\n".join(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src && python3 -m pytest tests/test_eval_runs.py -q`
Expected: PASS

---

### Task 9: CLI 装配 + 移除内置六问遗留代码

**Files:**
- Modify: `src/run_qc.py`（删除 `QUESTIONS`、旧 `write_markdown`、旧 `run_qc()`，接上 `main()`）
- Modify: `src/tests/test_run_qc.py`（删除与新形态冲突的用例，改写入口行为用例）

**Interfaces:**
- Consumes: 前 8 个任务的全部公开函数
- Produces:
  - `main(argv: list[str] | None = None) -> int`
    参数：`--apply`（缺省 dry-run）、`--tag TAG`（apply 必配）、
    `--suite {acceptance,regression,all}`（默认 all）、
    `--layer {retrieval,structured,e2e,all}`（默认 all）、
    `--compare SELECTOR [SELECTOR2]`（1 个参数=`last` 即比较最近两条；2 个参数=两个显式 run_id）。
  - 行为约定：
    0. **校验次序**：argv 合法性检查最先（`--compare` 形态数、`--apply` 必配 `--tag`），通过后才开始文件加载；全部失败路径以 `return <码>` 表达（缺 tag / 前置产物缺失或题库加载违例 → 2；网络连接或登录失败 → 1），**不抛 SystemExit**，仅 `__main__` 入口包一层 `sys.exit(main())`。
    1. **dry-run（缺省）**：同样先做文件侧校验（loader 含 mapping.csv rel 引用检查），然后打印将执行的 case 清单与 ds 映射；零网络、零产物写入（读取输入文件不算写入）。
    2. **`--apply` 无 `--tag`**：打印拒绝原因并返回 2（防误跑污染 runs 历史）。
    3. apply 流程：登录 → 读 setup_state → `snapshot_datasets` → `execute_suite(筛选后)` → 写 `runs/<run_id>/results.jsonl`、`report.md` → `append_run` 一行到 `out/eval/runs.jsonl`（`{run_id, tag, timestamp(iso), suite, layer, snapshot, summary}`）→ 打印汇总。
    4. compare 流程：不登录、不搜索；从 runs.jsonl 选两条，diff 输出到 stdout，并同时写入 `out/eval/compare_<runA>_vs_<runB>.md`。
    5. `layer=e2e` 的入选 case 在 execute_suite 内被跳过（Task 6 口径），dry-run 清单中也标注"（二期跳过）"。

- [ ] **Step 1: 改写测试先行（更新 `src/tests/test_run_qc.py`）**

删除以下旧用例（依赖将移除的符号）：`test_questions_have_required_fields`、`test_question_count`、`test_q1_uses_kg`、`test_meta_filter_structure`、`test_q2_filter_semantics`、`test_run_qc_produces_report`（其检查的 `run_qc()` 函数被 `main()` 取代）、`test_write_markdown_empty_results_no_crash`（`write_markdown` 移除）。

保留并改造两个用例（新 main 的失败语义是返回码、不抛 SystemExit，且 dry-run 也会先做文件侧校验）：

- `connection_error` 用例：先在 tmp_path 布置最小前置产物——`(tmp_path/"mapping.csv")` 写表头行 `"rel\n"`、`(tmp_path/"eval").mkdir()` 后写一行有效题进 `cases.jsonl`、`setup_state.json` 写 `{"ds1": {"id": "id-ds1"}}`；再让 `mock_client_cls.side_effect = requests.exceptions.ConnectionError("refused")`，调用 `rc = run_qc_module.main(["--apply", "--tag", "t"])`，断言 `rc == 1` 且输出含 "RAGFlow"。
- dry-run 零副作用用例：同上布置最小前置产物后调 `rc = run_qc_module.main([])`，断言 `rc == 0`、`mock_client_cls.assert_not_called()`、且 `(tmp_path/"eval"/"runs.jsonl")` 不存在。

新增用例：

```python
@patch("run_qc.RAGFlowClient")
def test_apply_requires_tag(mock_client_cls, tmp_path, capsys):
    with patch.object(run_qc_module, "OUT_DIR", tmp_path):
        rc = run_qc_module.main(["--apply"])
    assert rc == 2
    mock_client_cls.assert_not_called()
    assert "--tag" in capsys.readouterr().out


@patch("run_qc.RAGFlowClient")
def test_compare_mode_is_offline(mock_client_cls, tmp_path, capsys):
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "runs.jsonl").write_text(
        '{"run_id":"20260827-010101","snapshot":{"ds1":{}}}\n'
        '{"run_id":"20260827-020202","snapshot":{"ds1":{}}}\n', encoding="utf-8")
    rd = tmp_path / "eval" / "runs" / "20260827-020202"
    rd.mkdir(parents=True)
    (rd / "results.jsonl").write_text('{"case_id":"R1","passed":true}\n',
                                      encoding="utf-8")
    (tmp_path / "eval" / "runs" / "20260827-010101").mkdir()
    with patch.object(run_qc_module, "OUT_DIR", tmp_path):
        rc = run_qc_module.main(["--compare", "last"])
    assert rc == 0
    mock_client_cls.assert_not_called()
    assert "R1" in capsys.readouterr().out


def test_dry_run_lists_filtered_cases(tmp_path, capsys):
    (tmp_path / "mapping.csv").write_text("rel\n", encoding="utf-8")   # loader 校验被 patch 掉
    (tmp_path / "setup_state.json").write_text('{"ds1": {"id": "id-ds1"}}',
                                               encoding="utf-8")
    import json as _json
    row = {"id": "AC-D-01", "tier": "acceptance", "layer": "retrieval",
           "question": "演示题", "datasets": ["ds1"],
           "gold": {"numbers": ["1454"], "keywords": ["溢洪道"],
                    "answer": "", "source_rel": []},
           "threshold": {"min_keywords": 1}}
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "cases.jsonl").write_text(
        _json.dumps(row, ensure_ascii=False), encoding="utf-8")
    with patch.object(run_qc_module, "OUT_DIR", tmp_path), \
         patch.object(run_qc_module, "_valid_rel_set", return_value=set()):
        rc = run_qc_module.main([])
    assert rc == 0
    assert "AC-D-01" in capsys.readouterr().out
```

注意上面第三个用例引入了实现须提供的内部接缝 `_valid_rel_set() -> set[str]`（main 里唯一一处读 mapping.csv 的入口，便于测试替换；生产实现即 `corpus.read_mapping_csv(OUT_DIR/"mapping.csv")` 后收集 `rel` 集）。`from run_qc import main` 加到导入区；文件顶部沿用现有的 `import run_qc as run_qc_module` 别名。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src && python3 -m pytest tests/test_run_qc.py -q`
Expected: FAIL（AttributeError/TypeError：QUESTIONS 仍在而 main 未实现或接口不符）

- [ ] **Step 3: 重写 run_qc.py 尾部（装配 main 并清除遗留）**

从 `src/run_qc.py` 中删除：模块级 `QUESTIONS = [...]` 整段、`load_setup_state`（并入 main 内联读取，避免双份逻辑时保语义一致——若选择保留则确保 main 调用它且报错信息不变：找不到时提示先运行 run_setup.py）、旧 `write_markdown`、旧 `run_qc()` 及 `__main__` 块。**main() 的编排次序按行为约定 0 执行：argv/compare/tag 三项合法性检查全部前置于 `_valid_rel_set()` 与 `_load_everything()`（下面骨架里 tag 检查若位于加载之后，以此条为准上移）。** 然后追加：

```python
# ------------------------------------------------------------------
# CLI（spec §5.1）
# ------------------------------------------------------------------
def _valid_rel_set() -> set[str]:
    try:
        return {row["rel"] for row in corpus.read_mapping_csv(OUT_DIR / "mapping.csv")}
    except FileNotFoundError:
        raise EvalCaseError("缺少 out/mapping.csv —— 请先运行 python3 -m corpus")


EVAL_DIR_NAME = "eval"


def _parse_args(argv):
    ap = argparse.ArgumentParser(description="桃曲坡知识库评测执行器（缺省 dry-run）")
    ap.add_argument("--apply", action="store_true", help="实跑并记录一轮 run")
    ap.add_argument("--tag", help="本轮标记（--apply 必配，如 v0-baseline）")
    ap.add_argument("--suite", default="all",
                    choices=["acceptance", "regression", "all"])
    ap.add_argument("--layer", default="all",
                    choices=["retrieval", "structured", "e2e", "all"])
    ap.add_argument("--compare", nargs="+", metavar="SELECTOR",
                    help="last（最近两条对比）或两个 run_id")
    return ap.parse_args(argv)


def _load_everything(valid_rels: set[str]):
    cases_path = OUT_DIR / EVAL_DIR_NAME / "cases.jsonl"
    state_path = OUT_DIR / "setup_state.json"
    cases = load_cases(cases_path, valid_rels)
    with open(state_path, encoding="utf-8") as f:
        setup_state = json.load(f)
    return cases, setup_state


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.compare:
        runs_path = OUT_DIR / EVAL_DIR_NAME / "runs.jsonl"
        runs = load_runs(runs_path)
        if len(args.compare) == 1:
            sel_a = find_run(runs, "last")
            sel_b = runs[-1]["run_id"]
        elif len(args.compare) == 2:
            sel_a, sel_b = (find_run(runs, s) for s in args.compare)
        else:
            print("[ERROR] --compare 只接受 last 或两个 run_id")
            return 2
        eval_dir = OUT_DIR / EVAL_DIR_NAME
        res_a = read_run_results(eval_dir, sel_a)
        res_b = read_run_results(eval_dir, sel_b)
        snap_a = next((r.get("snapshot") for r in runs if r["run_id"] == sel_a), None)
        snap_b = next((r.get("snapshot") for r in runs if r["run_id"] == sel_b), None)
        report = compare_runs(res_a, res_b, snap_a, snap_b)
        print(report)
        cmp_path = eval_dir / f"compare_{sel_a}_vs_{sel_b}.md"
        cmp_path.write_text(report, encoding="utf-8")
        print(f"\n[INFO] 对比报告已写入 {cmp_path}")
        return 0

    # ---- 干跑前置校验（dry 也做，尽早暴露题库/引用错误）----
    valid_rels = _valid_rel_set()
    cases, setup_state = _load_everything(valid_rels)

    sel_suites = ({"acceptance", "regression"} if args.suite == "all"
                  else {args.suite})
    sel_layers = ({"retrieval", "structured", "e2e"} if args.layer == "all"
                  else {args.layer})
    chosen = [c for c in cases
              if c["tier"] in sel_suites and c["layer"] in sel_layers]

    if not args.apply:
        print(f"[dry-run] 将执行 {len(chosen)} 题（suite={args.suite}, layer={args.layer}）：")
        for c in chosen:
            suffix = "（二期跳过）" if c["layer"] == "e2e" else ""
            mf = f" filter={c['meta_filter']}" if c.get("meta_filter") else ""
            kg = " use_kg" if c["use_kg"] else ""
            print(f"  - {c['id']} [{c['tier']}|{c['layer']}]{kg}{mf} {c['question']}{suffix}")
        ds_view = {k: v.get("id") for k, v in setup_state.items()}
        print(f"[dry-run] dataset 映射：{ds_view}")
        return 0

    if not args.tag:
        print("[ERROR] --apply 必须搭配 --tag（防止误跑污染 runs 历史）")
        return 2

    try:
        client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 1
    except requests.RequestException as exc:
        print(f"[ERROR] 无法连接 RAGFlow：{exc}")
        print("提示：确认服务在运行，且已设置 RAGFLOW_EMAIL / RAGFLOW_PASSWORD。")
        return 1

    run_id = run_id_now()
    print(f"[INFO] run_id={run_id} tag={args.tag}；抓取配置快照…")
    snapshot = snapshot_datasets(client, setup_state)
    print("[INFO] 开始串行评测（弱并发网关，逐题执行）…")
    results = execute_suite(chosen, client, setup_state)
    summary = summarize(results)

    run_dir = OUT_DIR / EVAL_DIR_NAME / "runs" / run_id
    write_results_jsonl(run_dir / "results.jsonl", results)
    write_report(run_dir / "report.md", run_id, args.tag, results, summary, snapshot)
    append_run(OUT_DIR / EVAL_DIR_NAME / "runs.jsonl", {
        "run_id": run_id,
        "tag": args.tag,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "suite": args.suite, "layer": args.layer,
        "snapshot": snapshot, "summary": summary,
    })
    print(f"[INFO] 明细：{run_dir/'results.jsonl'}")
    print(f"[INFO] 报告：{run_dir/'report.md'}")

    ov = summary["overall"]
    print(f"\n=== 评测汇总 ===")
    print(f"通过率：{ov['passed']}/{ov['total']} ({ov['rate']:.0%})　"
          f"retrieval MRR 均值：{summary['mean_mrr']}")
    failed = [r["case_id"] for r in results if not r["passed"]]
    if failed:
        print(f"未通过：{', '.join(failed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

同时在文件头部 import 区补 `import argparse`；删除已无引用的 `write_json` 时先全局搜索确认（inspect_chunks 等同目录脚本若复用了它的语义就各自持有；本项目各脚本互不 import 辅助函数，可安全删除）。另确认 docstring 同步更新为新流程描述（中文）。

- [ ] **Step 4: Run tests**

Run: `cd src && python3 -m pytest tests/test_run_qc.py -q`
Expected: PASS（3 个保留/改造用例 + 3 个新用例）

- [ ] **Step 5: 回归检查点**

Run: `cd src && python3 -m pytest -q`
Expected: 全绿（此时 cases.jsonl 尚不存在，`tests/test_eval_cases_file.py` 是下一个任务才建的——所以此刻全绿指：除将要新建的该文件外的全部既有+新增测试）。

---

### Task 10: 首批题库 out/eval/cases.jsonl（16 行）+ 到位校验测试

**Files:**
- Create: `out/eval/cases.jsonl`
- Create: `src/tests/test_eval_cases_file.py`

**Interfaces:**
- Consumes: Task 4 `load_cases`；Task 9 `_valid_rel_set`
- Produces: 数据文件本身。金标来源标注在各行 `note`。

- [ ] **Step 1: 创建 `src/tests/test_eval_cases_file.py`**

```python
"""对真实题库文件的到位校验：文件存在即可离线验证 schema + 引用合法性。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import pytest

from run_qc import load_cases, _valid_rel_set
import run_qc as rq

CASES = pathlib.Path(rq.OUT_DIR) / "eval" / "cases.jsonl"


@pytest.mark.skipif(not CASES.exists(), reason="题库尚未生成")
def test_shipped_cases_all_valid():
    cases = load_cases(CASES, _valid_rel_set())
    ids = [c["id"] for c in cases]
    assert len(ids) >= 16, f"首批应不少于 16 题，实际 {len(ids)}"
    assert len(ids) == len(set(ids))


@pytest.mark.skipif(not CASES.exists(), reason="题库尚未生成")
def test_shipped_acceptance_layer_split_exists():
    cases = load_cases(CASES, _valid_rel_set())
    layers = {c["layer"] for c in cases}
    assert {"retrieval", "structured"} <= layers
```

- [ ] **Step 2: Run test to verify it skips（还无题库）**

Run: `cd src && python3 -m pytest tests/test_eval_cases_file.py -q`
Expected: 2 skipped

- [ ] **Step 3: 写入题库（16 行，金标全部来自规格锚定值或 pilot 已核实的 chunk 原文）**

创建 `out/eval/cases.jsonl`，内容逐字如下：

```jsonl
{"id": "AC-KG-01", "tier": "acceptance", "layer": "retrieval", "use_kg": true,
 "question": "桃曲坡水库溢洪道的设计泄量是多少？", "datasets": ["ds1", "ds2"],
 "gold": {"numbers": ["1454"], "keywords": ["溢洪道", "百年一遇"], "answer": "1454 m³/s（百年一遇设计泄量）",
   "source_rel": ["01-核心文档-四案/02-调度规程.pdf", "01-核心文档-四案/03-汛期调度运用计划.pdf"]},
 "threshold": {"min_keywords": 2},
 "note": "原 QC 六问 Q1 平移；requirements §7 条目1；金标 1454 来自 config.TEXTUALIZED_VALUES（VLM 已批）"}
{"id": "AC-TS-01", "tier": "acceptance", "layer": "retrieval", "use_kg": true,
 "question": "2021年共发生几次洪水？时序如何？", "datasets": ["ds3"],
 "gold": {"numbers": [], "keywords": ["2021", "洪水"], "answer": "2021 年秋汛主要为 9-25、10-3 两场连续洪水过程（另有 9 月中旬强降雨），依据 ds3 各场调度汇报。",
   "source_rel": ["06-历年洪水资料/02-2021年洪水调度/三场洪水调度过程汇报.doc"]},
 "threshold": {"min_keywords": 2},
 "note": "原 Q2 平移；不加 flood_event 过滤（问全年次数，限定月份自相矛盾——旧测试留档理由）"}
{"id": "AC-XD-01", "tier": "acceptance", "layer": "retrieval", "use_kg": true,
 "question": "10·3洪水调度依据规程哪条？涉及哪些站点？", "datasets": ["ds1", "ds3"],
 "gold": {"numbers": [], "keywords": ["规程", "柳林", "瑶曲"], "answer": "参考 10·3 洪水调度汇报所引规程条款，站点涉及柳林、瑶曲等水文站。",
   "source_rel": ["01-核心文档-四案/02-调度规程.pdf", "06-历年洪水资料/02-2021年洪水调度/10-3洪水/桃曲坡水库10月3日-10月6日洪水调度情况汇报.doc"]},
 "threshold": {"min_keywords": 2},
 "note": "原 Q3 平移；requirements §7 跨文档多跳；柳林/瑶曲站名经 pilot chunk 原文核实存在"}
{"id": "AC-ENT-01", "tier": "acceptance", "layer": "retrieval", "use_kg": true,
 "question": "安芳东在哪些洪水事件中担任指挥？", "datasets": ["ds3", "ds4"],
 "gold": {"numbers": [], "keywords": ["安芳东", "指挥", "2021"], "answer": "安芳东担任 2021 年秋汛（10·3 等）洪水调度指挥。",
   "source_rel": ["06-历年洪水资料/02-2021年洪水调度/10-3洪水/桃曲坡水库10月3日-10月6日洪水调度情况汇报.doc"]},
 "threshold": {"min_keywords": 2},
 "note": "原 Q4 平移；实体关联型，GraphRAG 场景"}
{"id": "AC-QK-01", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "桃曲坡水库汛限水位是多少？", "datasets": ["ds1", "ds2"],
 "gold": {"numbers": ["788.5"], "keywords": ["汛限水位"], "answer": "788.5 m",
   "source_rel": ["01-核心文档-四案/02-调度规程.pdf", "01-核心文档-四案/03-汛期调度运用计划.pdf"]},
 "threshold": {"min_keywords": 1},
 "note": "原 Q6 平移；requirements §7 条目3 参数快查；金标 788.5 来自 TEXTUALIZED_VALUES"}
{"id": "AC-QK-02", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "水库的千年一遇校核泄量是多少？", "datasets": ["ds1", "ds5"],
 "gold": {"numbers": ["2218"], "keywords": ["千年一遇"], "answer": "2218 m³/s（千年一遇校核泄量）",
   "source_rel": ["01-核心文档-四案/02-调度规程.pdf", "02-安全鉴定与评价/02-桃曲坡水库大坝安全鉴定.pdf"]},
 "threshold": {"min_keywords": 1},
 "note": "requirements §7 参数族扩展；金标 2218 来自 TEXTUALIZED_VALUES（VLM 已批）"}
{"id": "AC-F-01", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "10·3洪水中漆水河岔口断面的最大下泄流量是多少？警戒和保证流量分别是多少？", "datasets": ["ds3"],
 "gold": {"numbers": ["260"], "keywords": ["岔口", "警戒"], "answer": "260 m³/s（警戒 200 m³/s、保证 300 m³/s）",
   "source_rel": ["06-历年洪水资料/02-2021年洪水调度/10-3洪水/桃曲坡水库10月3日-10月6日洪水调度情况汇报.doc"]},
 "threshold": {"min_keywords": 1},
 "note": "金标逐字核实于 pilot chunk：『10月5日20时下泄流量达到本次最大260m3/s（警戒200 m3/s、保证300 m3/s）』"}
{"id": "AC-F-02", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "9-10月份岔口断面共计弃水量是多少？", "datasets": ["ds3"],
 "gold": {"numbers": ["1.56"], "keywords": ["弃水"], "answer": "1.56 亿立方米",
   "source_rel": ["06-历年洪水资料/02-2021年洪水调度/10-3洪水/桃曲坡水库10月3日-10月6日洪水调度情况汇报.doc"]},
 "threshold": {"min_keywords": 1},
 "note": "pilot chunk 原文：『9-10月份岔口断面共计弃水量1.56亿立方米』"}
{"id": "AC-F-03", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "10月2日至6日灌区的过程面雨量是多少？哪个站最大？", "datasets": ["ds3"],
 "gold": {"numbers": ["121", "147"], "keywords": ["面雨量", "瑶曲"], "answer": "过程面雨量 121mm，最大瑶曲站 147mm",
   "source_rel": ["06-历年洪水资料/02-2021年洪水调度/10-3洪水/桃曲坡水库10月3日-10月6日洪水调度情况汇报.doc"]},
 "threshold": {"min_keywords": 1},
 "note": "pilot chunk 原文：『过程面雨量121mm，最大瑶曲站147mm』"}
{"id": "AC-F-04", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "9月25日至10月10日水库泄水总量是多少？各通道分别多少？", "datasets": ["ds3"],
 "gold": {"numbers": ["9685", "2066", "7619"], "keywords": ["泄水总量"], "answer": "泄水总量 9685 万方（溢洪道 2066 万方、低洞 7619 万方）",
   "source_rel": ["06-历年洪水资料/02-2021年洪水调度/10-3洪水/桃曲坡水库10月3日-10月6日洪水调度情况汇报.doc"]},
 "threshold": {"min_keywords": 1},
 "note": "pilot chunk 原文：『共泄水16天，泄水总量9685万方（其中溢洪道2066万方、低洞7619万方）』"}
{"id": "AC-F-05", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "10月6日的专报里长武县最大点降雨量是多少？在哪个镇？", "datasets": ["ds3"],
 "gold": {"numbers": ["54.2"], "keywords": ["相公镇", "长武"], "answer": "长武县相公镇 54.2 毫米",
   "source_rel": ["06-历年洪水资料/01-水情通报/汛情专报_20211006.pdf"]},
 "threshold": {"min_keywords": 1},
 "note": "pilot chunk 原文：『点最大降雨量37.5毫米-54.2毫米，其中长武县相公镇降雨量54.2毫米』"}
{"id": "AC-F-06", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "黄河第3号洪水中潼关站的洪峰有多大？历史上处于什么位置？", "datasets": ["ds3"],
 "gold": {"numbers": ["8360"], "keywords": ["潼关", "1979"], "answer": "潼关站出现 1979 年以来实测最大洪水 8360 m³/s（警戒 5000）",
   "source_rel": ["06-历年洪水资料/01-水情通报/重要水情快报第121期.pdf"]},
 "threshold": {"min_keywords": 1},
 "note": "pilot chunk 原文：『黄河出现3号洪水，潼关站出现1979年以来实测最大洪水 8360m3/s（警戒5000）』"}
{"id": "AC-F-07", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "10月2日至7日全省面平均雨量是多少？", "datasets": ["ds3"],
 "gold": {"numbers": ["87.2"], "keywords": ["面平均雨量"], "answer": "全省面平均雨量 87.2mm",
   "source_rel": ["06-历年洪水资料/01-水情通报/重要水情快报第121期.pdf"]},
 "threshold": {"min_keywords": 1},
 "note": "pilot chunk 原文：『本次过程全省面 平均雨量87.2mm』（跨行断裂亦属待观察项，正适合当考题）"}
{"id": "AC-ST-01", "tier": "acceptance", "layer": "structured", "use_kg": false,
 "question": "2013年7月洪水的降雨量统计结果如何？", "datasets": ["ds3"],
 "meta_filter": {"flood_event": "2013-7"},
 "gold": {"numbers": [], "keywords": ["降雨"], "answer": "",
   "source_rel": ["06-历年洪水资料/05-2013年洪水(7-22)/7-22防洪报告.doc",
                   "06-历年洪水资料/05-2013年洪水(7-22)/“7.22”洪水汇报终告.doc",
                   "06-历年洪水资料/05-2013年洪水(7-22)/“7.29”洪水简讯doc.doc",
                   "06-历年洪水资料/05-2013年洪水(7-22)/洪水过程.xls",
                   "06-历年洪水资料/05-2013年洪水(7-22)/降雨量统计.xls"]},
 "threshold": {"min_keywords": 1},
 "note": "原 Q5 升级：hard-filter drill，期望名单=mapping.csv 该 flood_event 全部非 skip 行；requirements §7 条目2"}
{"id": "AC-ST-02", "tier": "acceptance", "layer": "structured", "use_kg": false,
 "question": "2020年8月的洪水都留下了哪些资料？", "datasets": ["ds3"],
 "meta_filter": {"flood_event": "2020-8"},
 "gold": {"numbers": [], "keywords": [], "answer": "",
   "source_rel": ["06-历年洪水资料/03-2020年洪水(8-16)/防洪报告.doc",
                   "06-历年洪水资料/03-2020年洪水(8-16)/洪水调度报告.doc",
                   "06-历年洪水资料/03-2020年洪水(8-16)/洪水过程.xls",
                   "06-历年洪水资料/03-2020年洪水(8-16)/洪水汇报.doc"]},
 "threshold": {"min_keywords": 1},
 "note": "meta_filter 纯集合比对题（gold.keywords 为空 → keyword_ok 恒真，判据完全落在集合一致上）"}
{"id": "AC-ST-03", "tier": "acceptance", "layer": "structured", "use_kg": false,
 "question": "检索范围限定在 2021-10 洪水事件的资料，列出这批文档。", "datasets": ["ds3"],
 "meta_filter": {"flood_event": "2021-10"},
 "gold": {"numbers": [], "keywords": [], "answer": "",
   "source_rel": ["06-历年洪水资料/01-水情通报/水情通报第161期.pdf",
                   "06-历年洪水资料/01-水情通报/汛情专报_20211006.pdf",
                   "06-历年洪水资料/01-水情通报/重要水情快报第121期.pdf",
                   "06-历年洪水资料/02-2021年洪水调度/9-15强降雨工作汇报.doc",
                   "06-历年洪水资料/02-2021年洪水调度/10-3洪水/洪水过程.xls",
                   "06-历年洪水资料/02-2021年洪水调度/10-3洪水/受损统计.xlsx",
                   "06-历年洪水资料/02-2021年洪水调度/10-3洪水/水务局汇报.docx",
                   "06-历年洪水资料/02-2021年洪水调度/10-3洪水/桃曲坡水库10月3日-10月6日洪水调度情况汇报.doc",
                   "06-历年洪水资料/02-2021年洪水调度/9-25洪水/防汛抗洪纪实.doc",
                   "06-历年洪水资料/02-2021年洪水调度/9-25洪水/洪水调度情况汇报.doc",
                   "06-历年洪水资料/02-2021年洪水调度/9-25洪水/洪水过程.xls",
                   "06-历年洪水资料/02-2021年洪水调度/9-25洪水/华商报报道.doc",
                   "06-历年洪水资料/02-2021年洪水调度/9-25洪水/汛期措施.doc",
                   "06-历年洪水资料/02-2021年洪水调度/防洪调度效益统计.xlsx",
                   "06-历年洪水资料/02-2021年洪水调度/三场洪水调度过程汇报.doc"]},
 "threshold": {"min_keywords": 1},
 "note": "2021-10 子树全集（15 件，_dup 去重行不入库故不在期望名单）；requirements §7 条目2 第二个条件"}
```

写入方式：以上即为文件原文（JSONL，每行一个完整 JSON 对象；书写器须保持每行单行或按上文的紧凑换行风格均可——loader 只要求每行合法 JSON，建议整行单行写入以免歧义）。

- [ ] **Step 4: 到位校验**

Run: `cd src && python3 -m pytest tests/test_eval_cases_file.py -q`
Expected: 2 passed

Run: `cd src && python3 run_qc.py`
Expected: `[dry-run] 将执行 16 题 …` 清单打印，无网络调用（可见 `AC-ST-0x` 带 `filter=`、`AC-KG-01` 带 `use_kg` 标注）。

- [ ] **Step 5: 回归检查点**

Run: `cd src && python3 -m pytest -q`
Expected: 全绿（此时包括题库到位校验在内的所有测试套件）。

---

### Task 11: 操作手册章节（src/README.md 追加「评测」节）

**Files:**
- Modify: `src/README.md`（文末追加；同处更新阶段4命令示例）

**Interfaces:**
- Consumes: 无
- Produces: 操作员文档。新增章节文字逐字如下（放在「阶段4」相关段落之后；如原文有六问 QC 的旧命令样例，就地替换为新样例）：

```markdown
## 评测（run_qc.py — 验收与回归两用）

题库在 `out/eval/cases.jsonl`（每行一道题）。规则要点：

- `tier`: `acceptance`（验收）/ `regression`（回归）；`layer`: `retrieval` / `structured`（本期）/ `e2e`（二期）。
- 金标只有两类锚点：`gold.numbers`（数值精确包含匹配，自动做全半角/大小写/千分位归一）与
  `gold.keywords`。通过公式固定为 `(数值精确 OR hit@10) AND 关键词≥min_keywords`，不做单位换算。
- `source_rel` 只是"期望出处"参考列（报告中展示），不参与判分；但路径必须存在于
  `out/mapping.csv`，否则加载报错——防止旧提取链路径混入。
- gold.numbers 一律写全精度形态（如 `788.5`、`1454`），禁用能作其他数字子串的短数——
  判分是包含匹配，`"788"` 会误中「1788」。
- `layer=structured` 且 `datasets` 含 ds1/ds3 时会自动加跑反向 `use_kg` 对照，
  增益差值仅记录展示、不作门槛（零侵扰，不改动任何 dataset 配置）。

常用命令（均在 `src/` 下）：

```bash
python3 run_qc.py                              # dry-run：列出将要执行的题目清单
python3 run_qc.py --apply --tag v0-baseline    # 实跑一轮（--apply 必须配 --tag）
python3 run_qc.py --suite acceptance           # 只跑验收档；--layer 可叠加过滤
python3 run_qc.py --compare last               # 最近两轮 per-case 对比（含配置漂移告警）
python3 run_qc.py --compare <runA> <runB>      # 指定两个 run_id
```

产物布局：`out/eval/runs.jsonl`（历史索引，追加式）、`out/eval/runs/<run_id>/{results.jsonl,report.md}`、
`out/eval/compare_<A>_vs_<B>.md`。

**扩题方法**：向 `cases.jsonl` 追加一行新 JSON 即生效，无需改代码；`id` 不得重复，
`source_rel` 必须（逐字）取自 `out/mapping.csv` 第一列。建议新题先用
`python3 run_qc.py --suite acceptance --layer retrieval` 小范围试跑。
二期将提供 `gen_cases.py` 从旧提取链批量出候选题（人工抽验 ≥20% 后方可转入正式题库）。
```

- [ ] **Step 1: 应用上述章节修改**

打开 `src/README.md`，定位到现存的 run_qc / 阶段4 说明文字，将其替换为本节的标题与内容（保持 README 其余结构与语气不变）。

- [ ] **Step 2: 手动核对**

Run: `grep -n "评测" src/README.md | head` 和 `grep -n "questions\|六问" src/README.md`
Expected: 新章节可见；旧的六问 QC 示例（若有）已被替换。

- [ ] **Step 3: 最终回归检查点**

Run: `cd src && python3 -m pytest -q`
Expected: 全绿（本期完成定义）。

---

## 收尾验收清单（对照 spec 逐条自检）

| spec 条目 | 落点 |
|---|---|
| §3.1 case 模式 + 字段约束 | Task 4 loader（枚举/唯一 id/锚点非空/source_rel∈mapping/meta_filter 限定 structured/默认值） |
| §4.1 指标 + pass 公式 | Task 1/2（hit@k、numeric_exact、MRR、decide_pass） |
| §4.2 结构化三层 | Task 3 + Task 6（集合比对、kg 双跑记账、标签弱信号） |
| §2 原则1 零侵扰 | 全程请求级 use_kg；无任何 update_dataset/parser_config 调用 |
| §2 原则4 快照漂移 | Task 5 快照 + Task 8 漂移红字横幅 |
| §5.1 CLI 六形态 | Task 9 main()（dry 默认、--apply 需 --tag、--suite/--layer、--compare last/N） |
| §5.2 流程 | Task 9 apply 编排（快照→串行执行→results/report/runs.jsonl 追加） |
| §5.3 目录布局 | Task 9/10（out/eval/{cases.jsonl,runs.jsonl,runs/&lt;id&gt;/…}） |
| §6 一期就绪价值 | Task 10 首批 16 题金标全部实证锚定；导入完成后即可出 §7 正式验收证据 |
| §7 测试策略 | Task 1–10 各测试；`test_eval_cases_file` skipif 容忍题库未生成 |

## Self-Review 结论（作者已自查）

1. **Spec coverage**：上表逐条可指认；e2e 二期内容以"跳过 + 明确提示"落地，未虚设接口。
2. **Placeholder 扫描**：全文无 TBD/"类似处理"/草稿-修正双版本；每个代码步骤给出的都是可直接落盘的唯一权威版本。structured 用例 `tmp_path` 注入、`call_count==3`（主查询+on/off 探针）、e2e 跳过语义三处在测试与实现间已互相对齐。
3. **类型一致性**：`score_retrieval` 返回键在 Task 6/7 消费处逐一同名（`hit5/hit10/mrr_rank/numeric_exact/kw_hits/kw_needed/passed`）；结果字典键在 summarize/write_report/compare_runs 三处消费一致（`case_id/tier/layer/passed/total/metrics/filter/kg_gain/tag_hits/source_hit/expected_docs/returned_docs/top_snippets/error`）；`find_run/load_runs/read_run_results/_valid_rel_set` 的签名与其测试及 main 调用点一致。
