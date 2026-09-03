import sys; sys.path.insert(0, "..")
from tables import lines_to_markdown, markdown_table_doc, qa_rows
import tempfile, pathlib, shutil

def test_tab_separated():
    lines = ["站名\t降雨量mm", "柳林\t52.3", "瑶曲\t38.7"]
    md = lines_to_markdown(lines)
    assert "站名" in md and "柳林" in md and "52.3" in md

def test_space_separated():
    lines = ["站名  降雨量mm", "柳林  52.3"]
    md = lines_to_markdown(lines)
    assert "柳林" in md

def test_fenced_block():
    lines = ["这是一段普通文本", "包含数字123和456"]
    md = lines_to_markdown(lines)
    assert "```" in md

def test_markdown_table_doc():
    doc = markdown_table_doc("柳林站降雨量", ["站名","雨量"], [["柳林","52.3"]])
    assert "# 表：柳林站降雨量" in doc
    assert "| 站名 | 雨量 |" in doc

def test_qa_rows_numeric():
    headers = ["站名","降雨量mm"]
    rows = [["柳林","52.3"], ["瑶曲","38.7"]]
    qas = qa_rows("降雨量统计", headers, rows)
    assert len(qas) >= 1
    q, a = qas[0]
    assert "柳林" in a or "52.3" in a


# ---------------------------------------------------------------------------
# P2 表头修复 + CLI 入口
# ---------------------------------------------------------------------------

import json
import sys
sys.path.insert(0, "..")

import pytest

import tables as tables_module


def test_lines_to_markdown_preserves_text_header_and_data():
    """数值占优的数据行不得抢占表头、真表头与其前内容不得丢失（评审 I7）。"""
    lines = ["站名\t雨量\t水位", "柳林\t52.3\t788.5"]
    md = lines_to_markdown(lines)
    assert "| 站名 | 雨量 | 水位 |" in md, f"真表头丢失：\n{md}"
    assert "| 柳林 | 52.3 | 788.5 |" in md, f"数据行丢失：\n{md}"


def test_lines_to_markdown_numeric_first_table_keeps_all_rows():
    """无文本表头（纯数值表）时合成列名，所有数据行保留。"""
    lines = ["2020\t2021", "52.3\t38.7"]
    md = lines_to_markdown(lines)
    assert "| 52.3 | 38.7 |" in md
    assert "2020" in md and "2021" in md


def test_tables_main_generates_outputs(tmp_path, monkeypatch):
    """python -m tables 必须读取 mapping.csv 并产出 generated_tables/。"""
    from config import IMPORT_COLS

    derived = tmp_path / "derived"
    src = derived / "excel_extracted" / "06-历年洪水资料_08-历年洪水统计_降雨量统计.txt"
    src.parent.mkdir(parents=True)
    src.write_text("站名\t降雨量mm\n柳林\t52.3\n瑶曲\t38.7\n", encoding="utf-8")

    cols = [""] * len(IMPORT_COLS)
    cols[IMPORT_COLS.index("rel")] = str(src.relative_to(derived))
    cols[IMPORT_COLS.index("dataset_key")] = "ds3"
    cols[IMPORT_COLS.index("doc_type")] = "文本"
    row = ",".join(cols)
    # 含逗号字段不存在，可直接 join；引号安全起见用 csv 模块写
    import csv as _csv
    mapping = tmp_path / "out" / "mapping.csv"
    mapping.parent.mkdir(parents=True)
    with open(mapping, "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(IMPORT_COLS)
        w.writerow(cols)

    monkeypatch.setattr(tables_module, "DERIVED_ROOT", derived)
    monkeypatch.setattr(tables_module, "OUT_DIR", tmp_path / "out")

    tables_module.main()

    gen = tmp_path / "out" / "generated_tables"
    md_files = list(gen.glob("*.md"))
    qa_files = list(gen.glob("*.qa.md"))
    assert any("降雨量统计" in p.name for p in md_files), f"未生成表格 markdown：{list(gen.iterdir())}"
    assert any("降雨量统计" in p.name for p in qa_files), "未生成 Q/A 行文件"
