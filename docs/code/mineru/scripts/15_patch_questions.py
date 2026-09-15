# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
"""Batch-write ``questions`` onto image/table chunks to fix vector recall for
exact figure-number / table-number queries (P0-A of the 2026-09-14 review).

背景（eval-failed-cases-image-vertical-2026-09-13 评审）：
  important_keywords PATCH 之后仍有 10 条用例连 /api/v1/retrieval 直检 top-10 都不含目标
  ——根因在 ES 候选窗口入选几乎纯向量化（FusionExpr weights 0.001,1），而图/表块的嵌入
  输入是几百 token 的 VLM 描述正文，图号"图5.5.2"被稀释，短查询余弦排不进 64 条窗口。
  PATCH questions 会整体替换重嵌入的输入：update_chunk 的重嵌入 =
  0.1×文档名向量 + 0.9×(question_kwd 拼接 if questions else content)。
  把嵌入源换成「图号+图题+核心部件词」一句话后，向量直接对齐图号查询，
  同时 question_tks 还拿到词袋 ×6 加成与全文 ^20 权重（比 important_kwd 的 ×5/^30 更强）。

写法（每个块一条短问题，覆盖"按图号查"与"按图名/部件查"两类问法）：
  图块: "图5.5.2 附着式变形测量架示意图（方框式）的可见要素：试块、位移计、标距定位杆、夹具"
  表块: "表D.0.15 水土保持措施量汇总表的表头列名"

数据来源: docs/code/mineru/testcases/mineru_content_inventory.json
  （352 的 images[].fig_no/desc 含图号+图题+Labels 部件词；447 的 tables[].caption 含表号+表名）
幂等: 已有 questions 的块跳过。回滚: 对同块 PATCH questions=[] 后再 PATCH content 原文
  （见 update_chunk：content 变更必触发按 content 重嵌入）。

Usage:
  python 15_patch_questions.py detect --kb <kb> --doc <doc>            # 预览将写入的问题串
  python 15_patch_questions.py apply   --kb <kb> --doc <doc> [--only-figures|--only-tables]
  python 15_patch_questions.py verify  --kb <kb> --doc <doc>           # 统计已有 questions 的块数
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
RE_LABELS = re.compile(r"Labels:\s*([^\n-]+)")


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


def fig_title_and_labels(desc: str) -> tuple[str, list[str]]:
    """从盘点 desc 提取（图题, Labels 部件词列表）。"""
    # desc 形如 "图5.5.2 附着式变形测量架示意图（方框式）； - Visual Type: ... Labels: 试块、位移计、..."
    m = RE_FIG.search(desc or "")
    title = ""
    if m:
        tail = desc[m.end(): m.end() + 60]
        tail = re.split(r"[;；\n]|Visual Type", tail)[0].strip(" ；;，,")
        if 2 <= len(tail) <= 40 and not any(w in tail for w in ("所示", "其中", "若", "应", "按")):
            title = tail[:30]
    labels = []
    lm = RE_LABELS.search(desc or "")
    if lm:
        raw = lm.group(1).strip()
        # Labels 字段有两种形态：顿号分隔的部件词列表（可入问题串），
        # 或长句描述（"图中包含…"——不可拆词，弃用只留图号+图题）
        if raw and raw not in ("无", "-") and len(raw) <= 80 and "。" not in raw and "图中" not in raw and "包含" not in raw:
            labels = [x.strip() for x in re.split(r"[、,，;；/]", raw) if 1 < len(x.strip()) <= 12][:6]
    return title, labels


def load_inventory_maps() -> tuple[dict[str, dict], list[dict]]:
    """返回 (图号 -> 盘点条目, 447 表盘点条目列表)。表匹配用列头比对（见 match_table）。"""
    inv = json.load(open(INVENTORY, encoding="utf-8"))
    figmap: dict[str, dict] = {}
    tables: list[dict] = []
    for doc_name, d in inv.items():
        for f in d.get("images", []):
            for n in (f.get("fig_no") or []):
                figmap.setdefault(n, f)
        if "447" in doc_name:
            for t in d.get("tables", []):
                if t.get("caption") and t.get("header"):
                    tables.append(t)
    return figmap, tables


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def match_table(content: str, tables: list[dict]) -> dict | None:
    """表块身份匹配。三个信号（附录 D 一页常有两张表、且多表同构列头，页码法与纯列头法都会错）：
    1) 块内前 4 个列头单元格在盘点表 header 中的子串命中（主信号）；
    2) 列头完整性：块列头长度 / 盘点 header 长度 ≈1 加分（区分"名称单位数量"与其超集"…备注"）；
    3) 块内正文行与盘点 first_data_rows 命中（破同构列头平局）。
    实测 6/6 正确（含 D.0.1-1 vs D.0.1-2、p87/p88/p94 同页双表）。"""
    tds = [_norm(x) for x in re.findall(r"<td[^>]*>([^<]*)</td>", content[:1500])]
    cols = [x for x in tds if len(x) >= 2]
    if not cols:
        return None
    head, body = cols[:4], cols[4:12]
    block_head_len = sum(len(c) for c in head)
    best, best_score = None, 0
    for t in tables:
        th = _norm(t.get("header"))
        if not th:
            continue
        head_score = sum(1 for c in head if c in th)
        hdr_ratio = block_head_len / max(1, len(th))
        body_score = sum(1 for c in body if len(c) >= 4 and c in _norm("".join(t.get("first_data_rows") or [])))
        total = head_score * 10 + body_score * 3 + (2 if 0.75 <= hdr_ratio <= 1.05 else 0)
        if total > best_score:
            best, best_score = t, total
    return best if best and best_score >= 12 else None


def plan_for_chunk(c: dict, figmap: dict, tables: list[dict]) -> list[str] | None:
    """返回应为该 chunk 写入的 questions；无需写返回 None（幂等：已有 questions 跳过）。"""
    if c.get("questions"):
        return None
    content = c.get("content") or ""
    if RE_TABLE_BLOCK.search(content):
        # 表块: 表号不在 content 里（合并后 <table> 直接开头），按列头+首行与盘点表比对定身份
        t = match_table(content, tables)
        if not t:
            return None
        m = re.search(r"表\s*((?:[0-9A-Z]+\s*\.\s*\d+(?:\s*\.\s*\d+)*(?:-\d+)?))\s*(.*)", t["caption"])
        if m:
            tid = "表" + _norm(m.group(1))
            name = m.group(2).strip()
            if name:
                return [f"{tid} {name}的表头列名"]
        return None
    # 图片块: 图号须在 content 开头出现（前 24 字符内），且图号前的短前缀
    # （子标题/部件清单，如 "（c）过分干燥；"）不得含引用动词——防"如图X所示"式正文块误标
    m = RE_FIG.search(content[:24])
    if not m:
        return None
    prefix = content[: m.start()]
    if len(prefix) > 12 or re.search(r"如|见|按|参照|参照|示于", prefix):
        return None
    fid = m.group(1).replace(" ", "")
    f = figmap.get(fid)
    if not f:
        return None
    title, labels = fig_title_and_labels(f.get("desc") or "")
    q = fid + ((" " + title) if title else "")
    if labels:
        q += "的可见要素：" + "、".join(labels)
    return [q]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["detect", "apply", "verify"])
    ap.add_argument("--kb", required=True)
    ap.add_argument("--doc", required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--only-figures", action="store_true")
    g.add_argument("--only-tables", action="store_true")
    args = ap.parse_args()

    figmap, tables = load_inventory_maps()

    if args.mode == "verify":
        n_q = sum(1 for c in iter_chunks(args.kb, args.doc) if c.get("questions"))
        n_k = sum(1 for c in iter_chunks(args.kb, args.doc) if c.get("important_keywords"))
        print(f"chunks with questions: {n_q}, with important_keywords: {n_k}")
        return 0

    n_patch = n_skip = 0
    for c in iter_chunks(args.kb, args.doc):
        qs = plan_for_chunk(c, figmap, tables)
        if qs is None:
            n_skip += 1
            continue
        is_table = RE_TABLE_BLOCK.search(c.get("content") or "") is not None
        if args.only_figures and is_table:
            continue
        if args.only_tables and not is_table:
            continue
        n_patch += 1
        kind = "表" if is_table else "图"
        if args.mode == "detect":
            print(f"[{c['id'][:8]}] {kind} -> {qs[0][:70]} | {(c.get('content') or '')[:40]}")
        elif args.mode == "apply":
            r = _req("PUT", f"/api/v1/datasets/{args.kb}/documents/{args.doc}/chunks/{c['id']}",
                     json={"questions": qs}).json()
            ok = r.get("code") == 0
            n_patch += 0 if ok else -1  # 失败回退计数
            print(f"[{c['id'][:8]}] {kind} {qs[0][:50]} -> {'OK' if ok else 'FAIL ' + str(r.get('message'))[:60]}")

    print(f"\n{args.mode}: {n_patch} chunk(s) {'待写' if args.mode == 'detect' else '已写'}, {n_skip} 跳过")
    if args.mode == "detect":
        print("(dry run) 生效: python 15_patch_questions.py apply --kb ... --doc ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
