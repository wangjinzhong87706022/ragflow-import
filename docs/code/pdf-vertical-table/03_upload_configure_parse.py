#!/usr/bin/env python3
"""上传原始（未旋转）PDF + 写入 table_vision_enhance 配置 + 触发解析 + 轮询.

关键 API 语义（实测确认）：
  - 上传：POST /datasets/{ds}/documents（multipart，必须用 requests，urllib 手拼会 413）
  - 更新配置：PATCH /datasets/{ds}/documents/{doc_id}（PUT/POST 均 405）
  - vision enhance 写法：parser_config.ext.table_vision_enhance=true
    （顶层写法被 schema 拒绝 Extra inputs；服务端会同步回显到顶层）
  - 触发解析：POST /datasets/{ds}/documents/parse {"document_ids":[...]}
    （上传后 run=UNSTART，不显式触发不会解析）
  - 文档列表的 chunk_num 字段不可靠；以 chunks 接口 data.total 为准
"""
import os
import sys
import time

import requests
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

RAGFLOW_BASE = "https://labragf.openagp.top:9080"
RAGFLOW_API_KEY = "ragflow-XXXX"          # ← 替换
DATASET_ID = "d42ae68aab5011f1a33465638b9ea3fd"
PDF_PATH = r"D:\doc\taineng\智能体平台\SL-T-447-2026_水土保持项目前期设计文件编制技术规程.pdf"
DOC_NAME = "SL-T-447-2026.pdf"

# 与已成功解析的 p79-96(2).pdf 一致的解析配置
PARSER_CONFIG = {
    "chunk_method": "naive",
    "parser_config": {
        "auto_keywords": 0,
        "auto_questions": 0,
        "chunk_token_num": 512,
        "delimiter": "\\n!?;。；！？",
        "layout_recognize": "DeepDOC",
        "html4excel": False,
        "filename_embd_weight": 0.1,
        "ext": {"table_vision_enhance": True},
    },
}


def upload_pdf(pdf_path, doc_name):
    """multipart 上传；返回新文档 ID。"""
    with open(pdf_path, "rb") as f:
        files = {"file": (doc_name, f, "application/pdf")}
        r = requests.post(
            f"{RAGFLOW_BASE}/api/v1/datasets/{DATASET_ID}/documents",
            headers={"Authorization": f"Bearer {RAGFLOW_API_KEY}"},
            files=files, verify=False, timeout=300,
        )
    resp = r.json()
    if resp.get("code") != 0:
        raise RuntimeError(f"upload failed: {resp}")
    doc_id = resp["data"][0]["id"]
    print(f"Uploaded {doc_name} ({os.path.getsize(pdf_path):,} bytes) -> {doc_id}")
    return doc_id


def patch_parser_config(doc_id):
    """PATCH 更新解析配置；验证 table_vision_enhance 已生效。"""
    r = requests.patch(
        f"{RAGFLOW_BASE}/api/v1/datasets/{DATASET_ID}/documents/{doc_id}",
        headers={"Authorization": f"Bearer {RAGFLOW_API_KEY}",
                 "Content-Type": "application/json"},
        json=PARSER_CONFIG, verify=False, timeout=60,
    )
    resp = r.json()
    if resp.get("code") != 0:
        raise RuntimeError(f"PATCH failed: {resp}")
    # 验证回显
    r = requests.get(
        f"{RAGFLOW_BASE}/api/v1/datasets/{DATASET_ID}/documents?page=1&page_size=20",
        headers={"Authorization": f"Bearer {RAGFLOW_API_KEY}"}, verify=False, timeout=30,
    )
    for doc in r.json().get("data", {}).get("docs", []):
        if doc["id"] == doc_id:
            pc = doc.get("parser_config", {})
            assert pc.get("table_vision_enhance") is True, "vision enhance not echoed"
            print(f"Config OK: table_vision_enhance={pc.get('table_vision_enhance')}")


def trigger_parse(doc_ids):
    """触发解析（上传后 run=UNSTART，必须显式触发）。"""
    r = requests.post(
        f"{RAGFLOW_BASE}/api/v1/datasets/{DATASET_ID}/documents/parse",
        headers={"Authorization": f"Bearer {RAGFLOW_API_KEY}",
                 "Content-Type": "application/json"},
        json={"document_ids": doc_ids}, verify=False, timeout=60,
    )
    print(f"Parse trigger: {r.json()}")


def poll_until_done(doc_id, interval=15, max_rounds=200):
    for i in range(max_rounds):
        time.sleep(interval)
        r = requests.get(
            f"{RAGFLOW_BASE}/api/v1/datasets/{DATASET_ID}/documents?page=1&page_size=20",
            headers={"Authorization": f"Bearer {RAGFLOW_API_KEY}"}, verify=False, timeout=30,
        )
        for doc in r.json().get("data", {}).get("docs", []):
            if doc["id"] == doc_id:
                run, progress = doc.get("run"), doc.get("progress", 0)
                pct = round(progress * 100, 1) if progress <= 1 else round(progress, 1)
                print(f"  [{(i + 1) * interval}s] run={run} progress={pct}%")
                if run in ("done", "FAIL"):
                    return run
    return "TIMEOUT"


def main():
    doc_id = upload_pdf(PDF_PATH, DOC_NAME)
    patch_parser_config(doc_id)
    trigger_parse([doc_id])
    run = poll_until_done(doc_id)
    print(f"Final run={run}")
    if run != "done":
        sys.exit(1)


if __name__ == "__main__":
    main()
