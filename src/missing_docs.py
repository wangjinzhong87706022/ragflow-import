"""
missing_docs.py — 细化"语料源里存在、但未出现在 RAGFlow 导出中"的非图片文件

对每个缺失文件按以下顺序判定:
1. junk      — desktop.ini / ~$ 锁文件 / _import_report 等本就该排除
2. archive   — rar/zip/tar 等压缩包(RAGFlow 不支持)
3. wps       — .wps 原件(语料源格式), 导出侧已有同名 stem 的转换件
4. renamed   — 导出侧存在同 stem 或高相似度文件名(改名/fixed_ 前缀等)
5. likely-missing — 真正没进库的文档, 需人工决定是否补导
6. name-diff — 同名匹配失败但有可疑近似名(差异字符), 单独列出

输出 out/qc/missing_docs.md
"""
from __future__ import annotations

import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import OUT_DIR

SRC = Path(r"E:\bak\资料收集")
EXP = Path(r"F:\ragflow\ragflow")
IMG = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
JUNK_NAMES = ("desktop.ini", "thumbs.db")
JUNK_PREFIX = "~$"
ARCHIVE = {".rar", ".zip", ".7z", ".tar", ".gz"}


def main() -> None:
    exp_files = [p for p in EXP.rglob("*") if p.is_file()]
    exp_names = {p.name for p in exp_files}
    exp_stems = {p.stem for p in exp_files}
    # 归一化名(去 fixed_ 前缀、去全角空格、小写) → 原导出名
    def norm(name: str) -> str:
        s = name
        if s.startswith("fixed_"):
            s = s[len("fixed_"):]
        s = s.replace("　", "").replace(" ", "").lower()
        return s
    exp_norm = {}
    for p in exp_files:
        exp_norm.setdefault(norm(p.name), p.name)

    buckets: dict[str, list[tuple[str, str]]] = {
        "junk": [], "archive": [], "wps": [], "renamed": [],
        "name-diff": [], "likely-missing": [],
    }
    all_exp_names = list(exp_names)

    for p in sorted(SRC.rglob("*")):
        if not p.is_file() or p.suffix.lower() in IMG:
            continue
        rel = p.relative_to(SRC).as_posix()
        name = p.name
        if name in exp_names:
            continue  # 已导出(主对比已覆盖)
        if name.startswith(JUNK_PREFIX) or name.lower() in JUNK_NAMES:
            buckets["junk"].append((rel, "锁文件/系统文件"))
        elif p.suffix.lower() in ARCHIVE:
            buckets["archive"].append((rel, "压缩包, RAGFlow 不支持"))
        elif p.suffix.lower() == ".wps":
            # 导出侧同 stem(转 docx/pdf) 或归一化同名
            if p.stem in exp_stems:
                cands = [q.name for q in exp_files if q.stem == p.stem]
                buckets["wps"].append((rel, f"导出侧有转换件: {cands}"))
            elif norm(name) in exp_norm:
                buckets["wps"].append((rel, f"导出侧归一化同名: {exp_norm[norm(name)]}"))
            else:
                buckets["likely-missing"].append((rel, ".wps 且导出侧无同名转换件"))
        else:
            if norm(name) in exp_norm:
                buckets["renamed"].append((rel, f"归一化同名: {exp_norm[norm(name)]}"))
            else:
                close = difflib.get_close_matches(name, all_exp_names, n=3, cutoff=0.6)
                stem_close = difflib.get_close_matches(p.stem, [Path(e).stem for e in all_exp_names],
                                                       n=3, cutoff=0.7)
                if close:
                    buckets["name-diff"].append((rel, f"近似名: {close}"))
                elif stem_close:
                    buckets["name-diff"].append((rel, f"近似 stem: {stem_close}"))
                else:
                    buckets["likely-missing"].append((rel, ""))

    out = ["# 语料源未导出非图片文件 · 细化分类", "",
           f"- 源: `{SRC}`  导出: `{EXP}`", ""]
    titles = {
        "junk": "本就该排除 (junk)",
        "archive": "压缩包 (不支持, 保持不导入)",
        "wps": ".wps 原件 (导出侧已有转换件)",
        "renamed": "改名/归一化后已进库",
        "name-diff": "疑似同名匹配失败 (名字细节差异)",
        "likely-missing": "真正未导出, 需决定是否补导",
    }
    for key, title in titles.items():
        items = buckets[key]
        out.append(f"## {title}（{len(items)}）")
        out.append("")
        for rel, note in items:
            out.append(f"- `{rel}`" + (f" — {note}" if note else ""))
        out.append("")

    dst = OUT_DIR / "qc" / "missing_docs.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out), encoding="utf-8")
    print("report:", dst)
    for key in titles:
        print(f"{key}: {len(buckets[key])}")


if __name__ == "__main__":
    main()
