"""tests/test_corpus.py — 语料源切换后：pdfs/ 目录族布局的扫描/分类/元数据测试"""
import sys; sys.path.insert(0, "..")
from corpus import (
    Entry, classify, parse_flood_event, scan,
    write_mapping_csv, read_mapping_csv, build_manifest,
)
import tempfile, csv, os

import pytest

import corpus as corpus_module
from config import CORPUS_ROOT as _CORPUS_ROOT

# 依赖真实语料盘的用例统一打标：语料根缺失时跳过而非报错（环境可移植性）
_needs_corpus = pytest.mark.skipif(
    not _CORPUS_ROOT.exists(), reason="真实语料根不可用（/home/scada/SmartTwinRes-skills/pdfs）"
)


# ---------------------------------------------------------------------------
# Hermetic tests：注入临时语料根，不依赖真实磁盘布局
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_roots(tmp_path, monkeypatch):
    """构造最小 pdfs/ 目录族语料根并注入 corpus 模块。"""
    root = tmp_path / "pdfs"
    root.mkdir()
    monkeypatch.setattr(corpus_module, "CORPUS_ROOT", root)
    return root


def _touch(root, rel, content="x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content.encode("utf-8"))
    return p


# --- P2：年份解析必须落在合理区间（TB0207.xls 曾被解出 year=207）---

def test_parse_year_rejects_out_of_range_numbers():
    """文件名里的编号片段（如 TB0207 的 0207）不得误判为年份。"""
    assert corpus_module.parse_year("05-基础数据与曲线/TB0207.xls") is None
    assert corpus_module.parse_year("06-历年洪水资料/附件123.docx") is None

def test_parse_year_accepts_sane_years():
    import config as cfg
    lo, hi = corpus_module.MIN_YEAR, corpus_module.MAX_YEAR
    assert corpus_module.parse_year(f"06-历年洪水资料/{lo}年报告.pdf") == lo
    assert corpus_module.parse_year("04-2019年洪水(9-14)/汇报材料2021版.docx") in (2019, 2021)
    assert corpus_module.parse_year("01-核心文档/无数字.pdf") is None


# --- 分类：一级目录即指派 ---

def test_classify_by_top_dir():
    assert classify("01-核心文档-四案/02-调度规程.pdf") == "ds1"
    assert classify("06-历年洪水资料/08-历年洪水统计/弃水量统计表.xls") == "ds3"
    # ds5 必须有真实来源目录（回归：此前 ds5 空库）
    assert classify("02-安全鉴定与评价/03-桃曲坡等三座大坝安全评价报告.pdf") == "ds5"
    assert classify("03-施工图纸与设计/05-数字孪生项目建设方案.docx") == "ds5"


def test_classify_unknown_dir_raises():
    with pytest.raises(ValueError, match="Unknown corpus directory"):
        classify("99-未知目录/文件.pdf")


def test_scan_collects_all_unknown_dirs(fake_roots):
    _touch(fake_roots, "99-未知甲/a.pdf")
    _touch(fake_roots, "98-未知乙/b.pdf")
    with pytest.raises(ValueError) as ei:
        scan()
    msg = str(ei.value)
    assert "99-未知甲/a.pdf" in msg and "98-未知乙/b.pdf" in msg


# --- flood_event：目录段推导 ---

def test_flood_event_from_year_subdir(fake_roots):
    rel = "06-历年洪水资料/05-2013年洪水(7-22)/7-22防洪报告.doc"
    assert parse_flood_event(rel) == "2013-7"


def test_flood_event_guard_non_2021(fake_roots):
    """非 2021 年份目录内的同形片段不得错标 2021；2019 场次按档案目录标注 9-14 记为 2019-9。"""
    rel = "06-历年洪水资料/04-2019年洪水(9-14)/9-14洪水汇报.doc"
    assert parse_flood_event(rel) == "2019-9"


def test_flood_event_2021_granular(fake_roots):
    rel = "06-历年洪水资料/02-2021年洪水调度/9-25洪水/防汛抗洪纪实.doc"
    assert parse_flood_event(rel) == "2021-09"


def test_flood_event_none_outside_ds3(fake_roots):
    assert parse_flood_event("01-核心文档-四案/02-调度规程.pdf") is None


# --- 元数据推导 ---

def test_scan_metadata_derivation(fake_roots):
    _touch(fake_roots, "03-施工图纸与设计/02-施工图修改意见/溢洪道防汛办图纸审查意见.pdf", "数据")
    e = scan()[0]
    assert e.dataset_key == "ds5"
    assert e.source_format == "pdf"
    assert e.quality == "medium"          # PDF 文本层可能是扫描件 → medium 默认
    assert e.location == "溢洪道"
    assert e.responsible_dept == "防汛办"
    assert e.year is None                 # 路径中无 4 位年份
    assert e.sub_category == "施工图修改意见"   # 二级目录名（去编号前缀）


def test_scan_word_excel_quality_and_doctype(fake_roots):
    _touch(fake_roots, "06-历年洪水资料/08-历年洪水统计/弃水量统计表.xls")
    _touch(fake_roots, "06-历年洪水资料/02-2021年洪水调度/三场洪水调度过程汇报.doc")
    entries = {e.source_format: e for e in scan()}
    assert entries["excel"].quality == "high"
    assert entries["excel"].doc_type == "表格"
    assert entries["word"].quality == "high"
    assert entries["word"].flood_event == "2021-10"


def test_scan_skip_media_dirs(fake_roots):
    _touch(fake_roots, "09-图像与多媒体/01-洪水现场照片/photo.jpg")   # 非文档扩展名本就不扫
    _touch(fake_roots, "10-压缩包待处理/x.rar")
    _touch(fake_roots, "01-核心文档-四案/02-调度规程.pdf")             # 唯一可导行
    entries = scan()
    assert [e.rel for e in entries] == ["01-核心文档-四案/02-调度规程.pdf"]


# --- 去重：名字标记 + 哈希兜底 ---

def test_scan_marks_dup_named_files(fake_roots):
    _touch(fake_roots, "06-历年洪水资料/02-2021年洪水调度/10-3洪水/受损统计.xlsx", "same")
    _touch(fake_roots, "06-历年洪水资料/02-2021年洪水调度/10-3洪水/受损统计_dup.xlsx", "same")
    imp, dup = [], []
    for e in scan():
        (imp if e.skip_reason is None else dup).append(e)
    assert len(imp) == 1 and len(dup) == 1
    assert dup[0].duplicate_of == imp[0].rel


# --- CSV 与 manifest ---

@_needs_corpus
def test_mapping_csv_roundtrip():
    entries = scan()[:5]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        write_mapping_csv(entries, f.name)
    loaded = read_mapping_csv(f.name)
    assert len(loaded) == 5
    assert loaded[0]["rel"] == entries[0].rel
    os.unlink(f.name)


@_needs_corpus
def test_build_manifest_excludes_skipped():
    entries = scan()
    manifest = build_manifest(entries)
    assert all(e.skip_reason is None for e in manifest)


# --- CLI 入口 ---

def test_corpus_main_writes_mapping_csv(fake_roots, tmp_path, monkeypatch):
    _touch(fake_roots, "01-核心文档-四案/01-防洪抢险应急预案.pdf", "x")
    out_dir = tmp_path / "out"
    monkeypatch.setattr(corpus_module, "OUT_DIR", out_dir)
    corpus_module.main()
    mapping = out_dir / "mapping.csv"
    assert mapping.exists()
    rows = read_mapping_csv(mapping)
    assert rows[0]["dataset_key"] == "ds1"


# ---------------------------------------------------------------------------
# 真实语料冒烟（skipif 守卫）
# ---------------------------------------------------------------------------

@_needs_corpus
def test_scan_smoke():
    entries = scan()
    rels = [e.rel for e in entries]
    # 现场影像/压缩包不入扫描
    assert not any(r.startswith(("09-", "10-")) for r in rels)
    # 只含文档扩展名
    assert all(r.lower().endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx")) for r in rels)
    # 结构不变量
    dups = [e for e in entries if e.skip_reason == "duplicate"]
    assert len(entries) == len(build_manifest(entries)) + len(dups)
    # ds5 不再是空库（本次语料源切换的核心目标之一）
    by_ds = {k: 0 for k in ("ds1", "ds2", "ds3", "ds4", "ds5")}
    for e in build_manifest(entries):
        by_ds[e.dataset_key] += 1
    assert by_ds["ds5"] > 0, f"ds5 工程资料为空——目录指派可能回归：{by_ds}"
    assert by_ds["ds3"] > 30 and by_ds["ds1"] >= 4


@_needs_corpus
def test_scan_real_flood_events():
    entries = scan()
    e2013 = next(e for e in entries if "05-2013年洪水(7-22)" in e.rel)
    assert e2013.flood_event == "2013-7"
    e2021 = next(e for e in entries if "/10-3洪水/" in e.rel)
    assert e2021.flood_event == "2021-10"


@_needs_corpus
def test_dedupe_same_content():
    entries = scan()
    by_sha_ds = {}
    for e in entries:
        by_sha_ds.setdefault((e.sha256, e.dataset_key), []).append(e)
    for group in by_sha_ds.values():
        if len(group) > 1:
            non_dup = [e for e in group if e.duplicate_of is None]
            assert len(non_dup) == 1
            for e in group:
                if e.duplicate_of:
                    assert e.duplicate_of == non_dup[0].rel
