# MinerU 评测代码与源码

配合 `docs/evaluation-mineru-2026-09-12.md`（主报告）与
`docs/design-paddleocr-image-support-2026-09-12.md`（PaddleOCR 出图设计）。

## 目录

```
docs/code/mineru/
├── README.md                     # 本文件
├── scripts/                      # 评测/运维脚本（仅调用 RAGFlow HTTP API）
│   ├── 00_create_dataset.py              # 建 MinerU 评测知识库
│   ├── 01_upload_and_parse_one.py        # 单文档：下载原文→上传→设参→触发解析（串行用）
│   ├── 02_poll_parse_status.py           # 轮询解析状态
│   ├── 03_compare_chunk_stats.py         # 对比两库 chunk/表/公式/图片统计
│   ├── 04_sample_image_formula_table.py  # 抽样图片/公式/表格内容
│   ├── 05_formula_loss_check.py          # 用本地 PDF 原文核验公式是否丢失（PyMuPDF）
│   ├── 06_gen_vertical_table_fixture.py  # 生成竖排表受控测试件（纯图像扫描件）
│   ├── 07_upload_vertical_fixture.py     # 上传竖排表测试件并触发解析
│   ├── 08_set_language_chinese.py        # 知识库语言改 Chinese 并重解析（图片描述转中文）
│   ├── 09_check_image_description_language.py  # 统计图片描述语言（中文/英文/空）
│   ├── 10_detect_abnormal_image_chunks.py      # 检测空描述/提示词泄漏
│   ├── 11_fix_abnormal_image_chunks.py         # 修复异常块（剥前缀 / picture 知识库重描述）
│   ├── 12_clean_helper_kb.py                   # 清理重描述辅助知识库的测试图片
│   └── 13_merge_continued_tables.py            # 合并跨页续表（表D.0.1 等），仅用现有 API
└── source/
    ├── docker-compose.mineru.yml       # MinerU API 服务自部署 compose（自包含，含构建说明注释）
    └── patches/                        # 需改源码时的参考补丁（未应用）
        ├── 0001-parser-config-utils-paddleocr-algorithm-config.patch
        ├── 0002-naive-by-paddleocr-algorithm-config.patch
        └── 0003-validation-utils-parserconfig-paddleocr-keys.patch
```

## 环境变量（脚本统一要求，无明文密钥）

| 变量 | 说明 |
|---|---|
| `RAGFLOW_API_BASE` | RAGFlow API 地址，默认 `https://labragf.openagp.top:9080` |
| `RAGFLOW_API_KEY` | RAGFlow API Key（Bearer） |

> 脚本中的 KB / document / chunk ID 来自本次评测实例，按需替换。

## 本次评测实例 ID（参考）

| 用途 | ID |
|---|---|
| 新知识库 `mineru-retest` | `76a691aeae5b11f1896583d540e218a6` |
| 原知识库 `paddleocr-retest` | `619a744aad0411f19cb4c765b4445b50` |
| SL-T-447-2026（mineru） | `a4c4e770ae5b11f1896583d540e218a6` |
| SL-T-352-2020（mineru） | `086ccd2eae5c11f1896583d540e218a6` |
| SL-101-2014（mineru） | `3142474aae5e11f1896583d540e218a6` |
| 竖排表测试件（mineru） | `8b0b778cae7311f1896583d540e218a6` |
| 重描述辅助库 `vlm-redesc` | `f4c44446ae8311f1896583d540e218a6` |

## 典型用法

```bash
export RAGFLOW_API_BASE="https://<host>:9080"
export RAGFLOW_API_KEY="ragflow-..."

# 1) 建库 + 串行上传解析（一次一个，等 DONE 再下一个）
python scripts/00_create_dataset.py
python scripts/01_upload_and_parse_one.py 447
python scripts/02_poll_parse_status.py <doc_id> 540   # 直到 DONE/FAIL
python scripts/01_upload_and_parse_one.py 352
python scripts/02_poll_parse_status.py <doc_id> 540
python scripts/01_upload_and_parse_one.py 101
python scripts/02_poll_parse_status.py <doc_id> 540

# 2) 评估
python scripts/03_compare_chunk_stats.py
python scripts/04_sample_image_formula_table.py
python scripts/05_formula_loss_check.py          # 需要本地 352 原文 PDF

# 3) 图片中文描述
python scripts/08_set_language_chinese.py
python scripts/09_check_image_description_language.py

# 4) 异常块修复（dry-run 默认，确认后加 --apply）
python scripts/10_detect_abnormal_image_chunks.py   # 或 11 的 detect 子命令
python scripts/11_fix_abnormal_image_chunks.py fix --kb <kb> --doc <doc> --apply
python scripts/12_clean_helper_kb.py

# 5) 跨页续表合并（dry-run 默认，确认后加 --apply；按"续表 <表号>"分组）
python scripts/13_merge_continued_tables.py detect --kb <kb> --doc <doc>
python scripts/13_merge_continued_tables.py merge  --kb <kb> --doc <doc> --apply
```

## 源码补丁说明

`source/patches/*.patch` 是对 RAGFlow 源码的参考改动（本次未落地，供后续实施）：

- 0001/0002：让 `paddleocr_*` 算法选项（mergeLayoutBlocks/restructurePages/mergeTables/relevelTitles）
  经 `parser_config → algorithm_config` 透传到 `PaddleOCRParser.parse_pdf`。
- 0003：在 API 的 `ParserConfig` schema 增加上述字段（否则 `extra="forbid"` 会拒绝）。

应用方式：在 RAGFlow 源码仓库根目录 `git apply <patch>`（需与目标版本对齐后人工校验）。

## 注意事项

- 所有脚本默认只读或 dry-run；写操作（PATCH/删除/触发解析）需显式参数或明确调用。
- `chunk_count`/`token_count` 文档字段会失真，统计一律以 `/chunks` 接口的 `total` 为准。
- 重描述依赖租户已配置 VISION 模型；辅助库 `vlm-redesc` 为 picture 类型、语言 Chinese。
