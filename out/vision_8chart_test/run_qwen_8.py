"""Qwen3.8-27B 复核剩余 8 图（三方对拍第二轮：已批准基线 + Qwen + mimo）。

复用 vision_qwen27b_test/run_qwen_stream_all.py 的流式+重试方案，改动：
- 图清单换成 8 张未复核图（05/07 目录，相对 CORPUS_ROOT）
- 提示词改通用全文识别（新图型：名单/架构图/证件/物资表，纯数值提示词不适用）
- 大坝注册登记证.png 4.7MB 重编码为 JPEG q92（保持原分辨率，规避网关载荷上限）
- 幂等：已有非空 <name>_qwen_raw.txt 的图跳过（网关抖动后可直接重跑）
- 每图最多 5 次尝试（上轮实测单图曾需 7 次，不足时重跑本脚本续传）
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

CHARTS = [
    "05-基础数据与曲线/01-水库基本信息/水库基本信息.jpg",
    "05-基础数据与曲线/03-溢洪道信息/溢洪道图2.jpg",
    "05-基础数据与曲线/07-抢险物资信息/物资图1.jpg",
    "05-基础数据与曲线/07-抢险物资信息/物资图2.jpg",
    "07-管理资料/01-组织架构与责任人/三个责任人.jpg",
    "07-管理资料/01-组织架构与责任人/中心架构图.png",
    "07-管理资料/02-注册登记证/大坝注册登记证.png",
    "07-管理资料/03-供配水计划/各部门用水需求.jpg",
]


def image_bytes(path: Path) -> tuple[bytes, str]:
    """返回 (字节, mime)。大 PNG 重编码为 JPEG q92 保持原分辨率。"""
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
    (OUT / f"{stem}_qwen_raw.txt").write_text(text, encoding="utf-8")
    if think:
        (OUT / f"{stem}_qwen_thinking.txt").write_text(think, encoding="utf-8")
    meta = {
        "ok": True, "seconds": round(time.time() - t0, 1), "finish_reason": finish,
        "content_chars": len(text), "reasoning_chars": len(think),
        "sent_mime": mime, "sent_kb": round(len(raw) / 1024),
        "usage": usage,
    }
    (OUT / f"{stem}_qwen_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main() -> None:
    for rel in CHARTS:
        p = CORPUS / rel
        out_raw = OUT / f"{p.stem}_qwen_raw.txt"
        if out_raw.exists() and out_raw.stat().st_size > 0:
            print(f"=== {p.name} === 已有产物，跳过", flush=True)
            continue
        print(f"=== {p.name} ===", flush=True)
        ok = False
        for attempt in range(1, 6):
            try:
                res = run_one_stream(p)
                print(json.dumps(res, ensure_ascii=False), flush=True)
                ok = True
                break
            except Exception as exc:
                print(f"  第{attempt}次失败: {type(exc).__name__}: {str(exc)[:150]}", flush=True)
                if attempt == 5:
                    print(json.dumps({"ok": False, "chart": p.name,
                                      "error": str(exc)[:200]}, ensure_ascii=False), flush=True)
                time.sleep(10)
        time.sleep(2)
    print("[done] 未完成的图可直接重跑本脚本续传", flush=True)


if __name__ == "__main__":
    main()
