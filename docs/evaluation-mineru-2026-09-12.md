# MinerU 接入与文档解析评测报告（2026-09-12）

> 目标：在 RAGFlow 实例上接入 MinerU 作为 PDF 解析器，对 `paddleocr-retest` 的三份典型 PDF
> 串行重解析，评估文本/图片/公式/表格/竖排表效果，并给出问题分析与解决方案。
> 评测实例：`https://labragf.openagp.top:9080`（远端，非本地部署）。

## 1. 结论摘要

| 维度 | 结果 |
|---|---|
| 接入方式 | 服务器已通过环境变量接入 MinerU（provider 实例 `mineru-from-env`）；仅需在知识库选 PDF 解析器 = MinerU |
| 文本 | 清晰、无乱码（矢量 PDF 的乱码问题消失） |
| 图片 | ✅ **MinerU 能出图**：SL-T-352 抽出 119 个图片块，含 `image_id`（可引用展示）+ VLM 语义描述 |
| 公式 | ✅ **无丢失**：原文 204 处「式中」，MinerU 抽到 5624 个 LaTeX 片段，含公式编号；抽查原页比对一致 |
| 表格 | ✅ HTML 结构完整（352 73 个表块） |
| 竖排表 | ✅ **实测通过**：447 中整表旋转 90° 的竖表（表D.0.16/D.0.17）MinerU 解析正确（表头/合并单元格/表注齐全）；仅**跨页长表按页拆分**需后处理合并。此前"合成逐单元格旋转"测试不具代表性 |
| 图片描述语言 | 默认英文；把知识库 `language` 改为 Chinese 后，118/119 中文描述 |
| 已知短板 | VLM 偶发空描述/提示词泄漏（已用后处理脚本修复）；服务器版本不接受 `mineru_*` 参数键 |

## 2. MinerU 接入方式

### 2.1 自部署（推荐，数据不出域）
- 部署 `mineru-api`（官方 FastAPI，端口例 8000），RAGFlow 以远程客户端方式调用 `/file_parse`。
- compose 模板见 `code/mineru/source/docker-compose.mineru.yml`（基于官方 `docker/compose.yaml` 适配）。
- 要点：镜像 ≈10GB（内置模型）；GPU ≥8G；`--ipc=host`；vlm-http-client 模式需 `--allow-public-http-client`。

### 2.2 线上 API（免部署，先看效果）
- 官方 `mineru.net`：
  - **Precision Extract API** `POST /api/v4/extract/task` —— 需 Token（API 管理页自助创建），支持 pipeline/vlm/MinerU-HTML。
  - **Agent Flash API** `/api/v1/agent/parse/*` —— 免登录、IP 限流、免费，但仅轻量模型、Markdown 为主。
- RAGFlow 内置 `MinerU.Net` 工厂（`conf/models/mineru.json`，`doc_parse: v4/extract/task`），在「模型提供方」填 Token 即可。
- 注意：文档会出境到官方云，敏感资料勿用；服务器需能出网到 `mineru.net`。

### 2.3 RAGFlow 侧配置
- **环境变量**（自部署）：`MINERU_APISERVER` / `MINERU_BACKEND` / `MINERU_SERVER_URL` 等，进程启动时读取，须重启。
- **模型提供方**（UI/API）：添加 MinerU + API Key，免重启。
- **知识库**：`parser_config.layout_recognize = "MinerU"`；本实例 MinerU 默认
  `method=auto`（自动区分矢量/扫描）、`formula_enable=true`、`table_enable=true`、语言默认中文。
- ⚠️ 本实例的 `ParserConfig` schema **拒绝 `mineru_*` 键**（`Extra inputs are not permitted`），只能用默认参数。

## 3. 本次串行评测执行

新建知识库 `mineru-retest`（`76a691aeae5b11f1896583d540e218a6`），从 `paddleocr-retest` 下载原文逐个上传解析，
**严格串行**（一个完成再下一个）。脚本见 `code/mineru/scripts/01..02`。

| 文档 | 大小 | 解析耗时 | chunk 数 | text | table | image | 含公式块 | html 表 |
|---|---|---|---|---|---|---|---|---|
| SL-T-447-2026 水土保持…规程 | 13.0MB | 107s | 87 | 53 | 34 | 0 | 10 | 34 |
| SL-T-352-2020 水工混凝土试验规程 | 41.7MB | 906s（首次）/ **833s（中文重跑）** | 494 | 302 | 73 | **119** | 317 | 72 |
| SL-101-2014 钢闸门和启闭机安全检测… | 18.9MB | 51s / 46s | 26 | 22 | 3 | 1 | 10 | 3 |

> 说明：文档列表字段 `chunk_count`/`token_count` 严重失真（352 字段 1595 vs 接口 494），评测一律以
> `/chunks` 接口的 `total` 为准。

## 4. 解析效果评估

### 4.1 文本
矢量 PDF（352）无乱码，`GB/T 51297`、`SL 73.6` 等编号正确；对比 DeepDoc 基线的 `SI 73.6`（应为 SL）、
`设计标推`（应为 设计标准）等 OCR 错误，MinerU 明显更干净。

### 4.2 图片（核心收益）
- 352：**119 个图片块**，每块带 `image_id`（引用处可展示原图）+ VLM 描述。
- 101：1 个图片块；447：0（文档本身图少）。
- 描述样例（352 工程图）：
  > 图像展示一个二维坐标系…分布着大量黑色点状元素，从底部水平轴向上逐渐变密，整体形成近似三角形轮廓…

### 4.3 公式
用本地 PyMuPDF 对照原文核验（脚本 `05_formula_loss_check.py`）：

| 项 | 数值 |
|---|---|
| 352 PDF 页数 | 433 |
| 原文「式中」（公式变量说明） | 204 处 / 158 页 |
| MinerU LaTeX 片段 | **5624** |
| 含「式中」的 chunk | 149，其中**缺 LaTeX = 0** |

原页 vs MinerU（page 30）：
- 原页：`ρd = G1/(G1+G3-G2) × ρw   (3.8.4-1)`
- MinerU：`$$ \rho_{\mathrm{d}} = \frac{G_{1}}{G_{1}+G_{3}-G_{2}} \times \rho_{\mathrm{w}} \tag{3.8.4-1} $$`

→ **公式无丢失，公式号（`\tag{}`）、下标、分式均保留。**

### 4.4 表格
输出为 HTML `<table>`，表头/合并单元格结构完整（如标准清单表、工程特性表）。

### 4.5 竖排表（447 实测 + 受控实验）

**（1）447 真实竖表 —— MinerU 解析正确**

447 的附录为**扫描图像页**（第 70–97 页，`page.get_text()` 为空、整页一张图）。其中含：

- **整表旋转 90° 的横排表**：第 92 页 `表D.0.16 工程量汇总表`、第 93 页 `表D.0.17 工程施工总进度表`；
- **纵向长表**：`表D.0.1-1/-2/-3`（第 70–80 页，跨页"续表"）。

把第 92 页用 `page.get_pixmap → PIL rotate(-90, expand=True)` 转正后逐项核对，MinerU 输出与之**一致**：

| 位置 | 真实内容 | MinerU 输出 |
|---|---|---|
| 表头 | 项目(合并2列) \| 单位 \| 措施数量 \| 土方开挖/m³ … 投工/工日 | 一致 |
| 首行 | 合 计（是"行"） | `colspan=2` 合计行 ✓ |
| 分组 | 工程措施 `rowspan=8`、林草措施 `rowspan=4` | rowspan=8 / rowspan=4 ✓ |
| 表注 | 注1/注2/注3 | 保留 ✓ |

→ **MinerU 对"整表旋转"的竖表读取正确**，无转置/表头错配。

**（2）跨页续表拆分 —— 真实问题，已由后处理脚本解决**

`表D.0.1` 跨第 70–74 页，MinerU 每页输出一张表 → 每页一个 chunk、表头重复。用
`13_merge_continued_tables.py` 按"续表 <表号>"分组合并（PATCH 首块 + DELETE 其余）：
表块 34→25、总块 87→78，三组续表各合并为一块，表头去重、positions 跨页合并、表题/表注完整，复跑幂等。

**（3）受控实验（仅作对照，结论已修订）**

早期用"未旋转表内**逐单元格**文字旋转 90°"的合成件测试，MinerU 误读（转置/串字）。
该情形与 447 的"**整表统一旋转**"不同且更难，**不能据此判定 MinerU 不支持竖排表**；已作废该结论。


### 4.6 与 PaddleOCR 的对照（按用户要求已取消，仅记录现象）
| 文档 | MinerU chunks | PaddleOCR chunks |
|---|---|---|
| 447 | 87 | 141 |
| 101 | 26 | 27 |
| 352 | 494 | —（解析被 CANCEL，接口 0） |

客观差异：PaddleOCR 链路 **图片块恒为 0**（解析器主动删图）；MinerU 能出图。粒度和速度上 MinerU 表现更好。

## 5. 问题清单

| 级别 | 问题 | 证据 |
|---|---|---|
| P0 | 首轮批量解析全部 FAIL，**根因是 Xinference `bge-m3` 嵌入模型进入 `stopping state`**，非 MinerU 故障 | `Fail to bind embedding model` / `Generate embedding error` |
| P1 | 文档列表 `chunk_count`/`token_count` 字段失真（含多次重解析残留） | 352 字段 1595 vs 接口 494 |
| P1 | 图片描述默认英文 | 知识库 `language=English` |
| P1 | VLM 偶发**空描述**与**提示词泄漏**（把 `MODE 1: STRUCTURED VISUAL DATA OUTPUT` 吐进正文） | 352 检出 LEAK=2、EMPTY=1 |
| P1 | **跨页续表被按页拆分**（每页一个 chunk、表头重复） | 447 表D.0.1 跨 70–74 页；已用脚本 13 合并 |
| P2 | 服务器 `ParserConfig` 拒绝 `mineru_*` 键，无法按文档特性调参 | `Extra inputs are not permitted` |
| P2 | PaddleOCR 链路不出图（需改源码） | `_remove_images_from_markdown` |
| P2 | 大文档（352，433页）耗时 ~15 分钟，需串行调度 | 本次串行执行 |

## 6. 根因分析

1. **FAIL ≠ 解析失败**：MinerU 解析本身成功（日志 `[MinerU] done, sections: …`），失败发生在**后续 embedding 阶段**；
   嵌入模型服务不稳定（进入 stopping state）导致整批任务标记 FAIL，且残留 chunk 污染统计字段。
2. **图片描述语言**：`rag/prompts/vision_llm_figure_describe_prompt.md` 用 `{{language}}` 渲染，取值来自知识库
   `language`；知识库默认 English → 描述英文。**改配置即可，无需改源码**。
3. **空描述/泄漏**：`_enhance_images_with_vlm`（`deepdoc/parser/mineru_parser.py`）对 VLM 返回不加校验，
   空则静默跳过、脏文本照写；且 `ThreadPoolExecutor(max_workers=10)` 高并发易诱发模型不稳定。
4. **跨页续表**：MinerU 按页输出表块，RAGFlow 对每个表块单独成 chunk；同一续表跨页时表头重复、被拆开。
   （注：真实"整表旋转"竖表 MinerU 解析正确，此前误判已更正。）
5. **PaddleOCR 无图**：`PaddleOCRParser._transfer_to_sections` 调 `_remove_images_from_markdown` 删图，
   且 `_transfer_to_tables` 直接 `return []`，`by_paddleocr` 也未接 VLM。虽然 `__images__/crop` 已实现，
   但整条图链路未打通。

## 7. 解决方案

### 7.1 配置层（零改码，立即可用）
- **分块**：`delimiter="\n!?;。；！？"`、`chunk_token_num=1024`（减少碎片，实测 447 从 180→141 块、极短块 21→2）。
- **图片中文描述**：知识库 `language=Chinese`（实测 352 的 118/119 图片描述转中文）。
- **串行调度**：受限实例逐个上传+解析，避免资源争抢（本次已按此执行）。
- **不要用文档列表字段做评测基准**，统一用 `/chunks` 接口。

### 7.2 后处理脚本（零改码，已交付并验证）
`code/mineru/scripts/11_fix_abnormal_image_chunks.py`：
- 检测两类异常：LEAK（含提示词标记）/ EMPTY（剥离字段名后值近乎为空）。
- LEAK：剥掉 `MODE 1/2`、`STRUCTURED VISUAL DATA OUTPUT`、`GENERAL FIGURE CONTENT` 前缀，PATCH chunk。
- EMPTY：取 `image_id` → 下载原图 → 上传辅助 picture 知识库（走租户 VISION 模型重描述）→ PATCH 回原块。
- 本次对 352 修复 3 个异常（2 LEAK + 1 EMPTY），复检 0 异常；447/101 检测 0 异常。

### 7.3 源码层（需部署，长期根治）
1. **MinerU VLM 健壮化**（`deepdoc/parser/mineru_parser.py::_enhance_images_with_vlm`）：
   - 增加描述校验：非空且足够长、语言匹配、**不含提示词标记**；
   - 失败重试 K 次，仍失败回退 caption/跳过，不写脏描述；
   - 降低 `max_workers`（10 → 3~5）或将并发做成可配置。
2. **PaddleOCR 图片能力**（若坚持 PaddleOCR 链路）：按 DeepDoc 既有 figure 流水线改造，详见
   `design-paddleocr-image-support-2026-09-12.md`；配套参数透传补丁：
   - `0001-parser-config-utils-paddleocr-algorithm-config.patch`
   - `0002-naive-by-paddleocr-algorithm-config.patch`
   - `0003-validation-utils-parserconfig-paddleocr-keys.patch`
3. **服务器端放开 `mineru_*` 参数键**：在目标版本的 `ParserConfig` schema 增加对应字段，使
   `mineru_parse_method/mineru_formula_enable/mineru_table_enable/mineru_lang` 可通过 API 下发。

### 7.4 跨页续表合并（已交付脚本，零改码）
`code/mineru/scripts/13_merge_continued_tables.py`：按"续表 <表号>"分组识别跨页续表，
PATCH 首块写入合并内容（表头去重、positions 跨页合并）+ DELETE 其余块。
447 实测：表块 34→25、总块 87→78，三组续表合并成功且幂等（dry-run 默认，`--apply` 生效）。
长期可下沉到解析器层（`mineru_parser._transfer_to_tables` 合并后再返回）。

### 7.5 竖排表能力结论（已更正）
447 实测表明 MinerU 能正确处理**整表旋转 90°**的竖表；无需方向矫正预处理。
仅在遇到"表内逐单元格旋转"等更极端版式时，才需在送解析前做**页面方向矫正**（可参考本仓库
已有的 `docs/pdf-vertical-table-*.md` 方案）。

## 8. 建议与后续

- **结论**：要「出图」就用 MinerU；文本/公式/表格质量均达标；**整表旋转的竖表也正确**；仅跨页续表需合并（脚本已交付）。
- 后续可做：
  1. 跨页续表合并下沉到解析器层（`_transfer_to_tables`），免去后处理；
  2. 对 447 的 0 图做漏抽排查（对照原文页码）；
  3. 将 MinerU VLM 健壮化与 PaddleOCR 参数透传补丁提交上游/部署；
  4. 若需图片中文描述成为默认，新建知识库时直接设 `language=Chinese`。

## 9. 附录：目录与脚本清单

见 `code/mineru/README.md`。本次产物：
- 知识库 `mineru-retest`：三个文档 + 竖排表受控测试件（`vertical_scan.pdf`）+ 447 副本（`SL-T-447-2026_hangye.pdf`，用于跨页续表合并验证：表块 34→25、总块 87→78）；
- 辅助知识库 `vlm-redesc`（picture 类型，重描述用，已清空）。
