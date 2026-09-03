"""
photo_triage.py — 09-图像与多媒体 照片堆 VLM 分诊摸底（只读）

背景：5A0287D3….png 证实"洪水现场照片"堆里混有文件扫描件（长江委《汛（旱）情通报》
第27期，含手写批示），被 SKIP_DIRS 整目录排除而漏掉。本脚本对 01-洪水现场照片 下
全部图片逐一做 VLM 分类（文件扫描件/图表图纸/现场照片/其他），产出人工复核报告，
供决定哪些重新归档入池。只读语料、只写 out/photo_triage/，不改任何 KB 状态。
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import config  # type: ignore[attr-defined]
from config import (  # type: ignore[attr-defined, assignment]
    LLM_API_ENDPOINT, LLM_MODEL, VISION_CALL_TIMEOUT,
)
from vision_extract import build_payload, call_vision

CORPUS_ROOT = config.CORPUS_ROOT
OUT_DIR = config.OUT_DIR

TRIAGE_ROOT = CORPUS_ROOT / "09-图像与多媒体" / "01-洪水现场照片"
TRIAGE_EXTS = (".jpg", ".jpeg", ".png")
CATEGORIES = ("文件扫描件", "图表图纸", "现场照片", "其他")

TRIAGE_PROMPT = (
    "你是水库档案整理助手。判断这张图片属于哪一类：\n"
    "- 文件扫描件：通知、通报、传真、信函、表格、纪要等以文字文档为主的扫描/翻拍件\n"
    "- 图表图纸：过程线、水位库容曲线、工程图纸等\n"
    "- 现场照片：实景拍摄的照片\n"
    "- 其他：无法判断\n"
    '只输出一行 JSON：{"category": "四类之一", "title_hint": "可见的文件标题或主题，无则空串", '
    '"date_hint": "可见日期，无则空串", "handwriting": 是否有手写批示或手写标注, '
    '"confidence": 0到1的小数}'
)

# 关键词归一：VLM 不总按四类原词回答（评测教训：输出键值漂移是常态）
_CATEGORY_KEYWORDS = (
    ("文件扫描件", ("扫描", "文件", "文档", "通报", "通知", "传真", "信函", "表格")),
    ("图表图纸", ("图表", "图纸", "曲线", "过程线", "剖面")),
    ("现场照片", ("照片", "现场", "实拍", "摄影")),
)


def iter_triage_images(root: Path | None = None) -> list[Path]:
    """枚举分诊范围内的图片，跨子目录按相对路径稳定排序。"""
    root = TRIAGE_ROOT if root is None else root
    if not root.exists():
        return []
    return sorted(
        (p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in TRIAGE_EXTS),
        key=lambda p: str(p.relative_to(root)),
    )


def parse_verdict(text: str) -> dict | None:
    """
    从 VLM 输出中宽容提取分类 JSON；解析失败返回 None（调用方记为失败，不猜）。

    类别归一到 CATEGORIES 四类：原词命中直接用，否则按关键词映射，仍不中归"其他"。
    confidence 截断到 [0, 1]。
    """
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None

    category = str(raw.get("category", "")).strip()
    if category not in CATEGORIES:
        category = next(
            (cat for cat, words in _CATEGORY_KEYWORDS if any(w in category for w in words)),
            "其他",
        )

    try:
        confidence = min(1.0, max(0.0, float(raw.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "category": category,
        "title_hint": str(raw.get("title_hint", "")).strip(),
        "date_hint": str(raw.get("date_hint", "")).strip(),
        "handwriting": bool(raw.get("handwriting", False)),
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# 分类执行与产出
# ---------------------------------------------------------------------------

_CSV_COLS = ("rel", "category", "title_hint", "date_hint", "handwriting", "confidence", "error")


def _result_name(rel: str) -> str:
    return rel.replace("/", "__") + ".json"


# 网关对请求体设限：11–15.8MB 原图（base64 后 15–21MB）整批 400，10.9MB 及以下实测可过
MAX_IMAGE_BYTES = int(os.getenv("TRIAGE_MAX_IMAGE_BYTES", str(8 * 1024 * 1024)))


def prepare_image_bytes(data: bytes, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    """超过 max_bytes 的图降采样重编码到限制以内；小图原样返回（不引入重编码损失）。"""
    if len(data) <= max_bytes:
        return data
    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        quality = 85
        while len(data) > max_bytes and img.width > 800:
            img = img.resize((img.width // 2, img.height // 2))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=quality)
            data = buf.getvalue()
            quality = max(60, quality - 10)
    return data


def classify_image(
    image_bytes: bytes,
    endpoint: str,
    api_key: str,
    model: str = LLM_MODEL,
    timeout: int = VISION_CALL_TIMEOUT,
    transport=requests.post,
) -> dict:
    """单图分类：VLM 调用 + 解析；输出无法解析为 JSON 时抛 ValueError（由 run 计入失败）。"""
    payload = build_payload(prepare_image_bytes(image_bytes), TRIAGE_PROMPT)
    text = call_vision(payload, endpoint, api_key, model=model, timeout=timeout, transport=transport)
    verdict = parse_verdict(text)
    if verdict is None:
        raise ValueError(f"VLM 输出无法解析为分类 JSON: {text[:120]!r}")
    return verdict


def _write_csv(triage_dir: Path, entries: list[dict]) -> None:
    with open(triage_dir / "results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLS)
        writer.writeheader()
        writer.writerows({k: e.get(k, "") for k in _CSV_COLS} for e in entries)


def _write_report(triage_dir: Path, entries: list[dict], counts: dict) -> None:
    lines = ["# 洪水现场照片 VLM 分诊报告\n",
             "- 范围：09-图像与多媒体/01-洪水现场照片（2021年9月 + 2021年10月）",
             f"- 总数 {len(entries)}；"
             + "、".join(f"{c} {counts[c]}" for c in (*CATEGORIES, "失败") if counts.get(c))
             + "\n"]
    for cat in (*CATEGORIES, "失败"):
        group = [e for e in entries if e["category"] == cat]
        if not group:
            continue
        lines.append(f"## {cat}（{len(group)}）\n")
        for e in group:
            if cat == "失败":
                lines.append(f"- `{e['rel']}` — {e.get('error', '')}")
                continue
            marks = []
            if e.get("handwriting"):
                marks.append("手写批示")
            if e.get("date_hint"):
                marks.append(e["date_hint"])
            suffix = f"（{'，'.join(marks)}）" if marks else ""
            lines.append(
                f"- `{e['rel']}` — {e.get('title_hint', '')}{suffix} confidence={e.get('confidence', 0):.2f}"
            )
        lines.append("")
    lines.append("---")
    lines.append("*本报告仅供人工复核：入池/归档决策须人工确认后另行执行（语料根目录只读）。*")
    (triage_dir / "triage_report.md").write_text("\n".join(lines), encoding="utf-8")


def run(
    api_key: str,
    endpoint: str = LLM_API_ENDPOINT,
    model: str = LLM_MODEL,
    transport=requests.post,
    limit: int = 0,
    force: bool = False,
) -> dict | None:
    """批量分类，逐图落盘（断点续跑），产出 results.csv + triage_report.md。"""
    if not api_key:
        print("[ERROR] LLM_API_KEY environment variable is not set")
        return None

    images = iter_triage_images()
    if limit:
        images = images[:limit]
    triage_dir = OUT_DIR / "photo_triage"
    triage_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    counts: dict[str, int] = {}
    for i, img in enumerate(images, 1):
        rel = str(img.relative_to(TRIAGE_ROOT))
        res_path = triage_dir / _result_name(rel)
        if res_path.exists() and not force:
            entry = json.loads(res_path.read_text(encoding="utf-8"))
        else:
            entry = None
            for attempt in range(3):
                try:
                    verdict = classify_image(
                        img.read_bytes(), endpoint, api_key, model=model, transport=transport
                    )
                    entry = {"rel": rel, **verdict, "error": ""}
                    break
                except Exception as exc:
                    if attempt == 2:
                        entry = {
                            "rel": rel, "category": "失败", "title_hint": "", "date_hint": "",
                            "handwriting": False, "confidence": 0.0, "error": str(exc),
                        }
                    else:
                        time.sleep(2 ** attempt)
            res_path.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")

        entry["rel"] = rel
        entries.append(entry)
        counts[entry["category"]] = counts.get(entry["category"], 0) + 1
        print(f"[{i}/{len(images)}] {rel} → {entry['category']}")

    _write_csv(triage_dir, entries)
    _write_report(triage_dir, entries, counts)
    print(f"\n分类完成：{counts}")
    print(f"报告: {triage_dir / 'triage_report.md'}")
    return {"total": len(entries), "counts": counts}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="洪水现场照片 VLM 分诊摸底（只读语料）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 张，0 不限制")
    parser.add_argument("--force", action="store_true", help="忽略已有结果全部重跑")
    args = parser.parse_args()

    run(
        api_key=os.environ.get("LLM_API_KEY", ""),
        limit=args.limit,
        force=args.force,
    )


if __name__ == "__main__":
    main()
