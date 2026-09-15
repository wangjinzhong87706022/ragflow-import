# 工程图纸 PDF 解析优化 — 手工操作手册（Web UI，已按部署版实测修订）

- 日期：2026-09-09（修订 v2：按源码调研 + Playwright 部署版实测修正）
- 适用：工程图纸类文档（竣工图/平面布置图/结构图），覆盖**图片文件、纯矢量图形 PDF、横版旋转**图纸
- 部署版本实测对象：`https://labragf.openagp.top:9080`（Dataset → Configuration 页为 **Parse type: Built-in / Pipeline** 新版结构）
- 配套报告：`evaluation-drawing-optimization-2026-09-09.md`

## 0. 结论速览（源码 + UI 双重核实）

**Q1：有 `picture` 解析方案吗？**
**有，但不是下拉里的一个选项。**

- 源码：`rag/app/picture.py` 存在，流程 = OCR（可配 PaddleOCR，否则 DeepDOC OCR）→ OCR 文字 <32 字时调 VLM `describe()` 整页描述；支持 `image_context_size`。
- **入口限制**（两处代码事实）：
  - `picture.py:78` 用 `Image.open(io.BytesIO(binary))` 直接打开二进制 → **只能吃真实图片文件（PNG/JPG），不能直接解析 PDF**；
  - 部署版 UI 的数据集 Built-in 方法下拉**没有 Picture**（实测选项见 §2）。PNG/JPG 上传后由系统**按文件类型自动路由**为 Picture（Files 列表 Parse 列显示 "Picture"）。

**Q2："图片不适合用 paper 解析"？**
**比"不适合"更严重——是根本不支持。**

- 源码 `paper.py:137` docstring："Only pdf is supported."；`paper.py:193` 非 PDF 直接 `raise NotImplementedError("file type not supported yet(pdf supported)")`。
- 即：给 `.png/.jpg` 配 paper → **解析必然失败**；paper 只用于 **PDF**（图纸 PDF 内容提取物 + `vision_figure_parser_pdf_wrapper` 对图元做 VLM 描述，`image_context_size` 对其生效，`picture.py`/`paper.py` 均读取该参数）。
- 评测实证（`evaluation-drawing-optimization-2026-09-09.md`）：`image_context_size` 只在方法内部调用 `vision_figure_parser_pdf_wrapper` 时生效 → **paper/naive/manual 生效，one 不生效**。

### 方法可用性对照（修订后的权威表）

| 内容形态 | 可用方法 | 说明 |
|---|---|---|
| 图纸 **PDF**（有标题栏/表格） | **Paper**（+ `image_context_size`） | PDF 专用；图元 VLM + 上下文增强 |
| 图纸 **PDF**（整卷，非结构化） | **One** | 不调用图元 VLM wrapper；`image_context_size` 对其无效 |
| **图片文件**（PNG/JPG，含转出的图纸 PNG） | **Picture（自动）** | 无需/无法手动在数据集下拉中选择；上传后系统自动指定 |
| 纯矢量图形 **PDF**（OCR 无文字） | **无直接方法** | 必须先渲染为 PNG 再上传（自动走 Picture），见 §6 |
| 正文为主的语言类文档 | General | 横排扫描表格场景另见 `pdf-vertical-table-operations-guide-2026-09-09.md` |

## 1. 部署版配置界面（实测结构）

入口：登录 → 顶层导航 **Dataset** → 数据集卡片 → 左侧 **Configuration**。
（路由 `/knowledge` 已 404，新版为 `/datasets` 与 `/dataset/configuration/{id}`）

Configuration 页实际三段结构：

```
Basic 区
  Name / Language / Permissions / Embedding model(bge-m3)
  Page rank / Tag sets(桃曲坡标签库) / Top-N tags

Switch or configure ingestion pipeline.
  Parse type:  ( ) Built-in   ( ) Pipeline        ← "解析方式"的两个大类

  Built-in 分支（选中 Built-in 时显示）
    Built-in:    [方法下拉]         ← 10 项：General Q&A Manual Table
                                    Paper Book Laws Presentation One Tag（无 Picture/Audio）
    PDF parser:  [DeepDOC ▼]
    Indexing model: [VLM/索引模型]   ← 当前 Qwen3.8-27B（image_context 相关 VLM 能力依赖此处）
    Auto metadata / Auto-keyword / Auto-question

  Pipeline 分支（选中 Pipeline 时显示）
    "Build it from scratch / Please select a pipeline."
    需从模板选择或从零构建可视化摄取流水线（另有 Built-in pipeline introduction 说明按钮）

Data source 区
  Link data source
  [Cancel] [Save]   ← 改完记得 Save；误改时点 Cancel
```

> **"pipeline" 怎么设置**：Parse type 二选一。
> - **Built-in** = 传统"解析方法"路线（本手册推荐，全部经实测验证明）；
> - **Pipeline** = 新版可视化摄取流水线（选模板或 Build from scratch），本部署中属可选项，未作为图纸导入验证路径。
> 表格/图纸两类问题在 Built-in + DeepDOC + 视觉增强下均已闭环，无需切 Pipeline。

## 2. 上传与首次配置

1. 定义图纸类型（§0 对照表）→ 确定方法
2. 上传前先设定数据集 Configuration（Built-in 方法 / PDF parser=DeepDOC / Indexing model），**再上传**；否则上传即以默认解析，浪费一轮 VLM 等待
3. **图片文件（PNG/JPG）不要做任何方法选择**——上传后自动按 Picture 解析
4. 单次上传 ≤5 个文件；解析进度看 Files 列表 Parse 列与状态开关

## 3. 各类型配置卡

**① 结构化图纸 PDF（Paper）**

```
Parse type: Built-in
Built-in:   Paper
PDF parser: DeepDOC
Indexing model: 选可用的 VLM（实测 Qwen3.8-27B）
保存
```

`image_context_size`：让图元 VLM 描述带上周边 OCR 文本，实测 0→256 时描述从
"Engineering Plan" 变为"溢流堰、导流墩、闸孔编号…"级别。
部署版 Configuration 页未暴露滑块——以 API 设置为准：

```bash
curl -X PATCH "$BASE/api/v1/datasets/$DS/documents/$DOC" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"parser_config":{"image_context_size":256}}'
```

（注意：若 PATCH 被 schema 拒绝，同表请参考竖排表格手册的 `ext` 兜底写法。）
设置后需重新解析该文档。

**② 普通竣工图 PDF（One）**：整卷合一、语义完整；不需要也不支持 `image_context_size`。

**③ 图片文件（PNG/JPG）**：自动 Picture，无需配置。

**④ 纯矢量图形 PDF**：先走 §6 补救转 PNG。

## 4. 审查解析结果（文档详情页）

Files 列表 → 点文件名 → 详情页（左侧原图预览，右侧 Chunk result）：

| 检查项 | 合格 | 不合格 → 处置 |
|---|---|---|
| Chunk 内容 | VLM 描述含工程名称/设计院/构件/尺寸/高程（实测加闸竣工图8.png 产出 5400+ 字符含"陕西省水利电力勘测设计研究院"等） | 只有几十字碎片 → OCR 失效，走 §6 |
| Parse 列 | Picture / General / Paper 与预期一致 | 方法不对 → 文档级改方法重新解析 |
| 乱码 | 无"口口口" | 渲染方向错误（§6.4） |

## 5. 问答/检索验收

**检索测试**（数据集 → Retrieval testing）推荐用例：

| 查询词 | 预期第 1 位 |
|---|---|
| 溢洪道平面布置图 | 溢洪道平面布置图.pdf |
| 溢洪道闸房结构 | 加闸竣工图5.pdf |
| 放水塔竣工图 | 低洞放水塔竣工图.pdf |
| 节制闸房设计 | 退水、节制闸房3.pdf |
| 层平面图 1:100 施工图 | 加闸竣工图7.png |
| 陕西省水利电力勘测设计研究院 桥墩 | 加闸竣工图8.png |

**Chat 问答**（顶层导航 Chat → 目标助手）：

| 问题 | 预期 |
|---|---|
| 你知道溢洪道的平面布置图吗？ | 提到"总平面布置图"，带 📎 引用文档 / 🖼️ 引用图片 |
| 溢洪道平面布置图的工程特性是什么？ | 列出校核洪水位、设计洪水位等具体数据 |
| 放水塔的竣工图有哪些？ | 提到"高洞/低洞放水塔" |

引用不显示：先查 Chunk 是否有 VLM 描述；若内容丰富仍无引用，属已登记的前端流式引用 bug（`bug-report-chat-stream-reference-2026-09-09.md`），可用非流式 API 验证。

## 6. 补救流程（纯矢量图纸 PDF / 横版）

依赖：`pip install pymupdf pillow`；自动化脚本 `tools/rescue_drawings.py`。

```python
import io, pymupdf
from PIL import Image

PDF_PATH = r"E:\downloads\加闸竣工图7.pdf"
PNG_PATH = r"E:\downloads\加闸竣工图7.png"
ZOOM = 2      # 解析超时/文件过大时降为 1
ROTATE = 0    # 横版图纸：90（首选）或 -90（乱码时换向）

pdf = pymupdf.open(PDF_PATH)
if pdf.page_count == 1:
    pix = pdf[0].get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM))
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
else:  # 多页纵向拼接
    imgs = []
    for p in pdf:
        pix = p.get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM))
        imgs.append(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))
    w = max(i.width for i in imgs)
    img = Image.new("RGB", (w, sum(i.height for i in imgs)), (255, 255, 255))
    y = 0
    for i in imgs:
        img.paste(i, (0, y)); y += i.height

if ROTATE:
    img = img.rotate(ROTATE, expand=True)
img.save(PNG_PATH, "PNG", optimize=True)
```

上传 PNG → **自动按 Picture 解析** → 预期 Chunk=1、5000+ 字符、描述含设计人员/尺寸/高程。
横版乱码（"口口口"）→ 换旋转方向重新渲染重传。

## 7. 常见故障对照（含本轮新增根因）

| 症状 | 根因 | 处置 |
|---|---|---|
| 图片文件配 Paper → 解析直接报错 | 源码仅支持 PDF（`NotImplementedError`） | 图片文件自动走 Picture；PDF 用 Paper |
| PDF 想走 Picture → 找不到该选项 | 图像二进制不该喂给 `Image.open`（仅图片文件） | 纯矢量 PDF 先渲染 PNG 再上传，自动 Picture |
| VLM 只给一句话 | `image_context_size=0` | PATCH 设 256（仅 Paper/naive/manual 生效，One 无效）→ 重解析 |
| Chunk 内容几十字 | 纯矢量 PDF 无文字层 | §6 转 PNG |
| "口口口"乱码 | 渲染方向错 | 换旋转方向（90 ↔ -90） |
| 解析卡 RUNNING | 大图 VLM 慢 | 等 10min；仍卡重解析；PNG 过大降 zoom |
| 回答无引用 | Chunk 无 VLM 或流式引用 bug | 补 VLM/上下文；非流式 API 验证 |
| 检索目标不在前列 | 未完成/语义弱/阈值高 | 查 DONE 与 Chunk；降 similarity_threshold(0.2)；换具体查询词 |
| 数据集级配置对旧文档无效 | 配置只作用新上传 | 旧文档文档级单独改 + 重解析 |

## 8. 操作检查清单

- [ ] 上传前：文件完整、<20MB、类型判定（对照 §0 表）
- [ ] 图纸 PDF：Built-in=Paper/One；Paper 已 PATCH `image_context_size=256`
- [ ] 图片文件：确认 Parse 列自动显示 Picture（不要手动选方法）
- [ ] 解析后：DONE、Chunk>0、抽查 VLM 描述含构件/尺寸/高程
- [ ] 验收：检索目标第 1 位 + Chat 带 📎/🖼️ 引用
- [ ] 收尾：删重复文档、遗留问题记录

## 9. 相关工具与文档

| 文件/工具 | 用途 |
|---|---|
| `tools/rescue_drawings.py` | PNG 补救自动化 |
| `tools/set_image_context.py` | 批量设置 image_context_size |
| `tools/verify_drawings_retrieval.py` | 检索验证 |
| `tools/analyze_stream_reference.py` | 流式引用 bug 验证 |
| `docs/evaluation-drawing-optimization-2026-09-09.md` | 完整优化分析报告 |
| `docs/bug-report-chat-stream-reference-2026-09-09.md` | 前端 bug 分析 |
| `docs/pdf-vertical-table-operations-guide-2026-09-09.md` | 语言类扫描件（横排表格）对照组手册 |

## 10. 一页速查

```
预检定形态: 图纸PDF(有标题栏=Paper | 整卷=One) | 图片PNG/JPG(自动Picture)
           | 纯矢量PDF(无解 → 转PNG 见§6)
  → 先设 Configuration(Built-in + PDF parser=DeepDOC + Indexing model/VLM) 再上传
  → Paper 场景 PATCH image_context_size=256 后重解析
  → 解析完审块: VLM 描述含设计院/构件/尺寸? (几十字或✗ → 转PNG)
  → 检索测试(目标第1位) + Chat(带📎🖼️引用)
  → 乱码换旋转方向重渲染 | 大图超时降 zoom
  → 收尾删重复
```
