"""Wiki 互链与反向索引（纯逻辑，不触 LLM/网络）。

规范化的中间数据结构（由 build_wiki_local 合并消歧后产出，本模块消费）：
  nodes  : dict[name] = {"name","type","description"}
  edges  : list[{"source","target","predicate","weight","description"}]
  pages  : dict[name] = markdown 正文（尚未注入 [[wikilink]]）

所有函数保持无副作用、可单测。
"""
import re
from typing import Dict, List, Iterable, Tuple


# ---------------------------------------------------------------- 名称归一
def normalize_name(name: str) -> str:
    """别名→标准名（复用 graph_optimizer.DomainKnowledgeDict，缺依赖时原样返回）。"""
    try:
        from graph_optimizer import DomainKnowledgeDict
        std = DomainKnowledgeDict.get_standard_name(name)
        return std if std else name
    except Exception:
        return name


# 噪声防护：单字符、纯数字、C1/十六进制 hash 形态不作文档级实体链接目标
_HEXHASH = re.compile(r"^[0-9a-fA-F]{6,}$")
_CODEID = re.compile(r"^[A-Za-z]{1,2}\d{1,3}$")


def _linkable(name: str) -> bool:
    n = (name or "").strip()
    if len(n) < 2:
        return False
    if _HEXHASH.match(n) or _CODEID.match(n):
        return False
    return True


# ---------------------------------------------------------------- 实体索引
def build_entity_index(nodes: Dict[str, dict]) -> List[str]:
    """返回可链接的规范实体名列表（按长度降序，长名优先匹配避免子串误链）。"""
    names = sorted(
        {normalize_name(k) for k in nodes if _linkable(k)},
        key=len, reverse=True,
    )
    return names


# ---------------------------------------------------------------- wikilink 注入
def linkify(markdown: str, entity_names: Iterable[str], self_name: str) -> str:
    """把正文里"其它实体名"的首次出现替换为 [[实体名]]；同页不链自身，已链的不重复。"""
    if not markdown:
        return markdown
    out = markdown
    self_norm = normalize_name(self_name)
    for name in entity_names:
        if normalize_name(name) == self_norm:
            continue
        if not _linkable(name):
            continue
        # 仅在尚未被 [[..]] 包裹、且非 markdown 链接[..](..) 的语境下替换第一处
        pattern = re.compile(r"(?<!\[)" + re.escape(name) + r"(?!\]\])")
        out = pattern.sub("[[" + name + "]]", out, count=1)
    return out


# ---------------------------------------------------------------- See also
def neighbors(name: str, edges: List[dict]) -> List[Tuple[str, float]]:
    """返回 (邻居名, 权重) 列表，按权重降序、去重。"""
    nn = normalize_name(name)
    agg: Dict[str, float] = {}
    for e in edges:
        s, t = normalize_name(e["source"]), normalize_name(e["target"])
        w = float(e.get("weight", 1) or 1)
        if s == nn:
            agg[t] = agg.get(t, 0.0) + w
        elif t == nn:
            agg[s] = agg.get(s, 0.0) + w
    agg.pop(nn, None)
    return sorted(agg.items(), key=lambda kv: -kv[1])


def see_also(name: str, edges: List[dict], top_k: int = 6,
             existing_links: Iterable[str] = ()) -> List[str]:
    """Top-K 相关实体名；排除已在本页正文出现过的链接，避免重复。"""
    have = {normalize_name(x) for x in existing_links}
    out = []
    for nb, _w in neighbors(name, edges):
        if nb in have or not _linkable(nb):
            continue
        out.append(nb)
        if len(out) >= top_k:
            break
    return out


# ---------------------------------------------------------------- 链接图
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def build_link_graph(pages: Dict[str, str]) -> List[dict]:
    """从各页正文抽取 [[链接]]，产出页面↔页面的有向边。"""
    links = []
    for src, md in pages.items():
        targets = set()
        for m in _WIKILINK.finditer(md or ""):
            targets.add(normalize_name(m.group(1).strip()))
        for tgt in targets:
            if tgt != normalize_name(src):
                links.append({"source": normalize_name(src), "target": tgt})
    return links


# ---------------------------------------------------------------- 反向索引
def reverse_index(nodes: Dict[str, dict], edges: List[dict],
                  source_type: str, target_type: str) -> Dict[str, List[str]]:
    """按实体类型构造反查：{source_type 实体名: [相连的 target_type 实体名...]}。

    regulation 场景：source_type=Subject(事项), target_type=Article(条款)
    → 支撑"从事项反查所有适用条款"。
    """
    def ntype(name):
        nd = nodes.get(name) or nodes.get(normalize_name(name)) or {}
        return (nd.get("type") or "").lower()

    idx: Dict[str, set] = {}
    for e in edges:
        s, t = normalize_name(e["source"]), normalize_name(e["target"])
        st, tt = ntype(s), ntype(t)
        if st == source_type.lower() and tt == target_type.lower():
            idx.setdefault(s, set()).add(t)
        elif st == target_type.lower() and tt == source_type.lower():
            idx.setdefault(t, set()).add(s)
    return {k: sorted(v) for k, v in sorted(idx.items())}


# ---------------------------------------------------------------- 时间线抽取
_YEAR = re.compile(r"(20\d{2}|19\d{2})")


def _year_of(text):
    m = _YEAR.search(text or "")
    return int(m.group(1)) if m else None


def timeline(name: str, edges: List[dict], order_predicates: Iterable[str] = None) -> List[dict]:
    """抽取与本实体相关的时间线/事件关联项（供案例页）。

    谓词匹配不要求英文字面量（中文抽取出的 predicate 常为关键词对）：
    若 order_predicates 命中子集则优先用之，否则回退到全部关联边。
    按描述/邻名中的年份升序（无年份排后，再按权重降序），并标注 has_year。
    """
    nn = normalize_name(name)
    toks = [p.lower() for p in (order_predicates or []) if p]
    incident = []
    for e in edges:
        s, t = normalize_name(e["source"]), normalize_name(e["target"])
        if s != nn and t != nn:
            continue
        other = t if s == nn else s
        if not _linkable(other):
            continue
        pred = e.get("predicate", "") or ""
        kws = e.get("keywords") or []
        hay = (pred + " " + " ".join(kws)).lower()
        hit = any(tk in hay for tk in toks) if toks else False
        desc = e.get("description", "") or ""
        incident.append({"predicate": pred, "other": other, "description": desc,
                         "_hit": hit, "_year": _year_of(desc) or _year_of(other),
                         "_w": e.get("weight", 0) or 0})
    chosen = [e for e in incident if e["_hit"]] if toks else incident
    if not chosen:
        chosen = incident
    chosen.sort(key=lambda e: (e["_year"] is None, e["_year"] if e["_year"] is not None else 0, -e["_w"]))
    for e in chosen:
        e["has_year"] = e["_year"] is not None
        e.pop("_hit", None); e.pop("_year", None); e.pop("_w", None)
    return chosen
