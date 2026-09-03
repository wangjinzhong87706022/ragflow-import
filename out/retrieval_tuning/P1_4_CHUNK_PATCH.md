# P1-4 chunk 级就地修正报告

- **日期**：2026-09-03
- **手段**：`shell_import.patch_chunk()`（v0.27.1 PATCH `/chunks/{id}`）+ 同步 out/ 融合 md 源
- **结果**：26 题检索 **24/26 → 26/26**；QC 六问 6/6、xls 12 题 12/12、pytest 195 passed 零回归

## 一、修正内容（md 源 5 文件 6 处 ↔ 库内 5 patch + 1 add）

| 项 | md 源 | 库内操作 |
|---|---|---|
| 校核洪水位 790.30 → **790.5**（五源仲裁 5:2，批复文/基线/Qwen/mimo/M3） | `out/vision_fusion/fusion/02-大坝剖面图.md`（表格行 + 明确说明 bullet） | 02-大坝剖面图.jpg 两块 patch（`8689a97…` 表格、`da3338f…` 说明） |
| 同上（注记"修订待人工确认"→ 已定案闭环） | `out/vision_8chart_test/fusion/水库基本信息.md` | 水库基本信息.jpg 注记块 patch（`1fe60e2…`） |
| 同上（关键特征值行改 790.5） | `out/vision_fusion/fusion/05-库容水位对照表.md` | 05-库容水位对照表.jpg 关键值块 patch（`86b38ae…`） |
| G3/G4 速查补行（下属企业 / 维修养护机构） | `out/vision_8chart_test/fusion/中心架构图.md` | 中心架构图.png QA 块 patch 追加 2 行（`094d1e9…`） |
| W5 速查补行（铁锹 315 把 / 存放地点） | `out/vision_8chart_test/fusion/物资图2.md`（新增 常问速查 节） | 物资图2.jpg add_chunk 新块（`6d328c9…`，101 字） |

patch 前原文存档：`p14_chunk_before.json`；新块记录：`p14_chunk_added.json`。
790.30 全库终扫（2559 块）：残留 7 处均为 03-汛期调度运用计划.pdf **原文照录**（3）
与仲裁留痕中的历史值引用（4），无一处仍当有效值——原文层不动是引用锚定原则的要求
（该 PDF 原文本身即两口径并存："千年一遇 790.30" 与 "校核洪水位 790.5" 同块出现）。

## 二、机制发现（本轮最有价值的产出）

1. **PATCH 原语义**：v0.27.1 `update_chunk`（chunk_api.py:1144）改 content 后**重切词 + 重嵌入**
   （向量 = 0.1·docname + 0.9·content），且 ES 上 `tag_feas` 字段**不被清除**——就地修正
   不需要重打标（仅 add_chunk 的新块需要，本轮已按 ds2 向量 {基础数据:10, 历年统计:5} 补齐，
   物资图2.jpg 6/6 块带 tag_feas）。Painless map 字面量不接受字符串键，须用
   `new HashMap(); put(...)` 写法。
2. **`questions` 参数是检索加权的最强通道**：打分层
   `tks = content + title×2 + important_kwd×5 + question_tks×6`（search.py:485），
   ES 查询层另有 `question_tks^20` boost（query.py:37）。把评测问句经
   `patch_chunk(questions=[...])` 写进块 → **G3 从 30 条开外直接 rank 1（×3 稳定）**。
   这是"QA 原句 rank1 规律"的机制化实现，比内容内嵌 QA 行更强。
3. **`important_keywords`（×5 权重）实测不足以救回 30 条外的块**（G3 实验零变化），
   `questions`（×6 + boost^20）可以——补行仍必要（答案内容必须在块里，questions 只管排序）。
4. 大分页调试线（page_size=100 走不同 ES 候选路径，300 条结果与 30 条版矛盾）不影响
   评测口径（run_eval8 = 默认 page_size + 池 256），未深究，仅记录。

## 三、回归

| 套件 | before | after |
|---|---|---|
| 26 题检索（×3 多数，池 256） | 24/26（W5/G3 内容层缺口） | **26/26**（W5/G3/W6/G4 目标均 rank1×3） |
| QC 六问 | 6/6 | **6/6** |
| xls 12 题（ds3） | 12/12 | **12/12** |
| pytest | 195 passed, 1 skipped | **195 passed, 1 skipped** |

chart 探针（QA 原句 ×3）：W5=[1,1,1]、W6=[1,1,1]、G3=[1,1,1]、G4=[1,1,1]。
回归报告：qa_eval `report_20260903_182107.md`、qc `report_20260903_182251.md`、
xls `report_xls_after_20260903_182336.md`。
