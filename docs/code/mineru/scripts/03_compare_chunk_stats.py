# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
# 文中的 KB / document / chunk ID 来自本次评测实例，按需替换。
import os
import requests, collections, statistics, re, json
BASE = os.getenv("RAGFLOW_API_BASE", "https://labragf.openagp.top:9080")
h = {"Authorization": f"Bearer {os.getenv('RAGFLOW_API_KEY', '')}"}

MINERU = {"447": ("76a691aeae5b11f1896583d540e218a6", "a4c4e770ae5b11f1896583d540e218a6"),
          "352": ("76a691aeae5b11f1896583d540e218a6", "086ccd2eae5c11f1896583d540e218a6"),
          "101": ("76a691aeae5b11f1896583d540e218a6", "3142474aae5e11f1896583d540e218a6")}
PADDLE = {"447": ("619a744aad0411f19cb4c765b4445b50", "699adc2aad0411f19cb4c765b4445b50"),
          "352": ("619a744aad0411f19cb4c765b4445b50", "6920f086ad0411f19cb4c765b4445b50"),
          "101": ("619a744aad0411f19cb4c765b4445b50", "68f62798ad0411f19cb4c765b4445b50")}

FORMULA = re.compile(r"\$[^$\n]{2,}\$|\$\$|\\pm|\\times|\\frac|\\mathrm|\\circ|\\left|\\right")

def fetch(kb, doc):
    out, page = [], 1
    while True:
        d = (requests.get(f"{BASE}/api/v1/datasets/{kb}/documents/{doc}/chunks?page={page}&page_size=100", headers=h, timeout=120).json().get("data") or {})
        b = d.get("chunks") or []
        if not b:
            break
        out += b
        if len(out) >= (d.get("total") or 0) or page > 30:
            break
        page += 1
    return out

def stats(chunks):
    types = collections.Counter(c.get("doc_type_kwd") for c in chunks)
    lens = [len(c.get("content", "")) for c in chunks]
    tbl = sum(1 for c in chunks if "<table" in c.get("content", "").lower())
    fml = sum(1 for c in chunks if FORMULA.search(c.get("content", "")))
    return types, (statistics.mean(lens) if lens else 0), (statistics.median(lens) if lens else 0), tbl, fml

for key in ("447", "352", "101"):
    print("=" * 80)
    for tag, KB in (("MinerU", MINERU), ("PaddleOCR", PADDLE)):
        kb, doc = KB[key]
        cs = fetch(kb, doc)
        types, avg, med, tbl, fml = stats(cs)
        t = dict(types)
        print(f"[{key}] {tag:9s} total={len(cs):4d} | text={t.get('text',0):4d} table={t.get('table',0):3d} image={t.get('image',0):3d} | html_table={tbl:3d} formula={fml:4d} | avg={avg:.0f} med={med:.0f}")
        img = [c for c in cs if c.get("doc_type_kwd") == "image"]
        if img:
            print(f"      image sample: {img[0].get('content','')[:220].replace(chr(10),' ')}")
        if key == "352":
            f = [c for c in cs if c.get("doc_type_kwd") == "table" and "<table" in c.get("content","").lower()]
            if f:
                print(f"      table sample: {f[0].get('content','')[:180].replace(chr(10),' ')}")
