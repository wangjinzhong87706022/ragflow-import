#!/bin/bash
# 全量 135 条分批驱动。断点续跑: 重跑本脚本即可 (run_eval_api 跳过已完成 ID)
set -u
export RAGFLOW_API_BASE="${RAGFLOW_API_BASE:?need RAGFLOW_API_BASE}"
export RAGFLOW_API_KEY="${RAGFLOW_API_KEY:?need RAGFLOW_API_KEY}"
cd "$(dirname "$0")"
CHAT=46f41fbaaf1611f1896583d540e218a6
OUT=results_full135_baseline.json
for ((s=1; s<=135; s+=15)); do
  e=$((s+14))
  echo "===== batch TC-$(printf %03d $s) .. TC-$(printf %03d $e) ====="
  python -X utf8 run_eval_api.py --chat "$CHAT" --cases mineru_eval_cases.json \
    --start $s --end $e --sleep 2 --timeout 400 --clean-every 15 --out "$OUT" || exit 1
done
python -X utf8 - <<'PY'
import json
d = json.load(open('results_full135_baseline.json', encoding='utf-8'))
n = len(d); p = sum(1 for r in d if r['pass'])
from collections import Counter
cat = Counter()
for r in d:
    cat[(r['category'], r['pass'])] += 1
print(f'\nFULL135 DONE: {p}/{n} pass ({100*p/max(1,n):.1f}%)')
for k in sorted(cat): print(' ', k[0], 'PASS' if k[1] else 'MISS', cat[k])
PY
