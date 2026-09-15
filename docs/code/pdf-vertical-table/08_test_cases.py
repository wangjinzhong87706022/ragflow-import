#!/usr/bin/env python3
"""SL-T-447-2026 解析质量测试套件：38 个案例，经 chats completions API 实测.

案例设计（对应 2026-09-09 修复后的 chunk）：
  A 竖排(旋转)大表结构   B 表内容/数据行   C 题注归属   D D.0.1 特性表系列
  E 数据精度/注释规则    F 其他附表        G 标准信息与正文   H 反幻觉/负例

判分：must 全部命中、forbid 全部不出现、must_any 至少一个命中（归一化后子串匹配）。
归一化：全角～＞％转半角、去空格、小写。
"""
import os
import json
import time

import requests
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

RAGFLOW_BASE = "https://labragf.openagp.top:9080"
RAGFLOW_API_KEY = os.getenv("RAGFLOW_API_KEY", "")
CHAT_ID = "c526f6f4abfc11f1b8ab3155a5f51bf7"  # 行业标准QA-test（绑定行业标准数据集）
OUT = "test_cases_results.json"

CASES = [
    # ---- A 竖排（旋转 90°）大表结构 ----
    dict(id="TC-01", cat="A-竖排表结构", q="表D.0.4 土地利用现状表的一级地类栏目有哪些？",
         must=["耕地", "园地", "林地", "草地", "水域及水利设施用地", "交通运输用地", "住宅用地", "工矿仓储用地", "特殊用地", "其他土地"],
         forbid=["梯田"]),
    dict(id="TC-02", cat="A-竖排表结构", q="表D.0.4 土地利用现状表中旱地进一步细分为哪几栏？",
         must=["坡耕地", "旱平地", "沟川坝地"], forbid=[]),
    dict(id="TC-03", cat="A-竖排表结构", q="表D.0.5 水土流失现状表把土壤侵蚀强度分为哪几级？",
         must=["轻度", "中度", "强烈", "极强烈", "剧烈"], forbid=["冻融"]),
    dict(id="TC-04", cat="A-竖排表结构", q="表D.0.5 水土流失现状表的列头从左到右依次是哪些？",
         must=["分区", "省", "县", "乡镇", "小流域", "总面积", "水土流失总面积"], forbid=[]),
    dict(id="TC-05", cat="A-竖排表结构", q="表D.0.7 侵蚀沟现状调查表的地理坐标包括哪几项？",
         must=["沟头经度", "沟头纬度", "沟口经度", "沟口纬度"], forbid=[]),
    dict(id="TC-06", cat="A-竖排表结构", q="表D.0.8 水土保持措施现状表的工程措施栏包括哪些措施？",
         must=["土坎梯田", "石坎梯田", "塘坝", "拦沙坝", "淤地坝", "蓄水池", "排洪沟", "谷坊", "沟头防护工程"],
         forbid=[]),
    dict(id="TC-07", cat="A-竖排表结构", q="表D.0.15 水土保持措施量汇总表中塘坝栏细分为哪几项指标？",
         must=["座数", "总库容", "淤积库容", "蓄水量"], forbid=[]),
    dict(id="TC-08", cat="A-竖排表结构", q="表D.0.16 工程量汇总表的表头包括哪些列？",
         must=["措施数量", "土方开挖", "土方回填", "石方挖方", "石方回填", "混凝土", "浆砌石", "干砌石", "整地", "栽植乔木", "栽植灌木", "种草", "投工"],
         forbid=["挖填"]),
    # ---- B 表内容/数据行 ----
    dict(id="TC-09", cat="B-表内容行", q="表D.0.16 工程量汇总表中工程措施栏目下列出了哪些措施行？",
         must=["土坎梯田", "石坎梯田", "蓄水池", "截排水沟", "谷坊", "水窖", "渠道"], forbid=[]),
    dict(id="TC-10", cat="B-表内容行", q="表D.0.16 工程量汇总表的注2对“投工”栏有什么规定？",
         must=["投工", "删除"], forbid=[]),
    dict(id="TC-11", cat="B-表内容行", q="表D.0.17 工程施工总进度表的最后两行是什么？各自的单位是什么？",
         must=["治理面积", "投工", "工日"], forbid=[]),
    dict(id="TC-12", cat="B-表内容行", q="表D.0.20 水土保持措施效益计算成果表中每条小流域统计哪三类效益？",
         must=["蓄水效益", "保土效益", "直接经济效益"], forbid=[]),
    # ---- C 题注归属（修复验证：原 D.0.2/D.0.3 题注互换）----
    dict(id="TC-13", cat="C-题注归属", q="表D.0.2 气象特征表包含哪些气象指标？",
         must=["降雨量", "蒸发量", "气温", "积温", "日照时数", "无霜期", "冻土深度", "大风日数", "风速"],
         forbid=["经济社会"]),
    dict(id="TC-14", cat="C-题注归属", q="表D.0.3 经济社会情况表中农村各业生产总值分为哪几项？",
         must=["种植业", "林业", "牧业", "渔业", "农林牧渔服务业"], forbid=["农林牧油"]),
    dict(id="TC-15", cat="C-题注归属", q="“农林牧渔服务业”这一栏出现在附录D的哪张表中？",
         must=["D.0.3"], forbid=[]),
    dict(id="TC-16", cat="C-题注归属", q="表D.0.13 的正式表名是什么？",
         must=["图斑现状及治理措施设计表"], forbid=[]),
    # ---- D D.0.1 特性表系列 ----
    dict(id="TC-17", cat="D-特性表系列", q="表D.0.1-1 项目特性表适用于哪些设计阶段？",
         must=["项目建议书", "可行性研究"], forbid=[]),
    dict(id="TC-18", cat="D-特性表系列", q="表D.0.1-2 工程建设特性表适用于哪些设计阶段？",
         must=["初步设计", "实施方案"], forbid=[]),
    dict(id="TC-19", cat="D-特性表系列", q="表D.0.1-3 工程特性表适用于哪类工程？",
         must=["淤地坝", "拦沙坝", "塘坝", "滚水坝"], forbid=[]),
    dict(id="TC-20", cat="D-特性表系列", q="表D.0.1-1 项目特性表中“一、项目区概况”包括哪些指标？",
         must=["行政区域", "所属流域", "项目区面积", "小流域"], forbid=[]),
    # ---- E 数据精度/注释规则 ----
    dict(id="TC-21", cat="E-精度规则", q="表D.0.15 和表D.0.16 对表中数据的精度分别有什么要求？",
         must=["2位小数", "取整数"], forbid=[]),
    dict(id="TC-22", cat="E-精度规则", q="表D.0.21 中比例、效益、费用数据的精度要求是什么？",
         must=["2位小数"], forbid=[]),
    dict(id="TC-23", cat="E-精度规则", q="附录D规定D.0.1各特性表中面积、长度、比例、投资等数据的精度要求是什么？",
         must=["2位小数"], forbid=[]),
    dict(id="TC-24", cat="E-精度规则", q="使用表D.0.2 气象特征表时，哪些指标要求取整数？",
         must=["积温", "日照", "大风", "无霜期"], forbid=[]),
    # ---- F 其他附表 ----
    dict(id="TC-25", cat="F-其他附表", q="表D.0.6 崩岗现状表中崩岗形态分为哪几种？",
         must=["瓢形", "爪形", "条形", "弧形", "混合", "崩岗群"], forbid=["圆形"]),
    dict(id="TC-26", cat="F-其他附表", q="表D.0.10 土地坡度组成表把坡度划分为哪几级？",
         must=["<5°", "5°~15°", "15°~25°", "25°~35°", "≥35°"], forbid=[]),
    dict(id="TC-27", cat="F-其他附表", q="表D.0.11 坡耕地坡度组成表把坡耕地划分为哪几级？",
         must=["<5°", "5°~10°", "10°~15°", "15°~25°", ">25°"], forbid=[]),
    dict(id="TC-28", cat="F-其他附表", q="表D.0.19 资金筹措表包含哪些资金来源渠道？",
         must=["中央投资", "地方投资", "群众自筹", "民间投资"], forbid=[]),
    dict(id="TC-29", cat="F-其他附表", q="表D.0.21 经济评价分析成果表包含哪四个指标？",
         must=["总经济效益", "总费用", "净现值", "内部收益率"], forbid=[]),
    dict(id="TC-30", cat="F-其他附表", q="表D.0.9 项目区分布（概况）表包含哪些列？适用于什么报告？",
         must=["流域面积", "水土流失面积", "林草覆盖率", "总人口", "耕地面积", "可行性研究"], forbid=[]),
    dict(id="TC-31", cat="F-其他附表", q="表D.0.14 图斑林草设计表包含哪些设计要素列？",
         must=["需苗量", "播种量", "整地规格", "株行距", "种植密度"], forbid=[]),
    dict(id="TC-32", cat="F-其他附表", q="表D.0.18 水土保持措施投资汇总表对哪些措施分列投资？",
         must=["梯田", "淤地坝", "拦沙坝", "水土保持造林", "经济林", "种草", "封育治理"], forbid=[]),
    # ---- G 标准信息与正文 ----
    dict(id="TC-33", cat="G-标准信息", q="SL/T 447-2026 的标准名称是什么？",
         must=["水土保持项目前期设计文件编制技术规程"], forbid=[]),
    dict(id="TC-34", cat="G-标准信息", q="标准用词说明中“必须”表示的严格程度是什么？",
         must=["很严格", "非这样做不可"], forbid=[]),
    dict(id="TC-35", cat="G-标准信息", q="SL 447-2009 的主编单位是谁？",
         must=["水利部水利水电规划设计总院"], forbid=[]),
    dict(id="TC-36", cat="G-标准信息", q="按表D.0.5的注，项目建议书和可行性研究报告阶段水土流失现状按什么单元统计？",
         must=["县"], forbid=[]),
    dict(id="TC-37", cat="G-标准信息", q="按表D.0.6的注，初步设计阶段崩岗现状如何统计？",
         must=["逐座"], forbid=[]),
    # ---- H 反幻觉/负例 ----
    dict(id="TC-38", cat="H-反幻觉", q="表D.0.5 水土流失现状表中是否包含“冻融侵蚀”这一侵蚀类型分级？",
         must_any=["没有", "不包含", "不含", "不包括", "无此", "仅", "只有"],
         forbid=["包括冻融侵蚀", "有冻融侵蚀"]),
    dict(id="TC-39", cat="H-反幻觉", q="表D.0.19 资金筹措表中是否设有“银行贷款”栏目？",
         must_any=["没有", "不包含", "不含", "不包括", "无此", "未设", "无“"],
         forbid=["设有银行贷款", "包括银行贷款"]),
    dict(id="TC-40", cat="H-反幻觉", q="表D.0.4 土地利用现状表中是否有“梯田”这一地类栏目？",
         must_any=["没有", "不包含", "不含", "不包括", "无此", "没有“", "无“"],
         forbid=["有“梯田”栏", "包括梯田"]),
]


def norm(s: str) -> str:
    for a, b in [("～", "~"), ("＞", ">"), ("％", "%"), (" ", ""), ("\u3000", ""), (" ", "")]:
        s = s.replace(a, b)
    for ch in "*#`":
        s = s.replace(ch, "")
    return s.lower().replace(" ", "")


def create_session(name):
    r = requests.post(f"{RAGFLOW_BASE}/api/v1/chats/{CHAT_ID}/sessions",
                      headers={"Authorization": f"Bearer {RAGFLOW_API_KEY}"}, json={"name": name},
                      verify=False, timeout=30)
    d = r.json()
    assert d.get("code") == 0, d
    return d["data"]["id"]


def ask(session_id, question):
    r = requests.post(f"{RAGFLOW_BASE}/api/v1/chats/{CHAT_ID}/completions",
                      headers={"Authorization": f"Bearer {RAGFLOW_API_KEY}"},
                      json={"question": question, "stream": False, "session_id": session_id},
                      verify=False, timeout=180)
    data = r.json().get("data", {})
    ref = data.get("reference") or {}
    ref_chunks = ref.get("chunks", []) if isinstance(ref, dict) else []
    return (data.get("answer", ""),
            [rc.get("id", "")[:8] for rc in ref_chunks],
            sorted({rc.get("document_name", "") for rc in ref_chunks}))


def grade(case, answer_norm):
    misses = [k for k in case.get("must", []) if norm(k) not in answer_norm]
    hits_any = [k for k in case.get("must_any", []) if norm(k) in answer_norm]
    bad = [k for k in case.get("forbid", []) if norm(k) in answer_norm]
    if case.get("must") and misses:
        return "FAIL", misses, bad
    if case.get("must_any") and not hits_any:
        return "FAIL", ["(none of must_any hit)"], bad
    if bad:
        return "FAIL", [], bad
    return "PASS", [], []


def main():
    results = []
    for i, case in enumerate(CASES, 1):
        try:
            sid = create_session(f"测试用例-{case['id']}-{case['cat']}")
            answer, ref_ids, docs = ask(sid, question=case["q"])
        except Exception as e:  # noqa: BLE001
            answer, ref_ids, docs = f"<ERROR {e}>", [], []
        verdict, misses, bad = grade(case, norm(answer))
        row = dict(id=case["id"], cat=case["cat"], q=case["q"], verdict=verdict,
                   misses=misses, forbid_hit=bad, refs=ref_ids[:5], docs=docs,
                   answer=answer)
        results.append(row)
        print(f"[{i:02d}/{len(CASES)}] {case['id']} {verdict}  miss={misses} forbid={bad}")
    json.dump(results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n_pass = sum(1 for r in results if r["verdict"] == "PASS")
    print(f"\n==== {n_pass}/{len(results)} PASS ====")
    for r in results:
        if r["verdict"] == "FAIL":
            print(f"  FAIL {r['id']} q={r['q'][:40]} miss={r['misses']} forbid={r['forbid_hit']}")


if __name__ == "__main__":
    main()
