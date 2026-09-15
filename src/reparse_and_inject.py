#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
重解析 + 自动补关键词 一键流水线。

为什么需要这个脚本
------------------
1) 解析侧的补丁（如 MinerU VLM 图注补丁）**只对"之后重新解析"的文档生效**，
   存量 chunk 不会自动重写；
2) 而重解析会**清空** chunk 上的 ``important_keywords``，
   导致"表1 / 表2 排序打平"这类问题立刻回归
   （实测：重解析后 表1 0.3499 / 表2 0.3482；补完关键词后 表2 0.3544 反超表1 0.350）；
3) 所以"重解析 → 补关键词"必须**成对执行**，本脚本把两步串起来并自动等待解析完成。

用法
----
    # 单文档：重解析 + 补关键词
    python src/reparse_and_inject.py --base-url URL --api-key KEY \
        --dataset-id DS --document-id DOC

    # 整库：逐个串行重解析 + 补关键词（耗时较长，慎用）
    python src/reparse_and_inject.py ... --dataset-id DS --all-documents

    # 只补关键词（文档已经重解析过了）
    python src/reparse_and_inject.py ... --dataset-id DS --all-documents --skip-parse

    # 只重解析（关键词稍后自己补）
    python src/reparse_and_inject.py ... --dataset-id DS --document-id DOC --skip-inject

关键词抽取规则复用 ``src/inject_media_keywords.py``，两者必须放在同一目录。
"""
from __future__ import annotations

import argparse
import sys
import time

import urllib3

from inject_media_keywords import Api, extract_media_keywords

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_BASE_URL = "https://labragf.openagp.top:9080/api/v1"


class PipelineApi(Api):
    """在 Api 之上补齐「触发解析 / 轮询状态」两个能力。"""

    def parse_documents(self, ds: str, doc_ids: list[str]) -> None:
        r = self.s.post(f"{self.base}/datasets/{ds}/documents/parse",
                        json={"document_ids": doc_ids}, timeout=self.timeout)
        self._check(r)

    def get_document(self, ds: str, doc_id: str) -> dict:
        r = self.s.get(f"{self.base}/datasets/{ds}/documents",
                       params={"id": doc_id}, timeout=self.timeout)
        docs = self._check(r)["data"]["docs"]
        return docs[0] if docs else {}

    def wait_document(self, ds: str, doc_id: str,
                      timeout: float = 1800, interval: float = 15) -> dict:
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            try:
                d = self.get_document(ds, doc_id)
            except Exception as e:          # 解析期间服务端偶发断连，忽略并重试
                print(f"    [poll] 传输错误(忽略): {e}")
                time.sleep(interval)
                continue
            prog = d.get("progress") or 0.0
            line = (f"    [poll] run={d.get('run')} "
                    f"progress={float(prog):.3f} chunks={d.get('chunk_count')}")
            if line != last:
                print(line)
                last = line
            if d.get("run") in ("DONE", "FAIL"):
                return d
            time.sleep(interval)
        raise TimeoutError(f"等待 {doc_id} 解析超时（{timeout}s）")


def inject_document(api: Api, ds: str, doc: dict,
                    replace: bool, sleep: float) -> tuple[int, int]:
    """复用 inject_media_keywords 的抽取规则，返回 (计划数, 已写入数)。"""
    chunks = api.list_chunks(ds, doc["id"])
    planned = []
    for ck in chunks:
        dtype = ck.get("doc_type_kwd") or "text"
        content = ck.get("content") or ck.get("content_with_weight") or ""
        kws = extract_media_keywords(content, dtype)
        if not kws:
            continue
        old = ck.get("important_keywords") or []
        if replace:
            merged = kws
            if set(merged) == set(old):
                continue
        else:
            if set(kws) <= set(old):
                continue
            merged = list(dict.fromkeys(list(old) + kws))
        planned.append((ck, merged))

    print(f"    [kw] 待写入 {len(planned)} 块 / 共 {len(chunks)} chunks")
    written = 0
    for ck, merged in planned:
        api.patch_chunk(ds, doc["id"], ck["id"], merged)
        written += 1
        time.sleep(sleep)
    if written:
        print(f"    [kw] 已写入 {written} 块")
    return len(planned), written


def main() -> int:
    p = argparse.ArgumentParser(description="重解析 + 自动补关键词 流水线")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--api-key", required=True)
    p.add_argument("--dataset-id", required=True)
    p.add_argument("--document-id", default="")
    p.add_argument("--all-documents", action="store_true")
    p.add_argument("--skip-parse", action="store_true", help="不重解析，只补关键词")
    p.add_argument("--skip-inject", action="store_true", help="只重解析，不补关键词")
    p.add_argument("--replace", action="store_true", help="关键词使用覆盖模式")
    p.add_argument("--timeout", type=float, default=1800, help="单文档解析等待上限(秒)")
    p.add_argument("--sleep", type=float, default=0.3, help="每块写入后的间隔秒数")
    args = p.parse_args()

    api = PipelineApi(args.base_url, args.api_key)
    docs = api.list_documents(args.dataset_id)
    if args.document_id:
        docs = [d for d in docs if d["id"] == args.document_id]
        if not docs:
            print(f"dataset 下找不到 document {args.document_id}")
            return 1
    elif not args.all_documents:
        print("请指定 --document-id 或 --all-documents")
        return 1

    print(f"dataset={args.dataset_id}  文档数={len(docs)}  "
          f"重解析={'否' if args.skip_parse else '是'}  "
          f"补关键词={'否' if args.skip_inject else '是'}")

    total_planned = total_written = 0
    for d in docs:
        print(f"\n[doc] {d['name']}  (当前 chunks={d.get('chunk_count')})")
        if not args.skip_parse:
            if d.get("run") == "RUNNING":
                print("  -> 该文档正在解析中，先等待完成")
                d = {**d, **api.wait_document(args.dataset_id, d["id"], timeout=args.timeout)}
            else:
                print("  -> 触发重解析")
                api.parse_documents(args.dataset_id, [d["id"]])
                print("  -> 等待解析完成 ...")
                d = {**d, **api.wait_document(args.dataset_id, d["id"], timeout=args.timeout)}
            print(f"  -> 解析结果: run={d.get('run')} chunks={d.get('chunk_count')}")
            if d.get("run") == "FAIL":
                print("  -> 解析失败，跳过关键词注入")
                continue
        if not args.skip_inject:
            n, w = inject_document(api, args.dataset_id, d, args.replace, args.sleep)
            total_planned += n
            total_written += w

    print(f"\n合计: 关键词计划 {total_planned} 块, 已写入 {total_written} 块")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
