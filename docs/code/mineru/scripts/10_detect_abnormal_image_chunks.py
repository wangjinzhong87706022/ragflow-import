# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
# 文中的 KB / document / chunk ID 来自本次评测实例，按需替换。
import os
import requests, re, json
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

LABELS = ["Visual Type:", "Title:", "Axes / Legends / Labels:", "Data Points:", "Captions / Annotations:"]
LEAK_MARKERS = ["MODE 1", "STRUCTURED VISUAL DATA OUTPUT", "MODE 2"]
cjk = re.compile(r"[\u4e00-\u9fff]")

cs = fetch(KB, DOC)
imgs = [c for c in cs if c.get("doc_type_kwd") == "image"]
anomalies = []
for c in imgs:
    t = c.get("content", "")
    up = t.upper()
    is_leak = any(m in up for m in LEAK_MARKERS)
    rem = t
    for lb in LABELS:
        rem = rem.replace(lb, "")
    ncj = len(cjk.findall(rem))
    nlat = len(re.findall(r"[A-Za-z]", rem))
    is_empty = (ncj < 3) and (nlat <= 5)
    if is_leak or is_empty:
        anomalies.append((c.get("id"), c.get("image_id"), "LEAK" if is_leak else "EMPTY", t.replace("\n", " ")[:160]))

print(f"image chunks={len(imgs)} anomalies={len(anomalies)}")
for a in anomalies:
    print(json.dumps(a, ensure_ascii=False))
