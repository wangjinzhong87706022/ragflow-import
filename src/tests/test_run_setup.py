"""Tests for run_setup: idempotent dataset creation + tag KB upload."""
import sys
sys.path.insert(0, "..")

import json
import pathlib
from unittest.mock import patch, MagicMock

import pytest

import run_setup as run_setup_module


def _isolate_out(tmp_path):
    """把 run_setup 的 OUT_DIR 指向临时目录，避免测试产物污染真实 out/。"""
    return patch.object(run_setup_module, "OUT_DIR", tmp_path)


@patch("run_setup.RAGFlowClient")
def test_idempotent_lookup_existing(mock_client_cls, tmp_path):
    """ds1 already exists → reuse its id, don't call create_dataset."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    # Simulate ds1 ("规程与预案") already exists
    mock_client.list_datasets.return_value = [
        {"id": "abc123", "name": "规程与预案"},
    ]
    mock_client.create_dataset.return_value = {"id": "new"}
    mock_client.update_dataset.return_value = {}
    mock_client.put_metadata_config.return_value = {}
    mock_client.upload_tag_vocab.return_value = {}
    mock_client.wait_parse.return_value = {}

    with _isolate_out(tmp_path), patch("run_setup.write_vocab_txt"):
        state = run_setup_module.run_setup(dry_run=True)

    # ds1 must reuse abc123, not create a new id
    ds1_entry = state.get("ds1", {})
    assert ds1_entry.get("id") == "abc123", (
        f"Expected ds1 to reuse id 'abc123' but got {ds1_entry.get('id')}"
    )


@patch("run_setup.RAGFlowClient")
def test_dry_run_does_not_create(mock_client_cls, tmp_path):
    """dry_run=True must not call create_dataset."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_datasets.return_value = []
    mock_client.create_dataset.return_value = {"id": "new"}

    with _isolate_out(tmp_path), patch("run_setup.write_vocab_txt"):
        run_setup_module.run_setup(dry_run=True)

    mock_client.create_dataset.assert_not_called()


def test_setup_state_schema():
    """If out/setup_state.json exists, validate its shape."""
    state_path = pathlib.Path(__file__).resolve().parent.parent / "out" / "setup_state.json"
    if not state_path.exists():
        pytest.skip("setup_state.json not written yet")

    with open(state_path) as f:
        state = json.load(f)

    for ds_key in ["ds0", "ds1", "ds2", "ds3", "ds4", "ds5"]:
        assert ds_key in state, f"{ds_key} missing from setup_state.json"
        assert "id" in state[ds_key], f"{ds_key} missing 'id' field"
        assert "name" in state[ds_key], f"{ds_key} missing 'name' field"


@patch("run_setup.RAGFlowClient")
def test_tag_kb_id_propagated_to_datasets(mock_client_cls, tmp_path):
    """tag_kb_id must be injected into tag_kb_ids for ds1..ds5."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_datasets.return_value = []
    mock_client.create_dataset.return_value = {"id": "new_id"}
    mock_client.update_dataset.return_value = {}
    mock_client.put_metadata_config.return_value = {}
    mock_client.upload_tag_vocab.return_value = {}
    mock_client.wait_parse.return_value = {}

    with _isolate_out(tmp_path), patch("run_setup.write_vocab_txt"):
        state = run_setup_module.run_setup(dry_run=False)

    # Verify update_dataset was called for ds1..ds5 with tag_kb_ids=[tag_kb_id]
    tag_kb_id = state["ds0"]["id"]
    calls = mock_client.update_dataset.call_args_list
    # There should be 5 update_dataset calls (ds1..ds5)
    assert len(calls) == 5, f"Expected 5 update_dataset calls, got {len(calls)}"
    for call in calls:
        args, _ = call
        # update_dataset(ds_id, parser_config) — parser_config is the 2nd positional arg
        parser_config = args[1] if len(args) > 1 else {}
        assert parser_config.get("tag_kb_ids") == [tag_kb_id], (
            f"Expected tag_kb_ids=[{tag_kb_id}], got {parser_config.get('tag_kb_ids')}"
        )

    # 产物必须只落在临时目录，真实 out/ 不得出现 mock 状态
    assert (tmp_path / "setup_state.json").exists()


@patch("run_setup.RAGFlowClient")
def test_metadata_schema_only_on_ds1_to_ds5(mock_client_cls, tmp_path):
    """METADATA_SCHEMA 只注册到 ds1..ds5——标签库 ds0 走 tag 切片，无元数据语义。"""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_datasets.return_value = []
    mock_client.create_dataset.side_effect = lambda name, chunk_method: {"id": f"id-{name}"}
    mock_client.update_dataset.return_value = {}
    mock_client.put_metadata_config.return_value = {}
    mock_client.upload_tag_vocab.return_value = {}
    mock_client.wait_parse.return_value = {}

    with _isolate_out(tmp_path), patch("run_setup.write_vocab_txt"):
        state = run_setup_module.run_setup(dry_run=False)

    called_ids = [c.args[0] for c in mock_client.put_metadata_config.call_args_list]
    expected_ids = [state[k]["id"] for k in ("ds1", "ds2", "ds3", "ds4", "ds5")]
    assert sorted(called_ids) == sorted(expected_ids)
    assert state["ds0"]["id"] not in called_ids


@patch("run_setup.RAGFlowClient")
def test_update_dataset_sends_nested_graphrag_raptor(mock_client_cls, tmp_path):
    """Step5 PUT 必须携带嵌套 graphrag/raptor（deep-merge 才能压掉创建期 naive 默认 True）。"""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_datasets.return_value = []
    mock_client.create_dataset.side_effect = lambda name, chunk_method: {"id": f"id-{name}"}
    mock_client.update_dataset.return_value = {}
    mock_client.put_metadata_config.return_value = {}
    mock_client.upload_tag_vocab.return_value = {}
    mock_client.list_documents.return_value = [{"id": "v"}]   # 标签库已有词表 → 跳过上传

    with _isolate_out(tmp_path):
        state = run_setup_module.run_setup(dry_run=False)

    calls = {c.args[0]: c.args[1] for c in mock_client.update_dataset.call_args_list}
    # 每个业务库都收到了完整 parser_config
    for k in ("ds1", "ds2", "ds3", "ds4", "ds5"):
        assert k in state and state[k]["id"] in calls, f"{k} 未收到 update_dataset"
        pc = calls[state[k]["id"]]
        assert pc["graphrag"]["use_graphrag"] == config_by_key()[k]["parser_config"]["graphrag"]["use_graphrag"]
        assert pc["raptor"]["use_raptor"] == config_by_key()[k]["parser_config"]["raptor"]["use_raptor"]
    # ds1 的 GraphRAG 域实体类型必须真正下发（创建期默认是 organization/person 等通用类型）
    ds1_pc = calls[state["ds1"]["id"]]
    assert set(ds1_pc["graphrag"]["entity_types"]) >= {"FloodEvent", "Station"}
    assert ds1_pc["graphrag"]["resolution"] is True
    tag_kb_id = state["ds0"]["id"]
    assert ds1_pc["tag_kb_ids"] == [tag_kb_id]


def config_by_key():
    from config import DATASETS
    return {ds["key"]: ds for ds in DATASETS}


# ---------------------------------------------------------------------------
# P1：requests 异常友好提示（原 except ConnectionError 是死代码）+ 词表幂等
# ---------------------------------------------------------------------------

import requests


@patch("run_setup.RAGFlowClient")
def test_connection_error_friendly_exit(mock_client_cls, capsys):
    """requests 层连接失败必须打印提示并以 SystemExit 退出，而非裸 traceback。"""
    mock_client_cls.side_effect = requests.exceptions.ConnectionError("connection refused")
    with pytest.raises(SystemExit):
        run_setup_module.run_setup()
    out = capsys.readouterr().out
    assert "RAGFlow" in out and "ERROR" in out


@patch("run_setup.RAGFlowClient")
def test_placeholder_credentials_friendly_exit(mock_client_cls, capsys):
    mock_client_cls.side_effect = ValueError("RAGFLOW_EMAIL 未正确设置")
    with pytest.raises(SystemExit):
        run_setup_module.run_setup()
    out = capsys.readouterr().out
    assert "RAGFLOW_EMAIL" in out


@patch("run_setup.RAGFlowClient")
def test_tag_vocab_upload_skipped_when_kb_nonempty(mock_client_cls, tmp_path):
    """标签库已有文档时重跑不得再次上传词表（幂等，防 topn_tags 权重扭曲）。"""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_datasets.return_value = []
    mock_client.create_dataset.return_value = {"id": "new_id"}
    mock_client.list_documents.return_value = [{"id": "v", "name": "taoqupo_vocab.txt"}]

    with _isolate_out(tmp_path), patch("run_setup.write_vocab_txt"):
        run_setup_module.run_setup(dry_run=False)

    mock_client.upload_tag_vocab.assert_not_called()


@patch("run_setup.RAGFlowClient")
def test_tag_vocab_uploaded_when_kb_empty(mock_client_cls, tmp_path):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_datasets.return_value = []
    mock_client.create_dataset.return_value = {"id": "new_id"}
    mock_client.list_documents.return_value = []

    with _isolate_out(tmp_path):
        run_setup_module.run_setup(dry_run=False)

    mock_client.upload_tag_vocab.assert_called_once()


# ---------------------------------------------------------------------------
# P0-6：CLI 入口——CLAUDE.md 记载的阶段2命令 `python3 run_setup.py --dry-run`
# 此前因缺 main/__main__ 静默空跑（live 冒烟发现，exit 0 零输出）。
# ---------------------------------------------------------------------------

@pytest.fixture
def _no_creds_needed(monkeypatch):
    """main 不应触碰真实凭据——占位值即可，因为 dry-run 只列库。"""
    monkeypatch.setenv("RAGFLOW_EMAIL", "placeholder@example.com")
    monkeypatch.setenv("RAGFLOW_PASSWORD", "placeholder")


@patch("run_setup.RAGFlowClient")
def test_cli_dry_run_prints_plan_without_creating(mock_client_cls, tmp_path, capsys):
    """`run_setup.py --dry-run` 必须打印建库计划且不调用 create_dataset。"""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_datasets.return_value = []

    with _isolate_out(tmp_path), patch("run_setup.write_vocab_txt"), \
         patch("sys.argv", ["run_setup.py", "--dry-run"]):
        run_setup_module.main()

    out = capsys.readouterr().out
    assert "[dry_run] Would create dataset" in out, (
        f"dry-run 必须输出计划，实际 stdout: {out!r}"
    )
    assert "Dataset Summary" in out
    mock_client.create_dataset.assert_not_called()
    mock_client.put_metadata_config.assert_not_called()


@patch("run_setup.RAGFlowClient")
def test_cli_defaults_to_dry_run(mock_client_cls, tmp_path, capsys):
    """变更类脚本默认即 dry-run——不带任何旗标时不得落库。"""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_datasets.return_value = []

    with _isolate_out(tmp_path), patch("run_setup.write_vocab_txt"), \
         patch("sys.argv", ["run_setup.py"]):
        run_setup_module.main()

    out = capsys.readouterr().out
    assert "[dry_run]" in out
    mock_client.create_dataset.assert_not_called()


@patch("run_setup.RAGFlowClient")
def test_cli_apply_and_dry_run_conflict_errors(mock_client_cls, tmp_path, capsys):
    """--apply 与 --dry-run 同时给出 → argparse 报错退出，不执行任何动作。"""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    with _isolate_out(tmp_path), pytest.raises(SystemExit) as excinfo:
        with patch("sys.argv", ["run_setup.py", "--apply", "--dry-run"]):
            run_setup_module.main()

    assert excinfo.value.code != 0
    err = capsys.readouterr().err   # parser.error 写 stderr
    assert "互斥" in err or "not allowed" in err
    mock_client.create_dataset.assert_not_called()
