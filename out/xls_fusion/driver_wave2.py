#!/usr/bin/env python3
"""批次一 driver：process（引擎全护栏）→ ES 回填 tag_feas → 引擎同款 verify 重验。

背景（2026-09-04 首次 apply 实证）：壳块免 parse 无 tag_feas，ds3 内被姊妹垃圾块与
汇报 .doc 高密度块挤出 top30，验证门 FAIL——即 P2-9 根因一在 wave-2 复现（wave-1
当时门过只是垃圾池尚浅）。回填必须在"挂块之后"做，因此不能走 run_wave 一次跑完：
每份 process 后立即回填，再用引擎同一 verify 门重验，回写 state/manifest。

tag_feas 词表照抄 P2-9（tag_shells_experiment.json）：场次事件文档 {洪水资料:10, 基础数据:8}。
ES 索引 ragflow_72fc3f62a01f11f19d7235ad4ea699d4（ds3），scope=docnm_kwd 精确到本文档。

用法：RAGFLOW_API_KEY=… python3 driver_wave2.py
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

from rollout_xls_shell2 import CORPUS, ARCHIVE, STATE, MANIFEST, TARGETS, load_questions  # noqa: E402


def backfill_tag_feas(kb_name, expect_chunks):
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
        n = backfill_tag_feas(kb, state.get(kb, {}).get("chunks", 0))
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
