# 工程图纸 PDF 解析优化 — 代码操作指南

- **目标**：通过 Python 脚本和 API 调用完成工程图纸 PDF 的解析优化
- **适用场景**：批量操作、自动化处理、前端 UI 不便操作时
- **前置条件**：Python 3.13 + requests + PyMuPDF + PIL

---

## 一、环境准备

### 1.1 安装依赖

```powershell
# 安装 Python 依赖
pip install requests pymupdf pillow

# 或使用 requirements.txt
pip install -r E:\git\ragflow-import\src\requirements.txt
```

### 1.2 配置环境变量

```powershell
# 设置 API 地址和密钥（PowerShell）
$env:RAGFLOW_API_BASE = "https://labragf.openagp.top:9080/api/v1"
$env:RAGFLOW_API_KEY = "<RAGFLOW_API_KEY>"
$env:DRAWING_KB_ID = "fe5a7ba4a87c11f1998b3dc126099a8d"

# 或永久设置（系统环境变量）
[System.Environment]::SetEnvironmentVariable("RAGFLOW_API_BASE", "https://labragf.openagp.top:9080/api/v1", "User")
[System.Environment]::SetEnvironmentVariable("RAGFLOW_API_KEY", "<RAGFLOW_API_KEY>", "User")
```

### 1.3 验证配置

```powershell
python -c "import requests; h={'Authorization':'Bearer <RAGFLOW_API_KEY>'}; r=requests.get('https://labragf.openagp.top:9080/api/v1/users/me/models', headers=h); print('Tenant:', r.json()['data']['tenant_id'][:16])"
```

---

## 二、脚本使用指南

### 2.1 rescue_drawings.py — PNG 补救脚本

**功能**：将纯矢量图形 PDF 渲染为 PNG，上传后用 picture 方法解析

**位置**：`E:\git\ragflow\tools\rescue_drawings.py`

**基本用法**：

```powershell
# 补救加闸竣工图7/8（默认目标）
python tools/rescue_drawings.py

# 指定目标文档
python tools/rescue_drawings.py --target "加闸竣工图7.pdf" "加闸竣工图8.pdf"

# 横版图纸逆时针旋转 90°
python tools/rescue_drawings.py --target "溢洪道闸房.pdf" --rotate 90
```

**输出示例**：

```
============================================================
处理: 加闸竣工图7.pdf -> 加闸竣工图7.png
============================================================
  PDF 页数: 1
  渲染尺寸: (2823, 7004)
  PNG: 20699941 bytes (20215 KB)
  上传成功: 4419c49cac1a11f1
  解析已触发
  [17s] run=RUNNING progress=0.4
  [35s] run=DONE progress=1.0

  === 结果: chunks=1 ===

  --- chunk 0 (长度 5437) ---
  This is a detailed architectural or structural engineering drawing...
```

**脚本核心逻辑**：

```python
# 1. 下载 PDF
r = requests.get(f"{BASE}/datasets/{KB}/documents/{doc_id}", headers=h)
pdf = pymupdf.open(stream=r.content, filetype="pdf")

# 2. 渲染为 PNG
pix = pdf[0].get_pixmap(matrix=pymupdf.Matrix(2, 2))  # zoom=2
img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")

# 3. 旋转（横版图纸）
if rotate_deg:
    img = img.rotate(rotate_deg, expand=True)

# 4. 上传 PNG
r = requests.post(f"{BASE}/datasets/{KB}/documents", files={"file": (png_name, png_bytes, "image/png")})

# 5. 触发解析并轮询
requests.post(f"{BASE}/datasets/{KB}/documents/parse", json={"document_ids": [new_id]})
```

### 2.2 set_image_context.py — 设置 image_context_size

**功能**：通过文档级别 API 的 ext 提升逻辑设置 image_context_size

**位置**：`E:\git\ragflow\tools\set_image_context.py`

**基本用法**：

```powershell
# 设置 image_context_size=256（不重新解析）
python tools/set_image_context.py --target "溢洪道平面布置图.pdf" --size 256

# 设置并重新解析
python tools/set_image_context.py --target "溢洪道平面布置图.pdf" --size 256 --reparse
```

**输出示例**：

```
=== 溢洪道平面布置图.pdf ===
  chunk_method: paper
  当前 image_context_size: 0
  设置成功: image_context_size=256
  验证: image_context_size=256

  触发重新解析...
  [16s] run=RUNNING progress=0.0234836
  [35s] run=DONE progress=1.0

  重新解析后: chunks=2
  --- chunk 0 (长度 1842, VLM=True) ---
  Visual Type: Technical drawing / Engineering plan layout...
```

**核心 API 调用**：

```python
# 通过 ext 字段设置（会被提升到顶层）
payload = {
    "parser_config": {
        "ext": {
            "image_context_size": 256,
            "table_context_size": 256,
        }
    }
}
r = requests.put(f"{BASE}/datasets/{KB}/documents/{doc_id}", json=payload)
```

### 2.3 verify_drawings_retrieval.py — 检索验证

**功能**：验证图纸在相关查询中的排名

**位置**：`E:\git\ragflow\tools\verify_drawings_retrieval.py`

**基本用法**：

```powershell
python tools/verify_drawings_retrieval.py
```

**输出示例**：

```
=== 查询: '溢洪道平面布置图' (top30) ===
目标图纸命中: 6 条 | 最好排名: 第1位 (溢洪道平面布置图.pdf, sim=0.493)
VLM描述chunks: 21/30
Top5文档分布: {'数字孪生工程施工图册（修改意见）.pdf': 4, '溢洪道平面布置图.pdf': 1, ...}

=== 查询: '层平面图 1:100 施工图' (top30) ===
目标图纸命中: 5 条 | 最好排名: 第1位 (加闸竣工图7.png, sim=0.522)
...

汇总: 8/8 查询第1位命中目标图纸
```

### 2.4 analyze_stream_reference.py — 流式引用验证

**功能**：验证前端流式调用中 reference 的返回情况

**位置**：`E:\git\ragflow\tools\analyze_stream_reference.py`

**基本用法**：

```powershell
python tools/analyze_stream_reference.py
```

**输出示例**：

```
使用 session: f7688796ac3711f1
Status: 200
[chunk 1-68] reference=空
[chunk 69 final] reference.chunks=8, doc_aggs=3

总 chunk 数: 69
有 reference 的 chunk: [69]
最终 reference: chunks=8, doc_aggs=3
```

---

## 三、API 调用示例

### 3.1 获取文档列表

```python
import requests

BASE = "https://labragf.openagp.top:9080"
API_KEY = "<RAGFLOW_API_KEY>"
KB_ID = "fe5a7ba4a87c11f1998b3dc126099a8d"
h = {"Authorization": f"Bearer {API_KEY}"}

# 获取文档列表（page_size 上限 100）
r = requests.get(f"{BASE}/api/v1/datasets/{KB_ID}/documents", 
                 headers=h, params={"page": 1, "page_size": 100})
docs = {d["name"]: d for d in r.json()["data"]["docs"]}

# 查看文档状态
for name, doc in docs.items():
    print(f"{name}: run={doc.get('run')} chunks={doc.get('chunk_count')}")
```

### 3.2 修改解析方法

```python
# 修改文档的 chunk_method
doc_id = docs["加闸竣工图7.pdf"]["id"]
payload = {"chunk_method": "one"}
r = requests.put(f"{BASE}/api/v1/datasets/{KB_ID}/documents/{doc_id}", 
                 headers=h, json=payload)
print(f"修改结果: code={r.json().get('code')}")
```

### 3.3 触发重新解析

```python
# 触发重新解析
payload = {"document_ids": [doc_id]}
r = requests.post(f"{BASE}/api/v1/datasets/{KB_ID}/documents/parse", 
                  headers=h, json=payload)
print(f"解析触发: code={r.json().get('code')}")

# 轮询等待完成
import time
t0 = time.time()
while time.time() - t0 < 600:
    time.sleep(15)
    r = requests.get(f"{BASE}/api/v1/datasets/{KB_ID}/documents", 
                     headers=h, params={"page": 1, "page_size": 100})
    docs = {d["name"]: d for d in r.json()["data"]["docs"]}
    doc = docs["加闸竣工图7.pdf"]
    print(f"  [{int(time.time()-t0)}s] run={doc.get('run')} progress={doc.get('progress')}")
    if doc.get('run') == 'DONE':
        break
```

### 3.4 查看 Chunk 内容

```python
# 获取文档的 chunks
doc_id = docs["加闸竣工图7.png"]["id"]
r = requests.get(f"{BASE}/api/v1/datasets/{KB_ID}/documents/{doc_id}/chunks", 
                 headers=h, params={"page": 1, "page_size": 5})
chunks = r.json()["data"].get("chunks", [])

for i, c in enumerate(chunks):
    content = c["content"]
    has_vlm = "Visual Type" in content or "visual_type" in content
    print(f"Chunk {i}: 长度={len(content)} VLM={has_vlm}")
    print(content[:200])
```

### 3.5 Chat 问答测试

```python
CHAT_ID = "67d6fe12abe911f18c5257600a4ed920"

# 非流式调用（推荐测试）
payload = {
    "question": "你知道溢洪道的平面布置图吗？",
    "stream": False,
}
r = requests.post(f"{BASE}/api/v1/chats/{CHAT_ID}/completions", 
                  headers=h, json=payload, timeout=300)
result = r.json()["data"]
answer = result.get("answer", "")
refs = result.get("reference", {})

print(f"答案长度: {len(answer)}")
print(f"引用 chunks: {len(refs.get('chunks', []))}")
print(f"引用文档: {len(refs.get('doc_aggs', []))}")

# 检查是否提到目标内容
if "溢洪道平面布置图" in answer:
    print("✅ 提到目标文档")
```

---

## 四、批量操作示例

### 4.1 批量重解析（paper 方法）

```python
# 批量重解析 paper 方法文档
paper_docs = [
    "溢洪道平面布置图.pdf",
    # 其他 paper 方法文档...
]

for name in paper_docs:
    doc = docs.get(name)
    if doc:
        print(f"重解析: {name}")
        requests.post(f"{BASE}/api/v1/datasets/{KB_ID}/documents/parse", 
                      headers=h, json={"document_ids": [doc["id"]]})
        time.sleep(3)  # 避免并发
```

### 4.2 批量设置 image_context_size

```python
# 批量设置 image_context_size=256
target_docs = ["溢洪道平面布置图.pdf"]  # paper 方法文档

for name in target_docs:
    doc = docs.get(name)
    if doc:
        payload = {"parser_config": {"ext": {"image_context_size": 256}}}
        r = requests.put(f"{BASE}/api/v1/datasets/{KB_ID}/documents/{doc['id']}", 
                         headers=h, json=payload)
        print(f"设置 {name}: code={r.json().get('code')}")
        time.sleep(1)
```

### 4.3 批量 PNG 补救

```python
# 批量补救纯矢量图形 PDF
vector_pdfs = ["加闸竣工图7.pdf", "加闸竣工图8.pdf"]

for pdf_name in vector_pdfs:
    # 渲染为 PNG
    pdf_path = f"E:\downloads\{pdf_name}"
    png_path = pdf_path.replace(".pdf", ".png")
    
    # 使用 PyMuPDF 渲染
    pdf = pymupdf.open(pdf_path)
    pix = pdf[0].get_pixmap(matrix=pymupdf.Matrix(2, 2))
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    img.save(png_path, "PNG", optimize=True)
    
    # 上传 PNG
    with open(png_path, "rb") as f:
        png_bytes = f.read()
    
    r = requests.post(f"{BASE}/api/v1/datasets/{KB_ID}/documents", 
                      headers=h, files={"file": (pdf_name.replace(".pdf", ".png"), png_bytes, "image/png")})
    print(f"上传 {pdf_name}: {r.json().get('code')}")
```

---

## 五、常见代码操作步骤

### 5.1 完整优化流程（代码版）

```python
import requests
import time
import pymupdf
from PIL import Image
import io

# 配置
BASE = "https://labragf.openagp.top:9080"
API_KEY = "<RAGFLOW_API_KEY>"
KB_ID = "fe5a7ba4a87c11f1998b3dc126099a8d"
h = {"Authorization": f"Bearer {API_KEY}"}

# 步骤 1: 获取文档列表
r = requests.get(f"{BASE}/api/v1/datasets/{KB_ID}/documents", 
                 headers=h, params={"page": 1, "page_size": 100})
docs = {d["name"]: d for d in r.json()["data"]["docs"]}

# 步骤 2: 修改解析方法
doc = docs["加闸竣工图7.pdf"]
r = requests.put(f"{BASE}/api/v1/datasets/{KB_ID}/documents/{doc['id']}", 
                 headers=h, json={"chunk_method": "one"})
print(f"修改解析方法: {r.json().get('code')}")

# 步骤 3: 触发重新解析
r = requests.post(f"{BASE}/api/v1/datasets/{KB_ID}/documents/parse", 
                  headers=h, json={"document_ids": [doc["id"]]})
print(f"触发解析: {r.json().get('code')}")

# 步骤 4: 轮询等待
t0 = time.time()
while time.time() - t0 < 600:
    time.sleep(15)
    r = requests.get(f"{BASE}/api/v1/datasets/{KB_ID}/documents", 
                     headers=h, params={"page": 1, "page_size": 100})
    docs = {d["name"]: d for d in r.json()["data"]["docs"]}
    doc = docs["加闸竣工图7.pdf"]
    print(f"  [{int(time.time()-t0)}s] run={doc.get('run')} progress={doc.get('progress')}")
    if doc.get('run') == 'DONE':
        break

# 步骤 5: 查看结果
r = requests.get(f"{BASE}/api/v1/datasets/{KB_ID}/documents/{doc['id']}/chunks", 
                 headers=h, params={"page": 1, "page_size": 5})
chunks = r.json()["data"].get("chunks", [])
print(f"\n结果: {len(chunks)} chunks")
for c in chunks[:1]:
    print(c["content"][:300])
```

### 5.2 PNG 补救完整流程

```python
# 渲染 PDF 为 PNG
pdf_path = r"E:\downloads\加闸竣工图7.pdf"
pdf = pymupdf.open(pdf_path)
pix = pdf[0].get_pixmap(matrix=pymupdf.Matrix(2, 2))
img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")

# 保存 PNG
png_path = pdf_path.replace(".pdf", ".png")
img.save(png_path, "PNG", optimize=True)

# 上传 PNG
with open(png_path, "rb") as f:
    png_bytes = f.read()

r = requests.post(f"{BASE}/api/v1/datasets/{KB_ID}/documents", 
                  headers=h, files={"file": ("加闸竣工图7.png", png_bytes, "image/png")})
new_id = r.json()["data"][0]["id"]
print(f"上传成功: {new_id}")

# 触发解析
r = requests.post(f"{BASE}/api/v1/datasets/{KB_ID}/documents/parse", 
                  headers=h, json={"document_ids": [new_id]})
print(f"解析触发: {r.json().get('code')}")
```

---

## 六、脚本与文档对照

| 操作 | 前端手工操作 | 代码操作 |
|---|---|---|
| 上传 PDF | 点击"上传文件"按钮 | `POST /api/v1/datasets/{kb}/documents` |
| 修改解析方法 | 文档详情 → 编辑 → 解析方法 | `PUT /api/v1/datasets/{kb}/documents/{id}` |
| 设置 image_context_size | 拖动滑块 | `set_image_context.py` |
| 重新解析 | 点击"重新解析"按钮 | `POST /api/v1/datasets/{kb}/documents/parse` |
| 查看结果 | 文档详情 → Chunk 列表 | `GET /api/v1/datasets/{kb}/documents/{id}/chunks` |
| 检索测试 | 检索测试标签页 | `verify_drawings_retrieval.py` |
| Chat 测试 | 聊天页面输入问题 | `POST /api/v1/chats/{id}/completions` |
| PNG 补救 | 手工渲染 → 上传 | `rescue_drawings.py` |

---

## 七、常见问题排查（代码版）

### 7.1 API 返回 code=100

**现象**：`r.json()["code"] == 100`，`data` 为 None

**原因**：参数错误（如 page_size > 100）

**解决**：
```python
# 检查 page_size
params = {"page": 1, "page_size": 100}  # 上限 100
```

### 7.2 API 返回 code=101

**现象**：`r.json()["code"] == 101`，"Extra inputs are not permitted"

**原因**：parser_config 中有未知字段

**解决**：
```python
# 使用 ext 字段
payload = {"parser_config": {"ext": {"image_context_size": 256}}}
```

### 7.3 解析卡住（RUNNING 不结束）

**现象**：轮询超时，run 仍为 RUNNING

**原因**：大图 VLM 处理耗时较长

**解决**：
```python
# 增加超时时间
while time.time() - t0 < 1200:  # 20 分钟
    time.sleep(20)
    # ...
```

### 7.4 SSL 连接断开

**现象**：`SSLError: UNEXPECTED_EOF_WHILE_READING`

**原因**：长轮询期间网络断开

**解决**：
```python
# 捕获异常后重新连接
try:
    r = requests.get(...)
except requests.exceptions.SSLError:
    time.sleep(10)
    continue  # 重试
```

---

## 八、相关文件清单

| 文件 | 用途 |
|---|---|
| `tools/rescue_drawings.py` | PNG 补救脚本 |
| `tools/set_image_context.py` | 设置 image_context_size |
| `tools/verify_drawings_retrieval.py` | 检索验证脚本 |
| `tools/analyze_stream_reference.py` | 流式引用验证 |
| `tools/test_retrieval.py` | 完整检索+问答测试 |
| `docs/manual-guide-drawing-pdf-optimization-2026-09-09.md` | 前端手工操作指南 |
| `docs/evaluation-drawing-optimization-2026-09-09.md` | 完整技术分析报告 |
| `docs/fix-chat-stream-reference-bug-2026-09-09.md` | 前端 bug 修复方案 |
| **本文档** | **代码操作指南** |