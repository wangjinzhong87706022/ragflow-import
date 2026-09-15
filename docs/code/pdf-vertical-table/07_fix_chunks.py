#!/usr/bin/env python3
"""按 PDF 原表核对结果修复 SL-T-447-2026.pdf 的 chunk 内容.

核对基准：D:/doc/taineng/智能体平台/SL-T-447-2026_水土保持项目前期设计文件编制技术规程.pdf
逐页渲染放大核对（p70-p96），全部列名以原表为准。

API 语义（本地仓库 api/apps/restful_apis/chunk_api.py 实证）：
  - 更新 chunk：PATCH /datasets/{ds}/documents/{doc}/chunks/{chunk_id}，body {"content": ...}
    服务端重新分词（content_ltks/content_sm_ltks）并重新向量化（q_*_vec）后写回 doc store，
    检索立即生效。
  - 删除 chunk：POST 不行，用 DELETE /datasets/{ds}/documents/{doc}/chunks，
    body {"chunk_ids": [...]}（单块 DELETE 路径 405）。
"""
import os
import json
import sys
import time

import requests
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

RAGFLOW_BASE = "https://labragf.openagp.top:9080"
RAGFLOW_API_KEY = os.getenv("RAGFLOW_API_KEY", "")
DATASET_ID = "d42ae68aab5011f1a33465638b9ea3fd"
DOC_ID = "7acf1fc2ac2011f1b8ab3155a5f51bf7"
H = {"Authorization": f"Bearer {RAGFLOW_API_KEY}"}

SEP = "| " + " | ".join(["---"] * 40)


def md_table(header, n_cols=None, empty_rows=1, extra_rows=()):
    n = n_cols or len(header)
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("| " + " | ".join(["---"] * n) + " |")
    for _ in range(empty_rows):
        lines.append("| " + " | ".join([" "] * n) + " |")
    for r in extra_rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------- 重写块
REWRITES = {}

# p82: D.0.2 气象特征表 + D.0.3 经济社会情况表（原块题注互换、缺年平均气温列、农林牧油）
REWRITES["621f2d3c"] = """表D.0.2 气象特征表

{t2}

表D.0.3 经济社会情况表

{t3}

注1：本表主要适用于项目建议书和可行性研究报告，并按县（市、区、旗）统计，初步设计报告、实施方案以小流域（片区、工程建设区）涉及的行政村进行统计，人口单位为“人”，劳动力单位为“个”。
注2：坡耕地水土流失综合治理实施方案，本表可以进一步细化种植业和林业组成。""".format(
    t2=md_table(
        ["分区", "省（自治区、直辖市）", "站名（县、市、区、旗）", "多年平均降雨量/mm", "多年平均蒸发量/mm",
         "年最高气温/℃", "年最低气温/℃", "年平均气温/℃", "≥10℃积温/℃", "年日照时数/h", "无霜期/d",
         "最大冻土深度/m", "大风日数/d", "平均风速/(m/s)", "……"]),
    t3=md_table(
        ["分区", "省（自治区、直辖市）", "县（市、区、旗）", "乡镇/村", "总人口/万人", "农业人口/万人",
         "农业劳动力/万人", "劳动力转移/万个", "种植业", "林业", "牧业", "渔业", "农林牧渔服务业", "小计",
         "农村常住人口可支配收入/元", "人均耕地/(hm²/人)", "人均年产粮/(kg/人)", "备注"]),
)

# p83: D.0.4 土地利用现状表（原块多级表头压扁错位、尾部乱行）
REWRITES["7dc0139a"] = """表D.0.4 土地利用现状表（单位：hm²）

{t}

注1：项目建议书和可行性研究报告可按县（市、区、旗）统计，初步设计报告、实施方案以小流域（片区、工程建设区）涉及的行政村为单位进行统计。
注2：如有其他林地、园地等可根据土地利用现状分类表调整。""".format(
    t=md_table(
        ["分区", "省（自治区、直辖市）", "县（市、区、旗）", "乡镇/村",
         "水田", "水浇地", "坡耕地", "旱平地", "沟川坝地", "耕地小计",
         "园地", "乔木林地", "灌木林地", "其他林地", "林地小计",
         "天然牧草地", "人工牧草地", "其他草地", "草地小计",
         "河流水面", "湖泊水面", "水域及水利设施用地-其他", "水域及水利设施用地-小计",
         "交通运输用地", "住宅用地", "工矿仓储用地", "特殊用地", "……",
         "沼泽地", "沙地", "盐碱地", "裸土地", "其他土地小计"]),
)

# p84: D.0.5 水土流失现状表（原块缺省列、占比列单位杂乱）
REWRITES["0913c4f4"] = """表D.0.5 水土流失现状表

{t}

注1：项目建议书和可行性研究报告可按县（市、区、旗）统计，初步设计报告、实施方案按小流域（片区）统计。
注2：项目建议书和可行性研究报告的典型小流域水土流失现状也按此表填写，可以不填写省（自治区、直辖市）、县（市、区、旗）栏。""".format(
    t=md_table(
        ["分区", "省（自治区、直辖市）", "县（市、区、旗）", "乡镇/村", "小流域（片区）",
         "总面积/km²", "水土流失总面积/km²",
         "轻度/km²", "轻度占比/%", "中度/km²", "中度占比/%", "强烈/km²", "强烈占比/%",
         "极强烈/km²", "极强烈占比/%", "剧烈/km²", "剧烈占比/%",
         "侵蚀模数/[t/(km²·a)]", "沟壑密度/(km/km²)"],
        extra_rows=[["项目区合计"] + [" "] * 18],
        empty_rows=3),
)

# p85: D.0.6 崩岗现状表（原块瓢形误读为圆形、混合形误为混合型、缺题注）——结构正确，定点替换
REWRITES["5f2edf9b"] = None  # 占位，下面用定点替换生成

# p86: D.0.7 侵蚀沟现状调查表（原块首列括号乱序、缺省列、缺均深列、注2-4丢失）
REWRITES["6749cbe9"] = """表D.0.7 侵蚀沟现状调查表（地理坐标单位：° ′ ″）

{t}

注1：项目建议书和可行性研究报告可按县（市、区、旗）统计，初步设计报告、实施方案按小流域（片区）统计。
注2：沟道分级通常分为大型、中型、小型三级，分级标准按有关规定执行。
注3：危害类型：包括危害房屋安全、危害道路安全、严重影响农业生产、严重影响河湖库塘等，填写主要危害类型，可多选。
注4：本表可根据实际需求适当调整。""".format(
    t=md_table(
        ["分区", "省（自治区、直辖市）", "县（市、区、旗）", "乡镇/村", "小流域（片区）",
         "沟头经度", "沟头纬度", "沟口经度", "沟口纬度",
         "集水区面积/hm²", "沟壑面积/hm²", "长度/m", "上口均宽/m", "下口均宽/m", "均深/m",
         "沟道比降/%", "沟道分级", "危害类型", "土壤类型"]),
)

# p86: D.0.8 水土保持措施现状表（原块子表头错乱：坡耕地/排洪沟误读、缺题注）
REWRITES["4d60d73f"] = """表D.0.8 水土保持措施现状表

{t}

注1：项目建议书和可行性研究报告可按县（市、区、旗）统计，初步设计报告、实施方案按小流域（片区）统计。
注2：本表可根据实际情况对措施栏适当取舍。""".format(
    t=md_table(
        ["分区", "省（自治区、直辖市）", "县（市、区、旗）", "小流域（片区）",
         "总面积/km²", "水土流失面积/km²",
         "土坎梯田/hm²", "石坎梯田/hm²",
         "塘坝座数/座", "塘坝总库容/万m³", "拦沙坝座数/座", "拦沙坝总库容/万m³",
         "淤地坝座数/座", "淤地坝总库容/万m³", "蓄水池数量/座", "蓄水池容量/万m³",
         "排洪沟/m", "谷坊/座", "沟头防护工程/(处或m)", "……",
         "乔木林/hm²", "灌木林/hm²", "其他林/hm²",
         "经济林/hm²", "经济林栽培园和果园/hm²", "水土保持种草/hm²", "封育治理/hm²",
         "农业耕作/hm²", "其他工程"]),
)

# p87: D.0.9 项目区分布（概况）表 + D.0.10 土地坡度组成表（原块 D.0.10 缺 5°~15° 级、注被截断）
REWRITES["450a181f"] = """表D.0.9 项目区分布（概况）表

{t9}

注：适用于可行性研究报告。

表D.0.10 土地坡度组成表

{t10}

注：项目建议书和可行性研究报告可按县（市、区、旗）统计，初步设计报告、实施方案以小流域（片区、工程建设区）涉及的行政村进行统计。""".format(
    t9=md_table(
        ["分区", "省（自治区、直辖市）", "县（市、区、旗）", "小流域名称", "流域面积/km²",
         "水土流失面积/km²", "林草覆盖率/%", "总人口/万人", "耕地面积/hm²"], empty_rows=3),
    t10=md_table(
        ["分区", "省（自治区、直辖市）", "县（市、区、旗）", "行政村", "土地总面积/km²",
         "<5°面积/hm²", "<5°占比/%", "5°~15°面积/hm²", "5°~15°占比/%",
         "15°~25°面积/hm²", "15°~25°占比/%", "25°~35°面积/hm²", "25°~35°占比/%",
         "≥35°面积/hm²", "≥35°占比/%", "小计面积/hm²", "小计占比/%"],
        extra_rows=[["项目区合计"] + [" "] * 16],
        empty_rows=3),
)

# p91: D.0.15 水土保持措施量汇总表（干净变体，仍有库数/乔灌木林/经济林果园等误读，按原表重写）
REWRITES["9325ef4f"] = """表D.0.15 水土保持措施量汇总表

{t}

注：项目建议书和可行性研究报告可按县（市、区、旗）统计，初步设计报告、实施方案以小流域（片区、工程建设区）涉及的行政村为单位进行统计。""".format(
    t=md_table(
        ["分区", "小流域（片区、工程建设区）", "总面积/km²", "水土流失面积/km²", "治理面积/km²",
         "土坎梯田/hm²", "石坎梯田/hm²",
         "塘坝座数/座", "塘坝总库容/万m³", "塘坝淤积库容/万m³", "塘坝蓄水量/万m³",
         "拦沙坝座数/座", "拦沙坝总库容/万m³", "拦沙坝淤积量/万m³",
         "淤地坝座数/座", "淤地坝总库容/万m³", "淤地坝淤积面积/hm²", "淤地坝淤积量/万m³",
         "蓄水池数量/座", "蓄水池容量/万m³",
         "水窖/座", "谷坊/座", "沟头防护工程/(座或m)", "截排水沟/m", "……",
         "乔木林/hm²", "灌木林/hm²", "经济林/hm²", "经济林栽培园和果园/hm²",
         "水土保持种草/hm²", "封育治理/hm²", "农业耕作/hm²", "其他工程"],
        extra_rows=[["合计"] + [" "] * 32],
        empty_rows=3),
)

# p92: D.0.16 工程量汇总表（石方挖填→挖方、其他工程单位应为空、注1 误字、缺题注）
OLD = None
REWRITES["cd59615f"] = "PATCH_TARGETED"

# p93: D.0.17 工程施工总进度表（投资/万元→投工/工日、注文字修正、缺题注）
REWRITES["664db9dd"] = "PATCH_TARGETED"

# p95: D.0.20 水土保持措施效益计算成果表（经渍林/微地坝误读、缺渠道与种草列、缺题注）
REWRITES["3e5934c6"] = """表D.0.20 水土保持措施效益计算成果表

其中：工程措施包括土坎梯田、石坎梯田、淤地坝、拦沙坝、蓄水池、截排水沟、谷坊、水窖、渠道等；水土保持林包括乔木林、灌木林。

{t}

注：此表适用初步设计报告和实施方案，项目建议书和可行性研究报告可以按实际情况简化。""".format(
    t=md_table(
        ["县区", "小流域（片区、工程建设区）", "项目", "总计",
         "土坎梯田/hm²", "石坎梯田/hm²", "淤地坝/座", "拦沙坝/座", "蓄水池/座", "截排水沟/m",
         "谷坊/座", "水窖/座", "渠道/m", "……",
         "乔木林/hm²", "灌木林/hm²", "……",
         "经济林/hm²", "经济林栽培园和果园/hm²", "种草/hm²", "封禁治理/hm²", "农业耕作/hm²", "其他工程"],
        extra_rows=[
            [" ", " ", "蓄水效益/万m³"] + [" "] * 20,
            [" ", " ", "保土效益/万t"] + [" "] * 20,
            [" ", " ", "直接经济效益/万元"] + [" "] * 20,
            [" ", "……", "……"] + [" "] * 20,
            [" ", "合计", "蓄水效益/万m³"] + [" "] * 20,
            [" ", " ", "保土效益/万t"] + [" "] * 20,
            [" ", " ", "直接经济效益/万元"] + [" "] * 20,
        ],
        empty_rows=0),
)

# p96: D.0.21 经济评价分析成果表（补题注 + 补回删除块时丢失的精度规则句）
REWRITES["5f8efc8c"] = (
    "D.0.21 经济评价分析成果见表D.0.21，表中比例、效益、费用数据应保留2位小数。\n\n"
    "表D.0.21 经济评价分析成果表\n\n"
    "| 项目 | 单位 | 指标值 | 备注 |\n| :---: | :---: | :---: | :---: |\n"
    "| 总经济效益 | 万元 |  |  |\n"
    "| 总费用 | 万元 |  |  |\n"
    "| 净现值 | 万元 |  |  |\n"
    "| 内部收益率 | % |  |  |"
)

# ---------------------------------------------------------------- 补题注（前缀）
CAPTION_PREFIX = {
    "861e8a09": "表D.0.1-1 项目特性表（适用于项目建议书和可行性研究报告）",  # p70 首段
    "f94ae158": "表D.0.1-1 项目特性表（续）",   # p71
    "ca33e0e6": "表D.0.1-1 项目特性表（续）",   # p72
    "1549c9c6": "表D.0.1-1 项目特性表（续）",   # p73
    "a484c32d": "表D.0.1-1 项目特性表（续）",   # p74
    "f3afda9d": "表D.0.1-2 工程建设特性表（适用于初步设计报告、实施方案）",  # p74
    "98440019": "表D.0.1-2 工程建设特性表（续）",  # p75
    "6ece2a4e": "表D.0.1-2 工程建设特性表（续）",  # p76
    "e3da45be": "表D.0.1-2 工程建设特性表（续）",  # p77
    "16e9a1e9": "表D.0.1-3 工程特性表（适用于淤地坝、拦沙坝、塘坝、滚水坝等单项工程，可参照使用）",  # p78
    "7b3abbde": "表D.0.1-3 工程特性表（续）",   # p79
    "58f839ed": "表D.0.1-3 工程特性表（续）",   # p80
    "49783b9a": "表D.0.13 图斑现状及治理措施设计表",  # p89
    "ae6379eb": "表D.0.14 图斑林草设计表",      # p90
    "c0c63d98": "表D.0.12 建设规模汇总表",      # p88（内容与原表一致，仅缺题注）
}

# ---------------------------------------------------------------- 定点替换（结构正确、局部错字）
TARGETED = {
    # p92 D.0.16
    "cd59615f": [
        ("表D.0.16 工程量汇总表\n\n", "PREFIX"),
        ("石方<br>挖填<br>/m³", "石方<br>挖方<br>/m³"),
        ("| 其他工程 | hm² |", "| 其他工程 |  |"),
        ("工程量和利用典型小流域推算", "工程量利用典型小流域推算"),
    ],
    # p93 D.0.17
    "664db9dd": [
        ("表D.0.17 工程施工总进度表\n\n", "PREFIX"),
        ("| 投资 | 万元 |", "| 投工 | 工日 |"),
        ("本表以可行性研究报告报告为主", "本表以可行性研究报告为主"),
        ("初步设计报告，实施方案可以行政村为单元统计", "初步设计报告、实施方案可以行政村为单元统计"),
    ],
    # p94 D.0.18/D.0.19：D.0.19 补小计行与注（另一重复块将删除）
    "fca60236": [
        ("| **小计** | | | | | | | | |",
         "| **小计** | | | | | | | | |\n| 注：主要适用于项目建议书和可行性研究报告。 | | | | | | | | |"),
    ],
    # p85 D.0.6：瓢形/混合形 + 题注
    "5f2edf9b": [
        ("表D.0.6 崩岗现状表\n\n", "PREFIX"),
        ("| 活动型 | 圆形 |", "| 活动型 | 瓢形 |"),
        ("| | 混合型 |", "| | 混合形 |"),
    ],
}


def patch_chunk(chunk_id: str, content: str) -> bool:
    full = full_id_of.get(chunk_id, chunk_id)
    r = requests.patch(
        f"{RAGFLOW_BASE}/api/v1/datasets/{DATASET_ID}/documents/{DOC_ID}/chunks/{full}",
        headers=H, json={"content": content}, verify=False, timeout=120)
    ok = r.ok and r.json().get("code") == 0
    print(("OK  " if ok else "FAIL") + f" PATCH {chunk_id} -> {r.status_code} {r.text[:120] if not ok else ''}")
    return ok


def delete_chunks(chunk_ids: list) -> bool:
    r = requests.delete(
        f"{RAGFLOW_BASE}/api/v1/datasets/{DATASET_ID}/documents/{DOC_ID}/chunks",
        headers=H, json={"chunk_ids": chunk_ids}, verify=False, timeout=120)
    ok = r.ok and r.json().get("code") == 0
    print(("OK  " if ok else "FAIL") + f" DELETE {chunk_ids} -> {r.status_code} {r.text[:160] if not ok else ''}")
    return ok


def main():
    chunks = {c["id"][:8]: c for c in json.load(open("chunks_current.json", encoding="utf-8"))}
    global full_id_of
    full_id_of = {k: v["id"] for k, v in chunks.items()}
    backup = {c["id"]: c["content"] for c in chunks.values()}
    json.dump(backup, open("chunks_backup_before_fix.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # 1) 全量重写
    for cid, content in REWRITES.items():
        if content in (None, "PATCH_TARGETED"):
            continue
        patch_chunk(cid, content)
        time.sleep(0.3)

    # 2) 定点替换
    for cid, ops in TARGETED.items():
        text = chunks[cid]["content"]
        for old, new in ops:
            if new == "PREFIX":
                text = old + text.lstrip()
                continue
            if old not in text:
                print(f"WARN  {cid}: pattern not found: {old[:40]!r}")
                continue
            text = text.replace(old, new)
        patch_chunk(cid, text)
        time.sleep(0.3)

    # 3) 补题注
    for cid, cap in CAPTION_PREFIX.items():
        text = chunks[cid]["content"]
        if cid in TARGETED or cid in REWRITES:
            # 已重写/定点处理的块不再叠加（除明确列出者）
            if cid not in ("c0c63d98",):
                continue
        if text.lstrip().startswith(cap[:12]):
            print(f"SKIP  {cid} already captioned")
            continue
        patch_chunk(cid, cap + "\n\n" + text.lstrip())
        time.sleep(0.3)

    # 4) 删除重复/损坏块（均有干净等价覆盖）——上一轮已删除成功，此处幂等跳过
    to_delete = []
    for prefix in ("9fc12905", "a7bc5f99", "81ca42ec", "9024ce30"):
        hit = [fid for fid in full_id_of.values() if fid.startswith(prefix)]
        if hit:
            to_delete.append(hit[0])
        else:
            print(f"INFO  {prefix}: not found (already deleted?), skip")
    if to_delete:
        delete_chunks(to_delete)


if __name__ == "__main__":
    main()
