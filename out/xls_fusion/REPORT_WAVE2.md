# F1 收口 · 批次一（D1+D5）apply 报告 — S 级 5 份壳迁移 + dup 并入

- 日期：2026-09-04；评审门：approved2.flag（用户审阅 fusion/ 5 文本 + REVIEW_WAVE2.md 后放行）
- 执行：`rollout_xls_shell2.py --apply`（首跑验证门 FAIL 停机）→ `driver_wave2.py`
  （逐文件 process → ES tag_feas 回填 → 引擎同款 verify 重验，见下"回填时序"）
- 结果：**5/5 过验证门**（重验 rank：3/1/1/1/4）；**29/29**（X1–X29 ×3 多数）；
  26 题 **26/26**（检索+问答，report_20260904_120516.md）；QC 六问 **6/6**；
  pytest **204 passed, 1 skipped**。

## 一、迁移台账（manifest_xls_shell2.json 同步）

| KB 名 | 旧块(naive) | 新块(fusion) | 探针锚点 | 首验 | 重验 |
|---|---|---|---|---|---|
| 降雨量统计.xls | 6 | 8 | 258 | FAIL(rank=None) | OK rank=3 |
| 洪水统计.xls | 14 | 9 | 1065 | OK rank=20 | OK rank=1 |
| 洪水过程(2).xls | 15 | 8 | 536.92 | OK rank=8 | OK rank=1 |
| 洪水过程(1).xls | 16 | 9 | 2567.22 | OK rank=14 | OK rank=1 |
| 洪水过程.xls | 28 | 10 | 679 | OK rank=4 | OK rank=4 |
| 合计 | 79 | 44 | | | |

元数据：11 schema 字段按 2026-09-04 活库只读核验值；唯一纠偏 洪水统计.xls
flood_event 2019-7→2019-9（已按清单同步修订存档快照 + meta_amended 留痕，
`--rollback` 不倒退）。doc_meta 随 patch_document 自动同步（P1-5 实证）。
dup 独有 sheet（入库洪水 68 行/桃库溢洪道溢流 28 行/10.3 预报值/灌区降雨子表）
并入 洪水过程.xls.md，dup 本身不入库（D5 裁定）。

## 二、首跑 FAIL 与回填时序（本轮核心教训）

首跑（`rollout_xls_shell2.py --apply`）第 1 份即 FAIL(验证门) rank=None：壳块 UNSTART
免 parse → 无 tag_feas（P2-9 根因复现；wave-1 门过只因当时垃圾池尚浅），被汇报 .doc
高密度块（sim 9.3/8.3）+ 姊妹 naive 垃圾块挤出 top30。

**回填必须在挂块之后做**——重跑 process 会重挂新块（无 tag_feas，回填即丢），
故 run_wave 一次跑完不可行，改为 driver_wave2.py 逐文件 process→回填→重验。
词表照抄 P2-9 场次标准 `{洪水资料:10, 基础数据:8}`，ES 索引 ragflow_72fc3f62…（ds3），
scope=docnm_kwd。ES 注意：`refresh` 必须作 URL 参数（`?refresh=true`），
放 body 报 parsing_exception。

## 三、X10 回归与 P2-9b 词表碰撞修正

apply 后 29 题首跑 28/29：X10（2011年下泄水量统计.xls，wave-1 资产）目标掉 top10。
根因 = **跨波 tag_feas 词表模长碰撞**（详见 `out/retrieval_tuning/P2_9B_WAVE2_COLLISION.md`）：

- v0.27.1 打分实测 `sim = 0.7×term + 0.3×vec + tag_fea + pagerank`（逐块拟合 <0.001 误差）；
  tag_fea = cos(查询标签, 块词表)×10。
- 查询标签 {洪水资料:1} 时：wave-2 两维词表 tag_fea=7.806，2011 doc 三维（+历年统计:5）
  7.274，固定 −0.53；X10 问句与 5 场洪水 QA 句式同构，被纯模长套利挤出。
- 无效尝试：P1-4 questions 补丁（`similarity()` 是查询侧覆盖率，content 已含问句原词时
  增益仅 +0.07 < 0.53；留档 p14_x10_patch_before/after.json）。
- 修正：2011 doc 7 块词表对齐两维（语义成立：弃水/溢流类查询实测无标签或仅 洪水资料；
  吃 `历年统计` 标签的"历年最大洪水"正主是较大洪水统计表.xls，词表不动）→ 29/29。

## 四、apply 后必做清单核销（REVIEW_WAVE2.md 第四节）

1. ✅ tag_feas 回填——driver_wave2.py 逐文件回填（5 壳 44 块）；
2. ✅ 存档快照 R1 式修订——洪水统计.xls 快照 flood_event→2019-9 + meta_amended 留痕；
3. ✅ 29 题 after 评测——29/29（report_xls_after_20260904_114437.md）；
4. ✅ 全量回归——QC 六问 6/6（report_20260904_120547.md）+ 26 题 26/26
   （report_20260904_120516.md）+ pytest 204 passed。

## 五、回滚

`python3 rollout_xls_shell2.py --rollback "KB名"` 打印预案 / 加 `--apply` 执行
（删壳→重传语料→恢复存档 11 字段元数据（已含 2019-9 纠偏）→naive 重解析兜底）。
ES 词表回滚见 P2_9B 文档第四节。

## 六、下一步（后续批次，各带评审门）

批次二（D2 2010 三份 + D7 TB0207 年份纠偏 1997）、批次三（D3 防洪减灾统计表、
D4 洪水过程(3).xls 实为 2011-7-29 + 扩枚举）、批次四（D6 水位库容曲线推求 局部 fuse）。
批次二离线段启动时按 P2_9B 教训先复核三份 2010 系旧壳词表（+历年统计:3/6 维）。
