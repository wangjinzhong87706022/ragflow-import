# 桃曲坡水库知识库 RAGFlow 导入工具

## 环境准备

```bash
# 1. 创建 venv（Python 3.11+）
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 确认 RAGFlow 运行中
curl http://localhost:9380/api/v1/datasets  # 应返回 JSON

# 3. 设置凭据（环境变量，不入库）
export RAGFLOW_EMAIL="736599796@qq.com"
export RAGFLOW_PASSWORD="你的密码"
```

## 命令序列

### 阶段 0 — 扫描 + 映射表（语料源 = pdfs/ 原始文件库）

```bash
# 扫描整理后的原始文件库（pdf/doc/xls 直导），生成 out/mapping.csv
python -m corpus
# 人工审查 mapping.csv（重点：dataset_key 指派 / flood_event / skip_reason）
# 确认后继续
#
# 说明：09-图像与多媒体、10-压缩包待处理 整目录排除；
#       _dup 命名变体自动标记 duplicate 跳过。
```

### 阶段 1 — 图表 VLM 预处理（必须先于导入）

```bash
# tables.py 为遗留通道（仅对 .txt 行生效），当前语料下无产物，无需运行。

# 离线 VLM 识别 12 张参数/管理图表（需 LLM_API_KEY；与文字化值交叉校验）
python vision_extract.py --limit 2   # dry-run，先看两幅图
# 人工审查 out/vision/compare_report.md
# 确认数值正确后批准
python vision_extract.py --approve
```

> **产物入库说明**（2026-08-26 语料源切换后）：导入源即 `pdfs/` 原始文件，
> 原生 xlsx 随 run_import 一并直导，不再存在 `native_xlsx_path` 合成行；
> VLM 图表文本如需入库，需人工经 Web UI 上传至对应库（ds2），自动化通道留待后续版本。

### 阶段 2 — 建库（幂等）

```bash
# dry-run 检查
python run_setup.py --dry-run

# 正式建库（创建 dataset + 标签库 + metadata schema）
python run_setup.py

# 确认 out/setup_state.json 生成
```

### 阶段 3 — 导入（先导，后全量）

```bash
# 导入门槛：标签库已完成 parse（run_setup.py 最后一步）
# 先导：ds3 抽 5 个文件，验证 GraphRAG
python run_import.py --dataset ds3 --limit 5
# 人工抽查 RAGFlow Web UI：检索"2021年10月3日洪水"
# 确认 chunks 有图谱关系后继续

# 全量导入（无 --limit）
python run_import.py --apply --dataset ds3   # ds3 全量（含 GraphRAG，较慢）
python run_import.py --apply --dataset ds1   # ds1（含 GraphRAG + Raptor）
python run_import.py --apply --dataset ds2   # ds2
python run_import.py --apply --dataset ds4   # ds4
python run_import.py --apply --dataset ds5   # ds5（含 Raptor）
```

### 阶段 3.5 — chunk 切片审查（pilot 后、全量前，强烈建议）

```bash
# pilot 导入解析完成后，对被审文档出切片审查报告（只读）
python inspect_chunks.py --dataset ds3 --limit 5
# 多库 / 指定文档：
python inspect_chunks.py --dataset ds1 ds2
python inspect_chunks.py --dataset ds3 --doc-id <document_id>
```

报告写入 `out/qc/chunks_{ds}_{ts}.md`：每文档 chunk 数与长度分布、自动异常标记
（碎片/噪声/重复/空块）、前 K 块预览、六条人工判据清单与"症状→调参旋钮"对照。
**全量导入前调参成本几乎为零；全量后再调需重解析整库。**

### 阶段 4 — 人工验收

```bash
python run_qc.py
# 审查 out/qc/report_*.md
# Q1-Q4 使用 use_kg=true，需图谱
# Q5-Q6 验证 meta_data_filter 硬过滤
```

## 图 / 表题注工具链（2026-09-15 新增）

用于修复"图 / 表与其题注未关联，导致问答时图号、表号查不到"的问题。
完整背景、根因分析与 RAGFlow 侧代码补丁见 `docs/fix-ragflow-caption-pipeline-2026-09-15.md`。

### 脚本

| 脚本 | 用途 |
|---|---|
| `inject_media_keywords.py` | 从 chunk 内容抽取"表X / 图Y"题注，写入 `important_keywords` |
| `reparse_and_inject.py` | 触发重解析 → 等待完成 → 自动补关键词（一条命令，复用上面的抽取规则） |

**为什么有效**：RAGFlow 检索打分时 `important_kwd` 的权重是正文的 5 倍
（`rag/nlp/search.py`：`tks = content_ltks + title_tks * 2 + important_kwd * 5 + question_tks * 6`）。
表格/图片 chunk 的题注常埋在块中部、正文里只剩"见表2"这类引用，
不注入关键词时"表2 / 图10"这类精确查询召回不稳。

支持的题注落位：`<caption>表D.0.1-1项目特性表</caption>`（DeepDOC）、
`…</table>表 2 2.5 次抛物线表…`（MinerU）、块首图注（图片块）。

### 用法

```bash
# 预演：只打印将要写入什么（不调写接口）
python src/inject_media_keywords.py --base-url <URL>/api/v1 --api-key <KEY> \
    --dataset-id <DS> --document-id <DOC>

# 写入（合并模式，幂等）
python src/inject_media_keywords.py ... --apply
# 覆盖模式（清掉历史脏关键词，仅限识别出的媒体块）
python src/inject_media_keywords.py ... --apply --replace
# 整库
python src/inject_media_keywords.py ... --dataset-id <DS> --all-documents --apply --replace

# 重解析 + 补关键词（推荐：解析补丁上线后让存量文档生效）
python src/reparse_and_inject.py --base-url <URL>/api/v1 --api-key <KEY> \
    --dataset-id <DS> --document-id <DOC>
python src/reparse_and_inject.py ... --dataset-id <DS> --all-documents
python src/reparse_and_inject.py ... --dataset-id <DS> --all-documents --skip-parse  # 只补关键词
```

> ⚠️ **重解析会清空 chunk 的 `important_keywords`**，会让"表1/表2 排序打平"等问题立刻回归，
> 因此「重解析 → 补关键词」必须成对执行（`reparse_and_inject.py` 已把两步串起来并自动等待解析完成）。

> ⚠️ **前提**：走 VLM 图片描述需要租户配了 **Vision 模型**
> （`GET /api/v1/models` 中存在 `model_type` 含 `vision` 的模型），
> 否则 MinerU 的图片增强会被静默跳过，图号与图形内容补不出来。

### 相关产出

- 交接工单（可直接交给部署机上的 coding agent）：`docs/fix-ragflow-caption-pipeline-2026-09-15.md`
- P0 补丁（MinerU VLM 图注上下文）：`docs/code/patches/mineru-vlm-figure-caption.patch`

## 人工门槛

| 门槛 | 位置 | 通过标准 |
|---|---|---|
| mapping.csv 审查 | `out/mapping.csv` | flood_event 列正确；skip_reason 无误 |
| VLM 批准 | `out/vision/approved.flag` | 人工确认库容表/泄流曲线数值正确 |
| 10% 解析抽样 | RAGFlow Web UI 或 `python inspect_chunks.py --dataset ds3 --limit 5` | 抽查已解析文档的切片质量与 quality 标注（原 pdf_text_analysis OCR 路径已随语料源切换废弃） |
| 导入门槛 | ds3 5 文件 pilot | 检索"2021年10月3日洪水"有图谱关系返回 |

## 故障排查

- **启动即报 "[配置错误] RAGFLOW_EMAIL..."**: 凭据未设置或仍是占位值——`export RAGFLOW_EMAIL=... && export RAGFLOW_PASSWORD=...` 后重跑（fail-fast 前移到客户端构造）
- **Login 失败 (401)**: 密码错误或 RSA 加密格式不匹配；`python -c "from ragflow_client import encrypt_password; print(encrypt_password('pass','/opt/git/ragflow/conf/public.pem'))"` 验证
- **create_dataset 400**: tenant 未配置 embedding 模型 → RAGFlow Web UI → 模型配置
- **parse 一直 running**: 重启 RAGFlow 容器；检查 `docker compose -f docker/docker-compose-base.yml logs deepdoc`
- **meta_data_filter 无效**: 运行 `python run_qc.py` 时观察错误信息；filter shape 见 spec §4.3
- **导入状态卡住**: 检查 `out/import_state.json` 的 `failed` 条目；删除对应 doc 后重跑
