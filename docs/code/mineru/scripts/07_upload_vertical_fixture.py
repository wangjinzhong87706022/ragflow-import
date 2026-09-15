# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
# 文中的 KB / document / chunk ID 来自本次评测实例，按需替换。
import os
import requests, json
BASE = os.getenv("RAGFLOW_API_BASE", "https://labragf.openagp.top:9080")
h = {"Authorization": f"Bearer {os.getenv('RAGFLOW_API_KEY', '')}"}
DST_KB = "76a691aeae5b11f1896583d540e218a6"
PDF = r"C:\Users\ADMINI~1\AppData\Local\Temp\opencode\pdfs\vertical_scan.pdf"
with open(PDF, "rb") as f:
    r = requests.post(f"{BASE}/api/v1/datasets/{DST_KB}/documents", headers=h, timeout=600,
                      files=[("file", ("vertical_scan.pdf", f, "application/pdf"))])
j = r.json()
print("upload:", j.get("code"), str(j.get("message"))[:150])
doc = j["data"][0]["id"]
print("DOC =", doc)
pc = {"layout_recognize": "MinerU", "chunk_token_num": 512, "delimiter": "\n!?;。；！？"}
print("set cfg:", requests.put(f"{BASE}/api/v1/datasets/{DST_KB}/documents/{doc}", headers=h, timeout=60, json={"parser_config": pc}).json().get("code"))
r = requests.post(f"{BASE}/api/v1/datasets/{DST_KB}/chunks", headers=h, timeout=120, json={"document_ids": [doc]})
print("trigger:", r.status_code, json.dumps(r.json(), ensure_ascii=False)[:120])
