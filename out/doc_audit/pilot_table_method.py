#!/usr/bin/env python3
"""P1 试点：ds3 两个 xlsx 转 table method 重解析 + A/B 对比。

- 8.21 洪水雨量表所在 xls（自动定位）+ 水位库容曲线推求.xls（523 块最大户）
- 改 doc 级 chunk_method=table → parse → 轮询 DONE
- 指标：切片数、nan/Unnamed 垃圾数、样本；8.21 探针同头占比（基线 7/10）
用法：RAGFLOW_API_KEY=… python3 -u pilot_table_method.py
"""
import json
import re
import sys
import time
import requests
from pathlib import Path

sys.path.insert(0, "/opt/wangjz/ragflow-import/src")
from config import PUBLIC_PEM, RAGFLOW_API_KEY, RAGFLOW_EMAIL, RAGFLOW_PASSWORD  # noqa: E402
from ragflow_client import RAGFlowClient  # noqa: E402

HERE = Path(__file__).resolve().parent
API = "http://localhost:9380/api/v1"
H = {"Authorization": f"Bearer {RAGFLOW_API_KEY}", "Content-Type": "application/json"}


def txt(c):
    return c.get("content_with_weight") or c.get("content") or ""


def junk_count(chunks):
    return sum(1 for x in chunks
               if re.search(r"Unnamed: \d|：nan", txt(x)))


def doc_status(client, ds, doc_id):
    d = client.find_document_by_name  # noqa
    r = requests.get(f"{API}/datasets/{ds}/documents", headers=H,
                     params={"page": 1, "page_size": 100}, timeout=30)
    for doc in r.json()["data"]["docs"]:
        if doc["id"] == doc_id:
            return doc.get("run"), doc.get("progress"), doc.get("chunk_count")
    return None, None, None


def main():
    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)
    ds = [d for d in client.list_datasets() if d["name"] == "洪水资料"][0]["id"]
    xls_docs = [d for d in client.list_documents(ds) if d["name"].lower().endswith((".xls", ".xlsx"))]
    print(f"ds3 xls/xlsx 文档 {len(xls_docs)} 个")

    # 定位 8.21 雨量表
    target821 = None
    for d in xls_docs:
        chunks = client.list_chunks(ds, d["id"])
        if chunks and "8.21洪水" in txt(chunks[0])[:60]:
            target821 = d
            print(f"8.21 雨量表定位: {d['name']} ({d['chunk_count']} 块)")
            break
    hll = [d for d in xls_docs if d["name"] == "水位库容曲线推求.xls"][0]
    pilots = [target821, hll]

    # before 指标
    before = {}
    for d in pilots:
        chunks = client.list_chunks(ds, d["id"])
        before[d["id"]] = {"chunks": len(chunks), "junk": junk_count(chunks)}
        print(f"[before] {d['name']}: {len(chunks)} 块, Unnamed/nan 垃圾 {junk_count(chunks)} 块")

    # 转 table method + parse
    for d in pilots:
        r = requests.put(f"{API}/datasets/{ds}/documents/{d['id']}", headers=H,
                         json={"chunk_method": "table"}, timeout=30)
        print(f"PUT chunk_method=table {d['name']}: code={r.json().get('code')}")
    r = requests.post(f"{API}/datasets/{ds}/documents/parse", headers=H,
                      json={"document_ids": [d["id"] for d in pilots]}, timeout=30)
    print("parse 触发:", r.json().get("code"), r.json().get("message", ""))

    # 轮询
    pending = {d["id"]: d["name"] for d in pilots}
    t0 = time.time()
    while pending and time.time() - t0 < 900:
        time.sleep(10)
        for did in list(pending):
            run, prog, cc = doc_status(client, ds, did)
            if run in ("DONE", "3") or (isinstance(prog, (int, float)) and prog >= 1):
                print(f"  ✓ {pending.pop(did)} DONE, {cc} 块, 用时 {time.time()-t0:.0f}s")
            elif run in ("FAIL", "4") or (isinstance(prog, (int, float)) and prog < 0):
                print(f"  ✗ {pending.pop(did)} FAIL run={run} prog={prog}")
        if pending:
            print(f"  … 等待 {len(pending)} 个 ({time.time()-t0:.0f}s)")

    # after 指标 + 探针
    print("\n== after ==")
    for d in pilots:
        chunks = client.list_chunks(ds, d["id"])
        lens = sorted(len(txt(x)) for x in chunks)
        n = len(lens)
        print(f"[after] {d['name']}: {len(chunks)} 块 (before {before[d['id']]['chunks']}), "
              f"垃圾 {junk_count(chunks)} (before {before[d['id']]['junk']}), "
              f"len p50={lens[n//2] if n else 0} max={lens[-1] if n else 0}")
        for x in chunks[:2]:
            print(f"   样本: {txt(x)[:80]!r}")

    data = requests.post(f"{API}/retrieval", headers=H, timeout=60, json={
        "question": "“8.21洪水”流域内各雨量站日降雨量",
        "dataset_ids": [ds], "top_k": 10}).json()["data"]["chunks"]
    same = sum(1 for x in data if "雨量站日降雨量统计表" in txt(x)[:40])
    print(f"\n8.21 探针: 同头表切片占 {same}/10 (基线 7/10)")
    for i, x in enumerate(data, 1):
        print(f"  {i}. [{len(txt(x)):>4}] {txt(x)[:44]!r}")

    (HERE / "pilot_table_result.json").write_text(json.dumps(
        {"before": before, "same_header_after": same}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
