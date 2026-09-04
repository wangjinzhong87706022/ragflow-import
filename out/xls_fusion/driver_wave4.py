#!/usr/bin/env python3
"""批次三 driver：process（引擎全护栏）→ ES 回填 tag_feas → 引擎同款 verify 重验。

与 driver_wave2.py 同构（回填必须在挂块之后做，run_wave 一次跑完不可行——
process 重跑会重挂无 tag_feas 的新块）。词表两维 {洪水资料:10, 基础数据:8}：
P2-9b 教训（out/retrieval_tuning/P2_9B_WAVE2_COLLISION.md），勿加第三维，
否则词表模长碰撞 −0.53 挤掉同类行切片（X10 事故复现路径）。
批次三特检：防洪减灾统计表 doc_nature→统计（全库首例）；洪水过程(3) 承接
批次三枚举扩 2011-7（config/tag_vocab/ds0/schema×5 已于 2026-09-04 前置生效）。
ES 索引 ragflow_72fc3f62…（ds3），scope=docnm_kwd。

用法：RAGFLOW_API_KEY=… python3 driver_wave4.py
"""
import json
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
SRC = Path("/opt/wangjz/ragflow-import/src")
sys.path.insert(0, str(SRC))

from config import PUBLIC_PEM, RAGFLOW_API_KEY, RAGFLOW_EMAIL, RAGFLOW_PASSWORD  # noqa: E402
from ragflow_client import RAGFlowClient  # noqa: E402
import shell_import  # noqa: E402

DS3 = "d6fcb56ea1d711f19d7235ad4ea699d4"
ES_INDEX = "ragflow_72fc3f62a01f11f19d7235ad4ea699d4"
ES_AUTH = ("elastic", "infini_rag_flow")
ES_URL = f"http://localhost:1200/{ES_INDEX}/_update_by_query?refresh=true"
TAG_FEAS = {"洪水资料": 10, "基础数据": 8}

from rollout_xls_shell4 import CORPUS, ARCHIVE, STATE, MANIFEST, TARGETS, load_questions  # noqa: E402


def backfill_tag_feas(kb_name):
    """给本文档全部块写 tag_feas（scope=docnm_kwd）；返回更新块数（校验用）。"""
    body = {
        "query": {"bool": {"filter": [{"term": {"docnm_kwd": kb_name}}]}},
        "script": {"source": "ctx._source.tag_feas = params.m",
                   "params": {"m": TAG_FEAS}},
    }
    r = requests.post(ES_URL, json=body, auth=ES_AUTH, timeout=60)
    r.raise_for_status()
    d = r.json()
    if d.get("failures"):
        raise RuntimeError(f"ES 回填失败: {d['failures'][:1]}")
    return d.get("updated", 0)


def main():
    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)
    questions = load_questions()
    state = shell_import.load_json(STATE, {})
    manifest = shell_import.load_json(MANIFEST, {})
    for target in TARGETS:
        kb = target["kb_name"]
        if state.get(kb, {}).get("status") == "OK":
            print(f"跳过（已完成）: {kb}", flush=True)
            continue
        ok = shell_import.process(client, DS3, target, CORPUS, questions, True,
                                  state, manifest, ARCHIVE, STATE, MANIFEST)
        # 挂块后回填 tag_feas（无论门首轮过没过，壳块都缺这层加成）
        n = backfill_tag_feas(kb)
        print(f"  tag_feas 回填 {kb}: 更新 {n} 块 {TAG_FEAS}", flush=True)
        if n != state.get(kb, {}).get("chunks"):
            print(f"  [警告] 回填块数 {n} != 挂载 {state.get(kb, {}).get('chunks')}", flush=True)
        # 引擎同一 verify 门重验（回填已 refresh）
        ok2, rank = shell_import.verify(client, DS3, kb,
                                        target["probe"]["question"], target["probe"]["anchor"])
        rec = manifest.setdefault(kb, {})
        rec["tag_feas"] = TAG_FEAS
        rec["tag_feas_chunks"] = n
        rec["verified"] = ok2
        rec["probe_rank"] = rank
        rec["status"] = "OK" if ok2 else "FAIL(验证门)"
        state[kb] = {"status": rec["status"], "shell_id": rec.get("shell_id"),
                     "chunks": rec.get("chunks"), "verified": ok2}
        shell_import.save_json(MANIFEST, manifest)
        shell_import.save_json(STATE, state)
        print(f"  ⇒ {kb}: {rec['status']}（重验 rank={rank}）", flush=True)
        if not ok2:
            print("重验仍未过，停止后续（勿纸面放行；先诊断）", flush=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
