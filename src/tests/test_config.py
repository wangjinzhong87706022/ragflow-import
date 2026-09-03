# tests/test_config.py
import sys; sys.path.insert(0, "..")
from config import (
    DATASETS, TAG_KB, METADATA_SCHEMA,
    DIR_DATASET, DOC_EXTS, FLOOD_EVENT_BY_SUBDIR, QA_TABLE_KEYWORDS,
    VISION_SOURCES, TEXTUALIZED_VALUES, LOCATION_KEYWORDS, SKIP_DIRS,
    IMPORT_COLS, CORPUS_ROOT, DERIVED_ROOT, ORIGINALS_ROOT, API_BASE,
)

def test_datasets_have_required_keys():
    for ds in DATASETS:
        assert "key" in ds and "parser_config" in ds
    tag = TAG_KB
    assert tag["chunk_method"] == "tag"

def test_metadata_schema_11_fields():
    assert len(METADATA_SCHEMA) == 11
    keys = {f["key"] for f in METADATA_SCHEMA}
    assert "flood_event" in keys
    assert "location" in keys

def test_dir_dataset_assignment():
    """语料源切换后：一级目录→库指派必须完整覆盖 01–08，且 ds5 非空。"""
    assert set(DIR_DATASET) == {
        "01-核心文档-四案", "02-安全鉴定与评价", "03-施工图纸与设计",
        "04-确权划界", "05-基础数据与曲线", "06-历年洪水资料",
        "07-管理资料", "08-政策文件",
    }
    assert DIR_DATASET["01-核心文档-四案"] == "ds1"
    assert DIR_DATASET["06-历年洪水资料"] == "ds3"
    # ds5 工程资料必须有来源目录（修复空库问题）
    ds5_dirs = [k for k, v in DIR_DATASET.items() if v == "ds5"]
    assert ds5_dirs, "ds5 必须有指派目录，否则仍是空库"

def test_corpus_root_is_originals():
    """导入语料源必须是整理后的原始文件库 pdfs/（引用需锚定原文）。"""
    assert CORPUS_ROOT.name == "pdfs"
    assert ORIGINALS_ROOT == CORPUS_ROOT
    assert (CORPUS_ROOT / "01-核心文档-四案").exists()

def test_skip_dirs_exclude_media_and_archives():
    assert "09-图像与多媒体" in SKIP_DIRS
    assert "10-压缩包待处理" in SKIP_DIRS

def test_doc_exts_no_images():
    """图片不入扫描范围——走 VLM 预处理管线。"""
    for e in DOC_EXTS:
        assert e.startswith(".") and e not in {".jpg", ".jpeg", ".png"}

def test_flood_event_subdirs():
    assert FLOOD_EVENT_BY_SUBDIR["02-2021年洪水调度"] == "2021-10"
    assert "video_analysis" in SKIP_DIRS

def test_vision_sources_exist_and_cover_param_charts():
    """"VLM 源须覆盖全部参数/管理图表（≥12 张），且文件真实存在。"""
    assert len(VISION_SOURCES) >= 12
    names = {n for _, n in VISION_SOURCES}
    assert {"库容水位对照表", "泄流曲线", "三个责任人"} <= names

def test_import_cols_without_native_xlsx_path():
    """native_xlsx_path 列已废弃（语料即原件）。"""
    assert "rel" in IMPORT_COLS
    assert "dataset_key" in IMPORT_COLS
    assert "native_xlsx_path" not in IMPORT_COLS


# ---------------------------------------------------------------------------
# P0-4：graphrag/raptor 必须嵌套进 parser_config 下发（v0.27.0 容器契约）
#   - 创建期 PUT deep-merge，兄弟顶层键永远不会生效；
#   - naive 创建默认 raptor.use_raptor=True 且 graphrag.use_graphrag=True，
#     laws/paper 默认 False——关不掉就得显式发 False。
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# P2 批量：事件名修正、枚举对齐、部门关键词最长优先
# ---------------------------------------------------------------------------

def test_flood_event_by_subdir_2019_is_september():
    """2019年洪水的档案目录标注是 9-14（9月14日），值必须是 2019-9 而非 2019-7。"""
    assert FLOOD_EVENT_BY_SUBDIR["04-2019年洪水(9-14)"] == "2019-9"


def test_flood_event_enum_has_2021_09_not_2019_7():
    """mapping.csv 实测有 2021-09 子目录行；2019-7 已被 2019-9 取代。"""
    fld = next(f for f in METADATA_SCHEMA if f["key"] == "flood_event")
    assert "2021-09" in fld["enum"]
    assert "2019-7" not in fld["enum"]


def test_flood_event_sources_are_mutually_consistent():
    """目录映射 / 元数据枚举 / 标签词表三处的洪水事件名必须互相覆盖——
    三处各改各的正是本轮评审发现的漂移根因。"""
    from tag_vocab import VOCAB_ROWS

    fld = next(f for f in METADATA_SCHEMA if f["key"] == "flood_event")
    subdir_values = set(FLOOD_EVENT_BY_SUBDIR.values())
    assert subdir_values <= set(fld["enum"]), (
        f"目录映射中的事件不在 schema 枚举里: {subdir_values - set(fld['enum'])}"
    )
    vocab_tags = {t for _, t in VOCAB_ROWS}
    missing = subdir_values - vocab_tags
    assert not missing, f"目录映射中的事件不在标签词表里: {missing}"


def test_flood_event_2008_dir_is_actually_2018():
    """P1-5 纠偏回归 pin：档案目录 "06-2008年洪水(8-22)" 内表格数据实为
    2018-08-21 洪水（表内标题 20180821 + Excel 序列日 43333 + 《较大洪水统计表》
    180821 行互证），映射必须取 2018-8；枚举/词表须含 2018-8，2008-8 仅作历史值域。"""
    assert FLOOD_EVENT_BY_SUBDIR["06-2008年洪水(8-22)"] == "2018-8"
    fld = next(f for f in METADATA_SCHEMA if f["key"] == "flood_event")
    assert "2018-8" in fld["enum"]
    from tag_vocab import VOCAB_ROWS
    assert ("2018-8", "2018-8") in VOCAB_ROWS


def test_dept_keywords_longest_first():
    """多模式提取按序匹配，长词必须在前——否则'管理局'抢在'桃曲坡水库管理局'之前命中。"""
    lengths = [len(k) for k in LOCATION_KEYWORDS]
    assert lengths == sorted(lengths, reverse=True)
    import config as cfg
    dept_lengths = [len(k) for k in cfg.DEPT_KEYWORDS]
    assert dept_lengths == sorted(dept_lengths, reverse=True)


def _ds_by_key():
    return {ds["key"]: ds for ds in DATASETS}


def test_no_sibling_graphrag_raptor_keys():
    """兄弟键 graphrag_config/raptor_config 已废除——只能走 parser_config。"""
    for ds in DATASETS:
        assert "graphrag_config" not in ds and "raptor_config" not in ds, (
            f"{ds['key']} 仍带兄弟键：run_setup 从不下发它们"
        )


def test_parser_config_carries_nested_graphrag_and_raptor():
    for ds in DATASETS:
        g = ds["parser_config"].get("graphrag")
        r = ds["parser_config"].get("raptor")
        assert isinstance(g, dict) and "use_graphrag" in g, f"{ds['key']} 缺 graphrag.use_graphrag"
        assert isinstance(r, dict) and "use_raptor" in r, f"{ds['key']} 缺 raptor.use_raptor"


def test_graphrag_scope_matches_spec():
    ds = _ds_by_key()
    types = {"FloodEvent", "Station", "Structure", "Person", "Regulation", "Parameter"}
    for k in ("ds1", "ds3"):
        g = ds[k]["parser_config"]["graphrag"]
        assert g["use_graphrag"] is True and g["method"] == "light"
        assert g["resolution"] is True
        assert set(g["entity_types"]) == types
    # naive 创建默认 use_graphrag=True → ds2/ds4 必须显式 False；ds5(paper) 显式自文档化
    for k in ("ds2", "ds4", "ds5"):
        assert ds[k]["parser_config"]["graphrag"]["use_graphrag"] is False


def test_raptor_scope_matches_spec():
    ds = _ds_by_key()
    for k in ("ds1", "ds5"):
        assert ds[k]["parser_config"]["raptor"]["use_raptor"] is True
    # naive 创建默认 use_raptor=True → ds2/ds3/ds4 必须显式 False
    for k in ("ds2", "ds3", "ds4"):
        assert ds[k]["parser_config"]["raptor"]["use_raptor"] is False


def test_raptor_keys_are_server_accepted():
    """RaptorConfig extra="forbid"：非法键（如 max_leaf_nodes）会被整单拒绝(code=101)。"""
    allowed = {
        "use_raptor", "prompt", "max_token", "clustering_threshold",
        "clustering_ratio", "max_cluster", "random_seed", "scope",
        "auto_disable_for_structured_data",
    }
    for ds in DATASETS:
        extra = set(ds["parser_config"]["raptor"]) - allowed
        assert not extra, f"{ds['key']} 的 raptor 含服务端不认识的键: {extra}"


def test_ragflow_api_key_from_env(monkeypatch):
    """RAGFLOW_API_KEY 缺省空串；注入环境变量后 reload 可见（run_qc Bearer 免登录入口）。"""
    import importlib
    import config as cfg
    assert cfg.RAGFLOW_API_KEY == ""
    monkeypatch.setenv("RAGFLOW_API_KEY", "ragflow-test")
    try:
        reloaded = importlib.reload(cfg)
        assert reloaded.RAGFLOW_API_KEY == "ragflow-test"
    finally:
        monkeypatch.delenv("RAGFLOW_API_KEY")
        importlib.reload(cfg)  # 恢复默认，避免污染其他测试
    assert cfg.RAGFLOW_API_KEY == ""
