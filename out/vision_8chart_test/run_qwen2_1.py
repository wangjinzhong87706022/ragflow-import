"""Qwen 调优后复测：单图冒烟（中心架构图——第一轮 Qwen 最差、且已有读图标准答案）。

与 run_qwen_8.py 同网关/同模型名/同提示词（逐字一致，可比）；
输出改用 <stem>_qwen2_* 新文件名，不覆盖第一轮证据。幂等可续传。
"""

import base64
import io
import json
import time
from pathlib import Path

import requests
from PIL import Image

OUT = Path("/opt/wangjz/ragflow-import/out/vision_8chart_test")
CORPUS = Path("/home/scada/SmartTwinRes-skills/pdfs")
URL = "https://llm.openagp.top:9080/v1/chat/completions"
MODEL = "Qwen3.8-27B-Q4_K_M.gguf"
PROMPT = ("请完整识别图中全部信息：标题、表格（行列名与全部数值）、文字说明、"
          "名单（人名/单位/职务/电话）、日期、印章文字等，按原图结构逐项输出，"
          "所有数值照抄原图，不要推算、不要编造。")

CHARTS = ["07-管理资料/01-组织架构与责任人/中心架构图.png"]
SUFFIX = "qwen2"  # 第二轮输出后缀


def image_bytes(path: Path) -> tuple[bytes, str]:
    if path.suffix.lower() == ".png" and path.stat().st_size > 2_000_000:
        img = Image.open(path)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=92)
        return buf.getvalue(), "image/jpeg"
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return path.read_bytes(), mime


def run_one_stream(path: Path) -> dict:
    raw, mime = image_bytes(path)
    b64 = base64.b64encode(raw).decode()
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
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
    stem = path.stem
    (OUT / f"{stem}_{SUFFIX}_raw.txt").write_text(text, encoding="utf-8")
    if think:
        (OUT / f"{stem}_{SUFFIX}_thinking.txt").write_text(think, encoding="utf-8")
    meta = {
        "ok": True, "seconds": round(time.time() - t0, 1), "finish_reason": finish,
        "content_chars": len(text), "reasoning_chars": len(think),
        "sent_mime": mime, "sent_kb": round(len(raw) / 1024),
        "usage": usage,
    }
    (OUT / f"{stem}_{SUFFIX}_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main() -> None:
    for rel in CHARTS:
        p = CORPUS / rel
        out_raw = OUT / f"{p.stem}_{SUFFIX}_raw.txt"
        if out_raw.exists() and out_raw.stat().st_size > 0:
            print(f"=== {p.name} === 已有产物，跳过", flush=True)
            continue
        print(f"=== {p.name} ===", flush=True)
        for attempt in range(1, 6):
            try:
                res = run_one_stream(p)
                print(json.dumps(res, ensure_ascii=False), flush=True)
                break
            except Exception as exc:
                print(f"  第{attempt}次失败: {type(exc).__name__}: {str(exc)[:150]}", flush=True)
                if attempt == 5:
                    print(json.dumps({"ok": False, "chart": p.name,
                                      "error": str(exc)[:200]}, ensure_ascii=False), flush=True)
                time.sleep(10)
    print("[done]", flush=True)


if __name__ == "__main__":
    main()
