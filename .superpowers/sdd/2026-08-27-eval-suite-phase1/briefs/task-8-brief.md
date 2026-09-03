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

