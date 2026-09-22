import sys
import pathlib
import asyncio

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))


def _profile(key):
    from wiki_port import get_profile
    return get_profile(key)


def test_prioritize_nodes_no_cap_returns_all_in_order():
    import build_wiki_local as b
    nodes = {"A": {"type": "X"}, "B": {"type": "X"}, "C": {"type": "X"}}
    assert b._prioritize_nodes(nodes, [], "X", 0) == ["A", "B", "C"]
    assert b._prioritize_nodes(nodes, [], "X", 5) == ["A", "B", "C"]


def test_prioritize_nodes_prefers_page_unit_then_degree():
    import build_wiki_local as b
    nodes = {
        "枢纽站": {"type": "Station"},   # 主实体、高度
        "小支流": {"type": "River"},     # 非主实体、高度
        "冷门库": {"type": "Station"},   # 主实体、0 度
    }
    edges = [{"source": "枢纽站", "target": "小支流"}] * 3 + [{"source": "小支流", "target": "冷门库"}]
    # max_pages=2 → 先选主实体类型（Station），其中按度降序；再补非主实体高优先
    picked = b._prioritize_nodes(nodes, edges, "Station", 2)
    assert set(picked) == {"枢纽站", "小支流"} or set(picked) == {"枢纽站", "冷门库"}
    assert picked[0] == "枢纽站", "度最高的主实体应排首位"


def test_prioritize_nodes_all_pageunit_sorts_by_degree():
    import build_wiki_local as b
    nodes = {"hub": {"type": "Station"}, "leaf": {"type": "River"}}
    edges = [{"source": "hub", "target": "leaf"}] * 2
    picked = b._prioritize_nodes(nodes, edges, "all", 1)
    assert picked == ["hub"]


# ---------------------------------------------------------------------------
# merge_results —— 跨文档合并 + 别名归一 + 自环丢弃 + 悬空边剔除
# ---------------------------------------------------------------------------

def test_merge_normalizes_aliases_and_drops_selfloop():
    import build_wiki_local as b
    p = _profile("regulation")
    doc_results = [{
        "entities": [
            {"entity_name": "桃曲坡", "entity_type": "Structure", "description": "水库", "source_id": ["d1"]},
            {"entity_name": "桃曲坡水库", "entity_type": "Structure", "description": "主体", "source_id": ["d2"]},
        ],
        "relationships": [
            {"src_id": "桃曲坡水库", "tgt_id": "桃曲坡", "description": "同一实体自环",
             "keywords": "alias", "weight": 1, "source_id": ["d1"]},
        ],
    }]
    nodes, edges = b.merge_results(p, doc_results)
    assert "桃曲坡水库" in nodes, "别名应归一到标准名"
    assert len(nodes) == 1
    assert edges == [], "自环边应被丢弃"


def test_merge_drops_edge_with_missing_endpoint():
    import build_wiki_local as b
    p = _profile("topology")
    doc_results = [{
        "entities": [
            {"entity_name": "A站", "entity_type": "Station", "description": "测站", "source_id": ["d1"]},
        ],
        "relationships": [
            {"src_id": "A站", "tgt_id": "不存在的对岸", "description": "x",
             "keywords": "located_in", "weight": 2, "source_id": ["d1"]},
        ],
    }]
    nodes, edges = b.merge_results(p, doc_results)
    assert "A站" in nodes
    assert edges == [], "端点缺失的悬空边应被过滤"


# ---------------------------------------------------------------------------
# finalize_pages —— 注入互链 + See also
# ---------------------------------------------------------------------------

def test_finalize_pages_adds_links_and_see_also():
    import build_wiki_local as b
    p = _profile("topology")
    nodes = {
        "A站": {"name": "A站", "type": "Station", "description": "", "source_id": []},
        "沮河": {"name": "沮河", "type": "River", "description": "", "source_id": []},
        "乙库": {"name": "乙库", "type": "Structure", "description": "", "source_id": []},
    }
    edges = [
        {"source": "A站", "target": "沮河", "predicate": "located_in", "weight": 3, "keywords": ["located_in"], "description": ""},
        {"source": "A站", "target": "乙库", "predicate": "regulates", "weight": 2, "keywords": ["regulates"], "description": ""},
    ]
    # 正文只提到 沮河（会被 linkify 成内联链接）；乙库 未在正文出现，应进 See also
    pages = {"A站": "A站位于沮河沿岸。", "沮河": "沮河是主要河流。", "乙库": "乙库为上游水库。"}
    out = b.finalize_pages(p, pages, nodes, edges)
    assert "[[沮河]]" in out["A站"], "正文首次提到的其它实体应被链化"
    assert "## See also" in out["A站"]
    assert "[[乙库]]" in out["A站"].split("## See also")[1], "未内联的邻居应出现在 See also"


# ---------------------------------------------------------------------------
# build_index_md —— 目录含标题与（regulation）反查区
# ---------------------------------------------------------------------------

def test_index_md_contains_reverse_index_and_titles():
    import build_wiki_local as b
    p = _profile("regulation")
    nodes = {
        "调度规程": {"name": "调度规程", "type": "Regulation", "description": "", "source_id": []},
        "第1条": {"name": "第1条", "type": "Article", "description": "", "source_id": []},
    }
    pages = {"调度规程": "正文", "第1条": "条款正文"}
    ridx = {"汛期调度": ["第1条"]}
    md = b.build_index_md(p, pages, nodes, ridx)
    assert p.title in md
    assert "事项 → 适用条款" in md
    assert "调度规程" in md


def test_finalize_pages_strips_llm_see_also_no_duplicate():
    import build_wiki_local as b
    p = _profile("topology")
    nodes = {
        "A站": {"name": "A站", "type": "Station", "description": "", "source_id": []},
        "沮河": {"name": "沮河", "type": "River", "description": "", "source_id": []},
        "乙库": {"name": "乙库", "type": "Structure", "description": "", "source_id": []},
    }
    edges = [{"source": "A站", "target": "乙库", "predicate": "regulates", "weight": 2, "keywords": [], "description": ""}]
    # 模型自作主张写了 See also 列 沮河；系统应剔除后重新按邻居生成
    pages = {"A站": "A站说明。\n\n## See also\n- [[沮河]]\n", "沮河": "河", "乙库": "库"}
    out = b.finalize_pages(p, pages, nodes, edges)
    assert out["A站"].count("## See also") == 1, "See also 段应唯一"
    assert "[[乙库]]" in out["A站"].split("## See also")[1], "系统补的 See also 应含真实邻居"


def test_index_md_topology_no_reverse_section():
    import build_wiki_local as b
    p = _profile("topology")
    nodes = {"A站": {"name": "A站", "type": "Station", "description": "", "source_id": []}}
    md = b.build_index_md(p, {"A站": "x"}, nodes, None)
    assert "事项 → 适用条款" not in md
    assert "A站" in md


# ---------------------------------------------------------------------------
# PageSynthesizer —— input_hash 缓存：相同输入只调一次 LLM
# ---------------------------------------------------------------------------

class _FakeLLM:
    def __init__(self):
        self.calls = 0
        self.llm_name = "fake"
        self.max_length = 4096

    async def async_chat(self, system, history, gen_conf=None):
        self.calls += 1
        return "这是合成的百科页面正文。"


def test_page_synthesizer_caches_by_input_hash(tmp_path):
    from wiki_port import PageSynthesizer
    llm = _FakeLLM()
    syn = PageSynthesizer(llm, cache_dir=str(tmp_path))
    entity = {"name": "溢洪道", "type": "Structure", "description": "泄洪设施"}
    edges = [{"source": "溢洪道", "target": "主坝", "predicate": "part_of", "weight": 1, "description": ""}]

    md1 = asyncio.run(syn.synth(entity, edges, src_texts=["溢洪道用于泄洪。"]))
    md2 = asyncio.run(syn.synth(entity, edges, src_texts=["溢洪道用于泄洪。"]))
    assert md1 == "这是合成的百科页面正文。"
    assert md2 == md1
    assert llm.calls == 1, "相同输入第二次应命中缓存，不再调 LLM"

    # 输入变化（源文不同）应触发重新合成
    asyncio.run(syn.synth(entity, edges, src_texts=["完全不同的原文内容。"]))
    assert llm.calls == 2


def test_page_synthesizer_force_bypasses_cache(tmp_path):
    from wiki_port import PageSynthesizer
    llm = _FakeLLM()
    syn = PageSynthesizer(llm, cache_dir=str(tmp_path))
    entity = {"name": "主坝", "type": "Structure", "description": "挡水"}
    asyncio.run(syn.synth(entity, [], src_texts=["原文"]))
    asyncio.run(syn.synth(entity, [], src_texts=["原文"], force=True))
    assert llm.calls == 2, "force=True 应绕过缓存重合成"


def test_write_pages_clears_stale_md(tmp_path):
    import build_wiki_local as b
    pdir = tmp_path / "pages"
    pdir.mkdir()
    (pdir / "ghost.md").write_text("stale", encoding="utf-8")  # 上次残留
    b._write_pages(str(pdir), {"Alpha": "a page", "Beta": "b page"})
    names = sorted(p.name for p in pdir.glob("*.md"))
    assert names == ["Alpha.md", "Beta.md"], "应清除旧 .md 只留本次页面"
    assert (pdir / "Alpha.md").read_text(encoding="utf-8") == "a page"
