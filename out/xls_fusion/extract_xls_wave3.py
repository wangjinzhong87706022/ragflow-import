#!/usr/bin/env python3
"""批次二 reader：2010 三份（2001年洪水过程线 / 2010年下泄水量统计 / 724-829两场洪水）。

与 extract_xls_wave2.py 同通道（语料字节 stdin 管进 docker calamine，
sheet_name=None header=None 全量网格），写出 survey/extract_<KB名>.json。
只读语料根，零落盘语料。
"""
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = Path("/home/scada/SmartTwinRes-skills/pdfs")
CONTAINER = "docker-ragflow-cpu-1"

TARGETS = {
    "2001年洪水过程线.xls": "06-历年洪水资料/08-历年洪水统计/2001年洪水过程线.xls",
    "2010年下泄水量统计.xls": "06-历年洪水资料/08-历年洪水统计/2010年下泄水量统计.xls",
    "724-829两场洪水.xls": "06-历年洪水资料/07-其他洪水事件/724-829两场洪水.xls",
}

_DOCKER_SNIPPET = r'''
import sys, io, json
import pandas as pd
b = sys.stdin.buffer.read()
frames = pd.read_excel(io.BytesIO(b), engine="calamine", sheet_name=None, header=None)
out = {}
for name, df in frames.items():
    grid = df.where(df.notna(), None).values.tolist()
    out[name] = {"shape": [int(df.shape[0]), int(df.shape[1])], "grid": grid}
sys.stdout.write(json.dumps(out, ensure_ascii=False, default=str))
'''


def read_docker(path: Path) -> dict:
    with open(path, "rb") as fh:
        r = subprocess.run(
            ["docker", "exec", "-i", "-e", "PYTHONIOENCODING=utf-8", CONTAINER,
             "python3", "-c", _DOCKER_SNIPPET],
            stdin=fh, capture_output=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"docker 通道失败 {path.name}: {r.stderr.decode(errors='replace')[:300]}")
    return json.loads(r.stdout.decode("utf-8"))


def main():
    outdir = HERE / "survey"
    outdir.mkdir(exist_ok=True)
    for kb, rel in TARGETS.items():
        path = CORPUS / rel
        if not path.exists():
            raise SystemExit(f"语料缺失: {path}")
        data = read_docker(path)
        dst = outdir / f"extract_{kb}.json"
        dst.write_text(json.dumps({"kb_name": kb, "corpus_path": rel,
                                   "size_bytes": path.stat().st_size, "sheets": data},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{kb} -> {dst.name}  sheets: " + ", ".join(
            f"{n}({d['shape'][0]}x{d['shape'][1]})" for n, d in data.items()))


if __name__ == "__main__":
    main()
