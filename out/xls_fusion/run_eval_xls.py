#!/usr/bin/env python3
"""xls 融合文本壳迁移 before/after 定向评测（检索层，ds3）。

- search_datasets([ds3], q, top_k=10) × REPEAT 次取多数（v0.27.1 单次检索有向量噪声漂移）。
  注意 v0.27.1 实际返回 30 条，判分与文档统计一律切片 [:10]。
- 判分口径与 run_qc/run_eval8 一致：all_keywords 全命中且 any_keywords（如给）任一命中。
- 用法：
    python3 run_eval_xls.py --phase before   # 壳迁移前基线
    python3 run_eval_xls.py --phase after    # 壳迁移后
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path("/opt/wangjz/ragflow-import/src")
sys.path.insert(0, str(SRC))

from config import (PUBLIC_PEM, RAGFLOW_API_KEY, RAGFLOW_EMAIL,  # noqa: E402
                    RAGFLOW_PASSWORD)
from ragflow_client import RAGFlowClient  # noqa: E402

DS3 = "d6fcb56ea1d711f19d7235ad4ea699d4"   # 洪水资料
DS_IDS = [DS3]
REPEAT = 3
TOPN = 10  # v0.27.1 检索实际返回 30 条，必须切片
# P2-9 实证（2026-09-03）：ds3 保持服务端默认候选池 64，不传 rerank_candidates_count。
# 256 池在 F1 垃圾重灾区会放进更多高复合分垃圾块挤掉行切片（X6/X7/X9/X10 0/3→FAIL）；
# 64 池 + 壳打标后 X5/X6/X7/X9 稳定 3/3（rank 1/5/5/3）。多库场景（26 题）才受益于 256。



def load_questions():
    return [json.loads(l) for l in
            (HERE / "questions_xls.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def chunk_texts(chunks):
    return [(c.get("content_with_weight") or c.get("content") or "") for c in chunks][:TOPN]


def chunk_doc(c):
    return c.get("document_name") or c.get("document_keyword") or c.get("docnm_kwd") or "?"


def score(all_kw, any_kw, texts):
    union = "\n".join(texts)
    if not all(k in union for k in (all_kw or [])):
        return False
    if any_kw and not any(k in union for k in any_kw):
        return False
    return True


def run_retrieval(client, q):
    passes, top1, target_topn, target_top1, hits = 0, "?", False, False, set()
    for _ in range(REPEAT):
        data = client.search_datasets(DS_IDS, q["question"], top_k=TOPN)
        chunks = (data.get("chunks") or [])[:TOPN]
        texts = chunk_texts(chunks)
        names = [chunk_doc(c) for c in chunks]
        if score(q["all_keywords"], q["any_keywords"], texts):
            passes += 1
        if names and top1 == "?":
            top1 = names[0]
        target_topn |= q["fusion_doc"] in names
        target_top1 |= bool(names) and names[0] == q["fusion_doc"]
        hits |= {n for n, t in zip(names, texts)
                 if all(k in t for k in (q["all_keywords"] or []))}
    return {"passed": passes >= 2, "pass_count": f"{passes}/{REPEAT}", "top1": top1,
            "top1_is_target": target_top1, "target_in_topn": target_topn,
            "hit_docs": sorted(hits)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["before", "after"], required=True)
    args = ap.parse_args()

    qs = load_questions()
    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)
    print(f"[{args.phase}] ds3 检索评测，{len(qs)} 题 ×{REPEAT} 取多数", flush=True)

    results = []
    for q in qs:
        row = dict(id=q["id"], fusion_doc=q["fusion_doc"], coverage=q["coverage"],
                   question=q["question"], note=q["note"], retrieval=run_retrieval(client, q))
        results.append(row)
        r = row["retrieval"]
        print(f"[{q['id']}] {'PASS' if r['passed'] else 'FAIL'} ({r['pass_count']})"
              f" top1={r['top1'][:30]} 目标在top10={r['target_in_topn']}", flush=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (HERE / f"results_xls_{args.phase}.json").write_text(
        json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                    "phase": args.phase, "results": results},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    n = len(results)
    rp = sum(1 for r in results if r["retrieval"]["passed"])
    lines = [f"# xls 融合文本壳迁移 {args.phase} 评测（ds3 检索层）", "",
             f"- 生成：{datetime.now().isoformat(timespec='seconds')}",
             f"- 口径：search_datasets([ds3], top_k=10)×{REPEAT} 取多数（≥2/3），锚点全命中判分；top10 切片",
             f"- 题库：{n} 题，全部 coverage=fusion（数值锚点出自 3 份目标 xls）", "",
             "| id | 目标文档 | 检索 | 次数 | top1=目标 | 目标在top10 |",
             "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {r['fusion_doc']} "
                     f"| {'✓' if r['retrieval']['passed'] else '✗'} "
                     f"| {r['retrieval']['pass_count']} "
                     f"| {'✓' if r['retrieval']['top1_is_target'] else '✗'} "
                     f"| {'✓' if r['retrieval']['target_in_topn'] else '✗'} |")
    lines.append(f"\n**检索层：{rp}/{n}**；top1=目标 {sum(1 for r in results if r['retrieval']['top1_is_target'])}/{n}；"
                 f"目标进 top10 {sum(1 for r in results if r['retrieval']['target_in_topn'])}/{n}")
    rfails = [r for r in results if not r["retrieval"]["passed"]]
    if rfails:
        lines.append("\n## 未通过题\n")
        for r in rfails:
            lines.append(f"- **{r['id']}** {r['question']}（{r['retrieval']['pass_count']}，命中：{r['retrieval']['hit_docs']}）")
    out = HERE / f"report_xls_{args.phase}_{stamp}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[{args.phase}] 检索 {rp}/{n}", flush=True)
    print(out, flush=True)


if __name__ == "__main__":
    main()
