# PaddleOCR 图片解析能力改造设计方案（2026-09-12）

> 背景：报告《Ragflow与YUXI文档解析测试对比报告》中，DeepDoc 对图片可做 VLM 结构化解析，而 PaddleOCR
> 链路图片块恒为 0。本方案给出让 **PaddleOCR 出图** 的架构与实施步骤。
> 说明：方案允许修改 RAGFlow 源码；本文仅设计，未落地。

## 1. 现状：为什么 PaddleOCR 出不了图

RAGFlow 的 PDF 图片机制是一套通用流水线（DeepDoc/MinerU 都在用）：

```
parser 产出的 sections 带位置标签 @@page\tx0\tx1\ty0\ty1##
        │
        ▼  rag/nlp.tokenize_chunks(chunks, doc, pdf_parser=...)
   pdf_parser.crop(ck, need_position=True) → 按标签从 page_images 裁图
        │
        ▼
   chunk 带 image 字段（引用缩略图）；图块经 VLM 描述成独立 image chunk
```

PaddleOCR 三处断点：
1. `deepdoc/parser/paddleocr_parser.py::_transfer_to_sections` —— 对每个 block 调 `_remove_images_from_markdown`，
   图片块内容被删空后 `if not block_content: continue` 直接丢弃。
2. `_transfer_to_tables` —— 直接 `return []`（日志恒 `tables: 0`），未产出 DeepDoc 的 figure item `((img,[caption]),positions)`。
3. `rag/app/naive.py::by_paddleocr` —— 未接 VLM（对比 `by_deepdoc` 调 `vision_figure_parser_pdf_wrapper`、
   `by_mineru` 注入 `vision_model`）。
   注：`__images__ / extract_positions / crop` 三方法解析器**已实现**，可直接复用。

## 2. 目标架构

```
PaddleOCR-VL API 响应 (layoutParsingResults[].parsing_res_list[])
        │  按 block_label 分流：text/title→文本；table→表；image/figure/chart→图
        ▼
┌──────────────── deepdoc/parser/paddleocr_parser.py ────────────────┐
│  _transfer_to_sections: 文本块 → (content, "@@page\tbbox##")（不再删图）│
│  _transfer_to_tables:                                                │
│     表块 → ((table_img, html), positions)                           │
│     图块 → ((figure_img, [caption]), positions)   ← figure item     │
│  取图来源：a) crop()/page_images 按 bbox 裁；b) API 返回的图片资源     │
└───────────────────────────────┬────────────────────────────────────┘
                                ▼  rag/app/naive.py::by_paddleocr
       vision_figure_parser_pdf_wrapper(tbls, sections=…, tenant_id, parser_config, lang)
                                │  → VisionFigureParser 调租户 VISION 模型
                                ▼
                tokenize_table(tables) + tokenize_chunks(chunks, pdf_parser)
                                ▼
        image chunk（image 字段 + 中文 VLM 描述）+ 文本 chunk（crop 缩略图用于引用）
```

核心思想：**把图片当作与表格同级的结构化产物**，走 DeepDoc 已验证的 figure item + VLM 通道，
而不是往 markdown 里塞图片（markdown 链路不支持图片）。

## 3. 改动点清单

| # | 文件/函数 | 改动 |
|---|---|---|
| 1 | `paddleocr_parser.py::_transfer_to_sections` | 图/figure/chart 块不再删空丢弃；保留图注 + 位置标签（供通用 crop） |
| 2 | `paddleocr_parser.py::_transfer_to_tables` | 从 `return []` 改为产出表块 + 图块 figure item |
| 3 | `paddleocr_parser.py::__images__` | 提高渲染 DPI（当前偏低，DeepDoc 用 216dpi） |
| 4 | `paddleocr_parser.py` | 新增 `block_label` → 图/表/文本映射与图注关联 |
| 5 | `rag/app/naive.py::by_paddleocr` | 仿 `by_deepdoc` 调 `vision_figure_parser_pdf_wrapper(...)` 注入 VLM 描述 |
| 6 | `common/constants.py` / `ParserConfig` / web 表单 | （可选）新增 `paddleocr_extract_images` 开关与 `image_context_size` 暴露 |

## 4. 取值来源的两种选择

- **方案 a（推荐）**：复用 `page_images` + bbox 裁剪。稳定、不依赖 API 响应格式；代价是渲染整页（内存/耗时）。
- **方案 b**：用 PaddleOCR-VL 响应自带的图片资源。省渲染，但依赖 SDK 字段，跨版本需适配。

## 5. 风险与注意

1. **坐标一致性**：`@@` 标签由 `block_bbox // _ZOOMIN` 得到，crop 用的 page_images 需同尺度。
2. **表内图误判**：需按 `block_label` + 是否在 table bbox 内过滤。
3. **内存**：整本大 PDF 全页渲染吃内存，建议按分页块处理（RAGFlow 已 12 页/任务）。
4. **回归**：区分「顶层图块」与「块内内联图」，避免 `_remove_images_from_markdown` 行为回归。
5. **依赖 VISION 模型**：未配则退化为有图无描述。

## 6. 验证方案

1. 用 447/352 重解析，检查 `/chunks` 出现 image 类型块（content 为描述、`image` 非空）。
2. 设计示意图问题验证召回与引用显示。
3. 与 DeepDoc 图片 JSON 解析做同文档对比。
4. 性能回归：耗时、内存、chunk 数。

## 7. 工作量

约 **3~5 人日**（parser 取图 + naive 接 VLM + 配置/表单），联调另计。配套参数透传补丁见
`code/mineru/source/patches/0001..0003`。
