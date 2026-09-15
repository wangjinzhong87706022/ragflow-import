# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
# 文中的 KB / document / chunk ID 来自本次评测实例，按需替换。
import os
import fitz, os

# build source with CJK font, then rasterize to an image-only PDF (scanned-like)
FONT = r"C:\Windows\Fonts\simsun.ttc"
tmp = fitz.open()

def draw_table(page, ox, oy, cw, rh, data, rotate_cells=False):
    for r, row in enumerate(data):
        for c, txt in enumerate(row):
            x0, y0 = ox + c * cw, oy + r * rh
            page.draw_rect(fitz.Rect(x0, y0, x0 + cw, y0 + rh), color=(0, 0, 0), width=0.8)
            if rotate_cells:
                page.insert_text((x0 + cw - 8, y0 + 6), txt, fontname="cjk", fontfile=FONT, fontsize=11, rotate=90)
            else:
                page.insert_text((x0 + 6, y0 + rh - 8), txt, fontname="cjk", fontfile=FONT, fontsize=11)

data = [
    ["序号", "项目", "单位", "数量"],
    ["1", "土方开挖", "m3", "1250"],
    ["2", "土方回填", "m3", "890"],
    ["3", "混凝土浇筑", "m3", "360"],
    ["4", "钢筋制安", "t", "45"],
]
p1 = tmp.new_page(width=595, height=842)
p1.insert_text((60, 80), "附表A 工程量汇总表（正常横排-扫描）", fontname="cjk", fontfile=FONT, fontsize=14)
draw_table(p1, 60, 120, 110, 40, data, False)
p2 = tmp.new_page(width=595, height=842)
p2.insert_text((60, 80), "附表B 工程量汇总表（竖排旋转90度-扫描）", fontname="cjk", fontfile=FONT, fontsize=14)
draw_table(p2, 60, 120, 110, 40, data, True)

out = fitz.open()
for i in range(tmp.page_count):
    pix = tmp[i].get_pixmap(dpi=200)
    img_pdf_bytes = pix.tobytes("png")
    page = out.new_page(width=tmp[i].rect.width, height=tmp[i].rect.height)
    page.insert_image(page.rect, stream=img_pdf_bytes)

dst = r"C:\Users\ADMINI~1\AppData\Local\Temp\opencode\pdfs\vertical_scan.pdf"
out.save(dst)
chk = fitz.open(dst)
print("image-only pdf text (should be empty):", repr(chk[0].get_text()[:50]))
print("saved", dst, os.path.getsize(dst), "bytes")
for i in range(chk.page_count):
    fn = dst.replace(".pdf", f"_p{i+1}.png")
    chk[i].get_pixmap(dpi=120).save(fn)
    print("rendered", fn)
