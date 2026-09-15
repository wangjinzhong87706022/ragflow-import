# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
# 文中的 KB / document / chunk ID 来自本次评测实例，按需替换。
import os
import requests, re
BASE = os.getenv("RAGFLOW_API_BASE", "https://labragf.openagp.top:9080")
h = {"Authorization": f"Bearer {os.getenv('RAGFLOW_API_KEY', '')}"}
KB = "76a691aeae5b11f1896583d540e218a6"
DOC = "086ccd2eae5c11f1896583d540e218a6"  # 352

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

cs = fetch(KB, DOC)
imgs = [c for c in cs if c.get("doc_type_kwd") == "image"]
fmls = [c for c in cs if c.get("doc_type_kwd") != "image" and re.search(r"\$|\\frac|\\mathrm|\\pm", c.get("content", ""))]
tbls = [c for c in cs if c.get("doc_type_kwd") == "table"]
print(f"352: images={len(imgs)} formula-ish={len(fmls)} tables={len(tbls)}")
print("\n### IMAGE SAMPLES ###")
for c in imgs[:3]:
    print("image_id:", c.get("image_id"), "| pos:", c.get("positions"))
    print("content:", c.get("content", "")[:400].replace("\n", " "))
    print("-" * 60)
print("\n### FORMULA SAMPLES ###")
for c in fmls[:3]:
    print("content:", c.get("content", "")[:300].replace("\n", " "))
    print("-" * 60)
print("\n### TABLE SAMPLE ###")
if tbls:
    print(tbls[0].get("content", "")[:400].replace("\n", " "))
