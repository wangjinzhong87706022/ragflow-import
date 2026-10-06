"""
transcribe_mount.py — B/C 扫描页 VLM 转写文本 → 合并 PDF 壳模式挂载（阶段 3-BC）

背景（泾惠渠 E 盘）：
  jhc_prepare 把扫描页图按「父目录 + 去页号主干」合并为 ``<组名>（合并）.pdf``；
  image_triage 分诊出 B（手写/混写）/ C（低清不可读）页 —— 这些页 DeepDOC/OCR
  质量不可用，改由 image_transcribe 离线 VLM 全文转写（人工复核后 --approve）。
  同一合并组内若含 B/C 页，组内**全部**页面都必须有转写文本，否则该 PDF 走
  壳模式（UNSTART + 手动挂 chunk）后会丢页内容 —— 故本工具按组挂载，组内
  每页都要有文字（preflight 断言）。

机制沿用 shell_import 引擎（已验证 v0.27.1：手动挂切片免 parse 即入检索索引；
壳保持 UNSTART 绕开 tag 阶段逐块 LLM 兜底）。严格串行，逐组：

  preflight 全量断言（approve flag / 暂存件在 / 页数=页图数 / 每页有文字）
  → 删同名旧文档 → 上传合并 PDF 保持 UNSTART → patch 11 字段元数据
  → 按 '## 第N页' 小节挂切片 → 探针（每页锚点必现在已挂切片中）→ 记状态

CLI（默认 dry-run，--apply 显式）：
    python transcribe_mount.py                      # dry-run：列计划 + 预检
    python transcribe_mount.py --apply              # 实际挂载（断点续跑）
    python transcribe_mount.py --apply --limit 2    # 冒烟：只挂 2 组
    python transcribe_mount.py --apply --only <子串>  # 限定暂存 rel 子串
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path, PurePosixPath

from config import CORPUS_ROOT, OUT_DIR
from image_transcribe import TRIAGE_JSONL
from jhc_constants import JHC_SRC_ROOT_DEFAULT
from jhc_prepare import build_plan
from ragflow_client import RAGFlowClient
from run_import import row_to_meta_fields
from config import PUBLIC_PEM, RAGFLOW_API_KEY, RAGFLOW_EMAIL, RAGFLOW_PASSWORD
from shell_import import add_chunk, build_chunks, delete_doc

# 分诊/转写产物在 profile 无关的 out/triage（见 image_transcribe.TRIAGE_JSONL 注释）；
# mapping.csv / setup_state.json 则随 profile 走 OUT_DIR（out/jinghuiqu）。
TRIAGE_DIR = TRIAGE_JSONL.parent
TRANSCRIBE_JSONL = TRIAGE_DIR / "transcribe.jsonl"
APPROVE_FLAG = TRIAGE_DIR / "transcribe_approved.flag"
MOUNT_STATE = TRIAGE_DIR / "mount_state.json"
MAPPING_CSV = OUT_DIR / "mapping.csv"
SETUP_STATE = OUT_DIR / "setup_state.json"

MAX_LINES = 15
MAX_CHARS = 800
# 页级转写：整页只有一行（标题页/图签）的合法短页必须保留，
# 故把 build_chunks 的 40 字残片下限降到 8（切片仍带 "## 第N页" 页头，
# 短页在库内可检索且可溯源到页）。
MIN_CHARS = 8
ANCHOR_CHARS = 12


# ---------------------------------------------------------------------------
# 纯函数（供单测）
# ---------------------------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def last_ok_by_rel(rows: list[dict]) -> dict[str, dict]:
    """append-only 语义：只认成功行，同 rel 取最后一条。"""
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("ok"):
            out[r["rel"]] = r
    return out


def is_bc(row: dict) -> bool:
    """与 image_transcribe.is_target 同口径的 B/C 判定（空白页不算 B/C）。"""
    if not row.get("ok") or row.get("content") == "blank":
        return False
    return (row.get("writing") in ("handwritten", "mixed")
            or row.get("quality") == "low"
            or row.get("readable") is False)


def page_anchor(text: str, n: int = ANCHOR_CHARS) -> str | None:
    """取页文本首个有内容的行的前 n 字作探针锚点；无可用锚点返回 None。

    跳过 Markdown 标题符号、表格分隔行（``|---|``）与纯符号行——否则锚点会
    在任意表格切片里都命中，探针形同虚设。
    """
    for line in text.splitlines():
        s = line.strip().lstrip("#").strip()
        if len(s) >= 4 and not re.fullmatch(r"[\s|:\-]+", s):
            return s[:n]
    return None


def pick_bc_groups(src_root: Path, triage_jsonl: Path) -> dict[str, list[str]]:
    """含 B/C 页的合并组 → {暂存合并PDF rel: [页图 rel…（按页序）]}。

    分组与命名复用 jhc_prepare.build_plan（同一份分组/命名逻辑，避免双源漂移）；
    空白页与同哈希副本在 build_plan 内已剔除，与实际合并 PDF 页序一致。
    """
    tri = {r["rel"]: r for r in read_jsonl(triage_jsonl)}
    plan = build_plan(src_root, triage_jsonl)
    out: dict[str, list[str]] = {}
    for out_rel, rels in plan["merges"]:
        if any(is_bc(tri.get(r, {})) for r in rels):
            out[out_rel] = list(rels)
    return out


def build_page_md(pdf_rel: str, page_rels: list[str], texts: dict[str, str]) -> str:
    """逐页拼 '## 第N页' 小节（build_chunks 依此切分）。缺页文本抛 ValueError。"""
    name = PurePosixPath(pdf_rel).name
    parts = [f"# {name}（VLM 转写，共 {len(page_rels)} 页）\n"]
    for i, rel in enumerate(page_rels, 1):
        text = texts.get(rel)
        if not text or not text.strip():
            raise ValueError(f"页 {i} 无转写文本: {rel}")
        parts.append(f"## 第{i}页\n\n{text.strip()}\n")
    return "\n".join(parts)


def preflight(groups: dict[str, list[str]], staging: Path,
              texts: dict[str, str], flag: Path) -> list[str]:
    """全量断言，返回问题清单（空=可挂载）。任何删除动作之前必须为空。"""
    problems: list[str] = []
    if not flag.is_file():
        problems.append(f"人工复核未打标（缺 {flag.name}）：先 python image_transcribe.py --approve")
    for pdf_rel, page_rels in groups.items():
        pdf = staging / pdf_rel
        if not pdf.is_file():
            problems.append(f"暂存件缺失: {pdf_rel}")
            continue
        missing = [r for r in page_rels if not (texts.get(r) or "").strip()]
        if missing:
            problems.append(f"无转写文本 {len(missing)} 页: {pdf_rel}（如 {missing[0]}）")
    return problems


def missing_anchors(page_rels: list[str], texts: dict[str, str],
                    chunks: list[str]) -> list[str]:
    """探针：每页锚点必须出现在已挂切片全文里，否则返回缺失锚点清单。"""
    joined = "\n".join(chunks)
    out: list[str] = []
    for i, rel in enumerate(page_rels, 1):
        a = page_anchor(texts.get(rel, ""))
        if a and a not in joined:
            out.append(f"第{i}页:{a}")
    return out


# ---------------------------------------------------------------------------
# 挂载
# ---------------------------------------------------------------------------

def mount_one(client: RAGFlowClient, ds_id: str, staging: Path, pdf_rel: str,
              page_rels: list[str], texts: dict[str, str], meta: dict) -> dict:
    """单组合挂载：删同名旧文档 → 上传 UNSTART → patch 元数据 → 逐页挂切片 → 探针。"""
    pdf = staging / pdf_rel
    name = pdf.name
    old = client.find_document_by_name(ds_id, name)
    if old:
        delete_doc(ds_id, old["id"])
    result = client.upload_document(ds_id, pdf)
    doc_id = result[0]["id"] if isinstance(result, list) else result["id"]
    client.patch_document(ds_id, doc_id, {**meta, "rel": pdf_rel})
    md = build_page_md(pdf_rel, page_rels, texts)
    chunks = build_chunks(md, max_lines=MAX_LINES, max_chars=MAX_CHARS,
                          min_chars=MIN_CHARS)
    if not chunks:
        raise RuntimeError(f"{pdf_rel}: build_chunks 为空（页文本过短或全空）")
    for c in chunks:
        add_chunk(ds_id, doc_id, c, [])
    miss = missing_anchors(page_rels, texts, chunks)
    if miss:
        raise RuntimeError(f"{pdf_rel}: 探针未覆盖 {len(miss)} 页锚点，如 {miss[:3]}")
    return {"doc_id": doc_id, "chunks": len(chunks), "pages": len(page_rels)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="B/C 转写文本 → 合并 PDF 壳模式挂载")
    ap.add_argument("--src", type=Path, default=JHC_SRC_ROOT_DEFAULT,
                    help="原始资料根（只读；分组逻辑输入）")
    ap.add_argument("--staging", type=Path, default=CORPUS_ROOT,
                    help="暂存语料根（合并 PDF 所在）")
    ap.add_argument("--triage", type=Path, default=TRIAGE_JSONL)
    ap.add_argument("--transcribe", type=Path, default=TRANSCRIBE_JSONL)
    ap.add_argument("--mapping", type=Path, default=MAPPING_CSV)
    ap.add_argument("--setup", type=Path, default=SETUP_STATE)
    ap.add_argument("--state", type=Path, default=MOUNT_STATE)
    ap.add_argument("--apply", action="store_true", help="实际挂载（默认 dry-run）")
    ap.add_argument("--redo", action="store_true",
                    help="重挂已 done 的组（转写文本修正后刷新库内切片）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 组（0=不限）")
    ap.add_argument("--only", action="append", default=[], metavar="SUBSTR",
                    help="仅处理暂存 rel 含此子串的组（可重复）")
    args = ap.parse_args(argv)

    texts = {rel: r.get("text", "") for rel, r in
             last_ok_by_rel(read_jsonl(args.transcribe)).items()}
    groups = pick_bc_groups(args.src, args.triage)
    if args.only:
        groups = {k: v for k, v in groups.items() if any(s in k for s in args.only)}
    if args.limit:
        groups = dict(sorted(groups.items())[: args.limit])

    total_pages = sum(len(v) for v in groups.values())
    print(f"[INFO] 含B/C的合并组={len(groups)} 页={total_pages} "
          f"（暂存根={args.staging}）")

    problems = preflight(groups, args.staging, texts, APPROVE_FLAG)
    mapping: dict[str, dict] = {}
    if args.mapping.is_file():
        with open(args.mapping, encoding="utf-8") as fh:
            mapping = {r["rel"]: r for r in csv.DictReader(fh)}
    for rel in groups:
        if rel not in mapping:
            problems.append(f"mapping.csv 缺行: {rel}")
    setup = json.loads(args.setup.read_text(encoding="utf-8")) if args.setup.is_file() else {}
    if problems:
        print("[ERROR] preflight 未通过：")
        for p in problems[:20]:
            print(f"  - {p}")
        if len(problems) > 20:
            print(f"  …共 {len(problems)} 项")
        sys.exit(1)
    print("[OK] preflight 通过")

    state = json.loads(args.state.read_text(encoding="utf-8")) if args.state.is_file() else {}
    todo = [(rel, pages) for rel, pages in sorted(groups.items())
            if args.redo or state.get(rel, {}).get("status") != "done" or not args.apply]
    if not args.apply:
        for rel, pages in sorted(groups.items())[:15]:
            st = state.get(rel, {}).get("status", "-")
            print(f"  [{st}] {len(pages):>4}页 {rel}")
        if len(groups) > 15:
            print(f"  …共 {len(groups)} 组")
        print("\n[dry-run] 加 --apply 执行挂载")
        return

    client = RAGFlowClient(email=RAGFLOW_EMAIL, password=RAGFLOW_PASSWORD,
                           public_pem_path=PUBLIC_PEM, api_key=RAGFLOW_API_KEY)
    args.state.parent.mkdir(parents=True, exist_ok=True)
    n_ok = n_err = 0
    t0 = time.time()
    for rel, pages in todo:
        ds_k = mapping[rel].get("dataset_key", "")
        ds_id = setup.get(ds_k, {}).get("id")
        if not ds_id:
            print(f"[ERROR] 未找到 {ds_k} 的 dataset_id（先跑 run_setup.py）")
            n_err += 1
            continue
        meta = row_to_meta_fields(mapping[rel])
        try:
            info = mount_one(client, ds_id, args.staging, rel, pages, texts, meta)
            state[rel] = {"status": "done", "dataset_key": ds_k, **info}
            n_ok += 1
            print(f"[OK] {rel} → {info['pages']}页/{info['chunks']}块 "
                  f"({time.time() - t0:.0f}s)")
        except Exception as exc:  # noqa: BLE001
            state[rel] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            n_err += 1
            print(f"[FAIL] {rel}: {exc}")
        args.state.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(f"[完成] ok={n_ok} err={n_err}，耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()