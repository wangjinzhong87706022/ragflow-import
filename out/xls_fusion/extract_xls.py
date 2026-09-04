#!/usr/bin/env python3
"""xls 定向融合文本化 · 阶段2 离线读数器（只读，无 --apply 概念）。

读 3 份目标 xls 的全部 sheet 原始网格（header=None，不做任何解释），
产出 survey/extract_<KB名>.json 供融合文本撰写。语料根只读。

读取通道（自动降级）：
  1. 本机 python_calamine（pandas engine="calamine"）
  2. docker exec -i docker-ragflow-cpu-1 stdin 流式（已验证 27/27 可读，零落盘）
     —— openpyxl/xlrd 对这批损坏 BIFF 全部失败，calamine 是唯一通道。
"""
import io
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = Path("/home/scada/SmartTwinRes-skills/pdfs")
CONTAINER = "docker-ragflow-cpu-1"

# KB 名 → 语料相对路径（KB 名即 RAGFlow ds3 里的文档名）
TARGETS = {
    "洪水统计(1).xls": "06-历年洪水资料/06-2008年洪水(8-22)/洪水统计.xls",
    "较大洪水统计表.xls": "06-历年洪水资料/08-历年洪水统计/较大洪水统计表.xls",
    "2011年下泄水量统计.xls": "06-历年洪水资料/08-历年洪水统计/2011年下泄水量统计.xls",
}


def read_local(path: Path):
    import pandas as pd  # 需 python_calamine
    frames = pd.read_excel(path, engine="calamine", sheet_name=None, header=None)
    return {name: df.where(df.notna(), None).values.tolist()
            for name, df in frames.items()}


_DOCKER_SNIPPET = """
import sys, io, json, pandas as pd
b = sys.stdin.buffer.read()
frames = pd.read_excel(io.BytesIO(b), engine="calamine", sheet_name=None, header=None)
out = {name: df.where(df.notna(), None).values.tolist() for name, df in frames.items()}
sys.stdout.write(json.dumps(out, ensure_ascii=False, default=str))
"""


def read_docker(path: Path):
    with open(path, "rb") as fh:
        r = subprocess.run(
            ["docker", "exec", "-i", "-e", "PYTHONIOENCODING=utf-8", CONTAINER,
             "python3", "-c", _DOCKER_SNIPPET],
            stdin=fh, capture_output=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"docker 通道失败: {r.stderr.decode(errors='replace')[:300]}")
    return json.loads(r.stdout.decode("utf-8"))


def main():
    mode = None
    try:
        import pandas  # noqa: F401
        import python_calamine  # noqa: F401  pip 包 python-calamine 的模块名
        # （评审 F9：旧探测 `import calamine` 模块名不存在，local 分支永不激活）
        mode = "local-calamine"
    except ImportError:
        mode = "docker-stdin"
    print(f"读取通道: {mode}", flush=True)

    for kb_name, rel in TARGETS.items():
        path = CORPUS / rel
        if not path.exists():
            raise SystemExit(f"语料缺失: {path}")
        sheets = read_local(path) if mode == "local-calamine" else read_docker(path)
        out = {"kb_name": kb_name, "corpus_path": rel,
               "size_bytes": path.stat().st_size, "sheets": {}}
        for name, grid in sheets.items():
            out["sheets"][name] = {"rows": len(grid),
                                   "cols": max((len(r) for r in grid), default=0),
                                   "grid": grid}
        dest = HERE / "survey" / f"extract_{kb_name}.json"
        dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        summary = "、".join(f"{n}({d['rows']}×{d['cols']})" for n, d in out["sheets"].items())
        print(f"  {kb_name}: {len(sheets)} sheet → {dest.name}", flush=True)
        print(f"    {summary}", flush=True)


if __name__ == "__main__":
    main()
