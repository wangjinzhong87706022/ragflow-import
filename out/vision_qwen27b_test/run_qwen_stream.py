"""库容水位对照表 流式重试：token 流式返回，读超时只作用于字节间隔，规避长生成单次阻塞。"""

import base64
import json
import time
from pathlib import Path

import requests

OUT = Path("/opt/wangjz/ragflow-import/out/vision_qwen27b_test")
IMG = Path("/home/scada/SmartTwinRes-skills/pdfs/05-基础数据与曲线/05-库容水位对照表.jpg")
URL = "https://llm.openagp.top:9080/v1/chat/completions"
MODEL = "Qwen3.8-27B-Q4_K_M.gguf"
PROMPT = "请识别图中所有数值，包括：水位(m)、库容(万m³)、泄量(m³/s)等"

b64 = base64.b64encode(IMG.read_bytes()).decode()
payload = {
    "model": MODEL,
    "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        {"type": "text", "text": PROMPT},
    ]}],
    "max_tokens": 6000,
    "stream": True,
}

t0 = time.time()
chunks, reasoning = [], []
finish = None
usage = None
with requests.post(URL, json=payload, stream=True, timeout=(30, 120)) as r:
    print("HTTP", r.status_code, flush=True)
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

text = "".join(chunks)
think = "".join(reasoning)
(OUT / "05-库容水位对照表_raw_stream.txt").write_text(text, encoding="utf-8")
if think:
    (OUT / "05-库容水位对照表_thinking_stream.txt").write_text(think, encoding="utf-8")
print(f"({time.time()-t0:.0f}s) finish={finish} | content {len(text)} 字符 | reasoning {len(think)} 字符")
print("usage:", json.dumps(usage) if usage else "n/a")
