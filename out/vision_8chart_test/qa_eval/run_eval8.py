#!/usr/bin/env python3
"""剩余 8 图融合文本导入后定向评测（检索层 + 问答层，仅 after）。

- 检索层：search_datasets([ds1, ds2, ds4], q, top_k=10)。判分 = all_keywords 全命中
  **全返回集并集** 且 any_keywords（如给）任一命中——注意 v0.27.1 恒返回 ~30 条、
  本脚本不切 top10，与 run_qc / run_eval_xls 的 top10 切片口径**不同**
  （2026-09-04 评审 F3 更正；历史"26/26"成绩系全返回集口径，不可直接当 top10 证据引用）。
  另候选池 rerank_candidates_count=256 为 P2-9 有意配置（见 out/retrieval_tuning/REPORT.md）。
- 问答层：POST /chats/{vision8_qa_test}/completions（绑定 ds1+ds2+ds4；不存在则按
  vision_qa_test 同款默认配置创建），判分 = 锚点出现在 answer 或引用切片并集。
- 每题额外记录：检索 top1 文档、目标融合文档是否进入召回（top1_check）。
- 用法：python3 run_eval8.py            # 跑评测并出报告
        python3 run_eval8.py --no-chat  # 只跑检索层
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
CHAT_NAME = "vision8_qa_test"
DS1 = "d6e064e0a1d711f19d7235ad4ea699d4"      # 规程与预案
DS2 = "d6ec4e36a1d711f19d7235ad4ea699d4"      # 基础数据
DS4 = "d756f7b8a1d711f19d7235ad4ea699d4"      # 组织管理
DS_IDS = [DS1, DS2, DS4]


def ensure_chat():
    """复用同名助手；不存在则创建（租户默认模型，绑定 ds1+ds2+ds4）。"""
    r = requests.get(f"{API}/chats?page_size=100", headers=H, timeout=30).json()
    for c in r.get("data", {}).get("chats", []):
        if c["name"] == CHAT_NAME:
            return c["id"], "reuse"
    body = {"name": CHAT_NAME, "dataset_ids": DS_IDS,
            "description": "剩余 8 图融合文本导入后定向评测专用（绑定 ds1 规程与预案 + ds2 基础数据 + ds4 组织管理）"}
    r = requests.post(f"{API}/chats", headers=H, json=body, timeout=30).json()
    if r.get("code") != 0:
        raise SystemExit(f"创建助手失败: {r}")
    return r["data"]["id"], "created"


def load_questions():
    return [json.loads(l) for l in
            (HERE / "questions.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def chunk_texts(chunks):
    return [c.get("content_with_weight") or c.get("content") or "" for c in chunks]


def chunk_doc(c):
    return c.get("document_name") or c.get("document_keyword") or c.get("docnm_kwd") or "?"


def score(all_kw, any_kw, texts):
    union = "\n".join(texts)
    if not all(k in union for k in (all_kw or [])):
        return False
    if any_kw and not any(k in union for k in any_kw):
        return False
    return True


REPEAT = 3  # v0.27.1 单次检索排序有向量噪声漂移，每题跑 3 次取多数
POOL = 256  # P2-9：候选池 64→256（默认 64 池会预截断目标块，G4 实证；见 out/retrieval_tuning/）


def run_retrieval(client, q):
    passes, top1, target_topn, target_top1, hits = 0, "?", False, False, set()
    for _ in range(REPEAT):
        data = client.search_datasets(DS_IDS, q["question"], top_k=10,
                                      rerank_candidates_count=POOL)
        chunks = data.get("chunks", [])
        texts = chunk_texts(chunks)
        names = [chunk_doc(c) for c in chunks]
        if score(q["all_keywords"], q["any_keywords"], texts):
            passes += 1
        if names and top1 == "?":
            top1 = names[0]
        target_topn |= q["fusion_doc"] in names
        target_top1 |= bool(names) and names[0] == q["fusion_doc"]
        hits |= {n for n, t in zip(names, texts)
                 if any(k in t for k in (q["all_keywords"] or []) + (q["any_keywords"] or []))}
    return {
        "passed": passes >= 2,          # 3 次中 ≥2 次锚点命中 = 稳定通过
        "pass_count": f"{passes}/{REPEAT}",
        "top1": top1,
        "top1_is_target": target_top1,
        "target_in_topn": target_topn,
        "hit_docs": sorted(hits),
    }


def run_chat(chat_id, q):
    payload = {"messages": [{"role": "user", "content": q["question"]}], "stream": False}
    t0 = time.time()
    r = requests.post(f"{API}/chats/{chat_id}/completions", headers=H,
                      json=payload, timeout=540)
    elapsed = round(time.time() - t0, 1)
    body = r.json()
    if body.get("code") != 0:
        return {"passed": False, "error": str(body.get("message"))[:200], "elapsed_s": elapsed}
    d = body["data"]
    refs = d.get("reference") or {}
    chunks = refs.get("chunks", []) if isinstance(refs, dict) else []
    return {
        "passed": score(q["all_keywords"], q["any_keywords"],
                        [d.get("answer", "")] + chunk_texts(chunks)),
        "answer_head": d.get("answer", "")[:160].replace("\n", " "),
        "cited_docs": sorted({chunk_doc(c) for c in chunks}),
        "elapsed_s": elapsed,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-chat", action="store_true", help="只跑检索层")
    args = ap.parse_args()

    qs = load_questions()
    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)
    chat_id, mode = (None, "-") if args.no_chat else ensure_chat()
    print(f"chat assistant: {chat_id} ({mode})，题目 {len(qs)} 题", flush=True)

    results = []
    for q in qs:
        row = {"id": q["id"], "fusion_doc": q["fusion_doc"], "coverage": q["coverage"],
               "question": q["question"], "note": q["note"]}
        row["retrieval"] = run_retrieval(client, q)
        if not args.no_chat:
            row["qa"] = run_chat(chat_id, q)
        results.append(row)
        rp = "PASS" if row["retrieval"]["passed"] else "FAIL"
        qp = "-" if args.no_chat else ("PASS" if row["qa"].get("passed") else "FAIL")
        print(f"[{q['id']}] 检索={rp} 问答={qp} top1={row['retrieval']['top1'][:28]}"
              f" 目标在召回={row['retrieval']['target_in_topn']}", flush=True)

    (HERE / "results_after8.json").write_text(
        json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                    "chat_id": chat_id, "pool": POOL,
                    "scoring": "top30 全返回集并集（非 run_qc top10，评审 F3 更正）",
                    "results": results},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    n = len(results)
    rp = sum(1 for r in results if r["retrieval"]["passed"])
    lines = ["# 剩余 8 图融合文本导入后评测报告", "",
             f"- 生成：{datetime.now().isoformat(timespec='seconds')}",
             f"- 检索层：search_datasets(ds1+ds2+ds4, top_k=10)×{REPEAT} 次取多数（≥2/3 判过），"
             "锚点全命中判分（**top30 全返回集并集口径，非 run_qc top10**；候选池 256，"
             "见 out/retrieval_tuning/REPORT.md）",
             f"- 问答层：chat 助手 vision8_qa_test（{chat_id}，绑定 ds1+ds2+ds4，top_n=6+rerank）"
             if not args.no_chat else "- 问答层：本次跳过（--no-chat）",
             f"- 题库：{n} 题（覆盖 8 篇融合文档；coverage=fusion 纯delta / both 补强 / text 校准）", "",
             "| id | 目标文档 | 覆盖 | 检索 | 次数 | top1=目标 | 目标在召回 | 问答 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in results:
        qp = "-" if args.no_chat else ("✓" if r["qa"].get("passed") else "✗")
        lines.append(f"| {r['id']} | {r['fusion_doc'][:10]} | {r['coverage']} "
                     f"| {'✓' if r['retrieval']['passed'] else '✗'} "
                     f"| {r['retrieval']['pass_count']} "
                     f"| {'✓' if r['retrieval']['top1_is_target'] else '✗'} "
                     f"| {'✓' if r['retrieval']['target_in_topn'] else '✗'} | {qp} |")
    lines.append(f"\n**检索层**：稳定通过 {rp}/{n}（{REPEAT} 次取多数）；top1 命中目标 "
                 f"{sum(1 for r in results if r['retrieval']['top1_is_target'])}/{n}；"
                 f"目标进入召回 {sum(1 for r in results if r['retrieval']['target_in_topn'])}/{n}")
    wob = [r for r in results if r["retrieval"]["pass_count"] not in (f"{REPEAT}/{REPEAT}", f"0/{REPEAT}")]
    if wob:
        lines.append(f"- 检索不稳定题（1~{REPEAT-1}/{REPEAT}，向量噪声漂移）："
                     + "、".join(f"{r['id']}({r['retrieval']['pass_count']})" for r in wob))
    if not args.no_chat:
        qp = sum(1 for r in results if r["qa"].get("passed"))
        lines.append(f"**问答层**：{qp}/{n}")
        fails = [r for r in results if not r["qa"].get("passed")]
        if fails:
            lines.append("\n## 问答层未通过题\n")
            for r in fails:
                lines.append(f"- **{r['id']}** {r['question']}\n  答案片段：{r['qa'].get('answer_head', r['qa'].get('error', '?'))}")
    rfails = [r for r in results if not r["retrieval"]["passed"]]
    if rfails:
        lines.append("\n## 检索层未通过题\n")
        for r in rfails:
            lines.append(f"- **{r['id']}** {r['question']}（命中：{r['retrieval']['hit_docs']}）")
    out = HERE / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n检索 {rp}/{n}" + ("" if args.no_chat else
          f"  问答 {sum(1 for r in results if r['qa'].get('passed'))}/{n}"))
    print(out)


if __name__ == "__main__":
    main()
