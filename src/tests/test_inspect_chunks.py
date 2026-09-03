"""tests/test_inspect_chunks.py — chunk 审查工具（纯函数 + CLI 装配）"""
import sys
sys.path.insert(0, "..")

import json

import pytest
from unittest.mock import MagicMock, patch

import inspect_chunks as ic


# --- 纯函数：长度统计 ---

def test_length_stats_basic():
    s = ic.length_stats([10, 20, 30])
    assert s == {"count": 3, "avg": 20.0, "min": 10, "max": 30}


def test_length_stats_empty():
    assert ic.length_stats([])["count"] == 0


# --- 纯函数：单块异常标记 ---

def test_flag_chunk_normal_text_is_none():
    long_text = "根据入库流量变化，桃曲坡水库计划调整泄量，百年一遇泄量为1454。" * 2
    assert ic.flag_chunk(long_text) is None


def test_flag_chunk_empty():
    assert ic.flag_chunk("") == "empty"
    assert ic.flag_chunk("   \n ") == "empty"


def test_flag_chunk_fragment():
    assert ic.flag_chunk("流量上涨") == "fragment"  # 4 字符 < 默认阈值50


def test_flag_chunk_noise():
    # 有效字符（CJK/字母数字）占比 < 30%
    assert ic.flag_chunk("。。。！！！、、、、————") == "noise"


# --- 纯函数：重复内容检测 ---

def test_find_duplicates_keeps_first():
    chunks = [
        {"id": "a", "content": "相同内容"},
        {"id": "b", "content": "相同内容"},
        {"id": "c", "content": "不同"},
        {"id": "d", "content": "相同内容"},
    ]
    assert ic.find_duplicates(chunks) == {"b", "d"}


# --- 报告渲染 ---

def test_render_report_contains_checklist_and_flags():
    doc_report = {
        "name": "示例.txt",
        "id": "d1",
        "run": "3",
        "chunks": [{"id": "x", "content": "短"}],
        "flags": [{"id": "x", "tag": "fragment", "content": "短"}],
        "previews": ["预览文本……"],
        "stats": {"count": 1, "avg": 1.0, "min": 1, "max": 1},
    }
    md = ic.render_report([doc_report], short_threshold=50, preview_k=3)
    assert "# Chunk 审查报告" in md
    assert "| 示例.txt |" in md          # 总览表含文档名
    assert "语义自包含" in md            # 六条判据清单在报告中
    assert "fragment" in md             # 异常标记可见
    assert "调大 chunk_token_num" in md  # 症状→旋钮提示


def test_render_report_accepts_rest_run_string():
    """REST 层 run 是字符串名：run="DONE" 的文档必须照常出明细，不得误报未完成（P0-3 家族）。"""
    doc_report = {
        "name": "示例.txt", "id": "d1", "run": "DONE",
        "chunks": [], "flags": [], "previews": [],
        "stats": {"count": 0, "avg": 0.0, "min": 0, "max": 0},
    }
    md = ic.render_report([doc_report], short_threshold=50, preview_k=3)
    assert "可能尚未解析完成" not in md
    assert "无自动异常标记" in md


# --- CLI 装配 ---

@patch("inspect_chunks.RAGFlowClient")
def test_main_writes_report(mock_client_cls, tmp_path, monkeypatch):
    monkeypatch.setattr(ic, "OUT_DIR", tmp_path)
    (tmp_path / "setup_state.json").write_text(
        json.dumps({"ds1": {"id": "ds1-real", "name": "规程与预案"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_documents.return_value = [
        {"id": "d1", "name": "示例.txt", "run": "3"},
        {"id": "d2", "name": "未解析.txt", "run": "0"},
    ]
    mock_client.list_chunks.return_value = [
        {"id": "c1", "content": "正常长度的切片内容，包含汛限水位788.5m等参数。" * 3, "keywords": []},
        {"id": "c2", "content": "碎片", "keywords": []},
        {"id": "c3", "content": "重复内容块" , "keywords": []},
        {"id": "c4", "content": "重复内容块", "keywords": []},
    ]

    rc = ic.main(["--dataset", "ds1", "--limit", "1"])

    reports = list((tmp_path / "qc").glob("chunks_ds1_*.md"))
    assert len(reports) == 1, "必须产出 chunk 审查报告"
    text = reports[0].read_text(encoding="utf-8")
    assert "示例.txt" in text and "未解析.txt" not in text  # limit=1 只处理第一个
    assert rc["docs"] == 1
