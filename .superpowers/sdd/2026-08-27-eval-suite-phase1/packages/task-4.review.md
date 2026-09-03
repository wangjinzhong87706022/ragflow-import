# 评审包 — Task 4（cases.jsonl loader）

## A. 完整性证明
- 基线 = Tasks 1–3 通过时的磁盘快照（base/run_qc.py.task123.pre），当前 = 实施 Task 4 后。
- 删除行数 **0**（必须为 0 —— 证 D2 未修改/删除任何既有代码）
- 新增行数共 139；其中前 112 行为上批已批准的 Task 1–3 内容，本次任务新增段即 B 节所列。
```diff
diff --git a/opt/wangjz/ragflow-import/.superpowers/sdd/2026-08-27-eval-suite-phase1/base/run_qc.py.task123.pre b/src/run_qc.py
index 399499f..03f8d43 100644
--- a/opt/wangjz/ragflow-import/.superpowers/sdd/2026-08-27-eval-suite-phase1/base/run_qc.py.task123.pre
+++ b/src/run_qc.py
@@ -7,30 +7,204 @@ Procedure:
        map ds_keys to dataset_ids via setup_state
        call client.search_datasets(...)
        check expected_keywords against chunk texts
        record pass/fail result
   4. Write OUT_DIR/qc/results_{timestamp}.json
   5. Write OUT_DIR/qc/report_{timestamp}.md
   6. Print overall pass rate and list of failed questions.
 """
 
 import json
+import posixpath
 import sys
 import time
 from pathlib import Path
 
 import requests
 
 from config import OUT_DIR, RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM
 from ragflow_client import RAGFlowClient
 
 
+# ------------------------------------------------------------------
+# 评测纯函数（spec §4.1）：同一输入必得同一分数
+# ------------------------------------------------------------------
+def normalize_for_match(text: str) -> str:
+    """小写化 + 全角转半角 + 去千分位逗号。只做包含匹配前的归一，不做单位换算。"""
+    out_chars = []
+    prev_digit = False
+    for ch in (text or "").lower():
+        code = ord(ch)
+        if 0xFF01 <= code <= 0xFF5E:            # 全角 ASCII 区
+            ch = chr(code - 0xFEE0)
+        if ch == "," and prev_digit:            # 千分位逗号（仅数字后跟数字场景，宽泛去除无害：
+            out_chars.append("")                # 普通“,”紧跟数字的组合中文文本中不存在）
+            continue
+        out_chars.append(ch)
+        prev_digit = ch.isdigit()
+    return "".join(out_chars)
+
+
+def numeric_hits(numbers: list[str], texts: list[str]) -> int:
+    norm_texts = [normalize_for_match(t) for t in texts]
+    return sum(
+        1 for n in numbers
+        if n and any(normalize_for_match(n) in t for t in norm_texts)
+    )
+
+
+def keyword_hits(keywords: list[str], texts: list[str]) -> int:
+    norm_texts = [normalize_for_match(t) for t in texts]
+    return sum(
+        1 for kw in keywords
+        if kw and any(normalize_for_match(kw) in t for t in norm_texts)
+    )
+
+
+def anchor_hit_at_k(gold: dict, texts_ranked: list[str], k: int) -> bool:
+    """hit@k：gold.numbers ∪ keywords 任一命中前 k 名 chunk。空集 gold 由 loader 拒绝。"""
+    topk = texts_ranked[:k]
+    return numeric_hits(gold.get("numbers", []), topk) > 0 or \
+        keyword_hits(gold.get("keywords", []), topk) > 0
+
+
+def decide_pass(*, numeric_exact: bool, hit10: bool, kw_hits: int, min_keywords: int) -> bool:
+    """spec §4.1 通过公式，运算顺序显式化。min_keywords 为 0/None（对应
+    gold.keywords 为空）时关键词门恒真——数值锚点独立承载该 case。"""
+    anchor_gate = bool(numeric_exact) or bool(hit10)
+    kw_ok = True if not min_keywords else kw_hits >= int(min_keywords)
+    return anchor_gate and kw_ok
+
+
+def score_retrieval(gold: dict, texts: list[str]) -> dict:
+    """对单个 case 的检索结果打分（text 相关度序，最多取 10 条）。"""
+    hit5 = anchor_hit_at_k(gold, texts, 5)
+    hit10 = anchor_hit_at_k(gold, texts, 10)
+    numbers, keywords = gold.get("numbers", []), gold.get("keywords", [])
+    numeric_exact = numeric_hits(numbers, texts[:10]) > 0 and bool(numbers)
+
+    mrr_rank = 0
+    for rank, t in enumerate(texts[:10], start=1):
+        if numeric_hits(numbers, [t]) or keyword_hits(keywords, [t]):
+            mrr_rank = rank
+            break
+
+    kw_hits = keyword_hits(keywords, texts[:10])
+    kw_needed = 0 if not keywords else \
+        int(gold.get("threshold", {}).get("min_keywords", 1))
+    passed = decide_pass(
+        numeric_exact=numeric_exact, hit10=hit10,
+        kw_hits=kw_hits, min_keywords=kw_needed,
+    )
+    return {
+        "hit5": hit5, "hit10": hit10, "mrr_rank": mrr_rank,
+        "numeric_exact": numeric_exact, "kw_hits": kw_hits,
+        "kw_needed": kw_needed, "passed": passed,
+    }
+
+
+def mrr_mean(mrr_ranks: list[int]) -> float:
+    """MRR = Σ(1/rank) / n；rank 为 0（无命中）计 0 分。"""
+    if not mrr_ranks:
+        return 0.0
+    return sum(1.0 / r for r in mrr_ranks if r > 0) / len(mrr_ranks)
+
+
+def rel_to_name(rel: str) -> str:
+    return posixpath.basename(rel)
+
+
+def score_meta_filter(expected_names: list[str], returned_names: list[str]) -> dict:
+    """元数据硬过滤 drill：返回文档名单与期望名单精确一致才 pass（顺序无关）。"""
+    exp, got = set(expected_names), set(returned_names)
+    return {
+        "passed": exp == got,
+        "missing": sorted(exp - got),
+        "unexpected": sorted(got - exp),
+    }
+
+
+def tag_signal(chunks: list[dict], expected_tags: list[str]) -> int:
+    seen = set()
+    for c in chunks[:10]:
+        for kw in c.get("important_keywords") or []:
+            if kw in expected_tags:
+                seen.add(kw)
+    return len(seen)
+
+
+def kg_gain_record(hit5_on: int, hit5_off: int) -> int:
+    return int(hit5_on) - int(hit5_off)
+
+
+CASE_REQUIRED_FIELDS = ("id", "tier", "layer", "question", "datasets", "gold")
+CASE_TIERS = ("acceptance", "regression")
+CASE_LAYERS = ("retrieval", "structured", "e2e")
+
+
+class EvalCaseError(ValueError):
+    """cases.jsonl 行级 schema/引用违例。"""
+
+
+def load_cases(path: Path, valid_rel_set: set[str] | None = None) -> list[dict]:
+    """解析并校验题库；通过校验的行补齐默认字段后返回。错误立即抛出并带行号。"""
+    cases: list[dict] = []
+    seen_ids: set[str] = set()
+    with open(path, encoding="utf-8") as f:
+        for lineno, line in enumerate(f, start=1):
+            raw = line.strip()
+            if not raw:
+                continue
+            try:
+                case = json.loads(raw)
+            except json.JSONDecodeError as exc:
+                raise EvalCaseError(f"第 {lineno} 行不是合法 JSON：{exc}") from exc
+
+            for field in CASE_REQUIRED_FIELDS:
+                if field not in case:
+                    raise EvalCaseError(f"第 {lineno} 行缺少必填字段 {field}")
+            cid = case["id"]
+            if cid in seen_ids:
+                raise EvalCaseError(f"第 {lineno} 行 id 重复：{cid}")
+            seen_ids.add(cid)
+            if case["tier"] not in CASE_TIERS:
+                raise EvalCaseError(f"{cid}: tier 必须是 {CASE_TIERS}")
+            if case["layer"] not in CASE_LAYERS:
+                raise EvalCaseError(f"{cid}: layer 必须是 {CASE_LAYERS}")
+            if not isinstance(case["datasets"], list) or not case["datasets"]:
+                raise EvalCaseError(f"{cid}: datasets 必须是非空列表")
+
+            gold = case["gold"]
+            if not isinstance(gold, dict):
+                raise EvalCaseError(f"{cid}: gold 必须是对象")
+            if not gold.get("numbers") and not gold.get("keywords"):
+                raise EvalCaseError(f"{cid}: 金标锚点至少要有 numbers 或 keywords 之一")
+
+            rels = gold.get("source_rel") or []
+            if valid_rel_set is not None:
+                for rel in rels:
+                    if rel not in valid_rel_set:
+                        raise EvalCaseError(
+                            f"{cid}: source_rel 不在 mapping.csv 中：{rel}"
+                        )
+
+            mf = case.get("meta_filter")
+            if mf is not None and case["layer"] != "structured":
+                raise EvalCaseError(f"{cid}: meta_filter 仅允许 layer=structured 使用")
+
+            case.setdefault("use_kg", False)
+            case.setdefault("meta_filter", None)
+            case.setdefault("threshold", {}).setdefault("min_keywords", 1)
+            cases.append(case)
+    return cases
+
+
 # ------------------------------------------------------------------
 # Questions (spec §3.5)
 # ------------------------------------------------------------------
 QUESTIONS = [
     {
         "id": "Q1",
         "text": "桃曲坡水库溢洪道的设计泄量是多少？",
         "dataset_ids": ["ds1", "ds2"],
         "use_kg": True,
         "meta_data_filter": None,
```

## B. 本任务在 run_qc.py 的新增段（139..202 行，含行号）
   139	CASE_REQUIRED_FIELDS = ("id", "tier", "layer", "question", "datasets", "gold")
   140	CASE_TIERS = ("acceptance", "regression")
   141	CASE_LAYERS = ("retrieval", "structured", "e2e")
   142	
   143	
   144	class EvalCaseError(ValueError):
   145	    """cases.jsonl 行级 schema/引用违例。"""
   146	
   147	
   148	def load_cases(path: Path, valid_rel_set: set[str] | None = None) -> list[dict]:
   149	    """解析并校验题库；通过校验的行补齐默认字段后返回。错误立即抛出并带行号。"""
   150	    cases: list[dict] = []
   151	    seen_ids: set[str] = set()
   152	    with open(path, encoding="utf-8") as f:
   153	        for lineno, line in enumerate(f, start=1):
   154	            raw = line.strip()
   155	            if not raw:
   156	                continue
   157	            try:
   158	                case = json.loads(raw)
   159	            except json.JSONDecodeError as exc:
   160	                raise EvalCaseError(f"第 {lineno} 行不是合法 JSON：{exc}") from exc
   161	
   162	            for field in CASE_REQUIRED_FIELDS:
   163	                if field not in case:
   164	                    raise EvalCaseError(f"第 {lineno} 行缺少必填字段 {field}")
   165	            cid = case["id"]
   166	            if cid in seen_ids:
   167	                raise EvalCaseError(f"第 {lineno} 行 id 重复：{cid}")
   168	            seen_ids.add(cid)
   169	            if case["tier"] not in CASE_TIERS:
   170	                raise EvalCaseError(f"{cid}: tier 必须是 {CASE_TIERS}")
   171	            if case["layer"] not in CASE_LAYERS:
   172	                raise EvalCaseError(f"{cid}: layer 必须是 {CASE_LAYERS}")
   173	            if not isinstance(case["datasets"], list) or not case["datasets"]:
   174	                raise EvalCaseError(f"{cid}: datasets 必须是非空列表")
   175	
   176	            gold = case["gold"]
   177	            if not isinstance(gold, dict):
   178	                raise EvalCaseError(f"{cid}: gold 必须是对象")
   179	            if not gold.get("numbers") and not gold.get("keywords"):
   180	                raise EvalCaseError(f"{cid}: 金标锚点至少要有 numbers 或 keywords 之一")
   181	
   182	            rels = gold.get("source_rel") or []
   183	            if valid_rel_set is not None:
   184	                for rel in rels:
   185	                    if rel not in valid_rel_set:
   186	                        raise EvalCaseError(
   187	                            f"{cid}: source_rel 不在 mapping.csv 中：{rel}"
   188	                        )
   189	
   190	            mf = case.get("meta_filter")
   191	            if mf is not None and case["layer"] != "structured":
   192	                raise EvalCaseError(f"{cid}: meta_filter 仅允许 layer=structured 使用")
   193	
   194	            case.setdefault("use_kg", False)
   195	            case.setdefault("meta_filter", None)
   196	            case.setdefault("threshold", {}).setdefault("min_keywords", 1)
   197	            cases.append(case)
   198	    return cases
   199	
   200	
   201	# ------------------------------------------------------------------
   202	# Questions (spec §3.5)

## C. 新建测试文件 src/tests/test_eval_loader.py 全文（含行号）
     1	import sys, json, pathlib
     2	sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
     3	
     4	import pytest
     5	
     6	from run_qc import load_cases, EvalCaseError, CASE_REQUIRED_FIELDS
     7	
     8	
     9	RELS = {
    10	    "01-核心文档-四案/02-调度规程.pdf",
    11	    "06-历年洪水资料/05-2013年洪水(7-22)/7-22防洪报告.doc",
    12	}
    13	
    14	
    15	def _write(tmp_path, *rows):
    16	    p = tmp_path / "cases.jsonl"
    17	    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    18	    return p
    19	
    20	
    21	VALID = {
    22	    "id": "AC-T-01", "tier": "acceptance", "layer": "retrieval",
    23	    "question": "溢洪道的设计泄量是多少？",
    24	    "datasets": ["ds1"],
    25	    "gold": {"numbers": ["1454"], "keywords": ["溢洪道"],
    26	             "answer": "", "source_rel": ["01-核心文档-四案/02-调度规程.pdf"]},
    27	}
    28	
    29	
    30	def test_load_valid_case_gets_defaults(tmp_path):
    31	    cases = load_cases(_write(tmp_path, VALID), RELS)
    32	    assert cases[0]["use_kg"] is False                     # 缺省 false
    33	    assert cases[0]["threshold"]["min_keywords"] == 1      # 缺省 1
    34	    assert cases[0]["meta_filter"] is None
    35	
    36	
    37	@pytest.mark.parametrize("field,value", [("tier", "other"), ("layer", "chat")])
    38	def test_enum_violations_raise(tmp_path, field, value):
    39	    bad = json.loads(json.dumps(VALID))
    40	    bad[field] = value
    41	    with pytest.raises(EvalCaseError):
    42	        load_cases(_write(tmp_path, bad), RELS)
    43	
    44	
    45	def test_missing_required_field_raises(tmp_path):
    46	    for field in CASE_REQUIRED_FIELDS:
    47	        bad = json.loads(json.dumps(VALID))
    48	        bad.pop(field)
    49	        with pytest.raises(EvalCaseError, match=field):
    50	            load_cases(_write(tmp_path, bad), RELS)
    51	
    52	
    53	def test_empty_datasets_raises(tmp_path):
    54	    bad = json.loads(json.dumps(VALID)); bad["datasets"] = []
    55	    with pytest.raises(EvalCaseError, match="datasets"):
    56	        load_cases(_write(tmp_path, bad), RELS)
    57	
    58	
    59	def test_duplicate_ids_raise(tmp_path):
    60	    with pytest.raises(EvalCaseError, match="重复"):
    61	        load_cases(_write(tmp_path, VALID, dict(VALID)), RELS)
    62	
    63	
    64	def test_both_anchor_sets_empty_raises(tmp_path):
    65	    bad = json.loads(json.dumps(VALID))
    66	    bad["gold"]["numbers"], bad["gold"]["keywords"] = [], []
    67	    with pytest.raises(EvalCaseError, match="金标锚点"):
    68	        load_cases(_write(tmp_path, bad), RELS)
    69	
    70	
    71	def test_source_rel_not_in_mapping_raises(tmp_path):
    72	    bad = json.loads(json.dumps(VALID))
    73	    bad["gold"]["source_rel"] = ["05-基础数据与曲线/泄流曲线.png.txt"]   # 旧提取链路径混入
    74	    with pytest.raises(EvalCaseError, match="mapping.csv"):
    75	        load_cases(_write(tmp_path, bad), RELS)
    76	
    77	
    78	def test_meta_filter_outside_structured_raises(tmp_path):
    79	    bad = json.loads(json.dumps(VALID))
    80	    bad["meta_filter"] = {"flood_event": "2013-7"}
    81	    with pytest.raises(EvalCaseError, match="structured"):
    82	        load_cases(_write(tmp_path, bad), RELS)
    83	
    84	
    85	def test_none_relset_skips_source_validation(tmp_path):
    86	    bad = json.loads(json.dumps(VALID))
    87	    bad["gold"]["source_rel"] = ["任意路径.pdf"]
    88	    assert load_cases(_write(tmp_path, bad), None)[0]["question"].startswith("溢洪道")

## D. 插入点上下文核对（前接 kg_gain_record，后接旧 QUESTIONS 区段）
   127	    seen = set()
   128	    for c in chunks[:10]:
   129	        for kw in c.get("important_keywords") or []:
   130	            if kw in expected_tags:
   131	                seen.add(kw)
   132	    return len(seen)
   133	
   134	
   135	def kg_gain_record(hit5_on: int, hit5_off: int) -> int:
   136	    return int(hit5_on) - int(hit5_off)
   137	
   138	
   139	CASE_REQUIRED_FIELDS = ("id", "tier", "layer", "question", "datasets", "gold")
   140	CASE_TIERS = ("acceptance", "regression")
   141	CASE_LAYERS = ("retrieval", "structured", "e2e")
   142	
……
   198	    return cases
   199	
   200	
   201	# ------------------------------------------------------------------
   202	# Questions (spec §3.5)
   203	# ------------------------------------------------------------------
   204	QUESTIONS = [
   205	    {
   206	        "id": "Q1",
   207	        "text": "桃曲坡水库溢洪道的设计泄量是多少？",
   208	        "dataset_ids": ["ds1", "ds2"],
   209	        "use_kg": True,
   210	        "meta_data_filter": None,
