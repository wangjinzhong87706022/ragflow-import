### Task 10: 首批题库 out/eval/cases.jsonl（16 行）+ 到位校验测试

**Files:**
- Create: `out/eval/cases.jsonl`
- Create: `src/tests/test_eval_cases_file.py`

**Interfaces:**
- Consumes: Task 4 `load_cases`；Task 9 `_valid_rel_set`
- Produces: 数据文件本身。金标来源标注在各行 `note`。

- [ ] **Step 1: 创建 `src/tests/test_eval_cases_file.py`**

```python
"""对真实题库文件的到位校验：文件存在即可离线验证 schema + 引用合法性。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import pytest

from run_qc import load_cases, _valid_rel_set
import run_qc as rq

CASES = pathlib.Path(rq.OUT_DIR) / "eval" / "cases.jsonl"


@pytest.mark.skipif(not CASES.exists(), reason="题库尚未生成")
def test_shipped_cases_all_valid():
    cases = load_cases(CASES, _valid_rel_set())
    ids = [c["id"] for c in cases]
    assert len(ids) >= 16, f"首批应不少于 16 题，实际 {len(ids)}"
    assert len(ids) == len(set(ids))


@pytest.mark.skipif(not CASES.exists(), reason="题库尚未生成")
def test_shipped_acceptance_layer_split_exists():
    cases = load_cases(CASES, _valid_rel_set())
    layers = {c["layer"] for c in cases}
    assert {"retrieval", "structured"} <= layers
```

- [ ] **Step 2: Run test to verify it skips（还无题库）**

Run: `cd src && python3 -m pytest tests/test_eval_cases_file.py -q`
Expected: 2 skipped

- [ ] **Step 3: 写入题库（16 行，金标全部来自规格锚定值或 pilot 已核实的 chunk 原文）**

创建 `out/eval/cases.jsonl`，内容逐字如下：

```jsonl
{"id": "AC-KG-01", "tier": "acceptance", "layer": "retrieval", "use_kg": true,
 "question": "桃曲坡水库溢洪道的设计泄量是多少？", "datasets": ["ds1", "ds2"],
 "gold": {"numbers": ["1454"], "keywords": ["溢洪道", "百年一遇"], "answer": "1454 m³/s（百年一遇设计泄量）",
   "source_rel": ["01-核心文档-四案/02-调度规程.pdf", "01-核心文档-四案/03-汛期调度运用计划.pdf"]},
 "threshold": {"min_keywords": 2},
 "note": "原 QC 六问 Q1 平移；requirements §7 条目1；金标 1454 来自 config.TEXTUALIZED_VALUES（VLM 已批）"}
{"id": "AC-TS-01", "tier": "acceptance", "layer": "retrieval", "use_kg": true,
 "question": "2021年共发生几次洪水？时序如何？", "datasets": ["ds3"],
 "gold": {"numbers": [], "keywords": ["2021", "洪水"], "answer": "2021 年秋汛主要为 9-25、10-3 两场连续洪水过程（另有 9 月中旬强降雨），依据 ds3 各场调度汇报。",
   "source_rel": ["06-历年洪水资料/02-2021年洪水调度/三场洪水调度过程汇报.doc"]},
 "threshold": {"min_keywords": 2},
 "note": "原 Q2 平移；不加 flood_event 过滤（问全年次数，限定月份自相矛盾——旧测试留档理由）"}
{"id": "AC-XD-01", "tier": "acceptance", "layer": "retrieval", "use_kg": true,
 "question": "10·3洪水调度依据规程哪条？涉及哪些站点？", "datasets": ["ds1", "ds3"],
 "gold": {"numbers": [], "keywords": ["规程", "柳林", "瑶曲"], "answer": "参考 10·3 洪水调度汇报所引规程条款，站点涉及柳林、瑶曲等水文站。",
   "source_rel": ["01-核心文档-四案/02-调度规程.pdf", "06-历年洪水资料/02-2021年洪水调度/10-3洪水/桃曲坡水库10月3日-10月6日洪水调度情况汇报.doc"]},
 "threshold": {"min_keywords": 2},
 "note": "原 Q3 平移；requirements §7 跨文档多跳；柳林/瑶曲站名经 pilot chunk 原文核实存在"}
{"id": "AC-ENT-01", "tier": "acceptance", "layer": "retrieval", "use_kg": true,
 "question": "安芳东在哪些洪水事件中担任指挥？", "datasets": ["ds3", "ds4"],
 "gold": {"numbers": [], "keywords": ["安芳东", "指挥", "2021"], "answer": "安芳东担任 2021 年秋汛（10·3 等）洪水调度指挥。",
   "source_rel": ["06-历年洪水资料/02-2021年洪水调度/10-3洪水/桃曲坡水库10月3日-10月6日洪水调度情况汇报.doc"]},
 "threshold": {"min_keywords": 2},
 "note": "原 Q4 平移；实体关联型，GraphRAG 场景"}
{"id": "AC-QK-01", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "桃曲坡水库汛限水位是多少？", "datasets": ["ds1", "ds2"],
 "gold": {"numbers": ["788.5"], "keywords": ["汛限水位"], "answer": "788.5 m",
   "source_rel": ["01-核心文档-四案/02-调度规程.pdf", "01-核心文档-四案/03-汛期调度运用计划.pdf"]},
 "threshold": {"min_keywords": 1},
 "note": "原 Q6 平移；requirements §7 条目3 参数快查；金标 788.5 来自 TEXTUALIZED_VALUES"}
{"id": "AC-QK-02", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "水库的千年一遇校核泄量是多少？", "datasets": ["ds1", "ds5"],
 "gold": {"numbers": ["2218"], "keywords": ["千年一遇"], "answer": "2218 m³/s（千年一遇校核泄量）",
   "source_rel": ["01-核心文档-四案/02-调度规程.pdf", "02-安全鉴定与评价/02-桃曲坡水库大坝安全鉴定.pdf"]},
 "threshold": {"min_keywords": 1},
 "note": "requirements §7 参数族扩展；金标 2218 来自 TEXTUALIZED_VALUES（VLM 已批）"}
{"id": "AC-F-01", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "10·3洪水中漆水河岔口断面的最大下泄流量是多少？警戒和保证流量分别是多少？", "datasets": ["ds3"],
 "gold": {"numbers": ["260"], "keywords": ["岔口", "警戒"], "answer": "260 m³/s（警戒 200 m³/s、保证 300 m³/s）",
   "source_rel": ["06-历年洪水资料/02-2021年洪水调度/10-3洪水/桃曲坡水库10月3日-10月6日洪水调度情况汇报.doc"]},
 "threshold": {"min_keywords": 1},
 "note": "金标逐字核实于 pilot chunk：『10月5日20时下泄流量达到本次最大260m3/s（警戒200 m3/s、保证300 m3/s）』"}
{"id": "AC-F-02", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "9-10月份岔口断面共计弃水量是多少？", "datasets": ["ds3"],
 "gold": {"numbers": ["1.56"], "keywords": ["弃水"], "answer": "1.56 亿立方米",
   "source_rel": ["06-历年洪水资料/02-2021年洪水调度/10-3洪水/桃曲坡水库10月3日-10月6日洪水调度情况汇报.doc"]},
 "threshold": {"min_keywords": 1},
 "note": "pilot chunk 原文：『9-10月份岔口断面共计弃水量1.56亿立方米』"}
{"id": "AC-F-03", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "10月2日至6日灌区的过程面雨量是多少？哪个站最大？", "datasets": ["ds3"],
 "gold": {"numbers": ["121", "147"], "keywords": ["面雨量", "瑶曲"], "answer": "过程面雨量 121mm，最大瑶曲站 147mm",
   "source_rel": ["06-历年洪水资料/02-2021年洪水调度/10-3洪水/桃曲坡水库10月3日-10月6日洪水调度情况汇报.doc"]},
 "threshold": {"min_keywords": 1},
 "note": "pilot chunk 原文：『过程面雨量121mm，最大瑶曲站147mm』"}
{"id": "AC-F-04", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "9月25日至10月10日水库泄水总量是多少？各通道分别多少？", "datasets": ["ds3"],
 "gold": {"numbers": ["9685", "2066", "7619"], "keywords": ["泄水总量"], "answer": "泄水总量 9685 万方（溢洪道 2066 万方、低洞 7619 万方）",
   "source_rel": ["06-历年洪水资料/02-2021年洪水调度/10-3洪水/桃曲坡水库10月3日-10月6日洪水调度情况汇报.doc"]},
 "threshold": {"min_keywords": 1},
 "note": "pilot chunk 原文：『共泄水16天，泄水总量9685万方（其中溢洪道2066万方、低洞7619万方）』"}
{"id": "AC-F-05", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "10月6日的专报里长武县最大点降雨量是多少？在哪个镇？", "datasets": ["ds3"],
 "gold": {"numbers": ["54.2"], "keywords": ["相公镇", "长武"], "answer": "长武县相公镇 54.2 毫米",
   "source_rel": ["06-历年洪水资料/01-水情通报/汛情专报_20211006.pdf"]},
 "threshold": {"min_keywords": 1},
 "note": "pilot chunk 原文：『点最大降雨量37.5毫米-54.2毫米，其中长武县相公镇降雨量54.2毫米』"}
{"id": "AC-F-06", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "黄河第3号洪水中潼关站的洪峰有多大？历史上处于什么位置？", "datasets": ["ds3"],
 "gold": {"numbers": ["8360"], "keywords": ["潼关", "1979"], "answer": "潼关站出现 1979 年以来实测最大洪水 8360 m³/s（警戒 5000）",
   "source_rel": ["06-历年洪水资料/01-水情通报/重要水情快报第121期.pdf"]},
 "threshold": {"min_keywords": 1},
 "note": "pilot chunk 原文：『黄河出现3号洪水，潼关站出现1979年以来实测最大洪水 8360m3/s（警戒5000）』"}
{"id": "AC-F-07", "tier": "acceptance", "layer": "retrieval", "use_kg": false,
 "question": "10月2日至7日全省面平均雨量是多少？", "datasets": ["ds3"],
 "gold": {"numbers": ["87.2"], "keywords": ["面平均雨量"], "answer": "全省面平均雨量 87.2mm",
   "source_rel": ["06-历年洪水资料/01-水情通报/重要水情快报第121期.pdf"]},
 "threshold": {"min_keywords": 1},
 "note": "pilot chunk 原文：『本次过程全省面 平均雨量87.2mm』（跨行断裂亦属待观察项，正适合当考题）"}
{"id": "AC-ST-01", "tier": "acceptance", "layer": "structured", "use_kg": false,
 "question": "2013年7月洪水的降雨量统计结果如何？", "datasets": ["ds3"],
 "meta_filter": {"flood_event": "2013-7"},
 "gold": {"numbers": [], "keywords": ["降雨"], "answer": "",
   "source_rel": ["06-历年洪水资料/05-2013年洪水(7-22)/7-22防洪报告.doc",
                   "06-历年洪水资料/05-2013年洪水(7-22)/“7.22”洪水汇报终告.doc",
                   "06-历年洪水资料/05-2013年洪水(7-22)/“7.29”洪水简讯doc.doc",
                   "06-历年洪水资料/05-2013年洪水(7-22)/洪水过程.xls",
                   "06-历年洪水资料/05-2013年洪水(7-22)/降雨量统计.xls"]},
 "threshold": {"min_keywords": 1},
 "note": "原 Q5 升级：hard-filter drill，期望名单=mapping.csv 该 flood_event 全部非 skip 行；requirements §7 条目2"}
{"id": "AC-ST-02", "tier": "acceptance", "layer": "structured", "use_kg": false,
 "question": "2020年8月的洪水都留下了哪些资料？", "datasets": ["ds3"],
 "meta_filter": {"flood_event": "2020-8"},
 "gold": {"numbers": [], "keywords": [], "answer": "",
   "source_rel": ["06-历年洪水资料/03-2020年洪水(8-16)/防洪报告.doc",
                   "06-历年洪水资料/03-2020年洪水(8-16)/洪水调度报告.doc",
                   "06-历年洪水资料/03-2020年洪水(8-16)/洪水过程.xls",
                   "06-历年洪水资料/03-2020年洪水(8-16)/洪水汇报.doc"]},
 "threshold": {"min_keywords": 1},
 "note": "meta_filter 纯集合比对题（gold.keywords 为空 → keyword_ok 恒真，判据完全落在集合一致上）"}
{"id": "AC-ST-03", "tier": "acceptance", "layer": "structured", "use_kg": false,
 "question": "检索范围限定在 2021-10 洪水事件的资料，列出这批文档。", "datasets": ["ds3"],
 "meta_filter": {"flood_event": "2021-10"},
 "gold": {"numbers": [], "keywords": [], "answer": "",
   "source_rel": ["06-历年洪水资料/01-水情通报/水情通报第161期.pdf",
                   "06-历年洪水资料/01-水情通报/汛情专报_20211006.pdf",
                   "06-历年洪水资料/01-水情通报/重要水情快报第121期.pdf",
                   "06-历年洪水资料/02-2021年洪水调度/9-15强降雨工作汇报.doc",
                   "06-历年洪水资料/02-2021年洪水调度/10-3洪水/洪水过程.xls",
                   "06-历年洪水资料/02-2021年洪水调度/10-3洪水/受损统计.xlsx",
                   "06-历年洪水资料/02-2021年洪水调度/10-3洪水/水务局汇报.docx",
                   "06-历年洪水资料/02-2021年洪水调度/10-3洪水/桃曲坡水库10月3日-10月6日洪水调度情况汇报.doc",
                   "06-历年洪水资料/02-2021年洪水调度/9-25洪水/防汛抗洪纪实.doc",
                   "06-历年洪水资料/02-2021年洪水调度/9-25洪水/洪水调度情况汇报.doc",
                   "06-历年洪水资料/02-2021年洪水调度/9-25洪水/洪水过程.xls",
                   "06-历年洪水资料/02-2021年洪水调度/9-25洪水/华商报报道.doc",
                   "06-历年洪水资料/02-2021年洪水调度/9-25洪水/汛期措施.doc",
                   "06-历年洪水资料/02-2021年洪水调度/防洪调度效益统计.xlsx",
                   "06-历年洪水资料/02-2021年洪水调度/三场洪水调度过程汇报.doc"]},
 "threshold": {"min_keywords": 1},
 "note": "2021-10 子树全集（15 件，_dup 去重行不入库故不在期望名单）；requirements §7 条目2 第二个条件"}
```

写入方式：以上即为文件原文（JSONL，每行一个完整 JSON 对象；书写器须保持每行单行或按上文的紧凑换行风格均可——loader 只要求每行合法 JSON，建议整行单行写入以免歧义）。

- [ ] **Step 4: 到位校验**

Run: `cd src && python3 -m pytest tests/test_eval_cases_file.py -q`
Expected: 2 passed

Run: `cd src && python3 run_qc.py`
Expected: `[dry-run] 将执行 16 题 …` 清单打印，无网络调用（可见 `AC-ST-0x` 带 `filter=`、`AC-KG-01` 带 `use_kg` 标注）。

- [ ] **Step 5: 回归检查点**

Run: `cd src && python3 -m pytest -q`
Expected: 全绿（此时包括题库到位校验在内的所有测试套件）。

---

### Task 11: 操作手册章节（src/README.md 追加「评测」节）

**Files:**
- Modify: `src/README.md`（文末追加；同处更新阶段4命令示例）

**Interfaces:**
- Consumes: 无
- Produces: 操作员文档。新增章节文字逐字如下（放在「阶段4」相关段落之后；如原文有六问 QC 的旧命令样例，就地替换为新样例）：

```markdown
## 评测（run_qc.py — 验收与回归两用）

题库在 `out/eval/cases.jsonl`（每行一道题）。规则要点：

- `tier`: `acceptance`（验收）/ `regression`（回归）；`layer`: `retrieval` / `structured`（本期）/ `e2e`（二期）。
- 金标只有两类锚点：`gold.numbers`（数值精确包含匹配，自动做全半角/大小写/千分位归一）与
  `gold.keywords`。通过公式固定为 `(数值精确 OR hit@10) AND 关键词≥min_keywords`，不做单位换算。
- `source_rel` 只是"期望出处"参考列（报告中展示），不参与判分；但路径必须存在于
  `out/mapping.csv`，否则加载报错——防止旧提取链路径混入。
- `layer=structured` 且 `datasets` 含 ds1/ds3 时会自动加跑反向 `use_kg` 对照，
  增益差值仅记录展示、不作门槛（零侵扰，不改动任何 dataset 配置）。

常用命令（均在 `src/` 下）：

```bash
python3 run_qc.py                              # dry-run：列出将要执行的题目清单
python3 run_qc.py --apply --tag v0-baseline    # 实跑一轮（--apply 必须配 --tag）
python3 run_qc.py --suite acceptance           # 只跑验收档；--layer 可叠加过滤
python3 run_qc.py --compare last               # 最近两轮 per-case 对比（含配置漂移告警）
python3 run_qc.py --compare <runA> <runB>      # 指定两个 run_id
```

产物布局：`out/eval/runs.jsonl`（历史索引，追加式）、`out/eval/runs/<run_id>/{results.jsonl,report.md}`、
`out/eval/compare_<A>_vs_<B>.md`。

**扩题方法**：向 `cases.jsonl` 追加一行新 JSON 即生效，无需改代码；`id` 不得重复，
`source_rel` 必须（逐字）取自 `out/mapping.csv` 第一列。建议新题先用
`python3 run_qc.py --suite acceptance --layer retrieval` 小范围试跑。
二期将提供 `gen_cases.py` 从旧提取链批量出候选题（人工抽验 ≥20% 后方可转入正式题库）。
```

- [ ] **Step 1: 应用上述章节修改**

打开 `src/README.md`，定位到现存的 run_qc / 阶段4 说明文字，将其替换为本节的标题与内容（保持 README 其余结构与语气不变）。

- [ ] **Step 2: 手动核对**

Run: `grep -n "评测" src/README.md | head` 和 `grep -n "questions\|六问" src/README.md`
Expected: 新章节可见；旧的六问 QC 示例（若有）已被替换。

- [ ] **Step 3: 最终回归检查点**

Run: `cd src && python3 -m pytest -q`
Expected: 全绿（本期完成定义）。

---

