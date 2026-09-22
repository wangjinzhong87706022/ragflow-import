"""OpenAI 兼容端点适配器 —— 替代 rag.llm.chat_model.Base（LLMBundle）。

只需满足 Extractor 对 LLM 的三点契约：
  - .llm_name    : str，缓存键用
  - .max_length  : int，上下文窗口
  - .async_chat(system, message_history, gen_conf) -> str
"""
import asyncio
import os

import requests
import urllib3

urllib3.disable_warnings()


class OpenAICompatLLM:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        max_length: int | None = None,
        timeout: int | None = None,
        temperature: float = 0.1,
    ):
        self.base_url = (base_url or os.environ.get("LLM_API_ENDPOINT", "https://llm.openagp.top:9080/v1")).rstrip("/")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL", "Qwen3.8-27B-Q4_K_M.gguf")
        self.llm_name = self.model
        self.max_length = max_length or int(os.environ.get("LLM_MAX_LENGTH", "32768"))
        self.timeout = timeout or int(os.environ.get("LLM_CALL_TIMEOUT", "600"))
        self.temperature = temperature
        self.verify = os.environ.get("GRAPHRAG_INSECURE", "1") != "1"
        if not self.api_key:
            raise RuntimeError("LLM_API_KEY 未设置（OpenAI 兼容端点的 Bearer token）")

    def _post(self, messages: list[dict], gen_conf: dict) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": float(gen_conf.get("temperature", self.temperature)),
        }
        if gen_conf.get("max_tokens"):
            body["max_tokens"] = gen_conf["max_tokens"]
        resp = requests.post(
            self.base_url + "/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=self.timeout,
            verify=self.verify,
        )
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        content = msg.get("content") or ""
        # 部分推理网关把思维链放在 reasoning_content，content 为空时回退
        if not content and msg.get("reasoning"):
            content = msg["reasoning"]
        return content

    async def async_chat(self, system: str, history: list[dict], gen_conf: dict | None = None) -> str:
        gen_conf = gen_conf or {}
        messages = ([{"role": "system", "content": system}] if system else []) + list(history)
        return await asyncio.to_thread(self._post, messages, gen_conf)

    def chat(self, system: str, history: list[dict], gen_conf: dict | None = None) -> str:
        return self._post(( [{"role": "system", "content": system}] if system else []) + list(history), gen_conf or {})
