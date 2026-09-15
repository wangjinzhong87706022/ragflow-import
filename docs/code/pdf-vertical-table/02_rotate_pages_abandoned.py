#!/usr/bin/env python3
"""【已弃用，留档】PDF 级旋转竖排表格页方案.

为什么弃用：
  1. 旋转破坏原文档，且解析收益有限；
  2. 首次执行时按"印刷页码"误转 p76/p77（实为竖向 D.0.1-2 续表）——
     必须用 PDF 绝对页码，且旋转后务必渲染复核标题方向；
  3. 最终方案改为 RAGFlow 原生 table_vision_enhance（见 03 脚本），
     VLM 在表格区域级做视觉重建，无需旋转即可正确解析。

若确需旋转（例如解析器不支持视觉增强），本脚本保留正确的 6 页页码：
  PDF p83/p84/p89/p91/p92/p95 = D.0.4/D.0.5/D.0.13/D.0.15/D.0.16/D.0.20
"""
import os

import pymupdf

PDF_IN = r"D:\doc\taineng\智能体平台\SL-T-447-2026_水土保持项目前期设计文件编制技术规程.pdf"
PDF_OUT = r"output\SL-T-447-2026_rotated.pdf"

# 0-indexed；1-based 为 83/84/89/91/92/95
ROTATE_PAGES_0BASED = [82, 83, 88, 90, 91, 94]


def main():
    doc = pymupdf.open(PDF_IN)
    print(f"Input: {len(doc)} pages")
    print(f"Rotating pages (1-based): {[p + 1 for p in ROTATE_PAGES_0BASED]}")
    for idx in ROTATE_PAGES_0BASED:
        page = doc[idx]
        page.set_rotation(270)  # 270° = 逆时针 90°，把横排内容转正
        print(f"  Page {idx + 1}: rotation -> {page.rotation}°")
    os.makedirs(os.path.dirname(PDF_OUT), exist_ok=True)
    doc.save(PDF_OUT, garbage=4, deflate=True)
    doc.close()
    print(f"Saved: {PDF_OUT} ({os.path.getsize(PDF_OUT):,} bytes)")


def verify_rotation(pdf_path, pages_1based):
    """旋转后复核：渲染并确认标题方向正确（教训：页码判错必须靠渲染发现）。"""
    doc = pymupdf.open(pdf_path)
    for pg in pages_1based:
        pix = doc[pg - 1].get_pixmap(matrix=pymupdf.Matrix(150 / 72, 150 / 72))
        out = f"output/verify_page_{pg:03d}.png"
        pix.save(out)
        print(f"  page {pg}: rotation={doc[pg-1].rotation}° -> {out}")
    doc.close()


if __name__ == "__main__":
    main()
    verify_rotation(PDF_OUT, [p + 1 for p in ROTATE_PAGES_0BASED])
