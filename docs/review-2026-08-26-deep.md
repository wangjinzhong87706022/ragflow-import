# 深入评审报告（第二轮，2026-08-26）

评审对象：整个项目（代码 + 测试 + 文档）与导入计划。
评审方法：全部源码与测试逐行阅读；`python3 -m pytest -q` 实跑；**所有 RAGFlow 契约结论均在运行中的容器 `docker-ragflow-cpu-1`（`infiniflow/ragflow:v0.27.0`，已确认与 `/opt/git/ragflow` 检出一致）内 grep 实证**，非纸面推断。mapping.csv 全量 73 行做了数据质量审计。

**总体结论：架构与流程设计是好的（分阶段、人工门、断点续命、dry-run 默认），但集成层存在 5 个 P0——当前代码无法与线上 v0.27.0 实例完成任何一次完整交互。在修复 P0 之前，阶段2/阶段3 必然失败。** 测试 91 通过 1 跳过（README 写 81，已过时），全绿的原因是 mock 边界划错了位置：所有测试 mock 掉了 `RAGFlowClient`，客户端所假设的服务端形状从未被验证过。

严重度：P0 = 阻断主流程；P1 = 功能失效/静默数据错误；P2 = 数据质量/文档/一致性。置信度为 n/10。

---

## 一、P0（阻断级，5 项）

### P0-1 登录加密缺少 pre-base64，登录必然失败（置信 10/10，容器实证）

- `src/ragflow_client.py:29`：`cipher.encrypt(password.encode("utf-8"))` —— 注释还特意写明 "not base64"，这是错误的决定。
- 容器内 `api/utils/crypt.py`（实测）：`password_base64 = base64.b64encode(line.encode())` → `cipher.encrypt(password_base64.encode())`。服务端 `decrypt()` 得到的是 **base64(明文)**，并以其哈希与注册时存储的哈希比对。
- 后果：客户端发送 raw 密码的密文 → 服务端解出 raw 明文 → 哈希不匹配 → 返回 **HTTP 200 + `code=109`**（见 P1-1，客户端不查 code）→ `raise_for_status()` 通过 → 实际未建立会话 → 后续第一个请求 401，报错位置远离真实病因。
- 修复：`encrypt_password` 改为 `base64.b64encode(password.encode())` 后再加密，输出再 base64（与服务端 `crypt()` 完全对偶）。

### P0-2 列表接口响应形状与分页参数全面失配（置信 10/10，容器实证）

- `src/ragflow_client.py:61` `list_datasets` 发送 `params={"offset":0,"limit":100}` 并解析 `resp.json()["data"].get("list")`。
- `src/ragflow_client.py:131,135` `list_documents` 同样发送 `offset/limit`、解析 `data.list`。
- 容器实测（v0.27.0）：
  - `GET /api/v1/datasets` 返回 `get_result(data=<list>, total=...)` —— **`data` 是数组**，`.get("list")` 直接 `AttributeError`，run_setup 第一步幂等查找即崩；
  - `GET /api/v1/datasets/{id}/documents` 返回 `get_json_result(data={"total": total, "docs": [...]})`（document_api.py:859）—— 键名是 **`docs`**，客户端读 `list` 恒得 `[]`；
  - 请求模型 `BaseListReq` 为 `extra="forbid"`，只认 **`page`/`page_size`**（validation_utils.py:1021-1029）—— `offset/limit` 会被参数校验拒绝。
- 连锁后果（全部失效）：`find_document_by_name` 恒 None → 失败重跑必重复上传；`wait_document` 找不到文档 → 每个文件在 Step 4 抛 "Document not found" → 全部标记 failed；`wait_parse`（标签库）空转 300s 后超时；`inspect_chunks` 产出空报告。
- 修复：datasets 解析改为直接接受 `data` 为 list；documents 解析 `data["docs"]`；分页统一 `page/page_size`（`chunks` 路由本来就是 `page/page_size`，`list_chunks` 是唯一写对的）。

### P0-3 `run` 状态比较值错误："3"/"4" vs 实际 "DONE"/"FAIL"（置信 9/10，容器实证）

- `src/ragflow_client.py:207,209,263,265`：`run == "3"` / `run == "4"`。
- 容器实测：REST 响应里 run 被映射为字符串名（document_api_service.py:288-307 `run_mapping = {"0":"UNSTART","1":"RUNNING","2":"CANCEL","3":"DONE","4":"FAIL"}`）。设计文档引用的是 DB 枚举值，REST 层不是它。
- 后果：`wait_document` 靠 `progress>=1` / `progress<0` 兜底才没有死循环（建议同时兼容两种表示）；`inspect_chunks.py` 的 `run != "3"` 告警会**对每个已完成文档误报"未解析"**，污染人工审查报告。

### P0-4 GraphRAG/Raptor 配置从未下发，且 naive 库创建默认全开 —— 配置全面偏离设计（置信 9/10，容器实证）

证据链：
1. `src/config.py:50-56,85-91`：`graphrag_config`/`raptor_config` 是与 `parser_config` 并列的键，**全仓库无任何代码引用**（grep 实证）；
2. `src/run_setup.py:119`：`parser_config = {**ds_def["parser_config"], "tag_kb_ids": [...]}` —— 只合并了扁平键，graphrag/raptor 从未进入 `update_dataset`；
3. `src/ragflow_client.py:69`：`create_dataset` 只发 `name+chunk_method`，**创建时也不带 parser_config**；
4. 容器实测 `api/utils/api_utils.py:374-405`：`get_parser_config("naive")` 的默认是 **`use_raptor: True` 且 `use_graphrag: True`**（laws/paper/qa 等方法默认均为 False）；
5. 容器实测 dataset 更新是**深合并**（dataset_api_service.py:342 `deep_merge(kb.parser_config, req.parser_config)`）—— Step 5 不发 graphrag/raptor 键，创建时的默认值就永远存活。

把 1–5 连起来，实际效果与设计**每个库都相反**：

| 库 | 设计意图 | 实际将发生 |
|---|---|---|
| ds1 规程与预案 (laws) | GraphRAG light+resolution + Raptor | **两者都不跑**（laws 默认 False，且从未下发） |
| ds2 基础数据 (naive) | 无图谱无 Raptor | **GraphRAG + Raptor 意外全开**（naive 默认 True） |
| ds3 洪水资料 (naive) | GraphRAG、6 类领域实体、resolution | GraphRAG 开但用**默认实体类型**（organization/person/geo/event/category）且 **resolution=False**；且实体抽取对类型做硬过滤（extractor.py:117-119），FloodEvent/Station 等领域实体会被**直接丢弃** |
| ds4 组织管理 (naive) | 无 | 同 ds2，意外全开 |
| ds5 工程资料 (paper) | Raptor | **不跑**（paper 默认 False） |

附加陷阱：`raptor_config` 里的 `max_leaf_nodes` **不是合法键**（容器内 RaptorConfig 无此键，且模型 `extra="forbid"`）——即使现在补下发也会被 400 拒绝（HTTP 200 + code=101，再被 P1-1 吞掉）。合法键为 `max_token(≥512)`/`max_cluster`/`clustering_threshold` 等。

修复：run_setup 的 Step 5 对 ds1–ds5 显式下发嵌套 `"graphrag": {...}` 与 `"raptor": {...}`（不要的库显式 `use_graphrag/use_raptor: False`，否则深合并清不掉创建默认），实体类型、`resolution: true` 按 config 原意；删除 `max_leaf_nodes`。

### P0-5 按文件名（basename）复用文档：ds3 内 11 个同名文件，7 个永远不会被导入且元数据互相覆盖（置信 8/10）

- `src/run_import.py:232-237`：`existing` 中按 `d.get("name") == file_path.name` 匹配。RAGFlow 文档名就是上传文件名，不含目录。
- mapping.csv 实测同名冲突（同库内）：`洪水过程.xls ×4`（10-3/9-25/2020-8-16/2013-7-22 四场洪水各一份）、`洪水汇报.doc ×3`、`洪水统计.xls ×2`、`降雨量统计.xls ×2` —— 共 11 个文件、7 个被遮蔽。
- 后果：第二个及之后的同名文件走"复用"分支 → **不上传** → 其 `patch_document` 元数据**覆盖**第一个文档的元数据（如 2013 年的 `洪水过程.xls` meta 被写为 2021-10 场次）→ 行被标 done。静默的数据丢失 + 元数据污染，且 `test_reupload_reuses_existing_doc_by_name` 把这个行为锁成了"预期"。
- 修复：复用键改为 `name + sha256`（mapping.csv 已有 sha256 列；上传后可经 chunk/文档大小或二次 patch 校验），或上传时携带唯一 `filename`（`upload_document` 本就支持 `filename` 参数）并在元数据里保留 `rel`。

---

## 二、P1（功能失效/静默错误，3 项）

### P1-1 客户端从不检查响应体 `code`（置信 9/10，容器实证）

容器实测：本版本 RAGFlow 所有业务失败都是 **HTTP 200 + `{"code": 非0}`**（`get_json_result` 系列默认 200；真正非 200 的只有未登录 401 与未知路由 404）。登录失败(109)、参数错误(101)、业务失败(102)、上传失败(500) 全部如此。`ragflow_client.py` 所有方法只有 `raise_for_status()` + `resp.json()["data"]` → 业务失败被当成功，导入循环继续写状态机。修复：封装一个 `_check(resp)` 统一断言 `code == 0`。

### P1-2 vision_extract 交叉校验报告恒为 MISSING / 0/N 通过（置信 8/10）

- `cross_check` 返回的 `found` 以**数值字符串**为键（"1454"/"2218"/"788.5"，tests/test_vision_extract.py:16-18 印证）；
- `vision_extract.py:217-219` 又往 `result["found"]` 里塞 `TEXTUALIZED_VALUES` 的**中文键**（"百年一遇泄量"等）=False；
- `vision_extract.py:234-235,248` 报告与 `ok_count` 只读中文键 → compare_report.md 永远全 MISSING、永远打印 `[0/N] 图片通过交叉校验`。
- 后果：这道人工门的机器信号是死的（不放大错误数据，但让校验形同虚设，且训练操作员无视报告）。修复：键统一（建议全部用 TEXTUALIZED_VALUES 的中文键，cross_check 内部映射到数值）。

### P1-3 wait_document 600s 对 GraphRAG 文档偏紧；超时→failed→重跑触发重解析（置信 6/10）

- `src/run_import.py:261`：`timeout=600`。GraphRAG light 抽取每 chunk ≥3 次 LLM 调用（gleaning），resolution 再按实体对批次调用；ds1 规程类 PDF 完全可能超过 10 分钟。
- 容器实测：对已 DONE 文档再调 parse 会**清空旧 chunk 重来**（document_api.py:1631-1637）→ 超时重跑 = 反复烧 LLM tokens 且状态永远到不了 done。
- 修复：超时区分"服务端仍在跑"与"真失败"（超时只标 `wait_timeout` 不入 failed，重跑先查 run/progress 再决定是否重新 parse）；timeout 对 GraphRAG 库放宽。

---

## 三、P2（数据质量 / 一致性 / 文档，按重要性）

1. **`"04-2019年洪水(9-14)": "2019-7"` 应为 `2019-9`**（config.py:148；事件是 9-14，同表其余条目均为"事件月"）。该错误连锁进入 METADATA_SCHEMA enum（config.py:131）与 tag_vocab 洪水事件标签，三处一致地错。enum 不拦截写入（容器实证：`update_document_metadata` 无枚举校验），但 Q5 式按 `flood_event=2019-9` 过滤将恒为空。置信 8/10（目录名与同构模式强烈支持，建议人工确认事件命名习惯）。
2. **`2021-09`（9-25 洪水，5 行）不在 enum/tag_vocab**（mapping.csv 实测 5 行）。写入不会被拒（同上），但 Web UI 枚举过滤/标签软重排都不认识它。建议 enum 与词表补 `2021-09`，或归并入 `2021-10`（需人工定夺口径——2021 年三场洪水目前拆成 09/10 两档）。
3. **parse_year 取路径首个 4 位数**：`TB0207.xls → year=207`（corpus.py:98-101，mapping.csv 实测）。建议加合理区间过滤（如 1970–2026）+ 白名单目录年份优先。
4. **DEPT_KEYWORDS 非最长优先**（config.py:189-192）："管理局"排在"桃曲坡水库管理局"/"应急管理局"前，`next()` 短词恒胜。实测 70 行里 responsible_dept 仅 1 行非空、location 0 行非空 —— 这两个字段的硬过滤价值实际为零，README 所称"location/dept 元数据推导落地"名存实亡（语料路径里本就几乎没有这些词，属语料现状而非代码缺陷，但验收预期要相应调低）。
5. **run_setup 把 11 字段业务元数据 schema 也注册到 ds0 标签库**（tests 断言 6 次 `put_metadata_config`）。无害但语义噪声。
6. **run_qc.py:253-254** 死代码（dry_run 在 197 行已 return）；docstring 宣称的"invalid filter 探测调用"不存在。
7. **文档陈旧清单**：
   - `docs/requirements.md` §3 仍写"pdf_text_analysis/ 110 个 txt 唯一可导入语料"，与 2026-08-26 语料切换直接矛盾（根 README 有覆盖声明，但权威需求文档本体未改）；
   - `src/README.md:45-48` 描述 `native_xlsx_path` 列自动上传机制——该列已废弃（test_config.py:60-64 明确断言不存在），段落失实；
   - `src/README.md` 人工门槛表"10% OCR 抽样"仍指向旧 `pdf_text_analysis/ocr_new/`，新语料下此门已无对象，应改写为对原件 PDF 的抽样或删除；
   - 根 README "81 用例"实为 92（91 过 1 跳）；设计文档的 ds5 分类、计划文档的语料路径均未随切换更新；
   - `docs/specs` 中引用的"run='3'"等契约描述需按本报告 P0-2/P0-3 修正。
8. **测试边界**：全部 P0 都有绿色测试陪伴，因为 mock 全部打在 `RAGFlowClient` 类上。建议补一个**真实冒烟门**（登录 + list_datasets 一次调用，需凭据，独立标记 `@pytest.mark.live`），在阶段2 之前跑。
9. 小项：`inspect_chunks` 的 duplicate 标记覆盖 fragment（elif）；import_state 里 failed→done 后旧 error 残留（纯观感）；`list_documents` 每文件全量拉取（O(n²) 流量，70 文件无碍）。

## 四、mapping.csv 数据质量（阶段0 人工门的输入，实测）

73 行 = 70 可导入 + 3 `_dup`（ds1=4 / ds2=1 / ds3=45 / ds4=5 / ds5=15，与 README 一致 ✓）。需人工裁定项：
- P0-5 所列 4 组 11 个同名文件（核心）；
- `year=207` 1 行；`flood_event` 空 28 行（多为 08-历年统计 与非洪水目录，合理）；`2021-09` 5 行；`2019-7` 2 行（见 P2-1）；
- `doc_type=图纸` 误标到 `05-数字孪生项目建设方案.docx`（目录名"施工图纸与设计"含"图纸"触发，corpus.py:148-153 按整条 rel 判断）；
- `sub_category`=文件名 21 行（一级目录直接子文件，无二级目录可用，可接受）；
- `9-15强降雨工作汇报.doc` → flood_event=2021-10（靠 02-2021年洪水调度 目录段，事件归属需人工确认）。

## 五、与设计契约相符的部分（容器实证 ✓）

以下设计假设经容器核实**正确**，予以确认：标签只软重排不过滤（`_tag_feature_scores` ×10，三条 rerank 路径均为加法）；`meta_data_filter` manual 形状 `{key,op,value}`、op 集含 `=`，ES 下推 + 内存兜底；search 载荷字段（dataset_ids/question/top_k/use_kg/meta_data_filter）合法；`chunks` 路由 page/page_size 与响应 `data.chunks`/`content` 键；PATCH 文档 `meta_fields` 包裹（上一轮 C1 修复正确）；parse `document_ids` 键；metadata/config PUT `{"metadata": [...], "built_in_metadata": []}`（走 PUT 而非创建时注入，恰好绕开了创建路径读取旧形状的陷阱）；上传 multipart 字段名 `file`；`auto_keywords/auto_questions` 文档级快照（KB 配置必须先于上传定稿——现有 setup→import 顺序正确）；`tag_kb_ids/topn_tags/graphrag/raptor` KB 级运行时读取（建库后注入有效）；chunk_token_num 全局上限 2048（512/256 合法）。

## 六、建议修复顺序

1. **先修 P0-1/P0-2/P0-3 + P1-1**（一个下午量级）：`encrypt_password` pre-base64；datasets/documents 响应解析与分页参数；run 双兼容（"3"/"DONE"）；统一 code==0 断言。修完立即用真实凭据跑 `run_setup.py --dry-run` 作冒烟。
2. **P0-4**：run_setup 显式下发嵌套 graphrag/raptor（注意 `max_leaf_nodes` 非法键、深合并需要显式 False 关默认）；`test_config`/`test_run_setup` 补断言 update_dataset 收到 graphrag/raptor 键。
3. **P0-5**：复用键加 sha256（或上传改名）；mapping.csv 人工门同步裁定同名文件。
4. **P1-2/P1-3、P2 清单**：VLM 报告键统一；超时语义；`2019-7`→`2019-9`（人工确认）、enum 补 `2021-09`、year 区间过滤、文档同步。
5. 之后再进入 vision → setup → ds3 pilot 流程（四道人工门保持不变）。

---
*验证方法备注：契约结论均以 `docker exec docker-ragflow-cpu-1 grep/sed` 在运行容器内定位（镜像 `infiniflow/ragflow:v0.27.0`，与 `/opt/git/ragflow` 检出在 4 个抽查点全部一致）；未做任何写操作，未触碰语料与上游源码。*
