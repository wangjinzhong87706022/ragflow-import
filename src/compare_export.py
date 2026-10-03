"""
compare_export.py — RAGFlow 导出目录与语料源的全量对比

把“导入 RAGFlow 后又导出”的目录（每顶层子目录=一个库、平铺原始文件）
与语料源根目录逐文件比对（文件名 + 大小 + SHA-256），产出
out/qc/export_compare.md：

- 导出侧：identical / 同名但大小不同 / 同名同大小但内容不同 / 语料源中不存在
- 语料源侧：已入库(内容一致) / 入库后有变化 / 从未导出
- 两侧都按扩展名分组统计覆盖率

CLI:
    python compare_export.py --export F:\\ragflow\\ragflow --src E:\\bak\\资料收集
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import DOC_EXTS, OUT_DIR  # noqa: F401

CHUNK = 1 << 20


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def collect(root: Path) -> dict[str, list[Path]]:
    by_name: dict[str, list[Path]] = defaultdict(list)
    for p in sorted(root.rglob("*")):
        if p.is_file():
            by_name[p.name].append(p)
    return by_name


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="RAGFlow 导出目录 vs 语料源 全量对比")
    ap.add_argument("--export", type=Path, required=True)
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=OUT_DIR / "qc" / "export_compare.md")
    args = ap.parse_args(argv)

    exp = collect(args.export)
    src = collect(args.src)

    identical: list[tuple[str, str]] = []      # (export rel, src rel)
    size_mismatch: list[tuple[str, str, int, int]] = []
    hash_mismatch: list[tuple[str, str, int]] = []
    export_only: list[str] = []
    # 语料源覆盖
    covered: dict[str, int] = defaultdict(int)   # name -> identical count
    name_matched: dict[str, int] = defaultdict(int)
    unchanged_cnt = changed_cnt = 0
    cache: dict[Path, str] = {}

    def digest(p: Path) -> str:
        if p not in cache:
            cache[p] = sha256(p)
        return cache[p]

    # name -> 尚未配对的语料源路径
    unmatched_src: dict[str, list[Path]] = {n: list(ps) for n, ps in src.items()}

    for name, epaths in exp.items():
        cands = src.get(name, [])
        if not cands:
            export_only.append(rel(epaths[0], args.export))
            for extra in epaths[1:]:
                export_only.append(rel(extra, args.export))
            continue
        for ep in epaths:
            pool = unmatched_src.get(name, [])
            hit = None
            # 1) 同名同大小同哈希
            for i, sp in enumerate(pool):
                if sp.stat().st_size == ep.stat().st_size and digest(sp) == digest(ep):
                    hit = i
                    break
            if hit is not None:
                sp = pool.pop(hit)
                identical.append((rel(ep, args.export), rel(sp, args.src)))
                covered[name] += 1
                unchanged_cnt += 1
                continue
            # 2) 同名同大小但哈希不同
            for i, sp in enumerate(pool):
                if sp.stat().st_size == ep.stat().st_size:
                    sp = pool.pop(i)
                    hash_mismatch.append((rel(ep, args.export), rel(sp, args.src),
                                          ep.stat().st_size))
                    name_matched[name] += 1
                    changed_cnt += 1
                    hit = i
                    break
            if hit is not None:
                continue
            # 3) 同名但大小不同
            if pool:
                sp = pool.pop(0)
                size_mismatch.append((rel(ep, args.export), rel(sp, args.src),
                                      ep.stat().st_size, sp.stat().st_size))
                name_matched[name] += 1
                changed_cnt += 1
            else:
                export_only.append(rel(ep, args.export))

    src_only = [(n, rel(p, args.src))
                for n, ps in unmatched_src.items() for p in ps]
    src_only.sort(key=lambda x: x[1])

    # ---- 分扩展名统计 ----
    def ext(name: str) -> str:
        return Path(name).suffix.lower() or "(none)"

    def ext_stat(pairs_from: list, key=lambda x: x) -> dict[str, int]:
        d: dict[str, int] = {}
        for x in pairs_from:
            d[ext(key(x))] = d.get(ext(key(x)), 0) + 1
        return dict(sorted(d.items()))

    src_ext_total: dict[str, int] = {}
    for n, ps in src.items():
        src_ext_total[ext(n)] = src_ext_total.get(ext(n), 0) + len(ps)
    src_ext_covered: dict[str, int] = {}
    for n, c in covered.items():
        src_ext_covered[ext(n)] = src_ext_covered.get(ext(n), 0) + c
    src_ext_changed: dict[str, int] = {}
    for n, c in name_matched.items():
        src_ext_changed[ext(n)] = src_ext_changed.get(ext(n), 0) + c

    n_export = sum(len(v) for v in exp.values())
    n_src = sum(len(v) for v in src.values())

    lines = ["# RAGFlow 导出 vs 语料源 对比报告", "",
             f"- 导出目录：`{args.export}`（{n_export} 文件 / {len(exp)} 个重名组）",
             f"- 语料源：`{args.src}`（{n_src} 文件 / {len(src)} 个重名组）", "",
             "## 总览", "",
             "| 指标 | 数量 |", "|---|---|",
             f"| 导出文件总数 | {n_export} |",
             f"| ├ 内容与语料源完全一致 | {len(identical)} |",
             f"| ├ 同名但大小不同 | {len(size_mismatch)} |",
             f"| ├ 同名同大小但内容不同 | {len(hash_mismatch)} |",
             f"| └ 语料源中不存在（导出侧独有） | {len(export_only)} |",
             f"| 语料源文件总数 | {n_src} |",
             f"| ├ 已导出且一致 | {unchanged_cnt} |",
             f"| ├ 有同名导出但有变化 | {changed_cnt} |",
             f"| └ 从未出现在导出中 | {len(src_only)} |", ""]

    lines += ["## 语料源按扩展名覆盖率", "",
              "| 扩展名 | 语料源 | 一致 | 有变化 | 覆盖率 |", "|---|---|---|---|---|"]
    for e in sorted(src_ext_total):
        t = src_ext_total[e]
        c = src_ext_covered.get(e, 0)
        ch = src_ext_changed.get(e, 0)
        pct = 100.0 * (c + ch) / t if t else 0
        lines.append(f"| {e} | {t} | {c} | {ch} | {pct:.0f}% |")
    lines.append("")

    def dump(title: str, items: list[str], limit: int = 400) -> None:
        lines.append(f"## {title}（{len(items)}）")
        lines.append("")
        for it in items[:limit]:
            lines.append(f"- `{it}`")
        if len(items) > limit:
            lines.append(f"- …（其余 {len(items) - limit} 条省略）")
        lines.append("")

    dump("导出侧独有（语料源中无此文件）", sorted(export_only))
    dump("同名但大小不同（导出侧 vs 语料源）",
         [f"{e}  ←→  {s}  ({es}B vs {ss}B)"
          for e, s, es, ss in size_mismatch])
    dump("同名同大小但内容不同（哈希不一致）",
         [f"{e}  ←→  {s}" for e, s, _ in hash_mismatch])

    # 语料源未导出的：按一级目录归组
    lines.append(f"## 语料源从未导出（{len(src_only)}）")
    lines.append("")
    by_top: dict[str, int] = {}
    for n, r in src_only:
        top = r.split("/")[0]
        by_top[top] = by_top.get(top, 0) + 1
    lines.append("| 一级目录 | 未导出数 |")
    lines.append("|---|---|")
    for k, v in sorted(by_top.items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} |")
    lines.append("")
    limit = 600
    for n, r in src_only[:limit]:
        lines.append(f"- `{r}`")
    if len(src_only) > limit:
        lines.append(f"- …（其余 {len(src_only) - limit} 条省略）")
    lines.append("")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[INFO] 报告: {args.out}")
    print(f"[INFO] 导出 {n_export}: 一致 {len(identical)} / 大小异 {len(size_mismatch)}"
          f" / 内容异 {len(hash_mismatch)} / 源外 {len(export_only)}")
    print(f"[INFO] 语料源 {n_src}: 覆盖 {unchanged_cnt} / 变化 {changed_cnt}"
          f" / 未导出 {len(src_only)}")


if __name__ == "__main__":
    main()
