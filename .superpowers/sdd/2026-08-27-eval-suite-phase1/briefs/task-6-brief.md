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

