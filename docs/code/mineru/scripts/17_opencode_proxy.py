# NOTE: 运行前设置环境变量 OPENCODE_API_KEY。
"""Local header-injection proxy for OpenCode Go (x-opencode-session is
hard-required by the gateway but cannot be set through RAGFlow's
OpenAI-API-Compatible channel, which only sends Authorization).

RAGFlow 侧模型配置（OpenAI-API-Compatible）:
  api_base = http://127.0.0.1:8787/v1
  api_key  = <OPENCODE_API_KEY 的任意占位, 代理用环境变量里的真值>
  model    = deepseek-v4-flash

Usage:
  export OPENCODE_API_KEY=sk-...
  python 17_opencode_proxy.py            # 监听 127.0.0.1:8787
  python 17_opencode_proxy.py --port 8788 --session my-session
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

UPSTREAM = "https://opencode.ai/zen/go/v1"
API_KEY = os.environ.get("OPENCODE_API_KEY", "")
SESSION = os.environ.get("OPENCODE_SESSION", "ragflow-eval-proxy")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _forward(self, method: str):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "x-opencode-session": SESSION,
            "User-Agent": "ragflow-import-proxy/1.0",
            "Accept": self.headers.get("Accept", "application/json"),
        }
        # RAGFlow ensure_v1 会把 base_url 规整为 .../v1，故入站 path 自带 /v1 前缀；
        # UPSTREAM 已含 /v1，剥掉入站前缀避免 /v1/v1/... 404
        path = self.path
        if path.startswith("/v1/"):
            path = path[len("/v1"):]
        url = UPSTREAM + path
        try:
            up = requests.request(method, url, headers=headers, data=body, stream=True, timeout=600)
        except Exception as e:  # noqa: BLE001
            self.send_error(502, str(e))
            return
        self.send_response(up.status_code)
        content_type = up.headers.get("Content-Type", "application/json")
        self.send_header("Content-Type", content_type)
        # SSE 需要逐块转发
        if "text/event-stream" in content_type:
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for chunk in up.iter_content(chunk_size=1024):
                if chunk:
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except Exception:  # noqa: BLE001
                        return
            return
        data = up.content
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        self._forward("GET")

    def do_POST(self):  # noqa: N802
        self._forward("POST")

    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write("[proxy] " + fmt % args + "\n")


def main() -> int:
    global SESSION  # noqa: PLW0603
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--session", default=SESSION)
    args = ap.parse_args()
    SESSION = args.session
    if not API_KEY:
        print("env OPENCODE_API_KEY is required", file=sys.stderr)
        return 1
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"proxy listening on http://127.0.0.1:{args.port}/v1 -> {UPSTREAM} (session={SESSION})")
    print("RAGFlow model config: api_base=http://127.0.0.1:%d/v1  model=deepseek-v4-flash" % args.port)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
