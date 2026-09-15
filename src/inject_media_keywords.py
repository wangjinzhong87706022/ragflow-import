#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
给表格 / 图片 chunk 自动注入「表X / 图Y」题注关键词（important_keywords）。

为什么有效
----------
RAGFlow 检索打分时，关键词相似度里 important_kwd 的权重是正文的 5 倍：
    rag/nlp/search.py:485 / :517
    tks = content_ltks + title_tks * 2 + important_kwd * 5 + question_tks * 6
    sim  = tkweight * tksim + vtweight * vtsim,  tkweight = 1 - vector_similarity_weight
项目 chat 配置 vector_similarity_weight=0.31 → 关键词分占 69%。

而表格 / 图片 chunk 的题注常常埋在块中部，正文里只剩「见表2 / 见图10」这类引用，
「表2 / 图10」这种精确查询召回不稳。把题注编号写进 important_kwd 后才有稳定高分。

题注的两种落位（本脚本都支持）
------------------------------
1. DeepDOC 路径：``<table><caption>表D.0.1-1项目特性表</caption>…``  —— 题注在 caption 标签内
2. MinerU 路径：``…</table>表 2 2.5 次抛物线表…``               —— 题注在表格之后

用法
----
    # 预演（只打印，不写）
    python src/inject_media_keywords.py --base-url URL --api-key KEY \
        --dataset-id DS --document-id DOC

    # 写入（合并到已有 important_keywords）
    python src/inject_media_keywords.py ... --apply

    # 写入并覆盖（清掉历史脏关键词，仅限本脚本识别出的媒体块）
    python src/inject_media_keywords.py ... --apply --replace

    # 整库所有文档
    python src/inject_media_keywords.py ... --dataset-id DS --all-documents --apply

注意
----
* 请求体字段名是 ``important_keywords``；列表接口返回同名别名，单块 GET 返回内部名
  ``important_kwd``，且对 image 类型 chunk 可能不返回（核对请用列表接口）。
* 只传 important_keywords 不破坏正文，但服务端会重算该块 embedding（chunk_api.py:1220）。
* 更新接口本地源码注册的是 PATCH（api/apps/restful_apis/chunk_api.py:1144）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib3

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_BASE_URL = "https://labragf.openagp.top:9080/api/v1"

# ---------------------------------------------------------------- 题注抽取规则
# 1) DeepDOC 路径：题注在 <caption> 标签内
CAPTION_TAG = re.compile(r"<caption[^>]*>(.*?)</caption>", re.S | re.I)
# 2) MinerU 路径：题注在 </table> 之后
TABLE_AFTER_HTML = re.compile(r"</table>\s*(表\s*[0-9A-Z][0-9.\-]*)")
# 3) 兜底：块内任意位置的「表X」
TABLE_ANY = re.compile(r"(表\s*[0-9A-Z][0-9.\-]*)")
# 4) 图片块：图注在块首
FIG_ANY = re.compile(r"(图\s*[0-9A-Z][0-9.\-]*)")

TAG_RE = re.compile(r"<[^>]+>")
ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"), ("&nbsp;", " "), ("&quot;", '"'), ("&#39;", "'"))

# 题注名截断：遇到这些词说明已经进入正文
STOP_WORDS = ("通过", "所示", "如下", "详见", "其中", "见表", "见图", "见 ", "如 ")
PUNCT = "，。；、,;:：!？\n\r\t）)】》］"

MAX_TITLE_LEN = 24


def _strip_html(s: str) -> str:
    """去掉 HTML 标签与实体，保留文字。"""
    s = re.sub(r"(?is)<\s*br\s*/?\s*>", " ", s)
    s = TAG_RE.sub(" ", s)
    for k, v in ENTITIES:
        s = s.replace(k, v)
    return re.sub(r"[\s\u3000]+", " ", s).strip()


def _trim_title(tail: str) -> str:
    """从「 2.5 次抛物线表通过详细算法…」里取出干净的题注名「2.5次抛物线表」。"""
    tail = _strip_html(tail)
    if not tail:
        return ""
    cut = len(tail)
    for i, ch in enumerate(tail):
        if ch in PUNCT:
            cut = i
            break
    title = tail[:cut]
    title = re.split(r"[图表]\s*[0-9A-Z][0-9.\-]*", title)[0]   # 撞到下一个编号即截断
    for w in STOP_WORDS:
        j = title.find(w)
        if j >= 0:            # 出现在开头同样不是题注名（如「图6所示」）
            title = title[:j]
    title = re.sub(r"[\s\u3000]+", "", title)
    title = title.lstrip("）)】》］、，,;:：")
    return title[:MAX_TITLE_LEN]


def build_keywords(kind: str, num_raw: str, title: str) -> list[str]:
    """表 2 → ['表2', '表2 2.5次抛物线表', ...]。"""
    num = re.sub(r"[\s\u3000]+", "", num_raw).rstrip(".-")   # '表 2'→'表2'; '表6.2.'→'表6.2'
    num = f"{kind}{num[len(kind):]}" if num.startswith(kind) else f"{kind}{num}"
    if len(num) <= len(kind):
        return []
    kws = [num]
    if title:
        kws.append(f"{num} {title}")
        if len(title) <= 16:
            kws.append(f"{num}{title}")
    return list(dict.fromkeys(k for k in kws if k))


def extract_media_keywords(content: str, doc_type: str) -> list[str]:
    """返回该 chunk 应注入的关键词列表；非表格/图片或抽不到题注返回 []。"""
    content = content or ""

    if doc_type == "table" or "</table>" in content:
        # 1) <caption> 内（DeepDOC）。形态多样：
        #    「表D.0.1-1项目特性表」/「水土流失现状表表D.0.5」/「续表A.0.1」/「表D. 0. 4表」
        m = CAPTION_TAG.search(content)
        if m:
            raw = re.sub(r"[\s\u3000]+", "", _strip_html(m.group(1)))   # 去空格，修「D. 0. 4」
            raw = re.sub(r"^续", "", raw)                               # 「续表…」→「表…」
            mm = TABLE_ANY.search(raw)                                  # 表名可能在编号前
            if mm:
                name = raw[: mm.start()] + raw[mm.end():]
                return build_keywords("表", mm.group(1), _trim_title(name))
        # 2) </table> 之后（MinerU）
        m = TABLE_AFTER_HTML.search(content)
        if m:
            return build_keywords("表", m.group(1), _trim_title(content[m.end():]))
        # 3) 兜底
        m = TABLE_ANY.search(content)
        if m:
            return build_keywords("表", m.group(1), _trim_title(content[m.end():]))
        return []

    if doc_type == "image":
        m = FIG_ANY.search(content)          # 保留换行：题注后常以换行切分
        if not m:
            return []
        return build_keywords("图", m.group(1), _trim_title(content[m.end():]))

    return []


# ------------------------------------------------------------------- API 封装
class Api:
    def __init__(self, base_url: str, api_key: str, timeout: int = 60):
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {api_key}"
        self.s.verify = False
        self.timeout = timeout

    def _check(self, resp) -> dict:
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            raise RuntimeError(f"code={body.get('code')} msg={body.get('message')}")
        return body

    def list_documents(self, ds: str) -> list[dict]:
        r = self.s.get(f"{self.base}/datasets/{ds}/documents",
                       params={"page": 1, "page_size": 100}, timeout=self.timeout)
        return self._check(r)["data"]["docs"]

    def list_chunks(self, ds: str, doc: str, page_size: int = 100) -> list[dict]:
        out, page = [], 1
        while True:
            r = self.s.get(f"{self.base}/datasets/{ds}/documents/{doc}/chunks",
                           params={"page": page, "page_size": page_size}, timeout=self.timeout)
            payload = self._check(r)["data"] or {}
            raw = payload.get("chunks", [])
            out.extend(raw)
            total = payload.get("total")
            if not raw or (isinstance(total, int) and len(out) >= total) or len(raw) < page_size:
                break
            page += 1
        return out

    def patch_chunk(self, ds: str, doc: str, chunk_id: str, keywords: list[str]) -> None:
        body = json.dumps({"important_keywords": keywords}, ensure_ascii=False).encode("utf-8")
        r = self.s.patch(f"{self.base}/datasets/{ds}/documents/{doc}/chunks/{chunk_id}",
                         data=body, headers={"Content-Type": "application/json; charset=utf-8"},
                         timeout=self.timeout)
        self._check(r)


# ---------------------------------------------------------------------- 主流程
def process_document(api: Api, ds: str, doc: dict, apply: bool, replace: bool, sleep: float) -> tuple[int, int]:
    chunks = api.list_chunks(ds, doc["id"])
    planned, skipped, written = [], 0, 0
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
                skipped += 1
                continue
        else:
            if set(kws) <= set(old):      # 幂等：已包含则跳过
                skipped += 1
                continue
            merged = list(dict.fromkeys(list(old) + kws))
        planned.append((ck, merged))

    print(f"\n[doc] {doc['name']}  chunks={len(chunks)}  "
          f"待写入={len(planned)}  无需变更={skipped}")
    for ck, merged in planned:
        head = re.sub(r"\s+", " ", (ck.get("content") or ""))[:60]
        print(f"  - [{ck.get('doc_type_kwd')}] {ck['id']}")
        print(f"      旧: {ck.get('important_keywords') or []}")
        print(f"      新: {merged}")
        print(f"      文首: {head}")

    if apply and planned:
        for ck, merged in planned:
            api.patch_chunk(ds, doc["id"], ck["id"], merged)
            written += 1
            time.sleep(sleep)
        print(f"  已写入 {written} 个块")
    return len(planned), written


def main() -> int:
    p = argparse.ArgumentParser(description="注入表号/图号到 chunk 的 important_keywords")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--api-key", required=True)
    p.add_argument("--dataset-id", required=True)
    p.add_argument("--document-id", default="")
    p.add_argument("--all-documents", action="store_true")
    p.add_argument("--apply", action="store_true", help="真正写入；缺省为预演")
    p.add_argument("--replace", action="store_true", help="覆盖旧关键词（仅限识别出的媒体块）")
    p.add_argument("--sleep", type=float, default=0.3, help="每块写入后的间隔秒数")
    args = p.parse_args()

    api = Api(args.base_url, args.api_key)
    docs = api.list_documents(args.dataset_id)
    if args.document_id:
        docs = [d for d in docs if d["id"] == args.document_id]
        if not docs:
            print(f"dataset 下找不到 document {args.document_id}")
            return 1
    elif not args.all_documents:
        print("请指定 --document-id 或 --all-documents")
        return 1

    print(f"模式: {'写入' if args.apply else '预演（不写入）'}"
          f"{' + 覆盖' if args.replace else ''}  "
          f"dataset={args.dataset_id}  文档数={len(docs)}")
    total_planned = total_written = 0
    for d in docs:
        if d.get("run") != "DONE":
            print(f"[skip] {d['name']} run={d.get('run')}")
            continue
        n, w = process_document(api, args.dataset_id, d, args.apply, args.replace, args.sleep)
        total_planned += n
        total_written += w
    print(f"\n合计: 待写入 {total_planned} 块, 已写入 {total_written} 块")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
