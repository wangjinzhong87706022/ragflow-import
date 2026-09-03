#!/usr/bin/env python3
"""剩余 8 图融合文本导入（ds2×4 基础数据 + ds4×4 组织管理）——文本化导入第二批（8/12）。

- 默认 dry-run：只打印将执行的动作，不做任何写操作。
- `--apply` 为显式写入门，且**必须先通过人工评审**（out/vision_8chart_test/approved.flag 存在）。
- 幂等：按文档名查重，已存在则跳过上传（不重复导入）。
- 流程：upload → patch_document(11 字段元数据) → parse → wait。
- 来源矩阵/判级/仲裁/幻觉留痕见同目录 FUSION_REPORT.md。
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path("/opt/wangjz/ragflow-import/src")
sys.path.insert(0, str(SRC))

from config import (PUBLIC_PEM, RAGFLOW_API_KEY, RAGFLOW_EMAIL,  # noqa: E402
                    RAGFLOW_PASSWORD)
from ragflow_client import RAGFlowClient  # noqa: E402

DS2 = "d6ec4e36a1d711f19d7235ad4ea699d4"  # 基础数据（setup_state.json 同值）
DS4 = "d756f7b8a1d711f19d7235ad4ea699d4"  # 组织管理（setup_state.json 同值）

# 与 FUSION_REPORT.md 第五节一致；rel 指向语料源图（read-only，仅作溯源）
DOCS = [
    # ---- ds2 基础数据（沿用第一轮 ds2 口径）----
    {"ds": DS2, "file": "fusion/水库基本信息.md",
     "meta": {"doc_category": "基础数据", "sub_category": "水库枢纽概况与工程参数（VLM融合文本）",
              "flood_event": "历年统计", "doc_type": "图片",
              "source_format": "ocr_jpg", "quality": "high", "doc_nature": "技术",
              "location": "全库", "flood_magnitude": "不适用",
              "rel": "05-基础数据与曲线/01-水库基本信息/水库基本信息.jpg"}},
    {"ds": DS2, "file": "fusion/溢洪道图2.md",
     "meta": {"doc_category": "基础数据", "sub_category": "溢洪道闸门与启闭机参数（VLM融合文本）",
              "flood_event": "历年统计", "doc_type": "图片",
              "source_format": "ocr_jpg", "quality": "high", "doc_nature": "技术",
              "location": "溢洪道", "flood_magnitude": "不适用",
              "rel": "05-基础数据与曲线/03-溢洪道信息/溢洪道图2.jpg"}},
    {"ds": DS2, "file": "fusion/物资图1.md",
     "meta": {"doc_category": "基础数据", "sub_category": "防汛物资储备限额（VLM融合文本）",
              "flood_event": "历年统计", "doc_type": "图片",
              "source_format": "ocr_jpg", "quality": "high", "doc_nature": "管理",
              "location": "全库", "flood_magnitude": "不适用",
              "rel": "05-基础数据与曲线/07-抢险物资信息/物资图1.jpg"}},
    {"ds": DS2, "file": "fusion/物资图2.md",
     "meta": {"doc_category": "基础数据", "sub_category": "枢纽站实有物资清单（VLM融合文本）",
              "flood_event": "历年统计", "doc_type": "图片",
              "source_format": "ocr_jpg", "quality": "high", "doc_nature": "管理",
              "location": "全库", "flood_magnitude": "不适用",
              "rel": "05-基础数据与曲线/07-抢险物资信息/物资图2.jpg"}},
    # ---- ds4 组织管理（沿用在库 ds4 口径：管理/不适用/flood_event 留空）----
    {"ds": DS4, "file": "fusion/三个责任人.md",
     "meta": {"doc_category": "组织管理", "sub_category": "防汛安全责任人名单（VLM融合文本）",
              "doc_type": "图片", "source_format": "ocr_jpg", "quality": "high",
              "doc_nature": "管理", "location": "不适用", "flood_magnitude": "不适用",
              "rel": "07-管理资料/01-组织架构与责任人/三个责任人.jpg"}},
    {"ds": DS4, "file": "fusion/中心架构图.md",
     "meta": {"doc_category": "组织管理", "sub_category": "组织机构图（VLM融合文本）",
              "doc_type": "图片", "source_format": "ocr_png", "quality": "high",
              "doc_nature": "管理", "location": "不适用", "flood_magnitude": "不适用",
              "rel": "07-管理资料/01-组织架构与责任人/中心架构图.png"}},
    {"ds": DS4, "file": "fusion/大坝注册登记证.md",
     "meta": {"doc_category": "组织管理", "sub_category": "水库大坝注册登记证（VLM融合文本）",
              "doc_type": "图片", "year": 2023, "source_format": "ocr_png",
              "quality": "high", "doc_nature": "管理", "location": "不适用",
              "flood_magnitude": "不适用",
              "rel": "07-管理资料/02-注册登记证/大坝注册登记证.png"}},
    {"ds": DS4, "file": "fusion/各部门用水需求.md",
     "meta": {"doc_category": "组织管理", "sub_category": "各部门用水需求（VLM融合文本）",
              "doc_type": "图片", "year": 2026, "source_format": "ocr_jpg",
              "quality": "high", "doc_nature": "管理", "location": "不适用",
              "flood_magnitude": "不适用",
              "rel": "07-管理资料/03-供配水计划/各部门用水需求.jpg"}},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="显式写入（默认 dry-run）")
    args = ap.parse_args()

    flag = HERE / "approved.flag"
    if args.apply and not flag.exists():
        sys.exit("[评审门] 缺少 out/vision_8chart_test/approved.flag——请先人工审阅 "
                 "fusion/*.md 与 FUSION_REPORT.md，通过后 touch approved.flag")

    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== 剩余 8 图融合文本导入 ds2×4 + ds4×4 [{mode}] ===")

    by_ds: dict = {}
    for spec in DOCS:
        path = HERE / spec["file"]
        name = path.name
        ds = spec["ds"]
        meta = dict(spec["meta"])
        if not path.exists():
            print(f"  [缺失] {spec['file']}")
            continue
        existing = client.find_document_by_name(ds, name)
        if existing:
            print(f"  [跳过] {name} 已存在（doc={existing['id']}，不重复导入）")
            continue
        print(f"  [计划] {name} → {'ds2' if ds == DS2 else 'ds4'}"
              f" / {meta['sub_category']}（{meta['doc_type']}/{meta['quality']}）")
        if not args.apply:
            continue
        doc = client.upload_document(ds, path, filename=name)
        doc_id = doc["id"] if isinstance(doc, dict) else doc[0]["id"]
        client.patch_document(ds, doc_id, meta)
        by_ds.setdefault(ds, []).append((name, doc_id))

    if args.apply and by_ds:
        for ds, docs in by_ds.items():
            tag = "ds2" if ds == DS2 else "ds4"
            client.parse_documents(ds, [d for _, d in docs])
            for name, doc_id in docs:
                final = client.wait_document(ds, doc_id, timeout=300)
                print(f"  [解析:{tag}] {name} → run={final.get('run')} "
                      f"progress={final.get('progress')} chunks={final.get('chunk_count')}")
    elif not args.apply:
        print("（dry-run 结束：未写入任何内容。--apply 需 approved.flag）")


if __name__ == "__main__":
    main()
