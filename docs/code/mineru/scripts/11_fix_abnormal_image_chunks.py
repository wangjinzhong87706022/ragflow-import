# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
# 文中的 KB / document / chunk ID 来自本次评测实例，按需替换。
"""Repair abnormal image chunks produced by the MinerU + VISION pipeline.

Detects two kinds of anomalies in image chunks and fixes them via the
existing RAGFlow HTTP API only (no source changes):

  * LEAK  - the VLM echoed its prompt header into the description
            (e.g. "MODE 1: STRUCTURED VISUAL DATA OUTPUT"). The leaked
            prefix is stripped and the chunk content is patched.
  * EMPTY - the VLM returned no usable description. The original figure is
            downloaded from RAGFlow, re-described through a helper
            "picture" knowledge base (which runs the tenant VISION model),
            and the result is patched back.

Usage:
  python tools/fix_image_chunks.py detect --kb <kb_id> --doc <doc_id>
  python tools/fix_image_chunks.py fix    --kb <kb_id> --doc <doc_id> [--apply]

Without --apply the script only reports what it would change.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time

import requests

BASE = os.getenv("RAGFLOW_API_BASE", "https://labragf.openagp.top:9080")
API_KEY = os.getenv("RAGFLOW_API_KEY", "")
HELPER_KB_NAME = "vlm-redesc"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

FIELD_LABELS = (
    "Visual Type:",
    "Title:",
    "Axes / Legends / Labels:",
    "Data Points:",
    "Captions / Annotations:",
)
LEAK_MARKERS = (
    "MODE 1",
    "MODE 2",
    "STRUCTURED VISUAL DATA OUTPUT",
    "GENERAL FIGURE CONTENT",
)
RE_LEAK_LINE = re.compile(
    r"(?im)^\s*(?:#+\s*)?(?:MODE\s*[12]\s*:\s*)?(?:STRUCTURED VISUAL DATA OUTPUT|GENERAL FIGURE CONTENT)[^\n]*\n?"
)
RE_CJK = re.compile(r"[\u4e00-\u9fff]")
RE_LATIN = re.compile(r"[A-Za-z]")


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


def classify(content: str) -> str | None:
    """Return 'LEAK', 'EMPTY' or None for an image chunk content."""
    upper = content.upper()
    if any(m in upper for m in LEAK_MARKERS):
        return "LEAK"
    remainder = content
    for label in FIELD_LABELS:
        remainder = remainder.replace(label, "")
    cjk = len(RE_CJK.findall(remainder))
    latin = len(RE_LATIN.findall(remainder))
    if cjk < 3 and latin <= 5:
        return "EMPTY"
    return None


def strip_leak(content: str) -> str:
    cleaned = RE_LEAK_LINE.sub("", content)
    cleaned = cleaned.replace("STRUCTURED VISUAL DATA OUTPUT", "").replace("GENERAL FIGURE CONTENT", "")
    return "\n".join(line for line in cleaned.splitlines() if line.strip()).strip()


def ensure_helper_kb() -> str:
    data = (_req("GET", "/api/v1/datasets", params={"page": 1, "page_size": 100}).json().get("data")) or []
    kb = next((x for x in data if x.get("name") == HELPER_KB_NAME), None)
    if kb is None:
        r = _req("POST", "/api/v1/datasets", json={"name": HELPER_KB_NAME, "chunk_method": "picture"}).json()
        if r.get("code") != 0:
            raise RuntimeError(f"failed to create helper KB: {r}")
        kb = r["data"]
    kb_id = kb["id"]
    _req("PUT", f"/api/v1/datasets/{kb_id}", json={"language": "Chinese"})
    return kb_id


def redact_images(helper_kb: str, images: list[tuple[str, bytes, str]]) -> dict[str, str]:
    """Upload figures to the helper KB and return {chunk_id: description}."""
    doc_map: dict[str, str] = {}
    for chunk_id, blob, ext in images:
        r = _req("POST", f"/api/v1/datasets/{helper_kb}/documents",
                 files=[("file", (f"{chunk_id}.{ext}", blob, f"image/{ext}"))]).json()
        if r.get("code") != 0:
            print(f"  ! upload failed for {chunk_id}: {r.get('message')}")
            continue
        doc_map[r["data"][0]["id"]] = chunk_id
    if not doc_map:
        return {}
    _req("POST", f"/api/v1/datasets/{helper_kb}/chunks", json={"document_ids": list(doc_map)})

    for _ in range(60):
        time.sleep(5)
        docs = (_req("GET", f"/api/v1/datasets/{helper_kb}/documents", params={"page_size": 100}).json().get("data") or {}).get("docs", [])
        running = [d for d in docs if d["id"] in doc_map and d.get("run") not in ("DONE", "FAIL", "CANCEL")]
        if not running:
            break

    descriptions: dict[str, str] = {}
    for doc_id, chunk_id in doc_map.items():
        d = _req("GET", f"/api/v1/datasets/{helper_kb}/documents/{doc_id}/chunks", params={"page": 1, "page_size": 5}).json().get("data") or {}
        parts = [(c.get("content") or "").strip() for c in (d.get("chunks") or [])]
        desc = "\n".join(p for p in parts if p)
        if desc:
            descriptions[chunk_id] = desc
    return descriptions


def patch_chunk(kb: str, doc: str, chunk_id: str, content: str) -> bool:
    r = _req("PATCH", f"/api/v1/datasets/{kb}/documents/{doc}/chunks/{chunk_id}", json={"content": content}).json()
    return r.get("code") == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["detect", "fix"])
    ap.add_argument("--kb", required=True)
    ap.add_argument("--doc", required=True)
    ap.add_argument("--apply", action="store_true", help="actually patch chunks (default: dry run)")
    args = ap.parse_args()

    chunks = [c for c in list_chunks(args.kb, args.doc) if c.get("doc_type_kwd") == "image"]
    leaks, empties = [], []
    for c in chunks:
        kind = classify(c.get("content", ""))
        if kind == "LEAK":
            leaks.append(c)
        elif kind == "EMPTY":
            empties.append(c)

    print(f"image chunks={len(chunks)} | LEAK={len(leaks)} EMPTY={len(empties)}")
    for c in leaks:
        print(f"  [LEAK]  {c['id']}  {c.get('content','')[:80]!r}")
    for c in empties:
        print(f"  [EMPTY] {c['id']}  {c.get('content','')[:80]!r}")

    if args.mode == "detect":
        return 0

    if not args.apply:
        print("\n(dry run) re-run with --apply to patch. Nothing changed.")
        return 0

    # 1. strip leaked prompt headers
    fixed = 0
    for c in leaks:
        cleaned = strip_leak(c.get("content", ""))
        if classify(cleaned) == "EMPTY":
            empties.append({**c, "content": cleaned})
            continue
        if patch_chunk(args.kb, args.doc, c["id"], cleaned):
            fixed += 1
            print(f"  patched LEAK {c['id']}")

    # 2. re-describe empty chunks via the helper picture KB
    if empties:
        helper = ensure_helper_kb()
        images = []
        for c in empties:
            image_id = c.get("image_id")
            if not image_id:
                continue
            r = _req("GET", f"/api/v1/documents/images/{image_id}")
            ext = "jpg" if "jpeg" in (r.headers.get("Content-Type") or "") else "png"
            images.append((c["id"], r.content, ext))
        print(f"  re-describing {len(images)} images via helper KB {helper} ...")
        descriptions = redact_images(helper, images)
        for c in empties:
            desc = descriptions.get(c["id"], "").strip()
            if not desc:
                print(f"  ! no description returned for {c['id']}")
                continue
            caption = c.get("content", "").strip()
            content = f"{caption}\n{desc}" if caption else desc
            if patch_chunk(args.kb, args.doc, c["id"], content):
                fixed += 1
                print(f"  patched EMPTY {c['id']}")

    print(f"\ndone. patched {fixed} chunk(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
