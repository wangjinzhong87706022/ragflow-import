#!/usr/bin/env python3
"""逐表质量评审：OCR 误读模式量化 + 异常块扫描 + 题注缺失统计.

评审方法论（本次实测沉淀）：
  1. "缺表"结论必须逐块核对块尾——多表合并块（p82/p87/p94）会掩盖真实覆盖；
     本次 D.0.2/D.0.19/D.0.12 三次"缺失"初判全部是误判。
  2. OCR 误读按模式量化（"<"替代"/"、梯田→修田、库容→岸 等），
     同时排除标准术语假阳性（淤积面/淤地面积 是淤地坝栏的正规栏名）。
  3. 题注缺失单独统计：无"表D.0.x"前缀的表块影响召回，需批量补注。
"""
import json
import re

CHUNKS_DUMP = "output/chunks_dump.json"

# 已知 OCR 误读模式（模式, 说明, 是否计错）
ERROR_PATTERNS = [
    (r"<", '"<"替代"/"或误读', True),
    (r"修田", "修田（应为梯田）", True),
    (r"经渍林", "经渍林（应为经济林）", True),
    (r"微地坝", "微地坝（应为淤地坝）", True),
    (r"农林牧油", "农林牧油（应为农林牧渔）", True),
    (r"总岸|岸<", "岸（应为库容）", True),
    (r"库数", "库数（应为座数）", True),
    (r"弃木", "弃木（应为灌木类）", True),
    # 假阳性排除项（标准术语，不计错）
    (r"淤积面|淤地面|淤地面积", "标准术语（淤地坝栏），不计错", False),
]

# 严重度分级（按块 ID 前缀手工核定，运行时按命中数量自动提示）
SEVERE_NOTE = "命中 >10 处 '<' 或 >=4 类不同误读 → 建议删除（若同表存在干净变体）"


def load_chunks():
    with open(CHUNKS_DUMP, encoding="utf-8") as f:
        return json.load(f)["chunks"]


def page_of(chunk):
    positions = chunk.get("positions") or []
    return min([p[0] for p in positions]) if positions else -1


def scan_ocr_errors(content):
    hits = []
    for pat, name, is_error in ERROR_PATTERNS:
        found = re.findall(pat, content)
        if found and is_error:
            hits.append((name, len(found)))
    return hits


def scan_anomalies(chunk, content):
    """结构异常：竖排残留（单字行占比）、超短块、题注错位线索。"""
    issues = []
    lines = [l for l in content.split("\n") if l.strip()]
    if len(lines) > 5:
        single = sum(1 for l in lines if len(l.strip()) <= 2)
        if single / len(lines) > 0.3:
            issues.append(f"单字行占比 {single}/{len(lines)}（竖排残字特征）")
    if len(content) < 120 and not content.lstrip().startswith("|"):
        issues.append(f"超短文本块（{len(content)} chars）")
    # 题注错位线索：题注后跟的表头与题注语义不符需人工核对（如 p82 案例）
    caps = re.findall(r"表\s*D\.0\.\d+[^\n|]{0,20}", content)
    if len(caps) > 1:
        issues.append(f"多题注块 {caps}（核对题注与相邻表头语义）")
    return issues


def main():
    chunks = load_chunks()
    print(f"Loaded {len(chunks)} chunks\n")

    print("=== OCR 误读量化（附录区域 p70-97 的表格块）===")
    for c in chunks:
        content = c.get("content") or ""
        if not content.lstrip().startswith("|"):
            continue
        page = page_of(c)
        if not 70 <= page <= 97:
            continue
        hits = scan_ocr_errors(content)
        if hits:
            total_hits = sum(n for _, n in hits)
            kinds = len(hits)
            level = "重度" if (total_hits > 10 or kinds >= 4) else ("中度" if total_hits > 3 else "轻度")
            detail = "; ".join(f"{name}×{n}" for name, n in hits)
            print(f"  p{page:>3} [{c['id'][:8]}] {level}: {detail}")
    print(f"  分级规则：{SEVERE_NOTE}")

    print("\n=== 异常块扫描（全文档）===")
    for c in chunks:
        content = c.get("content") or ""
        issues = scan_anomalies(c, content)
        if issues:
            print(f"  p{page_of(c):>3} [{c['id'][:8]}] len={len(content)}: {'; '.join(issues)}")

    print("\n=== 题注缺失统计（表块无 '表D.0.x' 前缀）===")
    missing_caption = []
    for c in chunks:
        content = c.get("content") or ""
        if not content.lstrip().startswith("|"):
            continue
        page = page_of(c)
        if not 70 <= page <= 97:
            continue
        if not re.match(r"^\s*\|?\s*表?\s*D\.\s*0\.\s*\d", content):
            missing_caption.append((page, c["id"][:8]))
    print(f"  无题注表块 {len(missing_caption)} 个（建议批量补 '表D.0.x 表名' 前缀）")
    for page, cid in missing_caption:
        print(f"    p{page} [{cid}]")


if __name__ == "__main__":
    main()
