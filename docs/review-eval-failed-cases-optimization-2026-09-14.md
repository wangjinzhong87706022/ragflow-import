# 评审：图片/竖表未通过用例明细 × RAGFlow 源码级优化方案（2026-09-14）

> 评审对象：`docs/eval-failed-cases-image-vertical-2026-09-13.md`（18 条未通过用例的四分类归因），
> 以及 `docs/eval-report-mineru-glm-2026-09-13.md` §7 已落地优化（important_keywords PATCH 等）的残余问题。
> 评审方法：逐条核对归因证据 + 通读本机 RAGFlow v0.27.1 源码（`E:\git\ragflow`，git tag v0.27.1，
> 与评测实例 `labragf.openagp.top:9080` 同源）的检索/对话/chunk 更新链路。
> 结论先行：**归因四分类基本成立；但"检索排序未召回"10 条的根因可以进一步定位到
> 「ES 候选窗口入选」而不是「窗口内排序」，因此还有 3 个未尝试的强杠杆（questions PATCH 重嵌入、
> rerank_candidates_count 扩窗、top_n 提额），预期可将图片/竖表残余 MISS 再收敛一半以上。**

---

## 1. 对原文档的评审结论

| 维度 | 评价 |
|---|---|
| 归因分类 | ★★★★☆ 四分类（未召回/丢块/判分口径/用例设计）与直检证据一致，且"关键更正"（10 条连直检都不含）方法论正确 |
| 证据链 | ★★★★☆ 每条给出 targets/top_sim/目标是否在返回块中，可复核 |
| 机理表述 | ★★★☆☆ 两处机理描述与源码不符，需更正（见 §2，影响后续方案选择） |
| 可执行性 | ★★★★☆ 残余归因表已给出根治手段，但其中一条（vector_similarity_weight 0.3→0.5）对主因无效，应替换 |

### 1.1 需更正的三点

**① "Agentic 过滤丢块"实为 top_n 截断，不存在"关键词过滤"阶段。**
本版本对话链路（`api/db/services/dialog_service.py:788-822`）在检索后只有三次加工：
`retrieval_by_toc`（可选）、`retrieval_by_children`（父子块换入）、`use_kg`（KG 块插首），
然后 `kb_prompt()` 按 top_n 顺序截断喂 LLM（`rag/prompts/generator.py:139`）——没有任何 LLM"过滤"步骤。
TC-011/030/082 的直检用的是 **top_n=10**，而评测助手配置是 **top_n=6**（交接文档 §3 实测）：
目标块排名 7~10 时，直检"目标在返回块中=True"与助手"上下文里没有"两者同时成立。
**归因命名应改为「top_n 截断」，对应修法就是提额，不是改提示词。**

**② 「调 vector_similarity_weight 0.3→0.5」治不了窗口外问题。**
`retrieval()` 里 vsw 只参与两处：最终融合配比 `tkweight*tksim + vtweight*vtsim`（`rag/nlp/search.py:491`）
和 min_match 开关（`:620`）。**候选窗口的构成在 ES 层就已定死**：
`FusionExpr("weighted_sum", knn_top_k, {"weights": "0.001,1"})`（`search.py:234`，ES 路径文本/向量权重硬编码），
ES 按 0.001×BM25 + 1.0×knn 余弦排序后只返回前 `rerank_candidates_count`（默认 64，`db_models.py:1478`）条，
下游所有打分（tksim、rerank、tag_fea、pagerank）都只能在这 64 条内重排。
vsw 0.3→0.5 改变的是 64 条内部的配比——对"目标块根本不在 64 条里"的用例（TC-002/003/006/012/013/014/029 等）无效。
**该 A/B 应替换为 `rerank_candidates_count` 64→512**（`/api/v1/retrieval` 与 dialog 均接受该参数，
`chunk_api.py:397`、`db_models.py:1478`），把窗口开大到让长尾图/表块进入，再由本地重排抬升。

**③ 同图不同期望要点（用例内部不一致）。**
TC-006 与 TC-029 问的是同一张图 5.5.2，期望要点却分别是
`['试块','位移计','标距定位杆']` 与 `['固定螺钉','位移计顶杆']`——后者在"正确答案/依据"文本里并未出现。
这 2 条即使检索修复后也会各有一条被误判。建议以 `mineru_content_inventory.json` 的 Labels 字段统一重生成。

---

## 2. 源码机制核查（优化方案的依据）

检索全链路（ES doc engine 路径）与各可调点的位置：

```
问题 q ──▶ ES 查询: matchText(0.001) + matchDense(1.0) 融合排序          search.py:234
        ──▶ 取前 rerank_candidates_count(=64) 条为候选窗口               search.py:599-603
        ──▶ 本地重排 sim = 0.7×tksim + 0.3×knn余弦 + pagerank + tag_fea   search.py:491,393,389
             其中 tksim 词袋 = content×1 + title×2 + important_kwd×5 + question×6   search.py:485
        ──▶ 相似度阈值过滤(≥0.2/0.1) → 稳定排序                          search.py:698-700
        ──▶ 截前 top_n(=6) 条进 prompt                                   dialog_service.py:796
```

全文检索字段权重（决定 BM25 侧谁说了算，`rag/nlp/query.py:32-40`）：
`important_kwd^30 > question_tks^20 / important_tks^20 > title_tks^10 > content_ltks^2`。

三个**唯一能把窗口外的块补进来**的通道（都在重排之前/之外起作用）：
1. `important_kwd^30` 的 BM25 加成：0.001 的融合权重下，图号精确命中产生的 BM25（^30 放大后可达几十）
   仍能撬动 0.1 量级的排序差——这解释了 9-13 PATCH 为什么翻转了 TC-022/024/074/088，而对 BM25 增益不足的用例无效；
2. `toc_enhance`：`retrieval_by_toc` 按 LLM 对目录条目打分**直接从 doc-store 取块追加**，完全绕过窗口
   （`search.py:872-931`，块按 TOC ids 直取，`:907`）；
3. `retrieval_by_children` / `use_kg` 同理是窗口外补块（`search.py:935`、`dialog_service.py:816-822`）。

**关键发现（本次评审最重要的杠杆）**：PATCH chunk 的 `questions` 字段会**整体替换重嵌入的文本**——
`update_chunk` 的重嵌入逻辑是 `v = 0.1×doc名向量 + 0.9×(question_kwd 拼接 || content)`（`api/apps/restful_apis/chunk_api.py:1220-1226`），
即**一旦设置 questions，嵌入输入从整段正文换成短问题串**。图/表块的痛点恰恰是：
正文是几百 token 的 VLM 描述，向量被稀释，"图5.5.2"式短查询的余弦排不进窗口；
把嵌入换成 `图5.5.2 附着式变形测量架示意图（方框式）` 这样的短文本后，向量直接对齐图号查询，
同时 `question_tks` 还拿到 ×6 词袋权重 + ^20 全文权重。这比已做过的 important_keywords PATCH
（只改词袋与 BM25，向量不变，`14_patch_important_keywords.py` 的重嵌入输入仍是原 content）强一档。

判分/抖动侧的源码事实：检索排序用稳定排序（`search.py:695`），同一检索配置下是确定性的；
评测观察到的翻转（TC-022 两跑不一致）只能来自 LLM 环节——`refine_multiturn` 的
`full_question` 改写（`dialog_service.py:729-730`）、`prompt_config.keyword` 的关键词扩展
（`:737-738`，LLM 生成）、以及 temperature 0.1 的生成本身。评测期把前两个固定住，抖动即可归零到生成本身。

---

## 3. 优化方案（按杠杆强度/实施成本排序）

### P0-A　图/表块 PATCH `questions` 字段（重嵌入）——主攻 10 条"未召回"

- **做法**：新脚本 `docs/code/mineru/scripts/15_patch_questions.py`（沿用 14 号脚本的 dry-run/apply/verify 三段式）：
  - 图块：`questions = ["图5.5.2 附着式变形测量架示意图（方框式）的可见要素：试块、位移计、标距定位杆、夹具"]`
    （图号 + 图题 + Labels 部件词，一句话覆盖"按图号查"与"按图名/部件查"两类问法）；
  - 表块：`questions = ["表D.0.15 水土保持措施量汇总表的表头列名"]`（表号 + 表名 + 查询意图词）。
- **依据**：`chunk_api.py:1178-1182`（questions → question_kwd/question_tks）、`:1220-1226`（重嵌入换源）。
- **脚本状态（已落地并自检，2026-09-14）**：
  - 盘点数据核验：18 条未通过用例涉及的 8 个图号（图3.5.2/3.29.2/5.5.2/5.5.3/5.13.2/5.25.2-1/5.25.2-2/5.25.3）
    与 6 个表号（表D.0.1-1/-2/-3、D.0.15/D.0.16/D.0.17）在 `mineru_content_inventory.json` 全部可定位；
  - 守卫测试 5 例通过：图号开头命中、带子图前缀（"（c）过分干燥；"）命中、
    "如图5.5.2所示"引用正文拒绝、长前缀正文拒绝、已有 questions 幂等跳过；
  - Labels 提取带形态过滤：顿号部件词列表才入问题串；长句描述形态
    （如 图3.2.3 的"图中包含一条水平黑实线…"）弃用，只写图号+图题；
  - 全量 51 个图号问题串生成 0 坏例（无超长/无句级字符污染）。
- **执行**（需环境变量后人工运行）：
  ```bash
  cd docs/code/mineru/scripts
  python 15_patch_questions.py detect --kb <KB> --doc <352文档ID>          # 预览
  python 15_patch_questions.py apply  --kb <KB> --doc <352文档ID>          # 352 图块
  python 15_patch_questions.py apply  --kb <KB> --doc <447合并版文档ID>    # 447 表块
  python 15_patch_questions.py verify --kb <KB> --doc <文档ID>             # 复查
  ```
- **预期**：10 条"未召回"中图号/表号精确类（TC-002/003/006/012/013/014/029、TC-074/076/082）
  的向量入选位次大幅前移；这也是对 PATCH 前后对照实验的自然延续。
- **风险与对策**：嵌入不再表示描述正文 → 问题串已含图题与核心部件词兜底；
  上线前先对 5 条已 PASS 图片用例回归，确认无回退；
  回滚：对同块 PATCH `questions: []` 后再 PATCH content 原文即按 content 重新嵌入（可逆）。

### P0-B　助手 top_n 6→12——主攻 3 条"截断"（原"Agentic 过滤"）

- TC-011/030/082 直检 top-10 已含目标；top_n=12 一并覆盖排名 11~12 的边缘情形。
- 同步把 §7.3 归因表中的"Agentic 过滤丢块"改名"top_n 截断"，避免后续接手者去找不存在的过滤阶段。

### P0-C　`rerank_candidates_count` 64→512 A/B——替代原方案中的 vsw 调参

- `/api/v1/retrieval` 直检与 dialog 均支持（`chunk_api.py:397-403`，要求 ≥ page×page_size）。
- 窗口开大后，长尾块进入候选集，由本地重排（0.7 tksim，含 important_kwd ×5）抬升；
  单 KB 千余块规模下 512 窗口的本地重排开销可忽略（词袋相似 + 一次 ES knn-score 回查）。
- 对照设计：P0-A 前/后 × 窗口 64/512，四格各跑 18 条未通过用例。

### P1-D　评测稳定性收敛（对应用例"抖动"）

- 评测助手固定 `refine_multiturn=false`、`keyword` 固定一种配置（建议 false——图号查询不需要 LLM 扩词，
  扩词反而引入不确定性），跑前用 API 检查并写死这两个开关；
- 落地交接文档待办#3：每类抽 ≥3 条 × 2 次，报"稳定通过/抖动/稳定失败"三态。

### P1-E　按文档名的 metadata 硬过滤缩窗

- 评测 KB 混装 6 个文档，352 的图块要和 447/101 的块竞争 64 个窗口位。
- 给评测助手配 `meta_data_filter`（auto 模式按"标准名/文档名"字段过滤，`common/metadata_utils.py:153`，
  已支持 ES 推送下推）——问 352 时窗口里只剩 352 的 494 块，图块的窗口内位次整体前移。
- 注意：mineru-retest 建库脚本（00~14）若未注册 metadata schema，需先补注册；生产管线的 11 字段模式可复用。

### P1-F　rerank 模型 A/B（可选）

- `rerank_id` 配置后走 `rerank_by_model`（`search.py:527-552`）：cross-encoder 对
  `content + title + important_kwd` 重打分。**注意配比仍是 0.7×词袋 + 0.3×rerank 分**
  （`tkweight/vtweight` 沿用 vsw 配置），且只在窗口内重排——优先级低于 P0-A/B/C。
- 前置：确认 tokenrhythm 租户是否有可用 rerank 模型。

### P2-G　`toc_extraction` + `toc_enhance`（竖表专项，需重解析）

- 附录 D 的表 D.0.x 通常出现在标准目录中；开启 naive `parser_config.toc_extraction`（重解析生成
  `toc_kwd="toc"` 块，`task_executor.py:1628,669-698`）+ 助手 `toc_enhance=true`，
  `retrieval_by_toc` 会按目录条目直接补块，绕过窗口——对"表D.0.15/D.0.1-2/D.0.1-3"类查询是独立召回通道。

### P2-H　竖表 parent_child 重解析（中期）

- 跨页合并后的整表块动辄上千 token，表头只占向量的一小部分（TC-074/076 的直接痛点）。
- naive 已支持 `parent_child.use_parent_child` + `children_delimiter`（`api_utils.py:444-450`，
  父块 `available_int=0` 不参检，子块命中后由 `retrieval_by_children` 换入整表内容喂 LLM，
  `task_executor.py:1307-1326`、`search.py:935`）——子块粒度召回 + 整表完整性兼得。
- 与 P2-G 同一次重解析顺带做；MinerU 产物是 md，需确认 md 导入路径同样生效。

### P2-I　上游补丁（仅评测实例，需用户裁定）

- ES 融合权重 `{"weights": "0.001,1"}` 硬编码（`search.py:234`）是窗口入选几乎纯向量化的根子。
  参数化为随 `vector_similarity_weight` 可配（对齐 Infinity 路径的 `build_fusion_expr`）后，
  `important_kwd^30` 的全文通道才能真正参与窗口入选。
- 仅建议在评测实例（labragf）上以补丁形式试验；生产导入实例继续遵守"零上游修改"约束。
- 另注：`update_chunk` 若设置 questions 后想恢复原向量，需清空 questions 再 PATCH 一次 content 即可，可逆。

### 判分与用例（与原文档 §结论分类一致，补两点）

- TC-073/079/081 按已修的判分口径复跑即 PASS；TC-020/025 按原文档建议改反幻觉类或删除。
- 补 TC-029/TC-006 期望要点统一（§1.1-③），随 P0 复测一起修，避免修复被误判掩盖。

---

## 4. 建议实施顺序与预期

| 步骤 | 动作 | 成本 | 预期翻转 |
|---|---|---|---|
| 1 | P0-B top_n 6→12 + 固定 refine_multiturn/keyword | 纯配置 | 3 条截断类全翻 |
| 2 | P0-A questions PATCH（352 图块 + 447 表块，脚本已落地待执行） | detect 复核后 apply | 未召回 10 条中 4~6 条 |
| 3 | P0-C 窗口 64→512 对照 | 纯配置 | 再翻 1~3 条长尾 |
| 4 | P1-E 文档名过滤 + P1-D 三态稳定性报告 | 低 | 收敛口径、防误判 |
| 5 | P2-G/H 重解析实验（toc + parent_child） | 一次重解析 | 竖表类长尾根治 |

按保守估计（步骤 1-3），18 条未通过中可再翻转 7~10 条；
叠加判分口径修正（3 条）与用例修订（2 条），图片/竖表两类可达到
图片 ≈26/30、竖表 ≈26/28 的自动判分口径。

## 5. 与既有待办的合并

- 交接文档待办#2（important_keywords 实验）→ 已完成，由本方案 P0-A 接棒（questions 重嵌入是同一杠杆的加强版）；
- 待办#3（稳定性三态）→ P1-D 落地；
- 待办#4（API 全量分批）→ 不变；P0 三项落地后建议全量重跑一次作为新基线；
- `docs/eval-report-mineru-glm-2026-09-13.md` §7.3 归因表 → 替换"vsw 0.3→0.5"为"rerank_candidates_count 64→512"，
  "Agentic 过滤"改"top_n 截断"（理由见 §1.1-①②）。

---

## 6. 执行记录（2026-09-14，P0 三项已落地）

### 6.1 配置变更（P0-B / P0-C）

| 项 | 变更 | 状态 |
|---|---|---|
| top_n | 6 → **12**（发现已被提前调至 12，确认生效） | ✅ |
| refine_multiturn | true → **false**（消除 LLM 问题改写抖动） | ✅ |
| rerank_candidates_count | 64 → **512**（候选窗口扩容） | ✅ |

### 6.2 questions PATCH（P0-A）

- 352 图块：**39 个**写入（detect→apply 全 OK）；447 合并版表块：**23 个**写入（verify 复核 39/23 全部持久化）。
- 表块身份匹配升级：附录 D 一页常有两张表且多表同构列头，页码法实测错 3 处（p87/p88/p94）、
  同构列头法错 1 处（D.0.1-1 vs D.0.1-2）。最终算法 = 列头子串命中(×10) + 列头完整性比(±2) +
  正文行命中(×3)，实测 **6/6 正确**。
- 图块 questions 富集：39 块中 33 块初版只有"图号+图题"（盘点 Labels 为长句形态不可拆），
  补一步从块 content 提取 Labels 引号词/尺寸数字（φ80、300~500、R8.9 等）追加为"可见要素"，**26 块富集完成**。
  实测富集是决定性的：TC-002 的图3.5.2 块在富集前长问题查询连 512 窗口都进不去，富集后进位次 5。

### 6.3 直检对照（P0-C，16 用例 × 2 轮 × 64/512 双窗）

| 结果 | 用例 |
|---|---|
| 64 窗即命中且双轮稳定 | TC-003(r1) TC-006(r1) TC-011(r1) TC-029(r5) TC-030(r2) TC-074(r3) TC-076(r1) TC-082(r1) TC-073(r1) TC-079(r1) TC-081(r1) — 共 11 条 |
| **仅 512 窗命中（确定性翻转）** | TC-012(r1) TC-013(r3) TC-022(r2) — 3 条 |
| 仍 miss（双窗一致） | TC-002（图3.5.2）、TC-014（图5.25.3，块在 512 窗内位次 51） |

- 相比 9-13 基线（16 条直检 10 条 miss）：**检索层 miss 从 10 → 2**，且所有命中均双轮可复现。
- 512 窗对 3 条用例是必要条件（64 窗两轮全 miss），证明候选窗口扩容是独立有效杠杆，
  已固化为助手配置。
- 残余 2 条的共同点：目标块与查询的语义重叠最低（TC-014 目标块是 5 个 token 的位置说明图；
  TC-002 的尺寸词与"可见要素"问法语义距离远）。根治路径即 §3 P2-I（ES 融合权重参数化，
  让 important_kwd^30 的 BM25 精确通道参与窗口入选），当前 API 层无解。

### 6.4 评测模型切换

- opencode go `deepseek-v4-flash`：网关硬性要求 `x-opencode-session` 请求头，
  RAGFlow OpenAI-API-Compatible 通道无法注入（本地注入代理 `17_opencode_proxy.py` 已写好并测通，
  但 RAGFlow 部署在远程服务器，无法回连本机代理——如需启用，把代理部署到 RAGFlow 服务器即可）。
- **改用 StepFun Step Plan `step-3.7-flash`**：真实 baseurl `https://api.stepfun.com/step_plan/v1`
  （通用端点 api.stepfun.com/v1 对该 key 报 quota 超额；step_plan 专用端点 200）。
  已注册为 RAGFlow OpenAI-API-Compatible 实例 `stepfun-step-plan` 并绑定评测助手，端到端冒烟通过。

### 6.5 事故与恢复（重要，供后续运维参考）

P0-B 施加 `refine_multiturn` 时误传了 `{"prompt_config": {"refine_multiturn": false}}`，
PUT 语义是**整体替换** prompt_config，导致助手 system 提示词/参数丢失，所有请求报
`KeyError('system')`。已从桌面备份 `assistant_backup_46f41fba.json` 完整恢复
（system/parameters/prologue/quote/empty_response/tts），并在恢复后的配置上置
refine_multiturn=false。### 6.6 复测结果（18 条未通过用例 × P0 全量落地后，模型 step-3.7-flash）

结果文件：`docs/code/mineru/testcases/results_retest18_post_p0.json`。**10/18 自动判分 PASS**（基线 0/18）。

| 用例 | 结果 | 说明 |
|---|---|---|
| TC-003/006/011/012/013/022/030 | ✅ 图片 7 条翻转 | TC-012/013/022 是 512 窗直检命中的端到端验证；TC-006/011/030 满分 5/5 |
| TC-074/076/082 | ✅ 竖表 3 条翻转 | TC-082 从"完全找不到"→ 3/3 满分 |
| TC-002 | ❌ | 检索层残余（富集后仍进不了窗口，见 §6.3） |
| TC-014 | ❌ | 同上（目标块 512 窗内位次 51） |
| TC-020/025 | ❌ | 9-13 已定性为用例设计缺陷，非检索问题（TC-025 模型正确拒答"该试验已删除"，行为正确） |
| TC-029 | ❌ 0/2 | 检索命中且答案实质正确（答出图5.5.2 方框式测量架），但期望要点 `固定螺钉/位移计顶杆` 与盘点 Labels 不符——§1.1-③ 用例内部不一致问题的实锤，修用例即 PASS |
| TC-073/079/081 | ❌ 0/N | 答案实质正确（9-13 已定性为判分口径问题），按已修的反幻觉/人工复核口径应计 PASS |

**修正口径**（检索行为正确 + 模型行为正确 + 用例修订后）：
18 条中实际失败仅 **TC-002/TC-014**（纯检索层长尾）+ TC-020（用例缺陷待改）≈ 3 条，
自动判分口径 10/18，人工复核口径 **15/18（83%）**。

### 6.7 遗留事项

1. TC-002/TC-014：API 层无解，走 §3 P2-I（ES 融合权重参数化，仅评测实例）或接受；
2. TC-029 期望要点按盘点 Labels 统一重生成（随下次全量前修订）；
3. opencode go `deepseek-v4-flash` 启用路径：把 `17_opencode_proxy.py` 部署到 RAGFlow 服务器
   （RAGFlow 模型 base_url 指 `http://127.0.0.1:8787/v1`），或为该 key 走独立中转；
4. StepFun key 为 step-plan 套餐额度（通用端点已超额，仅 step_plan 端点可用），评测后建议从助手解绑以免套餐额度被日常问答消耗。
