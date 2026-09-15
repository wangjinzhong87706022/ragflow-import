# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
"""Batch-write ``important_keywords`` onto image/table chunks to boost exact
figure-number / table-number recall in the Q&A retrieval layer.

背景（2026-09-13 评测结论）：图片/竖表类 MISS 的根因是图号/表号只以普通 token 存在于
chunk 正文，混合相似度（keyword 权重 0.7）下被长文本块压制，完整问题检索时图块排名
跌出 top_n。``important_keywords`` 是 RAGFlow 检索的关键词加权字段，写入后精确图号/
表号查询可直接命中。

图块写: ["图3.5.2", "高石粉含量人工细骨料饱和"]   （图号 + 图题）
表块写: ["表D.0.1-1", "项目特性表"]               （表号 + 表名）
数据来源: docs/code/mineru/testcases/mineru_content_inventory.json（解析产物盘点）

Usage:
  python 14_patch_important_keywords.py detect --kb <kb> --doc <doc>          # 预览将写入的内容
  python 14_patch_important_keywords.py apply  --kb <kb> --doc <doc> [--only-figures|--only-tables]
  python 14_patch_important_keywords.py verify --kb <kb> --doc <doc>          # 复查写入结果
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

import requests

BASE = os.getenv("RAGFLOW_API_BASE", "https://labragf.openagp.top:9080")
API_KEY = os.getenv("RAGFLOW_API_KEY", "")
HEADERS = {"Authorization": "Bearer " + API_KEY}
HERE = os.path.dirname(os.path.abspath(__file__))
INVENTORY = os.path.join(HERE, "..", "testcases", "mineru_content_inventory.json")

RE_FIG = re.compile(r"(图\s*[0-9A-Z]+\.(?:\d+(?:\.\d+)?)(?:-\d+)?)")
RE_TAB = re.compile(r"(表\s*[0-9A-Z]+\.(?:\d+(?:\.\d+)?)(?:-\d+)?)")
RE_TABLE_BLOCK = re.compile(r"<table", re.I)


def _req(method: str, path: str, **kw):
    r = requests.request(method, BASE + path, headers=HEADERS, timeout=kw.pop("timeout", 120), **kw)
    r.raise_for_status()
    return r


def iter_chunks(kb: str, doc: str):
    page = 1
    while True:
        j = _req("GET", f"/api/v1/datasets/{kb}/documents/{doc}/chunks",
                 params={"page": page, "page_size": 100}).json()
        d = j.get("data") or {}
        cs = d.get("chunks") or []
        if not cs:
            return
        yield from cs
        total = d.get("total") or 0
        if page * 100 >= total:
            return
        page += 1


def extract_fig(caption: str) -> list[str]:
    """从图片块 caption（content 前缀）提取图号, 如 图3.5.2 / 图5.9.2-1."""
    return [m.replace(" ", "") for m in RE_FIG.findall(caption or "")]


def extract_tab(caption: str) -> list[str]:
    return [m.replace(" ", "") for m in RE_TAB.findall(caption or "")]


def figure_title(content: str) -> str:
    """图题 = 图号后到第一个模板字段前的短文本（只取干净部分，防正文污染）."""
    m = RE_FIG.search(content or "")
    if not m:
        return ""
    tail = content[m.end(): m.end() + 60]
    # 截断到模板字段/标点/换行，且不允许出现句子级字符（如 "若/所/在" 开头的正文）
    tail = re.split(r"[-;；:：(（\n]|Visual Type", tail)[0].strip(" ；;，,")
    if any(w in tail for w in ("所示", "其中", "若", "应", "按")):
        return ""
    return tail[:30] if 2 <= len(tail) <= 30 else ""


def plan_for_chunk(c: dict, table_titles: dict[str, str] | None = None) -> list[str] | None:
    """返回应为该 chunk 写入的 important_keywords；无需写返回 None.

    表块的表号不在 content 里（447 合并后表块以 <table> 直接开头，表号在 chunk 的
    positions/前序块中），因此按页码从盘点 JSON 的 tables 清单匹配表号+表名。
    """
    content = c.get("content") or ""
    existing = c.get("important_keywords") or []
    if existing:
        return None  # 已写过, 跳过（幂等）
    kws: list[str] = []
    if RE_TABLE_BLOCK.search(content):
        # 表块: 优先 caption 区取表号; 否则按首页码查盘点
        head = RE_TABLE_BLOCK.split(content)[0]
        ids = extract_tab(head)[:1]
        if not ids and table_titles:
            pages = [int(p[0]) for p in (c.get("positions") or []) if isinstance(p, (list, tuple)) and p]
            for p in pages:
                if p in table_titles:
                    kws = list(table_titles[p])
                    break
            if kws:
                return _clean(kws)
        if ids:
            kws.extend(ids)
            m = re.search(re.escape(ids[0]) + r"\s*([^<\-;；:：]{2,20})", head)
            if m:
                kws.append(m.group(1).strip())
    else:
        # 图片块: 图号必须在 content 开头（前 12 字符内）才是图块自身；
        # 图号出现在正文中段的是"引用正文的文本块"，跳过（避免把正文块标成图块）
        head = content[:12]
        ids = extract_fig(head)[:1]
        if ids:
            kws.extend(ids)
            t = figure_title(content)
            if t:
                kws.append(t)
    return _clean(kws) if kws else None


def _clean(kws: list[str]) -> list[str] | None:
    kws = [k for k in kws if k and len(k) >= 2]
    return kws or None


def load_table_titles() -> dict[int, list[str]]:
    """从盘点 JSON 构建 {页码: [表号, 表名]}（447 合并版清单）."""
    try:
        inv = json.load(open(INVENTORY, encoding="utf-8"))
    except OSError:
        return {}
    out: dict[int, list[str]] = {}
    for doc_name, d in inv.items():
        if "447" not in doc_name:
            continue
        for t in d.get("tables", []):
            cap = t.get("caption") or ""
            # caption 形如 "表 D. 0.1-1 项目特性表"（表号内带空格），宽松匹配
            m = re.search(r"表\s*((?:[0-9A-Z]+\s*\.\s*\d+(?:\s*\.\s*\d+)*(?:-\d+)?))\s*(.*)", cap)
            if m and t.get("page"):
                tid = "表" + re.sub(r"\s+", "", m.group(1))
                title = (m.group(2) or "").strip()[:16]
                kws = [tid] + ([title] if title else [])
                # 同页多表: 累积全部表号（表块按起始页匹配时一并写入）
                entry = out.setdefault(int(t["page"]), [])
                for kw_ in kws:
                    if kw_ not in entry:
                        entry.append(kw_)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["detect", "apply", "verify"])
    ap.add_argument("--kb", required=True)
    ap.add_argument("--doc", required=True)
    ap.add_argument("--table-titles", default="", help="JSON file mapping page -> [表号, 表名] for table chunks")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--only-figures", action="store_true")
    g.add_argument("--only-tables", action="store_true")
    args = ap.parse_args()

    table_titles = load_table_titles() if not args.table_titles else json.load(open(args.table_titles, encoding="utf-8"))

    n_patch = n_skip = 0
    for c in iter_chunks(args.kb, args.doc):
        kws = plan_for_chunk(c, table_titles)
        if kws is None:
            n_skip += 1
            continue
        is_table = RE_TABLE_BLOCK.search(c.get("content") or "") is not None
        if args.only_figures and is_table:
            continue
        if args.only_tables and not is_table:
            continue
        n_patch += 1
        if args.mode == "detect":
            print(f"[{c['id'][:8]}] {'表' if is_table else '图'} -> {kws} | {(c.get('content') or '')[:50]}")
        elif args.mode == "apply":
            r = _req("PUT", f"/api/v1/datasets/{args.kb}/documents/{args.doc}/chunks/{c['id']}",
                     json={"important_keywords": kws}).json()
            ok = r.get("code") == 0
            n_patch += 0 if ok else -1  # 失败回退计数
            print(f"[{c['id'][:8]}] {'表' if is_table else '图'} {kws} -> {'OK' if ok else 'FAIL ' + str(r.get('message'))[:60]}")

    if args.mode == "verify":
        n_with = sum(1 for c in iter_chunks(args.kb, args.doc) if c.get("important_keywords"))
        print(f"chunks with important_keywords: {n_with}")
        return 0

    print(f"\n{args.mode}: {n_patch} chunk(s) {'待写' if args.mode == 'detect' else '已写'}, {n_skip} 跳过")
    if args.mode == "detect":
        print("(dry run) 加 --mode apply 生效: python 14_patch_important_keywords.py apply --kb ... --doc ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
