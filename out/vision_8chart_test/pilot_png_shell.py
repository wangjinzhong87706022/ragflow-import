#!/usr/bin/env python3
"""试点：原图为壳 + 融合文本为切片（大坝注册登记证）。

目标：检索引用从《大坝注册登记证.md》改锚到原始扫描件《大坝注册登记证.png》。
路径：上传 png（不 parse）→ 复制 md 的元数据 → 把 md 现有切片手动挂到 png 名下
     → 检索验证 → 禁用 md → 复验检索与问答。
全程可回滚：md 只禁用不删除（status 置回 "1" 即恢复）。
用法：python3 pilot_png_shell.py            # 全流程（含禁用 md 与复验）
     python3 pilot_png_shell.py --keep-md  # 挂完切片即停，不禁用 md
     python3 pilot_png_shell.py --rollback # 重新启用 md（回滚）
"""
import argparse
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
SRC = Path("/opt/wangjz/ragflow-import/src")
sys.path.insert(0, str(SRC))

from config import (PUBLIC_PEM, RAGFLOW_API_KEY, RAGFLOW_EMAIL,  # noqa: E402
                    RAGFLOW_PASSWORD)
from ragflow_client import RAGFlowClient  # noqa: E402

API = "http://localhost:9380/api/v1"
KEY = "ragflow-3NZDMvfCVXMVJLXYB_ixCWuduqcI-fcksfBOQ_VCE5E"
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
DS4 = "d756f7b8a1d711f19d7235ad4ea699d4"
PNG_PATH = Path("/home/scada/SmartTwinRes-skills/pdfs/07-管理资料/02-注册登记证/大坝注册登记证.png")
PNG_NAME = "大坝注册登记证.png"
MD_NAME = "大坝注册登记证.md"
CHAT_ID = "d4746f6aa68911f196c0d10ff025f702"  # vision8_qa_test
PROBE_Q = "桃曲坡水库大坝注册登记证的注册登记号是多少？"
ANCHOR = "61000030008-A2"


def add_chunk(doc_id, content, important_keywords=None):
    body = {"content": content}
    if important_keywords:
        body["important_keywords"] = important_keywords
    r = requests.post(f"{API}/datasets/{DS4}/documents/{doc_id}/chunks",
                      headers=H, json=body, timeout=30)
    data = r.json()
    if data.get("code") != 0:
        raise SystemExit(f"add_chunk 失败: {data}")
    return data["data"]["chunk"]["id"]


def set_doc_status(doc_id, status):
    """status: "1"=启用, "0"=禁用。"""
    r = requests.put(f"{API}/datasets/{DS4}/documents/{doc_id}",
                     headers=H, json={"status": status}, timeout=30)
    body = r.json()
    print(f"  文档 {doc_id[:8]} status→{status}: code={body.get('code')} {body.get('message')}")
    return body.get("code") == 0


def search_and_report(client, tag):
    data = client.search_datasets([DS4], PROBE_Q, top_k=10)
    chunks = data.get("chunks", [])
    union = "\n".join(c.get("content_with_weight") or "" for c in chunks)
    names = [(c.get("document_name") or "?") for c in chunks]
    hit = [(i + 1, n, round(c.get("similarity", 0), 3)) for i, (n, c) in
           enumerate(zip(names, chunks)) if ANCHOR in (c.get("content_with_weight") or "")]
    print(f"[{tag}] 锚点命中分片: {hit or '无'}")
    print(f"[{tag}] 召回文档分布: {sorted(set(names))}")
    return bool(hit)


def chat_and_report(tag):
    r = requests.post(f"{API}/chats/{CHAT_ID}/completions", headers=H,
                      json={"messages": [{"role": "user", "content": PROBE_Q}],
                            "stream": False}, timeout=540)
    d = r.json()["data"]
    refs = (d.get("reference") or {}).get("chunks", [])
    cited = sorted({c.get("document_name") or "?" for c in refs})
    answer = d.get("answer", "")
    print(f"[{tag}] 问答 anchor={'✓' if ANCHOR in answer else '✗'} 引用={cited}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-md", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    args = ap.parse_args()

    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)

    md = client.find_document_by_name(DS4, MD_NAME)
    if not md:
        raise SystemExit("ds4 中未找到 md 文档")
    if args.rollback:
        set_doc_status(md["id"], "1")
        return
    print(f"md 文档: {md['id']} chunks={md.get('chunk_count')} run={md.get('run')}")

    # 1) 上传 png（幂等，不 parse）
    png = client.find_document_by_name(DS4, PNG_NAME)
    if png:
        print(f"png 已存在: {png['id']} run={png.get('run')} chunks={png.get('chunk_count')}")
    else:
        uploaded = client.upload_document(DS4, PNG_PATH)
        png = uploaded[0] if isinstance(uploaded, list) else uploaded
        print(f"png 上传成功: {png['id']} name={png.get('name')}")

    # 2) 元数据对齐 md（不变更解析配置，保持 UNSTART）
    if not png.get("meta_fields"):
        client.patch_document(DS4, png["id"], md.get("meta_fields") or {})
        print("png 元数据: 已按 md 复制")
    else:
        print(f"png 元数据: 已有 {list(png['meta_fields'].keys())[:4]}…")

    # 3) 挂切片（幂等：png 已有切片则跳过）
    png_full = client.find_document_by_name(DS4, PNG_NAME)
    if int(png_full.get("chunk_count") or 0) > 0:
        print(f"png 已有 {png_full['chunk_count']} 条切片，跳过挂载")
    else:
        chunks = client.list_chunks(DS4, md["id"])
        print(f"从 md 读到 {len(chunks)} 条切片，开始挂载…")
        for c in chunks:
            cid = add_chunk(png["id"], c["content"],
                            (c.get("keywords") or None) or None)
            print(f"  + chunk {cid[:8]} ({len(c['content'])} 字)")

    # 4) 检索验证（md 仍启用，双份并存观察锚点落谁家）
    time.sleep(3)
    search_and_report(client, "挂载后/md 启用")

    # 5) 禁用 md → 复验
    if not args.keep_md:
        set_doc_status(md["id"], "0")
        time.sleep(3)
        ok = search_and_report(client, "md 禁用后")
        chat_and_report("md 禁用后")
        print(f"\n结果: 检索锚点 {'✓ 保持可达' if ok else '✗ 丢失——请回滚: python3 pilot_png_shell.py --rollback'}")
        print("回滚命令: python3 pilot_png_shell.py --rollback")
        print("前端验证: vision8_qa_test 里重问『桃曲坡水库大坝注册登记证的注册登记号是多少？』，")
        print("         引用应显示 大坝注册登记证.png，点开应直接预览原始扫描件。")


if __name__ == "__main__":
    main()
