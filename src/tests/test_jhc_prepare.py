"""Tests for jhc_prepare：扫描件分组纯函数 + 导入计划构建。"""
import sys; sys.path.insert(0, "..")
from pathlib import Path

import pytest

from jhc_prepare import (
    MERGED_TAG,
    build_plan,
    group_images,
    merge_images_to_pdf,
    strip_page_suffix,
)


# ---------------------------------------------------------------------------
# 页号剥离
# ---------------------------------------------------------------------------

def test_strip_underscore_page():
    assert strip_page_suffix("附件2：实施方案_01") == ("附件2：实施方案", 1)
    assert strip_page_suffix("证件_0002") == ("证件", 2)


def test_strip_chinese_page_and_paren():
    assert strip_page_suffix("批复第3页") == ("批复", 3)
    assert strip_page_suffix("报告(12)") == ("报告", 12)
    assert strip_page_suffix("报告（12）") == ("报告", 12)


def test_strip_bare_trailing_digits():
    assert strip_page_suffix("机电设备运行记录——2025年张家山水库管理处1") == (
        "机电设备运行记录——2025年张家山水库管理处", 1,
    )


def test_no_page_suffix_keeps_stem():
    assert strip_page_suffix("大坝剖面图") == ("大坝剖面图", None)


def test_pure_numeric_uses_parent_name():
    # 1.png…10.png 拆页批复：主干=父目录名，文件名数字=页码
    assert strip_page_suffix("10", parent_name="实施方案批复") == ("实施方案批复", 10)


def test_wechat_timestamp_groups_by_parent():
    # 微信拍图时间戳命名：回归父目录归组，时间戳升序=拍摄序
    assert strip_page_suffix("微信图片_20260422102021", "2号机组巡查记录表") == ("2号机组巡查记录表", 20260422102021)
    assert strip_page_suffix("微信图片_20260422105753_225", "d") == ("d", 20260422105753225)
    # 双重后缀：时间戳_流水号_图号（真实语料形态）
    assert strip_page_suffix("微信图片_20260422105809_240_2", "d") == ("d", 202604221058092402)
    # 构造乱序输入，显式断言按时间戳数值升序（评审 #14：避免输入恰好有序导致误绿）
    rels_in = [
        "记录表/微信图片_20260422105827.jpg",       # 中
        "记录表/微信图片_20260422105753_225.jpg",   # 大（17 位拼接）
        "记录表/微信图片_20260422102021.jpg",       # 小
    ]
    expected = [
        "记录表/微信图片_20260422102021.jpg",
        "记录表/微信图片_20260422105827.jpg",
        "记录表/微信图片_20260422105753_225.jpg",
    ]
    pages = group_images(rels_in)["记录表/记录表"]
    assert pages == expected


# ---------------------------------------------------------------------------
# 分组与排序
# ---------------------------------------------------------------------------

def test_group_multi_page_scans_in_order():
    rels = [f"计划处/安全防护/实施方案/附件2：实施方案_{i:02d}.png" for i in range(1, 14)]
    groups = group_images(rels)
    assert list(groups) == ["计划处/安全防护/实施方案/附件2：实施方案"]
    pages = groups["计划处/安全防护/实施方案/附件2：实施方案"]
    assert pages[0].endswith("_01.png") and pages[-1].endswith("_13.png")


def test_numeric_sort_not_lexicographic():
    rels = ["d/记录10.jpg", "d/记录2.jpg", "d/记录1.jpg"]
    pages = group_images(rels)["d/记录"]
    assert [Path(p).name for p in pages] == ["记录1.jpg", "记录2.jpg", "记录10.jpg"]


def test_mixed_case_ext_same_group():
    # 批复1.jpg / 批复10.JPG 大写扩展名不拆组、按数值序
    rels = ["d/批复10.JPG", "d/批复1.jpg", "d/批复2.jpg"]
    pages = group_images(rels)["d/批复"]
    assert [Path(p).name for p in pages] == ["批复1.jpg", "批复2.jpg", "批复10.JPG"]


def test_lone_image_is_own_group():
    groups = group_images(["d/大坝剖面图.png"])
    assert groups == {"d/大坝剖面图": ["d/大坝剖面图.png"]}


def test_different_dirs_never_merge():
    rels = ["a/方案_01.png", "b/方案_02.png"]
    assert len(group_images(rels)) == 2


# ---------------------------------------------------------------------------
# build_plan（tmp_path 构造迷你语料树）
# ---------------------------------------------------------------------------

def _touch(root: Path, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(f"content:{rel}".encode())   # 每文件内容唯一：避免被同哈希图片去重误折叠


def test_build_plan_routes_docs_and_merged_pdf(tmp_path):
    _touch(tmp_path, "工程管理处/预案.docx")
    _touch(tmp_path, "计划处/安全防护/批复_01.png")
    _touch(tmp_path, "计划处/安全防护/批复_02.png")
    _touch(tmp_path, "泾惠渠灌区干支渠道安全防护实施方案.pdf")   # 根目录 → 计划处
    plan = build_plan(tmp_path)
    assert ("工程管理处/预案.docx", "工程管理处/预案.docx") in plan["docs"]
    assert ("泾惠渠灌区干支渠道安全防护实施方案.pdf",
            "计划处/泾惠渠灌区干支渠道安全防护实施方案.pdf") in plan["docs"]
    assert len(plan["merges"]) == 1
    out_rel, pages = plan["merges"][0]
    assert out_rel == f"计划处/安全防护/批复{MERGED_TAG}.pdf"
    assert pages == ["计划处/安全防护/批复_01.png", "计划处/安全防护/批复_02.png"]


def test_build_plan_archives_and_unsupported(tmp_path):
    _touch(tmp_path, "工程管理处/包.zip")
    (tmp_path / "工程管理处" / "包").mkdir(parents=True)      # 同名解压目录存在
    _touch(tmp_path, "工程管理处/孤包.rar")                    # 无同名目录
    _touch(tmp_path, "工程管理处/x/记录.json")
    _touch(tmp_path, "工程管理处/y/通知.wps")
    plan = build_plan(tmp_path)
    skips = dict(plan["skips"])
    assert skips["工程管理处/包.zip"] == "压缩包内容已解压为同名目录"
    assert "needs_review" in skips["工程管理处/孤包.rar"]
    assert plan["reviews"] == ["工程管理处/孤包.rar"]
    assert skips["工程管理处/x/记录.json"] == "不支持的格式 .json"
    assert skips["工程管理处/y/通知.wps"] == "不支持的格式 .wps"


def test_build_plan_ignores_junk_dirs(tmp_path):
    _touch(tmp_path, "计划处/__MACOSX/._hidden.pdf")
    assert build_plan(tmp_path)["docs"] == []

def test_build_plan_ignores_junk_files(tmp_path):
    # Thumbs.db / .DS_Store 是文件非目录，按文件名过滤（评审 #11）
    _touch(tmp_path, "计划处/Thumbs.db")
    _touch(tmp_path, "计划处/.DS_Store")
    plan = build_plan(tmp_path)
    assert plan["docs"] == []
    assert plan["skips"] == []


# ---------------------------------------------------------------------------
# merge_images_to_pdf I/O（评审 #7：此前合并路径零覆盖）
# ---------------------------------------------------------------------------

def test_merge_png_happy_path(tmp_path):
    pytest.importorskip("img2pdf")
    pytest.importorskip("PIL")
    from PIL import Image
    p1 = tmp_path / "p1.png"
    p2 = tmp_path / "p2.png"
    Image.new("RGB", (10, 10), "white").save(p1)
    Image.new("RGB", (10, 10), "black").save(p2)
    out = tmp_path / "merged.pdf"
    merge_images_to_pdf([p1, p2], out)
    assert out.read_bytes()[:5] == b"%PDF-"


def test_merge_tif_uses_tmpfile_and_cleans_up(tmp_path):
    pytest.importorskip("img2pdf")
    pytest.importorskip("PIL")
    from PIL import Image
    tif = tmp_path / "scan.tif"
    Image.new("RGB", (10, 10), "white").save(tif, format="TIFF")
    out = tmp_path / "merged.pdf"
    merge_images_to_pdf([tif], out)
    assert out.read_bytes()[:5] == b"%PDF-"
    # 临时转码文件应已清理
    tmp_dir = out.parent / ".jhc_tmp"
    assert not tmp_dir.exists() or not any(tmp_dir.iterdir())


def test_apply_plan_reports_failures(tmp_path):
    """apply_plan 返回失败列表，不中断后续（评审 #10）"""
    import jhc_prepare
    src = tmp_path / "src"
    staging = tmp_path / "staging"
    src.mkdir()
    (src / "ok.docx").write_bytes(b"x")
    plan = {"docs": [("ok.docx", "ok.docx"), ("missing.docx", "missing.docx")],
            "merges": [], "skips": [], "reviews": []}
    copied, merged, failures = jhc_prepare.apply_plan(plan, src, staging)
    assert copied == 1
    assert merged == 0
    assert len(failures) == 1
    assert "missing.docx" in failures[0][0]


# ---------------------------------------------------------------------------
# 质量处置：分诊空白剔除 / 双份目录 PDF 去重 / 超限图重压
# ---------------------------------------------------------------------------

import json  # noqa: E402

from jhc_prepare import (  # noqa: E402
    _needs_recompress,
    _recompress_tmpfile,
    load_blank_rels,
    pair_duplicate_pdfs,
)


def _write_triage(path: Path, rows: list[dict], bad_line: bool = False) -> None:
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    if bad_line:
        text += "\n{bad json"
    path.write_text(text + "\n", encoding="utf-8")


def test_load_blank_rels(tmp_path):
    triage = tmp_path / "t.jsonl"
    _write_triage(triage, [
        {"rel": "d/a.jpg", "content": "blank"},
        {"rel": "d/b.jpg", "content": "text"},
        {"rel": "d/c.png", "content": "blank"},
    ], bad_line=True)
    assert load_blank_rels(triage) == {"d/a.jpg", "d/c.png"}
    assert load_blank_rels(None) == set()
    assert load_blank_rels(tmp_path / "missing.jsonl") == set()


def test_build_plan_filters_blank_pages(tmp_path):
    for i in (1, 2, 3):
        _touch(tmp_path, f"d/记录{i}.jpg")
    triage = tmp_path / "t.jsonl"
    _write_triage(triage, [{"rel": "d/记录2.jpg", "content": "blank"}])
    plan = build_plan(tmp_path, triage)
    assert len(plan["merges"]) == 1
    _, pages = plan["merges"][0]
    assert pages == ["d/记录1.jpg", "d/记录3.jpg"]
    assert ("d/记录2.jpg", "VLM 分诊为空白页，合并时剔除") in plan["skips"]


def test_build_plan_all_blank_group_skipped(tmp_path):
    _touch(tmp_path, "d/封面.png")
    triage = tmp_path / "t.jsonl"
    _write_triage(triage, [{"rel": "d/封面.png", "content": "blank"}])
    plan = build_plan(tmp_path, triage)
    assert plan["merges"] == []
    assert any("合并组全部为空白页" in reason for _, reason in plan["skips"])


def _make_pdf(path: Path, pages: int = 1) -> None:
    """用 img2pdf 造真 PDF（页数规则需要 pypdf 能读）"""
    import img2pdf
    from PIL import Image
    imgs = []
    for i in range(pages):
        p = path.parent / f"_mk{i}.png"
        Image.new("RGB", (20, 20), "white").save(p)
        imgs.append(str(p))
    path.write_bytes(img2pdf.convert(imgs))
    for p in imgs:
        Path(p).unlink()


def _save_img(path: Path, tag: int) -> None:
    """内容唯一的测试图（纯白图会同哈希被去重折叠）"""
    from PIL import Image
    Image.new("RGB", (20, 20), (tag % 256, (tag * 7) % 256, (tag * 13) % 256)).save(path)


def test_build_plan_dedup_pdf_by_stem(tmp_path):
    # 旧转换 PDF 与图组同主干且页数精确相等 → 弃 PDF（页数须真读，故造真 PDF）
    pytest.importorskip("img2pdf")
    pytest.importorskip("PIL")
    (tmp_path / "d").mkdir()
    for i in (1, 2):
        _save_img(tmp_path / "d" / f"批复{i}.jpg", tag=i)
    _make_pdf(tmp_path / "d" / "批复.pdf", pages=2)
    plan = build_plan(tmp_path, None)
    assert plan["docs"] == []
    dup = [(r, why) for r, why in plan["skips"] if "旧转换 PDF" in why]
    assert dup and dup[0][0] == "d/批复.pdf"


def test_build_plan_stem_match_count_mismatch_goes_review(tmp_path):
    """概算书教训：stem 命中但 PDF 页数 ≠ 图组页数 → 不弃、进 review。"""
    pytest.importorskip("img2pdf")
    pytest.importorskip("PIL")
    (tmp_path / "d").mkdir()
    for i in (1, 2):
        _save_img(tmp_path / "d" / f"批复{i}.jpg", tag=i)
    _make_pdf(tmp_path / "d" / "批复.pdf", pages=1)      # 1 页 vs 图组 2 页
    plan = build_plan(tmp_path, None)
    assert [r for r, _ in plan["docs"]] == ["d/批复.pdf"]
    assert any("批复.pdf" in r and "页数不符" in r for r in plan["reviews"])
    assert not any("旧转换 PDF" in why for _, why in plan["skips"])


def test_build_plan_cover_plus_pages_dir_total_dedup(tmp_path):
    """供暖泵房型：封面独立组 + 内页组，PDF 页数 == 目录页图总数 → 弃。"""
    pytest.importorskip("img2pdf")
    pytest.importorskip("PIL")
    (tmp_path / "d").mkdir()
    _save_img(tmp_path / "d" / "封面.jpg", tag=10)
    for i in (1, 2):
        _save_img(tmp_path / "d" / f"记录{i}.jpg", tag=20 + i)
    _make_pdf(tmp_path / "d" / "记录表.pdf", pages=3)     # 1 + 2 = 目录总图数
    plan = build_plan(tmp_path, None)
    assert plan["docs"] == []
    assert any("目录页图整体重复" in why for _, why in plan["skips"])


def test_build_plan_dedup_pdf_by_page_count(tmp_path):
    pytest.importorskip("img2pdf")
    pytest.importorskip("PIL")
    (tmp_path / "d").mkdir()
    _save_img(tmp_path / "d" / "扫描A1.jpg", tag=1)
    _save_img(tmp_path / "d" / "扫描A2.jpg", tag=2)
    _make_pdf(tmp_path / "d" / "原始扫描件.pdf", pages=2)   # 主干不同 → 走页数规则
    plan = build_plan(tmp_path, None)
    assert plan["docs"] == []
    assert any("旧转换 PDF" in why for _, why in plan["skips"])


def test_build_plan_pdf_page_mismatch_goes_review(tmp_path):
    pytest.importorskip("img2pdf")
    pytest.importorskip("PIL")
    (tmp_path / "d").mkdir()
    for i in (1, 2):
        _save_img(tmp_path / "d" / f"记录{i}.jpg", tag=i)
    _make_pdf(tmp_path / "d" / "另一份材料.pdf", pages=1)    # 1 页 vs 图组 2 页
    plan = build_plan(tmp_path, None)
    assert [r for r, _ in plan["docs"]] == ["d/另一份材料.pdf"]   # 不自动弃
    assert any("另一份材料.pdf" in r and "页数不符" in r for r in plan["reviews"])


def test_pair_duplicate_pdfs_no_image_dir_untouched(tmp_path):
    # 无图组目录的 PDF 原样保留、不进 review
    _touch(tmp_path, "d/单独文件.pdf")
    kept, dup, reviews = pair_duplicate_pdfs([("d/单独文件.pdf", "d/单独文件.pdf")],
                                             [], tmp_path)
    assert kept == [("d/单独文件.pdf", "d/单独文件.pdf")]
    assert dup == [] and reviews == []


def test_build_plan_dedup_identical_images(tmp_path):
    """同目录同哈希双份副本（xx(1).jpg）去重；跨目录同内容不去重。"""
    (tmp_path / "d").mkdir()
    (tmp_path / "e").mkdir()
    (tmp_path / "d" / "表_01.jpg").write_bytes(b"same-bytes")
    (tmp_path / "d" / "表_01(1).jpg").write_bytes(b"same-bytes")
    (tmp_path / "d" / "表_02.jpg").write_bytes(b"other-bytes")
    (tmp_path / "e" / "表_01.jpg").write_bytes(b"same-bytes")   # 跨目录不去重
    plan = build_plan(tmp_path, None)
    assert plan["merges"][0][1] == ["d/表_01.jpg", "d/表_02.jpg"]
    assert any("(1).jpg" in r and "同哈希" in why for r, why in plan["skips"])
    # e/ 独立成组保留
    assert ["e/表_01.jpg"] in [rels for _, rels in plan["merges"]]


def test_build_plan_dedup_identical_images_keeps_distinct_same_size(tmp_path):
    # 同目录同大小但内容不同 → 不去重（同组保留两页）
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "记录_01.jpg").write_bytes(b"AAAA")
    (tmp_path / "d" / "记录_02.jpg").write_bytes(b"BBBB")
    plan = build_plan(tmp_path, None)
    assert plan["merges"][0][1] == ["d/记录_01.jpg", "d/记录_02.jpg"]
    assert not any("同哈希" in why for _, why in plan["skips"])


def test_needs_recompress_rules(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image
    small = tmp_path / "s.jpg"
    Image.new("RGB", (100, 80), "white").save(small)
    big = tmp_path / "b.png"
    Image.new("RGB", (4000, 100), "white").save(big)
    tif = tmp_path / "x.tif"
    Image.new("RGB", (10, 10), "white").save(tif, format="TIFF")
    assert not _needs_recompress(small)
    assert _needs_recompress(big)          # 长边超 3500
    assert _needs_recompress(tif)          # 非 jpg/png
    assert _needs_recompress(tmp_path / "missing.jpg")   # 头读不出 → True


def test_recompress_downscales_and_writes_jpeg(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image
    src = tmp_path / "big.png"
    Image.new("RGB", (7000, 1000), "white").save(src)
    out = _recompress_tmpfile(src, tmp_path / "tmp", 0)
    with Image.open(out) as im:
        assert im.format == "JPEG"
        assert max(im.size) <= 3500
        assert abs(im.size[0] / im.size[1] - 7.0) < 0.01   # 宽高比保持
