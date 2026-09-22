"""Wiki 三场景画像（决策点，配置写死）。

每个画像描述：目标数据集、抽取用的 entity/relation 类型、页面聚合单位、
主视图类型、以及可选的反向索引规则。build_wiki_local.py 据画像驱动全流程。

对应 docs 里的三个业务场景：
  - regulation（ds1）：法规知识库——每部法规一页，条款互链，事项→条款反查。
  - topology（ds3）：河网-测站-水库拓扑——可交互关系图谱。
  - case（ds3）：历史洪水案例——每场洪水一页，互链工程/措施/后果。
"""
from dataclasses import dataclass
from typing import List, Optional, Dict


@dataclass
class ScenarioProfile:
    """单个 wiki 场景的可执行画像。"""

    key: str                         # regulation / topology / case
    title: str                       # 中文标题（index / HTML 标题用）
    dataset: str                     # 目标数据集 key（ds1 / ds3）
    entity_types: List[str]          # 抽取实体类型（喂 GraphExtractor）
    relation_types: List[str]        # 允许的关系谓词（用于边过滤/文案）
    page_unit: str                   # 页面聚合单位：某实体类型 或 "all"
    view: str                        # 主视图：pages / canvas
    reverse_index: Optional[Dict[str, str]] = None  # {"source_type":..,"target_type":..,"via":..} 反查规则
    timeline: bool = False           # 页面内是否渲染时间线小节
    notes: str = ""                  # 备注

    def is_valid(self) -> bool:
        return bool(
            self.key and self.dataset and self.entity_types
            and self.relation_types and self.page_unit and self.view in ("pages", "canvas")
        )


SCENARIOS: Dict[str, "ScenarioProfile"] = {
    # 场景二：法规知识库
    "regulation": ScenarioProfile(
        key="regulation",
        title="法规知识库",
        dataset="ds1",
        entity_types=["Regulation", "Article", "Authority", "Subject", "Person", "Parameter"],
        relation_types=["regulates", "cites", "issued_by", "applies_to", "amends"],
        page_unit="Regulation",
        view="pages",
        reverse_index={"source_type": "Subject", "target_type": "Article", "via": "applies_to"},
        notes="每部法规一页，条款(Article)成页内小节并互链；事项(Subject)→适用条款反查。",
    ),
    # 场景三：河网拓扑图谱
    "topology": ScenarioProfile(
        key="topology",
        title="河网-测站-水库拓扑",
        dataset="ds3",
        entity_types=["Station", "River", "Basin", "Structure", "CrossSection", "Region"],
        relation_types=["located_in", "part_of", "regulates", "upstream_of", "downstream_of"],
        page_unit="all",
        view="canvas",
        notes="测站-位于->河流-属于->流域，水库-调控->断面；投影为可交互力导向图谱。",
    ),
    # 场景四：历史洪水案例库
    "case": ScenarioProfile(
        key="case",
        title="历史洪水案例库",
        dataset="ds3",
        entity_types=["FloodEvent", "Structure", "Measure", "Consequence", "Station", "Parameter"],
        relation_types=["occurred_at", "affected", "mitigated_by", "caused_by", "triggered", "measured_at"],
        page_unit="FloodEvent",
        view="pages",
        timeline=True,
        notes="每场洪水一案例页；时间线按 triggered/occurred_at 边排序，互链涉及工程与调度措施。",
    ),
}


def get_profile(scenario: str) -> ScenarioProfile:
    """按 key 取画像；未知 key 抛错并列出可选值。"""
    if scenario not in SCENARIOS:
        raise KeyError(f"未知场景 {scenario!r}，可选：{sorted(SCENARIOS)}")
    return SCENARIOS[scenario]
