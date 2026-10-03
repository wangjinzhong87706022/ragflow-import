"""
image_transcribe.py — B/C 类扫描页 VLM 全文转写（手写/混写 + 低清不可读）

从 out/triage/image_triage.jsonl 选目标（与 triage_report 分诊桶同口径：
A 空白剔除后，B 手写/混写 ∪ C 低清不可读），逐页调 VLM 全文转写为 Markdown，
产出 out/triage/transcribe.jsonl（断点续跑）与 transcribe_report.md
（含 10% 确定性抽样复核门 + 低置信全量清单）。

处置方案依据 triage_report.md「处置方案（最终）」：
  B 手写体 → VLM 全文转写，转写文本经人工抽样确认后以 shell 模式挂 chunk，
             原图不作 OCR（转写吃原图，合并 PDF 仅作引用锚点）；
  C 低清   → 同 B 路径（重扫优先，无法重扫的走转写）。

目标集先过 jhc_prepare._dedup_identical_images：同目录同哈希副本（xx(1).jpg）
只转写保留名，副本行不产孤儿转写（合并 PDF 里只有保留名那张图）。

密钥只走环境变量 LLM_API_KEY；端点/模型经 LLM_API_ENDPOINT / LLM_MODEL 覆盖
（全量分诊实测 agnes 4 并发最优、0.2~0.3 张/s）。产物只写 out/，语料根只读。

CLI：
    python image_transcribe.py --limit 5      # 试点（先跑 5 张看质量）
    python image_transcribe.py                # 全量（断点续跑）
    python image_transcribe.py --report-only  # 只重出报告
    python image_transcribe.py --approve      # 人工抽样复核通过后打标
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import LLM_API_ENDPOINT, LLM_MODEL, OUT_DIR, VISION_CALL_TIMEOUT  # noqa: F401
from image_triage import prepare_image
from jhc_constants import JHC_SRC_ROOT_DEFAULT
from jhc_prepare import _dedup_identical_images
from vision_extract import build_payload, call_vision

TRIAGE_JSONL = OUT_DIR / "triage" / "image_triage.jsonl"

DEFAULT_MAX_SIDE = 2000     # 密集手写表格比分诊的 1600 口径更需要细节
DEFAULT_WORKERS = 4         # agnes 实测 4 并发最优
DEFAULT_SAMPLE_PCT = 10     # 人工抽样复核门比例（分诊报告执行顺序建议）
MAX_TOKENS = 8192           # 密集表格转写易触网关默认 4k 截断
RETRY_BACKOFF = (3, 6, 15, 30, 60)   # 429 限流退避秒数，最多 1+5 次

# 两段式信封（CONF 头 + Markdown 正文）而非 JSON：长文本 JSON 信封下
# 模型偶发不转义换行/整个不套信封（全量首跑 7/135 失败全是这类格式错），
# 两段式把失败面缩到"首行没按 CONF"，且该情况可安全降级为低置信兜底。
TRANSCRIBE_PROMPT = (
    "你是档案转写员。把图片中的全部文字忠实转写为 Markdown。\n"
    "输出格式（严格两段式：不要 JSON、不要代码块围栏、不要任何解释）：\n"
    "第一行：CONF: HIGH 或 CONF: LOW，后跟 | NOTES: <辨认困难/缺损说明，无则写 ->\n"
    "第二行起：Markdown 转写正文。\n"
    "示例：\n"
    "CONF: LOW | NOTES: 签名潦草难以辨认\n"
    "# 泄洪排沙调度通知单\n"
    "日期：2025年6月7日　天气：晴\n"
    "转写规则：\n"
    "1. 表格转成 Markdown 表格；记录单/票据保留标题、编号、日期、栏目名与填写值的对应关系；\n"
    "2. 手写字迹逐字辨认，无法确认的字用〔?〕占位，禁止臆造或补全内容；\n"
    "3. 数字、编号、日期、单位逐位照抄（注意 0/O、1/l、小数点与单位）；\n"
    "4. 签名照写姓名，看不清写〔签名?〕；印章写〔红章：<单位名>〕；\n"
    "5. 按原文顺序输出，不总结、不评论、不改写；空白区域跳过；\n"
    "6. CONF: LOW 仅在整页大面积难以辨认或〔?〕很多时使用，NOTES 说明原因；\n"
    "7. 表格每个单元格独立照抄：认不出的单元格整格填〔?〕，"
    "**严禁把上一行的数值复制到下一行充数**——宁可整行〔?〕也绝不编造重复值。"
)

LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 目标选择
# ---------------------------------------------------------------------------

def is_target(row: dict) -> bool:
    """与 triage_report 分诊桶同口径：A 空白剔除后，B（手写/混写）∪ C（低清不可读）。"""
    if not row.get("ok") or row.get("content") == "blank":
        return False
    return (row.get("writing") in ("handwritten", "mixed")
            or row.get("quality") == "low"
            or row.get("readable") is False)


def bucket_of(row: dict) -> str:
    """B 优先（与分诊报告桶序一致）；其余目标归 C。"""
    if row.get("writing") in ("handwritten", "mixed"):
        return "B"
    return "C"


def load_targets(triage_jsonl: Path, src_root: Path) -> tuple[list[dict], int]:
    """读分诊 JSONL → 目标行列表；同哈希副本去重（只留保留名）。

    返回 (targets, n_dup_skipped)。targets 行含 bucket 字段（B/C）。
    """
    rows: list[dict] = []
    if not triage_jsonl.exists():
        return [], 0
    for line in triage_jsonl.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if is_target(row):
            rows.append(row)
    if not rows:
        return [], 0
    kept, dup_skips = _dedup_identical_images(src_root, [r["rel"] for r in rows])
    kept_set = set(kept)
    targets = []
    for r in rows:
        if r["rel"] in kept_set:
            r = dict(r)
            r["bucket"] = bucket_of(r)
            targets.append(r)
    return targets, len(dup_skips)


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


def select_todo(targets: list[dict], done: set[str], only: str | list[str] = "",
                redo: bool = False, limit: int = 0) -> list[dict]:
    """断点续跑 + --only/--redo 子集筛选（only 可为多个子串，命中任一即可）+ --limit 截断。"""
    subs = [only] if isinstance(only, str) else list(only)
    subs = [s for s in subs if s]
    todo = [t for t in targets if redo or t["rel"] not in done]
    if subs:
        todo = [t for t in todo if any(s in t["rel"] for s in subs)]
    if limit:
        todo = todo[:limit]
    return todo


# ---------------------------------------------------------------------------
# 判读
# ---------------------------------------------------------------------------

def _strip_fence(s: str) -> str:
    """剥离 ``` 代码块围栏（模型偶发套壳）；无围栏原样返回。"""
    t = s.strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_transcription(raw: str) -> dict:
    """解析转写响应 → {text, low_conf, notes}。

    首选 CONF 头两段式；兼容旧 JSON 信封（已入库行/模型坚持套 JSON 时）。
    首行不合规 → 全文当正文并标 low_conf=true（notes 注明兜底），强制进抽查清单。
    """
    s = _strip_fence(raw)
    if not s:
        raise ValueError("VLM 返回为空")
    if s.startswith("{"):
        try:
            data = json.loads(s[s.find("{"): s.rfind("}") + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 信封解析失败: {exc}") from exc
        body = data.get("text") if isinstance(data, dict) else None
        if not isinstance(body, str) or not body.strip():
            raise ValueError("VLM 未返回 text 字段或为空")
        return {"text": body, "low_conf": bool(data.get("low_conf", False)),
                "notes": str(data.get("notes", "") or "")}
    first, _, rest = s.partition("\n")
    m = re.match(r"^CONF:\s*(HIGH|LOW)\b", first, re.IGNORECASE)
    if m:
        body = rest.strip()
        if not body:
            raise ValueError("CONF 头后无正文")
        notes = ""
        if "NOTES:" in first:
            notes = first.split("NOTES:", 1)[1].strip().lstrip("|").strip()
            if notes in ("-", "—", "无", "->"):
                notes = ""
        return {"text": body, "low_conf": m.group(1).upper() == "LOW",
                "notes": notes}
    return {"text": s, "low_conf": True,
            "notes": "未返回 CONF 头，按低置信抽查"}


def transcribe_one(path: Path, src_root: Path, bucket: str, endpoint: str,
                   api_key: str, model: str, max_side: int,
                   transport=None, backoff=RETRY_BACKOFF) -> dict:
    rel = path.relative_to(src_root).as_posix()
    row: dict = {"rel": rel, "bucket": bucket, "endpoint": endpoint, "model": model}
    try:
        img = prepare_image(path, max_side)
        payload = build_payload(img, TRANSCRIBE_PROMPT)
        kwargs: dict = {"max_tokens": MAX_TOKENS}
        if transport is not None:
            kwargs["transport"] = transport
        text = ""
        last_exc: Exception | None = None
        for wait in (0, *backoff):
            if wait:
                time.sleep(wait)
            try:
                text = call_vision(payload, endpoint, api_key, model=model,
                                   timeout=VISION_CALL_TIMEOUT, **kwargs)
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        data = parse_transcription(text)
        row.update({
            "ok": True,
            "text": data["text"],
            "low_conf": bool(data["low_conf"]),
            "notes": data["notes"],
        })
    except Exception as exc:  # noqa: BLE001
        row.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return row


# ---------------------------------------------------------------------------
# 报告（10% 抽样复核门）
# ---------------------------------------------------------------------------

def sample_rows(rows: list[dict], pct: int) -> list[dict]:
    """确定性抽样：sha256(rel) 落在 [0, pct) 的行，跨次运行结果稳定。"""
    picked = []
    for r in sorted((r for r in rows if r.get("ok")), key=lambda r: r["rel"]):
        h = int(hashlib.sha256(r["rel"].encode("utf-8")).hexdigest(), 16)
        if h % 100 < pct:
            picked.append(r)
    return picked


def render_report(rows: list[dict], targets: list[dict], src: Path,
                  sample_pct: int, endpoint: str, model: str) -> str:
    # append-only 语义：同一 rel 曾失败后重试成功 → 只认成功行（取最后一条）
    ok_by_rel: dict[str, dict] = {}
    for r in rows:
        if r.get("ok"):
            ok_by_rel[r["rel"]] = r
    ok_rows = list(ok_by_rel.values())
    err_rows = [r for r in rows if not r.get("ok") and r["rel"] not in ok_by_rel]
    low_rows = [r for r in ok_rows if r.get("low_conf")]
    n_b = sum(1 for t in targets if t.get("bucket") == "B")
    n_c = sum(1 for t in targets if t.get("bucket") == "C")
    avg_len = (sum(len(r.get("text", "")) for r in ok_rows) / len(ok_rows)) if ok_rows else 0
    # provenance：优先行内 endpoint/model（运行时写入），缺省回退调用方参数
    eps = sorted({r["endpoint"] for r in ok_rows if r.get("endpoint")})
    models = sorted({r["model"] for r in ok_rows if r.get("model")})
    ep_str = ", ".join(eps) if eps else endpoint
    model_str = ", ".join(models) if models else model

    lines = [
        "# B/C 类扫描页转写报告（image_transcribe）", "",
        f"- 语料根：`{src}`",
        f"- 端点/模型：{ep_str} / {model_str}",
        f"- 转写目标：{len(targets)} 页（B 手写/混写 {n_b}，C 低清不可读 {n_c}）",
        f"- 已转写：{len(ok_rows)} 成功 / {len(err_rows)} 失败，"
        f"低置信 {len(low_rows)}，平均 {avg_len:.0f} 字", "",
    ]

    if err_rows:
        lines += ["## 失败清单（排查后重跑即可断点续跑）", ""]
        lines += [f"- `{r['rel']}` — {r.get('error', '')}" for r in err_rows]
        lines.append("")

    if low_rows:
        lines += [f"## 低置信清单（low_conf=true，共 {len(low_rows)} 页，需重点抽查）", ""]
        for r in low_rows:
            lines += [f"### `{r['rel']}`",
                      f"> notes: {r.get('notes', '') or '—'}", "",
                      "```markdown", r.get("text", ""), "```", ""]
    low_rels = {r["rel"] for r in low_rows}

    sampled = [r for r in sample_rows(ok_rows, sample_pct) if r["rel"] not in low_rels]
    lines += [f"## 抽样复核（{sample_pct}% 确定性抽样，共 {len(sampled)} 页"
              + (f"，另 {len(low_rows)} 页已在低置信清单" if low_rows else "") + "）", ""]
    for r in sampled:
        lines += [f"### `{r['rel']}`", "",
                  "```markdown", r.get("text", ""), "```", ""]

    lines += ["---",
              "*人工复核通过后执行：`python image_transcribe.py --approve`（写 "
              "transcribe_approved.flag；任何重跑后需重新复核/打标）。*", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="B/C 类扫描页 VLM 全文转写")
    parser.add_argument("--src", type=Path, default=JHC_SRC_ROOT_DEFAULT,
                        help="语料根（只读）")
    parser.add_argument("--triage", type=Path, default=TRIAGE_JSONL,
                        help="分诊 JSONL（目标选择依据）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 页，0=不限")
    parser.add_argument("--only", action="append", default=[], metavar="SUBSTR",
                        help="仅处理 rel 含此子串的目标（可重复多个；与 --redo 搭配做子集重转）")
    parser.add_argument("--redo", action="store_true",
                        help="忽略断点续跑，重转所选目标（配合 --only 限定范围）")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--max-side", type=int, default=DEFAULT_MAX_SIDE)
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE_PCT,
                        help="抽样复核比例百分数")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR / "triage")
    parser.add_argument("--report-only", action="store_true",
                        help="不调 VLM，仅从已有 jsonl 重出报告")
    parser.add_argument("--approve", action="store_true",
                        help="人工抽样复核通过：创建 transcribe_approved.flag 并退出")
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = args.out_dir / "transcribe.jsonl"
    report = args.out_dir / "transcribe_report.md"
    flag = args.out_dir / "transcribe_approved.flag"

    if args.approve:
        flag.touch()
        print(f"[INFO] 已打标：{flag}")
        return

    if not args.triage.is_file():
        print(f"[ERROR] 未找到分诊结果 {args.triage}（先跑 image_triage.py）")
        sys.exit(1)

    targets, n_dup = load_targets(args.triage, args.src)
    if n_dup:
        print(f"[INFO] 同哈希副本 {n_dup} 页已跳过（只转写保留名）")

    def rows_all() -> list[dict]:
        rows: list[dict] = []
        if jsonl.exists():
            for line in jsonl.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return rows

    def write_report() -> None:
        report.write_text(render_report(rows_all(), targets, args.src,
                                        args.sample, endpoint_live, model_live),
                          encoding="utf-8")
        print(f"[INFO] 报告已写入 {report}")

    if args.report_only:
        endpoint_live, model_live = LLM_API_ENDPOINT, LLM_MODEL
        write_report()
        return

    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        print("[ERROR] LLM_API_KEY environment variable is not set")
        sys.exit(1)
    endpoint_live, model_live = LLM_API_ENDPOINT, LLM_MODEL

    done = load_done(jsonl)
    todo = select_todo(targets, done, only=args.only, redo=args.redo,
                       limit=args.limit)
    print(f"[INFO] 目标 {len(targets)}，已转写 {len(done)}，待处理 {len(todo)}；"
          f"endpoint={endpoint_live} model={model_live}")
    if flag.exists():
        print(f"[WARN] 已存在 {flag.name}，但本次有新增转写——完成后需重新复核并打标")

    if not todo:
        write_report()
        return

    t0 = time.time()
    n_ok = n_err = 0
    with open(jsonl, "a", encoding="utf-8") as fh, \
            ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(transcribe_one, args.src / t["rel"], args.src,
                               t["bucket"], endpoint_live, api_key, model_live,
                               args.max_side) for t in todo]
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
                      f"{rate:.2f} 页/s，剩余约 {(len(todo) - i) / max(rate, 1e-6) / 60:.0f} 分钟")

    write_report()
    print(f"[INFO] 完成：ok={n_ok} err={n_err}，耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
