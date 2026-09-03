#!/usr/bin/env python3
"""xls 壳迁移探针（before/after 对比用，只读）。

探针 1（8.21 同头垃圾）：query「“8.21洪水”流域内各雨量站日降雨量」，统计 top10 里
  同头垃圾切片（前 40 字含“雨量站日降雨量统计表”）数量——基线 7/10，目标 ≤2/10。
探针 2（逐文档锚点）：对 3 份目标文档各用特征锚点查询，看目标文档切片是否带锚点进 top10。
注意：v0.27.1 检索实际返回 30 条，统计一律切片 [:10]。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path("/opt/wangjz/ragflow-import/src")
sys.path.insert(0, str(SRC))

from config import (PUBLIC_PEM, RAGFLOW_API_KEY, RAGFLOW_EMAIL,  # noqa: E402
                    RAGFLOW_PASSWORD)
from ragflow_client import RAGFlowClient  # noqa: E402

DS3 = "d6fcb56ea1d711f19d7235ad4ea699d4"
TOPN = 10
REPEAT = 3

JUNK_HEADER = "雨量站日降雨量统计表"
PROBE_821_Q = "“8.21洪水”流域内各雨量站日降雨量"

# 文档 → (探针问句, 锚点)
DOC_PROBES = {
    "洪水统计(1).xls": (PROBE_821_Q, "瑶曲"),
    "较大洪水统计表.xls": ("桃曲坡流域实测最大洪峰流量的洪水是哪一场", "1353"),
    "2011年下泄水量统计.xls": ("2011年桃曲坡水库累计弃水量是多少", "10561"),
}


def txt(c):
    return c.get("content_with_weight") or c.get("content") or ""


def name(c):
    return c.get("document_name") or c.get("document_keyword") or c.get("docnm_kwd") or "?"


def main():
    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)
    print("== 探针1：8.21 同头垃圾占比（基线 7/10，目标 ≤2/10）==", flush=True)
    junk_counts, target_hits = [], 0
    for i in range(REPEAT):
        data = client.search_datasets([DS3], PROBE_821_Q, top_k=TOPN)
        chunks = (data.get("chunks") or [])[:TOPN]
        junk = sum(1 for c in chunks if JUNK_HEADER in txt(c)[:40])
        tgt = [name(c) for c in chunks if name(c) == "洪水统计(1).xls"]
        junk_counts.append(junk)
        target_hits += bool(tgt)
        print(f"  run{i+1}: 同头垃圾 {junk}/10, 洪水统计(1).xls 切片 {len(tgt)} 个", flush=True)
    print(f"  ⇒ 同头垃圾多数 {sorted(junk_counts)[REPEAT//2]}/10；"
          f"目标文档出现 {target_hits}/{REPEAT} 次", flush=True)

    print("\n== 探针2：逐文档锚点（目标文档切片带锚点进 top10）==", flush=True)
    for doc, (q, anchor) in DOC_PROBES.items():
        ok = 0
        for _ in range(REPEAT):
            data = client.search_datasets([DS3], q, top_k=TOPN)
            chunks = (data.get("chunks") or [])[:TOPN]
            if any(name(c) == doc and anchor in txt(c) for c in chunks):
                ok += 1
        print(f"  [{doc}] 锚点“{anchor}” {ok}/{REPEAT} {'✓' if ok >= 2 else '✗'}", flush=True)


if __name__ == "__main__":
    main()
