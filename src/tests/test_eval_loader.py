import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import pytest

from run_qc import load_cases, EvalCaseError, CASE_REQUIRED_FIELDS


RELS = {
    "01-核心文档-四案/02-调度规程.pdf",
    "06-历年洪水资料/05-2013年洪水(7-22)/7-22防洪报告.doc",
}


def _write(tmp_path, *rows):
    p = tmp_path / "cases.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return p


VALID = {
    "id": "AC-T-01", "tier": "acceptance", "layer": "retrieval",
    "question": "溢洪道的设计泄量是多少？",
    "datasets": ["ds1"],
    "gold": {"numbers": ["1454"], "keywords": ["溢洪道"],
             "answer": "", "source_rel": ["01-核心文档-四案/02-调度规程.pdf"]},
}


def test_load_valid_case_gets_defaults(tmp_path):
    cases = load_cases(_write(tmp_path, VALID), RELS)
    assert cases[0]["use_kg"] is False                     # 缺省 false
    assert cases[0]["threshold"]["min_keywords"] == 1      # 缺省 1
    assert cases[0]["meta_filter"] is None


@pytest.mark.parametrize("field,value", [("tier", "other"), ("layer", "chat")])
def test_enum_violations_raise(tmp_path, field, value):
    bad = json.loads(json.dumps(VALID))
    bad[field] = value
    with pytest.raises(EvalCaseError):
        load_cases(_write(tmp_path, bad), RELS)


def test_missing_required_field_raises(tmp_path):
    for field in CASE_REQUIRED_FIELDS:
        bad = json.loads(json.dumps(VALID))
        bad.pop(field)
        with pytest.raises(EvalCaseError, match=field):
            load_cases(_write(tmp_path, bad), RELS)


def test_empty_datasets_raises(tmp_path):
    bad = json.loads(json.dumps(VALID)); bad["datasets"] = []
    with pytest.raises(EvalCaseError, match="datasets"):
        load_cases(_write(tmp_path, bad), RELS)


def test_duplicate_ids_raise(tmp_path):
    with pytest.raises(EvalCaseError, match="重复"):
        load_cases(_write(tmp_path, VALID, dict(VALID)), RELS)


def test_both_anchor_sets_empty_raises(tmp_path):
    bad = json.loads(json.dumps(VALID))
    bad["gold"]["numbers"], bad["gold"]["keywords"] = [], []
    with pytest.raises(EvalCaseError, match="金标锚点"):
        load_cases(_write(tmp_path, bad), RELS)


def test_source_rel_not_in_mapping_raises(tmp_path):
    bad = json.loads(json.dumps(VALID))
    bad["gold"]["source_rel"] = ["05-基础数据与曲线/泄流曲线.png.txt"]   # 旧提取链路径混入
    with pytest.raises(EvalCaseError, match="mapping.csv"):
        load_cases(_write(tmp_path, bad), RELS)


def test_meta_filter_outside_structured_raises(tmp_path):
    bad = json.loads(json.dumps(VALID))
    bad["meta_filter"] = {"flood_event": "2013-7"}
    with pytest.raises(EvalCaseError, match="structured"):
        load_cases(_write(tmp_path, bad), RELS)


def test_none_relset_skips_source_validation(tmp_path):
    bad = json.loads(json.dumps(VALID))
    bad["gold"]["source_rel"] = ["任意路径.pdf"]
    assert load_cases(_write(tmp_path, bad), None)[0]["question"].startswith("溢洪道")
