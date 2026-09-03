# Tasks 1–3 报告 — 判分用纯函数（run_qc.py 外置题库评测器 第一批）

日期：2026-08-27
需求来源：`.superpowers/sdd/2026-08-27-eval-suite-phase1/briefs/tasks-1-3-brief.md`（唯一需求来源）
工作目录：`/opt/wangjz/ragflow-import/src`

## 状态

DONE_WITH_CONCERNS（功能全部落地、全绿；但简报自带 2 处测试侧缺陷，需控制方确认）

## 改动文件

| 文件 | 变更 |
|---|---|
| `src/run_qc.py` | 追加式修改。顶部 `import json` 之后新增 `import posixpath`；在 `from ragflow_client import RAGFlowClient` 与旧 `QUESTIONS` 区之间插入 11 个纯函数（Task1: normalize_for_match / numeric_hits / keyword_hits / anchor_hit_at_k / decide_pass；Task2: score_retrieval / mrr_mean；Task3: rel_to_name / score_meta_filter / tag_signal / kg_gain_record）。322 行 → 434 行，全部为插入，无删除 |
| `src/tests/test_eval_scoring.py` | 新建，16 例测试，按 TDD 三轮追加 |

旧代码保全性核查：AST 解析顶层定义 = 新增 11 函数 + 原有 6 函数
（load_setup_state / keywords_found / chunk_texts_from_response / write_json / write_markdown / run_qc），
顶层常量仅 `QUESTIONS`（6 条）不变，`if __name__ == "__main__"` 尾块不变。
模块导入仍零副作用、零网络（只有 import + 常量赋值），新测试全程不登录不发 HTTP。

## 各 Task 测试命令与结果数字

基线（动手前）：`cd src && python3 -m pytest -q` → **132 passed, 1 skipped, 2 deselected**

### Task 1 匹配归一化与 pass 公式
- Step2 红：`python3 -m pytest tests/test_eval_scoring.py -q`
  → ImportError: cannot import name 'normalize_for_match' from 'run_qc'（符合预期文案）
- Step4 绿：同命令 → **6 passed**
- Step5 回归检查点：`python3 -m pytest -q` → **138 passed, 1 skipped**（132+6）

### Task 2 retrieval 层打分
- Step2 红：`python3 -m pytest tests/test_eval_scoring.py -q`
  → ImportError: cannot import name 'score_retrieval'（收集期错误，按简报预期掩盖 Task1 用例单独展示；
    Task1 的 6 例已在上一检查点独立证明绿）
- Step4 绿：同命令 → **11 passed**

### Task 3 structured 层打分
- Step2 红：同命令 → ImportError: cannot import name 'rel_to_name'
- Step4 绿：同命令 → **16 passed**

### 终态全量回归
- `cd src && python3 -m pytest -q` → **148 passed, 1 skipped, 2 deselected in 13.54s**（132 + 16，live 冒烟照常默认关闭）

TDD 全程红→绿逐字推进，实现代码与简报逐字一致，未做任何改写。

## 自审发现（重要：简报自身的 2 处矛盾，均在「测试侧」修正，实现保持逐字原样）

1. **Task 1 断言方向写反**：
   `assert "," not in normalize_for_match("雨情,水情")` 与其同行注释「中文/普通逗号不动」、
   测试名 `..._removed_only_between_digits` 以及实现的文档契约（只删千分位）三者互相矛盾——
   该输入中的 `,` 是 ASCII U+002C 且前字符非数字，正确行为是保留，故输出必含 `,`，
   该断言恒假。已改为 `"," in ...`（一行修正，保留原注释并补充说明）。实测验证：源字节确为 U+002C。

2. **Task 2 fixture 缺关键词**：
   `test_score_retrieval_full_fields` 期望 `kw_hits == 2`，但 fixture 文本 `"溢洪道设计泄量为1454"`
   不含第二个金标关键词 `百年一遇`，任何满足 Task1 关键词包含语义的实现最多得 1——实现端不可能
   通过此用例而不破坏其它用例。已将 fixture 改为 `"溢洪道百年一遇设计泄量1454"`（仅改这一行数据，
   其余断言含 mrr_rank==2 / numeric_exact / passed 全部原样成立）。

未采信的另一修复方向（供评审参考）：若把断言改成 `kw_hits == 1` 会弱化「计数多个不同关键词」的测试意图；
若让 normalizer 吞掉所有逗号以满足第 1 处，则违反函数文档契约并有破坏正文文本的风险，均被排除。

## 其他自审备注（无需改动，仅备案）

- `normalize_for_match` 按简报注释「宽泛去除无害」删去任意紧跟数字的逗号（如 "2021,10" → "202110"）。
  后续构造真实题库 gold.numbers 时应统一千分位形态以利用该归一，避免同类形态分裂导致漏命中。
- `score_retrieval.kw_needed` 默认取 `gold["threshold"]["min_keywords"]`，缺省 1 —— 题库 loader（后续任务）
  落地时需保证该字段约定，否则无 threshold 的 case 一律按 1 计。
- Task1 测试文件里模块级 `from run_qc import score_retrieval, ...`（Task2 追加段）会使后续红灯以
  collection error 形式出现而非逐例 FAIL，属简报既定结构，行为已如实记录。
- 本目录非 git 仓库，无 commit 步骤；收尾检查点即上述 pytest 结果。
