# tests/test_ragflow_client.py
# 契约以线上 v0.27.0 容器实证为准（docs/review-2026-08-26-deep.md §一）：
#   - 登录密码：服务端 crypt() 是 base64(明文)→RSA→base64，客户端必须对偶
#   - 所有业务失败都是 HTTP 200 + {"code": 非0}，必须检查响应体 code
#   - GET /datasets 返回 data 为数组；GET .../documents 返回 data.docs；
#     列表分页参数是 page/page_size（BaseListReq extra="forbid"）
#   - REST 层 run 状态是字符串名 "DONE"/"FAIL"（兼容数字 "3"/"4"）
import sys
sys.path.insert(0, "..")

import pytest
from requests.adapters import HTTPAdapter

from ragflow_client import encrypt_password, RAGFlowClient, RAGFlowApiError


# ---------------------------------------------------------------------------
# P0-1 登录加密：与服务端 crypt() 完全对偶（base64(明文) → RSA → base64）
# ---------------------------------------------------------------------------

def _rsa_keypair(tmp_path):
    from Crypto.PublicKey import RSA
    key = RSA.generate(2048)
    priv = tmp_path / "private.pem"
    pub = tmp_path / "public.pem"
    priv.write_bytes(key.export_key())
    pub.write_bytes(key.publickey().export_key())
    return priv, pub


def test_encrypt_password_roundtrips_to_base64_plaintext(tmp_path):
    """用私钥解密客户端密文，必须得到 base64(明文)——服务端 check 的就是它。"""
    import base64
    from Crypto.Cipher import PKCS1_v1_5
    priv, pub = _rsa_keypair(tmp_path)

    result = encrypt_password("testpass", str(pub))

    key = __import__("Crypto").PublicKey.RSA.import_key(priv.read_text())
    decrypted = PKCS1_v1_5.new(key).decrypt(base64.b64decode(result), None)
    assert decrypted == base64.b64encode(b"testpass"), (
        f"解密结果应为 base64(明文)，实际为 {decrypted!r}"
    )


def test_encrypt_password_nondeterministic(tmp_path):
    import base64
    _, pub = _rsa_keypair(tmp_path)
    r1 = encrypt_password("testpass", str(pub))
    r2 = encrypt_password("testpass", str(pub))
    assert r1 != r2  # PKCS1_v1_5 随机填充
    assert len(base64.b64decode(r1)) == len(base64.b64decode(r2)) == 256


# ---------------------------------------------------------------------------
# 测试脚手架：mock Session + 统一构造合法信封
# ---------------------------------------------------------------------------

def _resp(payload, status_code=200):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = payload
    return r


def _ok(data=None, **extra):
    """HTTP 200 + code==0 的标准成功信封。"""
    return _resp({"code": 0, "message": "success", "data": data, **extra})


def _mk_client():
    with patch("ragflow_client.requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.post.return_value = _ok({})
        with patch("ragflow_client.encrypt_password", return_value="e"):
            c = RAGFlowClient("t@t.com", "p", "/fake/pem")
    return c, mock_session


from unittest.mock import patch, MagicMock  # noqa: E402  (供上方工厂使用)


# ---------------------------------------------------------------------------
# P1-1 业务失败信封：HTTP 200 + code!=0 必须抛错而非静默当成功
# ---------------------------------------------------------------------------

def test_login_failure_code109_raises():
    """登录失败返回 200+code=109——必须在构造时立即报错并带出 message。"""
    with patch("ragflow_client.requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.post.return_value = _resp(
            {"code": 109, "message": "Email and password do not match!", "data": False}
        )
        with patch("ragflow_client.encrypt_password", return_value="e"):
            with pytest.raises(RAGFlowApiError, match="109"):
                RAGFlowClient("t@t.com", "wrong", "/fake/pem")


def test_business_error_code_raises_on_search():
    c, session = _mk_client()
    session.post.return_value = _resp({"code": 102, "message": "bad filter", "data": None})
    with pytest.raises(RAGFlowApiError, match="bad filter"):
        c.search_datasets(["ds1"], "q")


def test_all_calls_carry_timeout():
    with patch("ragflow_client.requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.post.return_value = _ok({})
        mock_session.get.return_value = _ok([])
        with patch("ragflow_client.encrypt_password", return_value="e"):
            c = RAGFlowClient("t@t.com", "p", "/fake/pem")
        assert "timeout" in mock_session.post.call_args.kwargs

        mock_session.post.reset_mock()
        c.search_datasets(["d"], "q")
        c.parse_documents("d", ["x"])
        for call in mock_session.post.call_args_list:
            assert call.kwargs.get("timeout"), f"{call} 缺少 timeout"


# ---------------------------------------------------------------------------
# P0-2 列表接口形状与分页参数
# ---------------------------------------------------------------------------

def test_list_datasets_parses_array_data_and_uses_page_params():
    """GET /datasets 的 data 直接是数组（get_result），分页参数为 page/page_size。"""
    with patch("ragflow_client.requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.post.return_value = _ok({})   # login
        mock_session.get.side_effect = [
            _ok([{"id": "a", "name": "规程与预案"}]),
            _ok([]),
        ]
        with patch("ragflow_client.encrypt_password", return_value="e"):
            c = RAGFlowClient("t@t.com", "p", "/fake/pem")

        ds = c.list_datasets()

    assert ds == [{"id": "a", "name": "规程与预案"}]
    first_kwargs = mock_session.get.call_args_list[0].kwargs
    assert "page" in first_kwargs["params"] and "page_size" in first_kwargs["params"]
    assert "offset" not in first_kwargs["params"] and "limit" not in first_kwargs["params"]


def test_list_documents_reads_docs_key_and_pages_by_page_param():
    """>page_size 时翻页取全；响应键是 data.docs（不是 list）。"""
    with patch("ragflow_client.requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.post.return_value = _ok({})   # login
        page1 = [{"id": f"d{i}"} for i in range(100)]
        mock_session.get.side_effect = [
            _ok({"total": 101, "docs": page1}),
            _ok({"total": 101, "docs": [{"id": "last"}]}),
        ]
        with patch("ragflow_client.encrypt_password", return_value="e"):
            c = RAGFlowClient("t@t.com", "p", "/fake/pem")

        docs = c.list_documents("ds1")

    assert len(docs) == 101
    second_call_kwargs = mock_session.get.call_args_list[1].kwargs
    assert second_call_kwargs["params"]["page"] == 2
    assert second_call_kwargs["params"]["page_size"] == 100


def test_find_document_by_name():
    c, session = _mk_client()
    session.get.return_value = _ok(
        {"total": 2, "docs": [{"id": "a", "name": "x.txt"}, {"id": "b", "name": "y.txt"}]}
    )
    assert c.find_document_by_name("ds1", "y.txt") == {"id": "b", "name": "y.txt"}
    assert c.find_document_by_name("ds1", "不存在.txt") is None


def test_placeholder_credentials_rejected():
    """config 占位默认值必须在构造客户端时 fail-fast，而非推迟到登录 400。"""
    with pytest.raises(ValueError, match="RAGFLOW_EMAIL"):
        RAGFlowClient("placeholder@example.com", "placeholder", "/fake/pem")


def test_api_key_mode_skips_login_and_sets_bearer_header():
    """API-key 模式：占位邮箱/密码不再校验、完全跳过登录（零网络往返），
    Session 直接挂 Authorization: Bearer —— SDK 端点原生支持该鉴权。"""
    with patch("ragflow_client.requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session.headers = {}
        mock_session_class.return_value = mock_session
        with patch.object(RAGFlowClient, "login") as mock_login:
            c = RAGFlowClient(
                "placeholder@example.com", "placeholder", "/fake/pem",
                api_key="ragflow-test-key",
            )
    mock_login.assert_not_called()
    assert c.session.headers["Authorization"] == "Bearer ragflow-test-key"


def test_empty_api_key_keeps_login_validation():
    """api_key 为空串时行为完全不变：占位凭据仍 fail-fast，防止静默裸跑。"""
    with pytest.raises(ValueError, match="RAGFLOW_EMAIL"):
        RAGFlowClient("placeholder@example.com", "placeholder", "/fake/pem", api_key="")


def test_search_payload_structure():
    c, session = _mk_client()
    session.post.return_value = _ok({"chunks": [], "total": 0})
    c.search_datasets(
        ["ds1", "ds2"],
        "溢洪道设计泄量",
        use_kg=True,
        meta_data_filter={"method": "manual", "logic": "and", "conditions": []},
    )
    payload = session.post.call_args[1]["json"]
    assert "dataset_ids" in payload
    assert payload["use_kg"] is True


def test_meta_data_filter_conditions_normalized_to_manual():
    """P1-5 回归 pin：v0.27.1 apply_meta_data_filter 的 manual 分支读
    meta_data_filter["manual"]（列表），不读 "conditions"——写错键会被静默忽略，
    过滤等于没过滤（QC Q5 曾因此假阳性）。client 必须归一成 "manual"，
    已写 "manual" 的调用方原样放行。"""
    c, session = _mk_client()
    session.post.return_value = _ok({"chunks": [], "total": 0})
    cond = [{"key": "flood_event", "op": "=", "value": "2013-7"}]

    c.search_datasets(["ds3"], "q", meta_data_filter={
        "method": "manual", "logic": "and", "conditions": cond})
    payload = session.post.call_args[1]["json"]["meta_data_filter"]
    assert payload["manual"] == cond
    assert "conditions" not in payload
    assert payload["method"] == "manual" and payload["logic"] == "and"

    c.search_datasets(["ds3"], "q", meta_data_filter={"manual": cond})
    payload = session.post.call_args[1]["json"]["meta_data_filter"]
    assert payload["manual"] == cond           # 原样放行
    assert payload["method"] == "manual"       # setdefault 补齐

    # 无过滤时完全不携带该键（行为与旧版一致）
    c.search_datasets(["ds3"], "q")
    assert "meta_data_filter" not in session.post.call_args[1]["json"]


def test_search_optional_ranking_params_only_when_set():
    """P2-9 检索层专项：rerank_id / vector_similarity_weight / keyword / page_size /
    rerank_candidates_count 为 v0.27.1 REST 可选项；缺省一律不进 payload（行为与
    旧版完全一致），显式传入才携带（rerank_id=对话层同款 rerank 模型行 id）。"""
    c, session = _mk_client()
    session.post.return_value = _ok({"chunks": [], "total": 0})

    c.search_datasets(["ds1"], "q")
    base = session.post.call_args[1]["json"]
    for absent in ("rerank_id", "vector_similarity_weight", "keyword", "page_size",
                   "rerank_candidates_count"):
        assert absent not in base, f"缺省时不应携带 {absent}"

    c.search_datasets(["ds1"], "q", rerank_id="r1", vector_similarity_weight=0.5,
                      keyword=True, page_size=10, rerank_candidates_count=256)
    payload = session.post.call_args[1]["json"]
    assert payload["rerank_id"] == "r1"
    assert payload["vector_similarity_weight"] == 0.5
    assert payload["keyword"] is True
    assert payload["page_size"] == 10
    assert payload["rerank_candidates_count"] == 256


# ---------------------------------------------------------------------------
# P0-3 run 状态双兼容："DONE"/"FAIL"（REST 层）与 "3"/"4"（DB 枚举）
# ---------------------------------------------------------------------------

def test_wait_document_accepts_done_string():
    c, _ = _mk_client()
    c.list_documents = MagicMock(return_value=[{"id": "v", "run": "DONE", "progress": 1.0}])
    doc = c.wait_document("ds1", "v", timeout=2)
    assert doc["run"] == "DONE"


def test_wait_document_accepts_numeric_three():
    c, _ = _mk_client()
    c.list_documents = MagicMock(return_value=[{"id": "v", "run": "3", "progress": 1.0}])
    assert c.wait_document("ds1", "v", timeout=2)["id"] == "v"


def test_wait_document_accepts_legacy_db_enum_without_progress():
    """run=DONE 但 progress 尚未刷到 1 时也必须判完成（两信号独立）。"""
    c, _ = _mk_client()
    c.list_documents = MagicMock(return_value=[{"id": "v", "run": "DONE", "progress": 0.7}])
    assert c.wait_document("ds1", "v", timeout=2)["id"] == "v"


def test_wait_document_raises_on_fail_string():
    c, _ = _mk_client()
    c.list_documents = MagicMock(return_value=[{"id": "v", "run": "FAIL", "progress": -1}])
    with pytest.raises(RuntimeError, match="FAIL"):
        c.wait_document("ds1", "v")


def test_wait_document_raises_on_numeric_four():
    c, _ = _mk_client()
    c.list_documents = MagicMock(return_value=[{"id": "v", "run": "4", "progress": 0}])
    with pytest.raises(RuntimeError):
        c.wait_document("ds1", "v")


def test_wait_parse_returns_docs_list_on_done_string():
    c, _ = _mk_client()
    c.list_documents = MagicMock(return_value=[{"id": "v", "run": "DONE", "progress": 1.0}])
    out = c.wait_parse("ds0", timeout=2)
    assert isinstance(out, list) and out[0]["id"] == "v"


def test_wait_parse_fails_on_fail_string():
    c, _ = _mk_client()
    c.list_documents = MagicMock(return_value=[{"id": "v", "run": "FAIL", "progress": -1}])
    with pytest.raises(RuntimeError):
        c.wait_parse("ds0")


# ---------------------------------------------------------------------------
# chunk 审查支撑：list_chunks 分页封装
# ---------------------------------------------------------------------------

def test_list_chunks_paginates_and_normalizes():
    with patch("ragflow_client.requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.post.return_value = _ok({})   # login
        page1 = {
            "chunks": [
                {"id": "c1", "content": "百年一遇泄量1454 m³/s", "important_keywords": ["泄量"]},
                {"id": "c2", "content_with_weight": "旧字段形状的文本"},
            ],
            "total": 3,
        }
        page2 = {"chunks": [{"id": "c3", "content": "汛限水位788.5m"}], "total": 3}
        mock_session.get.side_effect = [_ok(page1), _ok(page2)]
        with patch("ragflow_client.encrypt_password", return_value="e"):
            c = RAGFlowClient("t@t.com", "p", "/fake/pem")

        chunks = c.list_chunks("ds1", "doc1")

    assert [x["id"] for x in chunks] == ["c1", "c2", "c3"]
    assert chunks[0]["content"] == "百年一遇泄量1454 m³/s"
    assert chunks[1]["content"] == "旧字段形状的文本"
    assert chunks[0]["keywords"] == ["泄量"]
    second_kwargs = mock_session.get.call_args_list[1].kwargs
    assert second_kwargs["params"]["page"] == 2
    assert "timeout" in second_kwargs


def test_list_chunks_empty():
    c, session = _mk_client()
    session.get.return_value = _ok({"chunks": [], "total": 0})
    assert c.list_chunks("ds1", "doc1") == []


# ---------------------------------------------------------------------------
# P1-4 瞬时连接错误重试：RemoteDisconnected 等连接层错误应被适配器吞掉重试，
# 不应击穿整条导入链路。业务错误（code!=0）不重试。
# ---------------------------------------------------------------------------

def test_retry_adapter_mounted_on_session():
    """构造客户端后 session 必须挂载带 Retry 的 HTTPAdapter（http 与 https）。

    这里必须走真实 Session（只 patch 掉登录与加密），否则拿不到真实适配器。
    """
    with patch("ragflow_client.encrypt_password", return_value="e"), \
         patch.object(RAGFlowClient, "login", lambda self: None):
        c = RAGFlowClient("t@t.com", "p", "/fake/pem")
    for scheme in ("http://", "https://"):
        adapter = c.session.get_adapter(scheme)
        assert isinstance(adapter, HTTPAdapter), f"{scheme} 未挂载 HTTPAdapter"
        assert adapter.max_retries.total == 4, f"{scheme} 适配器未挂载重试策略"
        assert adapter.max_retries.connect == 4


def test_transient_connection_error_does_not_breach(monkeypatch):
    """wait_document 轮询中一次 RemoteDisconnected 应被重试吞掉，最终拿到 DONE。"""
    import requests.exceptions as _rexc
    c, _ = _mk_client()
    calls = {"n": 0}

    def _flaky_list(_dataset_id):
        calls["n"] += 1
        if calls["n"] == 1:
            # 模拟瞬时连接断开（urllib3 会在适配器层重试，但此处直接 mock
            # list_documents，故模拟"重试后第二次成功"的端到端语义）
            raise _rexc.ConnectionError("Remote end closed connection without response")
        return [{"id": "v", "run": "DONE", "progress": 1.0}]

    c.list_documents = _flaky_list
    doc = c.wait_document("ds1", "v", timeout=5)
    assert doc["run"] == "DONE"
    assert calls["n"] == 2  # 第一次失败（连接错误），第二次成功


def test_business_error_not_retried():
    """业务失败信封（HTTP 200 + code!=0）必须立即抛出，不被重试策略拖延。"""
    c, session = _mk_client()
    session.post.reset_mock()   # 清掉构造期登录那次调用，只统计本次 search
    session.post.return_value = _resp({"code": 102, "message": "bad filter", "data": None})
    with pytest.raises(RAGFlowApiError, match="102"):
        c.search_datasets(["ds1"], "q")
    # 业务失败只调一次——Retry 的 status_forcelist 为空，不会重试 200 响应
    assert session.post.call_count == 1


def test_repeated_connection_errors_still_raise(monkeypatch):
    """连续连接错误超出等待窗后仍必须上抛（带最后一次错误），不静默吞掉。"""
    import requests.exceptions as _rexc
    c, _ = _mk_client()

    def _always_fail(_dataset_id):
        raise _rexc.ConnectionError("Remote end closed connection without response")

    c.list_documents = _always_fail
    # 轮询容错会在窗口内反复退避重试；窗口耗尽后以 TimeoutError 上抛，
    # 并把最后一次传输错误附在消息里——绝不是无解释的静默失败。
    with pytest.raises(TimeoutError, match="Remote end closed connection"):
        c.wait_document("ds1", "v", timeout=1)


# ---------------------------------------------------------------------------
# P1-5 会话瞬时失效（HTTP 401）容错：MySQL 闪断期间鉴权查询失败会让轮询中的
# GET 突然 401（2026-08-31 ds5 实证）。此时应 re-login 后继续轮询，而不是把
# 基础设施抖动误判成文档解析失败。非 401 的 HTTP 状态码不属瞬时抖动，原样上抛。
# ---------------------------------------------------------------------------

def _http_error(status_code: int):
    """构造携带真实 Response 的 HTTPError（_check 的 raise_for_status 语义）。"""
    import requests
    resp = requests.Response()
    resp.status_code = status_code
    return requests.exceptions.HTTPError(f"{status_code} error", response=resp)


def test_wait_document_relogin_on_401_and_continue():
    """轮询中一次 401 应 re-login 后继续，最终拿到 DONE，而不是判死。"""
    import requests.exceptions as _rexc
    c, _ = _mk_client()
    calls = {"n": 0}

    def _auth_blip_list(_dataset_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(401)
        return [{"id": "v", "run": "DONE", "progress": 1.0}]

    c.list_documents = _auth_blip_list
    c.login = MagicMock()
    doc = c.wait_document("ds1", "v", timeout=5)
    assert doc["run"] == "DONE"
    assert calls["n"] == 2
    c.login.assert_called_once()   # 401 后必须重建会话，否则后续请求继续 401


def test_wait_document_reraises_non_401_http_error():
    """非 401 的 HTTP 错误（如 500）不属会话抖动，必须原样上抛且不碰 login。"""
    import requests.exceptions as _rexc
    c, _ = _mk_client()
    c.list_documents = lambda _dataset_id: (_ for _ in ()).throw(_http_error(500))
    c.login = MagicMock()
    with pytest.raises(_rexc.HTTPError):
        c.wait_document("ds1", "v", timeout=5)
    c.login.assert_not_called()


def test_wait_parse_relogin_on_401_and_continue():
    """wait_parse（tag KB 解析等待）享有同款 401 容错：re-login 后继续。"""
    import requests.exceptions as _rexc
    c, _ = _mk_client()
    calls = {"n": 0}

    def _auth_blip_list(_dataset_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(401)
        return [{"id": "v", "run": "DONE", "progress": 1.0}]

    c.list_documents = _auth_blip_list
    c.login = MagicMock()
    docs = c.wait_parse("ds0", timeout=5)
    assert docs[0]["run"] == "DONE"
    assert calls["n"] == 2
    c.login.assert_called_once()

