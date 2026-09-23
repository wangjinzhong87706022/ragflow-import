"""Tests for jhc_prepare：扫描件分组纯函数 + 导入计划构建。"""
import sys; sys.path.insert(0, "..")
from pathlib import Path

from jhc_prepare import (
    MERGED_TAG,
    build_plan,
    group_images,
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
    rels = ["记录表/微信图片_20260422102021.jpg", "记录表/微信图片_20260422105827.jpg",
            "记录表/微信图片_20260422105753_225.jpg"]
    pages = group_images(rels)["记录表/记录表"]
    assert pages == rels  # 目录名主干归组，前 14 位时间戳升序


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
    p.write_bytes(b"x")


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
