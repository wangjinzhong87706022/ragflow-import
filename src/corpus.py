"""corpus.py — 语料扫描、去重、映射表生成（语料源=整理后的原始文件库 CORPUS_ROOT）."""

from __future__ import annotations

import csv
import datetime
import hashlib
import re
from dataclasses import dataclass, asdict
from pathlib import Path, PurePosixPath

from config import (
    CORPUS_ROOT,
    DIR_DATASET,
    DOC_EXTS,
    FLOOD_EVENT_BY_SUBDIR,
    SKIP_DIRS,
    IMPORT_COLS,
    LOCATION_KEYWORDS,
    DEPT_KEYWORDS,
    OUT_DIR,
)


# ---------------------------------------------------------------------------
# Entry dataclass
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    rel: str                       # relative path under CORPUS_ROOT（即导入源文件）
    dataset_key: str               # ds1..ds5
    doc_category: str
    sub_category: str
    flood_event: str | None
    doc_type: str                  # 文本/表格/图纸
    year: int | None
    source_format: str             # pdf/word/excel
    quality: str
    responsible_dept: str | None
    doc_nature: str
    location: str | None
    flood_magnitude: str
    skip_reason: str | None        # None = importable; set if skipped
    duplicate_of: str | None       # rel of canonical file if this is a dup
    sha256: str                    # hex digest of file content


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sha256_hex(path: Path) -> str:
    """Return SHA-256 hex digest of file at *path*."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_marked_duplicate(name: str) -> bool:
    """整理期手工复制的 _dup/_dupx 变体按名字标记（内容哈希去重另行兜底）。"""
    stem = PurePosixPath(name).stem.lower()
    return stem.endswith("_dup") or stem.endswith("_dupx")


def classify(rel: str) -> str:
    """
    Map *rel* to a dataset key：一级目录即分类（DIR_DATASET），不猜测。
    """
    top = PurePosixPath(rel.replace("\\", "/")).parts[0]
    if top in DIR_DATASET:
        return DIR_DATASET[top]
    raise ValueError(f"Unknown corpus directory: {rel}")


def parse_flood_event(rel: str) -> str | None:
    r"""
    Derive flood_event from *rel*（新布局：目录段即语义）。

    Priority:
      1. 月-日子事件段（如 ``10-3洪水`` → 2021-10）。仅在 rel 含 "2021" 时启用，
         保证粒度的同时防止其他年份目录的同形片段错标。
      2. 路径段精确匹配 FLOOD_EVENT_BY_SUBDIR（如 ``05-2013年洪水(7-22)`` → 2013-7）。
    """
    parts = list(PurePosixPath(rel.replace("\\", "/")).parts)
    joined = "/".join(parts)

    if "2021" in joined:
        for part in parts:
            for seg in part.split("_"):
                m = re.match(r"(\d{1,2})-(\d{1,2})洪水", seg)
                if m:
                    return f"2021-{m.group(1).zfill(2)}"

    for part in parts:
        if part in FLOOD_EVENT_BY_SUBDIR:
            return FLOOD_EVENT_BY_SUBDIR[part]
    return None


# 文件名年份解析的合理区间：下限取建库史料起点；上限取当前年份+1（跨年计划/预算文件常见）
MIN_YEAR = 1949
MAX_YEAR = datetime.date.today().year + 1


def parse_year(rel: str) -> int | None:
    """Extract the first *plausible* 4-digit year from *rel*.

    区间外的数字串视为编号而非年份（如 TB0207.xls 曾被解出 year=207，
    review P2）。找不到或越界一律 None。
    """
    m = re.search(r"\d{4}", rel)
    if not m:
        return None
    year = int(m.group(0))
    return year if MIN_YEAR <= year <= MAX_YEAR else None


def source_format_from_ext(ext: str) -> str:
    ext = ext.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in {".doc", ".docx"}:
        return "word"
    if ext in {".xls", ".xlsx"}:
        return "excel"
    return "unknown"


def quality_from_source_format(sf: str) -> str:
    """
    原生可编辑格式（word/excel）文本保真 → high；
    PDF 的文本层可能是扫描件 → 默认 medium，人工审查门可上调。
    """
    return "high" if sf in {"word", "excel"} else "medium"


def _should_skip(rel_parts: tuple[str, ...]) -> bool:
    return any(p in SKIP_DIRS for p in rel_parts)


# ---------------------------------------------------------------------------
# Metadata derivation
# ---------------------------------------------------------------------------

_DOC_CATEGORY_FROM_DS = {
    "ds1": "规程预案",
    "ds2": "基础数据",
    "ds3": "洪水资料",
    "ds4": "组织管理",
    "ds5": "工程资料",
}

_DOC_NATURE_BY_DS = {
    "ds1": "法规",
    "ds2": "技术",
    "ds3": "技术",
    "ds4": "管理",
    "ds5": "技术",
}


def _doc_type_from_rel(rel: str, source_format: str) -> str:
    if source_format == "excel":
        return "表格"
    if "图纸" in rel or "竣工图" in rel:
        return "图纸"
    return "文本"


def derive_metadata(rel: str, ds_key: str, source_format: str) -> dict:
    """由路径与扩展名推导全部元数据列（初稿——人工审查门最终裁定）。"""
    parts = list(PurePosixPath(rel.replace("\\", "/")).parts)
    raw_sub = parts[1] if len(parts) >= 2 else ""
    sub_category = re.sub(r"^\d+-", "", raw_sub) if raw_sub else ""
    return {
        "dataset_key": ds_key,
        "doc_category": _DOC_CATEGORY_FROM_DS.get(ds_key, "未知"),
        "sub_category": sub_category,
        "flood_event": parse_flood_event(rel),
        "doc_type": _doc_type_from_rel(rel, source_format),
        "year": parse_year(rel),
        "source_format": source_format,
        "quality": quality_from_source_format(source_format),
        "responsible_dept": next((kw for kw in DEPT_KEYWORDS if kw in rel), None),
        "doc_nature": _DOC_NATURE_BY_DS.get(ds_key, "技术"),
        "location": next((kw for kw in LOCATION_KEYWORDS if kw in rel), None),
        "flood_magnitude": "不适用",
    }


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def scan() -> list[Entry]:
    """
    Walk CORPUS_ROOT（仅文档扩展名），构建 Entry 列表并去重。

    - SKIP_DIRS 整目录排除（现场影像/压缩包等）
    - 一级目录必须能被 DIR_DATASET 分类；未收录目录收集后一次性报错
    - 去重两层：_dup 名字标记 + sha256 内容哈希（同库内保留最短 rel 为正本）
    """
    entries: list[Entry] = []
    seen: dict[tuple[str, str], list[Entry]] = {}   # (sha256, dataset_key) → [...]
    unknown: list[str] = []

    paths = sorted(
        p for p in CORPUS_ROOT.rglob("*")
        if p.is_file() and p.suffix.lower() in DOC_EXTS
    )
    for path in paths:
        rel_parts = path.relative_to(CORPUS_ROOT).parts
        if _should_skip(rel_parts):
            continue
        rel = str(Path(*rel_parts))

        try:
            ds_key = classify(rel)
        except ValueError:
            unknown.append(rel)
            continue

        sf = source_format_from_ext(path.suffix)
        meta = derive_metadata(rel, ds_key, sf)

        skip_reason: str | None = None
        if is_marked_duplicate(path.name):
            skip_reason = "duplicate"

        entry = Entry(
            rel=rel,
            **meta,
            skip_reason=skip_reason,
            duplicate_of=None,
            sha256=sha256_hex(path),
        )
        entries.append(entry)

        key = (entry.sha256, ds_key)
        seen.setdefault(key, []).append(entry)

    # 内容哈希去重：组内保留最短 rel 为正本
    for group in seen.values():
        if len(group) <= 1:
            continue
        canonical = min(group, key=lambda e: len(e.rel))
        for e in group:
            if e is not canonical:
                e.duplicate_of = canonical.rel
                e.skip_reason = "duplicate"

    if unknown:
        raise ValueError(
            f"{len(unknown)} 个文件位于未收录目录，请在 config.DIR_DATASET 显式指派后重跑:\n  "
            + "\n  ".join(unknown)
        )

    return entries


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------

def write_mapping_csv(entries: list[Entry], path: str | Path) -> None:
    """Write *entries* to CSV at *path* using IMPORT_COLS."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=IMPORT_COLS, extrasaction="ignore")
        writer.writeheader()
        for e in entries:
            writer.writerow(asdict(e))


def read_mapping_csv(path: str | Path) -> list[dict]:
    """Read mapping CSV back to list[dict]."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def build_manifest(entries: list[Entry]) -> list[Entry]:
    """Return entries where skip_reason is None (files to actually import)."""
    return [e for e in entries if e.skip_reason is None]


# ---------------------------------------------------------------------------
# CLI — python -m corpus 生成 out/mapping.csv（阶段0 入口）
# ---------------------------------------------------------------------------

def main() -> None:
    entries = scan()
    out_csv = OUT_DIR / "mapping.csv"
    write_mapping_csv(entries, out_csv)

    importable = [e for e in entries if e.skip_reason is None]
    dups = [e for e in entries if e.skip_reason == "duplicate"]
    print(
        f"[INFO] 扫描 {len(entries)} 行：可导入 {len(importable)}，重复跳过 {len(dups)}"
    )
    by_ds: dict[str, int] = {}
    for e in importable:
        by_ds[e.dataset_key] = by_ds.get(e.dataset_key, 0) + 1
    for k in sorted(by_ds):
        print(f"  {k}: {by_ds[k]}")
    print(f"[INFO] 映射表已写入 {out_csv} —— 请人工审查后继续（重点：dataset_key 指派 / flood_event / skip_reason）")


if __name__ == "__main__":
    main()
