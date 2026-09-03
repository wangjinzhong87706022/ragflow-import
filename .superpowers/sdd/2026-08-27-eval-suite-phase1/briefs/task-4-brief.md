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


---
## 控制器补充裁定（评审 D1 Important I-1 落点）

评审要求把「gold.numbers 一律写全精度形态」落到题库编写规范。裁定：**loader 不加代码级校验**
（spec §3.1 字段约束之外扩权需改规格，且语义上无法机判“是否为某数子串”）；
规则改落在两处：(a) Task 11 README 扩题方法 bullet（计划文本已由控制器同步增补）；
(b) Task 10 题库内容编写时控制器自检（现有 16 题金标均为全精度形态，已合规）。
本任务你无需为此做任何实现改动。
