# ragflow-import — 桃曲坡水库知识库 RAGFlow 导入工具集

将桃曲坡水库 222 个运营档案文件导入本地 RAGFlow v0.27.0 实例的完整工具链
（5 个知识库 + 1 个标签库，支持 GraphRAG / Raptor / 标签软重排 / 元数据硬过滤）。

## 目录结构

```
/opt/wangjz/ragflow-import/
├── README.md                  # 本文件：结构与入口
├── docs/
│   ├── requirements.md        # 任务要求（目标 / 功能 / 约束 / 验收）——从这里读起
│   ├── specs/                 # 设计文档（v2，含源码评审修订记录）
│   │   └── 2026-08-25-taoqupo-reservoir-kb-design.md
│   ├── plans/                 # 实施计划（10 任务 TDD 分解）
│   │   └── 2026-08-25-taoqupo-reservoir-kb-import.md
│   └── evaluation-2026-08-26.md  # 方案与代码评测报告
├── src/                       # 全部可执行代码与测试
│   ├── README.md              # 操作手册（环境准备 / 命令序列 / 人工门槛 / 故障排查）
│   ├── requirements.txt       # requests, pycryptodome, pytest
│   ├── config.py              # 所有常量：DATASETS、METADATA_SCHEMA、TOP_LEVEL_ASSIGNMENTS…
│   ├── corpus.py              # 语料扫描 / 去重 / 映射表（阶段0）
│   ├── tables.py              # 表格 Markdown 与 Q/A 行生成（阶段1）
│   ├── tag_vocab.py           # 12 行受控词表（阶段1）
│   ├── vision_extract.py      # 离线 VLM 识别 + 交叉校验 + --approve 人工门（阶段1）
│   ├── ragflow_client.py      # RSA 登录 + Dataset/Document/Search API 封装
│   ├── run_setup.py           # 幂等建库：标签库→parse→tag_kb_ids 注入→元数据 schema（阶段2）
│   ├── run_import.py          # 三步导入 upload→metadata→parse，断点续命（阶段3）
│   ├── run_qc.py              # 6 个验收问题 + meta_data_filter 演练（阶段4）
│   └── tests/                 # 单元测试（125 例；全 mock 不触网，语料依赖用例带 skipif 守卫；另含 2 例 live 冒烟需显式 -m live）
└── out/                       # 运行时产物（mapping.csv / setup_state.json / vision/ / qc/）
```

## 快速开始

```bash
cd /opt/wangjz/ragflow-import/src

# 环境（一次性）
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 凭据（不入库）
export RAGFLOW_EMAIL="..."
export RAGFLOW_PASSWORD="..."

# 阶段0：扫描语料 → out/mapping.csv → 人工审查
python -m corpus

# 阶段2：建库（先 dry-run 再 apply）
python run_setup.py --dry-run
python run_setup.py

# 阶段3：pilot 5 文件验证后全量
python run_import.py --dataset ds3 --limit 5 --apply
python run_import.py --apply

# 阶段4：验收
python run_qc.py
```

完整命令序列与人工门槛见 `src/README.md`。

## 关键外部路径（本目录外，只读）

| 路径 | 用途 |
|---|---|
| `/home/scada/SmartTwinRes-skills/pdfs/` | **导入语料源**：整理后的原始文件库（pdf/doc/xls 直导 + VLM 取材图表） |
| `/home/scada/SmartTwinRes-skills/pdf_text_analysis/` | 上一轮文本抽取产物（遗留，不再直接导入） |
| `/opt/git/ragflow/conf/public.pem` | 登录密码 RSA 加密公钥 |
| `http://localhost:9380/api/v1` | 本地 RAGFlow v0.27.0 API |

## 环境变量

| 变量 | 必需 | 用途 |
|---|---|---|
| `RAGFLOW_EMAIL` / `RAGFLOW_PASSWORD` | 是（除单测） | RAGFlow 登录 |
| `LLM_API_KEY` | 仅 vision_extract | VLM 端点 Bearer token |
| `LLM_API_ENDPOINT` | 否 | VLM 端点，默认 `https://llm.openagp.top:9080/v1` |

## 来源与同步

代码移植自 `/home/scada/SmartTwinRes-skills/ragflow_import/`（git 提交至 `c7e45e1`）；
设计/计划文档来自 `/opt/git/ragflow/docs/superpowers/`。移植时改动共四处：
① `config.OUT_DIR` 改为随代码位置自适应（项目根/out）；② `test_run_setup.py`
的绝对路径改为相对定位；③ `run_import.run_import()` 新增 `_out_dir` 注入参数，
dry-run 在 `setup_state.json`/`mapping.csv` 缺失时打印提示而非抛异常（--apply 仍硬校验）；
④ test_run_setup / test_run_qc / test_run_import 全部改为临时目录隔离，测试不再向真实
out/ 写任何产物。评测详见 `docs/evaluation-2026-08-26.md`。

2026-08-26 评审修复轮（P0–P3）：修正 patch_document 元数据双重包裹、导入器忽略
skip_reason、find_native_xlsx 目录重构失配（原生 xlsx 此前恒为 0 行）、corpus/tables
缺失 CLI 入口四个 Critical；补齐 client 超时与分页、失败重传按名复用、标签库词表幂等、
requests 异常友好退出；location/dept 元数据推导落地、quality 分级对齐 spec、表头判定
不再丢行、Q2 过滤语义修正；并清理死代码/陈旧注释、补状态机 round-trip 测试。
用例数由 46 增至 81。

**同日语料源切换（重要偏差，覆盖 requirements §3）**：应用户确认，导入源从
`pdf_text_analysis/` 派生 txt 改为 **`pdfs/` 整理后的原始文件库直导**——RAGFlow 的
引用溯源需在原文页面中锚定展示，txt 无法满足。新映射 `config.DIR_DATASET` 按一级目录
指派（02 安全鉴定+03 施工图纸→ds5 工程资料，空库问题一并解决）；09 现场照片视频与
10 压缩包不入库；05 参数图表全部走 VLM 预处理（VISION_SOURCES 扩到 12 张）。
实测分布：73 行=70 可导入+3 个 `_dup` 跳过（ds1=4 / ds2=1 / ds3=45 / ds4=5 / ds5=15）；
`requirements.md` §3"唯一可导入语料"条款自即日起以本说明为准。

**2026-08-27 深评修复轮（P0/P1，评审报告见 `docs/review-2026-08-26-deep.md`）**：
客户端层按线上 v0.27.0 容器实证契约重写（登录密码 base64(明文)→RSA 对偶、业务失败
HTTP 200 + code≠0 必须报错、列表接口 page/page_size 分页且 data.docs 取数、run 状态
"DONE"/"FAIL" 与 "3"/"4" 双兼容）；graphrag/raptor 改为嵌套进 parser_config 下发并
对 naive 默认开启的库显式 False（废非法键 max_leaf_nodes）；同名复用收紧为"import_state
doc_id 优先 + 同名单一且字节大小精确一致"，杜绝 basename 盲匹配在 11 个同名文件间张冠李戴；
wait_timeout 与 failed 区分、已 DONE 文档重跑不再重发 parse（避免清空旧 chunks）、GraphRAG
库等待窗放宽至 1800s；VLM 交叉校验键统一为中文标签修复永久 MISSING 死信号；2019 场次
改记 2019-9 并增补 2021-09（三处事件源一致性加测试锁定）、TB0207 年份误判加区间守卫、
DEPT_KEYWORDS 最长优先。用例数由 81 增至 125。
