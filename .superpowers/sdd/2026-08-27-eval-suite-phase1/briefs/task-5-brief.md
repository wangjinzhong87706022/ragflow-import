### Task 5: 配置快照与 runs.jsonl 持久化

**Files:**
- Modify: `src/run_qc.py`
- Test: `src/tests/test_eval_runs.py`

**Interfaces:**
- Consumes: `RAGFlowClient.list_datasets()`；`config.DATASETS`（拿 ds_key→name 对应关系：DATASETS 元素含 `"key"` 与 `"name"`）
- Produces:
  - `SNAPSHOT_FIELDS = ("id", "name", "chunk_method", "parser_config", "embedding_model")`
  - `snapshot_datasets(client, setup_state: dict) -> dict`
    返回 `{ds_key: {字段...}}`，只收 setup_state 里登记过的 ds；找不到的 ds_key 以 `None` 值占位。
  - `run_id_now() -> str` —— `time.strftime("%Y%m%d-%H%M%S")` 本地时间
  - `append_run(runs_path: Path, record: dict) -> None` —— 追加一行 JSON（父目录自动建）
  - `load_runs(runs_path: Path) -> list[dict]`
  - `snapshots_differ(a: dict, b: dict) -> bool`

- [ ] **Step 1: Write the failing test**

新建 `src/tests/test_eval_runs.py`：

```python
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from unittest.mock import MagicMock, patch

from run_qc import (
    snapshot_datasets, run_id_now, append_run, load_runs, snapshots_differ,
)
import run_qc as rq


def test_snapshot_pulls_each_registered_ds_by_name():
    cfg_datasets = [{"key": "ds1", "name": "规程与预案"},
                    {"key": "ds3", "name": "洪水资料"}]
    setup_state = {"ds1": {"id": "id-a"}, "ds3": {"id": "id-b"}}
    client = MagicMock()
    client.list_datasets.return_value = [
        {"id": "id-b", "name": "洪水资料", "chunk_method": "naive",
         "parser_config": {"chunk_token_num": 512}, "embedding_model": "bge-m3"},
        {"id": "id-zz", "name": "别的库", "chunk_method": "naive",
         "parser_config": {}, "embedding_model": "x"},
    ]
    with patch.object(rq.config, "DATASETS", cfg_datasets):
        snap = snapshot_datasets(client, setup_state)
    assert set(snap) == {"ds1", "ds3"}
    assert snap["ds3"]["id"] == "id-b"
    assert snap["ds1"] is None                              # 服务端缺这个库 → 占位 None
    assert "parser_config" in snap["ds3"]


def test_run_id_format():
    rid = run_id_now()
    assert len(rid) == 15 and rid[8] == "-"
    assert rid.replace("-", "").isdigit()


def test_append_then_load_roundtrip(tmp_path):
    rp = tmp_path / "eval" / "runs.jsonl"
    append_run(rp, {"run_id": "20260827-010101", "tag": "v0"})
    append_run(rp, {"run_id": "20260827-020202", "tag": "v1"})
    runs = load_runs(rp)
    assert [r["run_id"] for r in runs] == ["20260827-010101", "20260827-020202"]


def test_snapshots_differ_deep_compare():
    a = {"ds1": {"parser_config": {"chunk_token_num": 512}}}
    b1 = {"ds1": {"parser_config": {"chunk_token_num": 512}}}
    b2 = {"ds1": {"parser_config": {"chunk_token_num": 256}}}
    assert snapshots_differ(a, b1) is False
    assert snapshots_differ(a, b2) is True
    assert snapshots_differ(a, {}) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src && python3 -m pytest tests/test_eval_runs.py -q`
Expected: FAIL（ImportError）

- [ ] **Step 3: Write minimal implementation**

`src/run_qc.py`：import 区新增裸 `import config`（与既有 `from config import …` 并列，供测试 `patch.object(config, "DATASETS", …)` 走模块属性替换）；然后追加函数：

```python
SNAPSHOT_FIELDS = ("id", "name", "chunk_method", "parser_config", "embedding_model")


def snapshot_datasets(client, setup_state: dict) -> dict:
    """apply 前抓取各 ds 配置快照（供 --compare 漂移告警，spec §2 原则4）。"""
    server_dss = {d["id"]: d for d in client.list_datasets()}
    snap: dict[str, dict] = {}
    for ds in config.DATASETS:
        key = ds["key"]
        if key not in setup_state:
            continue
        info = server_dss.get(setup_state[key]["id"])
        snap[key] = ({f: info[f] for f in SNAPSHOT_FIELDS}
                     if info else None)
    return snap


def run_id_now() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def append_run(runs_path: Path, record: dict) -> None:
    runs_path.parent.mkdir(parents=True, exist_ok=True)
    with open(runs_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_runs(runs_path: Path) -> list[dict]:
    if not runs_path.exists():
        return []
    with open(runs_path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def snapshots_differ(a: dict, b: dict) -> bool:
    return json.dumps(a, ensure_ascii=False, sort_keys=True) != \
        json.dumps(b, ensure_ascii=False, sort_keys=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src && python3 -m pytest tests/test_eval_runs.py -q`
Expected: PASS

- [ ] **Step 5: 回归检查点**

Run: `cd src && python3 -m pytest -q`
Expected: 全绿。

---

