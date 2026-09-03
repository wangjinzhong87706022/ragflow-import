import sys; sys.path.insert(0, "..")
import json

import photo_triage
from photo_triage import iter_triage_images, parse_verdict


# ---------------------------------------------------------------------------
# 语料枚举：只收图片扩展名，递归且稳定排序
# ---------------------------------------------------------------------------

def _make_root(tmp_path):
    root = tmp_path / "01-洪水现场照片"
    (root / "2021年10月").mkdir(parents=True)
    (root / "2021年9月").mkdir()
    return root


def _touch(root, rel, data=b"x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_iter_triage_images_only_image_exts_sorted(tmp_path):
    root = _make_root(tmp_path)
    _touch(root, "2021年10月/b.jpg")
    _touch(root, "2021年10月/a.png")
    _touch(root, "2021年9月/c.jpeg")
    _touch(root, "2021年10月/movie.mp4")      # 视频不收
    _touch(root, "2021年10月/notes.txt")      # 非图片不收

    photo_triage.TRIAGE_ROOT = root
    rels = [p.name for p in iter_triage_images()]

    assert rels == ["a.png", "b.jpg", "c.jpeg"]   # 排序跨子目录稳定


def test_iter_triage_images_empty_root(tmp_path):
    photo_triage.TRIAGE_ROOT = tmp_path / "不存在"
    assert iter_triage_images() == []


# ---------------------------------------------------------------------------
# VLM 输出解析：宽容提取 JSON，类别归一到四类
# ---------------------------------------------------------------------------

def test_parse_verdict_plain_json():
    v = parse_verdict('{"category": "文件扫描件", "title_hint": "汛情通报", '
                      '"date_hint": "2021-10-05", "handwriting": true, "confidence": 0.9}')
    assert v["category"] == "文件扫描件"
    assert v["handwriting"] is True
    assert v["confidence"] == 0.9


def test_parse_verdict_fenced_json():
    text = '```json\n{"category": "现场照片", "title_hint": "", "date_hint": "", ' \
           '"handwriting": false, "confidence": 0.8}\n```'
    assert parse_verdict(text)["category"] == "现场照片"


def test_parse_verdict_json_embedded_in_prose():
    text = '好的，判断如下：{"category": "图表图纸", "title_hint": "过程线", ' \
           '"date_hint": "", "handwriting": false, "confidence": 0.7} 以上。'
    assert parse_verdict(text)["category"] == "图表图纸"


def test_parse_verdict_invalid_returns_none():
    assert parse_verdict("抱歉，我无法判断这张图片。") is None
    assert parse_verdict("") is None


def test_parse_verdict_normalizes_unknown_category():
    """VLM 自造类别词 → 按关键词归一到四类，绝不产出第五类。"""
    v = parse_verdict('{"category": "扫描的文件", "title_hint": "", "date_hint": "", '
                      '"handwriting": false, "confidence": 0.5}')
    assert v["category"] == "文件扫描件"


def test_parse_verdict_confidence_clamped():
    v = parse_verdict('{"category": "现场照片", "title_hint": "", "date_hint": "", '
                      '"handwriting": false, "confidence": 1.7}')
    assert v["confidence"] == 1.0


# ---------------------------------------------------------------------------
# classify_image：复用 vision_extract 的 payload/call，输出归一 verdict
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, content):
        self._content = content
    def raise_for_status(self):
        pass
    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


_DOC_JSON = '{"category": "文件扫描件", "title_hint": "汛情通报", "date_hint": "2021-10-05", "handwriting": true, "confidence": 0.9}'
_PHOTO_JSON = '{"category": "现场照片", "title_hint": "", "date_hint": "", "handwriting": false, "confidence": 0.8}'


def test_classify_image_sends_prompt_and_returns_verdict():
    captured = {}

    def fake_transport(url, headers=None, json=None, timeout=None):
        captured.update(json=json)
        return _FakeResp(_DOC_JSON)

    verdict = photo_triage.classify_image(b"\xff\xd8img", "http://gw/v1", "k", transport=fake_transport)

    assert verdict["category"] == "文件扫描件"
    parts = captured["json"]["messages"][0]["content"]
    assert any(p.get("type") == "image_url" for p in parts)
    assert "文件扫描件" in next(p["text"] for p in parts if p.get("type") == "text")


# ---------------------------------------------------------------------------
# run()：断点续跑、失败记录、报告与 CSV
# ---------------------------------------------------------------------------

def _prep(tmp_path, monkeypatch, names):
    root = _make_root(tmp_path)
    for n in names:
        _touch(root, n)
    monkeypatch.setattr(photo_triage, "TRIAGE_ROOT", root)
    out_dir = tmp_path / "out" / "photo_triage"
    monkeypatch.setattr(photo_triage, "OUT_DIR", tmp_path / "out")
    return root, out_dir


def test_run_classifies_all_and_writes_artifacts(tmp_path, monkeypatch):
    _, out_dir = _prep(tmp_path, monkeypatch, ["2021年10月/a.png", "2021年10月/b.jpg", "2021年9月/c.jpeg"])
    contents = [_DOC_JSON, _PHOTO_JSON, _PHOTO_JSON]
    calls = []

    def fake_transport(url, headers=None, json=None, timeout=None):
        calls.append(1)
        return _FakeResp(contents[len(calls) - 1])

    summary = photo_triage.run(api_key="k", transport=fake_transport)

    assert summary["counts"]["文件扫描件"] == 1
    assert summary["counts"]["现场照片"] == 2
    assert len(calls) == 3
    assert (out_dir / "results.csv").exists()
    report = (out_dir / "triage_report.md").read_text(encoding="utf-8")
    assert "## 文件扫描件" in report and "a.png" in report


def test_run_resume_skips_existing_result(tmp_path, monkeypatch):
    root, out_dir = _prep(tmp_path, monkeypatch, ["2021年10月/a.png", "2021年10月/b.jpg"])
    done_name = "2021年10月__a.png.json"
    (out_dir / done_name).parent.mkdir(parents=True)
    (out_dir / done_name).write_text(json.dumps(
        {"category": "文件扫描件", "title_hint": "旧", "date_hint": "", "handwriting": False, "confidence": 1.0}),
        encoding="utf-8")
    calls = []

    def fake_transport(url, headers=None, json=None, timeout=None):
        calls.append(1)
        return _FakeResp(_PHOTO_JSON)

    photo_triage.run(api_key="k", transport=fake_transport)
    assert len(calls) == 1   # 只补跑缺的 b.jpg

    calls.clear()
    photo_triage.run(api_key="k", transport=fake_transport, force=True)
    assert len(calls) == 2   # force 全部重跑


def test_run_failure_recorded_and_loop_continues(tmp_path, monkeypatch):
    root, out_dir = _prep(tmp_path, monkeypatch, ["2021年10月/a.png", "2021年10月/b.jpg"])
    # 按图像内容区分（而非调用序号）：a.png 恒失败，重试语义下 b.jpg 仍须正常分类
    (root / "2021年10月" / "a.png").write_bytes(b"A")
    (root / "2021年10月" / "b.jpg").write_bytes(b"B")
    import base64
    a_b64 = base64.b64encode(b"A").decode()

    def fake_transport(url, headers=None, json=None, timeout=None):
        img_url = json["messages"][0]["content"][0]["image_url"]["url"]
        if a_b64 in img_url:
            raise ConnectionError("网关超时")
        return _FakeResp(_PHOTO_JSON)

    summary = photo_triage.run(api_key="k", transport=fake_transport)

    assert summary["counts"].get("失败") == 1
    assert summary["counts"].get("现场照片") == 1
    report = (out_dir / "triage_report.md").read_text(encoding="utf-8")
    assert "## 失败" in report and "a.png" in report


def test_run_without_api_key_aborts_before_any_call(tmp_path, monkeypatch):
    _, out_dir = _prep(tmp_path, monkeypatch, ["2021年10月/a.png"])
    calls = []

    def fake_transport(url, headers=None, json=None, timeout=None):
        calls.append(1)
        return _FakeResp(_PHOTO_JSON)

    assert photo_triage.run(api_key="", transport=fake_transport) is None
    assert calls == []
    assert not (out_dir / "results.csv").exists()


def test_main_without_env_key_exits_cleanly(tmp_path, monkeypatch):
    _prep(tmp_path, monkeypatch, ["2021年10月/a.png"])
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["photo_triage.py"])
    photo_triage.main()   # 不抛异常即通过


# ---------------------------------------------------------------------------
# 大图缩放：超过网关请求体限制的图先降采样（11–15.8MB 原图曾整批 400）
# ---------------------------------------------------------------------------

import io

import pytest

PIL_Image = pytest.importorskip("PIL.Image")


def _big_jpeg(megabytes_target=12):
    """生成一张噪声 JPEG（随机像素压缩率低，容易超过目标体积）。"""
    import random
    w, h = 6000, 4000
    img = PIL_Image.new("RGB", (w, h))
    img.putdata([(random.randrange(256), random.randrange(256), random.randrange(256))
                 for _ in range(w * h)])
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    assert len(buf.getvalue()) > megabytes_target * 1024 * 1024, len(buf.getvalue())
    return buf.getvalue()


def test_prepare_small_image_passthrough():
    small = b"\xff\xd8 tiny"
    assert photo_triage.prepare_image_bytes(small, max_bytes=1024 * 1024) == small


def test_prepare_large_image_shrinks_under_limit():
    big = _big_jpeg()
    out = photo_triage.prepare_image_bytes(big, max_bytes=8 * 1024 * 1024)
    assert len(out) <= 8 * 1024 * 1024
    PIL_Image.open(io.BytesIO(out)).verify()   # 仍是合法图像


def test_classify_image_downscales_oversized_input():
    import base64
    captured = {}

    def fake_transport(url, headers=None, json=None, timeout=None):
        captured.update(json=json)
        return _FakeResp(_PHOTO_JSON)

    big = _big_jpeg()
    photo_triage.classify_image(big, "http://gw/v1", "k", transport=fake_transport)

    b64 = next(p["image_url"]["url"] for p in captured["json"]["messages"][0]["content"]
               if p.get("type") == "image_url").split(",")[1]
    assert len(base64.b64decode(b64)) <= 8 * 1024 * 1024
