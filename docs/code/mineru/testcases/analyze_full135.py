# NOTE: 只读分析。用法: python analyze_full135.py [results_full135_baseline.json]
"""全量 135 条结果分析: 分类通过率 + 与 9-13 glm 基线(108/135)对比 + 检索层/模型层 MISS 粗分。"""
import json, sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results_full135_baseline.json')
d = json.load(open(path, encoding='utf-8'))
n = len(d); p = sum(1 for r in d if r['pass'])

from collections import defaultdict
stat = defaultdict(lambda: [0, 0])  # cat -> [pass, total]
miss_cases = []
for r in d:
    stat[r['category']][1] += 1
    if r['pass']:
        stat[r['category']][0] += 1
    else:
        ans = r.get('answer') or ''
        # 粗分: 拒答型 MISS → 可能检索层未命中或反幻觉判分; 其他 → 关键词不匹配
        reject = any(w in ans for w in ('未找到', '未收录', '不存在', '未提供'))
        miss_cases.append((r['id'], r['category'], '拒答型' if reject else '要点不匹配', ans[:60].replace('\n',' ')))

print(f'== 总览: {p}/{n} ({100*p/max(1,n):.1f}%)   [9-13 glm 基线: 108/135 (80.0%)]')
print(f"{'类别':6s} {'PASS':>5s}/{'总':>4s} {'率':>7s}")
for cat, (x, t) in sorted(stat.items(), key=lambda kv: -kv[1][0]):
    print(f'{cat:6s} {x:5d}/{t:4d} {100*x/max(1,t):6.1f}%')
print(f'\n== MISS 明细 ({len(miss_cases)}):')
for tc, cat, kind, head in miss_cases:
    print(f'  {tc} [{cat}] {kind}: {head}')
