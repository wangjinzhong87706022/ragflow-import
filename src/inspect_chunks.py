"""
inspect_chunks — pilot 后的 chunk 切片审查工具（阶段3 验收支撑）。

用法（pilot 导入并解析完成后）：
    python inspect_chunks.py --dataset ds3 --limit 5
    python inspect_chunks.py --dataset ds1 ds2          # 多库
    python inspect_chunks.py --dataset ds3 --doc-id <document_id>

产出 out/qc/chunks_{ds}_{ts}.md：
  - 每文档 chunk 数 / 长度分布 / 异常块清单（碎片·噪声·重复·空）
  - 前 K 块预览 + 六条人工判据清单 + 症状→调参提示
仅读取，不修改任何服务端状态。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time

from config import OUT_DIR, RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM, RAGFLOW_API_KEY
from ragflow_client import RAGFlowClient, _run_done


# ---------------------------------------------------------------------------
# 纯函数：统计与标记
# ---------------------------------------------------------------------------

def length_stats(lengths: list[int]) -> dict:
    if not lengths:
        return {"count": 0, "avg": 0.0, "min": 0, "max": 0}
    return {
        "count": len(lengths),
        "avg": round(sum(lengths) / len(lengths), 1),
        "min": min(lengths),
        "max": max(lengths),
    }


_VALID_CHAR = re.compile(r"[一-鿿A-Za-z0-9]")


def flag_chunk(content: str, short_threshold: int = 50) -> str | None:
    """
    单块异常标记：empty（空白）/ noise（有效字符占比<30%，OCR 噪声信号）/
    fragment（短于阈值，切片过碎信号）/ None（正常）。
    """
    if not content or not content.strip():
        return "empty"
    valid = len(_VALID_CHAR.findall(content))
    total = len(content.strip())
    if total and valid / total < 0.3:
        return "noise"
    if len(content.strip()) < short_threshold:
        return "fragment"
    return None


def find_duplicates(chunks: list[dict]) -> set[str]:
    """返回内容重复的 chunk id 集合（每组保留首次出现）。"""
    seen: set[str] = set()
    dups: set[str] = set()
    for c in chunks:
        key = c.get("content", "")
        if key in seen:
            dups.add(c["id"])
        else:
            seen.add(key)
    return dups


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------

_FLAG_HINTS = {
    "fragment": "碎片过多 → 考虑调大 chunk_token_num 或检查分隔符",
    "noise": "噪声块 → OCR 质量问题，回查源文件 quality 标注",
    "duplicate": "重复块 → 服务端解析异常，考虑删文档重导",
    "empty": "空块 → 解析产物异常",
}


def render_report(doc_reports: list[dict], short_threshold: int, preview_k: int) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Chunk 审查报告",
        "",
        f"Generated: {ts}",
        f"参数：short_threshold={short_threshold} 字符，preview={preview_k} 块（长度为字符近似，非精确 token）",
        "",
        "## 六条人工判据（自动标记只覆盖其中可机判的部分）",
        "",
        "1. 结构完整：规程条款不被拦腰切断；表格 md/QA 行完整在同一块内",
        "2. **语义自包含**：遮住上下文单读一块，仍知道说的是哪场洪水、哪个站点",
        "3. 数值与语境同块：如 `百年一遇泄量1454 m³/s` 不与其限定条件分家",
        "4. 长度分布健康：大量碎片或超长块都需要调参",
        "5. 检索反推：用 QC 六问试检索，命中块应恰好包含答案句",
        "6. GraphRAG 实体完整：ds1/ds3 图谱实体不跨块断裂",
        "",
        "## 总览",
        "",
        "| 文档 | run | chunk数 | 平均字符 | 最短 | 最长 | 异常块 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in doc_reports:
        s = r["stats"]
        lines.append(
            f"| {r['name']} | {r['run']} | {s['count']} | {s['avg']} | {s['min']} | {s['max']} | {len(r['flags'])} |"
        )

    lines += ["", "## 明细", ""]
    for r in doc_reports:
        lines.append(f"### {r['name']}（id={r['id']}, run={r['run']}）")
        lines.append("")
        if not _run_done(r["run"]):   # 兼容 REST 层 "DONE" 与 DB 枚举 "3"（P0-3）
            lines.append(f"> ⚠️ 该文档 run={r['run']}，可能尚未解析完成——先确认 parse 状态再审切片。")
            lines.append("")
            continue
        if r["flags"]:
            lines.append("**异常块**：")
            lines.append("")
            for f in r["flags"]:
                hint = _FLAG_HINTS.get(f["tag"], "")
                preview = f["content"].replace("\n", " ")[:120] or "(空)"
                lines.append(f"- [{f['tag']}] `{f['id']}` {hint}")
                lines.append(f"  > {preview}")
            lines.append("")
        else:
            lines.append("无自动异常标记。")
            lines.append("")
        if r["previews"]:
            lines.append(f"**前 {preview_k} 块预览**（人工判据重点看这里）：")
            lines.append("")
            for i, p in enumerate(r["previews"], 1):
                lines.append(f"{i}. {p[:200]}")
            lines.append("")

    lines += ["---", "", "*症状→旋钮对照：碎片多→调大 chunk_token_num；单块多主题→调小；"
               "表格检索不到→强化 .qa.md / 确认原生 xlsx 行；关键词噪声→减 auto_keywords。改 config.py 后重导 pilot 对比。*"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description="chunk 切片审查报告（只读）")
    parser.add_argument("--dataset", nargs="+", help="数据集 key，如 ds3 或 ds1 ds2")
    parser.add_argument("--doc-id", default=None, help="只审查指定 document id")
    parser.add_argument("--limit", type=int, default=0, help="每库最多审查 N 个文档，0=不限")
    parser.add_argument("--short", type=int, default=50, help="碎片判定阈值（字符），默认50")
    parser.add_argument("--preview", type=int, default=3, help="每文档预览块数，默认3")
    args = parser.parse_args(argv)

    if not args.dataset:
        parser.error("必须通过 --dataset 指定至少一个数据集 key（如 --dataset ds3）")

    state_path = OUT_DIR / "setup_state.json"
    if not state_path.exists():
        print(f"[ERROR] {state_path} 不存在——请先运行 `python run_setup.py`。")
        sys.exit(1)
    setup_state = json.loads(state_path.read_text(encoding="utf-8"))

    try:
        client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM, api_key=RAGFLOW_API_KEY)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    ts = time.strftime("%Y%m%d_%H%M%S")
    all_docs = 0
    for ds_key in args.dataset:
        entry = setup_state.get(ds_key)
        if not entry:
            print(f"[WARN] {ds_key} 不在 setup_state.json 中，跳过")
            continue
        dataset_id = entry["id"]

        docs = client.list_documents(dataset_id)
        if args.doc_id:
            docs = [d for d in docs if d["id"] == args.doc_id]
        if args.limit:
            docs = docs[: args.limit]

        doc_reports: list[dict] = []
        for d in docs:
            chunks = client.list_chunks(dataset_id, d["id"])
            dup_ids = find_duplicates(chunks)
            flags = []
            for c in chunks:
                tag = flag_chunk(c.get("content", ""), short_threshold=args.short)
                if tag == "empty":
                    tag = "empty"
                elif c.get("id") in dup_ids:
                    tag = "duplicate"
                if tag:
                    flags.append({"id": c.get("id", ""), "tag": tag, "content": c.get("content", "")})
            previews = [c.get("content", "").replace("\n", " ") for c in chunks[: args.preview]]
            doc_reports.append({
                "name": d.get("name", d.get("id", "?")),
                "id": d.get("id", ""),
                "run": str(d.get("run", "")),
                "chunks": chunks,
                "stats": length_stats([len(c.get("content", "")) for c in chunks]),
                "flags": flags,
                "previews": previews,
            })

        report_path = OUT_DIR / "qc" / f"chunks_{ds_key}_{ts}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report(doc_reports, short_threshold=args.short, preview_k=args.preview),
                               encoding="utf-8")
        n_flags = sum(len(r["flags"]) for r in doc_reports)
        print(f"[INFO] {ds_key}: 审查 {len(doc_reports)} 个文档，{n_flags} 个异常块 → {report_path}")
        all_docs += len(doc_reports)

    return {"docs": all_docs}


if __name__ == "__main__":
    main()
