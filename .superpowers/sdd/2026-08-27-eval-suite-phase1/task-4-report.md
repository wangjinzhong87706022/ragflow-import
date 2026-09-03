# Task 4 报告：cases.jsonl loader 与 schema 校验

日期：2026-08-27
状态：DONE
简报：`.superpowers/sdd/2026-08-27-eval-suite-phase1/briefs/task-4-brief.md`

## 改动文件清单

| 文件 | 改动 |
| --- | --- |
| `/opt/wangjz/ragflow-import/src/run_qc.py` | 在 `kg_gain_record` 之后、旧版 `QUESTIONS` 区段之前追加（纯新增 62 行，未修改/删除任何既有内容）：常量 `CASE_REQUIRED_FIELDS` / `CASE_TIERS` / `CASE_LAYERS`、异常类 `EvalCaseError(ValueError)`、函数 `load_cases(path, valid_rel_set=None)` |
| `/opt/wangjz/ragflow-import/src/tests/test_eval_loader.py` | 新建（逐字采用简报 Step 1 代码） |

未改动：`tests/test_run_qc.py` 及其他任何既有测试；`out/` 下运行产物；无 git 提交（目录非仓库）。

## TDD 红→绿记录

### Step 2 — 红（失败方式符合预期：ImportError: EvalCaseError）

```
ERROR collecting tests/test_eval_loader.py ____________________
tests/test_eval_loader.py:6: in <module>
    from run_qc import load_cases, EvalCaseError, CASE_REQUIRED_FIELDS
E   ImportError: cannot import name 'load_cases' from 'run_qc' (/opt/wangjz/ragflow-import/src/run_qc.py)
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.99s
```

### Step 4 — 绿

```
........................                                                  [100%]
10 passed in 0.86s
```

（实际渲染为 `..........  [100%]` / `10 passed in 0.86s`。）

注：新文件共 10 个用例——8 个测试函数 + 1 个参数化展开为 2，合计 10。

### Step 5 — 全量回归

```
158 passed, 1 skipped, 2 deselected in 14.98s
```

基线 148 passed + 新增 10 = 158，与简报预期「基线相应增加」一致；1 skipped（live smoke）与 2 deselected 不变。

## 实现落点核对

```
135:def kg_gain_record(hit5_on:int, hit5_off:int) -> int   # 既有纯函数区段末尾
139:CASE_REQUIRED_FIELDS = ...
144:class EvalCaseError(ValueError)
148:def load_cases(...)
202:# Questions (spec §3.5)
204:QUESTIONS = [
```

AST 复核：模块级函数/类名清单无重复定义；既有 11 个评测纯函数（normalize_for_match … kg_gain_record）与其后 load_setup_state/write_json/run_qc 等全部原样保留；文件行数 434 → 496（+62，恰为插入块）。头部既有 import 名称核对无误：`import json`、`from pathlib import Path` 均已存在，实现无需新增 import（也未用到 `posixpath`/`sys`/`time`）。

## 离线性说明

测试全程不登录、不发 HTTP：仅向 pytest 提供的 `tmp_path` 写入 `cases.jsonl` 后调用 `load_cases`；未触 OUT_DIR，因此不需要 `_isolate_out` 方案（简报内亦已自带 tmp_path 用例，与约束一致）。

## 控制器补充裁定的处理

按简报要求忽略——loader 未对 `gold.numbers` 的全精度形态添加任何代码级校验，未新增字段或正则。本任务零相关改动。

## 自审备注

偏离简报之处：**一项**——简报 Step 1 代码块第 6 行写的是 `from run_qc import load_cases, EvalCaseError`，但同文件第 110 行明确注明「`CASE_REQUIRED_FIELDS` 从 run_qc 一并导入」，且测试体 `test_missing_required_field_raises` 直接引用了 `CASE_REQUIRED_FIELDS`。故测试文件的 import 行采用第 110 行的版本（`from run_qc import load_cases, EvalCaseError, CASE_REQUIRED_FIELDS`），其余测试代码逐字保留。这是简报两处文本间的显式指示冲突，按其更明确的补充说明处理，不属于自行发挥。
其余（实现代码、插入点、错误消息中文文案、默认值语义）：与简报逐字一致，无偏离。
