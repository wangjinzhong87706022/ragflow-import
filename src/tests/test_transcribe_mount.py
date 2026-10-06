"""tests/test_transcribe_mount.py — 合并 PDF 壳模式挂载（网络无关：fake client）"""
import sys; sys.path.insert(0, "..")

import json
from pathlib import Path

import pytest

import transcribe_mount as tm
from transcribe_mount import (build_page_md, is_bc, last_ok_by_rel,
                              missing_anchors, page_anchor, preflight,
                              read_jsonl)


def _row(rel, **kw):
    base = {"rel": rel, "ok": True, "content": "table", "writing": "printed",
            "quality": "good", "readable": True}
    base.update(kw)
    return base


def test_last_ok_by_rel_ignores_failed_and_takes_latest():
    rows = [
        {"rel": "a.jpg", "ok": True, "text": "第一版"},
        {"rel": "a.jpg", "ok": False, "error": "boom"},
        {"rel": "a.jpg", "ok": True, "text": "第二版"},
        {"rel": "b.jpg", "ok": False, "error": "boom"},
    ]
    got = last_ok_by_rel(rows)
    assert got["a.jpg"]["text"] == "第二版"
    assert "b.jpg" not in got


def test_is_bc_matches_image_transcribe_semantics():
    assert is_bc(_row("x", writing="handwritten"))
    assert is_bc(_row("x", writing="mixed"))
    assert is_bc(_row("x", quality="low"))
    assert is_bc(_row("x", readable=False))
    assert not is_bc(_row("x", content="blank", writing="handwritten"))
    assert not is_bc(_row("x"))
    assert not is_bc(_row("x", ok=False, writing="handwritten"))


def test_read_jsonl_skips_bad_lines(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"rel":"a","ok":true}\n\n坏行\n{"rel":"b","ok":true}\n',
                 encoding="utf-8")
    assert [r["rel"] for r in read_jsonl(p)] == ["a", "b"]
    assert read_jsonl(tmp_path / "missing.jsonl") == []


def test_page_anchor_takes_first_meaningful_line():
    assert page_anchor("# 标题\n\n| 姓名 | 单位 |\n| 张三 | 设计院 |") == "| 姓名 | 单位 |"
    assert page_anchor("|---|---|\n") is None
    assert page_anchor("") is None


def test_build_page_md_sections_per_page():
    md = build_page_md("目录/x（合并）.pdf", ["a/1.jpg", "a/2.jpg"],
                       {"a/1.jpg": "第一页正文内容足够长", "a/2.jpg": "第二页正文内容足够长"})
    assert md.startswith("# x（合并）.pdf（VLM 转写，共 2 页）")
    assert "## 第1页" in md and "## 第2页" in md
    assert md.index("## 第1页") < md.index("## 第2页")


def test_build_page_md_raises_on_missing_page_text():
    with pytest.raises(ValueError):
        build_page_md("x.pdf", ["a/1.jpg", "a/2.jpg"],
                      {"a/1.jpg": "有文字的页面内容", "a/2.jpg": "   "})


def test_short_page_still_becomes_chunk(monkeypatch, tmp_path):
    """回归：整页只有一行（标题页/图签）时不得被 40 字下限整页丢弃。"""
    posted = []
    monkeypatch.setattr(tm, "add_chunk",
                        lambda ds, doc, content, kw: posted.append(content))
    monkeypatch.setattr(tm, "delete_doc", lambda ds, doc: None)
    pdf = tmp_path / "短页（合并）.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    client = FakeClient()
    client.docs = {}
    texts = {"a/1.jpg": "标题页：进行编制。",
             "a/2.jpg": "正文页内容足够长的一段文字用于满足最小长度要求"}
    info = tm.mount_one(client, "ds-1", tmp_path, "短页（合并）.pdf",
                        ["a/1.jpg", "a/2.jpg"], texts, {"rel": "短页（合并）.pdf"})
    assert info["chunks"] == 3          # 文档头 1 块 + 两页各 1 块
    assert any("进行编制。" in c for c in posted)
    assert sum(1 for c in posted if c.startswith("## 第")) == 2


def test_preflight_flags_flag_file_and_missing_text(tmp_path):
    pdf = tmp_path / "目录" / "x（合并）.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4")
    groups = {"目录/x（合并）.pdf": ["a/1.jpg", "a/2.jpg"]}
    flag = tmp_path / "transcribe_approved.flag"

    problems = preflight(groups, tmp_path, {"a/1.jpg": "文字"}, flag)
    assert any("打标" in p for p in problems)
    assert any("无转写文本" in p for p in problems)

    flag.touch()
    problems = preflight(groups, tmp_path, {"a/1.jpg": "文字", "a/2.jpg": "文字"}, flag)
    assert problems == []


def test_preflight_flags_missing_staging_file(tmp_path):
    flag = tmp_path / "ok.flag"
    flag.touch()
    problems = preflight({"缺失/x（合并）.pdf": ["a/1.jpg"]}, tmp_path,
                         {"a/1.jpg": "文字"}, flag)
    assert any("暂存件缺失" in p for p in problems)


def test_missing_anchors_detects_uncovered_page():
    texts = {"a/1.jpg": "徐木泵站机组运行记录表 第一行", "a/2.jpg": "第二页完全不同的内容"}
    chunks = ["## 第1页\n徐木泵站机组运行记录表 第一行"]
    miss = missing_anchors(["a/1.jpg", "a/2.jpg"], texts, chunks)
    assert len(miss) == 1 and miss[0].startswith("第2页")


class FakeClient:
    def __init__(self):
        self.docs = {"x（合并）.pdf": {"id": "old-1"}}
        self.calls = []

    def find_document_by_name(self, ds, name):
        return self.docs.get(name)

    def upload_document(self, ds, path, filename=None):
        self.calls.append(("upload", Path(path).name))
        return {"id": "new-1"}

    def patch_document(self, ds, doc_id, meta):
        self.calls.append(("patch", doc_id, meta.get("rel")))


def test_mount_one_full_flow(tmp_path, monkeypatch):
    pdf = tmp_path / "目录" / "x（合并）.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4")
    page_rels = ["a/1.jpg", "a/2.jpg"]
    texts = {
        "a/1.jpg": "徐木泵站机组运行记录表\n日期：2026年8月16日\n机组号：1#\n"
                   "流量4541，AC 10.1，10KV进线柜电压 5746 5923",
        "a/2.jpg": "徐木泵站机组运行记录表\n日期：2026年8月17日\n机组号：1#\n"
                   "流量4439，AC 10.4，10KV进线柜电压 5688 5931",
    }
    posted = []

    def fake_add_chunk(ds, doc, content, keywords):
        posted.append(content)
        return f"ch{len(posted)}"

    deleted = []
    monkeypatch.setattr(tm, "add_chunk", fake_add_chunk)
    monkeypatch.setattr(tm, "delete_doc", lambda ds, doc: deleted.append(doc))

    client = FakeClient()
    info = tm.mount_one(client, "ds-1", tmp_path, "目录/x（合并）.pdf",
                        page_rels, texts, {"rel": "目录/x（合并）.pdf"})

    assert deleted == ["old-1"]                      # 同名旧文档先删
    assert ("upload", "x（合并）.pdf") in client.calls
    assert ("patch", "new-1", "目录/x（合并）.pdf") in client.calls
    assert info["pages"] == 2 and info["chunks"] == len(posted)
    assert all(len(c) >= tm.MIN_CHARS for c in posted)


def test_mount_one_probe_failure_raises(tmp_path, monkeypatch):
    pdf = tmp_path / "y（合并）.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(tm, "add_chunk", lambda ds, doc, content, kw: "ch")
    monkeypatch.setattr(tm, "delete_doc", lambda ds, doc: None)
    # 切片内容与页文本无关 → 探针锚点必然缺失
    monkeypatch.setattr(tm, "build_chunks",
                        lambda md, **kw: ["与页文本无关的切片内容占位文字"])
    client = FakeClient()
    client.docs = {}
    with pytest.raises(RuntimeError, match="探针"):
        tm.mount_one(client, "ds-1", tmp_path, "y（合并）.pdf",
                     ["a/1.jpg"], {"a/1.jpg": "徐木泵站机组运行记录表 正文内容足够长的一段"},
                     {"rel": "y（合并）.pdf"})


def test_main_dry_run_lists_groups(tmp_path, monkeypatch, capsys):
    src = tmp_path / "src" / "组1"
    src.mkdir(parents=True)
    (src / "01.jpg").write_bytes(b"x")
    tri = tmp_path / "triage.jsonl"
    tri.write_text(json.dumps(_row("组1/01.jpg", writing="handwritten")) + "\n",
                   encoding="utf-8")
    tx = tmp_path / "tx.jsonl"
    tx.write_text(json.dumps({"rel": "组1/01.jpg", "ok": True,
                              "text": "手写记录表正文内容足够长的一段文字"}, ensure_ascii=False) + "\n",
                  encoding="utf-8")
    staging = tmp_path / "staging" / "组1"
    staging.mkdir(parents=True)
    (staging / "组1（合并）.pdf").write_bytes(b"%PDF-1.4")
    mapping = tmp_path / "mapping.csv"
    mapping.write_text("rel,dataset_key\n组1/组1（合并）.pdf,jhc1\n", encoding="utf-8")
    setup = tmp_path / "setup.json"
    setup.write_text(json.dumps({"jhc1": {"id": "ds-1"}}), encoding="utf-8")
    flag = tmp_path / "flag"
    flag.touch()

    monkeypatch.setattr(tm, "APPROVE_FLAG", flag)
    monkeypatch.setattr(tm, "pick_bc_groups",
                        lambda s, t: {"组1/组1（合并）.pdf": ["组1/01.jpg"]})
    tm.main(["--src", str(tmp_path / "src"), "--staging", str(tmp_path / "staging"),
             "--triage", str(tri), "--transcribe", str(tx),
             "--mapping", str(mapping), "--setup", str(setup),
             "--state", str(tmp_path / "state.json")])
    out = capsys.readouterr().out
    assert "含B/C的合并组=1" in out and "preflight 通过" in out
    assert "dry-run" in out


def test_main_exits_when_preflight_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "APPROVE_FLAG", tmp_path / "nope.flag")
    monkeypatch.setattr(tm, "pick_bc_groups", lambda s, t: {"组1/x（合并）.pdf": ["a/1.jpg"]})
    with pytest.raises(SystemExit):
        tm.main(["--src", str(tmp_path), "--staging", str(tmp_path),
                 "--triage", str(tmp_path / "t.jsonl"),
                 "--transcribe", str(tmp_path / "x.jsonl"),
                 "--mapping", str(tmp_path / "m.csv"),
                 "--setup", str(tmp_path / "s.json")])