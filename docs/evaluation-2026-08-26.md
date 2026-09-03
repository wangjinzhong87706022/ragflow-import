# 桃曲坡知识库导入：方案与代码评测报告

- **日期**：2026-08-26
- **评测对象**：
  - 方案：`docs/specs/2026-08-25-taoqupo-reservoir-kb-design.md`（v2）、`docs/plans/2026-08-25-taoqupo-reservoir-kb-import.md`
  - 代码：`src/`（9 个模块 + 9 个测试文件，46 用例）
- **评测方法**：全量 diff 走查（对照 spec §1.3–§7 与计划 Global Constraints）+ 全套单元测试 + 关键 API 契约与 RAGFlow v0.27.0 源码比对

---

## 一、总体结论

| 维度 | 评级 | 说明 |
|---|---|---|
| 方案完备性 | **A** | spec 覆盖语料盘点→预处理→建库→导入→质检→持续优化全链路；关键技术决策均有源码级依据（picture.py:99-103、validation_utils.py:536、search.py:344-374 等） |
| 计划可执行性 | **A-** | 10 任务全部按 TDD 完成，接口契约自洽；个别任务实现时发现计划外事实（29 个无前缀 OCR 文件）需现场裁决 |
| 代码正确性 | **B+**（修复后 A-） | 初版存在 4 个 Critical（凭据读取方式、错误 PEM 路径、VLM 假认证、测试污染产物），已全部修复并复验 |
| 测试质量 | **B+** | 46 用例全 mock 不触网，覆盖核心不变量（去重唯一性、year int 强转、RSA 密文长度、幂等建库）；缺口见"遗留问题" |
| 安全性 | **A-** | 凭据仅经环境变量；`.gitignore out/` 防产物入库；语料根/RAGFlow 根零写入 |

## 二、方案评测（spec + plan）

### 2.1 做对了的关键决策

1. **多模态离线 VLM 路线**——绕开了 RAGFlow picture 解析器对 OCR>32 字符图片跳过视觉模型的限制（rag/app/picture.py:99-103），并配 `--approve` 人工门 + 三值交叉校验（1454/2218/788.5），风险控制闭环完整。
2. **GraphRAG 配置优先、提示词定制留作二阶段**——`entity_types` 经确认直接注入抽取提示词（light/graph_extractor.py:52,63），零上游改动即可限定 6 类本体；预构建镜像不可改源码的约束被正确识别。
3. **标签软信号 / 元数据硬过滤分离**——v2 修正了 v1 把标签当硬过滤的误判；精确过滤迁移到 `meta_fields + meta_data_filter`（manual/semi_auto/auto 三模式），与 search.py 的 ×10 重排加权实现一致。
4. **KB 级配置先于上传**——`tag_kb_ids/raptor/graphrag` 读 KB 实时配置而 `auto_keywords/auto_questions` 读文档快照的层级差异被显式写进流程（run_setup 先配齐再允许 run_import）。
5. **映射表人工门**——222 文件的元数据映射落成 `out/mapping.csv`，自动推导只做初稿，操作员复核后才进入导入；未匹配文件不猜测。

### 2.2 方案层面遗留风险（非缺陷）

- **GraphRAG token 成本未实测**：每 chunk ≥3 次 LLM 调用，ds1+ds3 全量开启的成本只有 pilot 后才知道。计划的 `--limit 5 --dataset ds3` 试跑步骤是必要门槛，不可跳过。
- **Qwen3.8-27B Q4 量化数值精度**：库容表/泄流曲线识别仍可能出错，交叉校验只能拦住已知三值，其余行依赖人工复核。
- **镜像与 repo HEAD 差异**：所有源码行号引用基于 repo HEAD；实施前应 diff 容器内 task_executor.py / rag/graphrag/*（spec §7 已列对策）。

## 三、代码评测

### 3.1 架构

数据流契约清晰、单向：

```
corpus.scan() ──→ out/mapping.csv ──→ run_import（消费）
run_setup    ──→ out/setup_state.json ──→ run_import + run_qc（消费）
vision_extract ──→ out/vision/*(approved.flag 门) ──→ 导入 ds2 的修正产物
```

- 幂等性：run_setup 按 name 查重建库，重复执行安全。
- 断点续命：ImportStateMachine 每 步骤持久化 `out/import_state.json`，中断后重跑跳过 done 条目。
- 可测性：HTTP transport / RAGFlowClient 全部可注入 mock，单测零网络依赖。

### 3.2 已修复的问题（最终评审 → 修复提交）

| # | 级别 | 问题 | 修复 |
|---|---|---|---|
| 1 | Critical | run_import 从 `.email/.password` 文件读凭据，违反环境变量约束 | 改用 `config.RAGFLOW_EMAIL/PASSWORD` |
| 2 | Critical | run_import 构造了不存在的 PEM 路径 | 直接使用 `config.PUBLIC_PEM` |
| 3 | Critical | vision_extract 用 SHA1(email:password) 当 Bearer token，必然 401 | 改读 `LLM_API_KEY`/`LLM_API_ENDPOINT` 环境变量 |
| 4 | Critical | 测试 mock 产生的 `setup_state.json`（id="new_id"）被提交入库 | `.gitignore out/` |
| 5 | Important | 词表 13 行含重复 filler 行，会扭曲 topn_tags 权重 | 删为 12 行（5+7），测试同步更新 |
| 6 | Important | import_state.json 损坏时静默重导 | 加 `[WARN] corrupt` 提示 |
| 7 | Important | 未知 dataset_key 使排序抛 ValueError | 兜底排到队尾 |
| 8 | Critical（移植时发现） | 测试 mock 运行会把 id="new_id" 的 setup_state.json 写入真实 out/，若被 run_import/run_qc 消费则全部检索打到假 dataset id（评审 #4 的活实例） | test_run_setup/test_run_qc 以 tmp_path 隔离 OUT_DIR；已删除污染文件 |
| 9 | Important（移植时发现） | run_import dry-run 在 setup_state.json/mapping.csv 缺失时直接抛 FileNotFoundError；且对应测试依赖污染文件才通过（顺序脆弱） | dry-run 缺前置产物改为友好提示返回，--apply 保持硬校验；新增 `_out_dir` 注入参数与自建 fixture 测试 |

### 3.3 遗留问题（Minor，均不阻塞运行，建议后续清理）

1. **corpus.py 孤儿 OCR 推导不确定**：29 个无前缀 OCR 文件靠文件名关键词推断归属，默认兜底 ds2；无告警输出。建议对每个 fallback 打 `[WARN] orphan-ocr` 行，并在 mapping.csv 审查门重点核对。
2. **异常捕获过窄**：run_setup/run_qc 只捕 `ConnectionError`，401/500（`requests.HTTPError`）走裸 traceback。建议放宽到 `requests.RequestException` 并保留友好提示。
3. **死代码**：`strip_dup_suffix` 未被调用；corpus.classify 内 `top`/`filename_seg0` 未使用；注释 "Rule 4" 编号错位。
4. **get_dataset 端点存疑**：`GET /datasets/{id}` 在 v0.27.0 REST surface 中未见文档化，当前无调用方，建议删除或实测后保留。
5. **wait_parse 返回 docs[0]**：语义与 wait_document 不一致，返回 `docs` 更合理。
6. **config 占位符默认值**：`RAGFLOW_EMAIL=placeholder@example.com` 会把配置错误推迟到登录 400 才暴露，建议缺失即 fail-fast。
7. **OUT_DIR 在 import 时创建目录**：副作用式初始化不够干净，建议移入各入口函数。
8. **测试缺口**：状态机 save/load round-trip 无用例（断点续命核心路径）；test_run_qc fixture 对 pass/fail 语义断言不足；test_run_setup 曾硬编码绝对路径（移植时已改为相对定位）。
9. **QC probe 形同仪式**：无效 filter 探测不能验证 Q2/Q5 真实 shape，价值有限。

### 3.4 测试与验证记录

```
tests/: 46 passed, 1 skipped (setup_state.json 未生成前按设计跳过), 0 failed
out/ 副作用检查：套件运行后 out/ 保持为空（测试完全隔离）
```

移植后在新位置 `/opt/wangjz/ragflow-import/src` 连续两轮全绿；移植改动共四处：
`config.OUT_DIR` 改为随代码位置自适应、test_run_setup 绝对路径改相对定位、
run_import 增加 `_out_dir` 注入与 dry-run 缺前置产物的友好提示、
三个测试文件改为临时目录隔离（不再向真实 out/ 写任何产物）。

## 四、运行就绪度判定

| 阶段 | 就绪度 | 前置条件 |
|---|---|---|
| 阶段0 扫描+映射 | ✅ 即刻可跑 | 无（只读语料） |
| 阶段1 表格/VLM 预处理 | ✅ 可跑 | VLM 步骤需 `LLM_API_KEY`；产出须过 `--approve` 人工门 |
| 阶段2 建库 | ✅ 可跑 | RAGFlow 实例在线 + 租户已配 embedding 模型 + 登录凭据 |
| 阶段3 批量导入 | ⚠️ 有条件 | 必须先跑 `--dataset ds3 --limit 5 --apply` pilot 实测 GraphRAG 成本 |
| 阶段4 验收 | ✅ 可跑 | 依赖阶段2/3 完成 |

## 五、建议的后续动作（优先级序）

1. 跑阶段0，人工审查 mapping.csv（重点：flood_event 列、29 个孤儿 OCR 行、skip_reason）
2. `export LLM_API_KEY=...` 后跑 vision_extract dry-run → 人工复核 → `--approve`
3. run_setup dry-run + apply；确认 Web UI 中 6 库与 schema 正确
4. ds3 pilot 5 文件实测 GraphRAG 单 chunk 成本与耗时，估算全量窗口
5. Minor 清理批次：孤儿 OCR 告警、异常捕获放宽、死代码删除、状态机 round-trip 测试
