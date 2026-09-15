# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
"""Add recall keywords (表号/图号 + 名称) to table/image chunks.

RAGFlow matches keyword recall against a chunk's ``important_kwd``. Table and
figure chunks produced by MinerU often carry the caption in the content, but
the table/figure number is not weighted, so "图3.2.3 / 表D.0.1-1" style queries
miss. This script extracts the number + name and PATCHes ``important_keywords``.

Usage:
  python 14_add_recall_keywords.py detect --kb <kb> --doc <doc>
  python 14_add_recall_keywords.py apply  --kb <kb> --doc <doc> [--overwrite]
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import requests

BASE = os.getenv("RAGFLOW_API_BASE", "https://labragf.openagp.top:9080")
API_KEY = os.getenv("RAGFLOW_API_KEY", "")
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

RE_TABLE_TAG = re.compile(r"(?is)</table>\s*(.{0,80})")
RE_TABLE_NO = re.compile(r"表\s*([A-Za-z]?\.?\s*\d+(?:\.\d+)*(?:-\d+)?)")
RE_FIG_NO = re.compile(r"图\s*(\d+(?:\.\d+)*(?:-\d+)?)")
RE_CJK_NAME = re.compile(r"[\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9（）()、·\- ]{1,24}")


def _req(method: str, path: str, **kw):
    r = requests.request(method, f"{BASE}{path}", headers=HEADERS, timeout=kw.pop("timeout", 120), **kw)
    r.raise_for_status()
    return r


def list_chunks(kb: str, doc: str) -> list[dict]:
    out, page = [], 1
    while True:
        d = _req("GET", f"/api/v1/datasets/{kb}/documents/{doc}/chunks", params={"page": page, "page_size": 100}).json().get("data") or {}
        b = d.get("chunks") or []
        if not b:
            break
        out += b
        if len(out) >= (d.get("total") or 0) or page > 60:
            break
        page += 1
    return out


def kw_table(content: str) -> list[str]:
    idx = content.rfind("</table>")
    cap = re.sub(r"\s+", "", content[idx + len("</table>"):]) if idx >= 0 else ""
    if not cap:
        return []
    m = RE_TABLE_NO.search(cap)
    kws: list[str] = []
    if m:
        num = m.group(1).replace(" ", "")
        kws += [f"表{num}", num]
        name = cap[m.end():]
    else:
        name = cap
    name = re.sub(r"^[^\u4e00-\u9fff]+", "", name)[:30]
    name = re.split(r"[（(]", name)[0] or name
    if name:
        kws.append(name)
        if m:
            kws.append(f"表{m.group(1).replace(' ', '')}{name}")
    return list(dict.fromkeys([k for k in kws if k]))


def kw_image(content: str) -> list[str]:
    text = re.sub(r"\s+", " ", content)
    kws: list[str] = []
    m = re.search(r"图\s*(\d+(?:\.\d+)*(?:-\d+)?)\s*([\u4e00-\u9fff][^；;，,V]{0,28})?", text)
    if m:
        num = m.group(1).replace(" ", "")
        kws += [f"图{num}", num]
        name = (m.group(2) or "").strip()
        if name:
            kws.append(name.rstrip("，,、"))
            kws.append(f"图{num}{name}")
    if not m:
        head = re.split(r"Visual Type|Axes / Legends|图像|图中", text)[0]
        for n in RE_CJK_NAME.findall(head):
            n = n.strip()
            if len(n) >= 3:
                kws.append(n)
    return list(dict.fromkeys([k for k in kws if k]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["detect", "apply"])
    ap.add_argument("--kb", required=True)
    ap.add_argument("--doc", required=True)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--dump", default="", help="write the plan to a UTF-8 JSON file")
    args = ap.parse_args()

    chunks = list_chunks(args.kb, args.doc)
    plan = []
    for c in chunks:
        typ = c.get("doc_type_kwd")
        if typ not in ("table", "image"):
            continue
        kws = kw_table(c.get("content", "")) if typ == "table" else kw_image(c.get("content", ""))
        if not kws:
            continue
        existing = c.get("important_keywords") or c.get("important_kwd") or []
        if isinstance(existing, str):
            existing = [existing]
        if existing and not args.overwrite:
            continue
        plan.append((c["id"], typ, kws))

    print(f"table/image chunks: {len([c for c in chunks if c.get('doc_type_kwd') in ('table', 'image')])} | to update: {len(plan)}")
    if args.dump:
        import json
        json.dump([{"id": cid, "type": typ, "keywords": kws} for cid, typ, kws in plan], open(args.dump, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for cid, typ, kws in plan[:25]:
        print(f"  {typ:5s} {cid} -> {kws[:5]}")

    if args.mode == "detect":
        return 0
    ok = 0
    for cid, typ, kws in plan:
        r = _req("PATCH", f"/api/v1/datasets/{args.kb}/documents/{args.doc}/chunks/{cid}", json={"important_keywords": kws}).json()
        ok += r.get("code") == 0
    print(f"updated {ok}/{len(plan)} chunks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
