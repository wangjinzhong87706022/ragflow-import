"""MiniMax-M3 复核第一轮 4 图（库容水位对照表/泄流曲线/大坝剖面图/溢洪道图1）——12 图 M3 全覆盖补齐。

走 MiniMax coding plan 的 Anthropic 兼容端点（M3 原生多模态，图像上限 10MB、
请求体上限 64MB——8 图全部原图直发，无需压缩重编码）。

环境变量：
- MINIMAX_API_KEY  coding plan 的 API key（必填）
- MINIMAX_BASE_URL 默认 https://api.minimaxi.com/anthropic（国内计划；
  国际计划改 https://api.minimax.io/anthropic）
- MINIMAX_MODEL    默认 MiniMax-M3
- MINIMAX_PROXY    可选，如 http://192.168.200.71:7897（国际端点被区域拦截时用）

提示词与 run_qwen_8.py / run_mimo_8.py 逐字一致（四方可比）。
幂等：已有非空 <name>_m3_raw.txt 的图跳过。
"""

import base64
import json
import os
import time
from pathlib import Path

import requests

KEY = os.environ["MINIMAX_API_KEY"]
BASE = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/anthropic")
MODEL = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")
PROXY = {"http": os.environ["MINIMAX_PROXY"], "https": os.environ["MINIMAX_PROXY"]} \
    if os.environ.get("MINIMAX_PROXY") else None
URL = f"{BASE}/v1/messages"
PROMPT = ("请完整识别图中全部信息：标题、表格（行列名与全部数值）、文字说明、"
          "名单（人名/单位/职务/电话）、日期、印章文字等，按原图结构逐项输出，"
          "所有数值照抄原图，不要推算、不要编造。")
OUT = Path("/opt/wangjz/ragflow-import/out/vision_8chart_test")
CORPUS = Path("/home/scada/SmartTwinRes-skills/pdfs")

CHARTS = [
    "05-基础数据与曲线/02-大坝剖面图.jpg",
    "05-基础数据与曲线/03-溢洪道信息/溢洪道图1.jpg",
    "05-基础数据与曲线/05-库容水位对照表.jpg",
    "05-基础数据与曲线/06-泄流曲线.jpg",
]

HEADERS = {
    "x-api-key": KEY,               # Anthropic 协议标准头
    "Authorization": f"Bearer {KEY}",  # coding plan token 兼容头
    "anthropic-version": "2023-06-01",
    "Content-Type": "application/json",
}


def probe() -> None:
    """微型文本调用：验证端点/鉴权/模型名。"""
    r = requests.post(URL, headers=HEADERS, proxies=PROXY, timeout=(30, 120), json={
        "model": MODEL, "max_tokens": 64,
        "messages": [{"role": "user", "content": "回复两个字：就绪"}],
    })
    print(f"[probe] {r.status_code} {r.text[:400]}", flush=True)
    r.raise_for_status()
    d = r.json()
    text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    print(f"[probe] 模型应答: {text[:50]} stop_reason={d.get('stop_reason')}", flush=True)


def run_one(path: Path) -> dict:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode()
    payload = {
        "model": MODEL, "max_tokens": 16384,
        "messages": [{"role": "user", "content": [
            {"type": "image",
             "source": {"type": "base64", "media_type": mime, "data": b64}},
            {"type": "text", "text": PROMPT},
        ]}],
    }
    t0 = time.time()
    r = requests.post(URL, headers=HEADERS, proxies=PROXY, timeout=(30, 600), json=payload)
    dt = time.time() - t0
    if not r.ok:
        return {"ok": False, "status": r.status_code, "error": r.text[:300], "seconds": round(dt, 1)}
    d = r.json()
    text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    thinking = "".join(b.get("thinking", "") for b in d.get("content", []) if b.get("type") == "thinking")
    meta = {
        "ok": True, "seconds": round(dt, 1), "stop_reason": d.get("stop_reason"),
        "content_chars": len(text), "thinking_chars": len(thinking),
        "sent_mime": mime, "sent_kb": round(path.stat().st_size / 1024),
        "usage": d.get("usage"),
    }
    (OUT / f"{path.stem}_m3_raw.txt").write_text(text, encoding="utf-8")
    if thinking:
        (OUT / f"{path.stem}_m3_thinking.txt").write_text(thinking, encoding="utf-8")
    return meta


def main() -> None:
    probe()
    for rel in CHARTS:
        p = CORPUS / rel
        out_raw = OUT / f"{p.stem}_m3_raw.txt"
        if out_raw.exists() and out_raw.stat().st_size > 0:
            print(f"=== {p.name} === 已有产物，跳过", flush=True)
            continue
        print(f"=== {p.name} ===", flush=True)
        last: dict = {"ok": False, "chart": p.name, "error": "未执行"}
        for attempt in range(1, 4):
            try:
                last = run_one(p)
                print(json.dumps(last, ensure_ascii=False), flush=True)
                if last["ok"]:
                    break
            except Exception as exc:
                last = {"ok": False, "chart": p.name,
                        "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
                print(f"  第{attempt}次失败: {last['error'][:150]}", flush=True)
                time.sleep(8)
        (OUT / f"{p.stem}_m3_meta.json").write_text(
            json.dumps(last, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(2)
    print("[done] 未完成的图可直接重跑本脚本续传", flush=True)


if __name__ == "__main__":
    main()
