# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
# 文中的 KB / document / chunk ID 来自本次评测实例，按需替换。
import os
import requests, time, sys
BASE = os.getenv("RAGFLOW_API_BASE", "https://labragf.openagp.top:9080")
h = {"Authorization": f"Bearer {os.getenv('RAGFLOW_API_KEY', '')}"}
DST_KB = "76a691aeae5b11f1896583d540e218a6"
doc = sys.argv[1]
seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 540
deadline = time.time() + seconds
while time.time() < deadline:
    try:
        r = requests.get(f"{BASE}/api/v1/datasets/{DST_KB}/documents?page_size=100", headers=h, timeout=40)
        d = next((x for x in r.json()["data"]["docs"] if x["id"] == doc), None)
        if d:
            print(f'{time.strftime("%H:%M:%S")} run={d.get("run")} prog={round(float(d.get("progress") or 0),3)} chunks={d.get("chunk_count")} dur={d.get("process_duration")}', flush=True)
            if d.get("run") in ("DONE", "FAIL", "CANCEL"):
                break
        else:
            print("doc not found yet", flush=True)
    except Exception as e:
        print("poll err", repr(e)[:120], flush=True)
    time.sleep(45)
