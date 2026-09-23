"""Tests for KB_PROFILE 画像机制：默认=桃曲坡（回归锁），jinghuiqu=泾惠渠覆盖。"""
import sys; sys.path.insert(0, "..")
import importlib

import pytest

import config as cfg


# ---------------------------------------------------------------------------
# 默认（不设 KB_PROFILE）——桃曲坡现值回归锁：现有管线零影响
# ---------------------------------------------------------------------------

def test_default_profile_keeps_taoqupo_values():
    assert cfg.KB_PROFILE == ""
    assert "01-核心文档-四案" in cfg.DIR_DATASET
    assert cfg.USE_TAG_KB is True
    assert cfg.TAG_VOCAB_FILENAME == "taoqupo_vocab.txt"
    assert cfg.DOC_CATEGORY_BY_DS["ds1"] == "规程预案"
    assert cfg.DOC_NATURE_BY_DS["ds5"] == "技术"


def test_corpus_uses_config_category_maps():
    """corpus 的推导映射源自 config（默认态下内容一致；profile 覆盖后联动需
    新进程重导，故只能比内容不能比对象——reload 会换 dict 实例）。"""
    import corpus
    assert corpus._DOC_CATEGORY_FROM_DS == cfg.DOC_CATEGORY_BY_DS
    assert corpus._DOC_NATURE_BY_DS == cfg.DOC_NATURE_BY_DS


# ---------------------------------------------------------------------------
# jinghuiqu 画像覆盖
# ---------------------------------------------------------------------------

def test_jinghuiqu_profile_overrides():
    import os
    os.environ["KB_PROFILE"] = "jinghuiqu"
    try:
        reloaded = importlib.reload(cfg)
        assert reloaded.DIR_DATASET == {
            "工程管理处": "jhc1", "工程建设处资料": "jhc2", "计划处": "jhc3",
        }
        assert reloaded.USE_TAG_KB is False
        assert reloaded.OUT_DIR.name == "jinghuiqu"
        assert reloaded.CORPUS_ROOT.name == "jhc_corpus"
        assert [d["key"] for d in reloaded.DATASETS] == ["jhc1", "jhc2", "jhc3"]
        for ds in reloaded.DATASETS:
            pc = ds["parser_config"]
            # 一期全关：naive 创建默认 True，必须显式 False（review P0-4 同款约束）
            assert pc["graphrag"]["use_graphrag"] is False
            assert pc["raptor"]["use_raptor"] is False
            assert pc["tag_kb_ids"] == []
        assert reloaded.FLOOD_EVENT_BY_SUBDIR == {}
        assert reloaded.DOC_CATEGORY_BY_DS["jhc3"] == "计划审批"
        assert len(reloaded.METADATA_SCHEMA) == 11   # run_import._META_FIELD_MAP 兼容前提
        srcs = next(f for f in reloaded.METADATA_SCHEMA if f["key"] == "source_format")
        assert "merged_pdf" in srcs["enum"]
        # 长词优先（桃曲坡教训：短词在前会前缀错配）
        lengths = [len(k) for k in reloaded.LOCATION_KEYWORDS]
        assert lengths == sorted(lengths, reverse=True)
    finally:
        del os.environ["KB_PROFILE"]
        importlib.reload(cfg)
    # 恢复默认，避免污染同会话其他测试
    assert "01-核心文档-四案" in cfg.DIR_DATASET


def test_unknown_profile_fails_fast():
    import os
    os.environ["KB_PROFILE"] = "no_such_profile"
    try:
        with pytest.raises(ValueError, match="no_such_profile"):
            importlib.reload(cfg)
    finally:
        del os.environ["KB_PROFILE"]
        importlib.reload(cfg)
    assert cfg.USE_TAG_KB is True
