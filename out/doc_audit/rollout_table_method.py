#!/usr/bin/env python3
"""P1 铺开: ds3 其余 xls/xlsx + ds4 的 xlsx 改 doc 级 table method 重解析（严格串行）。

背景（audit F1 + 试点经验）：
- naive 吃 xls 产生 Unnamed/nan 行文本垃圾 + 表头分流（8.21 探针同头 7/10）。
- v0.27.1 tag 阶段对 ES 打标失败的块逐块调 LLM（content_tagging），xls 行块几乎全部
  落入 LLM 兜底 —— 12 块的小表打了 60+ 次调用。铺开必须 doc 级关掉：
    auto_questions=0, auto_keywords=0, tag_kb_ids=[]
  （tag 仅软打标信号；xls 垃圾行块的 tag 价值≈0；恢复=改回配置再 parse。）
- 每文档一次只跑一个 parse，等 DONE 再发下一个（LLM 服务并发有限）。

用法：
  RAGFLOW_API_KEY=… python3 rollout_table_method.py            # dry-run：只列目标
  RAGFLOW_API_KEY=… python3 rollout_table_method.py --apply
  … --apply --limit 2                    # 分批
  … --apply --ds ds4                     # 只跑 ds4

状态：out/doc_audit/rollout_table_state.json（幂等，DONE 的文档自动跳过）。
回滚：doc 级 PUT chunk_method=naive + 原 parser_config 后重 parse；
     pilot 两表带 tag 变体见 pilot 记录。

⚠️⚠️ 已否决（2026-09-02 试点）——不得 --apply ⚠️⚠️
试点裁决见 PILOT_TABLE_VERDICT.md：table 法对政府 BIFF 表是净倒退
（曲线表 1331 块/94% 垃圾/值当表头）。本脚本仅保留作过程记录。
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, "/opt/wangjz/ragflow-import/src")
from config import PUBLIC_PEM, RAGFLOW_API_KEY, RAGFLOW_EMAIL, RAGFLOW_PASSWORD  # noqa: E402
from ragflow_client import RAGFlowClient  # noqa: E402

HERE = Path(__file__).resolve().parent
STATE = HERE / "rollout_table_state.json"
API = "http://localhost:9380/api/v1"
H = {"Authorization": f"Bearer {RAGFLOW_API_KEY}", "Content-Type": "application/json"}

# 试点两个文档不重复处理
PILOT_DONE = {"洪水统计(1).xls", "水位库容曲线推求.xls"}
POLL_SEC = 15
DOC_TIMEOUT = 2400  # 单文档上限 40min（关 tag 后 1-2min 足够，留足余量）


def txt(c):
    return c.get("content_with_weight") or c.get("content") or ""


def junk_count(chunks):
    return sum(1 for x in chunks if re.search(r"Unnamed: \d|：nan", txt(x)))


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def doc_by_id(client, ds, did):
    r = requests.get(f"{API}/datasets/{ds}/documents", headers=H,
                     params={"id": did}, timeout=30)
    return r.json()["data"]["docs"][0]


def wait_done(did, name):
    t0 = time.time()
    while time.time() - t0 < DOC_TIMEOUT:
        time.sleep(POLL_SEC)
        d = doc_by_id(None, None, did)
        run, prog, cc = d.get("run"), d.get("progress"), d.get("chunk_count")
        print(f"    [{name}] {time.time()-t0:.0f}s run={run} prog={prog} chunks={cc}", flush=True)
        if run == "DONE" or (isinstance(prog, (int, float)) and prog >= 1):
            return True, cc
        if run == "FAIL" or (isinstance(prog, (int, float)) and prog < 0):
            return False, cc
    return False, None


def process(client, ds, name, did, apply_changes, state):
    if name in state and state[name].get("status") == "DONE":
        print(f"  跳过（已完成）: {name}", flush=True)
        return
    print(f"== {name} ({did[:8]}) ==", flush=True)
    if not apply_changes:
        print("  [dry-run] 将执行: PUT parser_config(自动项全关) + chunk_method=table + parse", flush=True)
        return

    # 1) doc 级关自动项（deep-merge），顺带记录改前配置用于回滚
    d = doc_by_id(client, ds, did)
    if "prev_parser_config" not in state.get(name, {}):
        state.setdefault(name, {})["prev_parser_config"] = d.get("parser_config")
        state[name]["prev_chunk_method"] = d.get("chunk_method")
    r = requests.put(f"{API}/datasets/{ds}/documents/{did}", headers=H, json={
        "parser_config": {"auto_questions": 0, "auto_keywords": 0, "tag_kb_ids": []},
    }, timeout=30)
    print(f"  PUT parser_config: code={r.json().get('code')}", flush=True)

    # 2) doc 级改 table
    r = requests.put(f"{API}/datasets/{ds}/documents/{did}", headers=H,
                     json={"chunk_method": "table"}, timeout=30)
    print(f"  PUT chunk_method=table: code={r.json().get('code')}", flush=True)

    # 3) 串行 parse + 等待
    r = requests.post(f"{API}/datasets/{ds}/documents/parse", headers=H,
                      json={"document_ids": [did]}, timeout=30)
    print(f"  parse: code={r.json().get('code')} {r.json().get('message','')}", flush=True)
    ok, cc = wait_done(did, name)
    state[name] = dict(state.get(name, {}), status="DONE" if ok else "FAIL",
                       chunk_count=cc, finished=time.strftime("%F %T"))
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  ⇒ {name}: {'DONE' if ok else 'FAIL'} chunks={cc}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ds", default="", help="ds3|ds4，空=两者")
    args = ap.parse_args()

    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)
    dss = {"ds3": "洪水资料", "ds4": "组织管理"}
    targets = []
    for key, dsname in dss.items():
        if args.ds and key != args.ds:
            continue
        ds = [d for d in client.list_datasets() if d["name"] == dsname][0]["id"]
        for d in client.list_documents(ds):
            if d["name"].lower().endswith((".xls", ".xlsx")) and d["name"] not in PILOT_DONE:
                targets.append((key, ds, d["name"], d["id"]))

    state = load_state()
    todo = [t for t in targets if not (t[2] in state and state[t[2]].get("status") == "DONE")]
    if args.limit:
        todo = todo[:args.limit]
    print(f"目标 {len(targets)} 个，待处理 {len(todo)} 个（apply={args.apply}）", flush=True)
    for key, ds, name, did in targets:
        mark = "✓" if name in state and state[name].get("status") == "DONE" else " "
        print(f" {mark} [{key}] {name}", flush=True)

    for key, ds, name, did in todo:
        process(client, ds, name, did, args.apply, state)

    # 汇总
    print("\n== 汇总 ==", flush=True)
    done = [(k, n, s.get("chunk_count")) for (k, n), s in
            [((t[0], t[2]), state.get(t[2], {})) for t in targets] if s.get("status") == "DONE"]
    for k, n, cc in done:
        print(f"  [{k}] {n}: {cc} 块", flush=True)
    fails = [t[2] for t in targets if state.get(t[2], {}).get("status") == "FAIL"]
    if fails:
        print(f"  FAIL: {fails}", flush=True)


if __name__ == "__main__":
    main()
