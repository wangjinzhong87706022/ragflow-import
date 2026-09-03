"""mimo-v2.5:free 图像解析质量测试（tokenharbor 网关，经 192.168.200.71:7897 代理）。

与旧管线 vision_extract.py 同口径：同一句数值识别提示词，同一批语料图片，
产物存 <IMG>_raw.txt + <IMG>_meta.json，供与 out/vision/ 的 Qwen3.8-27B 基线对比。
"""

import base64
import io
import json
import os
import time
from pathlib import Path

import requests
from PIL import Image

KEY = os.environ["THK_KEY"]
PROXY = {"http": "http://192.168.200.71:7897", "https": "http://192.168.200.71:7897"}
URL = "https://tokenharbor.ai/v1/chat/completions"
MODEL = "mimo-v2.5:free"
PROMPT = "请识别图中所有数值，包括：水位(m)、库容(万m³)、泄量(m³/s)等"
OUT = Path("/opt/wangjz/ragflow-import/out/vision_mimo_test")
CORPUS = Path("/home/scada/SmartTwinRes-skills/pdfs/05-基础数据与曲线")

IMAGES = [
    CORPUS / "05-库容水位对照表.jpg",
    CORPUS / "06-泄流曲线.jpg",
    CORPUS / "02-大坝剖面图.jpg",
    CORPUS / "03-溢洪道信息/溢洪道图1.jpg",
]


def shrink(path: Path, max_side: int = 2000, quality: int = 85) -> bytes:
    img = Image.open(path)
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def run_one(path: Path) -> dict:
    raw = shrink(path)
    b64 = base64.b64encode(raw).decode()
    payload = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": PROMPT},
            ],
        }],
        "max_tokens": 4096,
    }
    t0 = time.time()
    try:
        r = requests.post(URL, headers={
            "Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
        }, json=payload, proxies=PROXY, timeout=540)
        dt = time.time() - t0
        if not r.ok:
            return {"ok": False, "status": r.status_code, "error": r.text[:300], "seconds": round(dt, 1)}
        d = r.json()
        text = d["choices"][0]["message"]["content"] or ""
        (OUT / f"{path.stem}_raw.txt").write_text(text, encoding="utf-8")
        meta = {
            "ok": True, "seconds": round(dt, 1),
            "usage": d.get("usage"),
            "finish_reason": d["choices"][0].get("finish_reason"),
            "src_bytes": path.stat().st_size,
            "sent_bytes": len(raw),
        }
        (OUT / f"{path.stem}_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "seconds": round(time.time() - t0, 1)}


def main() -> None:
    for p in IMAGES:
        print(f"=== {p.name} ===", flush=True)
        res = run_one(p)
        print(json.dumps(res, ensure_ascii=False), flush=True)
        time.sleep(3)  # 免费档限速缓冲


if __name__ == "__main__":
    main()
