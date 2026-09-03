"""溢洪道图1 补跑：流式 + 双重试（前两轮网关 503/断流）。"""
import base64, json, time
from pathlib import Path
import requests

OUT = Path("/opt/wangjz/ragflow-import/out/vision_qwen27b_test")
IMG = Path("/home/scada/SmartTwinRes-skills/pdfs/05-基础数据与曲线/03-溢洪道信息/溢洪道图1.jpg")
URL = "https://llm.openagp.top:9080/v1/chat/completions"
MODEL = "Qwen3.8-27B-Q4_K_M.gguf"
PROMPT = "请识别图中所有数值，包括：水位(m)、库容(万m³)、泄量(m³/s)等"

for attempt in (1, 2, 3):
    try:
        b64 = base64.b64encode(IMG.read_bytes()).decode()
        payload = {"model": MODEL, "stream": True, "max_tokens": 8192,
                   "messages": [{"role": "user", "content": [
                       {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                       {"type": "text", "text": PROMPT}]}]}
        t0 = time.time()
        chunks = []
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
                for ch in d.get("choices", []):
                    if ch.get("delta", {}).get("content"):
                        chunks.append(ch["delta"]["content"])
        text = "".join(chunks)
        (OUT / "溢洪道图1_raw.txt").write_text(text, encoding="utf-8")
        print(json.dumps({"ok": True, "seconds": round(time.time() - t0, 1), "chars": len(text)}, ensure_ascii=False))
        break
    except Exception as exc:
        print(f"第{attempt}次失败: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
        time.sleep(15)
