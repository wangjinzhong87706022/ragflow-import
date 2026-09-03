### Task 9: CLI 装配 + 移除内置六问遗留代码

**Files:**
- Modify: `src/run_qc.py`（删除 `QUESTIONS`、旧 `write_markdown`、旧 `run_qc()`，接上 `main()`）
- Modify: `src/tests/test_run_qc.py`（删除与新形态冲突的用例，改写入口行为用例）

**Interfaces:**
- Consumes: 前 8 个任务的全部公开函数
- Produces:
  - `main(argv: list[str] | None = None) -> int`
    参数：`--apply`（缺省 dry-run）、`--tag TAG`（apply 必配）、
    `--suite {acceptance,regression,all}`（默认 all）、
    `--layer {retrieval,structured,e2e,all}`（默认 all）、
    `--compare SELECTOR [SELECTOR2]`（1 个参数=`last` 即比较最近两条；2 个参数=两个显式 run_id）。
  - 行为约定：
    0. **校验次序**：argv 合法性检查最先（`--compare` 形态数、`--apply` 必配 `--tag`），通过后才开始文件加载；全部失败路径以 `return <码>` 表达（缺 tag / 前置产物缺失或题库加载违例 → 2；网络连接或登录失败 → 1），**不抛 SystemExit**，仅 `__main__` 入口包一层 `sys.exit(main())`。
    1. **dry-run（缺省）**：同样先做文件侧校验（loader 含 mapping.csv rel 引用检查），然后打印将执行的 case 清单与 ds 映射；零网络、零产物写入（读取输入文件不算写入）。
    2. **`--apply` 无 `--tag`**：打印拒绝原因并返回 2（防误跑污染 runs 历史）。
    3. apply 流程：登录 → 读 setup_state → `snapshot_datasets` → `execute_suite(筛选后)` → 写 `runs/<run_id>/results.jsonl`、`report.md` → `append_run` 一行到 `out/eval/runs.jsonl`（`{run_id, tag, timestamp(iso), suite, layer, snapshot, summary}`）→ 打印汇总。
    4. compare 流程：不登录、不搜索；从 runs.jsonl 选两条，diff 输出到 stdout，并同时写入 `out/eval/compare_<runA>_vs_<runB>.md`。
    5. `layer=e2e` 的入选 case 在 execute_suite 内被跳过（Task 6 口径），dry-run 清单中也标注"（二期跳过）"。

- [ ] **Step 1: 改写测试先行（更新 `src/tests/test_run_qc.py`）**

删除以下旧用例（依赖将移除的符号）：`test_questions_have_required_fields`、`test_question_count`、`test_q1_uses_kg`、`test_meta_filter_structure`、`test_q2_filter_semantics`、`test_run_qc_produces_report`（其检查的 `run_qc()` 函数被 `main()` 取代）、`test_write_markdown_empty_results_no_crash`（`write_markdown` 移除）。

保留并改造两个用例（新 main 的失败语义是返回码、不抛 SystemExit，且 dry-run 也会先做文件侧校验）：

- `connection_error` 用例：先在 tmp_path 布置最小前置产物——`(tmp_path/"mapping.csv")` 写表头行 `"rel\n"`、`(tmp_path/"eval").mkdir()` 后写一行有效题进 `cases.jsonl`、`setup_state.json` 写 `{"ds1": {"id": "id-ds1"}}`；再让 `mock_client_cls.side_effect = requests.exceptions.ConnectionError("refused")`，调用 `rc = run_qc_module.main(["--apply", "--tag", "t"])`，断言 `rc == 1` 且输出含 "RAGFlow"。
- dry-run 零副作用用例：同上布置最小前置产物后调 `rc = run_qc_module.main([])`，断言 `rc == 0`、`mock_client_cls.assert_not_called()`、且 `(tmp_path/"eval"/"runs.jsonl")` 不存在。

新增用例：

```python
@patch("run_qc.RAGFlowClient")
def test_apply_requires_tag(mock_client_cls, tmp_path, capsys):
    with patch.object(run_qc_module, "OUT_DIR", tmp_path):
        rc = run_qc_module.main(["--apply"])
    assert rc == 2
    mock_client_cls.assert_not_called()
    assert "--tag" in capsys.readouterr().out


@patch("run_qc.RAGFlowClient")
def test_compare_mode_is_offline(mock_client_cls, tmp_path, capsys):
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "runs.jsonl").write_text(
        '{"run_id":"20260827-010101","snapshot":{"ds1":{}}}\n'
        '{"run_id":"20260827-020202","snapshot":{"ds1":{}}}\n', encoding="utf-8")
    rd = tmp_path / "eval" / "runs" / "20260827-020202"
    rd.mkdir(parents=True)
    (rd / "results.jsonl").write_text('{"case_id":"R1","passed":true}\n',
                                      encoding="utf-8")
    (tmp_path / "eval" / "runs" / "20260827-010101").mkdir()
    with patch.object(run_qc_module, "OUT_DIR", tmp_path):
        rc = run_qc_module.main(["--compare", "last"])
    assert rc == 0
    mock_client_cls.assert_not_called()
    assert "R1" in capsys.readouterr().out


def test_dry_run_lists_filtered_cases(tmp_path, capsys):
    (tmp_path / "mapping.csv").write_text("rel\n", encoding="utf-8")   # loader 校验被 patch 掉
    (tmp_path / "setup_state.json").write_text('{"ds1": {"id": "id-ds1"}}',
                                               encoding="utf-8")
    import json as _json
    row = {"id": "AC-D-01", "tier": "acceptance", "layer": "retrieval",
           "question": "演示题", "datasets": ["ds1"],
           "gold": {"numbers": ["1454"], "keywords": ["溢洪道"],
                    "answer": "", "source_rel": []},
           "threshold": {"min_keywords": 1}}
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "cases.jsonl").write_text(
        _json.dumps(row, ensure_ascii=False), encoding="utf-8")
    with patch.object(run_qc_module, "OUT_DIR", tmp_path), \
         patch.object(run_qc_module, "_valid_rel_set", return_value=set()):
        rc = run_qc_module.main([])
    assert rc == 0
    assert "AC-D-01" in capsys.readouterr().out
```

注意上面第三个用例引入了实现须提供的内部接缝 `_valid_rel_set() -> set[str]`（main 里唯一一处读 mapping.csv 的入口，便于测试替换；生产实现即 `corpus.read_mapping_csv(OUT_DIR/"mapping.csv")` 后收集 `rel` 集）。`from run_qc import main` 加到导入区；文件顶部沿用现有的 `import run_qc as run_qc_module` 别名。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src && python3 -m pytest tests/test_run_qc.py -q`
Expected: FAIL（AttributeError/TypeError：QUESTIONS 仍在而 main 未实现或接口不符）

- [ ] **Step 3: 重写 run_qc.py 尾部（装配 main 并清除遗留）**

从 `src/run_qc.py` 中删除：模块级 `QUESTIONS = [...]` 整段、`load_setup_state`（并入 main 内联读取，避免双份逻辑时保语义一致——若选择保留则确保 main 调用它且报错信息不变：找不到时提示先运行 run_setup.py）、旧 `write_markdown`、旧 `run_qc()` 及 `__main__` 块。**main() 的编排次序按行为约定 0 执行：argv/compare/tag 三项合法性检查全部前置于 `_valid_rel_set()` 与 `_load_everything()`（下面骨架里 tag 检查若位于加载之后，以此条为准上移）。** 然后追加：

```python
# ------------------------------------------------------------------
# CLI（spec §5.1）
# ------------------------------------------------------------------
def _valid_rel_set() -> set[str]:
    try:
        return {row["rel"] for row in corpus.read_mapping_csv(OUT_DIR / "mapping.csv")}
    except FileNotFoundError:
        raise EvalCaseError("缺少 out/mapping.csv —— 请先运行 python3 -m corpus")


EVAL_DIR_NAME = "eval"


def _parse_args(argv):
    ap = argparse.ArgumentParser(description="桃曲坡知识库评测执行器（缺省 dry-run）")
    ap.add_argument("--apply", action="store_true", help="实跑并记录一轮 run")
    ap.add_argument("--tag", help="本轮标记（--apply 必配，如 v0-baseline）")
    ap.add_argument("--suite", default="all",
                    choices=["acceptance", "regression", "all"])
    ap.add_argument("--layer", default="all",
                    choices=["retrieval", "structured", "e2e", "all"])
    ap.add_argument("--compare", nargs="+", metavar="SELECTOR",
                    help="last（最近两条对比）或两个 run_id")
    return ap.parse_args(argv)


def _load_everything(valid_rels: set[str]):
    cases_path = OUT_DIR / EVAL_DIR_NAME / "cases.jsonl"
    state_path = OUT_DIR / "setup_state.json"
    cases = load_cases(cases_path, valid_rels)
    with open(state_path, encoding="utf-8") as f:
        setup_state = json.load(f)
    return cases, setup_state


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.compare:
        runs_path = OUT_DIR / EVAL_DIR_NAME / "runs.jsonl"
        runs = load_runs(runs_path)
        if len(args.compare) == 1:
            sel_a = find_run(runs, "last")
            sel_b = runs[-1]["run_id"]
        elif len(args.compare) == 2:
            sel_a, sel_b = (find_run(runs, s) for s in args.compare)
        else:
            print("[ERROR] --compare 只接受 last 或两个 run_id")
            return 2
        eval_dir = OUT_DIR / EVAL_DIR_NAME
        res_a = read_run_results(eval_dir, sel_a)
        res_b = read_run_results(eval_dir, sel_b)
        snap_a = next((r.get("snapshot") for r in runs if r["run_id"] == sel_a), None)
        snap_b = next((r.get("snapshot") for r in runs if r["run_id"] == sel_b), None)
        report = compare_runs(res_a, res_b, snap_a, snap_b)
        print(report)
        cmp_path = eval_dir / f"compare_{sel_a}_vs_{sel_b}.md"
        cmp_path.write_text(report, encoding="utf-8")
        print(f"\n[INFO] 对比报告已写入 {cmp_path}")
        return 0

    # ---- 干跑前置校验（dry 也做，尽早暴露题库/引用错误）----
    valid_rels = _valid_rel_set()
    cases, setup_state = _load_everything(valid_rels)

    sel_suites = ({"acceptance", "regression"} if args.suite == "all"
                  else {args.suite})
    sel_layers = ({"retrieval", "structured", "e2e"} if args.layer == "all"
                  else {args.layer})
    chosen = [c for c in cases
              if c["tier"] in sel_suites and c["layer"] in sel_layers]

    if not args.apply:
        print(f"[dry-run] 将执行 {len(chosen)} 题（suite={args.suite}, layer={args.layer}）：")
        for c in chosen:
            suffix = "（二期跳过）" if c["layer"] == "e2e" else ""
            mf = f" filter={c['meta_filter']}" if c.get("meta_filter") else ""
            kg = " use_kg" if c["use_kg"] else ""
            print(f"  - {c['id']} [{c['tier']}|{c['layer']}]{kg}{mf} {c['question']}{suffix}")
        ds_view = {k: v.get("id") for k, v in setup_state.items()}
        print(f"[dry-run] dataset 映射：{ds_view}")
        return 0

    if not args.tag:
        print("[ERROR] --apply 必须搭配 --tag（防止误跑污染 runs 历史）")
        return 2

    try:
        client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 1
    except requests.RequestException as exc:
        print(f"[ERROR] 无法连接 RAGFlow：{exc}")
        print("提示：确认服务在运行，且已设置 RAGFLOW_EMAIL / RAGFLOW_PASSWORD。")
        return 1

    run_id = run_id_now()
    print(f"[INFO] run_id={run_id} tag={args.tag}；抓取配置快照…")
    snapshot = snapshot_datasets(client, setup_state)
    print("[INFO] 开始串行评测（弱并发网关，逐题执行）…")
    results = execute_suite(chosen, client, setup_state)
    summary = summarize(results)

    run_dir = OUT_DIR / EVAL_DIR_NAME / "runs" / run_id
    write_results_jsonl(run_dir / "results.jsonl", results)
    write_report(run_dir / "report.md", run_id, args.tag, results, summary, snapshot)
    append_run(OUT_DIR / EVAL_DIR_NAME / "runs.jsonl", {
        "run_id": run_id,
        "tag": args.tag,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "suite": args.suite, "layer": args.layer,
        "snapshot": snapshot, "summary": summary,
    })
    print(f"[INFO] 明细：{run_dir/'results.jsonl'}")
    print(f"[INFO] 报告：{run_dir/'report.md'}")

    ov = summary["overall"]
    print(f"\n=== 评测汇总 ===")
    print(f"通过率：{ov['passed']}/{ov['total']} ({ov['rate']:.0%})　"
          f"retrieval MRR 均值：{summary['mean_mrr']}")
    failed = [r["case_id"] for r in results if not r["passed"]]
    if failed:
        print(f"未通过：{', '.join(failed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

同时在文件头部 import 区补 `import argparse`；删除已无引用的 `write_json` 时先全局搜索确认（inspect_chunks 等同目录脚本若复用了它的语义就各自持有；本项目各脚本互不 import 辅助函数，可安全删除）。另确认 docstring 同步更新为新流程描述（中文）。

- [ ] **Step 4: Run tests**

Run: `cd src && python3 -m pytest tests/test_run_qc.py -q`
Expected: PASS（3 个保留/改造用例 + 3 个新用例）

- [ ] **Step 5: 回归检查点**

Run: `cd src && python3 -m pytest -q`
Expected: 全绿（此时 cases.jsonl 尚不存在，`tests/test_eval_cases_file.py` 是下一个任务才建的——所以此刻全绿指：除将要新建的该文件外的全部既有+新增测试）。

---

