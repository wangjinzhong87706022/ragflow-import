# NOTE: 运行前设置环境变量 RAGFLOW_API_BASE / RAGFLOW_API_KEY。
# 文中的 KB / document / chunk ID 来自本次评测实例，按需替换。
import os
import requests, json
BASE = os.getenv("RAGFLOW_API_BASE", "https://labragf.openagp.top:9080")
h = {"Authorization": f"Bearer {os.getenv('RAGFLOW_API_KEY', '')}"}

# create new dataset for MinerU retest
body = {
    "name": "mineru-retest",
    "description": "MinerU retest KB",
    "chunk_method": "naive",
    "parser_config": {
        "layout_recognize": "MinerU",
        "chunk_token_num": 1024,
        "delimiter": "\n!?;。；！？",
    },
}
r = requests.post(f"{BASE}/api/v1/datasets", headers=h, timeout=40, json=body)
j = r.json()
print("create dataset:", j.get("code"), j.get("message"))
if j.get("code") == 0:
    print("KB_ID =", j["data"]["id"])
else:
    print(json.dumps(j, ensure_ascii=False)[:500])

# test whether mineru_* option keys are accepted by this server version
if j.get("code") == 0:
    kb = j["data"]["id"]
    test = dict(body["parser_config"])
    test.update({"mineru_parse_method": "auto", "mineru_formula_enable": True, "mineru_table_enable": True, "mineru_lang": "ch"})
    r2 = requests.put(f"{BASE}/api/v1/datasets/{kb}", headers=h, timeout=30, json={"parser_config": test})
    print("mineru_* keys test:", r2.json().get("code"), str(r2.json().get("message"))[:300])
