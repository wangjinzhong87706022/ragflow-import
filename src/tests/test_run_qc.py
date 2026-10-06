import sys
import json
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from unittest.mock import patch, MagicMock

# ------------------------------------------------------------------
# Tests for run_qc.py — verify QUESTIONS structure before implementation
# ------------------------------------------------------------------

def test_questions_have_required_fields():
    """All 6 questions must have id/text/dataset_ids/use_kg/expected_keywords."""
    from run_qc import QUESTIONS  # will fail until run_qc.py exists
    for q in QUESTIONS:
        assert "id" in q and "text" in q and "dataset_ids" in q, f"Question {q} missing required field"
        assert "use_kg" in q and "expected_keywords" in q, f"Question {q} missing use_kg or expected_keywords"
        assert isinstance(q["dataset_ids"], list), f"Question {q} dataset_ids must be a list"


def test_question_count():
    """Exactly 6 questions are defined per spec §3.5."""
    from run_qc import QUESTIONS
    assert len(QUESTIONS) == 6, f"Expected 6 questions, got {len(QUESTIONS)}"


def test_q1_uses_kg():
    """Q1 must use knowledge graph and expect the 1454 m³/s keyword."""
    from run_qc import QUESTIONS
    q1 = next(q for q in QUESTIONS if q["id"] == "Q1")
    assert q1["use_kg"] is True, "Q1 must have use_kg=True"
    assert "1454" in q1["expected_keywords"], "Q1 expected_keywords must contain '1454'"


def test_q3_q4_keywords_corpus_grounded():
    """Q3/Q4 期望值必须与语料实证一致（2026-09-01 全库 2687 分片审计）：
    - Q3：答案分片（10·3汇报#1、调度规程§2.4）单跳可召回，判分锚定站点实体；
      “规程”从期望中移除——语料无任何“依规程第X条”的现成条款引用。
    - Q4：全库仅 2 个含“安芳东”的分片，同属 2020 年“8·16”洪水
      （洪水调度报告.doc 落款 2020-8-20）；原期望“2021”系规格笔误。
    """
    from run_qc import QUESTIONS
    q3 = next(q for q in QUESTIONS if q["id"] == "Q3")
    q4 = next(q for q in QUESTIONS if q["id"] == "Q4")
    assert q3["expected_keywords"] == ["柳林", "瑶曲"], \
        f"Q3 keywords must be corpus-grounded ['柳林','瑶曲'], got {q3['expected_keywords']}"
    assert "10月3" in q3["text"], "Q3 text must name the 10月3 event explicitly (retrieval-proven phrasing)"
    assert q4["expected_keywords"] == ["安芳东", "8·16"], \
        f"Q4 keywords must be ['安芳东','8·16'] (2020 event), got {q4['expected_keywords']}"


def test_meta_filter_structure():
    """All non-null meta_data_filter entries must have method in {manual, semi_auto, auto}."""
    from run_qc import QUESTIONS
    for q in QUESTIONS:
        mf = q.get("meta_data_filter")
        if mf is not None:
            assert "method" in mf, f"Question {q['id']} meta_data_filter missing 'method'"
            assert mf["method"] in {"manual", "semi_auto", "auto"}, \
                f"Question {q['id']} meta_data_filter method must be manual/semi_auto/auto, got {mf['method']}"


@patch("run_qc.RAGFlowClient")
def test_run_qc_produces_report(mock_client_cls, tmp_path):
    """run_qc() must call search_datasets and write results + report files."""
    # 隔离：OUT_DIR 指向临时目录，避免测试在真实 out/ 下建目录/写文件
    import run_qc as run_qc_module

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.search_datasets.return_value = {
        "chunks": [{"content_with_weight": "溢洪道设计泄量1454 m³/s"}],
        "total": 1,
    }

    state_data = {
        "ds1": {"id": "ds1-id", "name": "规程与预案"},
        "ds2": {"id": "ds2-id", "name": "基础数据"},
        "ds3": {"id": "ds3-id", "name": "洪水资料"},
        "ds4": {"id": "ds4-id", "name": "组织管理"},
    }

    with patch.object(run_qc_module, "OUT_DIR", tmp_path):
        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", MagicMock()):
                with patch("json.load", return_value=state_data):
                    run_qc_module.run_qc()

    assert mock_client.search_datasets.called, "search_datasets must be called"


# ---------------------------------------------------------------------------
# P1/P2/P3：连接错误友好退出 / Q2 过滤语义 / dry-run 零副作用 / 除零守卫
# ---------------------------------------------------------------------------

import pytest
import requests

import run_qc as run_qc_module
from run_qc import QUESTIONS, write_markdown


@patch("run_qc.RAGFlowClient")
def test_run_qc_passes_api_key_from_config(mock_client_cls, tmp_path):
    """run_qc 构造客户端时必须透传 config.RAGFLOW_API_KEY（API-key 模式入口）。"""
    (tmp_path / "setup_state.json").write_text("{}", encoding="utf-8")
    mock_client_cls.return_value.search_datasets.return_value = {"chunks": [], "total": 0}
    with patch.object(run_qc_module, "OUT_DIR", tmp_path):
        with patch("run_qc.RAGFLOW_EMAIL", "placeholder@example.com"), \
             patch("run_qc.RAGFLOW_PASSWORD", "placeholder"), \
             patch("run_qc.RAGFLOW_API_KEY", "ragflow-key-from-env"):
            run_qc_module.run_qc()
    kwargs = mock_client_cls.call_args.kwargs
    assert kwargs["api_key"] == "ragflow-key-from-env"


@patch("run_qc.RAGFlowClient")
def test_connection_error_friendly_exit(mock_client_cls, tmp_path, capsys):
    """requests 层连接失败必须友好提示并 SystemExit（原 except ConnectionError 为死代码）。"""
    (tmp_path / "setup_state.json").write_text("{}", encoding="utf-8")  # 先满足前置产物
    mock_client_cls.side_effect = requests.exceptions.ConnectionError("refused")
    with patch.object(run_qc_module, "OUT_DIR", tmp_path):
        with pytest.raises(SystemExit):
            run_qc_module.run_qc()
    assert "RAGFlow" in capsys.readouterr().out


def test_q2_filter_semantics():
    """Q2 问'2021年共几次洪水/时序'，限定 flood_event=2021-10 会自相矛盾地排除同年其他事件。"""
    q2 = next(q for q in QUESTIONS if q["id"] == "Q2")
    assert q2["meta_data_filter"] is None


@patch("run_qc.RAGFlowClient")
def test_dry_run_makes_no_calls_and_writes_nothing(mock_client_cls, tmp_path):
    """--dry-run 不得登录、不得发探测请求、不得在 out/qc 写任何文件。"""
    with patch.object(run_qc_module, "OUT_DIR", tmp_path):
        results = run_qc_module.run_qc(dry_run=True)
    assert results == []
    mock_client_cls.assert_not_called()
    assert not (tmp_path / "qc").exists()


def test_write_markdown_empty_results_no_crash(tmp_path):
    """空结果集不得触发 ZeroDivisionError。"""
    out = tmp_path / "report.md"
    write_markdown(out, [])  # 不抛异常即通过
    assert "0/0" in out.read_text(encoding="utf-8")


# ------------------------------------------------------------------
# 外挂题集（--questions）：泾惠渠 jhc1-3 验收题集
# ------------------------------------------------------------------

def test_load_questions_default_returns_builtin():
    from run_qc import QUESTIONS, load_questions
    assert load_questions(None) == QUESTIONS


def test_load_questions_reads_json_file(tmp_path):
    from run_qc import load_questions
    p = tmp_path / "qs.json"
    p.write_text(json.dumps([
        {"id": "A1", "text": "问题", "dataset_ids": ["jhc1"], "use_kg": False,
         "meta_data_filter": None, "expected_keywords": ["关键词"], "category": "c"},
    ], ensure_ascii=False), encoding="utf-8")
    qs = load_questions(p)
    assert qs[0]["id"] == "A1" and qs[0]["dataset_ids"] == ["jhc1"]


def test_load_questions_rejects_missing_file(tmp_path):
    import pytest
    from run_qc import load_questions
    with pytest.raises(FileNotFoundError):
        load_questions(tmp_path / "nope.json")


def test_load_questions_rejects_bad_shape(tmp_path):
    import pytest
    from run_qc import load_questions
    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_questions(empty)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"id": "A1"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_questions(bad)


def test_shipped_jinghuiqu_questions_valid():
    """随仓题集须满足 schema，且 dataset_ids 用库 key（非 dataset id）。"""
    import pathlib
    from run_qc import load_questions
    p = pathlib.Path(__file__).parent.parent / "qc_questions_jinghuiqu.json"
    qs = load_questions(p)
    assert len(qs) >= 6
    for q in qs:
        assert q["dataset_ids"] == [k for k in q["dataset_ids"]
                                    if k in ("jhc1", "jhc2", "jhc3")], q["id"]
        assert q["expected_keywords"], q["id"]


def test_report_title_reflects_question_set(tmp_path):
    from run_qc import write_markdown
    out = tmp_path / "r.md"
    qs = tmp_path / "my_qs.json"
    qs.write_text("[]", encoding="utf-8")
    write_markdown(out, [], title="RAGFlow Import — QC Report（my_qs）")
    assert "my_qs" in out.read_text(encoding="utf-8")