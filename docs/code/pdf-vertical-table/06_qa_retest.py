#!/usr/bin/env python3
"""QA 检索复测：通过 chats completions API 对竖排表格提问并核验引用来源.

要点：
  - 先 POST /chats/{id}/sessions 创建会话，再 POST /chats/{id}/completions
  - stream:false 一次返回完整 answer + reference（reference.chunks[].document_name
    可核对引用了哪个文档——双文档并存时用于发现冗余命中）
  - 测试集设计：正例（表内容问答）+ 反幻觉题（缺表/空模板题）
  - 本次 8 题结论：6 全对；q2 部分幻觉（混入正文标题）；q7/q8 实质正确
"""
import json
import time

import requests
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

RAGFLOW_BASE = "https://labragf.openagp.top:9080"
RAGFLOW_API_KEY = "ragflow-XXXX"          # ← 替换
CHAT_ID = "c526f6f4abfc11f1b8ab3155a5f51bf7"   # 行业标准QA-test 助手
OUT = "output/qa_retest_results.json"

QUESTIONS = [
    ("q1", "表D.0.5 水土流失现状表包含哪些列？"),
    ("q2", "表D.0.15 水土保持措施量汇总表的表头包括哪些工程措施？"),
    ("q3", "表D.0.16 工程量汇总表包含哪些工程量项目列？"),
    ("q4", "表D.0.11 坡耕地坡度组成表的坡度分为哪几级？"),
    ("q5", "表D.0.20 水土保持措施效益计算成果表包含哪些效益指标？"),
    ("q6", "表D.0.21 经济评价分析成果表有哪些指标？"),
    ("q7", "表D.0.19 资金筹措表包含哪些资金来源列？"),
    ("q8", "表D.0.2 气象特征表包含哪些气象指标？"),
]


def create_session(name):
    r = requests.post(
        f"{RAGFLOW_BASE}/api/v1/chats/{CHAT_ID}/sessions",
        headers={"Authorization": f"Bearer {RAGFLOW_API_KEY}",
                 "Content-Type": "application/json"},
        json={"name": name}, verify=False, timeout=30,
    )
    d = r.json()
    assert d.get("code") == 0, d
    return d["data"]["id"]


def ask(session_id, question):
    r = requests.post(
        f"{RAGFLOW_BASE}/api/v1/chats/{CHAT_ID}/completions",
        headers={"Authorization": f"Bearer {RAGFLOW_API_KEY}",
                 "Content-Type": "application/json"},
        json={"question": question, "stream": False, "session_id": session_id},
        verify=False, timeout=120,
    )
    data = r.json().get("data", {})
    ref = data.get("reference") or {}
    ref_chunks = ref.get("chunks", []) if isinstance(ref, dict) else []
    sources = sorted({rc.get("document_name", "") for rc in ref_chunks})
    return data.get("answer", ""), len(ref_chunks), sources


def main():
    session_id = create_session(f"retest-{int(time.time())}")
    results = []
    for qid, q in QUESTIONS:
        answer, n_ref, sources = ask(session_id, q)
        results.append({"qid": qid, "question": q, "answer": answer,
                        "n_ref": n_ref, "sources": sources})
        print(f"\n===== {qid}: {q}")
        print(f"  refs={n_ref} sources={'; '.join(sources)}")
        print(f"  answer: {answer[:400]}")
        time.sleep(2)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\nSaved {OUT}")


if __name__ == "__main__":
    main()
