# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
# 文中的 KB / document / chunk ID 来自本次评测实例，按需替换。
import os
import requests, json
BASE = os.getenv("RAGFLOW_API_BASE", "https://labragf.openagp.top:9080")
h = {"Authorization": f"Bearer {os.getenv('RAGFLOW_API_KEY', '')}"}
HKB = "f4c44446ae8311f1896583d540e218a6"  # vlm-redesc helper KB

d = (requests.get(f"{BASE}/api/v1/datasets/{HKB}/documents?page_size=100", headers=h, timeout=40).json().get("data") or {})
docs = d.get("docs", [])
print("helper KB docs:", [(x["id"], x["name"]) for x in docs])
ids = [x["id"] for x in docs]
if ids:
    r = requests.delete(f"{BASE}/api/v1/datasets/{HKB}/documents", headers=h, timeout=120, json={"ids": ids})
    print("delete:", r.status_code, json.dumps(r.json(), ensure_ascii=False)[:200])
    d2 = (requests.get(f"{BASE}/api/v1/datasets/{HKB}/documents?page_size=100", headers=h, timeout=40).json().get("data") or {})
    print("remaining docs:", len(d2.get("docs", [])))
else:
    print("no docs to delete")
