"""已部署量化模型 Qwen3.8-27B-Q4_K_M.gguf（openagp 网关）视觉解析复测。

与 out/vision_mimo_test/run_mimo_test.py 同口径：同一提示词、同一批语料图、
同一产物结构（<IMG>_raw.txt + <IMG>_meta.json）。差异：原图直发（openagp 无
413 限制，与旧管线 vision_extract.py 一致）、免鉴权。
"""

import base64
import json
import os
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


def run_one(path: Path) -> dict:
    b64 = base64.b64encode(path.read_bytes()).decode()
    payload = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": PROMPT},
            ],
        }],
        "max_tokens": 8192,
    }
    t0 = time.time()
    try:
        r = requests.post(URL, json=payload, timeout=600)
        dt = time.time() - t0
        if not r.ok:
            return {"ok": False, "status": r.status_code, "error": r.text[:300], "seconds": round(dt, 1)}
        d = r.json()
        msg = d["choices"][0]["message"]
        text = msg.get("content") or ""
        (OUT / f"{path.stem}_raw.txt").write_text(text, encoding="utf-8")
        reasoning = msg.get("reasoning_content") or ""
        if reasoning:
            (OUT / f"{path.stem}_thinking.txt").write_text(reasoning, encoding="utf-8")
        meta = {
            "ok": True, "seconds": round(dt, 1),
            "usage": d.get("usage"),
            "finish_reason": d["choices"][0].get("finish_reason"),
            "content_chars": len(text),
            "reasoning_chars": len(reasoning),
            "src_bytes": path.stat().st_size,
        }
        (OUT / f"{path.stem}_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "seconds": round(time.time() - t0, 1)}


def main() -> None:
    for p in IMAGES:
        print(f"=== {p.name} ===", flush=True)
        print(json.dumps(run_one(p), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
