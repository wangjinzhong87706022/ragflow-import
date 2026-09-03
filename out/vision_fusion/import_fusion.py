#!/usr/bin/env python3
"""四图融合文本导入 ds2（基础数据）—— 文本化导入首批落地（4/12）。

- 默认 dry-run：只打印将执行的动作，不做任何写操作。
- `--apply` 为显式写入门，且**必须先通过人工评审**（out/vision_fusion/approved.flag 存在）。
- 幂等：按文档名查重，已存在则跳过上传（不重复导入）。
- 流程：upload → patch_document(11 字段元数据) → parse → wait。
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path("/opt/wangjz/ragflow-import/src")
sys.path.insert(0, str(SRC))

from config import (PUBLIC_PEM, RAGFLOW_API_KEY, RAGFLOW_EMAIL,  # noqa: E402
                    RAGFLOW_PASSWORD)
from ragflow_client import RAGFlowClient  # noqa: E402

DS2 = "d6ec4e36a1d711f19d7235ad4ea699d4"  # 基础数据（setup_state.json 同值）

# 与 FUSION_REPORT.md 第五节一致；rel 指向语料源图（read-only，仅作溯源）
DOCS = [
    {"file": "fusion/05-库容水位对照表.md",
     "meta": {"doc_category": "基础数据", "sub_category": "水位库容查对表（VLM融合文本）",
              "flood_event": "历年统计", "doc_type": "图片", "year": 1997,
              "source_format": "ocr_jpg", "quality": "high", "doc_nature": "技术",
              "location": "全库", "flood_magnitude": "不适用",
              "rel": "05-基础数据与曲线/05-库容水位对照表.jpg"}},
    {"file": "fusion/06-泄流曲线.md",
     "meta": {"doc_category": "基础数据", "sub_category": "泄流曲线（VLM融合文本）",
              "flood_event": "历年统计", "doc_type": "图片",
              "source_format": "ocr_jpg", "quality": "medium", "doc_nature": "技术",
              "location": "全库", "flood_magnitude": "不适用",
              "rel": "05-基础数据与曲线/06-泄流曲线.jpg"}},
    {"file": "fusion/02-大坝剖面图.md",
     "meta": {"doc_category": "基础数据", "sub_category": "主坝典型横断面图（VLM融合文本）",
              "flood_event": "历年统计", "doc_type": "图纸",
              "source_format": "ocr_jpg", "quality": "medium", "doc_nature": "技术",
              "location": "主坝", "flood_magnitude": "不适用",
              "rel": "05-基础数据与曲线/02-大坝剖面图.jpg"}},
    {"file": "fusion/溢洪道图1.md",
     "meta": {"doc_category": "基础数据", "sub_category": "溢洪道工程参数（VLM融合文本）",
              "flood_event": "历年统计", "doc_type": "图纸", "year": 2003,
              "source_format": "ocr_jpg", "quality": "high", "doc_nature": "技术",
              "location": "溢洪道", "flood_magnitude": "百年一遇",
              "rel": "05-基础数据与曲线/03-溢洪道信息/溢洪道图1.jpg"}},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="显式写入（默认 dry-run）")
    args = ap.parse_args()

    flag = HERE / "approved.flag"
    if args.apply and not flag.exists():
        sys.exit("[评审门] 缺少 out/vision_fusion/approved.flag——请先人工审阅 "
                 "fusion/*.md 与 FUSION_REPORT.md，通过后 touch approved.flag")

    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== 视觉融合文本导入 ds2 基础数据 [{mode}] ===")

    to_parse = []
    for spec in DOCS:
        path = HERE / spec["file"]
        name = path.name
        meta = dict(spec["meta"])
        if not path.exists():
            print(f"  [缺失] {spec['file']}")
            continue
        existing = client.find_document_by_name(DS2, name)
        if existing:
            print(f"  [跳过] {name} 已存在（doc={existing['id']}，不重复导入）")
            continue
        print(f"  [计划] 上传 {name} → 元数据 {meta['sub_category']}"
              f"（{meta['doc_type']}/{meta['quality']}）")
        if not args.apply:
            continue
        doc = client.upload_document(DS2, path, filename=name)
        doc_id = doc["id"] if isinstance(doc, dict) else doc[0]["id"]
        client.patch_document(DS2, doc_id, meta)
        to_parse.append((name, doc_id))

    if args.apply and to_parse:
        ids = [d for _, d in to_parse]
        client.parse_documents(DS2, ids)
        for name, doc_id in to_parse:
            final = client.wait_document(DS2, doc_id, timeout=300)
            print(f"  [解析] {name} → run={final.get('run')} "
                  f"progress={final.get('progress')} chunks={final.get('chunk_count')}")
    elif not args.apply:
        print("（dry-run 结束：未写入任何内容。--apply 需 approved.flag）")


if __name__ == "__main__":
    main()
