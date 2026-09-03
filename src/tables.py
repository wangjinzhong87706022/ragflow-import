"""tables.py — 表格 Markdown 生成与 Q/A 行抽取 (spec §6 阶段1 Task 3)."""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from config import OUT_DIR, DERIVED_ROOT, QA_TABLE_KEYWORDS
from corpus import Entry, read_mapping_csv


# ---------------------------------------------------------------------------
# lines_to_markdown
# ---------------------------------------------------------------------------

def _is_numeric_cell(cell: str) -> bool:
    cell = cell.strip()
    if not cell:
        return False
    # Remove leading +/- sign
    if cell[0] in ("+", "-"):
        cell = cell[1:]
    return bool(re.match(r"^\d+(\.\d+)?$", cell))


def lines_to_markdown(lines: list[str]) -> str:
    """
    Convert a list of raw text lines to a markdown string.

    Rules:
    1. Split each line on \\t; if 2+ cols → Markdown table row
    2. Else split on 2+ consecutive spaces; if 2+ cols → Markdown table row
    3. Else → verbatim line in a fenced code block
    4. Header detection（评审 I7 修复）: 数据起始行 = 首个数值占优行；
       其紧邻前一行（文本占优）作为表头；首行即数值占优 → 合成"列N"表头；
       表头之前的残余行保留进 fenced block——任何行都不得丢弃。
    5. Strip trailing whitespace from each cell
    """
    tab_rows: list[list[str]] = []
    space_rows: list[list[str]] = []
    non_table_lines: list[str] = []

    for line in lines:
        tab_parts = line.split("\t")
        if len(tab_parts) >= 2:
            tab_rows.append([c.strip() for c in tab_parts])
            continue

        # Try 2+ consecutive spaces
        space_parts = re.split(r"  +", line)
        if len(space_parts) >= 2:
            space_rows.append([c.strip() for c in space_parts])
            continue

        non_table_lines.append(line)

    # Choose the richer table format (prefer tab, fall back to space)
    table_rows = tab_rows if len(tab_rows) >= len(space_rows) else space_rows

    if not table_rows:
        # All non-table lines → fenced code block
        code_content = "\n".join(non_table_lines)
        return f"```\n{code_content}\n```\n"

    def _numeric_majority(row: list[str]) -> bool:
        numeric_count = sum(1 for cell in row if _is_numeric_cell(cell))
        return numeric_count * 2 > len(row)

    data_start = next(
        (i for i, row in enumerate(table_rows) if _numeric_majority(row)), None
    )

    leftover: list[list[str]] = []
    if data_start is None:
        # 纯文本表：首行作表头
        headers, data_rows = table_rows[0], table_rows[1:]
    elif data_start == 0:
        # 首行即数据（如年份列表头缺失）：合成列名，保留全部数据行
        headers = [f"列{i + 1}" for i in range(len(table_rows[0]))]
        data_rows = table_rows
    else:
        headers = table_rows[data_start - 1]
        data_rows = table_rows[data_start:]
        leftover = table_rows[: data_start - 1]

    non_table_out = ["\t".join(r) for r in leftover] + non_table_lines

    # Build markdown
    lines_out: list[str] = []
    lines_out.append(f"| {' | '.join(headers)} |")
    lines_out.append(f"| {' | '.join(['---'] * len(headers))} |")
    for row in data_rows:
        lines_out.append(f"| {' | '.join(row)} |")

    if non_table_out:
        lines_out.append("")
        lines_out.append("```")
        lines_out.extend(non_table_out)
        lines_out.append("```")

    return "\n".join(lines_out) + "\n"


# ---------------------------------------------------------------------------
# markdown_table_doc
# ---------------------------------------------------------------------------

def markdown_table_doc(title: str, headers: list[str], rows: list[list[str]]) -> str:
    """
    Build a complete markdown document for a table.

    Format:
    # 表：{title}

    | {headers} |
    | --- |
    | {rows...} |
    """
    lines_out = [f"# 表：{title}", ""]
    lines_out.append(f"| {' | '.join(headers)} |")
    lines_out.append(f"| {' | '.join(['---'] * len(headers))} |")
    for row in rows:
        lines_out.append(f"| {' | '.join(row)} |")
    return "\n".join(lines_out) + "\n"


# ---------------------------------------------------------------------------
# qa_rows
# ---------------------------------------------------------------------------

def qa_rows(title: str, headers: list[str], rows: list[list[str]]) -> list[tuple[str, str]]:
    """
    Generate Q/A pairs for numeric rows.

    For each row where >50% of cells are numeric, generate a question
    asking about the column values, with the full row as the answer.
    """
    qas: list[tuple[str, str]] = []
    for row in rows:
        numeric_count = sum(1 for cell in row if _is_numeric_cell(cell))
        if numeric_count / len(row) < 0.5:
            continue

        # Build meaningful question using headers
        if headers:
            kv_parts = [f"{h}={v}" for h, v in zip(headers, row)]
            answer = " | ".join(kv_parts)
            col_indices = [str(i) for i, cell in enumerate(row) if _is_numeric_cell(cell)]
            if col_indices:
                question = f"{title}中，第{col_indices[0]}列的数值是多少？"
            else:
                question = f"{title}的相关数据是多少？"
        else:
            answer = " | ".join(row)
            question = f"{title}的相关数据是多少？"

        qas.append((question, answer))

    return qas


# ---------------------------------------------------------------------------
# generate_all
# ---------------------------------------------------------------------------

def generate_all(entries: list[Entry], out_dir: Path) -> None:
    """
    Process table entries and generate markdown + Q/A files.

    Filter entries where:
      - native_xlsx_path is None
      - doc_type == "文本"
      - any QA_TABLE_KEYWORD in rel

    For each filtered entry:
      - Read lines from DERIVED_ROOT / rel
      - Convert to markdown via lines_to_markdown
      - Save to out_dir/generated_tables/{stem}.md
      - If QA keyword in rel: also save .qa.md

    For entries with native_xlsx_path not None:
      - Copy xlsx to out_dir/generated_tables/{stem}.xlsx

    Finally create out_dir/generated_tables/README.md listing all outputs.
    """
    tables_dir = out_dir / "generated_tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    outputs: list[str] = []

    for entry in entries:
        stem = Path(entry.rel).stem

        # 语料源切换后仅遗留 txt 行可生成 Markdown（原件 pdf/doc/xls 直接导入，无需补偿）
        if not entry.rel.lower().endswith(".txt"):
            continue

        # Filter: doc_type must be "文本" and QA keyword must be in rel
        if entry.doc_type != "文本":
            continue
        if not any(kw in entry.rel for kw in QA_TABLE_KEYWORDS):
            continue

        src_path = DERIVED_ROOT / entry.rel
        if not src_path.exists():
            continue

        lines = src_path.read_text(encoding="utf-8").splitlines()
        md = lines_to_markdown(lines)

        md_path = tables_dir / f"{stem}.md"
        md_path.write_text(md, encoding="utf-8")
        outputs.append(f"{stem}.md")

        # Also generate Q/A if QA keyword present
        if any(kw in entry.rel for kw in QA_TABLE_KEYWORDS):
            # We need headers and rows to call qa_rows — fall back to a generic approach
            # Parse tab-separated or space-separated rows for qa_rows
            tab_rows: list[list[str]] = []
            for line in lines:
                parts = line.split("\t")
                if len(parts) >= 2:
                    tab_rows.append([c.strip() for c in parts])
                    continue
                parts2 = re.split(r"  +", line)
                if len(parts2) >= 2:
                    tab_rows.append([c.strip() for c in parts2])

            if tab_rows:
                header_idx = None
                for i, row in enumerate(tab_rows):
                    numeric_count = sum(1 for cell in row if _is_numeric_cell(cell))
                    text_count = len(row) - numeric_count
                    if numeric_count > text_count:
                        header_idx = i
                        break
                headers = tab_rows[header_idx] if header_idx is not None else []
                data_rows = tab_rows[header_idx + 1:] if header_idx is not None else tab_rows
                qas = qa_rows(stem, headers, data_rows)
                if qas:
                    qa_lines = [f"Q: {q}\nA: {a}" for q, a in qas]
                    qa_doc = "\n\n".join(qa_lines) + "\n"
                    qa_path = tables_dir / f"{stem}.qa.md"
                    qa_path.write_text(qa_doc, encoding="utf-8")
                    outputs.append(f"{stem}.qa.md")

    # Write README
    readme_path = tables_dir / "README.md"
    readme_lines = ["# Generated Tables", "", f"Total outputs: {len(outputs)}", ""]
    for o in sorted(outputs):
        readme_lines.append(f"- {o}")
    readme_path.write_text("\n".join(readme_lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI — python -m tables（阶段1 入口：读 mapping.csv，产出 generated_tables/）
# ---------------------------------------------------------------------------

def _entry_from_row(row: dict) -> Entry:
    """mapping.csv 行 → Entry（空串归一为 None）。"""
    return Entry(
        rel=row.get("rel", ""),
        dataset_key=row.get("dataset_key", ""),
        doc_category=row.get("doc_category", ""),
        sub_category=row.get("sub_category", ""),
        flood_event=row.get("flood_event") or None,
        doc_type=row.get("doc_type", ""),
        year=int(row["year"]) if row.get("year") else None,
        source_format=row.get("source_format", ""),
        quality=row.get("quality", ""),
        responsible_dept=row.get("responsible_dept") or None,
        doc_nature=row.get("doc_nature", ""),
        location=row.get("location") or None,
        flood_magnitude=row.get("flood_magnitude", ""),
        skip_reason=row.get("skip_reason") or None,
        duplicate_of=row.get("duplicate_of") or None,
        sha256=row.get("sha256", ""),
    )


def main() -> None:
    mapping_path = OUT_DIR / "mapping.csv"
    if not mapping_path.exists():
        print(f"[提示] {mapping_path} 不存在——请先运行 `python -m corpus` 生成映射表。")
        sys.exit(1)

    entries = [_entry_from_row(r) for r in read_mapping_csv(mapping_path)]
    generate_all(entries, OUT_DIR)

    gen_dir = OUT_DIR / "generated_tables"
    n = len([p for p in gen_dir.iterdir() if p.name != "README.md"])
    print(f"[INFO] 已生成 {n} 个产物于 {gen_dir}")
    print("[说明] 当前版本 generated_tables/*.md、*.qa.md 与 xlsx 副本不自动入库，需人工经 Web UI 上传（见 src/README.md 阶段1）")


if __name__ == "__main__":
    main()
