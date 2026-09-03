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

