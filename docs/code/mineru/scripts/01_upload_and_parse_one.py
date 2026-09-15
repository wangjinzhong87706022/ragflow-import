# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
# 文中的 KB / document / chunk ID 来自本次评测实例，按需替换。
import os
import requests, sys, os, json
BASE = os.getenv("RAGFLOW_API_BASE", "https://labragf.openagp.top:9080")
h = {"Authorization": f"Bearer {os.getenv('RAGFLOW_API_KEY', '')}"}
SRC_KB = "619a744aad0411f19cb4c765b4445b50"
DST_KB = "76a691aeae5b11f1896583d540e218a6"
DOCS = {
    "447": ("699adc2aad0411f19cb4c765b4445b50", "SL-T-447-2026_水土保持项目前期设计文件编制技术规程.pdf"),
    "352": ("6920f086ad0411f19cb4c765b4445b50", "SL-T-352-2020_水工混凝土试验规程.pdf"),
    "101": ("68f62798ad0411f19cb4c765b4445b50", "SL-101-2014_水工钢闸门和启闭机安全检测技术规程.pdf"),
}
key = sys.argv[1]
doc_id, fname = DOCS[key]
outdir = os.path.join(os.environ.get("TEMP", "."), "opencode", "pdfs")
os.makedirs(outdir, exist_ok=True)
local = os.path.join(outdir, fname)

# 1. download original
r = requests.get(f"{BASE}/api/v1/datasets/{SRC_KB}/documents/{doc_id}", headers=h, timeout=300)
print("download:", r.status_code, "ctype:", r.headers.get("Content-Type"), "bytes:", len(r.content), flush=True)
open(local, "wb").write(r.content)

# 2. upload to new dataset
with open(local, "rb") as f:
    r = requests.post(f"{BASE}/api/v1/datasets/{DST_KB}/documents", headers=h, timeout=600,
                      files=[("file", (fname, f, "application/pdf"))])
j = r.json()
print("upload:", j.get("code"), str(j.get("message"))[:200], flush=True)
if j.get("code") != 0:
    sys.exit(1)
new_doc = j["data"][0]["id"]
print("NEW_DOC =", new_doc, flush=True)

# 3. set parser_config
pc = {"layout_recognize": "MinerU", "chunk_token_num": 1024, "delimiter": "\n!?;。；！？"}
r = requests.put(f"{BASE}/api/v1/datasets/{DST_KB}/documents/{new_doc}", headers=h, timeout=60, json={"parser_config": pc})
print("set cfg:", r.json().get("code"), flush=True)

# 4. trigger parse
r = requests.post(f"{BASE}/api/v1/datasets/{DST_KB}/chunks", headers=h, timeout=120, json={"document_ids": [new_doc]})
print("trigger:", r.status_code, json.dumps(r.json(), ensure_ascii=False)[:150], flush=True)
print("DONE_STEP key=%s doc=%s" % (key, new_doc), flush=True)
