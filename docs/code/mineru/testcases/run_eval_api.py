# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
"""Run the MinerU evaluation cases through the chat completions API and score them.

v3 (2026-09-13):
  * 会话治理：每 N 题批量删除本轮创建的会话（--clean-every，默认 10；--no-clean 关闭），
    避免 135 条跑完残留 135 个会话。
  * 断点续跑：--out 已有结果中的用例 ID 自动跳过（--no-resume 关闭）。
  * 错误分类：answer 前缀标记 [ERROR]（业务 code!=0）/ [EXC]（网络异常），
    便于汇总时区分"检索不到"与"服务故障"。
  * 判分：关键词 strip 空白；长关键词字符覆盖率 >=0.7 兜底（与浏览器运行器一致）。

Usage:
  python run_eval_api.py --chat <chat_id> [--cases <json>] [--category 图片]
                         [--start 1] [--end 20] [--sleep 1] [--out <json>]
                         [--clean-every 10] [--no-clean] [--no-resume]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import requests

BASE = os.getenv("RAGFLOW_API_BASE", "https://labragf.openagp.top:9080")
API_KEY = os.getenv("RAGFLOW_API_KEY", "")
HEADERS = {"Authorization": f"Bearer {API_KEY}"}
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CASES = os.path.join(HERE, "mineru_eval_cases.json")


def strip_think(text: str) -> str:
    """剥 <think>…</think>；未闭合（流被截断）时返回空——思考不算答案。"""
    if not text:
        return ""
    open_i, close_i = text.find("<think>"), text.rfind("</think>")
    if open_i == -1:
        return text
    if close_i > open_i:
        return text[close_i + len("</think>"):].strip()
    return ""


def score(answer: str, keywords: list[str]) -> tuple[int, float]:
    def hit(k0: str) -> bool:
        k = (k0 or "").strip()
        if not k:
            return False
        if k in answer:
            return True
        if len(k) >= 6 and answer:
            return sum(1 for ch in k if ch in answer) / len(k) >= 0.7
        return False

    hits = sum(1 for k in keywords if hit(k))
    return hits, (hits / len(keywords) if keywords else 0.0)


def load_done(out: str, no_resume: bool) -> set[str]:
    if no_resume or not out or not os.path.exists(out):
        return set()
    try:
        return {r["id"] for r in json.load(open(out, encoding="utf-8"))}
    except Exception:  # noqa: BLE001
        return set()


def delete_sessions(ids: list[str], chat: str) -> int:
    """DELETE sessions. Verified on this instance (2026-09-13): the list API returns data as a
    plain array, and DELETE only honors a single ids=<id> per call — comma-joined or repeated
    ids params return code=0 but delete at most one (sometimes none). So: one call per id."""
    n = 0
    for sid in ids:
        try:
            r = requests.delete(
                f"{BASE}/api/v1/chats/{chat}/sessions",
                headers=HEADERS,
                timeout=60,
                params={"ids": sid},
            ).json()
            if r.get("code") == 0:
                n += 1
        except Exception:  # noqa: BLE001
            pass
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat", required=True)
    ap.add_argument("--cases", default=DEFAULT_CASES)
    ap.add_argument("--category", default="")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=10**9)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--out", default="")
    ap.add_argument("--clean-every", type=int, default=10, help="delete created sessions every N cases")
    ap.add_argument("--no-clean", action="store_true")
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()

    out = args.out or os.path.join(HERE, "mineru_eval_results_api.json")
    done = load_done(out, args.no_resume)

    cases = json.load(open(args.cases, encoding="utf-8"))
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    cases = [c for c in cases if args.start <= int(c["id"].split("-")[1]) <= args.end]
    cases = [c for c in cases if c["id"] not in done]
    print(f"running {len(cases)} case(s) ({len(done)} already done)")

    existing = [] if args.no_resume or not os.path.exists(out) else json.load(open(out, encoding="utf-8"))
    existing_ids = {r["id"] for r in existing}
    results: list[dict] = []
    created_sessions: list[str] = []
    passed = 0
    for idx, c in enumerate(cases, 1):
        try:
            s = requests.post(f"{BASE}/api/v1/chats/{args.chat}/sessions", headers=HEADERS, timeout=60,
                              json={"name": c["id"]}).json()
            sid = (s.get("data") or {}).get("id")
            if sid:
                created_sessions.append(sid)
            r = requests.post(f"{BASE}/api/v1/chats/{args.chat}/completions", headers=HEADERS, timeout=args.timeout,
                              json={"question": c["question"], "stream": False, "session_id": sid}).json()
            answer = ((r.get("data") or {}).get("answer") or "").strip()
            if r.get("code") != 0:
                answer = f"[ERROR] {r.get('message')}"
        except Exception as e:  # noqa: BLE001
            answer = f"[EXC] {e}"
        visible = strip_think(answer)
        hits, ratio = score(visible, c["expected_keywords"])
        if c["category"] == "反幻觉":
            # 反幻觉专用判分: 拒答表达是同义集合, 命中任一即 PASS（语义: 模型未编造即达标）
            REJECT = ("未找到", "不存在", "没有", "无法确定", "无数据", "未收录", "未提及", "没有具体数值", "为空白", "未提供", "未填写", "未给出", "未列出", "未明确", "空白")
            rejected = any(w in visible for w in REJECT)
            ok = rejected and hits >= 1
        else:
            ok = ratio >= 0.5 and hits >= 1
        passed += ok
        results.append({**c, "answer": answer[:1500], "hits": hits, "ratio": round(ratio, 2), "pass": ok})
        print(f'{c["id"]} [{c["category"]}] {"PASS" if ok else "MISS"} ({hits}/{len(c["expected_keywords"])}) {answer[:80]!r}')
        # incremental flush with dedup (crash-safe)
        merged = [r for r in existing if r["id"] not in {x["id"] for x in results}] + results
        json.dump(merged, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        if not args.no_clean and created_sessions and idx % args.clean_every == 0:
            deleted = delete_sessions(created_sessions, args.chat)
            print(f"  cleaned {deleted} session(s)")
            created_sessions = []
        time.sleep(args.sleep)

    if not args.no_clean and created_sessions:
        delete_sessions(created_sessions, args.chat)
    print(f"\ndone: {passed}/{len(results)} pass this run | saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
