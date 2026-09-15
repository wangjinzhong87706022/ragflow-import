# MinerU 评测用例套件（testcases）

> 接手入口：`docs/handover-mineru-eval-2026-09-12.md`（资源 ID、方法、问题与方案、未完成事项）。
> 失败用例明细：`docs/eval-failed-cases-image-vertical-2026-09-13.md`（含正确答案与错误归因）。
> 配套主报告：`docs/evaluation-mineru-2026-09-12.md`。
本目录是「MinerU 解析知识库」的**问答级评测**：对解析产物（图片/公式/竖表/表格/文本）
在 RAGFlow 助手问答链路上的实际效果做端到端验证。

## 目录

```
testcases/
├── mineru_content_inventory.json     # MinerU 解析内容盘点（表/图/公式，按文档）
├── mineru_eval_cases.json/.md        # 135 条评测用例（6 类）
├── run_eval_browser.js               # Playwright 浏览器实测运行器（v2）
├── run_eval_api.py                   # chat completions API 运行器（备用/批量更快）
├── mineru_eval_results_batch1.json   # 首批浏览器实测结果（9 条）
├── mineru_eval_results_shots.json    # 竖表+反幻觉 带截图批次（如已生成）
└── screenshots/                      # 浏览器逐题截图（启用 SHOT_DIR 时）
```

## 用例构成（135 条，2026-09-13 措辞修复后）

| 类别 | 数量 | 重点 | 来源 |
|---|---|---|---|
| 图片 | 30 | 352 的 119 个图片块：图号 + VLM 描述关键要素 | `图3.2.3`、`图5.5.4` 等 |
| 公式 | 40 | 352 的 LaTeX/数值（养护温度、筛孔、公式号、下标） | 293 个含公式块 |
| 竖表 | 28 | 447 的表结构（表头/行/续表），含合并后的 表D.0.1-1/-2/-3、D.0.16、D.0.17 | 25 张表 |
| 表格 | 17 | 101 表2.3.4 抽样比例（有真实数值）、352 表头结构 | — |
| 文本 | 11 | 标准号/日期/章节/适用范围 | — |
| 反幻觉 | 9 | 空白模板数值、不存在的表/公式/章节 | — |

> 2026-09-13 归类修正：`TC-127/128/134` 原误归"反幻觉"，实为可答题 → 改判为 表格/文本；
> 并放宽运行器 `REJECT`（增加"未提供/未填写/未给出/未列出/未明确/空白"）。

**2026-09-13 措辞修复**（详见 handover §5 本轮新增）：
- 30 条图片用例去除"（约第 X 页）"/"第 X 页的图片"（Agentic 关键词过滤会把页码当过滤词丢命中）；
- 8 条无图号用例改为内容锚定（部件编号/特征值）；
- 13 条表 D.0.1-1/D.0.16/D.0.17 问题补全表名（防表号串扰，TC-071 复发）；
- TC-018/019 去重（同问图3.2.3，改为分别问图型/子图(b)）；
- 复测：TC-001 ✅、TC-092 ✅、TC-022 仍 ❌（图号未被索引为关键词，需 `important_keywords` PATCH）。

用例字段：`id / category / doc / question / expected_keywords / source / note`。
判分：命中 ≥50% 关键词且至少 1 个命中（长关键词用字符覆盖率 ≥0.7 兜底）。

## 助手配置（评测用）

- 名称：`MinerU-评测助手`，ID `46f41fbaaf1611f1896583d540e218a6`，绑定知识库 `mineru-retest`；
- 系统提示：只依据知识库回答；给出表号/图号/公式号；空白模板必须说明"需按实际工程填写"；找不到就说"未找到"；
- **rerank 已置空**（原 rerank 端点 `172.28.200.80:9997` 不可达，会阻塞问答）。

## 运行方式

```bash
# 浏览器（Playwright，需 playwright-skill 环境）
cd <playwright-skill> && \
RAGFLOW_URL=https://<host>:9080 RAGFLOW_EMAIL=<email> RAGFLOW_PASSWORD=<pwd> \
RAGFLOW_CHAT_ID=46f41fbaaf1611f1896583d540e218a6 \
CASES_FILE=<本目录>/mineru_eval_cases.json OUT_FILE=<本目录>/results.json \
IDS=1,31,71,124 HEADLESS=1 node run.js <本目录>/run_eval_browser.js

# 或 API 批量（推荐主力：无需浏览器环境；v3 支持断点续跑、每 10 题自动清理会话）
export RAGFLOW_API_BASE=... RAGFLOW_API_KEY=...
python run_eval_api.py --chat 46f41fbaaf1611f1896583d540e218a6 --category 竖表
# 分批示例：--start/--end 分 10 段跑完 135 条；中断重跑同命令会自动跳过已完成用例
```

答案来源：拦截 `POST /api/v1/chat/completions` 的 SSE，拼接片段并剔除 Agentic RAG 进度行。

## 首批浏览器实测结论（9 条：TC-001/022/031/045/071/092/099/114/124）

自动评分 5/9，按"字符覆盖率"宽松重评 **6/9 PASS**；未命中 3 条经检索层复核为**非解析问题**：

| 用例 | 结果 | 诊断 |
|---|---|---|
| TC-031 公式：养护温度 `(20±2)℃` | ✅ | 回答含 LaTeX，公式链路可用 |
| TC-045 公式：空隙率公式 | ✅ | 回答含 `$$ P_{vb} = ... $$` |
| TC-071 竖表：表D.0.1-1 列标题 | ✅ | 回答"名称/单位/数量/备注"——**合并后的续表可被检索** |
| TC-099 表格：101 抽样比例 1~10 | ✅ | 回答 `100~30%` |
| TC-114 文本：447 实施日期 | ✅ | 回答 `2026年4月4日` |
| TC-124 反幻觉：表D.0.1-1 项目区面积数值 | ✅ | 回答"数值为空白，该表为模板，需按实际填写" |
| TC-001/022 图片：图3.2.3 / 第64页图 | ❌ | 检索层能命中（相似度 0.40~0.46）；失败源于**问题含"约第22页"干扰 Agentic 关键词过滤** |
| TC-092 竖表：表D.0.14 列标题 | ❌ | 表头块可直接命中（0.376）；失败源于 Agentic 关键词过滤过严 |

**结论**：公式、竖表（含跨页合并）、反幻觉、数值表格在浏览器端均通过；
图片类失败主要出在**问答检索策略与问题措辞**，而非 MinerU 解析或索引缺失。

## 本地 Qwen3.8-27B 全量结果（2026-09-13）

- 助手已切回本地私有化 `Qwen3.8-27B-Q4_K_M.gguf@llamacpp-qwen3-27b`；全量 135 条 API 实测约 27 分钟。
- **判分修正后：114/135（84%）** —— 图片 18/30、公式 38/40、竖表 22/28、表格 16/17、文本 11/11、反幻觉 9/9。
- 结果文件：`results_qwen_full.json`（含 `pass_corrected` 字段）。

## 表号/图号召回增强实验（2026-09-13）

- 脚本：`../scripts/14_add_recall_keywords.py`；352 更新 137 块、101 更新 1 块（447 已覆盖）。
- 复跑 图片(30)+竖表(28)：`results_qwen_recallfix.json` —— 图片 18→17、竖表 22→23，**无实质提升**。
- 根因：**助手 Agentic RAG 的关键词过滤**丢掉命中块；`/api/v1/retrieval` 直检可在 0.38~0.46 命中。
- 结论：召回增强对 BM25/非 agentic 路径无害，但图片/竖表召回须从**问答检索策略**入手。

## 待办

1. 对图号/表号类问题改用 `/api/v1/retrieval` 直检，或调整 Agentic 检索参数（`top_n` 现 12、`vector_similarity_weight` 现 0.3）；
2. 稳定性抽样：对每类抽 ≥3 条各复跑 2 次，报"稳定通过/抖动通过/稳定失败"（存在 Agentic 抖动，如 TC-023/081/092）；
3. 修正公式歧义用例 TC-043/044；出最终评测报告 `docs/eval-report-mineru-final-*.md`。
