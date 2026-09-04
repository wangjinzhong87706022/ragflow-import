"""
Controlled vocabulary for the 桃曲坡 Reservoir KB RAGFlow import.
17 TAB-separated (description, tags) rows: 5 knowledge types + 12 flood events.
事件名与 config.FLOOD_EVENT_BY_SUBDIR / METADATA_SCHEMA 枚举保持一致
（2019 场次为 2019-9，2021 年另有 09 子场；2018-8 系档案 2008 目录名纠偏后
新增，2008-8 保留为历史值域；2010-7/2010-8 系 F1 批次二按内容归场新增——
2001年洪水过程线.xls 实为 20100724、2010年下泄水量统计.xls 实为 20100813；
2011-7 系 F1 批次三按内容归场新增——洪水过程(3).xls 实为 20110729，
错放在 05-2013年洪水(7-22) 目录下）。
"""

from pathlib import Path

VOCAB_ROWS = [
    ("规程预案", "规程预案"),
    ("基础数据", "基础数据"),
    ("洪水资料", "洪水资料"),
    ("组织管理", "组织管理"),
    ("工程资料", "工程资料"),
    ("2021-10", "2021-10"),
    ("2021-09", "2021-09"),
    ("2020-8", "2020-8"),
    ("2019-9", "2019-9"),
    ("2018-8", "2018-8"),
    ("2013-7", "2013-7"),
    ("2011-7", "2011-7"),
    ("2010-8", "2010-8"),
    ("2010-7", "2010-7"),
    ("2008-8", "2008-8"),
    ("其他", "其他"),
    ("历年统计", "历年统计"),
]


def write_vocab_txt(path: Path) -> None:
    """Write VOCAB_ROWS as UTF-8 TAB-separated lines. Creates parent dirs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for desc, tags in VOCAB_ROWS:
            # Mirror tag.py: tags column replaces '.' with '_' (no-op here — no dots)
            f.write(f"{desc}\t{tags}\n")


def parse_vocab_txt(path: Path) -> list[tuple[str, str]]:
    """
    Parse a vocab txt file and return list of (description, tags) tuples.
    Mirrors tag.py chunk() delimiter detection:
      - count TAB vs comma delimiters across all lines
      - use TAB if tab_count >= comma_count, else comma
    """
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    comma_count = sum(1 for line in lines if len(line.split(",")) == 2)
    tab_count = sum(1 for line in lines if "\t" in line)
    delimiter = "\t" if tab_count >= comma_count else ","

    result = []
    for line in lines:
        if not line.strip():
            continue
        parts = line.split(delimiter)
        if len(parts) == 2:
            result.append((parts[0].strip(), parts[1].strip()))
    return result
