import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from run_qc import (
    normalize_for_match, numeric_hits, keyword_hits,
    anchor_hit_at_k, decide_pass,
)


def test_normalize_fullwidth_and_case():
    assert normalize_for_match("Ｍ³/S １２３Ａ") == "m³/s 123a"


def test_normalize_thousands_comma_removed_only_between_digits():
    assert "1454" in normalize_for_match("设计泄量1,454 m³/s")
    assert "," in normalize_for_match("雨情,水情")   # 中文/普通逗号不动（千分位只删数字后跟随者）


def test_numeric_hits_containment():
    texts = ["泄量1,454立方米每秒", "汛限水位７８８.５米"]
    assert numeric_hits(["1454"], texts) == 1
    assert numeric_hits(["1454", "788.5"], texts) == 2
    assert numeric_hits(["9999"], texts) == 0


def test_keyword_hits_counts_distinct_keywords():
    texts = ["溢洪道百年一遇泄量1454"]
    assert keyword_hits(["溢洪道", "百年一遇", "瑶曲"], texts) == 2


def test_anchor_hit_boundary_at_k():
    gold = {"numbers": ["1454"], "keywords": ["溢洪道"]}
    texts = [f"噪声块{i}" for i in range(5)] + ["溢洪道设计泄量1454"]
    assert anchor_hit_at_k(gold, texts, 5) is False
    assert anchor_hit_at_k(gold, texts, 6) is True


def test_decide_pass_formula_truth_table():
    # 规格公式：pass = (numeric_exact OR hit@10) AND keyword_ok
    assert decide_pass(numeric_exact=True, hit10=False, kw_hits=1, min_keywords=1)
    assert decide_pass(numeric_exact=False, hit10=True, kw_hits=2, min_keywords=2)
    assert not decide_pass(numeric_exact=True, hit10=False, kw_hits=0, min_keywords=1)   # 关键词门挡下
    assert not decide_pass(numeric_exact=False, hit10=False, kw_hits=5, min_keywords=1)  # 锚点门挡下


from run_qc import score_retrieval, mrr_mean


def test_score_retrieval_full_fields():
    gold = {"numbers": ["1454"], "keywords": ["溢洪道", "百年一遇"]}
    texts = ["噪声", "溢洪道百年一遇设计泄量1454"]
    r = score_retrieval(gold, texts)
    assert r["hit5"] and r["hit10"]
    assert r["numeric_exact"] is True
    assert r["kw_hits"] == 2
    assert r["mrr_rank"] == 2
    assert r["passed"] is True


def test_score_retrieval_fails_on_empty_numbers_without_hits():
    gold = {"numbers": [], "keywords": ["瑶曲"]}          # 只有关键词锚点
    r = score_retrieval(gold, ["柳林站雨量偏大"])
    assert r["numeric_exact"] is False
    assert r["hit10"] is False
    assert r["passed"] is False                            # keyword_ok 但锚点门未过


def test_score_retrieval_keywords_empty_means_always_ok():
    gold = {"numbers": ["8360"], "keywords": []}
    r = score_retrieval(gold, ["潼关站8360m3/s"])
    assert r["kw_needed"] == 0
    assert r["passed"] is True


def test_score_retrieval_no_keywords_present_needs_one():
    gold = {"numbers": ["123"], "keywords": ["汛限水位"]}
    r = score_retrieval(gold, ["123 在正文出现但关键词缺席"])
    assert r["kw_needed"] == 1
    assert r["passed"] is False


def test_mrr_mean_over_cases():
    assert mrr_mean([1, 3, 0]) == (1 + 1 / 3 + 0) / 3
    assert mrr_mean([]) == 0.0


import posixpath
from run_qc import rel_to_name, score_meta_filter, tag_signal, kg_gain_record


def test_rel_to_name():
    assert rel_to_name("06-历年洪水资料/05-2013年洪水(7-22)/7-22防洪报告.doc") == "7-22防洪报告.doc"


def test_meta_filter_exact_set_equality():
    exp = ["防洪报告.doc", "洪水过程.xls"]
    assert score_meta_filter(exp, list(reversed(exp)))["passed"] is True
    r = score_meta_filter(exp, ["防洪报告.doc"])                # 少了一个
    assert r["passed"] is False and r["missing"] == ["洪水过程.xls"] and r["unexpected"] == []
    r2 = score_meta_filter(exp, exp + ["多余.doc"])
    assert r2["passed"] is False and r2["unexpected"] == ["多余.doc"]


def test_tag_signal_intersection_count():
    chunks = [
        {"important_keywords": ["溢洪道", "泄洪", "无关词"]},
        {"important_keywords": []},
        {},
    ]
    assert tag_signal(chunks, ["溢洪道", "泄洪", "灾损"]) == 2   # 去重计数


def test_tag_signal_tolerates_missing_field():
    assert tag_signal([{"content_with_weight": "x"}], ["任意"]) == 0


def test_kg_gain_is_plain_difference():
    assert kg_gain_record(hit5_on=1, hit5_off=0) == 1
    assert kg_gain_record(hit5_on=0, hit5_off=1) == -1
