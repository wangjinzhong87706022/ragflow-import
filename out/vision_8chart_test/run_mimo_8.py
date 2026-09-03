"""mimo-v2.5 复核剩余 8 图（三方对拍第二轮：已批准基线 + Qwen + mimo）。

复用 vision_mimo_test/run_mimo_test.py 的代理+压缩方案，改动：
- 图清单换成 8 张未复核图；提示词与 run_qwen_8.py 完全一致（可比性）
- 载荷防御：>3MB 或最长边 >2400 时压缩（大坝注册登记证.png 4.7MB 必触发，
  tokenharbor 413 上限实测 5.2MB 即拒）；其余图发原图避免重编码损失
- mimo 是推理模型：finish_reason=length 且 content 过短时视为思维链耗尽，
  自动升 max_tokens 重试
- 幂等：已有非空 <name>_mimo_raw.txt 的图跳过
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
PROMPT = ("请完整识别图中全部信息：标题、表格（行列名与全部数值）、文字说明、"
          "名单（人名/单位/职务/电话）、日期、印章文字等，按原图结构逐项输出，"
          "所有数值照抄原图，不要推算、不要编造。")
OUT = Path("/opt/wangjz/ragflow-import/out/vision_8chart_test")
CORPUS = Path("/home/scada/SmartTwinRes-skills/pdfs")

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
    """>3MB 或最长边 >2400 时压缩，否则发原图。"""
    img = Image.open(path)
    need = path.stat().st_size > 3_000_000 or max(img.size) > 2400
    if not need:
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return path.read_bytes(), mime
    img.thumbnail((2400, 2400))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=88)
    return buf.getvalue(), "image/jpeg"


def run_one(path: Path, max_tokens: int) -> dict:
    raw, mime = image_bytes(path)
    b64 = base64.b64encode(raw).decode()
    payload = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": PROMPT},
            ],
        }],
        "max_tokens": max_tokens,
    }
    t0 = time.time()
    r = requests.post(URL, headers={
        "Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
    }, json=payload, proxies=PROXY, timeout=540)
    dt = time.time() - t0
    if not r.ok:
        return {"ok": False, "status": r.status_code, "error": r.text[:300], "seconds": round(dt, 1)}
    d = r.json()
    text = d["choices"][0]["message"]["content"] or ""
    finish = d["choices"][0].get("finish_reason")
    meta = {
        "ok": True, "seconds": round(dt, 1), "finish_reason": finish,
        "content_chars": len(text), "max_tokens": max_tokens,
        "sent_mime": mime, "sent_kb": round(len(raw) / 1024),
        "usage": d.get("usage"),
    }
    if text:
        (OUT / f"{path.stem}_mimo_raw.txt").write_text(text, encoding="utf-8")
    return meta


def main() -> None:
    for rel in CHARTS:
        p = CORPUS / rel
        out_raw = OUT / f"{p.stem}_mimo_raw.txt"
        if out_raw.exists() and out_raw.stat().st_size > 0:
            print(f"=== {p.name} === 已有产物，跳过", flush=True)
            continue
        print(f"=== {p.name} ===", flush=True)
        last: dict = {"ok": False, "chart": p.name, "error": "未执行"}
        for attempt in range(1, 4):
            max_tokens = 8192 if attempt == 1 else 16384
            try:
                last = run_one(p, max_tokens)
                print(json.dumps(last, ensure_ascii=False), flush=True)
                if last["ok"] and last["content_chars"] > 20:
                    break
                print(f"  内容过短或 finish_reason={last.get('finish_reason')}，升 max_tokens 重试", flush=True)
            except Exception as exc:
                last = {"ok": False, "chart": p.name,
                        "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
                print(f"  第{attempt}次失败: {last['error'][:150]}", flush=True)
                time.sleep(8)
        (OUT / f"{p.stem}_mimo_meta.json").write_text(
            json.dumps(last, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(5)  # 免费档限速缓冲
    print("[done] 未完成的图可直接重跑本脚本续传", flush=True)


if __name__ == "__main__":
    main()
