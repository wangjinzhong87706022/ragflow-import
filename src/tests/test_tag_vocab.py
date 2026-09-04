import sys
sys.path.insert(0, "..")
from tag_vocab import write_vocab_txt, parse_vocab_txt, VOCAB_ROWS
import tempfile
import pathlib

def test_vocab_rows_count():
    assert len(VOCAB_ROWS) == 17  # 5 knowledge-type + 12 flood-event（2018-8 系 2008 目录名纠偏新增；2010-7/2010-8 系 F1 批次二按内容归场新增；2011-7 系批次三：洪水过程(3).xls 实为 20110729）

def test_write_and_parse_roundtrip():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        tmp = pathlib.Path(f.name)
    write_vocab_txt(tmp)
    rows = parse_vocab_txt(tmp)
    assert len(rows) == 17
    assert rows[0][0] == "规程预案"
    assert rows[0][1] == "规程预案"
    import os
    os.unlink(f.name)

def test_knowledge_type_rows():
    knowledge_rows = [r for r in VOCAB_ROWS if r[1] in {"规程预案", "基础数据", "洪水资料", "组织管理", "工程资料"}]
    assert len(knowledge_rows) == 5  # 5 knowledge-type enums

def test_flood_event_rows():
    """事件名与 FLOOD_EVENT_BY_SUBDIR / METADATA_SCHEMA 枚举一致（2019-9 取代 2019-7，
    增补 2021-09；2018-8 系 2008 目录名纠偏新增，2008-8 保留为历史值域；
    2010-7/2010-8 系 F1 批次二按内容归场新增；2011-7 系批次三按内容归场新增）。"""
    flood_tags = {r[1] for r in VOCAB_ROWS if r[1].startswith("20") or r[1] in {"其他", "历年统计"}}
    flood_rows = [r for r in VOCAB_ROWS if r[1] in flood_tags and r[1] not in {"规程预案","基础数据","组织管理","工程资料"}]
    flood_names = {r[1] for r in flood_rows}
    assert len(flood_names) == 12
    assert "2021-10" in flood_names
    assert "2021-09" in flood_names
    assert "2019-9" in flood_names
    assert "2018-8" in flood_names
    assert "2010-7" in flood_names
    assert "2010-8" in flood_names
    assert "2011-7" in flood_names
    assert "2019-7" not in flood_names
