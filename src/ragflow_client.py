# ragflow_client.py
"""
RAGFlow API client with RSA login and full dataset/document surface.
Import API_BASE and PUBLIC_PEM from config (resolved relative to ragflow_import/).

所有请求均携带 timeout：服务端挂起时快速失败，避免阶段3导入循环整夜卡死。

契约对齐线上 v0.27.0 容器（docs/review-2026-08-26-deep.md §一）：
  - 登录密码加密 = base64(明文) → RSA(PKCS1_v1_5) → base64，与服务端 crypt() 对偶；
    业务失败一律 HTTP 200 + {"code": 非0}，因此每个方法都检查响应体 code。
  - 列表接口分页参数为 page/page_size（extra="forbid"，offset/limit 会被拒），
    datasets 的 data 是数组，documents 的数据键是 data.docs。
  - REST 层 run 状态是字符串名 "DONE"/"FAIL"（DB 枚举 "3"/"4" 不出现在 API 响应），
    等待逻辑同时兼容两种表示与 progress 信号。
"""
import base64
import pathlib
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import HTTPError
from urllib3.util.retry import Retry

from config import API_BASE, PUBLIC_PEM

DEFAULT_TIMEOUT = 30   # 常规 JSON 调用
UPLOAD_TIMEOUT = 120   # 文件上传

# 瞬时连接错误重试：shared-LLM 网关与 Docker bridge 在并发解析期间偶发
# TCP reset（RemoteDisconnected / ConnectionAborted / ConnectionReset），服务端
# 解析并未中断，但单次 HTTP 调用被击穿后整条导入链路即判 failed。此处对纯
# 连接层错误做有限指数退避重试；业务错误（HTTP 200 + code!=0）由 _check 抛出、
# 不在此重试（Retry 默认只管连接/读超时，不管 status_forcelist，故不会重试业务失败）。
_RETRY_STRATEGY = Retry(
    total=4,                       # 最多 4 次重试（含首次共 5 次尝试）
    connect=4,                     # 连接建立阶段错误全重试
    read=4,                        # 读阶段错误（含 RemoteDisconnected）重试
    backoff_factor=0.5,            # 退避：0.5, 1, 2, 4 秒
    status_forcelist=(),           # 不按 HTTP 状态码重试——业务失败信封是 200
    allowed_methods=frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"}),
    raise_on_status=False,         # 状态码交给 _check / raise_for_status 处理
    respect_retry_after_header=False,
)

# 轮询循环可容忍的传输层错误——适配器重试耗尽后的最后防线。只容忍连接层，
# 绝不容忍 HTTPError（状态码属业务语义）或业务信封错误。
_TRANSIENT_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)


def _poll_backoff(consecutive_errors: int) -> float:
    """轮询中连续传输错误的退避时长：0.25→0.5→1→2→4 封顶 5 秒。"""
    return min(0.25 * 2 ** (consecutive_errors - 1), 5.0)


class RAGFlowApiError(RuntimeError):
    """业务失败：HTTP 200 但响应体 code != 0（v0.27.0 的错误约定）。"""
    def __init__(self, code, message: str):
        super().__init__(f"RAGFlow 返回 code={code}: {message}")
        self.code = code
        self.message = message


def _check(resp) -> dict:
    """断言 HTTP 2xx 且业务信封 code==0；返回解析后的 JSON body。"""
    resp.raise_for_status()
    try:
        body = resp.json()
    except ValueError as exc:
        raise RAGFlowApiError("non-json", f"响应不是 JSON：{exc}") from exc
    code = body.get("code")
    if isinstance(code, str) and code.isdigit():
        code = int(code)
    if code != 0:
        raise RAGFlowApiError(body.get("code"), str(body.get("message", "")))
    return body


def encrypt_password(password: str, public_pem_path: str) -> str:
    """
    Encrypt a password for the RAGFlow login route.

    与服务端 ``api/utils/crypt.py`` 完全对偶：先 ``base64(明文)`` 再以
    RSA PKCS1_v1_5 加密，密文再 base64。服务端 decrypt 得到 base64(明文)
    并以其哈希比对注册时存储的哈希——直接加密明文会解出 raw 明文而登录必败。
    """
    from Crypto.Cipher import PKCS1_v1_5
    from Crypto.PublicKey import RSA

    key = RSA.import_key(open(public_pem_path).read())
    cipher = PKCS1_v1_5.new(key)
    password_b64 = base64.b64encode(password.encode("utf-8")).decode("ascii")
    ciphertext = cipher.encrypt(password_b64.encode("ascii"))
    return base64.b64encode(ciphertext).decode("ascii")


def _run_done(run) -> bool:
    return str(run) in {"3", "DONE", "done"}

def _run_failed(run) -> bool:
    return str(run) in {"4", "FAIL", "fail"}

def _normalize_meta_data_filter(mf: dict) -> dict:
    """v0.27.1 契约：manual 模式的条件列表放在 "manual" 键下。

    服务端 apply_meta_data_filter（common/metadata_utils.py）manual 分支读
    meta_data_filter.get("manual", [])——写 "conditions" 键会被静默忽略：
    filters=[] → 不过滤也无 "-999" 占位，检索照常返回无过滤结果
    （QC Q5 的"元数据钻取"曾因此长期是假阳性）。这里把 "conditions"
    归一成 "manual"，已写 "manual" 的调用方原样放行。条件本身用
    key/op/value 形状（ES pushdown 的 is_pushdown_supported 认这个）。
    """
    mf = dict(mf)
    conditions = mf.pop("conditions", None)
    if conditions is not None and "manual" not in mf:
        mf["manual"] = conditions
    mf.setdefault("method", "manual")
    return mf




class RAGFlowClient:
    """RAGFlow API client. Handles RSA login and carries session cookie for all requests."""

    def __init__(self, email: str, password: str, public_pem_path: str, api_key: str = ""):
        self.session = requests.Session()
        # 挂载重试适配器：仅对瞬时连接层错误退避重试，业务失败不重试。
        _adapter = HTTPAdapter(max_retries=_RETRY_STRATEGY)
        self.session.mount("http://", _adapter)
        self.session.mount("https://", _adapter)
        self._api_key_mode = bool(api_key)
        if self._api_key_mode:
            # API-key 模式：SDK 端点原生支持 Bearer 鉴权，跳过登录（无需邮箱/密码/RSA 公钥）。
            self.session.headers["Authorization"] = f"Bearer {api_key}"
            # 显式置空：wait_* 的 401 分支会调用 login()，需要能识别"无凭据可重登"
            # （否则会落到 AttributeError，见 2026-09-12 评审 P2-11）。
            self._email = ""
            self._encrypted_password = ""
            return
        # Fail fast on placeholder/missing credentials instead of a confusing login 400
        if not email or "@" not in email or not password or password == "placeholder":
            raise ValueError(
                "[配置错误] RAGFLOW_EMAIL / RAGFLOW_PASSWORD 未正确设置——"
                "请通过环境变量注入真实凭据后再运行"
            )
        self._email = email
        self._encrypted_password = encrypt_password(password, public_pem_path)
        self.login()

    def login(self) -> None:
        if getattr(self, "_api_key_mode", False):
            raise RuntimeError(
                "API-key 模式无法重新登录（未持有邮箱/密码）：请使用长期有效的 key，"
                "或改用 RAGFLOW_EMAIL / RAGFLOW_PASSWORD 登录模式。"
            )
        resp = self.session.post(
            f"{API_BASE}/auth/login",
            json={"email": self._email, "password": self._encrypted_password},
            timeout=DEFAULT_TIMEOUT,
        )
        _check(resp)   # 登录失败 = 200 + code=109，在此立即报错并带出 message

    # ------------------------------------------------------------------
    # Dataset CRUD
    # ------------------------------------------------------------------
    def list_datasets(self, page_size: int = 100, max_pages: int = 10) -> list[dict]:
        """GET /datasets — data 直接是数组；分页参数 page/page_size。"""
        out: list[dict] = []
        for page in range(1, max_pages + 1):
            resp = self.session.get(
                f"{API_BASE}/datasets",
                params={"page": page, "page_size": page_size},
                timeout=DEFAULT_TIMEOUT,
            )
            batch = _check(resp)["data"] or []
            out.extend(batch)
            if len(batch) < page_size:
                break
        return out

    def create_dataset(self, name: str, chunk_method: str = "naive") -> dict:
        resp = self.session.post(
            f"{API_BASE}/datasets", json={"name": name, "chunk_method": chunk_method},
            timeout=DEFAULT_TIMEOUT,
        )
        return _check(resp)["data"]

    def update_dataset(self, dataset_id: str, parser_config: dict) -> dict:
        resp = self.session.put(
            f"{API_BASE}/datasets/{dataset_id}", json={"parser_config": parser_config},
            timeout=DEFAULT_TIMEOUT,
        )
        return _check(resp)["data"]

    # ------------------------------------------------------------------
    # Metadata schema
    # ------------------------------------------------------------------
    def put_metadata_config(self, dataset_id: str, metadata: list[dict]) -> dict:
        resp = self.session.put(
            f"{API_BASE}/datasets/{dataset_id}/metadata/config",
            json={"metadata": metadata, "built_in_metadata": []},
            timeout=DEFAULT_TIMEOUT,
        )
        return _check(resp)["data"]

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------
    def upload_document(
        self, dataset_id: str, file_path: pathlib.Path, filename: str | None = None
    ) -> dict | list:
        name = filename or file_path.name
        with open(file_path, "rb") as f:
            resp = self.session.post(
                f"{API_BASE}/datasets/{dataset_id}/documents",
                files={"file": (name, f, "application/octet-stream")},
                timeout=UPLOAD_TIMEOUT,
            )
        # v0.27.0 上传成功的 data 是文档 dict 列表
        return _check(resp)["data"]

    def patch_document(
        self, dataset_id: str, doc_id: str, meta_fields: dict
    ) -> dict:
        resp = self.session.patch(
            f"{API_BASE}/datasets/{dataset_id}/documents/{doc_id}",
            json={"meta_fields": meta_fields},
            timeout=DEFAULT_TIMEOUT,
        )
        return _check(resp)["data"]

    def list_documents(
        self, dataset_id: str, page_size: int = 100, max_pages: int = 10
    ) -> list[dict]:
        """List documents with pagination — 数据键是 data.docs，参数 page/page_size。"""
        docs: list[dict] = []
        total: int | None = None
        for page in range(1, max_pages + 1):
            resp = self.session.get(
                f"{API_BASE}/datasets/{dataset_id}/documents",
                params={"page": page, "page_size": page_size},
                timeout=DEFAULT_TIMEOUT,
            )
            payload = (_check(resp)["data"]) or {}
            docs.extend(payload.get("docs", []))
            if isinstance(payload.get("total"), int):
                total = payload["total"]
            if total is not None:
                if len(docs) >= total or not payload.get("docs"):
                    break
            elif len(payload.get("docs", [])) < page_size:
                break
        return docs

    def find_document_by_name(self, dataset_id: str, filename: str) -> Optional[dict]:
        """First document whose name matches *filename*, or None（重跑复用辅助）。"""
        return next(
            (d for d in self.list_documents(dataset_id) if d.get("name") == filename),
            None,
        )

    def parse_documents(self, dataset_id: str, document_ids: list[str]) -> dict:
        resp = self.session.post(
            f"{API_BASE}/datasets/{dataset_id}/documents/parse",
            json={"document_ids": document_ids},
            timeout=DEFAULT_TIMEOUT,
        )
        return _check(resp)["data"]

    def list_chunks(
        self, dataset_id: str, doc_id: str, page_size: int = 100, max_pages: int = 50
    ) -> list[dict]:
        """
        Fetch all chunks of a document with pagination（chunk 审查支撑）。

        归一化输出：每个 chunk 至少含 ``id`` / ``content`` / ``keywords``；
        ``content`` 兼容 ``content`` 与 ``content_with_weight`` 两种字段形状。
        """
        chunks: list[dict] = []
        total: int | None = None
        for page in range(1, max_pages + 1):
            resp = self.session.get(
                f"{API_BASE}/datasets/{dataset_id}/documents/{doc_id}/chunks",
                params={"page": page, "page_size": page_size},
                timeout=DEFAULT_TIMEOUT,
            )
            payload = (_check(resp))["data"] or {}
            raw = payload.get("chunks", [])
            if isinstance(payload.get("total"), int):
                total = payload["total"]
            for c in raw:
                chunks.append({
                    "id": c.get("id", ""),
                    "content": c.get("content") or c.get("content_with_weight") or "",
                    "keywords": c.get("important_keywords") or [],
                })
            # 停止条件：优先对齐服务端 total；响应缺 total 时退回短页判定
            if total is not None:
                if len(chunks) >= total or not raw:
                    break
            elif len(raw) < page_size:
                break
        return chunks

    def wait_document(
        self, dataset_id: str, doc_id: str, timeout: float = 300
    ) -> dict:
        deadline = time.time() + timeout
        last_err: Exception | None = None
        consecutive_errs = 0
        while time.time() < deadline:
            try:
                docs = self.list_documents(dataset_id)
                consecutive_errs = 0
            except _TRANSIENT_ERRORS as exc:
                # 瞬时断连不判死：退避后继续轮询；持续失败最终以超时上抛，
                # 并保留最后一次错误供人工诊断——绝不静默吞掉。
                last_err = exc
                consecutive_errs += 1
                time.sleep(_poll_backoff(consecutive_errs))
                continue
            except HTTPError as exc:
                # 401 = 会话瞬时失效（如 MySQL 闪断期间鉴权查询失败，2026-08-31
                # ds5 实证）：re-login 后继续轮询，别把基础设施抖动判成解析失败。
                # 其余 HTTP 状态码不属瞬时抖动，原样上抛；re-login 本身失败也只
                # 记入 last_err，由窗口耗尽统一以 TimeoutError 带出。
                if exc.response is None or exc.response.status_code != 401:
                    raise
                last_err = exc
                consecutive_errs += 1
                try:
                    self.login()
                except Exception as login_exc:
                    last_err = login_exc
                time.sleep(_poll_backoff(consecutive_errs))
                continue
            doc = next((d for d in docs if d["id"] == doc_id), None)
            if doc is None:
                raise ValueError(
                    f"Document {doc_id} not found in dataset {dataset_id}"
                )
            run = str(doc.get("run", ""))
            progress = doc.get("progress", 0)
            # run 双兼容："DONE"（REST 层）/"3"（DB 枚举）；progress 为独立信号
            if _run_done(run) or progress >= 1:
                return doc
            if _run_failed(run) or progress < 0:
                raise RuntimeError(
                    f"Document {doc_id} failed: run={run} progress={progress}"
                )
            time.sleep(5)
        tail = f" 最后一次轮询错误：{last_err}" if last_err else ""
        raise TimeoutError(
            f"Document {doc_id} did not complete within {timeout}s.{tail}"
        )

    # ------------------------------------------------------------------
    # Search (QC)
    # ------------------------------------------------------------------
    def search_datasets(
        self,
        dataset_ids: list[str],
        question: str,
        top_k: int = 10,
        use_kg: bool = False,
        meta_data_filter: dict | None = None,
        rerank_id: str | None = None,
        vector_similarity_weight: float | None = None,
        keyword: bool | None = None,
        page_size: int | None = None,
        rerank_candidates_count: int | None = None,
    ) -> dict:
        payload = {
            "dataset_ids": dataset_ids,
            "question": question,
            "top_k": top_k,
            "use_kg": use_kg,
        }
        if meta_data_filter is not None:
            payload["meta_data_filter"] = _normalize_meta_data_filter(meta_data_filter)
        # P2-9 检索层可选项（v0.27.1 REST 原生支持）：rerank_id=对话层同款
        # rerank 模型行 id；vector_similarity_weight=向量/关键词混合权重；
        # keyword=true 走 LLM 查询改写；page_size 控制返回条数（服务端默认 30）；
        # rerank_candidates_count=精排/分页候选池大小（服务端默认 64——实测多库检索
        # 时目标块可能被 64 池预截断，256 可救回，见 out/retrieval_tuning/）。
        # 缺省一律不传，行为与旧版完全一致。
        for key, val in (
            ("rerank_id", rerank_id),
            ("vector_similarity_weight", vector_similarity_weight),
            ("keyword", keyword),
            ("page_size", page_size),
            ("rerank_candidates_count", rerank_candidates_count),
        ):
            if val is not None:
                payload[key] = val
        resp = self.session.post(f"{API_BASE}/datasets/search", json=payload, timeout=DEFAULT_TIMEOUT)
        return _check(resp)["data"]

    # ------------------------------------------------------------------
    # Tag KB
    # ------------------------------------------------------------------
    def upload_tag_vocab(self, dataset_id: str, vocab_file_path: pathlib.Path) -> dict | list:
        """上传标签词表。返回形状与 :meth:`upload_document` 一致（data 为文档 dict 列表）。"""
        with open(vocab_file_path, "rb") as f:
            resp = self.session.post(
                f"{API_BASE}/datasets/{dataset_id}/documents",
                files={"file": (vocab_file_path.name, f, "application/octet-stream")},
                timeout=UPLOAD_TIMEOUT,
            )
        return _check(resp)["data"]

    def wait_parse(self, dataset_id: str, timeout: float = 300) -> list[dict]:
        deadline = time.time() + timeout
        last_err: Exception | None = None
        consecutive_errs = 0
        while time.time() < deadline:
            try:
                docs = self.list_documents(dataset_id)
                consecutive_errs = 0
            except _TRANSIENT_ERRORS as exc:
                # 与 wait_document 同源：瞬时断连退避重轮询，超时统一上抛
                last_err = exc
                consecutive_errs += 1
                time.sleep(_poll_backoff(consecutive_errs))
                continue
            except HTTPError as exc:
                # 与 wait_document 同源：401 会话瞬时失效 → re-login 续轮询
                if exc.response is None or exc.response.status_code != 401:
                    raise
                last_err = exc
                consecutive_errs += 1
                try:
                    self.login()
                except Exception as login_exc:
                    last_err = login_exc
                time.sleep(_poll_backoff(consecutive_errs))
                continue
            if not docs:
                time.sleep(5)
                continue
            runs = [str(d.get("run", "")) for d in docs]
            progresses = [d.get("progress", 0) for d in docs]
            if all(_run_done(r) or p >= 1 for r, p in zip(runs, progresses)):
                return docs
            if any(_run_failed(r) or p < 0 for r, p in zip(runs, progresses)):
                raise RuntimeError(f"Tag KB parse failed for dataset {dataset_id}")
            time.sleep(5)
        tail = f" 最后一次轮询错误：{last_err}" if last_err else ""
        raise TimeoutError(
            f"Tag KB {dataset_id} did not parse within {timeout}s.{tail}"
        )
