# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
# 文中的 KB / document / chunk ID 来自本次评测实例，按需替换。
"""Merge cross-page continued tables (续表) inside a document's chunks.

MinerU emits one table block per page, so a "continued table" that spans
several pages becomes several RAGFlow chunks, each repeating the header and
carrying a ``续表 <表号>`` caption. This script groups those chunks and merges
them into a single chunk, using only the existing RAGFlow HTTP API.

Detection (conservative by default):
  * a chunk starts a new table when its caption is ``表 <id> ...``;
  * a chunk continues the current table when its caption is ``续表 <id>`` and
    the id matches the open table (page adjacency is reported, not required);
  * with ``--aggressive`` also merge when the header row is byte-identical and
    the table id matches, even if the ``续表`` marker is missing.

Actions (only with --apply):
  * PATCH the first chunk of each group with the merged ``<table>`` content
    (header de-duplicated) plus the merged positions;
  * DELETE the remaining chunks of the group.

Usage:
  python 13_merge_continued_tables.py detect --kb <kb_id> --doc <doc_id>
  python 13_merge_continued_tables.py merge  --kb <kb_id> --doc <doc_id> [--apply] [--aggressive]
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

RE_TABLE = re.compile(r"(?is)<table.*?</table>")
RE_CAPTION_ID = re.compile(r"表\s*([A-Za-z]?\.?\s*\d+(?:\.\d+)*(?:-\d+)?)")
RE_FIRST_ROW = re.compile(r"(?is)<tr>.*?</tr>")
RE_TAGS = re.compile(r"<[^>]+>")


def _req(method: str, path: str, **kw):
    r = requests.request(method, f"{BASE}{path}", headers=HEADERS, timeout=kw.pop("timeout", 120), **kw)
    r.raise_for_status()
    return r


def list_chunks(kb: str, doc: str) -> list[dict]:
    out, page = [], 1
    while True:
        d = _req("GET", f"/api/v1/datasets/{kb}/documents/{doc}/chunks", params={"page": page, "page_size": 100}).json().get("data") or {}
        batch = d.get("chunks") or []
        if not batch:
            break
        out += batch
        if len(out) >= (d.get("total") or 0) or page > 50:
            break
        page += 1
    return out


def first_page(chunk: dict) -> int:
    m = re.search(r"\[\[?(\d+),", str(chunk.get("positions", "")))
    return int(m.group(1)) if m else 10**9


def split_content(content: str) -> tuple[str, str]:
    """Return (table_html, caption_text)."""
    m = RE_TABLE.search(content)
    if not m:
        return content.strip(), ""
    table = m.group(0)
    caption = content[m.end():].strip()
    return table, caption


def header_key(table_html: str) -> str:
    m = RE_FIRST_ROW.search(table_html)
    if not m:
        return ""
    return re.sub(r"\s+", "", RE_TAGS.sub("", m.group(0)))


def caption_id(caption: str) -> str:
    m = RE_CAPTION_ID.search(caption.replace(" ", ""))
    return m.group(1) if m else ""


def is_continued(caption: str) -> bool:
    return caption.replace(" ", "").startswith("续表")


def build_groups(tables: list[dict], aggressive: bool) -> list[list[dict]]:
    groups: list[list[dict]] = []
    current: list[dict] = []
    cur_id, cur_header = "", ""
    for c in tables:
        table_html, caption = split_content(c.get("content", ""))
        cid = caption_id(caption)
        cont = is_continued(caption)
        starts_new = not current
        if not starts_new:
            same_table = bool(cid) and cid == cur_id
            header_match = header_key(table_html) == cur_header
            if cont and same_table:
                pass  # explicit continuation
            elif aggressive and same_table and header_match and not cont:
                pass  # header-identical extension without marker
            else:
                starts_new = True
        if starts_new:
            if current:
                groups.append(current)
            current = [c]
            cur_id, cur_header = cid, header_key(table_html)
        else:
            current.append(c)
    if current:
        groups.append(current)
    return groups


def merge_group(group: list[dict]) -> tuple[str, list]:
    base_table, base_caption = split_content(group[0].get("content", ""))
    base_header = header_key(base_table)
    body_rows: list[str] = []
    positions: list = []
    for c in group:
        pos = c.get("positions")
        if isinstance(pos, list):
            positions.extend(pos)
    for c in group[1:]:
        table_html, _ = split_content(c.get("content", ""))
        rows = RE_FIRST_ROW.findall(table_html)
        if rows and header_key(table_html) == base_header:
            rows = rows[1:]  # drop repeated header
        body_rows.extend(rows)
    merged = base_table.replace("</table>", "".join(body_rows) + "</table>")
    if base_caption:
        merged = merged + "\n" + base_caption
    return merged, positions


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["detect", "merge"])
    ap.add_argument("--kb", required=True)
    ap.add_argument("--doc", required=True)
    ap.add_argument("--apply", action="store_true", help="apply PATCH/DELETE (default: dry run)")
    ap.add_argument("--aggressive", action="store_true", help="merge header-identical tables without 续表 marker")
    args = ap.parse_args()

    chunks = list_chunks(args.kb, args.doc)
    indexed = [(i, c) for i, c in enumerate(chunks) if c.get("doc_type_kwd") == "table"]
    tables = [c for _, c in sorted(indexed, key=lambda t: (first_page(t[1]), t[0]))]
    groups = build_groups(tables, args.aggressive)

    merges = [g for g in groups if len(g) > 1]
    print(f"table chunks={len(tables)} groups={len(groups)} mergeable_groups={len(merges)}")
    for g in merges:
        pages = [first_page(c) for c in g]
        _, cap = split_content(g[0].get("content", ""))
        print(f"  pages {pages} | caption={cap[:40]!r} | ids={[c.get('id') for c in g]}")
        if not args.aggressive:
            for c in g[1:]:
                _, cc = split_content(c.get("content", ""))
                if not is_continued(cc):
                    print(f"    ! warning: {c.get('id')} caption not marked 续表: {cc[:30]!r}")

    if args.mode == "detect":
        return 0
    if not args.apply:
        print("\n(dry run) re-run with --apply to merge. Nothing changed.")
        return 0

    done = 0
    for g in merges:
        base = g[0]
        content, positions = merge_group(g)
        body = {"content": content}
        if positions:
            body["positions"] = positions
        r = _req("PATCH", f"/api/v1/datasets/{args.kb}/documents/{args.doc}/chunks/{base['id']}", json=body).json()
        if r.get("code") != 0:
            print(f"  ! PATCH failed for {base['id']}: {r.get('message')}")
            continue
        victim_ids = [c["id"] for c in g[1:]]
        d = _req("DELETE", f"/api/v1/datasets/{args.kb}/documents/{args.doc}/chunks", json={"chunk_ids": victim_ids}).json()
        if d.get("code") != 0:
            print(f"  ! DELETE failed for {victim_ids}: {d.get('message')}")
            continue
        print(f"  merged {[c['id'] for c in g]} -> {base['id']}")
        done += 1
    print(f"\ndone. merged {done} group(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
