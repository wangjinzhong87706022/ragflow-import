"""
image_triage.py — 扫描件图片质检分诊（VLM 批量判读）

对语料根下的 jpg/png/tif 图片逐张调用 VLM，判读内容类型/书写形态/清晰度，
产出 out/triage/image_triage.jsonl（断点续跑）与 triage_report.md（分诊报告）。

分诊目的（泾惠渠 E:\\bak\\资料收集 评估 2026-09-30）：
  - 空白页   → 不合并、不入库
  - 低清晰度 → 重扫优先，否则 VLM 全文转写
  - 手写体   → VLM 转写文本入库，原图不作 OCR
  - 照片     → 摘要/描述入库
  - 图纸     → 图纸描述 + image_context
  - 正常文本 → 走原定 图片合并 PDF → OCR 路径

密钥只走环境变量 LLM_API_KEY；端点/模型经 LLM_API_ENDPOINT / LLM_MODEL 覆盖。
产物只写 out/，语料根只读。

CLI：
    python image_triage.py --src E:\\bak\\资料收集 --limit 10   # 试点
    python image_triage.py --src E:\\bak\\资料收集               # 全量（断点续跑）
    python image_triage.py --report-only                        # 只重出报告
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import LLM_API_ENDPOINT, LLM_MODEL, OUT_DIR, VISION_CALL_TIMEOUT  # noqa: F401
from jhc_constants import JHC_SRC_ROOT_DEFAULT, JUNK_DIRS, JUNK_FILES
from vision_extract import build_payload, call_vision

IMG_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
DEFAULT_MAX_SIDE = 1600
DEFAULT_WORKERS = 2
RETRY_BACKOFF = (3, 6, 15, 30, 60)  # 429 限流退避秒数，最多 1+5 次

PROMPT = (
    "你是档案扫描件质检员。只返回严格 JSON（不要 markdown 代码块，不要解释）：\n"
    '{"content":"text|table|photo|drawing|blank|mixed",'
    '"writing":"printed|handwritten|mixed|none",'
    '"quality":"good|medium|low",'
    '"readable":true|false,'
    '"issues":["blur|dark|skew|cut_off|noise|faint|handwriting|empty|other"],'
    '"summary":"不超过60字的中文摘要"}\n'
    "判定规则："
    "content=blank 表示整页无任何有效内容（纯白/仅装订痕迹/仅页眉页码）；"
    "quality=low 表示文字模糊、过暗、过曝或分辨率不足，人眼难以辨认；"
    "readable=false 表示主要文字内容无法顺利读出；"
    "手写文字为主体时 writing=handwritten，印刷体混少量手写批注时 writing=mixed；"
    "照片指现场/实物/人物照片，drawing 指图纸、曲线图、示意图；"
    "summary 用中文概括图里是什么（空白页写“空白页”）。"
)

LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 判读
# ---------------------------------------------------------------------------

def prepare_image(path: Path, max_side: int) -> bytes:
    """读图 → 等比缩放到 max_side → JPEG bytes（控制 token 成本）。"""
    from PIL import Image

    with Image.open(path) as im:
        im.seek(0)
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88)
        return buf.getvalue()


def parse_json(text: str) -> dict:
    """剥离代码围栏并取首个 JSON 对象；失败抛 ValueError。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.startswith("json"):
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in: {text[:200]}")
    return json.loads(t[start:end + 1])


def triage_one(path: Path, src_root: Path, endpoint: str, api_key: str,
               model: str, max_side: int) -> dict:
    rel = path.relative_to(src_root).as_posix()
    row: dict = {"rel": rel}
    try:
        img = prepare_image(path, max_side)
        payload = build_payload(img, PROMPT)
        text = ""
        last_exc: Exception | None = None
        for wait in (0, *RETRY_BACKOFF):
            if wait:
                time.sleep(wait)
            try:
                text = call_vision(payload, endpoint, api_key, model=model,
                                   timeout=VISION_CALL_TIMEOUT)
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        row.update(parse_json(text))
        row["ok"] = True
    except Exception as exc:  # noqa: BLE001
        row.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return row


def load_done(jsonl: Path) -> set[str]:
    done: set[str] = set()
    if jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("ok"):
                done.add(row["rel"])
    return done


def iter_images(src: Path):
    for p in sorted(src.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in IMG_EXTS:
            continue
        rel_parts = p.relative_to(src).parts
        if any(part in JUNK_DIRS for part in rel_parts[:-1]):
            continue
        if p.name in JUNK_FILES:
            continue
        yield p


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

BUCKETS = [
    ("A-blank", "空白页（剔除：不合并不入库）",
     lambda r: r.get("content") == "blank"),
    ("B-handwriting", "手写/混写（VLM 全文转写后以文本入库）",
     lambda r: r.get("writing") in ("handwritten", "mixed")),
    ("C-unreadable", "低清晰度不可读（重扫优先，否则 VLM 转写）",
     lambda r: r.get("quality") == "low" or r.get("readable") is False),
    ("D-photo", "照片（生成描述/摘要入库，不作 OCR）",
     lambda r: r.get("content") == "photo"),
    ("E-drawing", "图纸/曲线（图纸描述 + image_context 方式入库）",
     lambda r: r.get("content") == "drawing"),
    ("F-ok", "正常文本/表格（走图片合并 PDF → OCR 路径）",
     lambda r: True),
]

SOLUTIONS = """\
## 处置方案（最终）

| 桶 | 处置 | 管线动作 |
|---|---|---|
| A 空白页 | 直接剔除 | jhc_prepare 合并前按 triage 结果过滤，空白页不进（合并）.pdf |
| B 手写体 | VLM 全文转写 | 转写文本经人工抽样确认后，以 txt/md 形态入库（或 shell 模式挂 chunk），原图不作 OCR；转写置信度低的条目单独标注 |
| C 低清晰度 | 重扫优先 | 无法重扫的用 VLM 转写（与 B 同路径）；转写也失败的降级为摘要入库并标 quality=low |
| D 照片 | 摘要入库 | VLM 生成描述（时间/地点/设备/动作）作为 chunk 文本，原图可留作引用附件，不参与 OCR |
| E 图纸 | 描述 + image_context | 合并 PDF 走 RAGFlow image_context_size 描述路径（同桃曲坡图纸优化经验） |
| F 正常 | 原定路径 | 图片合并（合并）.pdf → naive PDF-OCR 解析 |

## 执行顺序建议

1. 先按本报告过滤 A（空白）与决定 B/C 的转写范围，再重跑 jhc_prepare 合并；
2. B/C/D 类转写用同一 VLM 端点批量出转写稿 → 10% 人工抽样门 → 通过后入库；
3. F 类按 56 个 PDF↔图组去重规则择一保留后正常导入。
"""


def render_report(rows: list[dict], src: Path) -> str:
    ok_rows = [r for r in rows if r.get("ok")]
    err_rows = [r for r in rows if not r.get("ok")]

    lines = ["# 图片质检分诊报告（image_triage）", "",
             f"- 语料根：`{src}`",
             f"- 判读成功：{len(ok_rows)}，失败：{len(err_rows)}", ""]

    lines += ["## 总体统计", "",
              "| content | 张数 |", "|---|---|"]
    by_content: dict[str, int] = {}
    for r in ok_rows:
        by_content[r.get("content", "?")] = by_content.get(r.get("content", "?"), 0) + 1
    for k, v in sorted(by_content.items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} |")

    lines += ["", "| writing | 张数 |", "|---|---|"]
    by_writing: dict[str, int] = {}
    for r in ok_rows:
        by_writing[r.get("writing", "?")] = by_writing.get(r.get("writing", "?"), 0) + 1
    for k, v in sorted(by_writing.items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} |")

    lines += ["", "| quality | 张数 |", "|---|---|"]
    by_q: dict[str, int] = {}
    for r in ok_rows:
        by_q[r.get("quality", "?")] = by_q.get(r.get("quality", "?"), 0) + 1
    for k, v in sorted(by_q.items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} |")

    lines += ["", "## 分诊桶（互斥，按 A→F 优先级归桶）", ""]
    remaining = list(ok_rows)
    for key, title, pred in BUCKETS:
        hit = [r for r in remaining if pred(r)]
        remaining = [r for r in remaining if not pred(r)]
        lines.append(f"### {key}　{title} — {len(hit)} 张")
        lines.append("")
        for r in hit:
            lines.append(f"- `{r['rel']}` — {r.get('summary', '')}")
        lines.append("")

    if err_rows:
        lines += ["## 判读失败清单", ""]
        for r in err_rows:
            lines.append(f"- `{r['rel']}` — {r.get('error', '')}")
        lines.append("")

    lines += [SOLUTIONS]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="扫描件图片质检分诊（VLM）")
    parser.add_argument("--src", type=Path, default=JHC_SRC_ROOT_DEFAULT,
                        help="语料根（只读）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 张，0=不限")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--max-side", type=int, default=DEFAULT_MAX_SIDE)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR / "triage")
    parser.add_argument("--report-only", action="store_true",
                        help="不调 VLM，仅从已有 jsonl 重出报告")
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = args.out_dir / "image_triage.jsonl"
    report = args.out_dir / "triage_report.md"

    def write_report() -> None:
        rows = []
        if jsonl.exists():
            for line in jsonl.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        report.write_text(render_report(rows, args.src), encoding="utf-8")
        print(f"[INFO] 报告已写入 {report}")

    if args.report_only:
        write_report()
        return

    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        print("[ERROR] LLM_API_KEY environment variable is not set")
        sys.exit(1)
    endpoint = LLM_API_ENDPOINT
    model = LLM_MODEL

    done = load_done(jsonl)
    todo = [p for p in iter_images(args.src)
            if p.relative_to(args.src).as_posix() not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"[INFO] 图片共 {len(done)} 已判读，待处理 {len(todo)}；"
          f"endpoint={endpoint} model={model}")

    if not todo:
        write_report()
        return

    t0 = time.time()
    n_ok = n_err = 0
    with open(jsonl, "a", encoding="utf-8") as fh, \
            ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(triage_one, p, args.src, endpoint, api_key,
                               model, args.max_side) for p in todo]
        for i, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            with LOCK:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
            if row.get("ok"):
                n_ok += 1
            else:
                n_err += 1
                print(f"[WARN] {row['rel']}: {row.get('error')}")
            if i % 20 == 0 or i == len(todo):
                rate = i / (time.time() - t0)
                print(f"[INFO] {i}/{len(todo)} ok={n_ok} err={n_err} "
                      f"{rate:.1f} 张/s，剩余约 {(len(todo) - i) / max(rate, 1e-6) / 60:.0f} 分钟")

    write_report()
    print(f"[INFO] 完成：ok={n_ok} err={n_err}，耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
