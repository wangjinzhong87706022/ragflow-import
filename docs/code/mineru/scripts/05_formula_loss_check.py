# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
# 文中的 KB / document / chunk ID 来自本次评测实例，按需替换。
import fitz, re, requests, json

PDF = r"C:\Users\ADMINI~1\AppData\Local\Temp\opencode\pdfs\SL-T-352-2020_水工混凝土试验规程.pdf"
BASE = os.getenv("RAGFLOW_API_BASE", "https://labragf.openagp.top:9080")
h = {"Authorization": f"Bearer {os.getenv('RAGFLOW_API_KEY', '')}"}
KB, DOC = "76a691aeae5b11f1896583d540e218a6", "086ccd2eae5c11f1896583d540e218a6"

# --- PDF side ---
doc = fitz.open(PDF)
pages_with_shizhong = []
total_shizhong = 0
for i, page in enumerate(doc):
    t = page.get_text()
    n = t.count("式中")
    if n:
        total_shizhong += n
        pages_with_shizhong.append((i + 1, n))
print(f"PDF pages={doc.page_count} | '式中' total={total_shizhong} on {len(pages_with_shizhong)} pages", flush=True)
print("pages with 式中 (first 20):", pages_with_shizhong[:20], flush=True)

# --- MinerU chunks ---
def fetch(kb, d):
    out, pg = [], 1
    while True:
        x = (requests.get(f"{BASE}/api/v1/datasets/{kb}/documents/{d}/chunks?page={pg}&page_size=100", headers=h, timeout=120).json().get("data") or {})
        b = x.get("chunks") or []
        if not b:
            break
        out += b
        if len(out) >= (x.get("total") or 0) or pg > 30:
            break
        pg += 1
    return out

cs = fetch(KB, DOC)
latex_spans = 0
latex_re = re.compile(r"\$[^$\n]+\$|\$\$[^$]+\$\$")
shizhong_chunks = [c for c in cs if "式中" in c.get("content", "")]
shizhong_no_latex = [c for c in shizhong_chunks if not latex_re.search(c.get("content", ""))]
for c in cs:
    latex_spans += len(latex_re.findall(c.get("content", "")))
print(f"\nMinerU: chunks={len(cs)} | latex_spans={latex_spans}", flush=True)
print(f"chunks containing '式中'={len(shizhong_chunks)} | of those, NO latex={len(shizhong_no_latex)}", flush=True)

print("\n--- suspect chunks (式中 但无 LaTeX) samples ---", flush=True)
for c in shizhong_no_latex[:5]:
    print("pos:", c.get("positions"), "|", c.get("content", "")[:260].replace("\n", " "), flush=True)
    print("-" * 60, flush=True)

# render suspect pages for visual check
outdir = r"C:\Users\ADMINI~1\AppData\Local\Temp\opencode\formula_pages"
import os
os.makedirs(outdir, exist_ok=True)
suspect_pages = set()
for c in shizhong_no_latex:
    m = re.findall(r"\[\[?(\d+)", str(c.get("positions")))
    for p in re.findall(r"\[(\d+),", str(c.get("positions"))):
        suspect_pages.add(int(p))
for p in sorted(suspect_pages)[:6]:
    if 1 <= p <= doc.page_count:
        pix = doc[p - 1].get_pixmap(dpi=150)
        fn = os.path.join(outdir, f"page_{p}.png")
        pix.save(fn)
        print("rendered", fn, flush=True)
