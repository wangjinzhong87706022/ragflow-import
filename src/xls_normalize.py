"""xls_normalize.py — 旧版 BIFF .xls 水文报表预处理规范化模块。

将非标准 .xls（WPS 生成的 BIFF 格式）规范化为表头优先的 .xlsx，
使 RAGFlow 原生 Excel 解析器能正确切片，无需修改 RAGFlow 源码。

综合方案来源：e:\\git\\ragflow\\tools\\normalize_and_import.py（方案 A 预处理路线）
+ 本项目 shell_import.py（壳模式回退路线）。本模块只负责预处理纯函数，
API 交互复用 ragflow_client.py，导入编排复用 run_import.py。

核心流程（逐 sheet）：
  1. pd.read_excel(header=None, sheet_name=None) 保留原始网格（空行/标题/单位行完整）
  2. 检测真实表头行（前 6 行中首个 ≥2 列填充且 ≥50% 文本的行）
  3. 提取上下文文本（表头上方的标题/单位行 → 拼入 sheet 名 → 随切片走）
  4. 还原日期序列号（列名含"日期/时间" + 值在 [20000,60000] → YYYY-MM-DD）
  5. 清洗单元格（NaN→None, 控制字符→空格, 整数浮点→int）
  6. 安全 sheet 名（去非法字符, 截断 31 字符, 去重）
  7. 写入新 Workbook 保存为 .xlsx

产物路径：out/xls_normalized/<原文件stem>.<路径短哈希>.normalized.xlsx
（不污染语料只读根目录；带哈希避免同名文件互相覆盖）
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Optional

try:
    import pandas as pd
    from openpyxl import Workbook
    _HAS_DEPS = True
except ImportError:
    pd = None
    Workbook = None
    _HAS_DEPS = False

# Excel 日期序列号还原：列名含以下关键字时触发
DATE_KEYWORDS = ("日期", "时间")
DATE_SERIAL_RANGE = (20000, 60000)  # Excel 序列号 20000≈1954年, 60000≈2064年
ILLEGAL_SHEET_CHARS = re.compile(r"[:\\/?*\[\]]")
# openpyxl 对 0x00-0x08, 0x0B-0x0C, 0x10-0x1F 会抛 IllegalCharacterError
ILLEGAL_CELL_CHARS = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]")


# ---------------------------------------------------------------------------
# 判断与入口
# ---------------------------------------------------------------------------

def is_biff_xls(path: Path) -> bool:
    """判断文件是否为旧版 BIFF .xls（OLE2 复合文档格式），而非 .xlsx（ZIP 格式）。

    BIFF .xls 文件头：\\xd0\\xcf\\x11\\xe0（OLE2 magic）
    .xlsx 文件头：PK\\x03\\x04（ZIP magic）
    """
    try:
        with open(path, "rb") as f:
            head = f.read(8)
        return head[:4] == b"\xd0\xcf\x11\xe0"
    except (OSError, IOError):
        return False


def normalize_if_needed(src: Path, out_dir: Path) -> tuple[Path, bool]:
    """如果是旧版 BIFF .xls 则规范化为 .xlsx，否则原样返回。

    Returns (upload_path, was_normalized)
    """
    if not is_biff_xls(src):
        return src, False
    normalized = normalize_xls(src, out_dir)
    return normalized, True


# ---------------------------------------------------------------------------
# 预处理纯函数（移植方案 A，去掉 API 交互）
# ---------------------------------------------------------------------------

def _is_blank(v) -> bool:
    """统一判断单元格是否为空：None / NaN / 纯空白字符串。"""
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        return False
    if isinstance(v, str):
        return v.strip() == ""
    return False


def _detect_header_index(grid: list[list]) -> int:
    """在前 6 行中找真实表头行：首个 ≥2 列填充且 ≥50% 为文本的行。

    水文报表的标题行通常只有 1 列有值（"柳林断面流量统计表"），
    表头行有多列且多为文本（"日期","报汛时间","库水位"）。
    """
    def filled(row):
        return [v for v in row if not _is_blank(v)]

    limit = min(len(grid), 6)
    i = 0
    while i < limit and len(filled(grid[i])) < 2:
        i += 1
    if i == 0 or i >= limit or i >= len(grid) - 1:
        return 0
    candidates = filled(grid[i])
    if len(candidates) >= 2 and sum(isinstance(v, str) for v in candidates) / len(candidates) >= 0.5:
        return i
    return 0


def _context_text(rows_before_header: list[list]) -> str:
    """把表头上方的非空文本拼接成上下文字符串（标题 + 单位行）。"""
    parts = []
    for row in rows_before_header:
        texts = [str(v).strip() for v in row if not _is_blank(v)]
        if texts:
            parts.append(" ".join(texts))
    return "; ".join(parts)


def _clean_cell(v):
    """清洗单元格：空值→None, 字符串去控制字符, 整数浮点→int。"""
    if _is_blank(v):
        return None
    if isinstance(v, str):
        return ILLEGAL_CELL_CHARS.sub(" ", v).strip()
    # 注意：不能加 `"." not in str(v)` 之类的判据——float 的 str() 恒含 "."，
    # 会让整个分支永不命中（2026-09-12 评审 P2-6）。整数浮点直接降为 int。
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return int(v)
    return v


def _restore_dates(header: list, row: list) -> list:
    """列名含"日期/时间"且值为合理序列号时，还原为 YYYY-MM-DD 字符串。

    Excel 1900 基准有闰年 bug：Excel 认为 1900-02-29 存在（实际不存在），
    因此序列号 ≥ 60 的日期需 +1 天补偿，否则会差 1 天
    （如 40752 应为 2011-07-29 而非 2011-07-28）。

    按行长度而不是 ``zip`` 遍历：数据行可能比表头长/短，用 zip 会截断整行
    导致尾部单元格丢失（评审 P2）。
    """
    out = []
    for i, v in enumerate(row):
        name = header[i] if i < len(header) else None
        if (isinstance(name, str)
                and any(k in name for k in DATE_KEYWORDS)
                and isinstance(v, (int, float))
                and not isinstance(v, bool)
                and DATE_SERIAL_RANGE[0] <= v <= DATE_SERIAL_RANGE[1]):
            serial = int(v)
            if serial >= 60:  # 1900-02-29 bug 补偿
                serial += 1
            v = (dt.datetime(1899, 12, 30) + dt.timedelta(days=serial)).strftime("%Y-%m-%d")
        out.append(v)
    return out


def _safe_sheet_name(title: str, used: set[str]) -> str:
    """清理 Excel sheet 名非法字符、截断 31 字符、去重。"""
    title = re.sub(r"\s+", "", ILLEGAL_SHEET_CHARS.sub("", title))
    title = title[:31].rstrip("; ；,，-(") or "Sheet1"
    base, k = title, 1
    while title.lower() in used:
        suffix = f"~{k}"
        title = base[: 31 - len(suffix)] + suffix
        k += 1
    return title


def _read_grids(src: Path, prefer_calamine: bool = False) -> dict:
    """多引擎读取：默认引擎与 calamine（Rust 实现，能读 WPS .xls）互为回退。

    ``prefer_calamine=True``（已知是 BIFF .xls 时）**优先**用 calamine——
    纯异常驱动的降级有个盲区：默认引擎（xlrd）若能打开但静默误读 WPS 非标准
    .xls，不会抛异常，就永远不会回退到 calamine（评审 P2-9）。
    """
    engines = ("calamine", None) if prefer_calamine else (None, "calamine")
    last_err = None
    for engine in engines:
        try:
            if engine is None:
                return pd.read_excel(src, sheet_name=None, header=None)
            return pd.read_excel(src, sheet_name=None, header=None, engine=engine)
        except Exception as e:
            last_err = e
    raise RuntimeError(f"cannot read {src}: {last_err}")


def normalize_xls(src: Path, out_dir: Path) -> Path:
    """将旧版 BIFF .xls 规范化为表头优先的 .xlsx。

    Parameters
    ----------
    src : Path
        原始 .xls 文件路径
    out_dir : Path
        产物目录（out/xls_normalized/），自动创建

    Returns
    -------
    Path
        规范化后的 .xlsx 文件路径
    """
    if not _HAS_DEPS:
        raise ImportError(
            "xls_normalize 需要 pandas + openpyxl + python-calamine，"
            "请 pip install pandas openpyxl python-calamine"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    # 产物名带源路径短哈希：不同目录下的同名 .xls（本项目存在多组同名文件）
    # 若共用 "<stem>.normalized.xlsx" 会互相覆盖（评审 P2-8）。
    digest = hashlib.sha1(str(src.resolve()).encode("utf-8")).hexdigest()[:8]
    dst = out_dir / f"{src.stem}.{digest}.normalized.xlsx"

    # 已确认是 BIFF .xls → 优先 calamine，避免 xlrd 静默误读 WPS 文件
    raw = _read_grids(src, prefer_calamine=is_biff_xls(src))
    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()

    for name, df in raw.items():
        df = df.dropna(how="all").dropna(axis=1, how="all").reset_index(drop=True)
        if df.empty:
            continue

        grid = df.values.tolist()
        h = _detect_header_index(grid)
        header = [_clean_cell(v) for v in df.iloc[h].tolist()]

        # 构造 sheet 名：原名 + 上下文文本（标题/单位行）
        title = re.sub(r"\s+", "", ILLEGAL_SHEET_CHARS.sub("", str(name)))
        for p in _context_text(grid[:h]).split(";"):
            p = re.sub(r"\s+", "", p.strip().rstrip(",，"))
            if not p:
                continue
            if len(title) + 1 + len(p) <= 31:
                title = f"{title}-{p}"
            else:
                break

        sheet_title = _safe_sheet_name(title, used)
        used.add(sheet_title.lower())
        ws = wb.create_sheet(title=sheet_title)

        # 写入表头 + 数据行（跳过全空行，还原日期，清洗单元格）
        ws.append(header)
        for row in grid[h + 1:]:
            if all(_is_blank(v) for v in row):
                continue
            ws.append([_clean_cell(v) for v in _restore_dates(header, row)])

    # 确保至少有一个可见 sheet（空文件 / 全空 sheet 时 openpyxl save 会报错）
    if not wb.worksheets:
        wb.create_sheet(title="Sheet1")

    wb.save(dst)
    return dst


# ---------------------------------------------------------------------------
# CLI — python -m xls_normalize <file.xls>  仅规范化不导入
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    import json
    from config import OUT_DIR

    parser = argparse.ArgumentParser(description="旧版 BIFF .xls 预处理规范化（不调用 RAGFlow API）")
    parser.add_argument("src", help="原始 .xls 文件路径")
    parser.add_argument("--out-dir", default=None, help="产物目录（默认 out/xls_normalized/）")
    args = parser.parse_args()

    src = Path(args.src)
    out_dir = Path(args.out_dir) if args.out_dir else OUT_DIR / "xls_normalized"

    if not is_biff_xls(src):
        print(f"[INFO] {src} 不是 BIFF .xls（可能是 .xlsx 或其他格式），无需规范化")
        return

    dst = normalize_xls(src, out_dir)
    from openpyxl import load_workbook
    wb = load_workbook(dst)
    sheets = [ws.title for ws in wb.worksheets]
    print(f"[INFO] 规范化完成: {src} → {dst}")
    print(f"[INFO] sheets: {json.dumps(sheets, ensure_ascii=False)}")


if __name__ == "__main__":
    main()