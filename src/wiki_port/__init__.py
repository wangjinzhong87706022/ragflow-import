"""wiki_port —— RAGFlow wiki 的本地移植层（图抽取复用 graphrag_port，页面/互链/场景在此）。

导出：
  SCENARIOS / get_profile / ScenarioProfile   三场景画像
  PageSynthesizer                             实体 → Markdown 页面合成（带缓存）
  crosslinker                                 互链 / See also / 反向索引 / 时间线（纯函数）
"""
from wiki_port.scenario_config import SCENARIOS, ScenarioProfile, get_profile
from wiki_port.page_synthesizer import PageSynthesizer
from wiki_port import crosslinker

__all__ = [
    "SCENARIOS", "ScenarioProfile", "get_profile",
    "PageSynthesizer", "crosslinker",
]
