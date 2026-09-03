#!/usr/bin/env python3
"""Agentic RAG 验证矩阵：Q3/Q4 × reasoning(0 直答 / 2 medium 分解检索 / 4 ultra)。
只读知识库；每次 completions 结果存 out/agentic_rag_test/<Q>_<mode>.json。
用法: python3 run_matrix.py [--modes 0,2,4]
"""
import json
import pathlib
import sys
import time

import requests

API = "http://localhost:9380/api/v1"
KEY = "ragflow-3NZDMvfCVXMVJLXYB_ixCWuduqcI-fcksfBOQ_VCE5E"
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
CHAT_ID = "0532598ea51611f19d7235ad4ea699d4"  # qc_qa_test：绑定 ds1/ds3/ds4
OUT = pathlib.Path(__file__).resolve().parent

QUESTIONS = {
    "Q3": "10·3洪水调度依据规程哪条？涉及哪些站点？",
    "Q4": "安芳东在哪些洪水事件中担任指挥？",
}
KEYWORDS = {
    "Q3": ["规程", "柳林", "瑶曲"],
    "Q4": ["安芳东", "8·16", "2020"],
}


def ask(question: str, reasoning: str, timeout: int = 540) -> dict:
    payload = {
        "messages": [{"role": "user", "content": question}],
        "stream": False,
        "reasoning": reasoning,
    }
    t0 = time.time()
    r = requests.post(f"{API}/chats/{CHAT_ID}/completions", headers=H,
                      json=payload, timeout=timeout)
    elapsed = round(time.time() - t0, 1)
    body = r.json()
    if body.get("code") != 0:
        return {"error": body.get("message"), "elapsed_s": elapsed}
    d = body["data"]
    refs = d.get("reference") or {}
    chunks = refs.get("chunks", []) if isinstance(refs, dict) else []
    return {
        "elapsed_s": elapsed,
        "answer": d.get("answer", ""),
        "cited_docs": sorted({c.get("document_keyword", "?") for c in chunks}),
        "cited_chunk_heads": [
            (c.get("document_keyword", "?"),
             (c.get("content_with_weight") or c.get("content") or "")[:80].replace("\n", " "))
            for c in chunks
        ][:10],
        "n_cited_chunks": len(chunks),
    }


def main(modes):
    for qid, text in QUESTIONS.items():
        for mode in modes:
            tag = f"{qid}_r{mode}"
            print(f"=== {tag}: {text}", flush=True)
            res = ask(text, str(mode))
            res.update({"question": text, "reasoning": str(mode)})
            blob = json.dumps(res, ensure_ascii=False)
            hit = [k for k in KEYWORDS[qid] if k in res.get("answer", "")]
            res["keywords_hit"] = hit
            print(f"    {res.get('elapsed_s')}s 引用{res.get('n_cited_chunks')}片 "
                  f"关键词={hit}", flush=True)
            ans = (res.get("answer") or "")[:300].replace("\n", " ")
            print(f"    答: {ans}", flush=True)
            (OUT / f"{tag}.json").write_text(
                json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    modes = [0, 2, 4]
    if "--modes" in sys.argv:
        modes = [int(x) for x in sys.argv[sys.argv.index("--modes") + 1].split(",")]
    main(modes)
