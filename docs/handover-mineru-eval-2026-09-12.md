# MinerU 知识库评测 —— 交接文档（2026-09-12，2026-09-13 评审更新）

> 面向后续接手的 Code Agent：本文是**唯一入口**，包含背景、全部资源 ID、方法、脚本用法、
> 已知问题与解决方案、未完成事项与坑位。配套阅读：
> `docs/evaluation-mineru-2026-09-12.md`（解析评测）、`docs/design-paddleocr-image-support-2026-09-12.md`（PaddleOCR 出图设计）、
> `docs/code/mineru/README.md`（脚本目录）、`docs/code/mineru/testcases/README.md`（用例套件）。
> **2026-09-13 评审**：实例/资源 ID 已核验有效；用例措辞已修复（见 §5 本轮新增）；两个运行器已升级 v3（会话治理/断点续跑/重试/DOM 兜底）。
> 评审详情与修复清单：`docs/review-handover-mineru-eval-2026-09-13.md`。
> **2026-09-13 全量评测（glm-5.3-flash）**：108/135（80%），分维度与 MISS 归因见 `docs/eval-report-mineru-glm-2026-09-13.md`。

---

## 0. 快速启动（TL;DR）

```bash
# 凭据只走环境变量，不入库
export RAGFLOW_API_BASE="https://<ragflow-host>:9080"
export RAGFLOW_API_KEY="ragflow-***"        # 向操作者索取；旧 key 视为已泄露需轮换

# 1) 内容盘点（可选）
python docs/code/mineru/testcases/run_eval_api.py --chat <chat_id> --category 竖表

# 2) 浏览器实测（Playwright）
cd <playwright-skill 目录> && \
RAGFLOW_URL=$RAGFLOW_API_BASE RAGFLOW_EMAIL=<邮箱> RAGFLOW_PASSWORD=<密码> \
RAGFLOW_CHAT_ID=46f41fbaaf1611f1896583d540e218a6 \
CASES_FILE=<repo>/docs/code/mineru/testcases/mineru_eval_cases.json \
OUT_FILE=<repo>/docs/code/mineru/testcases/results_next.json \
IDS=1,31,71 HEADLESS=1 node run.js <repo>/docs/code/mineru/testcases/run_eval_browser.js
```

---

## 1. 背景与已完成工作

目标：验证 **MinerU 解析知识库** 的问答效果，重点 **图片、公式、竖表**，用 Playwright 在浏览器实测。

已完成：
1. **解析评测**：三份 PDF（447/352/101）串行导入 `mineru-retest` 并用 MinerU 解析；图片 119 个、
   公式 5624 个 LaTeX 片段、跨页竖表已合并；详见 `evaluation-mineru-2026-09-12.md`。
2. **用例设计**：135 条（图片 30 / 公式 40 / 竖表 28 / 表格 15 / 文本 10 / 反幻觉 12）。
3. **评测助手**：`MinerU-评测助手` 已创建并绑定知识库。
4. **浏览器实测**：自研 Playwright 运行器；已跑 11 条（9 条无截图 + 2 条含截图），
   自动评分 6/9（另 2 条经检索层复核为"非解析问题"）。
5. **问题与方案**：见第 5 节。

---

## 2. 关键资源 ID（务必沿用）

### 2.1 RAGFlow 实例
- 地址：`https://labragf.openagp.top:9080`（远端，非本地部署；仓库为 `E:\git\ragflow` 与其版本不同）
- 浏览器账号：由操作者提供（历史测试使用实例管理员账号；**账号与密码均不得写入仓库**）

### 2.2 知识库 / 文档
| 用途 | ID |
|---|---|
| 评测知识库 `mineru-retest`（语言 Chinese） | `76a691aeae5b11f1896583d540e218a6` |
| 对照知识库 `paddleocr-retest`（已废弃对照） | `619a744aad0411f19cb4c765b4445b50` |
| 447 原始解析 | `a4c4e770ae5b11f1896583d540e218a6` |
| 447 跨页合并版（评测用） | `26f349ceae9511f1896583d540e218a6` |
| 352（公式/图片重点） | `086ccd2eae5c11f1896583d540e218a6` |
| 101 | `3142474aae5e11f1896583d540e218a6` |
| 竖排表合成件（带文本层，弃用） | `472f50e2ae7311f1896583d540e218a6` |
| 竖排表合成件（纯图像，受控实验） | `8b0b778cae7311f1896583d540e218a6` |
| 重描述辅助库 `vlm-redesc`（picture 类型，已清空） | `f4c44446ae8311f1896583d540e218a6` |

### 2.3 评测助手
- 名称 `MinerU-评测助手`，ID `46f41fbaaf1611f1896583d540e218a6`
- 绑定 `mineru-retest`；语言 Chinese；`quote=true`；`refine_multiturn=true`
- **`rerank_id` 已置空**（默认 rerank 端点 `172.28.200.80:9997` 不可达，会导致问答 404/100）
- 系统提示（要点）：仅依据知识库；给出表号/图号/公式号；空白模板须说明"需按实际工程填写"；找不到就直说
- **LLM**：评测要求使用**本地私有化部署的 Qwen3.8-27B**，即
  `Qwen3.8-27B-Q4_K_M.gguf@llamacpp-qwen3-27b@OpenAI-API-Compatible`（tenant model `31cf3ef8a73211f1998b3dc126099a8d`）。
  注意：助手曾被改为 `deepseek-v4-flash`（sensenova 实例），跑评测前须切回 Qwen：
  `PUT /api/v1/chats/{chat}` body `{"tenant_llm_id":"31cf3ef8a73211f1998b3dc126099a8d"}`，再 GET 确认 `llm_id` 为上述组合名。

### 2.4 内容与用例文件
| 文件 | 说明 |
|---|---|
| `docs/code/mineru/testcases/mineru_content_inventory.json` | 解析内容盘点（表/图/公式） |
| `docs/code/mineru/testcases/mineru_eval_cases.json/.md` | 135 条用例 |
| `docs/code/mineru/testcases/run_eval_browser.js` | Playwright 运行器 v2 |
| `docs/code/mineru/testcases/run_eval_api.py` | API 运行器（备用/批量） |
| `docs/code/mineru/testcases/mineru_eval_results_batch1.json` | 首批 9 条结果 |
| `docs/code/mineru/testcases/mineru_eval_results_shots.json` | 带截图批 2 条结果 |
| `docs/code/mineru/testcases/screenshots/*.png` | 浏览器取证截图 |

---

## 3. 评测方法（完整复现路径）

### 3.1 用例 schema
```json
{"id":"TC-071","category":"竖表","doc":"SL-T-447-2026",
 "question":"《SL/T 447-2026》表D.0.1-1 的列标题有哪些？",
 "expected_keywords":["名称单位数量备注"],"source":"page 70","note":"列/表头"}
```
- 类别：图片/公式/竖表/表格/文本/反幻觉
- 期望要点来源：解析产物的 caption/表头/LaTeX/描述（客观可核）

### 3.2 判分
- 严格：关键词命中 ≥50% 且 ≥1 个命中
- 宽松兜底：长度 ≥6 的关键词用**字符覆盖率 ≥0.7**（表头拼接串无分隔，需此兜底）
- **注意**：自动评分只是初筛，最终以人工复核答案为准（首批 5/9 自动 vs 6/9 复核）

### 3.3 浏览器运行器要点（重要坑位）
- 输入框：`textarea[placeholder*="message" i]`（**不是 contenteditable**）
- 发送：`Enter`；发送后输入框清空即成功
- 答案来源：拦截 `POST /api/v1/chat/completions` 的 **SSE**，拼接 `data.answer`，再**剔除 Agentic RAG 进度行**
  （`[...]`、`Running the ...`、`Kept N of M`、`Compiled expansion`、`Searching the knowledge base` 等）
- 完成判定：收到完整响应体（`response.text()` 在流结束后返回）
- 每新题点击 `button:has-text("New conversation")` 开新会话，避免上下文污染
- 单题耗时：**41~197 秒**（服务端 Agentic RAG，无法关闭）；135 条全量预计 **4~5 小时**，必须分批
- Windows 后台运行：`Start-Process node -ArgumentList "run.js","<脚本>" -WorkingDirectory <skill> -NoNewWindow -RedirectStandardOutput <log>`
  （工具可能报 `ChildProcess.kill`，但进程实际存活，用日志/结果文件确认）

### 3.4 API 运行器
- 建会话：`POST /api/v1/chats/{chat}/sessions {"name": ...}`
- 问答：`POST /api/v1/chats/{chat}/completions {"question","stream":false,"session_id"}`
- 与浏览器同样受 Agentic RAG 延迟影响

### 3.5 环境坑位
- PowerShell 中**不要用 `python -c` 写多引号脚本**（引号会被吞/报错）；一律写成 `.py` 文件执行
- 会话/文档详情接口在超大 chunk 数时会超时；统计一律用 `/datasets/{kb}/documents/{doc}/chunks` 分页
- **文档列表的 `chunk_count`/`token_count` 失真**，评测基准一律用 `/chunks` 接口 `total`

---

## 4. 浏览器实测现状（截至本批次）

| 用例 | 维度 | 结果 | 备注 |
|---|---|---|---|
| TC-031 | 公式 | ✅ | 回答含 `$(20 \pm 2)^{\circ}\mathrm{C}$` |
| TC-045 | 公式 | ✅ | 回答含 `$$P_{\mathrm{vb}}=...$$` |
| TC-071 | 竖表 | ⚠️ 不稳定 | 一次答对（名称/单位/数量/备注），一次答成 D.0.1-3（项目/单位/数量）→ **表号串扰** |
| TC-099 | 表格 | ✅ | `100~30%`（101 表2.3.4） |
| TC-114 | 文本 | ✅ | `2026年4月4日` |
| TC-124 | 反幻觉 | ✅ | "数量为空白，该表为模板，需按实际填写" |
| TC-001/022 | 图片 | ❌ | 检索层可命中（0.40~0.46），失败因问题含"约第22页"→ **Agentic 关键词过滤** |
| TC-092 | 竖表 | ❌ | 表头块可命中（0.376），同上关键词过滤过严 |

**定性结论**：公式/竖表（含跨页合并）/反幻觉/数值表格在端到端可用；图片召回受**问答检索策略与提问措辞**影响，非解析问题。

### 4.1 本地 Qwen3.8-27B 全量评测结果（2026-09-13，135/135 完成）

- 结果文件：`docs/code/mineru/testcases/results_qwen_full.json`
- 运行方式（API，后台，断点续跑）：
  ```bash
  RAGFLOW_API_BASE=... RAGFLOW_API_KEY=... \
  python docs/code/mineru/testcases/run_eval_api.py \
    --chat 46f41fbaaf1611f1896583d540e218a6 \
    --out docs/code/mineru/testcases/results_qwen_full.json --sleep 1
  ```
  实测总耗时约 **27 分钟**（本地 Qwen 明显快于此前 GLM/浏览器路径），可中断续跑。

| 类别 | 通过/总数 | 通过率 |
|---|---|---|
| 图片 | 18/30 | 60% |
| 公式 | 38/40 | 95% |
| 竖表 | 22/28 | 79% |
| 表格 | 16/17 | 94% |
| 文本 | 11/11 | 100% |
| 反幻觉 | 9/9 | 100% |
| **合计（判分修正后）** | **114/135** | **84%** |

> 判分修正：原自动评分 110/135；人工复核后把 `TC-127/128/134` 由"反幻觉"改判为可答题（表格/文本）、
> `TC-125` 补拒答词，并在运行器 `REJECT` 集合补充"未提供/未填写/未给出/未列出/未明确/空白"。
> 修正后的结果为 **114/135（84%）**，写入 `results_qwen_full.json` 的 `pass_corrected` 字段。

主要失败与判读：
- **图片 12 例未通过**：均为"图号精确召回"类（如 `图5.13.2`、`图9.9.2-1`），模型回答"知识库中未找到"但常能列出其它图号。
- **竖表 6 例未通过**：多为"某表列标题/条目"未召回（如 D.0.1-2/-3、D.0.15 部分）。
- **公式 2 例未通过**：`TC-043/044` 问题指向多义（多个表观密度/堆积密度公式），模型给多解——属用例歧义。

### 4.2 表号/图号召回增强实验（2026-09-13）

- 新增脚本 `scripts/14_add_recall_keywords.py`：给 table/image chunk 写入 `important_keywords`
  （表号/图号 + 名称 + 组合串）。
- 执行：352 更新 137 个块、101 更新 1 个、447 此前已被其他 agent 覆盖（仅 2 个无表题块无法提取）。
- **复跑 图片(30)+竖表(28) 对比（本地 Qwen，`results_qwen_recallfix.json`）**：

| 类别 | 基线 | 召回增强后 | 变化 |
|---|---|---|---|
| 图片 | 18/30 | 17/30 | −1（波动） |
| 竖表 | 22/28 | 23/28 | +1（波动） |

- **结论：`important_keywords` 未带来实质提升**。根因经复核：**瓶颈在助手问答链路的 Agentic RAG 检索/关键词过滤**，
  而非索引缺失——用 `/api/v1/retrieval` 直接检索 `图3.2.3`、`表D.0.14` 均可在 0.38~0.46 命中目标块，
  但助手侧日志显示 `Kept 1 of 12 passage(s) that actually mention the keywords`，即**关联过滤丢掉了命中块**。
- 因此该增强脚本保留（对 BM25/非 agentic 路径无害且可能有帮助），但**图片/竖表召回需从问答检索策略入手**：
  ① 若目标版本支持，关闭/放宽 Agentic 检索或提高 `top_n`；
  ② 对图号/表号类问题改用 `/api/v1/retrieval` 直检（已验证有效）；
  ③ 提问用词与原文一致（用例已去掉页码等干扰词）。

结论：**本地 Qwen3.8-27B 在 MinerU 知识库上端到端可用（修正后 114/135，84%）**；公式/文本/表格/反幻觉强，
图片与竖表的瓶颈在**问答链路的 Agentic 检索过滤**（非解析、非索引）。

---

## 5. 问题清单与解决方案（含前序全部问题）

### P0 级
| 问题 | 现象 | 解决方案 | 状态 |
|---|---|---|---|
| 嵌入模型不稳 | 批量解析 FAIL：`bge-m3-rep0 is in stopping state`；残留 chunk 污染统计 | 重启 Xinference bge-m3；FAIL 后重解析清残留；**评测前提是检索可用** | 已恢复 |
| rerank 端点不可达 | 问答返回 100/404 `172.28.200.80:9997/v1/rerank` | 助手 `rerank_id` 置空（已做）；或修复 rerank 服务 | 已规避 |

### P1 级
| 问题 | 解决方案 | 状态 |
|---|---|---|
| 图片描述默认英文 | 知识库 `language=Chinese`（已设）；新建库直接设中文 | 已解决 |
| VLM 空描述/提示词泄漏（`MODE 1: ...`） | 脚本 `11_fix_abnormal_image_chunks.py`（剥前缀 / picture 库重描述）；长期做源码校验+重试（见 `evaluation-mineru...md` §7.3） | 已做（352 修复 3 处） |
| 跨页续表被按页拆分 | 脚本 `13_merge_continued_tables.py`（D.0.1-1/-2/-3 已合并，表头去重+positions 合并） | 已做 |
| `chunk_count/token_count` 字段失真 | 评测/统计一律用 `/chunks` 接口 | 已规避 |

### P2 级
| 问题 | 解决方案 | 状态 |
|---|---|---|
| 服务器 `ParserConfig` 拒绝 `mineru_*` 键 | 需在目标版本 schema 加字段（补丁 `source/patches/0003`） | 待部署 |
| PaddleOCR 链路 0 图 | 设计见 `design-paddleocr-image-support-2026-09-12.md`；参数透传补丁 `0001/0002` | 待实施 |
| MinerU VLM 健壮化（校验/重试/降并发） | `_enhance_images_with_vlm` 改造要点见主报告 §7.3 | 待实施 |

### 本轮新增（评测链路）
| 问题 | 证据 | 解决方案 | 状态 |
|---|---|---|---|
| 图片问题带页码 → 关键词过滤丢命中 | TC-001/022；检索层 0.40~0.46 可命中 | **已修用例措辞（2026-09-13）**：30 条图片用例全部去页码；8 条无图号的改为内容锚定（部件编号/特征值）；复测 TC-001 → PASS、TC-092 → PASS | ✅ 已落地 |
| 表号精确召回串扰 | TC-071 两次答案对应不同表（D.0.1-1 / D.0.1-3） | **已修用例措辞**：13 条 D.0.1-1/D.0.16/D.0.17 问题补全表名（项目特性表/工程量汇总表/施工总进度表）；后续可叠加 ① chunk `PATCH` 写 `important_keywords` ② 调 `vector_similarity_weight`/`top_n` | ✅ 措辞已落地 |
| Agentic 关键词过滤过严 | 日志 `Kept 1 of 12 passage(s) that actually mention the keywords` | 提问用词与原文一致；或推动服务端放宽过滤（非本仓库可控） | 持续注意 |
| 单题延迟 1~3.5 分钟 | 服务端 Agentic RAG | 分批执行；或评估关闭 Agentic（该版本未见开关） | 接受 |
| 自动评分误判 | 表头关键词为无分隔拼接串 | 已加"字符覆盖率 ≥0.7"兜底；仍建议人工复核 | 已缓解 |
| 运行器会话堆积 | v2 每题新建会话不删除，135 条会残留上百个 | **已升级 v3**：API 运行器每 10 题批量删除；浏览器运行器每 10 题 `DELETE /sessions`（注意本版本列表接口 data 直接是数组） | ✅ 已落地 |
| 中断后全量重跑 | v2 无断点 | **v3 断点续跑**：两个运行器均按 OUT_FILE 已有 ID 跳过 | ✅ 已落地 |

### 2026-09-13 复测记录（修措辞后）
| 用例 | 修前 | 修后 | 说明 |
|---|---|---|---|
| TC-001 图片 | ❌（页码干扰） | ✅ PASS（API 通道） | 问题改为"图3.2.3 展示的内容是什么"，命中图号+描述 |
| TC-022 图片 | ❌ | ❌ 仍未命中（"知识库中未找到"） | 修后问题="图3.29.2 的内容"；检索层仍取不到 → 属于**图号未被索引为关键词**，需走 `important_keywords` 补丁或检索参数调整，非措辞问题 |
| TC-092 竖表 | ❌ | ✅ PASS（5/5 关键词） | 同一问题未改措辞即通过 → 说明该维度**不稳定**（Agentic 过滤抖动），建议全量跑时每类抽 3 条复跑估稳定性 |

---

## 6. 未完成事项（后续 Agent 直接接手）

1. **全量 135 条实测：已完成（本地 Qwen3.8-27B）** —— 见 §4.1（修正后 114/135，84%），
   结果 `results_qwen_full.json`；浏览器运行器仍可用于抽检/取证。
2. **表号/图号召回增强：已做且已证伪有效性** —— 脚本 `scripts/14_add_recall_keywords.py` 已给
   352(137)/101(1) 写入关键词，但复跑 图片/竖表无实质提升（`results_qwen_recallfix.json`）。
   **根因是助手 Agentic RAG 的关键词过滤**，不是索引。下一步应转向：
   - 若目标版本可配：关闭/放宽 Agentic 检索，或提高 `top_n`（现 12）、调 `vector_similarity_weight`（现 0.3）做 A/B；
   - 对图号/表号类问题改用 `/api/v1/retrieval` 直检（已验证可命中）；
   - 提问用词与原文一致（用例已去掉页码等干扰词）。
3. **稳定性抽样**：同一问题多次运行结果会翻转（Agentic 抖动，如 TC-023/TC-081/TC-092），
   建议对每类抽 ≥3 条各复跑 2 次，报"稳定通过/抖动通过/稳定失败"三态，而非单次 pass/fail。
4. **跨页续表合并下沉源码**（可选）：`mineru_parser._transfer_to_tables` 内合并，免后处理。
5. **人工复核与出报告**：把结果与检索诊断合并成最终评测报告（建议 `docs/eval-report-mineru-final-*.md`）；
   自动评分只做初筛，MISS 用例逐条人工看 answer 全文。

---

## 7. 脚本与文档清单（全部相关产物）

### docs/
- `docs/evaluation-mineru-2026-09-12.md` —— MinerU 解析评测（含竖表更正、跨页合并、问题与方案）
- `docs/design-paddleocr-image-support-2026-09-12.md` —— PaddleOCR 出图设计
- `docs/handover-mineru-eval-2026-09-12.md` —— **本文**
- `docs/eval-failed-cases-image-vertical-2026-09-13.md` —— 未通过的 图片/竖表 用例明细（问题/模型回答/正确答案/错误归因/直检证据）

### docs/code/mineru/（脚本与源码）
- `scripts/00~12`：建库、串行上传解析、轮询、统计、抽样、公式核验、竖排表合成、语言设置、
  语言统计、异常检测/修复、辅助库清理
- `scripts/13_merge_continued_tables.py`：跨页续表合并（本次新交付）
- `source/docker-compose.mineru.yml`：MinerU 自部署
- `source/patches/0001~0003`：PaddleOCR 参数透传与 schema 补丁（`git apply --check` 通过）

### docs/code/mineru/testcases/（用例与运行器）
- 盘点/用例/运行器/结果/截图/README（见 §2.4）

---

## 8. 安全与规范

- **禁止**把 API Key、密码写入仓库；一律环境变量。历史明文 key 已视为泄露，需轮换。
- 所有脚本默认 dry-run，写操作（PATCH/DELETE/触发解析）需显式参数；修复脚本用 `--apply`。
- 评测实例为**远端生产倾向实例**：大文档解析（352 约 15 分钟）须串行，避免资源争抢。
- 上传到 `mineru.net` 等云服务会使文档出境；本评测使用自部署/实例内解析。

## 9. 变更记录
- 2026-09-12：初版交接文档（评测用例 135、浏览器实测 11 条、问题与方案、后续任务）。
- 2026-09-13：补记本地 Qwen3.8-27B 全量评测（135/135，判分修正后 114 通过 84%，结果 `results_qwen_full.json`）；
  助手 LLM 从 `deepseek-v4-flash` 切回本地 Qwen 的步骤写入 §2.3。
- 2026-09-13（续）：表号/图号召回增强实验（脚本 `14_add_recall_keywords.py`）与 图片/竖表 复跑；
  结论为 Agentic 检索过滤是瓶颈，关键词增强无实质提升（§4.2）；用例归类修正（TC-127/128/134）与运行器判分放宽见 §4.1。
- 2026-09-13：评审更新——实例与全部资源 ID 核验有效；30 条图片用例去页码+8 条内容锚定+13 条表名补全+TC-018/019 去重；
  复测 TC-001 ✅ / TC-092 ✅ / TC-022 仍 ❌（需 `important_keywords`）；两个运行器升级 v3（会话治理、断点续跑、
  超时重试、DOM 兜底、错误分类）；未完成事项清单更新。
  另：发现 `docs/code/pdf-vertical-table/__pycache__/*.pyc` 内嵌明文 API Key（git 已忽略、不入库，但**本地磁盘存在**，
  该 key 需轮换；`__pycache__` 目录建议直接删除）。
