#!/usr/bin/env python3
"""F1 批次一（D1+D5）离线读数器：S 级 5 份 + 洪水过程_dup.xls（并入数据源）。

通道与 extract_xls.py 相同（本机 python_calamine 优先，否则 docker stdin calamine）；
header=None 不做解释，全量网格落 survey/extract_<KB名>.json。只读语料。

用法：python3 extract_xls_wave2.py [--only KB名子串]
"""
import argparse
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = Path("/home/scada/SmartTwinRes-skills/pdfs")
CONTAINER = "docker-ragflow-cpu-1"

# KB 名 → 语料相对路径（dup 不入库，仅作 #1 的并入数据源）
TARGETS = {
    "洪水过程.xls": "06-历年洪水资料/02-2021年洪水调度/10-3洪水/洪水过程.xls",
    "洪水过程_dup.xls": "06-历年洪水资料/02-2021年洪水调度/10-3洪水/洪水过程_dup.xls",
    "洪水过程(1).xls": "06-历年洪水资料/02-2021年洪水调度/9-25洪水/洪水过程.xls",
    "洪水过程(2).xls": "06-历年洪水资料/03-2020年洪水(8-16)/洪水过程.xls",
    "洪水统计.xls": "06-历年洪水资料/04-2019年洪水(9-14)/洪水统计.xls",
    "降雨量统计.xls": "06-历年洪水资料/05-2013年洪水(7-22)/降雨量统计.xls",
}

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
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只处理 KB 名含此子串的目标")
    args = ap.parse_args()

    for kb_name, rel in TARGETS.items():
        if args.only and args.only not in kb_name:
            continue
        path = CORPUS / rel
        if not path.exists():
            raise SystemExit(f"语料缺失: {path}")
        sheets = read_docker(path)
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
