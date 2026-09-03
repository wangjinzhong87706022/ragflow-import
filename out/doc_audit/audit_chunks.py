#!/usr/bin/env python3
"""全库只读审计：pipeline 配置 vs 实际切片质量。

拉取 6 个知识库的全部文档与切片，统计：
- 切片长度分布（过长=向量截断风险；过短=噪声）
- <table> 单体大表切片（已知 v0.27 naive 缺陷：深层行不可检索）
- 重复/近重复切片（页眉页脚、逐页重复标题）
- available=0 禁用切片、run 未完成文档
- 文档级 parser_config 覆盖、元数据覆盖
- 服务器端 parser_config 与 config.py 意图比对

产物：out/doc_audit/audit_data.json（原始统计）+ 控制台摘要
用法：RAGFLOW_API_KEY=… python3 audit_chunks.py
"""
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/opt/wangjz/ragflow-import/src")
from config import PUBLIC_PEM, RAGFLOW_API_KEY, RAGFLOW_EMAIL, RAGFLOW_PASSWORD  # noqa: E402
from ragflow_client import RAGFlowClient  # noqa: E402

HERE = Path(__file__).resolve().parent
IMG_DOCS = {"水库基本信息.jpg", "02-大坝剖面图.jpg", "溢洪道图1.jpg", "溢洪道图2.jpg",
            "05-库容水位对照表.jpg", "06-泄流曲线.jpg", "物资图1.jpg", "物资图2.jpg",
            "三个责任人.jpg", "中心架构图.png", "大坝注册登记证.png", "各部门用水需求.jpg"}


def chunk_len(c):
    return len(c.get("content_with_weight") or c.get("content") or "")


def main():
    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)
    datasets = client.list_datasets()
    out = {}
    for ds in datasets:
        key = ds["id"]
        name = ds.get("name", key)
        print(f"\n===== {name} ({key[:8]}) method={ds.get('chunk_method')} =====")
        pc = ds.get("parser_config") or {}
        docs = client.list_documents(key)
        stats = {
            "name": name, "chunk_method": ds.get("chunk_method"),
            "parser_config": pc, "embd_id": ds.get("embd_id"),
            "doc_count": len(docs),
            "docs_run_not_done": [], "docs_zero_chunks": [],
            "doc_parser_override": [], "docs_no_meta": [],
            "lens": [], "chunks_total": 0, "disabled_chunks": 0,
            "table_chunks": [], "long_chunks": [], "tiny_chunks": [],
            "dup_exact": [], "near_dup_keys": Counter(),
            "per_doc": [], "img_shell": [], "manual_chunks": 0,
            "chunk_keys": None,
        }
        for d in docs:
            dname = d.get("name", "?")
            run = str(d.get("run"))
            cc = int(d.get("chunk_count") or 0)
            if run not in ("3", "4") and dname not in IMG_DOCS:
                stats["docs_run_not_done"].append(
                    {"name": dname, "run": run, "progress": d.get("progress")})
            if cc == 0:
                stats["docs_zero_chunks"].append(dname)
            if d.get("parser_config"):
                stats["doc_parser_override"].append(dname)
            mf = d.get("meta_fields") or {}
            if not mf and dname not in IMG_DOCS:
                stats["docs_no_meta"].append(dname)
            is_img = dname in IMG_DOCS
            chunks = client.list_chunks(key, d["id"]) if cc else []
            if is_img:
                stats["manual_chunks"] += len(chunks)
                stats["img_shell"].append({"name": dname, "chunks": len(chunks),
                                           "run": d.get("run")})
                continue  # 手动壳切片不进原生质量统计
            lens = []
            for c in chunks:
                txt = c.get("content_with_weight") or c.get("content") or ""
                L = len(txt)
                lens.append(L)
                if stats["chunk_keys"] is None and chunks:
                    stats["chunk_keys"] = sorted(chunks[0].keys())
                if str(c.get("available")) == "0" or c.get("available_int") == 0:
                    stats["disabled_chunks"] += 1
                if "<table" in txt:
                    stats["table_chunks"].append({"doc": dname, "len": L,
                                                  "head": txt[:80]})
                if L > 2048:
                    stats["long_chunks"].append({"doc": dname, "len": L,
                                                 "head": txt[:80]})
                if L < 30:
                    stats["tiny_chunks"].append({"doc": dname, "len": L,
                                                 "head": txt[:60]})
                k = txt[:80]
                if k:
                    stats["near_dup_keys"][k] += 1
            stats["lens"].extend(lens)
            stats["chunks_total"] += len(chunks)
            stats["per_doc"].append({"name": dname, "type": d.get("type"),
                                     "run": run, "chunks": len(chunks),
                                     "avg_len": round(statistics.mean(lens), 1) if lens else 0,
                                     "max_len": max(lens) if lens else 0})
        # 聚合
        L = stats.pop("lens")
        lens_sorted = sorted(L)
        n = len(lens_sorted)
        agg = {"chunks_total": stats["chunks_total"],
               "len_min": lens_sorted[0] if n else 0,
               "len_p50": lens_sorted[n // 2] if n else 0,
               "len_p90": lens_sorted[int(n * 0.9)] if n else 0,
               "len_p99": lens_sorted[min(n - 1, int(n * 0.99))] if n else 0,
               "len_max": lens_sorted[-1] if n else 0,
               "len_mean": round(statistics.mean(lens_sorted), 1) if n else 0,
               "n_gt_2048": sum(1 for x in lens_sorted if x > 2048),
               "n_gt_4096": sum(1 for x in lens_sorted if x > 4096),
               "n_lt_30": len(stats["tiny_chunks"]),
               "n_table": len(stats["table_chunks"]),
               "table_max_len": max((x["len"] for x in stats["table_chunks"]), default=0),
               "disabled": stats["disabled_chunks"],
               "manual_chunks": stats["manual_chunks"]}
        exact = [k for k, v in stats["near_dup_keys"].items() if v >= 3]
        near = [(k[:40], v) for k, v in stats["near_dup_keys"].most_common(8) if v >= 3]
        stats["agg"] = agg
        stats["near_dup_examples"] = near
        stats.pop("near_dup_keys")
        # 控制台
        print(json.dumps(agg, ensure_ascii=False, indent=1))
        print(f"  run未完成: {[d['name'] for d in stats['docs_run_not_done']]}")
        print(f"  零切片: {stats['docs_zero_chunks']}")
        print(f"  doc级parser覆盖: {stats['doc_parser_override']}")
        print(f"  无元数据: {len(stats['docs_no_meta'])} 个")
        print(f"  表格切片 top: {sorted(stats['table_chunks'], key=lambda x: -x['len'])[:3]}")
        print(f"  超长切片 top: {sorted(stats['long_chunks'], key=lambda x: -x['len'])[:5]}")
        print(f"  近重复头部(≥3次): {near}")
        out[name] = stats

    (HERE / "audit_data.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n明细已写入 {HERE/'audit_data.json'}")


if __name__ == "__main__":
    main()
