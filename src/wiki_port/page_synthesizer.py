"""Wiki 页面合成 —— 每个实体（或聚合单元）→ 百科风格 Markdown 页。

复用 graphrag_port.llm_adapter 的 LLM 契约（.llm_name/.max_length/.async_chat），
风格锚定 create_all_template_groups.WIKI_TEMPLATE.config.instruction，
使本地产物与 RAGFlow 服务端 wiki 风格一致。

增量缓存：out/wiki_ckpt/pages/<hash>.json，hash 覆盖实体名+类型+描述集+相邻边签名
+源文摘要 hash，仅改动的实体才会重新调 LLM（对齐"低维护成本"诉求）。
"""
import hashlib
import json
import os
import re
from typing import List, Optional

# 兜底风格指令（取不到 WIKI_TEMPLATE 时用），与 RAGFlow wiki 页面规范一致
_FALLBACK_INSTRUCTION = (
    "- 每页是一篇百科条目，不是扁平要点列表：\n"
    "- 1. 开篇段落（2-4 句定义它是什么），无标题。\n"
    "- 2. 用 H2 分节，每节先散文叙述再列子项。\n"
    "- 3. 关键术语首次出现加粗。\n"
    "- 4. 结尾的 \"## See also\" 由系统补，勿自行编造链接。"
)


def _style_instruction() -> str:
    try:
        from create_all_template_groups import WIKI_TEMPLATE
        return WIKI_TEMPLATE["config"]["instruction"]
    except Exception:
        return _FALLBACK_INSTRUCTION


def _strip_think(text: str) -> str:
    """部分推理网关把思维链放在正文，去掉  之前的思考段。"""
    if "</think>" in text:
        text = text.split("</think>")[-1]
    return text.strip()


def _edge_signature(entity_name: str, edges: List[dict]) -> str:
    """相邻边的稳定签名（排序、去重），供缓存键区分拓扑变化。"""
    rows = []
    for e in edges:
        s, t = e.get("source", ""), e.get("target", "")
        if entity_name not in (s, t):
            continue
        rows.append("|".join([s, t, str(e.get("predicate", "")), str(e.get("weight", ""))]))
    return "\n".join(sorted(set(rows)))


PAGE_PROMPT = """你在为水利行业知识库撰写一篇百科式的 Wiki 页面。

# 页面主体
实体名称：{name}
实体类型：{etype}
已知描述（来自多来源合并，可能重复/零散）：
{description}

# 相关关系（实体之间的连线，谓词说明关系类型）
{relations}

# 原文摘录（供事实校准，不得照抄成长列表）
{source}

# 写作要求
{instruction}

# 硬性约束
- 只依据以上信息撰写，禁止编造未出现的数字、日期、编号。
- 保留水位/流量/库容等数值与其单位、测站与工程编号。
- 用简体中文，条理清晰，篇幅适中（约 200-500 字）。
- 直接输出 Markdown 正文，不要输出额外解释，不要加代码围栏包裹整页。
"""


class PageSynthesizer:
    """把（聚合后的）实体合成为一篇 Wiki 页面，带文件级增量缓存。"""

    def __init__(self, llm, cache_dir: str, max_source_chars: int = 4000):
        self.llm = llm
        self.cache_dir = cache_dir
        self.max_source_chars = max_source_chars
        self.instruction = _style_instruction()

    # ------------------------------------------------------------ 缓存
    def _cache_key(self, name: str, etype: str, description: str,
                   edges: List[dict], src_texts: List[str]) -> str:
        src_join = "\n".join(src_texts)[: self.max_source_chars]
        payload = "\x1f".join([
            name, etype,
            "|".join(sorted((description or "").split("<SEP>"))),
            _edge_signature(name, edges),
            hashlib.sha256(src_join.encode("utf-8")).hexdigest(),
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> str:
        return os.path.join(self.cache_dir, f"{key}.json")

    # ------------------------------------------------------------ 合成
    async def synth(self, entity: dict, edges: List[dict],
                    src_texts: Optional[List[str]] = None,
                    force: bool = False) -> str:
        name = entity["name"]
        etype = entity.get("type", "")
        description = entity.get("description", "")
        src_texts = src_texts or []

        key = self._cache_key(name, etype, description, edges, src_texts)
        path = self._cache_path(key)
        if not force and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f).get("markdown", "")

        rel_lines = []
        for e in edges:
            s, t = e.get("source", ""), e.get("target", "")
            if name in (s, t):
                rel_lines.append(f"- {s} --{e.get('predicate','related')}--> {t}: {e.get('description','')}")
        relations = "\n".join(rel_lines[:30]) or "（暂无显式关系）"

        source = "\n\n".join(src_texts)[: self.max_source_chars] or "（无原文摘录，仅凭描述撰写）"

        prompt = PAGE_PROMPT.format(
            name=name, etype=etype, description=description or "（无）",
            relations=relations, source=source, instruction=self.instruction,
        )
        raw = await self.llm.async_chat("", [{"role": "user", "content": prompt}], {})
        markdown = _strip_think(raw or "")

        os.makedirs(self.cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"name": name, "type": etype, "key": key, "markdown": markdown},
                      f, ensure_ascii=False)
        return markdown
