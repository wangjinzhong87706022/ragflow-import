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

