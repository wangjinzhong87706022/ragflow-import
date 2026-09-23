"""
jhc_prepare.py — 泾惠渠语料预处理（阶段 0.5）

职责（方案 2026-09-22，与 profiles/jinghuiqu.py 配套）：
  1. 遍历原始资料根（默认 JHC_SRC_ROOT，只读不动源文件）；
  2. 文档（pdf/word/excel）→ 复制到暂存语料根 out/jhc_corpus/ 同相对路径；
     根目录散落文档归入 计划处/（jhc3）；
  3. 扫描件图片（jpg/jpeg/png/tif，含大写扩展名）→ 按"父目录 + 去页号后的
     文件名主干"分组，组内按页码数值排序，用 img2pdf 合并为
     ``<组名>（合并）.pdf``（corpus.py 据（合并）标记打 source_format=merged_pdf）。
     单图独立成 1 页 PDF——统一走 RAGFlow naive 解析器的 PDF-OCR 路径，
     避免逐张图片入库造成检索碎片化；
  4. 压缩包（rar/zip）旁侧有同名解压目录则跳过，否则记 needs_review；
     json/wps 等不支持格式进跳过清单；
  5. 产出 out/jinghuiqu/prepare_report.md 审计报告。

CLI（变更类脚本惯例：默认 dry-run，--apply 显式确认）：
    python jhc_prepare.py            # 只出报告
    python jhc_prepare.py --apply    # 生成暂存语料
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path, PurePosixPath

# 自包含常量（不 import config，避免画像加载副作用）——与 profiles/jinghuiqu.py
# 的 JHC_SRC_ROOT / CORPUS_ROOT / OUT_DIR 保持一致，改一处须同步另一处。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = Path(os.getenv("JHC_SRC_ROOT", r"D:\doc\taineng\泾惠渠项目\资料收集"))
DEFAULT_STAGING = Path(os.getenv("JHC_CORPUS_ROOT", str(PROJECT_ROOT / "out" / "jhc_corpus")))
DEFAULT_REPORT = PROJECT_ROOT / "out" / "jinghuiqu" / "prepare_report.md"

DOC_EXTS = {".pdf", ".docx", ".doc", ".xls", ".xlsx"}
IMG_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
ARCHIVE_EXTS = {".rar", ".zip"}
# 合并 PDF 标记（corpus.scan 据此置 source_format=merged_pdf）；全角括号防与
# 原生文件名冲突，且保证同目录多个分组产物互不覆盖
MERGED_TAG = "（合并）"
# 根目录散落文件归入的一级目录（= jhc3 计划处）
ROOT_DOC_TARGET = "计划处"
# 目录遍历时无条件忽略的杂项
JUNK_DIRS = {"__MACOSX", ".claude"}

# 页号剥离模式（按序尝试，首个命中生效）：
#   方案_01 / 记录-2 / 证件_0001      → [_-] + 尾部数字
#   第3页 / 图（4） / 页(12)          → 中文页号与括号编号
#   记录 5                            → 空格 + 数字
#   机电设备运行记录……管理处1          → 裸尾数字（base 至少 1 字符）
_PAGE_PATTERNS = [
    re.compile(r"^(.+?)[_-](\d{1,4})$"),
    re.compile(r"^(.+?)第(\d{1,4})页$"),
    re.compile(r"^(.+?)[（(](\d{1,4})[）)]$"),
    re.compile(r"^(.+?)\s+(\d{1,4})$"),
    re.compile(r"^(.+?)(\d{1,4})$"),
]

# 微信手机拍图命名：微信图片_YYYYMMDDHHMMSS(_流水号)(_图号)．时间戳无语义，
# 若按通用规则剔页码会同小时成百张图碎成单页组/巧合性并组（真实语料：
# 机组/巡查记录表目录数百张照片，后缀有一到多段数字），改按父目录归组、
# 全部数字串拼接作页码保拍摄/流水序
_WECHAT_RE = re.compile(r"^微信图片[_\-]?\d{8,14}(?:[_\-]\d{1,4})*$")


# ---------------------------------------------------------------------------
# 分组（纯函数，供单测）
# ---------------------------------------------------------------------------

def strip_page_suffix(stem: str, parent_name: str = "") -> tuple[str, int | None]:
    """返回 (分组主干, 页码)。纯数字文件名（1.png/10.png）以父目录名为主干、
    文件名数字为页码——批复拆页扫描 ``1.png…10.png`` 才能合成一份 PDF。
    微信时间戳命名同理，页码取纯数字串（字典序=时间序）。"""
    if _WECHAT_RE.match(stem):
        digits = re.sub(r"\D", "", stem) or "0"
        return (parent_name or "扫描件", int(digits))
    if stem.isdigit():
        return (parent_name or "扫描件", int(stem))
    for pat in _PAGE_PATTERNS:
        m = pat.match(stem)
        if m:
            base = re.sub(r"[\s\-_]+$", "", m.group(1))
            if base:
                return base, int(m.group(2))
    return stem, None


def group_images(rels: list[str]) -> dict[str, list[str]]:
    """图片相对路径（POSIX）→ {分组键(父目录/主干): [按页码排序的 rel…]}。

    排序键：有页码的按数值升序（10 在 2 后），无页码的排组尾并按名序——
    保证合并 PDF 页序与阅读顺序一致。
    """
    groups: dict[str, list[tuple[int | None, str]]] = {}
    for rel in sorted(rels):
        p = PurePosixPath(rel)
        base, page = strip_page_suffix(p.stem, p.parent.name)
        key = str(p.parent / base)
        groups.setdefault(key, []).append((page, rel))
    out: dict[str, list[str]] = {}
    for key, items in groups.items():
        items.sort(key=lambda t: (t[0] is None, t[0] or 0, t[1].lower()))
        out[key] = [rel for _, rel in items]
    return out


# ---------------------------------------------------------------------------
# 扫描 → 导入计划
# ---------------------------------------------------------------------------

def build_plan(src_root: Path) -> dict:
    """遍历源根，产出 {"docs", "merges", "skips", "reviews"}。

    docs:    [(源rel, 暂存rel)]（根目录文档的暂存rel加 计划处/ 前缀）
    merges:  [(暂存rel(合并PDF), [源图片rel…])]
    skips:   [(源rel, 原因)]
    reviews: 需要人工定夺的压缩包（旁侧无同名解压目录）
    """
    docs: list[tuple[str, str]] = []
    images: list[str] = []
    skips: list[tuple[str, str]] = []
    reviews: list[str] = []

    for path in sorted(src_root.rglob("*")):
        if not path.is_file():
            continue
        rel = PurePosixPath(path.relative_to(src_root).as_posix())
        if any(part in JUNK_DIRS for part in rel.parts[:-1]):
            continue
        ext = rel.suffix.lower()
        at_root = len(rel.parts) == 1

        if ext in DOC_EXTS:
            out_rel = f"{ROOT_DOC_TARGET}/{rel.name}" if at_root else str(rel)
            docs.append((str(rel), out_rel))
        elif ext in IMG_EXTS:
            images.append(str(rel))
        elif ext in ARCHIVE_EXTS:
            sibling = src_root.joinpath(*rel.parts[:-1]).joinpath(rel.stem)
            if sibling.is_dir():
                skips.append((str(rel), "压缩包内容已解压为同名目录"))
            else:
                reviews.append(str(rel))
                skips.append((str(rel), "压缩包无同名解压目录（needs_review）"))
        else:
            skips.append((str(rel), f"不支持的格式 {ext or '(无扩展名)'}"))

    merges: list[tuple[str, list[str]]] = []
    for key, rels in sorted(group_images(images).items()):
        kp = PurePosixPath(key)
        parent = str(kp.parent)
        if parent == ".":                      # 根目录散图也归 计划处
            parent = ROOT_DOC_TARGET
        base = kp.name
        if base.isdigit():                     # "1-1/1-2 每张记录单"型分组：
            base = f"{PurePosixPath(parent).name}-{base}"   # 补目录语境，避免无语义名
        merges.append((f"{parent}/{base}{MERGED_TAG}.pdf", rels))

    return {"docs": docs, "merges": merges, "skips": skips, "reviews": reviews}


# ---------------------------------------------------------------------------
# 合并执行（I/O 部分；img2pdf/Pillow 延迟导入，单测只测分组纯函数）
# ---------------------------------------------------------------------------

def _to_png_bytes(path: Path):
    """tif（或 img2pdf 不认的变体，如 CMYJpeg/渐进式 png）经 Pillow 转 RGB PNG。"""
    import io
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
    buf.seek(0)
    return buf


def merge_images_to_pdf(page_paths: list[Path], out_path: Path) -> None:
    """按页序合并图片为单 PDF；任一页 img2pdf 直读失败则整组经 Pillow 转码兜底。"""
    import img2pdf
    out_path.parent.mkdir(parents=True, exist_ok=True)
    inputs = [
        _to_png_bytes(p) if p.suffix.lower() in {".tif", ".tiff"} else str(p)
        for p in page_paths
    ]
    try:
        data = img2pdf.convert(inputs)
    except Exception:
        data = img2pdf.convert([_to_png_bytes(p) for p in page_paths])
    out_path.write_bytes(data)


def apply_plan(plan: dict, src_root: Path, staging_root: Path) -> tuple[int, int]:
    """复制文档 + 合并图片组，返回 (复制数, 合并数)。幂等：覆盖同名产物。"""
    copied = 0
    for src_rel, out_rel in plan["docs"]:
        dst = staging_root / out_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_root / src_rel, dst)
        copied += 1
    merged = 0
    for out_rel, rels in plan["merges"]:
        merge_images_to_pdf([src_root / r for r in rels], staging_root / out_rel)
        merged += 1
    return copied, merged


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def render_report(plan: dict, src_root: Path, staging_root: Path, applied: bool) -> str:
    n_pages = sum(len(rels) for _, rels in plan["merges"])
    lines = [
        "# 泾惠渠语料预处理报告（jhc_prepare）",
        "",
        f"- 模式：{'--apply（已落盘）' if applied else 'dry-run（未落盘）'}",
        f"- 源：`{src_root}`",
        f"- 暂存语料根：`{staging_root}`",
        f"- 文档复制：{len(plan['docs'])} 个",
        f"- 图片合并：{n_pages} 张 → {len(plan['merges'])} 份 PDF",
        f"- 跳过：{len(plan['skips'])} 个",
        "",
        "## 合并分组（组名 / 页数）",
        "",
        "| 暂存路径 | 页数 |",
        "|---|---|",
    ]
    for out_rel, rels in plan["merges"]:
        lines.append(f"| {out_rel} | {len(rels)} |")
    lines += ["", "## 跳过清单", ""]
    for rel, reason in plan["skips"]:
        lines.append(f"- {rel} — {reason}")
    if plan["reviews"]:
        lines += ["", "## ⚠ needs_review（压缩包内容未解压，确认后用工具解出再重跑）", ""]
        lines += [f"- {r}" for r in plan["reviews"]]
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="泾惠渠语料预处理：扫描件合并 PDF + 文档归集到暂存语料根"
    )
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC, help="原始资料根（只读）")
    parser.add_argument("--staging", type=Path, default=DEFAULT_STAGING, help="暂存语料根（导入源）")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="审计报告输出路径")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="只出报告（默认）")
    parser.add_argument("--apply", action="store_true", help="实际复制文档并生成合并 PDF")
    args = parser.parse_args(argv)

    if args.apply and args.dry_run:
        parser.error("--apply 与 --dry-run 互斥")
    if not args.src.is_dir():
        print(f"[ERROR] 源目录不存在：{args.src}")
        sys.exit(1)

    plan = build_plan(args.src)
    applied = args.apply
    copied = merged = 0
    if applied:
        copied, merged = apply_plan(plan, args.src, args.staging)

    report = render_report(plan, args.src, args.staging, applied)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")

    print(f"[INFO] 文档 {len(plan['docs'])}，图片 {sum(len(r) for _, r in plan['merges'])} 张 → "
          f"合并 PDF {len(plan['merges'])} 份，跳过 {len(plan['skips'])}，needs_review {len(plan['reviews'])}")
    if applied:
        print(f"[INFO] 已落盘：复制 {copied} 文档，生成 {merged} 合并 PDF → {args.staging}")
    else:
        print("[dry-run] 未落盘。确认报告后执行：python jhc_prepare.py --apply")
    print(f"[INFO] 报告已写入 {args.report}")
    if plan["reviews"]:
        print(f"[WARN] {len(plan['reviews'])} 个压缩包无同名解压目录，见报告 needs_review")


if __name__ == "__main__":
    main()
