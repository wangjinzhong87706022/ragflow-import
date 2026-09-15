#!/usr/bin/env python3
"""分页拉取文档全部 chunk 并生成表格块清单（含 D.0.x 表号映射）.

要点：
  - chunks 接口 page_size ≤ 100，需分页
  - chunk 数量以 data.total 为准（文档列表 chunk_num 不可靠）
  - positions[i][0] 为页码；多表可合并在同一 chunk（p82/p87/p94 案例），
    映射表号时必须检查块尾而不只看块头
"""
import json
import os
import re

import requests
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

RAGFLOW_BASE = "https://labragf.openagp.top:9080"
RAGFLOW_API_KEY = "ragflow-XXXX"          # ← 替换
DATASET_ID = "d42ae68aab5011f1a33465638b9ea3fd"
DOC_ID = "7acf1fc2ac2011f1b8ab3155a5f51bf7"
OUT_DUMP = "output/chunks_dump.json"


def fetch_all_chunks():
    chunks, page = [], 1
    while True:
        r = requests.get(
            f"{RAGFLOW_BASE}/api/v1/datasets/{DATASET_ID}/documents/{DOC_ID}/chunks"
            f"?page={page}&page_size=100",
            headers={"Authorization": f"Bearer {RAGFLOW_API_KEY}"},
            verify=False, timeout=60,
        )
        data = r.json().get("data", {})
        batch, total = data.get("chunks", []), data.get("total", 0)
        chunks.extend(batch)
        print(f"page {page}: +{len(batch)} (total {len(chunks)}/{total})")
        if len(chunks) >= total or not batch:
            break
        page += 1
    return chunks


def find_table_numbers(content, limit=2000):
    """提取块内全部 D.0.x 表号（前 limit 字符内；多表合并块可能含多个）。"""
    return sorted(set(re.findall(r"D\.\s*0\.\s*(\d+)(?:-(\d+))?", content[:limit])))


def main():
    os.makedirs(os.path.dirname(OUT_DUMP), exist_ok=True)
    chunks = fetch_all_chunks()
    with open(OUT_DUMP, "w", encoding="utf-8") as f:
        json.dump({"total": len(chunks), "chunks": chunks}, f, ensure_ascii=False, indent=1)

    md_chunks = [c for c in chunks if (c.get("content") or "").lstrip().startswith("|")]
    print(f"\nTotal={len(chunks)}, MD-table chunks={md_chunks}, text chunks={len(chunks)-len(md_chunks)}")

    print("\n=== 表格块清单（页 / 表号 / 列数 / 行数 / 长度）===")
    for c in md_chunks:
        content = c.get("content") or ""
        positions = c.get("positions") or []
        page = min([p[0] for p in positions]) if positions else "?"
        lines = [l for l in content.split("\n") if l.strip()]
        ncols = lines[0].count("|") - 1 if lines else 0
        tnos = [f"D.0.{a}{('-' + b) if b else ''}" for a, b in find_table_numbers(content)]
        print(f"  p{page:>3} [{c['id'][:8]}] tno={','.join(tnos) or '??':14s} "
              f"cols={ncols:2d} rows={len(lines):2d} len={len(content):5d}")


if __name__ == "__main__":
    main()
