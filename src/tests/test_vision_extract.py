import sys; sys.path.insert(0, "..")
from vision_extract import build_payload, call_vision, cross_check
import base64, json

def test_build_payload_structure():
    fake_bytes = b"\xff\xd8\xff\xe0fake image data"
    payload = build_payload(fake_bytes, "识别数值")
    content = payload["content"]
    img_part = next(p for p in content if p.get("type") == "image_url")
    b64_data = img_part["image_url"]["url"].split(",")[1]
    assert base64.b64decode(b64_data) == fake_bytes


# ---------------------------------------------------------------------------
# P1-2 附带发现（live 冒烟 400）：call_vision 必须把单条消息包进 OpenAI
# chat/completions 信封 {"model": ..., "messages": [...]}——直接裸发 message dict
# 被网关以 400 Bad Request 拒收。
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, content):
        self._content = content
    def raise_for_status(self):
        pass
    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def test_call_vision_posts_openai_envelope_with_model():
    captured = {}

    def fake_transport(url, headers=None, json=None, timeout=None):
        captured.update(url=url, json=json)
        return _FakeResp("识别结果")

    message = build_payload(b"\xff\xd8fake", "识别数值")
    text = call_vision(message, "http://gw/v1", "k", model="m1", transport=fake_transport)

    assert text == "识别结果"
    assert captured["url"].endswith("/chat/completions")
    body = captured["json"]
    assert body["model"] == "m1"
    assert body["messages"] == [message]   # build_payload 的单条消息即 messages[0]


def test_call_vision_defaults_model_from_config():
    """model 缺省取 config.LLM_MODEL（随环境变量可覆盖），不再缺字段。"""
    from vision_extract import LLM_MODEL
    captured = {}

    def fake_transport(url, headers=None, json=None, timeout=None):
        captured.update(json=json)
        return _FakeResp("ok")

    call_vision(build_payload(b"x", "p"), "http://gw/v1", "k", transport=fake_transport)
    assert captured["json"]["model"] == LLM_MODEL


def test_call_vision_read_timeout_is_configurable_and_generous():
    """多模态生成本身可超分钟——读超时必须取 config.VISION_CALL_TIMEOUT（默认600s），
    120s 硬编码曾把正常生成掐成失败重试（live 冒烟第二轮实录）。"""
    import config as cfg
    from vision_extract import VISION_CALL_TIMEOUT
    captured = {}

    def fake_transport(url, headers=None, json=None, timeout=None):
        captured.update(timeout=timeout)
        return _FakeResp("ok")

    call_vision(build_payload(b"x", "p"), "http://gw/v1", "k", transport=fake_transport)

    assert VISION_CALL_TIMEOUT >= 300          # 至少给足多模态生成时间
    assert captured["timeout"] == cfg.VISION_CALL_TIMEOUT

def test_cross_check_found():
    """键必须是与 TEXTUALIZED_VALUES 一致的中文标签（P1-2：修复前后端各说各话的死信号）。"""
    text = "百年一遇泄量1454 m³/s，千年一遇泄量2218 m³/s，汛限水位788.5m"
    result = cross_check(text)
    assert result["found"]["百年一遇泄量"] is True
    assert result["found"]["千年一遇泄量"] is True
    assert result["found"]["汛限水位"] is True

def test_cross_check_values_record_matches():
    text = "百年一遇泄量1454 m³/s"
    result = cross_check(text)
    assert result["values"]["百年一遇泄量"] == "1454 m³/s"

def test_cross_check_missing():
    text = "汛限水位为788米"
    result = cross_check(text)
    assert result["found"]["百年一遇泄量"] is False
    assert result["found"]["汛限水位"] is False   # "788" alone doesn't match "788.5"
    assert result["values"]["百年一遇泄量"] == ""

def test_cross_check_empty_text_all_missing():
    """空输出（VLM 失败）→ 全部标签 MISSING，报告与 ok_count 才能如实反映。"""
    result = cross_check("")
    assert set(result["found"]) == {"百年一遇泄量", "千年一遇泄量", "汛限水位"}
    assert not any(result["found"].values())

def test_cross_check_output_shape():
    text = "test"
    result = cross_check(text)
    assert "found" in result and "values" in result
    assert isinstance(result["found"], dict)
