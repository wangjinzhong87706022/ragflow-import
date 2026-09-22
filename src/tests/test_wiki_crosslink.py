import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from wiki_port import crosslinker as cx


# ---------------------------------------------------------------------------
# normalize / _linkable —— 别名归一 + 噪声防护
# ---------------------------------------------------------------------------

def test_normalize_alias_maps_to_standard():
    # DomainKnowledgeDict 里 桃曲坡 → 桃曲坡水库
    assert cx.normalize_name("桃曲坡") == "桃曲坡水库"
    assert cx.normalize_name("不存在的名字XYZ") == "不存在的名字XYZ"


def test_linkable_rejects_noise():
    assert cx._linkable("溢洪道") is True
    assert cx._linkable("A") is False            # 单字符
    assert cx._linkable("C1") is False           # 代号型
    assert cx._linkable("deadbeef12") is False   # 十六进制 hash 形态


# ---------------------------------------------------------------------------
# build_entity_index —— 长名优先、去重、剔除噪声
# ---------------------------------------------------------------------------

def test_entity_index_longest_first_and_dedup():
    nodes = {
        "桃曲坡水库": {"name": "桃曲坡水库", "type": "Structure"},
        "桃曲坡水库主坝": {"name": "桃曲坡水库主坝", "type": "Structure"},
        "C1": {"name": "C1", "type": "Parameter"},
    }
    idx = cx.build_entity_index(nodes)
    assert "C1" not in idx
    assert idx[0] == "桃曲坡水库主坝", "更长实体名应排在前面避免子串误链"


# ---------------------------------------------------------------------------
# linkify —— 首现注入 [[链接]]、不自链、已链不重复
# ---------------------------------------------------------------------------

def test_linkify_injects_first_mention_only():
    md = "溢洪道用于泄洪。溢洪道很关键。主坝安全。"
    out = cx.linkify(md, ["溢洪道", "主坝"], self_name="桃曲坡水库")
    assert out.count("[[溢洪道]]") == 1, "同名只链首次出现"
    assert "[[主坝]]" in out


def test_linkify_skips_self():
    md = "主坝是核心，主坝需监测。"
    out = cx.linkify(md, ["主坝"], self_name="主坝")
    assert "[[" not in out, "同页不链自身"


# ---------------------------------------------------------------------------
# neighbors / see_also —— 按权重排序 Top-K，排除已有链接
# ---------------------------------------------------------------------------

def _edges():
    return [
        {"source": "甲站", "target": "乙河", "predicate": "regulates", "weight": 5},
        {"source": "甲站", "target": "丙库", "predicate": "part_of", "weight": 9},
        {"source": "甲站", "target": "丁坝", "predicate": "other", "weight": 1},
    ]


def test_see_also_ranks_by_weight_and_excludes_existing():
    sa = cx.see_also("甲站", _edges(), top_k=2, existing_links=["丙库"])
    assert sa == ["乙河", "丁坝"], "丙库 已被排除；其余按权重 乙河(5) > 丁坝(1)"


def test_neighbors_aggregates_weight():
    nb = dict(cx.neighbors("甲站", _edges()))
    assert nb["丙库"] == 9 and nb["乙河"] == 5


# ---------------------------------------------------------------------------
# build_link_graph —— 从页面正文抽取页面↔页面有向边
# ---------------------------------------------------------------------------

def test_build_link_graph_extracts_wikilinks():
    pages = {"主坝": "见 [[溢洪道]] 与 [[库区]]。", "溢洪道": "回到 [[主坝]]。"}
    links = cx.build_link_graph(pages)
    pairs = {(l["source"], l["target"]) for l in links}
    assert ("主坝", "溢洪道") in pairs
    assert ("溢洪道", "主坝") in pairs


# ---------------------------------------------------------------------------
# reverse_index —— 事项(Subject) → 条款(Article) 反查
# ---------------------------------------------------------------------------

def test_reverse_index_direction_agnostic():
    nodes = {
        "汛期调度": {"name": "汛期调度", "type": "Subject"},
        "第12条": {"name": "第12条", "type": "Article"},
    }
    # 边的方向无论是 Subject->Article 还是反向，都应被收进以 Subject 为键的索引
    edges_fwd = [{"source": "汛期调度", "target": "第12条", "predicate": "applies_to"}]
    edges_rev = [{"source": "第12条", "target": "汛期调度", "predicate": "applies_to"}]
    for edges in (edges_fwd, edges_rev):
        ridx = cx.reverse_index(nodes, edges, "Subject", "Article")
        assert ridx == {"汛期调度": ["第12条"]}


# ---------------------------------------------------------------------------
# timeline —— 案例页时间线：按顺序/触发谓词抽相关项
# ---------------------------------------------------------------------------

def test_timeline_filters_predicates():
    edges = [
        {"source": "2021洪水", "target": "洪峰", "predicate": "triggered", "description": "引发洪峰"},
        {"source": "2021洪水", "target": "主坝", "predicate": "affected", "description": "影响主坝"},
    ]
    tl = cx.timeline("2021洪水", edges, order_predicates=["triggered", "occurred_at"])
    assert len(tl) == 1 and tl[0]["other"] == "洪峰"


def test_timeline_falls_back_to_incident_when_no_english_predicate():
    # 中文抽取场景：predicate 为中文关键词对，无英文命中 → 回退全部关联边，按年份升序
    edges = [
        {"source": "洝河3号洪水", "target": "2018年调度", "predicate": "数据汇总, 防洪", "keywords": ["数据汇总"], "description": "2018年的防洪调度", "weight": 3},
        {"source": "洝河1号洪水", "target": "洝河3号洪水", "predicate": "数据关联, 指标", "keywords": [], "description": "早于9月19日", "weight": 5},
    ]
    tl = cx.timeline("洝河3号洪水", edges, order_predicates=["triggered", "occurred_at"])
    assert len(tl) == 2, "无英文谓词命中时应回退到全部关联边"
    # 含 2018 年份的描述优先（has_year）排在无年份项之前
    assert tl[0]["other"] == "2018年调度" and tl[0]["has_year"] is True
    assert tl[1]["has_year"] is False


def test_timeline_drops_noisy_neighbors():
    edges = [
        {"source": "甲站", "target": "ab12", "predicate": "located_in", "keywords": [], "description": "噪声编码", "weight": 1},
        {"source": "甲站", "target": "洝河", "predicate": "located_in", "keywords": [], "description": "位于洝河", "weight": 2},
    ]
    tl = cx.timeline("甲站", edges, order_predicates=["located_in"])
    others = {t["other"] for t in tl}
    assert "ab12" not in others and "洝河" in others
