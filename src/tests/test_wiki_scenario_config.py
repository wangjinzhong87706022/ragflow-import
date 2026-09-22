import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# wiki_port.scenario_config —— 三场景画像的完整性与关键决策位
# ---------------------------------------------------------------------------

def test_all_scenarios_present_and_valid():
    from wiki_port import SCENARIOS
    assert set(SCENARIOS) == {"regulation", "topology", "case"}
    for key, p in SCENARIOS.items():
        assert p.key == key, f"画像 key 与字典键不一致: {key}"
        assert p.is_valid(), f"场景 {key} 画像字段不完整或 view 非法"


def test_regulation_targets_ds1_and_pages_view():
    from wiki_port import get_profile
    p = get_profile("regulation")
    assert p.dataset == "ds1"
    assert p.view == "pages"
    assert p.page_unit == "Regulation"
    assert p.reverse_index and p.reverse_index["source_type"] == "Subject"
    assert p.reverse_index["target_type"] == "Article"


def test_topology_is_canvas_with_relation_types():
    from wiki_port import get_profile
    p = get_profile("topology")
    assert p.dataset == "ds3"
    assert p.view == "canvas"
    assert p.page_unit == "all"
    assert {"located_in", "part_of", "regulates"} <= set(p.relation_types)
    assert "Station" in p.entity_types and "River" in p.entity_types


def test_case_has_timeline_and_floodunit():
    from wiki_port import get_profile
    p = get_profile("case")
    assert p.dataset == "ds3"
    assert p.page_unit == "FloodEvent"
    assert p.timeline is True


def test_get_profile_unknown_raises():
    import pytest
    from wiki_port import get_profile
    with pytest.raises(KeyError):
        get_profile("nonexistent")
