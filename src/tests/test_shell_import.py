"""tests/test_shell_import.py — 壳模式导入引擎（网络无关：fake client + stub requests）"""
import sys; sys.path.insert(0, "..")
import json

import pytest

import shell_import as si
from shell_import import (PreflightError, build_chunks, patch_chunk, preflight,
                          process, question_anchors, rollback, run_wave)


MD = """# 融合文本头

引言行一
引言行二

## 关键数值速查

8.21洪水瑶曲站日降雨量148毫米（mm），全流域最大。
2018年8月21日柳林断面实测洪峰流量383立方米每秒（m³/s）。

## 常问速查

问：折算入库洪峰是多少？答：466立方米每秒。
"""


def target(tmp_path, md_text=MD, corpus="a.xls", kb="a.xls"):
    md = tmp_path / f"fusion_{kb}.md"
    md.write_text(md_text, encoding="utf-8")
    (tmp_path / corpus).write_bytes(b"binary")
    return {"kb_name": kb, "corpus_path": corpus, "fusion_md": str(md),
            "probe": {"question": "q", "anchor": "148"},
            "meta": {"doc_type": "表格"}, "keywords": ["k1"]}


QUESTIONS = [
    {"fusion_doc": "a.xls", "all_keywords": ["148", "瑶曲"]},
    {"fusion_doc": "a.xls", "all_keywords": ["148", "383"]},
    {"fusion_doc": "b.xls", "all_keywords": ["1353"]},
]


class StubResp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class StubRequests:
    """记录调用的 requests 替身；所有动词返回 code=0。"""

    def __init__(self):
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        return StubResp({"code": 0, "data": {"chunk": {"id": f"ch{len(self.calls)}"}}})

    def __getattr__(self, method):  # delete/patch/put
        def f(url, headers=None, json=None, timeout=None):
            self.calls.append((method.upper(), url, json))
            return StubResp({"code": 0})
        return f


class FakeClient:
    def __init__(self, docs=None, search_hits=None, poll_run="DONE"):
        self.docs = docs or {}
        self.search_hits = search_hits or []
        self.calls = []
        self.uploaded_name = None
        self.poll_run = poll_run

    def find_document_by_name(self, ds, name):
        if self.uploaded_name == name:  # 上传后轮询：按 poll_run 返回
            return {"id": "shell-1", "run": self.poll_run, "chunk_count": 12}
        return self.docs.get(name)

    def list_chunks(self, ds, doc_id):
        return [{"id": f"c{i}", "content": "旧块" * 20} for i in range(3)]

    def upload_document(self, ds, path, filename=None):
        self.calls.append(("upload", str(path), filename))
        self.uploaded_name = filename
        return {"id": "shell-1", "run": "UNSTART"}

    def patch_document(self, ds, doc_id, meta):
        self.calls.append(("patch", doc_id, meta))

    def search_datasets(self, ds_list, question, top_k=10):
        self.search_calls = getattr(self, "search_calls", 0) + 1
        return {"chunks": self.search_hits}


OLD_DOC = {"id": "old-1", "run": "DONE", "chunk_count": 12,
           "meta_fields": {"doc_type": "表格", "year": "2008"},
           "chunk_method": "naive", "parser_config": {"auto_questions": 0}}


@pytest.fixture
def stub(monkeypatch):
    s = StubRequests()
    monkeypatch.setattr(si, "requests", s)
    monkeypatch.setattr(si, "VERIFY_SLEEPS", (0, 0))
    monkeypatch.setattr(si, "POLL_SEC", 0)
    return s


def test_build_chunks_sections_and_caps():
    md = "## A\n" + "\n".join(f"行{i}内容数据行" for i in range(25))
    chunks = build_chunks(md, max_lines=10, max_chars=600)
    assert len(chunks) == 3 and all(c.startswith("## A") for c in chunks)  # 25行按10行切3段
    assert build_chunks("## B\n短") == []  # <40 字残片丢弃


def test_question_anchors_dedup_in_order():
    assert question_anchors(QUESTIONS, "a.xls") == ["148", "瑶曲", "383"]
    assert question_anchors(QUESTIONS, "zz.xls") == []


def test_preflight_ok_returns_pieces(tmp_path):
    pieces = preflight(target(tmp_path), tmp_path, QUESTIONS)
    assert any("148" in p for p in pieces) and all(len(p) >= 40 for p in pieces)


def test_preflight_collects_all_problems(tmp_path):
    t = target(tmp_path)
    t["corpus_path"] = "不存在.xls"
    with pytest.raises(PreflightError) as e:
        preflight(t, tmp_path, QUESTIONS + [{"fusion_doc": "a.xls", "all_keywords": ["99999"]}])
    msg = str(e.value)
    assert "语料缺失" in msg and "锚点未覆盖" in msg and "99999" in msg


def test_preflight_missing_fusion_md(tmp_path):
    t = target(tmp_path)
    t["fusion_md"] = str(tmp_path / "nope.md")
    with pytest.raises(PreflightError) as e:
        preflight(t, tmp_path)
    assert "融合文本缺失" in str(e.value)


def test_process_dry_run_touches_nothing(stub, tmp_path):
    client = FakeClient({"a.xls": dict(OLD_DOC)})
    ok = process(client, "ds", target(tmp_path), tmp_path, QUESTIONS, False,
                 {}, {}, tmp_path / "arch.json", tmp_path / "st.json", tmp_path / "man.json")
    assert ok and not stub.calls and not client.calls


def test_process_dry_run_runs_preflight(stub, tmp_path):
    """评审 F12：dry-run 也先过 pre-flight，缺文件/锚点缺口提前暴露。"""
    t = target(tmp_path)
    t["corpus_path"] = "不存在.xls"
    ok = process(FakeClient(), "ds", t, tmp_path, QUESTIONS, False,
                 {}, {}, tmp_path / "arch.json", None, None)
    assert not ok and not stub.calls


def test_process_apply_without_archive_refuses(stub, tmp_path):
    """评审 F6 根治：apply 无存档路径拒删（否则删了不可回滚）。"""
    client = FakeClient({"a.xls": dict(OLD_DOC)})
    manifest = {}
    ok = process(client, "ds", target(tmp_path), tmp_path, QUESTIONS, True,
                 {}, manifest, None, None, None)
    assert not ok
    assert not any(c[0] == "DELETE" for c in stub.calls)
    assert "存档" in manifest["a.xls"]["status"]


def test_process_apply_happy_path(stub, tmp_path):
    client = FakeClient({"a.xls": dict(OLD_DOC)},
                        search_hits=[{"document_name": "a.xls",
                                      "content_with_weight": "瑶曲 148mm", "similarity": 0.87}])
    state, manifest = {}, {}
    ok = process(client, "ds", target(tmp_path), tmp_path, QUESTIONS, True,
                 state, manifest, tmp_path / "arch.json", tmp_path / "st.json",
                 tmp_path / "man.json")
    assert ok and manifest["a.xls"]["status"] == "OK"
    methods = [c[0] for c in stub.calls]
    assert methods[0] == "DELETE" and methods.count("POST") == len(build_chunks(MD))
    assert state["a.xls"]["verified"] is True
    arch = json.loads((tmp_path / "arch.json").read_text(encoding="utf-8"))
    assert arch["a.xls"]["snapshot"]["id"] == "old-1" and len(arch["a.xls"]["chunks"]) == 3


def test_process_preflight_refuses_delete(stub, tmp_path):
    t = target(tmp_path)
    t["corpus_path"] = "不存在.xls"
    client = FakeClient({"a.xls": dict(OLD_DOC)})
    manifest = {}
    ok = process(client, "ds", t, tmp_path, QUESTIONS, True,
                 {}, manifest, tmp_path / "arch.json", None, tmp_path / "man.json")
    assert not ok
    assert not any(c[0] == "DELETE" for c in stub.calls)  # 拒删：库里文档分毫无损
    assert "pre-flight" in manifest["a.xls"]["status"]


def test_run_wave_stops_on_verify_fail(stub, tmp_path):
    t1, t2 = target(tmp_path, kb="a.xls"), target(tmp_path, kb="b.xls")
    client = FakeClient({"a.xls": dict(OLD_DOC), "b.xls": dict(OLD_DOC, id="old-2")})
    ok = run_wave(client, "ds", [t1, t2], tmp_path, QUESTIONS, True,
                  archive_path=tmp_path / "arch.json",
                  state_path=tmp_path / "st.json", manifest_path=tmp_path / "man.json")
    assert not ok
    assert len([c for c in stub.calls if c[0] == "DELETE"]) == 1  # FAIL 即停，b.xls 保持现状


def test_rollback_replays_archive(stub, tmp_path):
    (tmp_path / "arch.json").write_text(json.dumps(
        {"a.xls": {"snapshot": OLD_DOC, "chunks": []}}, ensure_ascii=False), encoding="utf-8")
    client = FakeClient({"a.xls": dict(OLD_DOC, run="UNSTART", chunk_count=9)})
    rollback(client, "ds", target(tmp_path), tmp_path, tmp_path / "arch.json")
    puts = [c for c in stub.calls if c[0] == "PUT"]
    parses = [c for c in stub.calls if c[0] == "POST" and c[1].endswith("/parse")]
    assert puts and parses and puts[0][2]["chunk_method"] == "naive"
    assert ("patch", "shell-1", {"doc_type": "表格", "year": "2008"}) in client.calls


class FailingPutRequests(StubRequests):
    """PUT 返回 code!=0（模拟本服务器"静默不生效"前科）。"""

    def put(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("PUT", url, json))
        return StubResp({"code": 102, "message": "put silent fail"})


def test_rollback_put_failure_raises(stub, tmp_path, monkeypatch):
    """评审 F7：恢复解析配置的 PUT 不再吞响应，code!=0 即抛。"""
    (tmp_path / "arch.json").write_text(json.dumps(
        {"a.xls": {"snapshot": OLD_DOC, "chunks": []}}, ensure_ascii=False), encoding="utf-8")
    failing = FailingPutRequests()
    monkeypatch.setattr(si, "requests", failing)
    client = FakeClient({"a.xls": dict(OLD_DOC, run="UNSTART", chunk_count=9)})
    with pytest.raises(RuntimeError, match="恢复解析配置"):
        rollback(client, "ds", target(tmp_path), tmp_path, tmp_path / "arch.json")


def test_rollback_parse_fail_aborts(stub, tmp_path):
    """评审 F7：run=FAIL 不算"回滚完成"，明确中止。"""
    (tmp_path / "arch.json").write_text(json.dumps(
        {"a.xls": {"snapshot": OLD_DOC, "chunks": []}}, ensure_ascii=False), encoding="utf-8")
    client = FakeClient({"a.xls": dict(OLD_DOC, run="UNSTART")}, poll_run="FAIL")
    with pytest.raises(SystemExit, match="回滚解析失败"):
        rollback(client, "ds", target(tmp_path), tmp_path, tmp_path / "arch.json")


def test_verify_early_exits_on_first_hit(stub):
    """评审 F13：首轮命中不再烧第二次等待+检索。"""
    client = FakeClient(search_hits=[{"document_name": "a.xls",
                                      "content_with_weight": "瑶曲 148mm"}])
    ok, rank = si.verify(client, "ds", "a.xls", "q", "148", tries=2)
    assert ok and rank == 1 and client.search_calls == 1


def test_patch_chunk_sends_only_given_fields(stub):
    patch_chunk("ds", "doc", "ch1", content="新内容 790.5")
    method, url, body = stub.calls[0]
    assert method == "PATCH" and url.endswith("/chunks/ch1") and body == {"content": "新内容 790.5"}
