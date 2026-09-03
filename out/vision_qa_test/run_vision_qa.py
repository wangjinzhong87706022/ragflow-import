#!/usr/bin/env python3
"""四图融合文本 before/after 定向评测（检索层 + 问答层）。

- 检索层：search_datasets(ds1+ds2, q, top_k=10)，判分口径与 src/run_qc.py 一致
  （锚点关键词 ⊆ 召回切片文本并集；any_keywords 提供宽松 OR 判分，用于近似值题）。
- 问答层：POST /chats/{vision_qa_test}/completions（绑定 ds1+ds2，两阶段绑定不变），
  判分 = 锚点出现在 answer 或引用切片并集；记录 cited_docs 以判断答案来源。
- 用法：
    python3 run_vision_qa.py --phase before          # 导入融合文档前
    python3 run_vision_qa.py --phase after           # 导入并解析完成后
    python3 run_vision_qa.py --compare               # 生成对比报告
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
SRC = Path("/opt/wangjz/ragflow-import/src")
sys.path.insert(0, str(SRC))

from config import (PUBLIC_PEM, RAGFLOW_API_KEY, RAGFLOW_EMAIL,  # noqa: E402
                    RAGFLOW_PASSWORD)
from ragflow_client import RAGFlowClient  # noqa: E402

API = "http://localhost:9380/api/v1"
KEY = "ragflow-3NZDMvfCVXMVJLXYB_ixCWuduqcI-fcksfBOQ_VCE5E"
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
CHAT_ID = "b8f44fdea5d611f196c0d10ff025f702"  # vision_qa_test（ds1+ds2）
DS1 = "d6e064e0a1d711f19d7235ad4ea699d4"      # 规程与预案
DS2 = "d6ec4e36a1d711f19d7235ad4ea699d4"      # 基础数据
FUSION_DOC_NAMES = {"05-库容水位对照表.md", "06-泄流曲线.md", "02-大坝剖面图.md",
                    "溢洪道图1.md"}


def load_questions():
    qs = []
    for line in (HERE / "questions.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            qs.append(json.loads(line))
    return qs


def chunk_texts(chunks):
    out = []
    for c in chunks:
        out.append(c.get("content_with_weight") or c.get("content") or "")
    return out


def chunk_doc(c):
    return (c.get("document_name") or c.get("document_keyword")
            or c.get("docnm_kwd") or "?")


def score(all_kw, any_kw, texts):
    union = "\n".join(texts)
    need = all(k in union for k in (all_kw or []))
    opt = True
    if any_kw:
        opt = any(k in union for k in any_kw)
    return need and opt


def hit_docs(all_kw, any_kw, chunks):
    """返回命中关键词所在的文档名（判断答案来自融合文档还是旧文本）。"""
    names = []
    for c in chunks:
        t = c.get("content_with_weight") or c.get("content") or ""
        bag = (all_kw or []) + (any_kw or [])
        if any(k in t for k in bag):
            names.append(chunk_doc(c))
    return sorted(set(names))


def run_retrieval(client, q):
    data = client.search_datasets([DS1, DS2], q["question"], top_k=10)
    chunks = data.get("chunks", [])
    texts = chunk_texts(chunks)
    return {
        "passed": score(q["all_keywords"], q["any_keywords"], texts),
        "total": data.get("total"),
        "hit_docs": hit_docs(q["all_keywords"], q["any_keywords"], chunks),
    }


def run_chat(q):
    payload = {"messages": [{"role": "user", "content": q["question"]}],
               "stream": False}
    t0 = time.time()
    r = requests.post(f"{API}/chats/{CHAT_ID}/completions", headers=H,
                      json=payload, timeout=540)
    elapsed = round(time.time() - t0, 1)
    body = r.json()
    if body.get("code") != 0:
        return {"passed": False, "error": str(body.get("message"))[:200],
                "elapsed_s": elapsed}
    d = body["data"]
    refs = d.get("reference") or {}
    chunks = refs.get("chunks", []) if isinstance(refs, dict) else []
    texts = [d.get("answer", "")] + chunk_texts(chunks)
    return {
        "passed": score(q["all_keywords"], q["any_keywords"], texts),
        "answer_head": d.get("answer", "")[:160].replace("\n", " "),
        "cited_docs": sorted({chunk_doc(c) for c in chunks}),
        "elapsed_s": elapsed,
    }


def run_phase(phase):
    qs = load_questions()
    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)
    results = []
    for q in qs:
        row = {"id": q["id"], "chart": q["chart"], "question": q["question"],
               "coverage": q["coverage"]}
        row["retrieval"] = run_retrieval(client, q)
        row["qa"] = run_chat(q)
        results.append(row)
        print(f"[{phase}] {q['id']} 检索={'PASS' if row['retrieval']['passed'] else 'FAIL'}"
              f" 问答={'PASS' if row['qa'].get('passed') else 'FAIL'}"
              f" 命中={row['retrieval'].get('hit_docs')}", flush=True)
    (HERE / f"results_{phase}.json").write_text(
        json.dumps({"phase": phase, "ts": datetime.now().isoformat(timespec='seconds'),
                    "results": results}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    rp = sum(1 for r in results if r["retrieval"]["passed"])
    qp = sum(1 for r in results if r["qa"].get("passed"))
    print(f"[{phase}] 检索 {rp}/{len(results)}  问答 {qp}/{len(results)}")


def compare():
    before = json.loads((HERE / "results_before.json").read_text(encoding="utf-8"))["results"]
    after = {r["id"]: r for r in json.loads(
        (HERE / "results_after.json").read_text(encoding="utf-8"))["results"]}
    lines = ["# 四图融合文本 before/after 定向评测报告", "",
             f"- 生成：{datetime.now().isoformat(timespec='seconds')}",
             "- 检索层：search_datasets(ds1+ds2, top_k=10)，锚点关键词全命中判分（run_qc 口径）",
             f"- 问答层：chat 助手 vision_qa_test（绑定 ds1+ds2，两阶段不变）", "",
             "| id | 图 | 覆盖预期 | 检索 before→after | 问答 before→after | after 命中来源 |",
             "|---|---|---|---|---|---|"]
    for b in before:
        a = after[b["id"]]
        lines.append(
            f"| {b['id']} | {b['chart'][:8]} | {b['coverage']} "
            f"| {'✓' if b['retrieval']['passed'] else '✗'}→{'✓' if a['retrieval']['passed'] else '✗'} "
            f"| {'✓' if b['qa'].get('passed') else '✗'}→{'✓' if a['qa'].get('passed') else '✗'} "
            f"| {', '.join(a['retrieval'].get('hit_docs', []))[:60]} |")
    n = len(before)
    for layer, key in (("检索", "retrieval"), ("问答", "qa")):
        bb = sum(1 for r in before if r[key].get("passed"))
        aa = sum(1 for r in after.values() if r[key].get("passed"))
        lines.append(f"\n**{layer}层**：before {bb}/{n} → after {aa}/{n}")
    out = HERE / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["before", "after"])
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args()
    if args.compare:
        compare()
    elif args.phase:
        run_phase(args.phase)
    else:
        ap.error("需要 --phase before|after 或 --compare")


if __name__ == "__main__":
    main()
