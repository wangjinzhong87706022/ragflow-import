#!/usr/bin/env python3
"""P2-9 三配置探针：8 个 FAIL 题在 无rerank / rerank vsw0.3 / rerank vsw0.5 下的对比。

背景：壳块补 tag_feas（见 tag_shells_experiment.json）后，剩余 FAIL 应为纯排序层缺口。
本脚本不改库，只按请求传 rerank_id/vector_similarity_weight（REST 原生支持），串行执行。

判分口径与 run_eval8.py / run_eval_xls.py 一致：all_keywords 全部出现在 top10 并集
且 any_keywords（硬 OR，若非空）至少命中一个；×3 取多数（≥2/3 判过）。

用法：RAGFLOW_API_KEY=… python3 probe_p29.py [--repeats 3]
产物：probe_p29_results.json + stdout 对比表。
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path("/opt/wangjz/ragflow-import/src")
sys.path.insert(0, str(SRC))

from config import PUBLIC_PEM, RAGFLOW_API_KEY, RAGFLOW_EMAIL, RAGFLOW_PASSWORD  # noqa: E402
from ragflow_client import RAGFlowClient  # noqa: E402

DS1 = "d6e064e0a1d711f19d7235ad4ea699d4"
DS2 = "d6ec4e36a1d711f19d7235ad4ea699d4"
DS3 = "d6fcb56ea1d711f19d7235ad4ea699d4"
DS4 = "d756f7b8a1d711f19d7235ad4ea699d4"
RERANK_ID = "269c3b0ea02911f19d7235ad4ea699d4"   # bge-reranker-v2-m3（labxinf，dialog 同款）
TOPN = 10

# 8 个 FAIL 题（题面/判分字段照抄两份题库；doc=迁移后的壳名，仅作遥测不计分）
QUESTIONS = [
    {"id": "X5", "ds": [DS3], "doc": "洪水统计(1).xls",
     "question": "8.21洪水的流域面雨量是多少毫米？",
     "all_keywords": ["87.66"], "any_keywords": ["面雨量"]},
    {"id": "X6", "ds": [DS3], "doc": "较大洪水统计表.xls",
     "question": "桃曲坡水库流域实测洪峰流量最大的是哪场洪水？洪峰流量是多少？",
     "all_keywords": ["1353"], "any_keywords": []},
    {"id": "X7", "ds": [DS3], "doc": "较大洪水统计表.xls",
     "question": "桃曲坡水库流域哪场洪水是200年一遇？",
     "all_keywords": ["1867"], "any_keywords": ["200年一遇"]},
    {"id": "X9", "ds": [DS3], "doc": "较大洪水统计表.xls",
     "question": "1932年的洪水洪峰流量是多少？",
     "all_keywords": ["2110"], "any_keywords": ["100年一遇"]},
    {"id": "W5", "ds": [DS1, DS2, DS4], "doc": "物资图2.jpg",
     "question": "桃曲坡水库枢纽站实有物资中铁锹有多少把？",
     "all_keywords": ["铁锹"], "any_keywords": ["315 把", "315把"]},
    {"id": "W6", "ds": [DS1, DS2, DS4], "doc": "物资图2.jpg",
     "question": "桃曲坡水库防汛物资的存放地点在哪里？",
     "all_keywords": ["枢纽站库房"], "any_keywords": []},
    {"id": "G3", "ds": [DS1, DS2, DS4], "doc": "中心架构图.png",
     "question": "桃曲坡水库灌溉中心下属有哪些企业？",
     "all_keywords": ["陕西铜川供水有限公司", "陕西飞龙水利水电工程有限公司"],
     "any_keywords": ["锦阳湖"]},
    {"id": "G4", "ds": [DS1, DS2, DS4], "doc": "中心架构图.png",
     "question": "桃曲坡水库灌溉中心设有哪个工程维修养护机构？",
     "all_keywords": ["工程维修养护大队"], "any_keywords": []},
]

CONFIGS = [
    {"key": "A_base", "label": "无rerank（现状）", "params": {}},
    {"key": "B_rerank_vsw03", "label": "rerank vsw=0.3",
     "params": {"rerank_id": RERANK_ID, "vector_similarity_weight": 0.3}},
    {"key": "C_rerank_vsw05", "label": "rerank vsw=0.5",
     "params": {"rerank_id": RERANK_ID, "vector_similarity_weight": 0.5}},
]


def chunk_text(c):
    return c.get("content_with_weight") or c.get("content") or ""


def chunk_doc(c):
    return c.get("document_name") or c.get("docnm_kwd") or c.get("document_keyword") or "?"


def judge(q, chunks):
    """run_qc 口径：all 全命中 top10 并集 + any 硬 OR。返回 (passed, target_rank)。"""
    top = chunks[:TOPN]
    joined = "\n".join(chunk_text(c) for c in top)
    ok_all = all(k in joined for k in q["all_keywords"])
    ok_any = (not q["any_keywords"]) or any(k in joined for k in q["any_keywords"])
    t_rank = None
    for i, c in enumerate(top):
        if chunk_doc(c) == q["doc"] and all(k in chunk_text(c) for k in q["all_keywords"]):
            t_rank = i + 1
            break
    return (ok_all and ok_any), t_rank


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()

    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM, api_key=RAGFLOW_API_KEY)
    results = {"ts": datetime.now().isoformat(timespec="seconds"),
               "rerank_id": RERANK_ID, "topn": TOPN, "repeats": args.repeats,
               "questions": []}
    for q in QUESTIONS:
        rec = {"id": q["id"], "question": q["question"], "target_doc": q["doc"], "configs": {}}
        for cfg in CONFIGS:
            passes, ranks = 0, []
            for _ in range(args.repeats):
                data = client.search_datasets(q["ds"], q["question"], top_k=TOPN, **cfg["params"])
                ok, tr = judge(q, data.get("chunks") or [])
                passes += int(ok)
                ranks.append(tr)
            rec["configs"][cfg["key"]] = {"pass_count": f"{passes}/{args.repeats}",
                                          "passed": passes >= 2, "target_ranks": ranks}
            print(f"  {q['id']:<3} {cfg['key']:<16} {passes}/{args.repeats}  ranks={ranks}",
                  flush=True)
        results["questions"].append(rec)

    out = HERE / "probe_p29_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n==== 对比（{args.repeats} 次取多数，top{TOPN}）====")
    hdr = f"{'题':<4}" + "".join(f"{c['key']:>18}" for c in CONFIGS)
    print(hdr)
    for rec in results["questions"]:
        row = f"{rec['id']:<4}"
        for cfg in CONFIGS:
            r = rec["configs"][cfg["key"]]
            row += f"{r['pass_count']:>14}{str(r['target_ranks']):>18}"[:18].rjust(18)
        print(row)
    for cfg in CONFIGS:
        n = sum(1 for rec in results["questions"] if rec["configs"][cfg["key"]]["passed"])
        print(f"{cfg['label']}: {n}/{len(results['questions'])}")
    print(f"结果已写入 {out}")


if __name__ == "__main__":
    main()
