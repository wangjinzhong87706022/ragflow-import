"""Tests for image_transcribe：目标选择 / 转写行 / 抽样门 / 报告 / CLI。"""
import sys; sys.path.insert(0, "..")
from pathlib import Path

import json
import pytest

from image_transcribe import (
    MAX_TOKENS,
    _expand_dittos,
    bucket_of,
    is_target,
    load_done,
    load_targets,
    main,
    parse_transcription,
    render_report,
    sample_rows,
    select_todo,
    transcribe_one,
)


def _row(rel: str, **kw) -> dict:
    base = {"rel": rel, "ok": True, "content": "table", "writing": "printed",
            "quality": "good", "readable": True}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# 目标选择（与 triage_report 分诊桶同口径）
# ---------------------------------------------------------------------------

def test_is_target_b_c_union_and_blank_priority():
    assert is_target(_row("a", writing="handwritten")) is True    # B
    assert is_target(_row("a", writing="mixed")) is True          # B
    assert is_target(_row("a", quality="low")) is True            # C
    assert is_target(_row("a", readable=False)) is True           # C
    assert is_target(_row("a")) is False                          # F 正常不转写
    # A 空白优先于 B/C
    assert is_target(_row("a", content="blank", writing="handwritten")) is False
    assert is_target(_row("a", ok=False)) is False                # 分诊失败行不转写


def test_bucket_of_b_priority():
    assert bucket_of(_row("a", writing="handwritten")) == "B"
    assert bucket_of(_row("a", writing="mixed")) == "B"
    assert bucket_of(_row("a", quality="low")) == "C"             # 印刷体低清归 C
    # 手写且低清 → B 优先（与分诊报告桶序一致）
    assert bucket_of(_row("a", writing="mixed", quality="low")) == "B"


def test_load_targets_dedup_and_bucket(tmp_path):
    """同哈希副本只留保留名；目标行附 bucket 字段。"""
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "记录_01.jpg").write_bytes(b"same-bytes")
    (tmp_path / "d" / "记录_01(1).jpg").write_bytes(b"same-bytes")
    (tmp_path / "d" / "记录_02.jpg").write_bytes(b"other-bytes")
    (tmp_path / "d" / "表_03.jpg").write_bytes(b"third-bytes")
    triage = tmp_path / "t.jsonl"
    rows = [
        _row("d/记录_01.jpg", writing="handwritten"),
        _row("d/记录_01(1).jpg", writing="handwritten"),   # 副本 → 去重
        _row("d/记录_02.jpg", writing="mixed"),
        _row("d/表_03.jpg"),                               # 正常印刷 → 非目标
        _row("d/坏行.jpg", content="blank"),               # 空白 → 非目标
    ]
    triage.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
                      + "\n{bad json\n", encoding="utf-8")
    targets, n_dup = load_targets(triage, tmp_path)
    assert [t["rel"] for t in targets] == ["d/记录_01.jpg", "d/记录_02.jpg"]
    assert n_dup == 1
    assert [t["bucket"] for t in targets] == ["B", "B"]


def test_load_targets_missing_triage(tmp_path):
    assert load_targets(tmp_path / "nope.jsonl", tmp_path) == ([], 0)


def test_load_done_only_ok(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join([
        json.dumps({"rel": "a", "ok": True}),
        json.dumps({"rel": "b", "ok": False}),
        "{broken",
    ]) + "\n", encoding="utf-8")
    assert load_done(p) == {"a"}
    assert load_done(tmp_path / "missing.jsonl") == set()


def test_select_todo_only_redo_limit():
    targets = [{"rel": "d/机组运行记录表/1.jpg"},
               {"rel": "d/机组运行记录表/2.jpg"},
               {"rel": "d/操作票/3.jpg"}]
    done = {"d/机组运行记录表/1.jpg", "d/操作票/3.jpg"}
    # 断点续跑：done 跳过
    assert [t["rel"] for t in select_todo(targets, done)] == ["d/机组运行记录表/2.jpg"]
    # --only 子集重转（配合 --redo 忽略 done）
    assert [t["rel"] for t in select_todo(targets, done, only="机组运行记录表", redo=True)] == [
        "d/机组运行记录表/1.jpg", "d/机组运行记录表/2.jpg"]
    # --only 无 redo：仍受 done 约束
    assert [t["rel"] for t in select_todo(targets, done, only="机组运行记录表")] == [
        "d/机组运行记录表/2.jpg"]
    # limit 截断
    assert len(select_todo(targets, set(), limit=2)) == 2
    # 多子串（--only 可重复）：命中任一即可
    assert len(select_todo(targets, set(), only=["机组运行记录表", "操作票"])) == 3
    assert len(select_todo(targets, set(), only=["操作票"])) == 1


# ---------------------------------------------------------------------------
# 抽样（10% 人工复核门）
# ---------------------------------------------------------------------------

def test_sample_rows_deterministic_and_pct():
    rows = [_row(f"f{i:03d}.jpg") for i in range(300)]
    rows.append(_row("err.jpg", ok=False))
    s1 = {r["rel"] for r in sample_rows(rows, 10)}
    s2 = {r["rel"] for r in sample_rows(rows, 10)}
    assert s1 == s2                          # 跨次运行稳定
    assert 5 <= len(s1) <= 60                # 期望约 30
    assert "err.jpg" not in s1               # 失败行不进抽样


def test_sample_rows_pct_100_covers_all_ok():
    rows = [_row("a.jpg"), _row("b.jpg", ok=False), _row("c.jpg")]
    assert {r["rel"] for r in sample_rows(rows, 100)} == {"a.jpg", "c.jpg"}


# ---------------------------------------------------------------------------
# parse_transcription（两段式信封 + 兼容 JSON）
# ---------------------------------------------------------------------------

def test_parse_conf_two_part():
    d = parse_transcription("CONF: LOW | NOTES: 签名潦草\n# 调度单\n编号 2-002")
    assert d["low_conf"] is True and d["notes"] == "签名潦草"
    assert d["text"].startswith("# 调度单")
    d = parse_transcription("CONF: HIGH | NOTES: ->\n正文一行")
    assert d["low_conf"] is False and d["notes"] == ""
    assert d["text"] == "正文一行"


def test_parse_fence_stripped():
    d = parse_transcription("```markdown\nCONF: HIGH | NOTES: -\n# 表\n```\n")
    assert d["low_conf"] is False and d["text"] == "# 表"


def test_parse_missing_header_falls_back_low_conf():
    """首行不合规 → 正文照收但强制低置信（进抽查清单，不丢转写）。"""
    d = parse_transcription("```markdown\n# 裸 markdown 无信封\n正文\n```")
    assert d["low_conf"] is True
    assert "未返回 CONF 头" in d["notes"]
    assert "# 裸 markdown 无信封" in d["text"]


def test_parse_legacy_json_envelope():
    d = parse_transcription('{"text":"旧 JSON 正文","low_conf":false,"notes":""}')
    assert d == {"text": "旧 JSON 正文", "low_conf": False, "notes": ""}
    with pytest.raises(ValueError):     # JSON 内换行未转义等格式坏 → 明确失败
        parse_transcription('{"text": "坏行1\n坏行2", "low_conf": false}')


def test_parse_empty_and_header_only_are_errors():
    with pytest.raises(ValueError):
        parse_transcription("   ")
    with pytest.raises(ValueError):
        parse_transcription("CONF: HIGH | NOTES: ->")


# ---------------------------------------------------------------------------
# transcribe_one（fake transport，网络无关）
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _make_img(tmp_path: Path, rel: str) -> Path:
    pytest.importorskip("PIL")
    from PIL import Image
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (50, 40), "white").save(p)
    return p


def test_transcribe_one_ok_row(tmp_path):
    p = _make_img(tmp_path, "d/记录1.jpg")
    captured = {}

    def fake_transport(url, headers=None, json=None, timeout=None):
        captured.update(json=json, url=url)
        return _FakeResp("CONF: LOW | NOTES: 签名模糊\n# 操作票\n编号 2-002")

    row = transcribe_one(p, tmp_path, "B", "http://gw/v1", "k", "m1", 2000,
                         transport=fake_transport, backoff=())
    assert row["ok"] is True
    assert row["bucket"] == "B"
    assert row["low_conf"] is True
    assert row["notes"] == "签名模糊"
    assert "操作票" in row["text"]
    assert row["rel"] == "d/记录1.jpg"
    # 密集表格转写必须显式放大 max_tokens（默认 4k 会截断出半截正文）
    assert captured["json"]["max_tokens"] == MAX_TOKENS
    assert captured["url"].endswith("/chat/completions")
    # 提示词应为两段式信封（JSON 信封长文本下模型不转义换行 → 格式失败）
    body = json.dumps(captured["json"]["messages"], ensure_ascii=False)
    assert "CONF: HIGH 或 CONF: LOW" in body


def test_transcribe_one_error_row(tmp_path):
    p = _make_img(tmp_path, "d/坏页.jpg")

    def fake_transport(url, headers=None, json=None, timeout=None):
        raise RuntimeError("429 Too Many Requests")

    row = transcribe_one(p, tmp_path, "C", "http://gw/v1", "k", "m1", 2000,
                         transport=fake_transport, backoff=())
    assert row["ok"] is False
    assert "RuntimeError" in row["error"]
    assert row["bucket"] == "C"


def test_transcribe_one_header_only_is_error(tmp_path):
    """CONF 头后无正文 → 失败行而非 ok 空文（空文会挂出空 chunk）。"""
    p = _make_img(tmp_path, "d/空响应.jpg")

    def fake_transport(url, headers=None, json=None, timeout=None):
        return _FakeResp("CONF: HIGH | NOTES: ->")

    row = transcribe_one(p, tmp_path, "B", "http://gw/v1", "k", "m1", 2000,
                         transport=fake_transport, backoff=())
    assert row["ok"] is False
    assert "正文" in row["error"]


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def test_render_report_sections_and_dedupe():
    targets = [{"rel": "a.jpg", "bucket": "B"}, {"rel": "b.jpg", "bucket": "C"}]
    rows = [
        {"rel": "a.jpg", "ok": True, "bucket": "B", "text": "转写正文A",
         "low_conf": False, "notes": ""},
        {"rel": "b.jpg", "ok": True, "bucket": "C", "text": "低清正文B",
         "low_conf": True, "notes": "大面积模糊"},
        {"rel": "c.jpg", "ok": False, "bucket": "B", "error": "HTTPError: 429"},
    ]
    out = render_report(rows, targets, Path("E:/corpus"), 100, "http://ep", "flash")
    assert "转写目标：2 页" in out
    assert "失败清单" in out and "HTTPError: 429" in out
    assert "低置信清单" in out and "大面积模糊" in out
    assert "抽样复核" in out and "另 1 页已在低置信清单" in out
    assert "转写正文A" in out                    # 正常页进抽样
    assert out.count("低清正文B") == 1           # 低置信页不重复进抽样
    assert "--approve" in out


def test_render_report_no_failures_no_low():
    targets = [{"rel": "a.jpg", "bucket": "B"}]
    rows = [{"rel": "a.jpg", "ok": True, "bucket": "B", "text": "x",
             "low_conf": False, "notes": ""}]
    out = render_report(rows, targets, Path("E:/corpus"), 10, "ep", "m")
    assert "失败清单" not in out
    assert "低置信清单" not in out


def test_render_report_recovered_error_not_counted():
    """append-only：同 rel 失败后重试成功 → 失败清单/计数只认成功行。"""
    targets = [{"rel": "a.jpg", "bucket": "B"}]
    rows = [
        {"rel": "a.jpg", "ok": False, "bucket": "B", "error": "ValueError: 格式坏"},
        {"rel": "a.jpg", "ok": True, "bucket": "B", "text": "恢复正文",
         "low_conf": False, "notes": ""},
    ]
    out = render_report(rows, targets, Path("E:/corpus"), 100, "ep", "m")
    assert "失败清单" not in out
    assert "1 成功 / 0 失败" in out
    assert out.count("恢复正文") == 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_main_approve_creates_flag(tmp_path):
    main(["--approve", "--out-dir", str(tmp_path)])
    assert (tmp_path / "transcribe_approved.flag").exists()


def test_main_report_only_writes_report(tmp_path):
    src = tmp_path / "src"
    (src / "d").mkdir(parents=True)
    (src / "d" / "记录1.jpg").write_bytes(b"img")
    triage = tmp_path / "t.jsonl"
    triage.write_text(json.dumps(_row("d/记录1.jpg", writing="handwritten"),
                                 ensure_ascii=False) + "\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    main(["--report-only", "--triage", str(triage), "--out-dir", str(out_dir),
          "--src", str(src)])
    report = out_dir / "transcribe_report.md"
    assert report.exists()
    assert "转写目标：1 页" in report.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 惯常写法展开（prompt 第 8 条的确定性兜底）
# ---------------------------------------------------------------------------

def test_expand_dittos_expands_ditto_cell():
    src = ("| 姓名 | 单位 |\n| :--- | :--- |\n"
           "| 张三 | 陕西省泾惠水利水电设计院 |\n| 张光伟 | 〃 |")
    out = _expand_dittos(src)
    assert "| 张光伟 | 陕西省泾惠水利水电设计院 |" in out


def test_expand_dittos_keeps_ditto_when_prev_empty():
    src = "| a | b |\n| - | - |\n|  | 〃 |\n| 张三 | 〃 |"
    out = _expand_dittos(src)
    assert "|  | 〃 |" in out


def test_expand_dittos_keeps_ditto_after_separator_row():
    src = "| 姓名 | 单位 |\n| :--- | :--- |\n| 张三 | 〃 |"
    out = _expand_dittos(src)
    assert "| 张三 | 〃 |" in out


def test_expand_dittos_ignores_non_table_lines():
    src = "同上所述\n\n| a | b |\n| - | - |\n| x | 〃 |"
    out = _expand_dittos(src)
    assert out.splitlines()[0] == "同上所述"
    assert "| x | 〃 |" in out
