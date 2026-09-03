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
