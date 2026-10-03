"""
jhc_prepare.py — 泾惠渠语料预处理（阶段 0 前置，先于 corpus.py 扫描）

职责（方案 2026-09-22，与 profiles/jinghuiqu.py 配套）：
  1. 遍历原始资料根（默认 JHC_SRC_ROOT，只读不动源文件）；
  2. 文档（pdf/word/excel）→ 复制到暂存语料根 out/jhc_corpus/ 同相对路径；
     根目录散落文档归入 计划处/（jhc3）；
  3. 扫描件图片（jpg/jpeg/png/tif，含大写扩展名）→ 按"父目录 + 去页号后的
     文件名主干"分组，组内按页码数值排序，用 img2pdf 合并为
     ``<组名>（合并）.pdf``（corpus.py 据 MERGED_TAG 标记打 source_format=merged_pdf）。
     单图独立成 1 页 PDF——统一走 RAGFlow naive 解析器的 PDF-OCR 路径，
     避免逐张图片入库造成检索碎片化；
  4. 压缩包（rar/zip）旁侧有同名解压目录则跳过，否则记 needs_review；
     json/wps 等不支持格式进跳过清单；
  5. 质量处置（配合 out/triage/image_triage.jsonl 分诊结果与双份目录分析）：
     - 空白页（content=blank）从合并组剔除，不生成死页；
     - 同目录字节级相同图片副本（如 xx(1).jpg）去重，防单页重复合并 PDF；
     - 双份目录中页图与旧转换 PDF 内容重复时，弃 PDF 保图片组
       （配对：PDF stem==组主干且页数精确相等，或页数==目录页图总数且
       单 PDF/stem 命中；页数不符的不自动弃——概算书教训：PDF 含页图
       缺页时弃 PDF 会丢内容——进 needs_review 人工定夺）；
     - 长边 >3500px 或非 jpg/png 页图合并时降采样/重编码 JPEG q85，
       防超大页撑爆解析、防旧转换式 FlateDecode 体积膨胀（2.1GB 单件教训）；
  6. 产出 out/jinghuiqu/prepare_report.md 审计报告。

CLI（变更类脚本惯例：默认 dry-run，--apply 显式确认）：
    python jhc_prepare.py            # 只出报告
    python jhc_prepare.py --apply    # 生成暂存语料
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path, PurePosixPath

# 共享常量单源：jhc_constants 不 import config，无画像加载副作用。
# profiles/jinghuiqu.py 与 corpus.py（经 config 转发）引用同一来源，
# 消除此前"改一处须同步另一处"的双源漂移风险。
from jhc_constants import (
    ARCHIVE_EXTS,
    DOC_EXTS,
    IMG_EXTS,
    JHC_CORPUS_ROOT_DEFAULT,
    JHC_SRC_ROOT_DEFAULT,
    JUNK_DIRS,
    JUNK_FILES,
    MERGE_JPEG_QUALITY,
    MERGE_MAX_LONG_SIDE,
    MERGED_TAG,
    PROJECT_ROOT,
    ROOT_DOC_TARGET,
    TRIAGE_JSONL_DEFAULT,
)

DEFAULT_SRC = JHC_SRC_ROOT_DEFAULT
DEFAULT_STAGING = JHC_CORPUS_ROOT_DEFAULT
DEFAULT_REPORT = PROJECT_ROOT / "out" / "jinghuiqu" / "prepare_report.md"
DEFAULT_TRIAGE = TRIAGE_JSONL_DEFAULT

# 页号剥离模式（按序尝试，首个命中生效）：
#   方案_01 / 记录-2 / 证件_0001      → [_-] + 尾部数字
#   第3页 / 图（4） / 页(12)          → 中文页号与括号编号
#   记录 5                            → 空格 + 数字
#   机电设备运行记录……管理处1          → 裸尾数字（base 至少 1 字符）
# 裸尾数字模式限 1-3 位：真实页号罕超 3 位，而"…记录表2025"的 4 位年份
# 若被剔成页码 2025 会把同年份文件误并组（评审 #8）。
_PAGE_PATTERNS = [
    re.compile(r"^(.+?)[_-](\d{1,4})$"),
    re.compile(r"^(.+?)第(\d{1,4})页$"),
    re.compile(r"^(.+?)[（(](\d{1,4})[）)]$"),
    re.compile(r"^(.+?)\s+(\d{1,4})$"),
    re.compile(r"^(.+?)(\d{1,3})$"),
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
# 分诊结果与双份目录去重（纯逻辑，供单测）
# ---------------------------------------------------------------------------

def load_blank_rels(triage_path: Path | None) -> set[str]:
    """读 image_triage.jsonl，返回 content==blank 的 rel（POSIX）集合。
    文件不存在/坏行 → 尽力解析，读不到即空集（调用方负责告警）。"""
    if not triage_path or not triage_path.is_file():
        return set()
    blanks: set[str] = set()
    for line in triage_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("content") == "blank" and rec.get("rel"):
            blanks.add(str(rec["rel"]))
    return blanks


def _pdf_page_count(src_root: Path, rel: str) -> int | None:
    """PDF 页数；读不了（损坏/非 PDF）返回 None，不阻断计划构建。"""
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(src_root / rel)).pages)
    except Exception:
        return None


def _dedup_identical_images(src_root: Path, images: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """同目录内字节完全相同的图片去重（如 ``xx(1).jpg`` 双份副本），保留规范名。

    只对"同目录 + 同大小"候选做 SHA-256，避免全量读盘。这类副本若不去重，
    会按 ``xx_01(1)`` 主干生成整批单页合并 PDF，向语料重复灌入同内容。
    返回 (保留 rel 列表, dup_skips)。
    """
    import hashlib
    by_dir_size: dict[tuple[str, int], list[str]] = {}
    for rel in images:
        p = src_root / rel
        by_dir_size.setdefault((str(PurePosixPath(rel).parent), p.stat().st_size), []).append(rel)
    kept: list[str] = []
    dup_skips: list[tuple[str, str]] = []
    for rels in by_dir_size.values():
        if len(rels) == 1:
            kept.extend(rels)
            continue
        # 无 "(1)" 副本标记者优先作保留名
        digests: dict[str, str] = {}
        for rel in sorted(rels, key=lambda r: ("(1)" in r, r)):
            h = hashlib.sha256((src_root / rel).read_bytes()).hexdigest()
            if h in digests:
                dup_skips.append((rel, f"与 {digests[h]} 内容重复（同哈希），已去重"))
            else:
                digests[h] = rel
                kept.append(rel)
    order = {rel: i for i, rel in enumerate(images)}
    kept.sort(key=lambda r: order[r])
    return kept, dup_skips


def pair_duplicate_pdfs(docs: list[tuple[str, str]],
                        merges: list[tuple[str, list[str]]],
                        src_root: Path) -> tuple[list[tuple[str, str]],
                                                 list[tuple[str, str]],
                                                 list[str]]:
    """双份目录去重：同目录下旧转换 PDF 与页图内容重复时，弃 PDF 保图片组。

    放弃条件（页数 pypdf 实读；merges 为剔空白前的原始页图口径）：
      ① PDF stem == 某分组主干 且 p == 该组页数（精确组覆盖）；
      ② p == 目录内全部页图总数，且（目录内唯一 PDF 或 stem 命中）——
         封面单独成组/整目录多组的场景（如"供暖泵房"封面 1 + 内页 24
         = 25 页与 PDF 相等），整目录覆盖即内容重复。
    其余（含页数读不出）保留 PDF 并进 needs_review：页数不等可能意味着
    PDF 含页图缺页（实测教训：概算书 PDF 28 页而页图缺 03/04/06 仅 25 张，
    直接弃 PDF 会丢 3 页内容）。

    旧转换 PDF（56 个双份目录）以原图为唯一数据源——历史教训：其 FlateDecode
    版比原图组大 4 倍（2.1GB 单件），且内容像素与图组一致，无入库价值。

    返回 (保留docs, dup_skips, review追加项)。
    """
    merges_by_dir: dict[str, list[tuple[str, list[str]]]] = {}
    dir_total: dict[str, int] = {}
    for out_rel, rels in merges:
        base = PurePosixPath(out_rel).stem
        if base.endswith(MERGED_TAG):
            base = base[: -len(MERGED_TAG)]
        parent = str(PurePosixPath(rels[0]).parent)
        merges_by_dir.setdefault(parent, []).append((base, rels))
        dir_total[parent] = dir_total.get(parent, 0) + len(rels)

    pdfs_by_dir: dict[str, list[str]] = {}
    for src_rel, _ in docs:
        if src_rel.lower().endswith(".pdf"):
            pdfs_by_dir.setdefault(str(PurePosixPath(src_rel).parent), []).append(src_rel)

    kept: list[tuple[str, str]] = []
    dup_skips: list[tuple[str, str]] = []
    review_adds: list[str] = []
    for src_rel, out_rel in docs:
        if not src_rel.lower().endswith(".pdf"):
            kept.append((src_rel, out_rel))
            continue
        d = str(PurePosixPath(src_rel).parent)
        groups = merges_by_dir.get(d, [])
        if not groups:
            kept.append((src_rel, out_rel))
            continue
        stem = PurePosixPath(src_rel).stem
        stem_group = next((g for g in groups if g[0] == stem), None)
        n = _pdf_page_count(src_root, src_rel) if (stem_group is not None or len(pdfs_by_dir.get(d, [])) == 1) else None
        total = dir_total[d]
        drop_reason: str | None = None
        if n is not None:
            if stem_group is not None and n == len(stem_group[1]):
                drop_reason = f"与图片组重复（旧转换 PDF，以图为准）：{stem_group[0]}（{n} 页）"
            elif (len(pdfs_by_dir[d]) == 1 or stem_group is not None) and n == total:
                drop_reason = f"与目录页图整体重复（旧转换 PDF，以图为准）：{total} 页"
        if drop_reason is not None:
            dup_skips.append((src_rel, drop_reason))
        else:
            kept.append((src_rel, out_rel))
            pages_desc = f"PDF {n} 页" if n is not None else "PDF 页数未读"
            review_adds.append(
                f"{src_rel}（同目录有页图组但页数不符，未自动去重，需人工确认："
                f"{pages_desc} vs 目录页图 {total} 张）")
    return kept, dup_skips, review_adds


# ---------------------------------------------------------------------------
# 扫描 → 导入计划
# ---------------------------------------------------------------------------

def build_plan(src_root: Path,
               triage_path: Path | None = DEFAULT_TRIAGE) -> dict:
    """遍历源根，产出 {"docs", "merges", "skips", "reviews"}。

    docs:    [(源rel, 暂存rel)]（根目录文档的暂存rel加 计划处/ 前缀）；
             已剔除与页图组重复的旧转换 PDF
    merges:  [(暂存rel(合并PDF), [源图片rel…])]；已剔除 VLM 分诊空白页
    skips:   [(源rel, 原因)]（含压缩包/不支持格式/空白页/重复旧 PDF）
    reviews: 需要人工定夺的项（无解压目录的压缩包、页数不符的并存 PDF）

    triage_path=None 显式关闭空白页剔除；默认读 DEFAULT_TRIAGE。
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
        if rel.name in JUNK_FILES:
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

    # 同目录字节级双份副本（xx(1).jpg）去重：否则按 (1) 主干生成单页重复合并 PDF
    images, img_dup_skips = _dedup_identical_images(src_root, images)
    skips.extend(img_dup_skips)

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

    # 质量处置顺序：先按原始页数做 PDF↔图组去重，再剔空白页（口径不被打乱）
    docs, dup_skips, review_adds = pair_duplicate_pdfs(docs, merges, src_root)
    skips.extend(dup_skips)
    reviews.extend(review_adds)

    blanks = load_blank_rels(triage_path)
    if blanks:
        filtered: list[tuple[str, list[str]]] = []
        for out_rel, rels in merges:
            kept = [r for r in rels if r not in blanks]
            dropped = [r for r in rels if r in blanks]
            skips.extend((r, "VLM 分诊为空白页，合并时剔除") for r in dropped)
            if kept:
                filtered.append((out_rel, kept))
            else:
                skips.append((out_rel, "合并组全部为空白页，未生成 PDF"))
        merges = filtered

    return {"docs": docs, "merges": merges, "skips": skips, "reviews": reviews}


# ---------------------------------------------------------------------------
# 合并执行（I/O 部分；img2pdf/Pillow 延迟导入，单测只测分组纯函数）
# ---------------------------------------------------------------------------

def _needs_recompress(path: Path) -> bool:
    """非 jpg/png 或长边 > MERGE_MAX_LONG_SIDE → 需 Pillow 重编码。
    头信息读不出也走重编码路径（让 Pillow 报出可读错误）。"""
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        return True
    try:
        from PIL import Image
        with Image.open(path) as im:
            return max(im.size) > MERGE_MAX_LONG_SIDE
    except Exception:
        return True


def _recompress_tmpfile(path: Path, tmp_dir: Path, idx: int = 0) -> Path:
    """Pillow 转 RGB，超长边降采样至 MERGE_MAX_LONG_SIDE，存 JPEG
    （MERGE_JPEG_QUALITY）临时文件，返回路径。img2pdf 对 JPEG 是字节级
    无损内嵌（DCTDecode），避免旧转换脚本"转 PNG → FlateDecode"的体积膨胀；
    idx 防同组同名 stem 冲突。"""
    from PIL import Image
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{idx:04d}.jpg"
    with Image.open(path) as im:
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")           # 透明底先合白底，防转黑底毁字迹
            bg = Image.new("RGB", rgba.size, "white")
            bg.paste(rgba, mask=rgba.split()[3])
            im = bg
        else:
            im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > MERGE_MAX_LONG_SIDE:
            scale = MERGE_MAX_LONG_SIDE / max(w, h)
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                           Image.Resampling.LANCZOS)
        im.save(tmp, format="JPEG", quality=MERGE_JPEG_QUALITY)
    return tmp


def merge_images_to_pdf(page_paths: list[Path], out_path: Path) -> None:
    """按页序合并图片为单 PDF。页图预处置：长边 ≤3500px 的 jpg/png 直接
    原字节嵌入（零损失）；tif、超长边图与直读失败的图经 Pillow 重编码
    JPEG q85 临时文件兜底。大组用临时文件而非全内存，避免 121 页扫描件
    转码峰值内存风险（评审 #3）。直读失败时打印警告含异常类型与信息，
    便于定位问题页（评审 #2）。"""
    import img2pdf
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_path.parent / ".jhc_tmp"
    tmp_files: list[Path] = []
    try:
        inputs = []
        for idx, p in enumerate(page_paths):
            if _needs_recompress(p):
                tmp = _recompress_tmpfile(p, tmp_dir, idx)
                tmp_files.append(tmp)
                inputs.append(str(tmp))
            else:
                inputs.append(str(p))
        try:
            data = img2pdf.convert(inputs)
        except Exception as exc:
            print(f"[WARN] img2pdf 直读失败（{type(exc).__name__}: {exc}），全组重编码 JPEG 兜底")
            fallback: list[str] = []
            for idx, p in enumerate(page_paths):
                tmp = _recompress_tmpfile(p, tmp_dir, idx)
                tmp_files.append(tmp)
                fallback.append(str(tmp))
            data = img2pdf.convert(fallback)
        out_path.write_bytes(data)
    finally:
        for tmp in tmp_files:
            tmp.unlink(missing_ok=True)
        if tmp_dir.exists():
            try:
                tmp_dir.rmdir()
            except OSError:
                pass


def apply_plan(plan: dict, src_root: Path, staging_root: Path) -> tuple[int, int, list[tuple[str, str]]]:
    """复制文档 + 合并图片组，返回 (复制数, 合并数, 失败列表)。
    幂等：覆盖同名产物。失败项记录但不中断后续，便于重跑只重试失败项（评审 #10）。"""
    failures: list[tuple[str, str]] = []
    copied = 0
    for src_rel, out_rel in plan["docs"]:
        try:
            dst = staging_root / out_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_root / src_rel, dst)
            copied += 1
        except OSError as exc:
            failures.append((out_rel, f"复制失败: {type(exc).__name__}: {exc}"))
    merged = 0
    for out_rel, rels in plan["merges"]:
        try:
            merge_images_to_pdf([src_root / r for r in rels], staging_root / out_rel)
            merged += 1
        except Exception as exc:
            failures.append((out_rel, f"合并失败: {type(exc).__name__}: {exc}"))
    return copied, merged, failures


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def render_report(plan: dict, src_root: Path, staging_root: Path, applied: bool,
                  failures: list[tuple[str, str]] | None = None) -> str:
    n_pages = sum(len(rels) for _, rels in plan["merges"])
    n_blank = sum(1 for _, r in plan["skips"] if r.startswith("VLM 分诊为空白页"))
    n_dup = sum(1 for _, r in plan["skips"] if "旧转换 PDF" in r)
    n_img_dup = sum(1 for _, r in plan["skips"] if "同哈希" in r)
    lines = [
        "# 泾惠渠语料预处理报告（jhc_prepare）",
        "",
        f"- 模式：{'--apply（已落盘）' if applied else 'dry-run（未落盘）'}",
        f"- 源：`{src_root}`",
        f"- 暂存语料根：`{staging_root}`",
        f"- 文档复制：{len(plan['docs'])} 个",
        f"- 图片合并：{n_pages} 张 → {len(plan['merges'])} 份 PDF",
        f"- 空白页剔除：{n_blank} 张（VLM 分诊）",
        f"- 重复旧 PDF 去重：{n_dup} 份（弃 PDF 保图片组）",
        f"- 相同图片去重：{n_img_dup} 张（同目录同哈希副本）",
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
        lines += ["", "## ⚠ needs_review（需人工定夺）", ""]
        lines += [f"- {r}" for r in plan["reviews"]]
    if failures:
        lines += ["", "## ✗ 失败清单（需排查后重跑，已成功项幂等跳过）", ""]
        for rel, reason in failures:
            lines.append(f"- {rel} — {reason}")
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
    parser.add_argument("--triage", type=Path, default=DEFAULT_TRIAGE,
                        help="VLM 分诊 JSONL（空白页剔除依据，默认读 out/triage）")
    parser.add_argument("--no-triage", action="store_true", help="关闭空白页剔除")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="只出报告（默认）")
    parser.add_argument("--apply", action="store_true", help="实际复制文档并生成合并 PDF")
    args = parser.parse_args(argv)

    if args.apply and args.dry_run:
        parser.error("--apply 与 --dry-run 互斥")
    if not args.src.is_dir():
        print(f"[ERROR] 源目录不存在：{args.src}")
        sys.exit(1)
    if not args.no_triage and not args.triage.is_file():
        print(f"[WARN] 未找到分诊结果 {args.triage}，空白页未剔除（先跑 image_triage.py 或 --no-triage 显式关闭）")

    plan = build_plan(args.src, None if args.no_triage else args.triage)
    applied = args.apply
    copied = merged = 0
    failures: list[tuple[str, str]] = []
    if applied:
        copied, merged, failures = apply_plan(plan, args.src, args.staging)

    report = render_report(plan, args.src, args.staging, applied, failures)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")

    n_blank = sum(1 for _, r in plan["skips"] if r.startswith("VLM 分诊为空白页"))
    n_dup = sum(1 for _, r in plan["skips"] if "旧转换 PDF" in r)
    n_img_dup = sum(1 for _, r in plan["skips"] if "同哈希" in r)
    print(f"[INFO] 文档 {len(plan['docs'])}，图片 {sum(len(r) for _, r in plan['merges'])} 张 → "
          f"合并 PDF {len(plan['merges'])} 份（空白剔除 {n_blank} 张、旧PDF去重 {n_dup} 份、"
          f"同哈希图片去重 {n_img_dup} 张），"
          f"跳过 {len(plan['skips'])}，needs_review {len(plan['reviews'])}")
    if applied:
        print(f"[INFO] 已落盘：复制 {copied} 文档，生成 {merged} 合并 PDF → {args.staging}")
        if failures:
            print(f"[WARN] {len(failures)} 项失败，见报告失败清单")
    else:
        print("[dry-run] 未落盘。确认报告后执行：python jhc_prepare.py --apply")
    print(f"[INFO] 报告已写入 {args.report}")
    if plan["reviews"]:
        print(f"[WARN] {len(plan['reviews'])} 项 needs_review（压缩包未解压 / 并存 PDF 页数不符），见报告")


if __name__ == "__main__":
    main()
