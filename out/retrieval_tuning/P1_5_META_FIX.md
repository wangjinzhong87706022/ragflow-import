# P1-5：2018 元数据纠偏 + meta_data_filter 双层修复（2026-09-03）

## 一、2018 元数据纠偏（档案错误①的 KB 侧落地）

档案目录 `06-2008年洪水(8-22)/` 实为 2018-08-21 洪水（四条证据链见
`docs/archive-feedback-2026-09-03.md` 错误①）。改动五处：

| 位置 | 改动 |
|---|---|
| `src/config.py` | `METADATA_SCHEMA.flood_event` 枚举加 `2018-8`；`FLOOD_EVENT_BY_SUBDIR["06-2008年洪水(8-22)"] = "2018-8"`（含证据链注释） |
| `src/tag_vocab.py` | VOCAB_ROWS 13→14 行（`2018-8` 插入 2019-9 与 2013-7 之间；`2008-8` 保留为历史值域） |
| `src/tests/` | 新增 `test_flood_event_2008_dir_is_actually_2018` 回归 pin；test_tag_vocab 计数 14/9 |
| ds0 标签库 | `taoqupo_vocab.txt` 插入 `2018-8` 行 → 14 chunks（新挂块 `ab396be80a86d4cd`） |
| ds3 五文档 patch | 洪水统计.xls（壳 ec08f730…）/ 降雨量统计.xls / 弃水计算.xls / 洪水汇报.doc / 洪水简报.doc → `flood_event=2018-8, year=2018, sub_category=2018年洪水(8-21)`，PATCH document 同步 doc_meta 索引已验证（term 查 5/5，2008-8 残留 0） |

## 二、meta_data_filter 静默失效：两层根因，两层修复

**现象**：`meta_data_filter={flood_event=2018-8}` 与 `{flood_event=2008-8}` 返回完全相同的
30 条无过滤结果（14 个无关文档）——过滤器被静默忽略。QC Q5 的"元数据钻取"自始是假阳性
（无过滤自然命中）。

### 第一层：REST 契约键名错误（client 侧）

v0.27.1 `apply_meta_data_filter`（common/metadata_utils.py）manual 分支读
`meta_data_filter.get("manual", [])`——**条件列表必须在 `manual` 键下**。我们（run_qc Q5
与各探针）写的 `conditions` 键被静默忽略：`filters=[]` → 不过滤也无 `"-999"` 占位。

**修复**：`ragflow_client.py::search_datasets` 出口统一经 `_normalize_meta_data_filter()`
归一（`conditions` → `manual`，`method` setdefault manual，已写 `manual` 的原样放行）。
测试 `test_meta_data_filter_conditions_normalized_to_manual` 锁定。

### 第二层：doc_meta 索引 mapping 与 v0.27.1 查询代码不匹配（数据面）

契约修正后 probe 反而全 0（2008-8 正确归零，2018-8 也归零）。源码追查：
pushdown `_term_or_match`（common/metadata_es_filter.py）对字符串值**硬编码查
`meta_fields.<key>.keyword` 子字段**；而我们的 doc_meta 索引是 v0.27.0 时代建的，
`meta_fields.*` 为裸 `keyword`、**无 `.keyword` 多字段**（v0.27.1 新装机 mapping
`conf/doc_meta_es_mapping.json` 为 `dynamic: runtime`，新装机会有 `.keyword`）→ term 落在
不存在字段 → 恒 0 → pushdown 返回空列表（被当确定性结果）→ 整个过滤塌成 0。

**修复（数据面维护，与此前 tag_feas `_update_by_query` 同类）**：
1. 存档 before mapping → `out/retrieval_tuning/p15_doc_meta_mapping_before.json`
2. PUT `_mapping` 给 11 个 schema 字段补 `.keyword` 多字段（仅 schema 字段，垃圾键不碰）
3. POST `_update_by_query?conflicts=proceed&refresh=true` 回填 → 82/82 updated, 0 failures

## 三、验证矩阵

| 验证 | 结果 |
|---|---|
| ES pushdown 复刻查询 | 2018-8 → 5（恰为纠偏文档族）；2008-8 → 0；2013-7 → 5 |
| 端到端 API probe | 2018-8 → total=44、返回 30 条、文档 ∈ {洪水统计(1).xls, 弃水计算.xls, 洪水汇报(2).doc}；2008-8 → total=0 |
| QC 六问 | **6/6**，且 Q5 chunks 64→**18**（过滤收窄实证，真钻取） |
| xls 12 题 | **12/12** 持平 |
| 26 题检索 | **26/26 满贯**；问答 25/26 与基线持平（唯一 FAIL 由 G3 挪到 B4，见下注） |
| pytest | **197 passed, 1 skipped**（新增 2 测试：纠偏 pin + 归一化） |

> **B4 注**：问答 FAIL 为判分词形假阴性——答案内容全对（"百年一遇设计洪水泄量为1454
> 立方米/秒"），判分词形只认 m³/s 族（report_20260903_193629.md）。检索层该题 3/3 满贯
> 目标在召回 ✓。判分词形修正归 P2-10 评测 harness 固化，本轮不动口径。

## 四、新发现（超出评审清单，已记档案反馈）

**错误③升级**：`05-2013年洪水(7-22)/洪水过程.xls`（KB 名 洪水过程(3).xls，id
`65807384a1f6…`）不是"sheet 标题笔误"而是**整文件错放**——三 sheet 标题均指
20110729/"7.29" 洪水 + 日期序列 40752=2011-07-28 20:00 起录 + 行内"29日"互证，实为
**2011-07-29 洪水**。同目录降雨量统计.xls 内标题 20130722 ✓，目录主体无误，单文件错放。
连带：该文件 KB 元数据 `flood_event=2013-7` 失真（正确值需扩 2011 场次枚举，涉及 Q5
过滤语义）——**未改值，留待用户与档案方核实后裁定**。同目录 `"7.29"洪水简讯doc.doc`
年份存疑（.doc 未离线解析）。

**解析器机制印证（外部分析采纳）**：`.xls` 走 openpyxl 失败→pandas 降级（header=0 +
`——Data` 指纹 + nan 字面量 + 日期退化序列号）是 F1 垃圾块的生成机制，与我们的 F1 审计
现象层结论互补闭环；其"根治四条"（header=None/滤 nan/日期换算/round）即
`extract_xls.py` 已实现清单。上游代码修改被零上游修改约束排除，根治保持在管线外。

## 五、后续裁定项（不入本任务）

1. 洪水过程(3).xls 的 flood_event 纠偏目标值（2011-7 不在枚举；或归 `其他`）——需用户裁定。
2. F1 收口（#55）triage 时，洪水过程(1)(2)(3).xls / 2001年洪水过程线.xls 按错误②③联动处理。
3. `year` 字段 pushdown 注记：本索引 `year` 为 keyword 型，数值过滤走父路径 term(int) 可能
   错配——stock 新装机动态映射同样首值定型，属 v0.27.1 上游特性，本轮不处理。
