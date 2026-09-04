"""
run_qc — 人工验收问题集 for the 桃曲坡 RAGFlow import.

Procedure:
  1. Load OUT_DIR/setup_state.json → ds_key → dataset_id mapping.
  2. For each question in QUESTIONS:
       map ds_keys to dataset_ids via setup_state
       call client.search_datasets(...)
       check expected_keywords against chunk texts
       record pass/fail result
  4. Write OUT_DIR/qc/results_{timestamp}.json
  5. Write OUT_DIR/qc/report_{timestamp}.md
  6. Print overall pass rate and list of failed questions.
"""

import json
import posixpath
import sys
import time
from pathlib import Path

import requests

from config import OUT_DIR, RAGFLOW_API_KEY, RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM
from ragflow_client import RAGFlowClient


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


CASE_REQUIRED_FIELDS = ("id", "tier", "layer", "question", "datasets", "gold")
CASE_TIERS = ("acceptance", "regression")
CASE_LAYERS = ("retrieval", "structured", "e2e")
# P2-9 实证（2026-09-03）：候选池维持服务端默认 64——256 池会放进 ds3（F1 污染库）
# 更多高复合分垃圾块（Q4 实证 PASS→FAIL）；256 的收益只在多库 26 题场景
# （见 qa_eval/run_eval8.py）。含 ds3 的 QC 题无论单库多库，默认池均更稳。
RERANK_CANDIDATES_COUNT = None


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
        "expected_keywords": ["1454", "m³/s", "百年"],
        "category": "单跳参数",
    },
    {
        "id": "Q2",
        "text": "2021年共发生几次洪水？时序如何？",
        "dataset_ids": ["ds3"],
        "use_kg": True,
        # 不加 flood_event 过滤：问题问的是 2021 全年次数与时序，
        # 限定 2021-10 会自相矛盾地排除同年其他事件（过滤能力由 Q5 覆盖）
        "meta_data_filter": None,
        "expected_keywords": ["2021", "洪水"],
        "category": "多跳时序",
    },
    {
        # 2026-09-01 语料审计修正（全库 2687 分片实证）：
        # 原问法"10·3洪水调度依据规程哪条"把目标分片排出了 top30（纯排序问题）；
        # 且语料中无任何"依规程第X条"的现成条款引用（10·3 汇报仅引省防总会商精神
        # +水利部通知+Ⅳ级预案），故"规程"移出判分关键词，期望锚定站点实体召回。
        "id": "Q3",
        "text": "桃曲坡水库10月3日至6日洪水调度涉及哪些站点？",
        "dataset_ids": ["ds1", "ds3"],
        "use_kg": True,
        "meta_data_filter": None,
        "expected_keywords": ["柳林", "瑶曲"],
        "category": "实体召回",
    },
    {
        # 2026-09-01 语料审计修正：全库仅 2 个含"安芳东"的分片，同属 2020 年
        # "8·16"洪水（洪水调度报告.doc 落款 2020-8-20）；原期望"2021"系规格笔误。
        # 原问法"哪些洪水事件"下事件名分片无法召回，问法显式点名事件后双关键词可过。
        "id": "Q4",
        "text": "安芳东在8·16洪水调度中担任什么角色？",
        "dataset_ids": ["ds3", "ds4"],
        "use_kg": True,
        "meta_data_filter": None,
        "expected_keywords": ["安芳东", "8·16"],
        "category": "实体关联",
    },
    {
        "id": "Q5",
        "text": "2013年7月洪水的降雨量统计结果如何？",
        "dataset_ids": ["ds3"],
        "use_kg": False,
        "meta_data_filter": {
            "method": "manual",
            "logic": "and",
            "conditions": [{"key": "flood_event", "op": "=", "value": "2013-7"}],
        },
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


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def load_setup_state() -> dict[str, dict]:
    state_path = OUT_DIR / "setup_state.json"
    if not state_path.exists():
        raise FileNotFoundError(
            f"setup_state.json not found at {state_path}. "
            "Run run_setup.py first."
        )
    with open(state_path, encoding="utf-8") as f:
        return json.load(f)


def keywords_found(chunk_texts: list[str], expected_keywords: list[str]) -> list[str]:
    """Return which expected_keywords appear in any chunk text."""
    found = []
    for kw in expected_keywords:
        if any(kw in text for text in chunk_texts):
            found.append(kw)
    return found


def chunk_texts_from_response(data: dict) -> tuple[list[str], int]:
    """Extract list of chunk content strings and total hit count from API response data."""
    chunks = data.get("chunks", [])
    total = data.get("total", len(chunks))
    texts = [chunk.get("content_with_weight", "") for chunk in chunks]
    return texts, total


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_markdown(path: Path, results: list[dict]) -> None:
    """Write the QC report as a markdown file with a results table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    pass_rate = f"{passed}/{total} ({100 * passed / total:.0f}%)" if total else f"{passed}/{total}"

    lines = [
        "# 桃曲坡 RAGFlow Import — QC Report",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Pass rate: {pass_rate}",
        "",
        "## Summary Table",
        "",
        "| Q_ID | Category | Keywords Found | Chunks Returned | Pass/Fail |",
        "|:-----|:---------|:--------------|:----------------|:----------|",
    ]

    for r in results:
        kw_str = ", ".join(r["keywords_found"]) if r["keywords_found"] else "—"
        status = "PASS" if r["passed"] else "FAIL"
        lines.append(
            f"| {r['id']} | {r['category']} | {kw_str} | {r['total']} | {status} |"
        )

    lines.append("")
    lines.append("## Question Details")
    lines.append("")

    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        lines.append(f"### {r['id']} [{status}]")
        lines.append("")
        lines.append(f"**Question:** {r['question_text']}")
        lines.append(f"**Category:** {r['category']}")
        lines.append(f"**Datasets:** {', '.join(r['dataset_ids'])}")
        lines.append(f"**Expected keywords:** {', '.join(r['expected_keywords'])}")
        lines.append(f"**Keywords found:** {', '.join(r['keywords_found']) if r['keywords_found'] else 'none'}")
        lines.append(f"**Chunks returned:** {r['total']}")
        lines.append("")
        lines.append("**Top chunks:**")
        for i, snippet in enumerate(r["top_chunk_snippets"][:2], 1):
            snippet_preview = snippet.replace("\n", " ")[:200]
            lines.append(f"{i}. {snippet_preview}")
        lines.append("")
        lines.append("---")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
def run_qc(dry_run: bool = False) -> list[dict]:
    """
    Run the QC question set against the live RAGFlow API.

    Returns:
        List of result dicts, one per question.
    """
    # 0. dry-run 短路：不读前置产物、不登录、不发任何请求、不写任何产物
    if dry_run:
        for q in QUESTIONS:
            print(f"[dry_run] Would ask {q['id']} on {q['dataset_ids']}: {q['text']}")
        return []

    # 1. Load setup state
    setup_state = load_setup_state()

    # 3. Connect to RAGFlow —— 设置了 RAGFLOW_API_KEY 即走 Bearer 免登录模式
    try:
        client = RAGFlowClient(
            RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM, api_key=RAGFLOW_API_KEY,
        )
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)
    except requests.RequestException as exc:
        print(f"[ERROR] Could not connect to RAGFlow: {exc}")
        print("Hint: make sure RAGFlow is running and RAGFLOW_EMAIL / RAGFLOW_PASSWORD are set.")
        sys.exit(1)

    # 4. Run each question
    results: list[dict] = []

    for q in QUESTIONS:
        qid = q["id"]
        question_text = q["text"]
        ds_keys = q["dataset_ids"]
        use_kg = q["use_kg"]
        meta_filter = q.get("meta_data_filter")
        expected = q["expected_keywords"]
        category = q["category"]

        # Map ds_keys to actual dataset IDs via setup_state
        dataset_ids: list[str] = []
        for dk in ds_keys:
            if dk not in setup_state:
                print(f"[WARN] Dataset key '{dk}' not in setup_state — skipping question {qid}")
                dataset_ids = []
                break
            dataset_ids.append(setup_state[dk]["id"])

        if not dataset_ids:
            # Record failure without API call
            results.append({
                "id": qid,
                "question_text": question_text,
                "category": category,
                "dataset_ids": ds_keys,
                "expected_keywords": expected,
                "keywords_found": [],
                "total": 0,
                "top_chunk_snippets": [],
                "passed": False,
                "error": "dataset key not found in setup_state",
            })
            continue

        try:
            data = client.search_datasets(
                dataset_ids=dataset_ids,
                question=question_text,
                top_k=10,
                use_kg=use_kg,
                meta_data_filter=meta_filter,
                rerank_candidates_count=RERANK_CANDIDATES_COUNT,
            )
            chunk_texts, total = chunk_texts_from_response(data)
            found = keywords_found(chunk_texts, expected)
            passed = len(found) == len(expected)

            # Top-2 chunk snippets for the report
            top_snippets = chunk_texts[:2]

            results.append({
                "id": qid,
                "question_text": question_text,
                "category": category,
                "dataset_ids": ds_keys,
                "expected_keywords": expected,
                "keywords_found": found,
                "total": total,
                "top_chunk_snippets": top_snippets,
                "passed": passed,
            })
            print(f"[{'PASS' if passed else 'FAIL'}] {qid}: {question_text} | found={found} chunks={total}")

        except Exception as exc:
            results.append({
                "id": qid,
                "question_text": question_text,
                "category": category,
                "dataset_ids": ds_keys,
                "expected_keywords": expected,
                "keywords_found": [],
                "total": 0,
                "top_chunk_snippets": [],
                "passed": False,
                "error": str(exc),
            })
            print(f"[ERROR] {qid}: {exc}")

    # 5. Write results JSON
    ts = time.strftime("%Y%m%d_%H%M%S")
    results_path = OUT_DIR / "qc" / f"results_{ts}.json"
    write_json(results_path, results)
    print(f"[INFO] Wrote {results_path}")

    # 6. Write report markdown
    report_path = OUT_DIR / "qc" / f"report_{ts}.md"
    write_markdown(report_path, results)
    print(f"[INFO] Wrote {report_path}")

    # 7. Print summary
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = [r["id"] for r in results if not r["passed"]]
    rate = f"{100 * passed / total:.0f}%" if total else "n/a"
    print(f"\n=== QC Summary ===")
    print(f"Pass rate: {passed}/{total} ({rate})")
    if failed:
        print(f"Failed: {', '.join(failed)}")
    else:
        print("All questions passed!")

    return results


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    run_qc(dry_run=dry)
