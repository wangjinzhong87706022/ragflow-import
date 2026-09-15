# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
# 文中的 KB / document / chunk ID 来自本次评测实例，按需替换。
import os
import requests, re
BASE = os.getenv("RAGFLOW_API_BASE", "https://labragf.openagp.top:9080")
h = {"Authorization": f"Bearer {os.getenv('RAGFLOW_API_KEY', '')}"}
KB, DOC = "76a691aeae5b11f1896583d540e218a6", "086ccd2eae5c11f1896583d540e218a6"

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
imgs = [c for c in cs if c.get("doc_type_kwd") == "image"]
print(f"total chunks={len(cs)} image chunks={len(imgs)}")

cjk = re.compile(r"[\u4e00-\u9fff]")
latin = re.compile(r"[A-Za-z]")
zh = en = mixed = 0
for c in imgs:
    t = c.get("content", "")
    nc, nl = len(cjk.findall(t)), len(latin.findall(t))
    if nc >= 5 and nc >= nl:
        zh += 1
    elif nl > nc:
        en += 1
    else:
        mixed += 1
print(f"Chinese-dominant={zh}  English-dominant={en}  other/mixed={mixed}")

print("\n--- samples ---")
for c in imgs[:5]:
    t = c.get("content", "").replace("\n", " ")
    nc, nl = len(cjk.findall(t)), len(latin.findall(t))
    print(f"[CJK={nc} LAT={nl}] {t[:220]}")
    print("-" * 60)
