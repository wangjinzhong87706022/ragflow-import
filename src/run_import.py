"""run_import.py — Task 8: three-step import (upload → metadata → parse) + resume support."""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from config import (
    CORPUS_ROOT,
    DATASETS,
    OUT_DIR,
    RAGFLOW_EMAIL,
    RAGFLOW_PASSWORD,
    PUBLIC_PEM,
    RAGFLOW_API_KEY,
    normalize_rel,
)
from corpus import read_mapping_csv
from ragflow_client import RAGFlowClient, _run_done

# ---------------------------------------------------------------------------
# 解析等待窗（秒）
# GraphRAG(light) + resolution 每个块 ≥3 次 LLM 调用，还要跑实体消解批——
# 600s 统一窗口曾把正常构建误判成超时失败（review P1-3）。
# ---------------------------------------------------------------------------
WAIT_TIMEOUT_GRAPH_RAG = 1800


def resolve_wait_timeout(ds_key: str) -> int:
    """按数据集是否启用 GraphRAG 决定解析等待窗。

    统一 1800s：GraphRAG(light)+resolution 每块 ≥3 次 LLM 调用，ds5 的 tag
    注入阶段同样耗时（早期 FAIL 即卡在 tag 步骤），600s 曾把正常构建误判成
    超时失败。后台串行跑时放宽窗口更稳，宁可多等也不误判。
    """
    return WAIT_TIMEOUT_GRAPH_RAG

# ---------------------------------------------------------------------------
# Metadata field mapping
# ---------------------------------------------------------------------------
# CSV column → meta_fields key (spec §4.1)
_META_FIELD_MAP = {
    "doc_category":    "doc_category",
    "sub_category":    "sub_category",
    "flood_event":     "flood_event",
    "doc_type":        "doc_type",
    "year":            "year",
    "source_format":   "source_format",
    "quality":         "quality",
    "responsible_dept":"responsible_dept",
    "doc_nature":      "doc_nature",
    "location":        "location",
    "flood_magnitude": "flood_magnitude",
}


def row_to_meta_fields(row: dict) -> dict:
    """
    Convert a mapping CSV row dict to the bare ``meta_fields`` payload.

    - Map CSV column names to the 11 metadata fields
    - Omit fields with empty string or None values

    Returns the plain field dict — the ``{"meta_fields": ...}`` envelope is
    added by ``RAGFlowClient.patch_document``, NOT here（双重包裹曾导致元数据
    静默丢失，见评审 C1）。``rel`` 溯源字段由调用方补充。

    year 保持字符串传入（ES 类型安全：schema 注册为 string，避免 number/string
    类型冲突导致索引重建）。
    """
    meta = {}
    for csv_col, field_key in _META_FIELD_MAP.items():
        val = row.get(csv_col, "")
        if val is None or val == "":
            continue
        meta[field_key] = val
    return meta


def find_reusable_doc(client, dataset_id: str, file_path: Path, docs=None, filename: str | None = None):
    """
    在库内找可复用的同名文档（review P0-5）。

    服务端文档模型没有暴露 per-doc 元数据或 content_hash，唯一可靠指纹是文件
    字节大小。规则：同名候选中**字节大小精确一致且唯一**才复用——
      - 0 个匹配：上传新文档；
      - ≥2 个匹配：无法分辨，一律上传新文档（宁可可见的重复，不可把元数据
        打到别人头上——mapping.csv 审计确认存在 4 组共 11 个同名文件）。

    ``docs`` 允许传入调用方已取好的文档快照，避免一次文件触发两轮翻页。
    ``filename`` 允许上传名与本地路径名不同（.xls 规范化后上传 .xlsx 内容
    但保持原 .xls 文档名，使同名复用与 import_state 逻辑不变）。
    """
    if docs is None:
        docs = client.list_documents(dataset_id)
    name = filename or file_path.name
    candidates = [d for d in docs if d.get("name") == name]
    size = file_path.stat().st_size
    exact = [d for d in candidates if d.get("size") == size]
    return exact[0] if len(exact) == 1 else None


# ---------------------------------------------------------------------------
# Import state
# ---------------------------------------------------------------------------

@dataclass
class DocImportState:
    rel: str
    status: str  # pending → uploaded → meta_done → parse_requested → done/failed
    dataset_key: str
    doc_id: str | None = None
    error: str | None = None


class ImportStateMachine:
    """
    Manages ``import_state.json`` persistence.

    State file is keyed by ``rel``; each value is a ``DocImportState`` dict.
    Supports resume: loading an existing state file preserves progress.
    """

    def __init__(self, state_path: Path | None = None) -> None:
        self._state_path = state_path or (OUT_DIR / "import_state.json")
        self._state: dict[str, dict] = {}
        if self._state_path.exists():
            try:
                raw = json.loads(self._state_path.read_text(encoding="utf-8"))
                for k, v in raw.items():
                    self._state[k] = v  # already dict (from JSON)
            except (json.JSONDecodeError, OSError):
                print("[WARN] import_state.json corrupt, starting fresh")

    def get(self, rel: str) -> str | None:
        return self._state.get(rel, {}).get("status")

    def get_field(self, rel: str, key: str) -> object:
        """读取状态条目里的附加字段（如 doc_id）——断点续跑的复用锚点。"""
        return self._state.get(rel, {}).get(key)

    def set(self, rel: str, status: str, **extra: object) -> None:
        if rel in self._state:
            entry = self._state[rel]
            entry["status"] = status
            # 状态翻转时清掉上一轮遗留的报错，除非本轮又显式给了 error——
            # 否则重跑成功的条目会一直挂着失败文本误导验收（review P2）
            if extra.get("error") is None:
                entry.pop("error", None)
        else:
            self._state[rel] = {"rel": rel, "status": status, **extra}
        for k, v in extra.items():
            if v is not None:
                self._state[rel][k] = v

    def save(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")

    def items(self):
        return self._state.items()


# ---------------------------------------------------------------------------
# Import logic
# ---------------------------------------------------------------------------

def run_import(
    apply: bool,
    dataset_key: str | None = None,
    limit: int | None = None,
    _out_dir: Path | None = None,
) -> dict:
    """
    Execute (or dry-run) the three-step import pipeline.

    Parameters
    ----------
    apply: if False, run in dry-run mode (no RAGFlow calls that modify state).
    dataset_key: if set, only import files belonging to this dataset key.
    limit: if set, cap the total number of files imported.
    _out_dir: override the output directory (dependency injection for tests).

    Steps per file
    ---------------
    1. resolve doc_id：import_state 已登记的 doc_id 最优先（断点续跑）；否则
       库内"同名 + 字节大小精确一致且唯一"才复用；再否则 upload 新文档
       （review P0-5：basename 盲匹配会在同名文件组里张冠李戴）
       → status = "uploaded"
    2. ``patch_document`` with bare fields from ``row_to_meta_fields`` plus a
       ``rel`` provenance key → status = "meta_done"
    3. ``parse_documents`` → status = "parse_requested"
    4. ``wait_document(timeout=600)`` → status = "done" or "failed"

    Rows carrying a non-empty ``skip_reason``（如 duplicate）不进入导入队列。
    语料为整理后的原始文件库（CORPUS_ROOT）：pdf→deepdoc 解析使引用可锚定原文页。

    Progress is persisted to ``import_state.json`` after every step.
    """
    # Load ds_key → dataset_id mapping from Task 7 output
    out_dir = _out_dir or OUT_DIR
    setup_path = out_dir / "setup_state.json"
    if not setup_path.exists():
        if apply:
            raise FileNotFoundError(f"setup_state.json not found at {setup_path}")
        print(f"[提示] {setup_path} 不存在——请先运行 `python run_setup.py` 完成建库。dry-run 结束。")
        return {"done": 0, "failed": 0, "skipped": 0}
    setup = json.loads(setup_path.read_text(encoding="utf-8"))

    # Load mapping CSV (list[dict])
    mapping_path = out_dir / "mapping.csv"
    if not mapping_path.exists():
        if apply:
            raise FileNotFoundError(f"mapping.csv not found at {mapping_path}")
        print(f"[提示] {mapping_path} 不存在——请先运行 `python -m corpus` 生成映射表。dry-run 结束。")
        return {"done": 0, "failed": 0, "skipped": 0}
    rows = read_mapping_csv(mapping_path)
    # rel 一律归一为 POSIX 分隔符：产物可能来自 Windows 本机（反斜杠），
    # 而服务器端按 "/" 拼路径并要求状态键一致。
    for row in rows:
        row["rel"] = normalize_rel(row.get("rel", ""))

    # Load existing import state (resume support) — 尊重 _out_dir 注入
    sm = ImportStateMachine(state_path=out_dir / "import_state.json")

    # Filter: skip already-done / duplicate-marked rows, optionally by dataset_key or limit
    pending = []
    for row in rows:
        rel = row.get("rel", "")
        if not rel:
            continue
        if row.get("skip_reason"):   # duplicate 等标记行不得入库（评审 C2）
            continue
        status = sm.get(rel)
        if status == "done":
            continue
        ds_k = row.get("dataset_key", "")
        if dataset_key and ds_k != dataset_key:
            continue
        pending.append(row)

    # Order: ds1..ds5 (ds0 tag KB is already done)
    ds_order = ["ds1", "ds2", "ds3", "ds4", "ds5"]
    pending.sort(key=lambda r: ds_order.index(r.get("dataset_key", "ds1")) if r.get("dataset_key", "ds1") in ds_order else len(ds_order))

    if limit:
        pending = pending[:limit]

    # ── Dry-run ──────────────────────────────────────────────────────────────
    if not apply:
        print(f"[dry-run] Would import {len(pending)} file(s)")
        for row in pending:
            print(f"  {row.get('dataset_key')}/{row.get('rel')}")
        return {"done": 0, "failed": 0, "skipped": len(pending)}

    # ── Real import ─────────────────────────────────────────────────────────
    try:
        client = RAGFlowClient(
            email=RAGFLOW_EMAIL,
            password=RAGFLOW_PASSWORD,
            public_pem_path=PUBLIC_PEM,
            api_key=RAGFLOW_API_KEY,
        )
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    done = 0
    failed = 0
    t0 = time.time()

    for row in pending:
        rel = row["rel"]
        ds_k = row.get("dataset_key", "")
        dataset_id = setup.get(ds_k, {}).get("id")
        if not dataset_id:
            sm.set(rel, "failed", error=f"Unknown dataset_key {ds_k}")
            sm.save()
            failed += 1
            continue

        # 语料源切换后，rel 即 CORPUS_ROOT 下的原始文件（pdf/doc/xls 直导）
        file_path = CORPUS_ROOT / rel
        if not file_path.exists():
            sm.set(rel, "failed", error=f"File not found: {file_path}")
            sm.save()
            failed += 1
            continue

        # 旧版 BIFF .xls 预处理：规范化为表头优先的 .xlsx 再上传
        # （方案 A 综合路线：预处理优先，shell_import 壳模式作为回退）
        # 保持原文件名上传，使同名复用与 import_state 逻辑不变
        upload_path = file_path
        try:
            from xls_normalize import normalize_if_needed
            normalized_dir = out_dir / "xls_normalized"
            upload_path, was_normalized = normalize_if_needed(file_path, normalized_dir)
            if was_normalized:
                print(f"[INFO] {rel}: BIFF .xls 已规范化 → {upload_path.name}")
        except ImportError:
            pass  # pandas/openpyxl 未安装时跳过规范化，直传原文件
        except Exception as exc:
            print(f"[WARN] {rel}: .xls 规范化失败 ({exc})，回退直传原文件")
            upload_path = file_path

        try:
            # 单次快照：同名复用探测与重跑状态预检共用，避免每文件两轮翻页
            existing = client.list_documents(dataset_id)
            by_id = {d["id"]: d for d in existing}

            # Step 1: resolve doc_id — 断点续跑优先用状态里的 doc_id（已消失则
            # 回退探测）；否则 "同名 + 字节大小精确一致且唯一" 才复用（review P0-5）
            doc_id = sm.get_field(rel, "doc_id")
            if doc_id and doc_id not in by_id:
                print(f"[WARN] {rel}: 状态中的 doc_id={doc_id} 已不存在（可能被手工删除），重新探测")
                doc_id = None
            if not doc_id:
                match = find_reusable_doc(client, dataset_id, upload_path, docs=existing, filename=file_path.name)
                if match is not None:
                    doc_id = match["id"]
                    print(f"[INFO] {rel}: 复用已有文档 id={doc_id}（同名同大小），跳过上传")
                else:
                    result = client.upload_document(dataset_id, upload_path, filename=file_path.name)
                    doc_id = result.get("id") if isinstance(result, dict) else None
                    if not doc_id and isinstance(result, list):
                        doc_id = result[0].get("id") if result else None
                    if not doc_id:
                        raise ValueError(f"No doc_id in upload response: {result}")
            sm.set(rel, "uploaded", dataset_key=ds_k, doc_id=doc_id)
            sm.save()

            # Step 2: patch metadata（+ rel 溯源字段；服务端接受未注册键，
            # Web UI 检查元数据时可反查回 mapping.csv 行）
            meta_payload = {**row_to_meta_fields(row), "rel": rel}
            client.patch_document(dataset_id, doc_id, meta_payload)
            sm.set(rel, "meta_done")
            sm.save()

            # 重跑预检：该文档此前已完成解析时，绝不能重发 parse——
            # v0.27.0 对 DONE 文档再 parse 会先清空旧 chunks，GraphRAG 重算一遍
            snapshot_run = str(by_id.get(doc_id, {}).get("run", ""))
            if _run_done(snapshot_run):
                sm.set(rel, "done")
                sm.save()
                done += 1
                print(f"[INFO] {rel}: 文档已是完成状态，跳过 parse（保护既有 chunks）")
                continue

            # Step 3: request parse
            client.parse_documents(dataset_id, [doc_id])
            sm.set(rel, "parse_requested")
            sm.save()

            # Step 4: wait for completion — GraphRAG 库放宽等待窗
            client.wait_document(
                dataset_id, doc_id, timeout=resolve_wait_timeout(ds_k)
            )
            sm.set(rel, "done")
            sm.save()
            done += 1

        except TimeoutError as exc:
            # 等待超时 ≠ 解析失败：区分状态便于人工决定重试还是排查
            sm.set(rel, "wait_timeout", error=str(exc))
            sm.save()
            failed += 1
            print(f"[TIMEOUT] {rel}: {exc}")
        except Exception as exc:
            sm.set(rel, "failed", error=str(exc))
            sm.save()
            failed += 1

    elapsed = time.time() - t0
    print(f"Import complete: {done} done, {failed} failed, {len(pending)-done-failed} skipped in {elapsed:.1f}s")
    return {"done": done, "failed": failed, "skipped": len(pending) - done - failed}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="RAGFlow three-step import")
    parser.add_argument("--apply", action="store_true", help="Actually import (omit for dry-run)")
    parser.add_argument("--dataset", dest="dataset_key", default=None, help="Limit to dataset key (e.g. ds3)")
    parser.add_argument("--limit", type=int, default=None, help="Cap total files imported")
    args = parser.parse_args()

    result = run_import(apply=args.apply, dataset_key=args.dataset_key, limit=args.limit)
    print(result)


if __name__ == "__main__":
    main()
