"""tests/test_run_import.py — Task 8"""
import sys; sys.path.insert(0, "..")
import csv
import json
import pathlib

import pytest

import run_import as run_import_module
from config import IMPORT_COLS
from run_import import row_to_meta_fields, DocImportState, ImportStateMachine, run_import
from unittest.mock import MagicMock, patch

def test_row_to_meta_fields_year_string():
    row = {"rel":"test.txt","year":"2021","flood_event":"2021-10","doc_type":"文本"}
    mf = row_to_meta_fields(row)
    assert mf["year"] == "2021"  # 保持字符串（ES 类型安全：schema 注册为 string）

def test_row_to_meta_fields_omits_empty():
    row = {"rel":"test.txt","year":"","flood_event":"","doc_type":"文本"}
    mf = row_to_meta_fields(row)
    assert "year" not in mf
    assert "flood_event" not in mf
    assert "meta_fields" not in mf

def test_doc_import_state_defaults():
    s = DocImportState(rel="test.txt", status="pending", dataset_key="ds1")
    assert s.doc_id is None
    assert s.error is None

def test_state_machine_set_get():
    sm = ImportStateMachine(state_path=None)
    sm.set("test.txt", "uploaded")
    assert sm.get("test.txt") == "uploaded"
    sm.set("test.txt", "done")
    assert sm.get("test.txt") == "done"

def test_dry_run_missing_setup_state(tmp_path, capsys):
    """dry-run 且 setup_state.json 缺失 → 友好提示并返回，不抛异常、不触网。"""
    result = run_import_module.run_import(
        apply=False, dataset_key=None, limit=None,
        _out_dir=tmp_path,
    )
    assert result == {"done": 0, "failed": 0, "skipped": 0}
    assert "run_setup.py" in capsys.readouterr().out


def _write_fixture(tmp_path):
    """在临时 OUT_DIR 中构造最小 setup_state.json + mapping.csv。"""
    (tmp_path / "setup_state.json").write_text(json.dumps({
        "ds1": {"id": "ds1-id", "name": "规程与预案"},
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "mapping.csv").write_text(
        "rel,dataset_key,doc_category,sub_category,flood_event,"
        "doc_type,year,source_format,quality,responsible_dept,doc_nature,location,"
        "flood_magnitude,skip_reason,duplicate_of,sha256\n"
        'a.pdf,ds1,规程预案,,,,"2021",pdf,high,,技术,,,,"sha-a"\n',
        encoding="utf-8",
    )


@patch("run_import.RAGFlowClient")
def test_dry_run_does_not_upload(mock_client_cls, tmp_path):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    _write_fixture(tmp_path)

    with patch.object(run_import_module, "OUT_DIR", tmp_path):
        run_import_module.run_import(apply=False, dataset_key=None, limit=None)

    mock_client.upload_document.assert_not_called()
    # dry-run 不得在任何位置留下 import_state.json（真实断言，替换原恒真表达式）
    assert not (tmp_path / "import_state.json").exists()


# ---------------------------------------------------------------------------
# C1 契约：patch_document 必须收到裸 meta_fields dict（不得再包一层）
# ---------------------------------------------------------------------------

@patch("run_import.RAGFlowClient")
def test_patch_document_receives_plain_meta_fields(mock_client_cls, tmp_path):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.upload_document.return_value = {"id": "doc-1"}
    mock_client.list_documents.return_value = []  # 无同名文档 → 走上传分支
    _write_fixture(tmp_path)
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 mock")  # 真实文件使 upload 分支可达

    with patch.object(run_import_module, "OUT_DIR", tmp_path), \
         patch.object(run_import_module, "CORPUS_ROOT", tmp_path):
        result = run_import_module.run_import(apply=True, dataset_key=None, limit=None)

    assert result["done"] == 1
    args, _ = mock_client.patch_document.call_args
    meta_arg = args[2]  # patch_document(dataset_id, doc_id, meta_fields)
    assert meta_arg == {
        "doc_category": "规程预案", "year": "2021",
        "source_format": "pdf", "quality": "high", "doc_nature": "技术",
        "rel": "a.pdf",
    }, f"patch_document 收到的 meta_fields 应为裸字段 dict + rel 溯源，实际为 {meta_arg}"
    # 状态文件必须落在注入的 out 目录，而非模块全局 OUT_DIR
    assert (tmp_path / "import_state.json").exists()


# ---------------------------------------------------------------------------
# C2：skip_reason=duplicate 的行不得进入导入队列
# ---------------------------------------------------------------------------

def _write_two_rows_csv(tmp_path, extra_rows):
    header = ",".join(IMPORT_COLS)
    lines = [header] + extra_rows
    (tmp_path / "mapping.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (tmp_path / "setup_state.json").write_text(
        json.dumps({"ds1": {"id": "ds1-id", "name": "规程与预案"}}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_dry_run_skips_duplicate_rows(tmp_path, capsys):
    blank = "," * (len(IMPORT_COLS) - 1)
    dup_cols = ["b.txt"] + [""] * (len(IMPORT_COLS) - 2)
    dup_cols[IMPORT_COLS.index("skip_reason")] = "duplicate"
    _write_two_rows_csv(tmp_path, ["a.txt" + blank, ",".join(dup_cols)])

    with patch.object(run_import_module, "OUT_DIR", tmp_path):
        run_import_module.run_import(apply=False, dataset_key=None, limit=None)

    out = capsys.readouterr().out
    assert "Would import 1 file(s)" in out
    assert "b.txt" not in out


# ---------------------------------------------------------------------------
# P0-5：同名复用语义——state doc_id 优先；否则同名单一 + 字节大小精确一致才复用。
#   mapping.csv 审计发现 4 组共 11 个同名文件（洪水过程.xls ×4 等），按 basename
#   盲匹配会把元数据打到别人的文档上。宁可可见的重复，不可静默的张冠李戴。
# ---------------------------------------------------------------------------

@patch("run_import.RAGFlowClient")
def test_resume_prefers_doc_id_from_import_state(mock_client_cls, tmp_path):
    """断点续跑：import_state 已有 doc_id → 优先续用。即使库内同名同大小候选
    有两个（大小指纹失效、复用判定歧义），state doc_id 仍压倒一切地锁定目标。"""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    n = len(b"%PDF-1.4 mock")
    mock_client.list_documents.return_value = [
        {"id": "twin-a", "name": "a.pdf", "size": n},
        {"id": "state-id", "name": "a.pdf", "size": n},
    ]
    _write_fixture(tmp_path)
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 mock")
    (tmp_path / "import_state.json").write_text(json.dumps({
        "a.pdf": {"rel": "a.pdf", "status": "parse_requested",
                  "dataset_key": "ds1", "doc_id": "state-id"},
    }), encoding="utf-8")

    with patch.object(run_import_module, "OUT_DIR", tmp_path), \
         patch.object(run_import_module, "CORPUS_ROOT", tmp_path):
        result = run_import_module.run_import(apply=True, dataset_key=None, limit=None)

    mock_client.upload_document.assert_not_called()
    parse_args, _ = mock_client.parse_documents.call_args
    assert parse_args[1] == ["state-id"]
    assert result["done"] == 1


# ---------------------------------------------------------------------------
# P1-3：超时与重跑语义——wait_timeout 与 failed 区分；DONE 文档不得重发 parse；
# GraphRAG 库的等待窗口放宽。
# ---------------------------------------------------------------------------

@patch("run_import.RAGFlowClient")
def test_resume_skips_parse_when_doc_already_done(mock_client_cls, tmp_path):
    """断点续跑遇到已 DONE 的文档：刷新元数据但不重发 parse——
    v0.27.0 对已解析文档重发 parse 会先清空旧 chunks，白烧一遍 GraphRAG。"""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_documents.return_value = [
        {"id": "state-id", "name": "a.pdf", "size": len(b"%PDF-1.4 mock"),
         "run": "DONE", "progress": 1},
    ]
    _write_fixture(tmp_path)
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 mock")
    (tmp_path / "import_state.json").write_text(json.dumps({
        "a.pdf": {"rel": "a.pdf", "status": "parse_requested",
                  "dataset_key": "ds1", "doc_id": "state-id"},
    }), encoding="utf-8")

    with patch.object(run_import_module, "OUT_DIR", tmp_path), \
         patch.object(run_import_module, "CORPUS_ROOT", tmp_path):
        result = run_import_module.run_import(apply=True, dataset_key=None, limit=None)

    mock_client.parse_documents.assert_not_called()
    mock_client.wait_document.assert_not_called()
    # 元数据仍要刷新（mapping.csv 可能已修正），只是不动 parse
    mock_client.patch_document.assert_called_once()
    state_entry = json.loads((tmp_path / "import_state.json").read_text(encoding="utf-8"))["a.pdf"]
    assert state_entry["status"] == "done"
    assert result["done"] == 1


@patch("run_import.RAGFlowClient")
def test_wait_timeout_persisted_as_wait_timeout(mock_client_cls, tmp_path):
    """等待解析完成超时 ≠ 解析失败：状态必须记为 wait_timeout，便于区分处理。"""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.upload_document.return_value = {"id": "doc-1"}
    mock_client.list_documents.return_value = []
    mock_client.wait_document.side_effect = TimeoutError("did not complete within 600s")
    _write_fixture(tmp_path)
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 mock")

    with patch.object(run_import_module, "OUT_DIR", tmp_path), \
         patch.object(run_import_module, "CORPUS_ROOT", tmp_path):
        result = run_import_module.run_import(apply=True, dataset_key=None, limit=None)

    entry = json.loads((tmp_path / "import_state.json").read_text(encoding="utf-8"))["a.pdf"]
    assert entry["status"] == "wait_timeout"
    assert result["failed"] == 1


def test_resolve_wait_timeout_unified_window():
    """统一等待窗 1800s：GraphRAG 与 tag 注入均耗时，600s 曾误判正常构建为超时。

    见 run_import.resolve_wait_timeout 说明——后台串行导入时统一放宽，不再按
    GraphRAG 开关区分。
    """
    assert run_import_module.resolve_wait_timeout("ds1") == 1800   # laws + graphrag
    assert run_import_module.resolve_wait_timeout("ds3") == 1800   # naive + graphrag
    assert run_import_module.resolve_wait_timeout("ds2") == 1800   # 无 graphrag，亦放宽
    assert run_import_module.resolve_wait_timeout("ds4") == 1800
    assert run_import_module.resolve_wait_timeout("不存在的库") == 1800


@patch("run_import.RAGFlowClient")
def test_wait_document_receives_dataset_specific_timeout(mock_client_cls, tmp_path):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.upload_document.return_value = {"id": "doc-1"}
    mock_client.list_documents.return_value = []
    _write_fixture(tmp_path)   # mapping 行 dataset_key=ds1（graphrag 库）
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 mock")

    with patch.object(run_import_module, "OUT_DIR", tmp_path), \
         patch.object(run_import_module, "CORPUS_ROOT", tmp_path):
        run_import_module.run_import(apply=True, dataset_key=None, limit=None)

    kwargs = mock_client.wait_document.call_args.kwargs
    assert kwargs.get("timeout") == run_import_module.resolve_wait_timeout("ds1")


@patch("run_import.RAGFlowClient")
def test_same_name_reuse_requires_exact_size_match(mock_client_cls, tmp_path):
    """同名候选必须字节大小精确一致才复用；多个候选中只有真正匹配的那个生效。"""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    # 首位是大小不符的同名诱饵——旧实现会盲选它（basename 匹配），正是缺陷所在
    mock_client.list_documents.return_value = [
        {"id": "wrong-size", "name": "a.pdf", "size": 999999},
        {"id": "right-size", "name": "a.pdf", "size": len(b"%PDF-1.4 mock")},
    ]
    _write_fixture(tmp_path)
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 mock")

    with patch.object(run_import_module, "OUT_DIR", tmp_path), \
         patch.object(run_import_module, "CORPUS_ROOT", tmp_path):
        run_import_module.run_import(apply=True, dataset_key=None, limit=None)

    mock_client.upload_document.assert_not_called()
    parse_args, _ = mock_client.parse_documents.call_args
    assert parse_args[1] == ["right-size"]


@patch("run_import.RAGFlowClient")
def test_same_name_without_size_match_uploads_new(mock_client_cls, tmp_path):
    """同名但大小对不上 → 不冒认，上传新文档（重复可见优于元数据被污染）。"""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_documents.return_value = [{"id": "stranger", "name": "a.pdf", "size": 4096}]
    mock_client.upload_document.return_value = {"id": "fresh"}
    _write_fixture(tmp_path)
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 mock")

    with patch.object(run_import_module, "OUT_DIR", tmp_path), \
         patch.object(run_import_module, "CORPUS_ROOT", tmp_path):
        result = run_import_module.run_import(apply=True, dataset_key=None, limit=None)

    mock_client.upload_document.assert_called_once()
    parse_args, _ = mock_client.parse_documents.call_args
    assert parse_args[1] == ["fresh"]
    assert result["done"] == 1


@patch("run_import.RAGFlowClient")
def test_ambiguous_size_matches_upload_new(mock_client_cls, tmp_path):
    """多个同大小同名候选（无法分辨）→ 一律上传新文档，绝不猜。"""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    n = len(b"%PDF-1.4 mock")
    mock_client.list_documents.return_value = [
        {"id": "twin-a", "name": "a.pdf", "size": n},
        {"id": "twin-b", "name": "a.pdf", "size": n},
    ]
    mock_client.upload_document.return_value = {"id": "fresh"}
    _write_fixture(tmp_path)
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 mock")

    with patch.object(run_import_module, "OUT_DIR", tmp_path), \
         patch.object(run_import_module, "CORPUS_ROOT", tmp_path):
        result = run_import_module.run_import(apply=True, dataset_key=None, limit=None)

    mock_client.upload_document.assert_called_once()
    parse_args, _ = mock_client.parse_documents.call_args
    assert parse_args[1] == ["fresh"]
    assert result["done"] == 1


@patch("run_import.RAGFlowClient")
def test_upload_resolves_corpus_root_relative_path(mock_client_cls, tmp_path):
    """rel 必须解析为 CORPUS_ROOT 下的原件路径（语料源切换后的核心契约）。"""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.upload_document.return_value = {"id": "doc-1"}
    mock_client.list_documents.return_value = []
    _write_fixture(tmp_path)
    real_dir = tmp_path / "01-核心文档-四案"
    real_dir.mkdir()
    (real_dir / "a.pdf").write_bytes(b"%PDF-1.4 mock")
    # 把 mapping 的 rel 改成带目录的真实相对路径
    rows = (tmp_path / "mapping.csv").read_text(encoding="utf-8").splitlines()
    rows[1] = rows[1].replace("a.pdf,", "01-核心文档-四案/a.pdf,", 1)
    (tmp_path / "mapping.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    with patch.object(run_import_module, "OUT_DIR", tmp_path), \
         patch.object(run_import_module, "CORPUS_ROOT", tmp_path):
        result = run_import_module.run_import(apply=True, dataset_key=None, limit=None)

    assert result["done"] == 1, f"原件路径必须可导入：{result}"
    upload_args, _ = mock_client.upload_document.call_args
    assert upload_args[1] == tmp_path / "01-核心文档-四案" / "a.pdf"


# ---------------------------------------------------------------------------
# P3：凭据占位值 fail-fast 的友好退出 + 状态机持久化 round-trip（回归锁定）
# ---------------------------------------------------------------------------

@patch("run_import.RAGFlowClient")
def test_placeholder_credentials_friendly_exit(mock_client_cls, tmp_path, capsys):
    mock_client_cls.side_effect = ValueError("RAGFLOW_EMAIL 未正确设置")
    _write_fixture(tmp_path)
    with patch.object(run_import_module, "OUT_DIR", tmp_path), \
         pytest.raises(SystemExit):
        run_import_module.run_import(apply=True, dataset_key=None, limit=None)
    assert "RAGFLOW_EMAIL" in capsys.readouterr().out


def test_state_machine_roundtrip(tmp_path):
    """save → 重载 → 状态保留（断点续命核心路径）。"""
    state_path = tmp_path / "import_state.json"
    sm = ImportStateMachine(state_path=state_path)
    sm.set("a.txt", "uploaded", dataset_key="ds1", doc_id="d-1")
    sm.set("b.txt", "done")
    sm.save()

    reloaded = ImportStateMachine(state_path=state_path)
    assert reloaded.get("a.txt") == "uploaded"
    entry = dict(reloaded.items())["a.txt"]
    assert entry["doc_id"] == "d-1"
    assert reloaded.get("b.txt") == "done"


def test_state_machine_clears_stale_error_on_recovery(tmp_path):
    """失败后重跑成功时，旧的 error 字段必须清掉——否则 done 条目仍挂着
    上一次的报错文本，验收报告会误读。"""
    state_path = tmp_path / "import_state.json"
    sm = ImportStateMachine(state_path=state_path)
    sm.set("a.txt", "failed", error="RAGFlow 返回 code=102: boom")
    sm.set("a.txt", "done")
    entry = dict(sm.items())["a.txt"]
    assert entry["status"] == "done"
    assert "error" not in entry, f"陈旧 error 未清除: {entry}"
