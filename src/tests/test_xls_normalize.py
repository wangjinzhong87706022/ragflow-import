"""test_xls_normalize.py — xls_normalize 预处理规范化模块单元测试。

全 mock 不触网；构造内存中的 .xls/.xlsx 样本验证规范化逻辑。
"""
from __future__ import annotations

import io
import pathlib
import tempfile

import pytest

# xls_normalize 依赖 pandas + openpyxl，缺失时跳过整组测试
try:
    import pandas as pd
    from openpyxl import Workbook, load_workbook
    import xls_normalize
except ImportError:
    pytest.skip("xls_normalize 依赖（pandas/openpyxl）未安装", allow_module_level=True)


# ---------------------------------------------------------------------------
# 辅助：构造测试样本
# ---------------------------------------------------------------------------

def _make_biff_xls_with_header_rows(tmp_path: pathlib.Path) -> pathlib.Path:
    """构造一个模拟水文报表的 .xls：表头上方有标题行和单位行。

    用 openpyxl 生成 .xlsx 再改后缀不行（BIFF 和 OOXML 格式不同），
    所以这里用 pandas + xlwt 写真正的 .xls。但 xlwt 可能没装——
    改用 openpyxl 生成 .xlsx 作为规范化输入的替身（normalize 只看内容不看格式）。
    """
    # 构造原始网格：标题行 + 空行 + 单位行 + 表头行 + 数据行
    data = {
        "Sheet1": [
            [None, None, None],
            ["柳林断面流量统计表", None, None],
            [None, None, None],
            ["万立米", None, None],
            ["日期", "报汛时间", "库水位"],
            [40752, "20:00:00", 772.22],
            [40753, "08:00:00", 773.10],
        ]
    }
    df = pd.DataFrame(data["Sheet1"])
    path = tmp_path / "flood_test.xls"
    # 用 pandas 写 .xls（通过 openpyxl 引擎写 .xlsx 再改后缀，normalize 只看内容）
    df.to_excel(path, index=False, header=False, engine="openpyxl")
    return path


def _make_simple_xlsx(tmp_path: pathlib.Path) -> pathlib.Path:
    """构造一个简单的 .xlsx（非 BIFF），is_biff_xls 应返回 False。"""
    wb = Workbook()
    ws = wb.active
    ws.append(["H1", "H2"])
    ws.append(["a1", "b1"])
    path = tmp_path / "simple.xlsx"
    wb.save(path)
    return path


def _make_grid_xlsx(path: pathlib.Path, rows: list[list]) -> pathlib.Path:
    """把给定网格写成 .xlsx 样本（normalize 只看内容不看容器格式）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(path, index=False, header=False, engine="openpyxl")
    return path


# ---------------------------------------------------------------------------
# is_biff_xls
# ---------------------------------------------------------------------------

class TestIsBiffXls:
    def test_xlsx_is_not_biff(self, tmp_path):
        path = _make_simple_xlsx(tmp_path)
        assert xls_normalize.is_biff_xls(path) is False

    def test_nonexistent_file_returns_false(self, tmp_path):
        assert xls_normalize.is_biff_xls(tmp_path / "no_such_file.xls") is False


# ---------------------------------------------------------------------------
# _detect_header_index
# ---------------------------------------------------------------------------

class TestDetectHeaderIndex:
    def test_header_at_row_0(self):
        grid = [["日期", "流量"], [40752, 16.1]]
        assert xls_normalize._detect_header_index(grid) == 0

    def test_header_after_title_and_unit(self):
        grid = [
            [None, None, None],
            ["柳林断面流量统计表", None, None],
            [None, None, None],
            ["万立米", None, None],
            ["日期", "报汛时间", "库水位"],
            [40752, "20:00:00", 772.22],
        ]
        assert xls_normalize._detect_header_index(grid) == 4

    def test_all_single_col_rows_returns_0(self):
        grid = [["标题"], ["单位"], ["数据"]]
        assert xls_normalize._detect_header_index(grid) == 0


# ---------------------------------------------------------------------------
# _clean_cell
# ---------------------------------------------------------------------------

class TestCleanCell:
    def test_none_returns_none(self):
        assert xls_normalize._clean_cell(None) is None

    def test_nan_returns_none(self):
        assert xls_normalize._clean_cell(float("nan")) is None

    def test_empty_string_returns_none(self):
        assert xls_normalize._clean_cell("   ") is None

    def test_integer_float_returns_int(self):
        result = xls_normalize._clean_cell(40752.0)
        # 必须断言类型：40752.0 == 40752 为真，仅比等值无法发现分支未命中
        # （旧实现含 `"." not in str(v)` 死条件，见 2026-09-12 评审 P2-6）。
        assert isinstance(result, int), f"应为 int，实际 {type(result).__name__}"
        assert result == 40752

    def test_non_integer_float_unchanged(self):
        assert xls_normalize._clean_cell(772.22) == 772.22

    def test_string_strips_control_chars(self):
        assert xls_normalize._clean_cell("hello\x00world") == "hello world"


# ---------------------------------------------------------------------------
# _restore_dates
# ---------------------------------------------------------------------------

class TestRestoreDates:
    def test_date_serial_converted(self):
        header = ["日期", "流量"]
        row = [40752, 16.1]
        out = xls_normalize._restore_dates(header, row)
        assert out[0] == "2011-07-29"
        assert out[1] == 16.1

    def test_non_date_column_unchanged(self):
        header = ["流量", "水位"]
        row = [40752, 772.22]
        out = xls_normalize._restore_dates(header, row)
        assert out[0] == 40752  # 列名不含"日期/时间"，不转换

    def test_out_of_range_serial_unchanged(self):
        header = ["日期", "流量"]
        row = [100, 16.1]  # 100 不在 [20000, 60000] 范围内
        out = xls_normalize._restore_dates(header, row)
        assert out[0] == 100

    def test_row_longer_than_header_not_truncated(self):
        """数据行比表头长时不得丢尾部单元格（旧实现用 zip 会截断整行，评审 P2）。"""
        header = ["日期", "流量"]
        row = [40752, 16.1, "尾部备注"]
        out = xls_normalize._restore_dates(header, row)
        assert out == ["2011-07-29", 16.1, "尾部备注"], f"尾部单元格丢失: {out}"


# ---------------------------------------------------------------------------
# _safe_sheet_name
# ---------------------------------------------------------------------------

class TestSafeSheetName:
    def test_strips_illegal_chars(self):
        assert ":" not in xls_normalize._safe_sheet_name("a:b/c", set())
        assert "\\" not in xls_normalize._safe_sheet_name("a:b\\c", set())

    def test_truncates_to_31(self):
        name = "A" * 50
        result = xls_normalize._safe_sheet_name(name, set())
        assert len(result) <= 31

    def test_dedup_with_suffix(self):
        used = {"sheet1"}
        result = xls_normalize._safe_sheet_name("sheet1", used)
        assert result.lower() != "sheet1"
        assert "sheet1" in result.lower()


# ---------------------------------------------------------------------------
# normalize_if_needed
# ---------------------------------------------------------------------------

class TestNormalizeIfNeeded:
    def test_xlsx_returns_unchanged(self, tmp_path):
        path = _make_simple_xlsx(tmp_path)
        out_dir = tmp_path / "out"
        result_path, was_normalized = xls_normalize.normalize_if_needed(path, out_dir)
        assert was_normalized is False
        assert result_path == path


# ---------------------------------------------------------------------------
# normalize_xls（端到端，用 openpyxl 生成的 .xlsx 内容做替身）
# ---------------------------------------------------------------------------

class TestNormalizeXls:
    def test_normalize_produces_xlsx_with_correct_header(self, tmp_path):
        """规范化后表头应在第 1 行，日期序列号应被还原。"""
        # 构造原始网格（标题行 + 单位行 + 表头 + 数据）
        src = tmp_path / "flood.xls"
        df = pd.DataFrame([
            [None, None, None],
            ["柳林断面流量统计表", None, None],
            ["万立米", None, None],
            ["日期", "报汛时间", "库水位"],
            [40752, "20:00:00", 772.22],
        ])
        df.to_excel(src, index=False, header=False, engine="openpyxl")

        out_dir = tmp_path / "normalized"
        dst = xls_normalize.normalize_xls(src, out_dir)

        assert dst.exists()
        assert dst.suffix == ".xlsx"

        wb = load_workbook(dst)
        ws = wb.worksheets[0]
        # 表头应在第 1 行
        assert ws.cell(1, 1).value == "日期"
        assert ws.cell(1, 2).value == "报汛时间"
        assert ws.cell(1, 3).value == "库水位"
        # 日期序列号应被还原
        assert ws.cell(2, 1).value == "2011-07-29"
        # sheet 名应含上下文
        assert "柳林" in ws.title or "万立米" in ws.title

    def test_normalize_skips_empty_sheets(self, tmp_path):
        src = tmp_path / "empty.xls"
        df = pd.DataFrame()
        df.to_excel(src, index=False, engine="openpyxl")

        out_dir = tmp_path / "normalized"
        dst = xls_normalize.normalize_xls(src, out_dir)
        assert dst.exists()

    def test_same_stem_in_different_dirs_do_not_collide(self, tmp_path):
        """不同目录下的同名 .xls 必须产出不同文件，否则互相覆盖（评审 P2-8）。"""
        rows = [["标题"], ["日期", "流量"], [40752, 16.1]]
        a = _make_grid_xlsx(tmp_path / "dirA" / "同名.xls", rows)
        b = _make_grid_xlsx(tmp_path / "dirB" / "同名.xls", rows)

        out_dir = tmp_path / "normalized"
        dst_a = xls_normalize.normalize_xls(a, out_dir)
        dst_b = xls_normalize.normalize_xls(b, out_dir)

        assert dst_a != dst_b, f"同名文件产物路径冲突: {dst_a}"
        assert dst_a.exists() and dst_b.exists()


# ---------------------------------------------------------------------------
# _read_grids：引擎优先级（评审 P2-9）
# ---------------------------------------------------------------------------

class TestReadGrids:
    def test_prefers_calamine_when_requested(self, monkeypatch, tmp_path):
        """已知 BIFF .xls 时必须先试 calamine——纯异常降级会漏掉 xlrd 静默误读。"""
        calls: list = []

        def fake_read_excel(path, sheet_name=None, header=None, engine=None):
            calls.append(engine)
            if engine == "calamine":
                return {"S": pd.DataFrame([["h1", "h2"]])}
            raise RuntimeError("默认引擎不应在 calamine 之前被调用")

        monkeypatch.setattr(xls_normalize.pd, "read_excel", fake_read_excel)
        grids = xls_normalize._read_grids(tmp_path / "x.xls", prefer_calamine=True)

        assert calls[0] == "calamine", f"首个引擎应为 calamine，实际顺序 {calls}"
        assert "S" in grids

    def test_falls_back_to_calamine_on_default_failure(self, monkeypatch, tmp_path):
        """默认引擎抛错时仍应回退 calamine（保留原有降级能力）。"""
        calls: list = []

        def fake_read_excel(path, sheet_name=None, header=None, engine=None):
            calls.append(engine)
            if engine is None:
                raise RuntimeError("default engine failed")
            return {"S": pd.DataFrame([["h1"]])}

        monkeypatch.setattr(xls_normalize.pd, "read_excel", fake_read_excel)
        xls_normalize._read_grids(tmp_path / "x.xls", prefer_calamine=False)

        assert calls[:2] == [None, "calamine"]


# ---------------------------------------------------------------------------
# CLI（评审 P2-7：旧实现 `Workbook.__self__` 在成功路径必抛 AttributeError）
# ---------------------------------------------------------------------------

class TestCli:
    def test_main_prints_sheets_without_crashing(self, monkeypatch, tmp_path, capsys):
        out_dir = tmp_path / "out"
        dst = out_dir / "fake.abc12345.normalized.xlsx"
        dst.parent.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        ws = wb.active
        ws.title = "柳林断面"
        ws.append(["日期", "流量"])
        wb.save(dst)

        monkeypatch.setattr(xls_normalize, "is_biff_xls", lambda p: True)
        monkeypatch.setattr(xls_normalize, "normalize_xls", lambda s, o: dst)
        monkeypatch.setattr(
            "sys.argv",
            ["xls_normalize", str(tmp_path / "fake.xls"), "--out-dir", str(out_dir)],
        )

        xls_normalize.main()   # 旧实现会在此抛 AttributeError

        out = capsys.readouterr().out
        assert "柳林断面" in out, f"CLI 应打印 sheet 名，实际输出: {out!r}"