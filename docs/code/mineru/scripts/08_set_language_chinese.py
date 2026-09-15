# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
# 文中的 KB / document / chunk ID 来自本次评测实例，按需替换。
import os
import requests, json
BASE = os.getenv("RAGFLOW_API_BASE", "https://labragf.openagp.top:9080")
h = {"Authorization": f"Bearer {os.getenv('RAGFLOW_API_KEY', '')}"}
KB = "76a691aeae5b11f1896583d540e218a6"  # mineru-retest
r = requests.put(f"{BASE}/api/v1/datasets/{KB}", headers=h, timeout=40, json={"language": "Chinese"})
print("set language:", r.json().get("code"), str(r.json().get("message"))[:200])
r2 = requests.get(f"{BASE}/api/v1/datasets?page=1&page_size=100", headers=h, timeout=40)
kb = next((x for x in r2.json()["data"] if x["id"] == KB), {})
print("language now:", kb.get("language"))
# re-parse 101 (small doc)
doc = "3142474aae5e11f1896583d540e218a6"
rr = requests.post(f"{BASE}/api/v1/datasets/{KB}/chunks", headers=h, timeout=60, json={"document_ids": [doc]})
print("trigger 101:", rr.status_code, json.dumps(rr.json(), ensure_ascii=False)[:100])
