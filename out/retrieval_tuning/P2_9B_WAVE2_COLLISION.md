# P2-9b wave-2 词表碰撞修正（X10 回归）

- **日期**：2026-09-04（F1 批次一 apply 后回归发现）
- **现象**：29 题评测 X10（2011年下泄水量统计.xls，锚点 904）wave-1 后时代 PASS → 批次一 5 壳
  apply 后 FAIL（目标 rank 22，top10 外）。X13–X29（新 17 题）与 5 壳探针全过。
- **修正**：29/29（report_xls_after_20260904_114437.md）；pytest 204 passed 零回归。

## 一、根因（打分公式实测解构）

v0.27.1 ES 路径（search.py `rerank_with_knn`）：

```
similarity = 0.7×term_sim + 0.3×vec_sim + tag_fea + pagerank
tag_fea    = cos(查询标签, 块 tag_feas) × 10        # _tag_feature_scores
```

（该公式由返回的 similarity/term_similarity/vector_similarity 三元组逐块验证拟合，误差 < 0.001。）

批次一 5 壳回填统一词表 `{洪水资料:10, 基础数据:8}`（P2-9 场次标准），而 wave-1 的
2011年下泄水量统计.xls 沿用 3 维 `{洪水资料:10, 基础数据:8, 历年统计:5}`。查询标签
`{洪水资料:1}` 时 tag_fea = 10/‖词表‖×10：

| 词表 | ‖·‖ | tag_fea |
|---|---|---|
| 2 维（wave-2 五壳） | 12.806 | **7.806** |
| 3 维（2011 doc） | 13.748 | **7.274** |

固定 −0.53 劣势。X10 问句与 5 场洪水 QA 句式同构（柳林断面/洪峰流量/5日洪量），5 壳
20+ 块 term 咬合，把 3 维词表的 2011 doc QA 块（term 0.3697 全场最高、vec 0.9637 最高）
仍挤出 top10——纯词表模长套利，非内容劣。

## 二、处置（外科，单文档）

1. **无效尝试（留档）**：P1-4 questions 杠杆（`patch_chunk(questions=[…])`，见
   `out/xls_fusion/p14_x10_patch_before/after.json`）——term 提到全场最高仍不够：
   `similarity()` 是查询侧覆盖率（doc 侧权重被注释），content 已含问句原词时
   questions 只补 柳林/断面/各 三词，base 增益 +0.07 < 词表缺口 0.53。
2. **有效修正**：2011年下泄水量统计.xls 7 块 tag_feas 对齐 2 维
   `{洪水资料:10, 基础数据:8}`（ES `_update_by_query?refresh=true`，scope=docnm_kwd）。
   语义成立：弃水量/溢流/场次类查询实测标签为 `{}` 或 `{洪水资料:1}`（历年统计 维
   对本文档零命中纯拖累）；真正吃 `历年统计` 标签的"历年最大洪水"类查询正主是
   较大洪水统计表.xls（词表不动）。X11/X12（无标签查询）不受词表影响，实测仍 PASS。

## 三、词表全景（2026-09-04 ES 实测）

| 文档 | tag_feas | 来源 |
|---|---|---|
| 批次一 5 壳 | {洪水资料:10, 基础数据:8} | 本次回填（driver_wave2.py） |
| 洪水统计(1).xls | {洪水资料:10, 基础数据:8} | P2-9 |
| 2011年下泄水量统计.xls | {洪水资料:10, 基础数据:8} | **本次修正**（原 +历年统计:5） |
| 较大洪水统计表.xls | {洪水资料:10, 基础数据:8, 历年统计:5} | P2-9（历年目录正主，保留） |
| 2010年下泄水量统计.xls | {洪水资料:10, 基础数据:8, 历年统计:6} | P2-9（批次二迁移时复核） |
| 724-829两场洪水.xls | {洪水资料:10, 基础数据:8, 历年统计:3} | P2-9（批次二迁移时复核） |
| 2001年洪水过程线.xls | {工程资料:5, 洪水资料:10, 基础数据:8} | P2-9（批次二迁移时复核） |
| 洪水过程(3).xls | {基础数据:9, 历年统计:4, 其他:1} | P2-9 异类（批次三迁移时复核） |

**批次二/三教训**：迁移壳回填时先查旧壳词表——若查询标签维度与旧词表相交，须整体
复核碰撞（本次 −0.53 模长差的教训）；同族文档词表统一，避免跨波套利。

## 四、回滚

```bash
curl -u elastic:infini_rag_flow "localhost:1200/ragflow_72fc3f62a01f11f19d7235ad4ea699d4/_update_by_query?refresh=true" \
  -H 'Content-Type: application/json' -d '{
  "query": {"bool": {"filter": [{"term": {"docnm_kwd": "2011年下泄水量统计.xls"}}]}},
  "script": {"source": "ctx._source.tag_feas = params.m",
             "params": {"m": {"洪水资料": 10, "基础数据": 8, "历年统计": 5}}}}'
```

（注意 refresh 必须作 URL 参数；放 body 会报 parsing_exception。）
