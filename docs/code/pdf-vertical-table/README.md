# pdf-vertical-table：扫描件竖排（横排旋转）表格处理脚本集

配套文档：
- 操作手册（人工/代码双路径）：`../../pdf-vertical-table-operations-guide-2026-09-09.md`
- 分析与评测报告：`../../pdf-vertical-table-optimization-2026-09-09.md`

## 脚本清单与执行顺序

| 顺序 | 脚本 | 作用 | 状态 |
|---|---|---|---|
| ① | `01_detect_table_pages.py` | 扫描件表格页/横排页检测（渲染 + 暗像素游程，输出缩略图供人工确认） | 可用 |
| ② | `03_upload_configure_parse.py` | 上传原始 PDF + PATCH 配置（`ext.table_vision_enhance=true`）+ 触发解析 + 轮询 | 可用 |
| ③ | `04_chunk_inventory.py` | 分页拉取全部 chunk（page_size≤100）+ 生成表号映射清单 | 可用 |
| ④ | `05_quality_review.py` | 逐表质量评审：OCR 误读量化（含"淤积面"等术语假阳性排除）+ 异常块扫描 + 题注缺失统计 | 可用 |
| ⑤ | `06_qa_retest.py` | QA 检索复测：正例 + 反幻觉题，核验 answer/reference 与引用文档来源 | 可用 |
| —  | `02_rotate_pages_abandoned.py` | PDF 级物理旋转方案 | **已弃用，留档勿用** |

## 使用前替换配置

各脚本头部常量：

```python
RAGFLOW_BASE     = "https://<your-host>"      # RAGFlow 服务地址
RAGFLOW_API_KEY  = "ragflow-XXXX"             # API key（勿提交真实密钥到仓库）
DATASET_ID       = "<dataset_id>"             # 知识库 ID
DOC_ID           = "<document_id>"            # 文档 ID（04/05/06 使用）
PDF_PATH         = r"path\to\source.pdf"      # 源 PDF（01/03 使用）
```

核心解析配置（与已验证成功的解析结果一致）：

```jsonc
{
  "chunk_method": "naive",
  "parser_config": {
    "chunk_token_num": 512,
    "delimiter": "\\n!?;。；！？",
    "layout_recognize": "DeepDOC",
    "html4excel": false,
    "filename_embd_weight": 0.1,
    "ext": { "table_vision_enhance": true }   // 竖排表格的关键开关
  }
}
```

写法约束（实测）：`table_vision_enhance` 必须放在 `parser_config.ext` 内（顶层会被 schema 拒绝），服务端保存后自动回显到顶层。

## 实测参考结果（SL/T 447-2026，128 页）

- 解析约 30 分钟，产出 128 chunks（清理 1 个乱序碎片后 127）
- 35 个 Markdown 表格块，附表覆盖 23/23（多表合并块需看块尾确认覆盖）
- QA 复测 8 题：6 全对、1 部分幻觉、1 实质正确

依赖：`pip install pymupdf numpy requests`
