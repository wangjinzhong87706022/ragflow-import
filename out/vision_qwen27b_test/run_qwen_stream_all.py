"""Qwen3.8-27B-Q4_K_M.gguf 四图复测（流式版）。

改动（针对首轮 600s 读超时失败）：
- stream=True：token 流式返回，读超时只作用于字节间隔，长生成不再被单次阻塞读掐断
- 读超时放宽到 300s（字节间隔口径，正常流式远用不满）
- 失败自动重试一次（网关排队/瞬断）
"""

import base64
import json
import time
from pathlib import Path

import requests

OUT = Path("/opt/wangjz/ragflow-import/out/vision_qwen27b_test")
CORPUS = Path("/home/scada/SmartTwinRes-skills/pdfs/05-基础数据与曲线")
URL = "https://llm.openagp.top:9080/v1/chat/completions"
MODEL = "Qwen3.8-27B-Q4_K_M.gguf"
PROMPT = "请识别图中所有数值，包括：水位(m)、库容(万m³)、泄量(m³/s)等"

IMAGES = [
    CORPUS / "05-库容水位对照表.jpg",
    CORPUS / "06-泄流曲线.jpg",
    CORPUS / "02-大坝剖面图.jpg",
    CORPUS / "03-溢洪道信息/溢洪道图1.jpg",
]


def run_one_stream(path: Path) -> dict:
    b64 = base64.b64encode(path.read_bytes()).decode()
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": PROMPT},
        ]}],
        "max_tokens": 8192,
        "stream": True,
    }
    t0 = time.time()
    chunks, reasoning = [], []
    finish, usage = None, None
    with requests.post(URL, json=payload, stream=True, timeout=(30, 300)) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line or not line.startswith(b"data: "):
                continue
            body = line[6:]
            if body == b"[DONE]":
                break
            try:
                d = json.loads(body)
            except json.JSONDecodeError:
                continue
            if d.get("usage"):
                usage = d["usage"]
            for ch in d.get("choices", []):
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
                delta = ch.get("delta") or {}
                if delta.get("reasoning_content"):
                    reasoning.append(delta["reasoning_content"])
                if delta.get("content"):
                    chunks.append(delta["content"])
    text, think = "".join(chunks), "".join(reasoning)
    (OUT / f"{path.stem}_raw.txt").write_text(text, encoding="utf-8")
    if think:
        (OUT / f"{path.stem}_thinking.txt").write_text(think, encoding="utf-8")
    return {
        "ok": True, "seconds": round(time.time() - t0, 1), "finish_reason": finish,
        "content_chars": len(text), "reasoning_chars": len(think),
        "usage": usage,
    }


def main() -> None:
    for p in IMAGES:
        print(f"=== {p.name} ===", flush=True)
        for attempt in (1, 2):
            try:
                res = run_one_stream(p)
                print(json.dumps(res, ensure_ascii=False), flush=True)
                break
            except Exception as exc:
                print(f"  第{attempt}次失败: {type(exc).__name__}: {str(exc)[:150]}", flush=True)
                if attempt == 2:
                    print(json.dumps({"ok": False, "error": str(exc)[:200]}, ensure_ascii=False), flush=True)
                time.sleep(10)


if __name__ == "__main__":
    main()
