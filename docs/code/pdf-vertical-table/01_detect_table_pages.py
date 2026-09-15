#!/usr/bin/env python3
"""扫描版 PDF 表格页 / 横排（旋转）页检测.

原理：扫描件无文字层、无矢量图形，只能渲染位图做像素分析。
对每页渲染 100 DPI 位图，逐行统计最长连续暗像素游程：
  - 游程 > 30% 页宽 → 该行记为一条横向表格线
  - line_density = 表格线行数 / 总行数
  - line_density > 0.03 且 fill_ratio > 0.02 → 判为表格页
命中页再渲染 150 DPI 缩略图供人工确认（区分竖向窄表 vs 横排大表）。

实测（SL/T 447-2026，128 页）：
  命中 17 页；视觉复核后，横排（旋转 90°）大表为 PDF p83/p84/p89/p91/p92/p95
  （D.0.4/D.0.5/D.0.13/D.0.15/D.0.16/D.0.20），p70-80 为竖向窄表（D.0.1-x）。
"""
import os
import json

import pymupdf
import numpy as np

PDF_PATH = r"D:\doc\taineng\智能体平台\SL-T-447-2026_水土保持项目前期设计文件编制技术规程.pdf"
OUT_DIR = r"output\table_pages"
DPI_ANALYZE = 100
DPI_THUMB = 150
LINE_RUN_RATIO = 0.30   # 暗像素游程占页宽比例阈值
DENSITY_THRESHOLD = 0.03
FILL_THRESHOLD = 0.02


def analyze_page(page):
    """返回 (h_line_rows, line_density, fill_ratio)."""
    mat = pymupdf.Matrix(DPI_ANALYZE / 72, DPI_ANALYZE / 72)
    pix = page.get_pixmap(matrix=mat)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    gray = np.mean(img[:, :, :3], axis=2)
    binary = gray < 128
    img_h, img_w = binary.shape

    h_line_rows = 0
    for row in range(img_h):
        max_run = current_run = 0
        for px in binary[row, :]:
            if px:
                current_run += 1
                max_run = max(max_run, current_run)
            else:
                current_run = 0
        if max_run > img_w * LINE_RUN_RATIO:
            h_line_rows += 1

    line_density = h_line_rows / max(img_h, 1)
    fill_ratio = float(np.sum(binary)) / (img_h * img_w)
    return h_line_rows, line_density, fill_ratio


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    doc = pymupdf.open(PDF_PATH)
    print(f"Total pages: {len(doc)}")

    table_pages = []
    results = []
    for i in range(len(doc)):
        page = doc[i]
        h_lines, density, fill = analyze_page(page)
        is_table = density > DENSITY_THRESHOLD and fill > FILL_THRESHOLD
        results.append({
            "page": i + 1,
            "h_line_rows": int(h_lines),
            "line_density": round(density, 5),
            "fill_ratio": round(fill, 5),
            "is_table": bool(is_table),
        })
        if is_table or h_lines > 40:
            table_pages.append(i + 1)
            print(f"  Page {i+1:3d}: lines={h_lines:4d} density={density:.4f} fill={fill:.4f}")

    # 连续区间分组
    groups = []
    for pg in sorted(table_pages):
        if groups and pg - groups[-1]["end"] <= 1:
            groups[-1]["end"] = pg
        else:
            groups.append({"start": pg, "end": pg})

    print(f"\nTable pages: {len(table_pages)} / {len(results)}")
    for g in groups:
        print(f"  Pages {g['start']:3d}-{g['end']:3d} ({g['end']-g['start']+1} pages)")

    # 渲染缩略图供人工复核
    for pg in table_pages:
        pix = doc[pg - 1].get_pixmap(matrix=pymupdf.Matrix(DPI_THUMB / 72, DPI_THUMB / 72))
        pix.save(os.path.join(OUT_DIR, f"page_{pg:03d}.png"))

    with open(os.path.join(OUT_DIR, "results.json"), "w", encoding="utf-8") as f:
        json.dump({"results": results, "table_pages": table_pages, "groups": groups}, f,
                  ensure_ascii=False, indent=1)
    doc.close()
    print(f"\nThumbnails + results.json saved to {OUT_DIR}")


if __name__ == "__main__":
    main()
