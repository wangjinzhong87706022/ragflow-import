# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
"""P0-C 对照实验：18 条未通过用例在 rerank_candidates_count=64 vs 512 下的直检召回。
只读实验：仅调 /api/v1/retrieval，不写任何数据。输出每条用例目标块是否入选 + 位次。
"""
from __future__ import annotations
import json, os, sys, io
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = os.environ["RAGFLOW_API_BASE"]
H = {"Authorization": "Bearer " + os.environ["RAGFLOW_API_KEY"]}
KB = "76a691aeae5b11f1896583d540e218a6"

# (TC, 问题, 目标编号)
CASES = [
    ("TC-002", "《SL-T-352-2020 水工混凝土试验规程》中图3.5.2展示的内容是什么？请按图中可见要素描述。", "图3.5.2"),
    ("TC-003", "《SL-T-352-2020 水工混凝土试验规程》中图3.29.2展示的内容是什么？请按图中可见要素描述。", "图3.29.2"),
    ("TC-006", "《SL-T-352-2020 水工混凝土试验规程》中图5.5.2展示的内容是什么？请按图中可见要素描述。", "图5.5.2"),
    ("TC-011", "《SL-T-352-2020 水工混凝土试验规程》中图5.13.2展示的内容是什么？请按图中可见要素描述。", "图5.13.2"),
    ("TC-012", "《SL-T-352-2020 水工混凝土试验规程》中图5.25.2-1展示的内容是什么？请按图中可见要素描述。", "图5.25.2-1"),
    ("TC-013", "《SL-T-352-2020 水工混凝土试验规程》中图5.25.2-2展示的内容是什么？请按图中可见要素描述。", "图5.25.2-2"),
    ("TC-014", "《SL-T-352-2020 水工混凝土试验规程》中图5.25.3展示的内容是什么？请按图中可见要素描述。", "图5.25.3"),
    ("TC-022", "《SL-T-352-2020 水工混凝土试验规程》图3.29.2 的内容是什么？请按图中可见要素描述。", "图3.29.2"),
    ("TC-029", "《SL-T-352-2020 水工混凝土试验规程》图5.5.2 的内容是什么？请按图中可见要素描述。", "图5.5.2"),
    ("TC-030", "《SL-T-352-2020 水工混凝土试验规程》图5.5.3 的内容是什么？请按图中可见要素描述。", "图5.5.3"),
    ("TC-074", "《SL/T 447-2026》表D.0.1-2 的表头有哪些列？", "表D.0.1-2"),
    ("TC-076", "《SL/T 447-2026》表D.0.1-3 的表头有哪些列？", "表D.0.1-3"),
    ("TC-082", "《SL/T 447-2026》表D.0.15 的表头包含哪些列？", "表D.0.15"),
    ("TC-073", "《SL/T 447-2026》表D.0.1-1（项目特性表） 的'建设条件'下的自然概况包含哪些内容？", "表D.0.1-1"),
    ("TC-079", "《SL/T 447-2026》表D.0.16（工程量汇总表） 的林草措施包含哪些条目？", "表D.0.16"),
    ("TC-081", "《SL/T 447-2026》表D.0.17（工程施工总进度表） 的进度计划栏包含几个年度列？", "表D.0.17"),
]


def probe(question: str, target: str, rcc: int, top_n: int = 12):
    r = requests.post(
        f"{BASE}/api/v1/retrieval", headers=H,
        json={
            "question": question, "dataset_ids": [KB],
            "page": 1, "page_size": top_n,
            "similarity_threshold": 0.1, "vector_similarity_weight": 0.3,
            "rerank_candidates_count": rcc,
        }, timeout=120,
    ).json()
    if r.get("code") != 0:
        return None, r.get("message", "")[:80]
    chunks = (r.get("data") or {}).get("chunks") or []
    for i, c in enumerate(chunks):
        content = c.get("content") or ""
        # 目标判定: 目标编号出现在 content 或 questions/important_keywords 字段
        ik = " ".join(c.get("important_keywords") or []) + " " + " ".join(c.get("questions") or [])
        if target.replace(" ", "") in content.replace(" ", "") or target in ik:
            return i + 1, None
    return None, None


def main():
    print(f"{'TC':8s} {'目标':12s} | {'64窗':>10s} | {'512窗':>10s}")
    flip = 0
    for tc, q, tgt in CASES:
        p64, e64 = probe(q, tgt, 64)
        p512, e512 = probe(q, tgt, 512)
        s64 = f"rank {p64}" if p64 else ("ERR" if e64 else "miss")
        s512 = f"rank {p512}" if p512 else ("ERR" if e512 else "miss")
        mark = "  <-- 翻转" if (p64 is None) != (p512 is None) else ""
        if mark: flip += 1
        print(f"{tc:8s} {tgt:12s} | {s64:>10s} | {s512:>10s}{mark}")
    print(f"\n翻转数: {flip} / {len(CASES)}")


if __name__ == "__main__":
    main()
