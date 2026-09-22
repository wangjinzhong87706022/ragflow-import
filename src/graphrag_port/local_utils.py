"""本地版 rag/graphrag/utils.py —— 只保留纯计算函数，I/O 依赖全部替换。

与原版的对应关系：
  split_string_by_multi_markers / clean_str / is_float_regex /
  handle_single_entity_extraction / handle_single_relationship_extraction /
  pack_user_ass_to_openai_messages / flat_uniq_list / get_from_to /
  perform_variable_replacements  -> 逐字移植（get_llm_cache/set_llm_cache 改为本地文件缓存）
"""
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field

import networkx as nx  # noqa: F401  (保留以对齐原版类型)

GRAPH_FIELD_SEP = "<SEP>"


@dataclass
class GraphChange:
    """移植自 rag/graphrag/utils.py —— 记录图变更（消歧/合并时使用）。"""

    removed_nodes: set = field(default_factory=set)
    added_updated_nodes: set = field(default_factory=set)
    removed_edges: set = field(default_factory=set)
    added_updated_edges: set = field(default_factory=set)

# 并发限流：原版是模块级 asyncio.Semaphore，这里同样在导入时创建
MAX_CONCURRENT_LLM_CALLS = int(os.environ.get("GRAPHRAG_CONCURRENCY", "4"))
import asyncio

chat_limiter = asyncio.Semaphore(MAX_CONCURRENT_LLM_CALLS)


# ---------------------------------------------------------------- token 工具
_encoder = None
_encoder_tried = False


def _get_encoder():
    global _encoder, _encoder_tried
    if _encoder_tried:
        return _encoder
    _encoder_tried = True
    try:
        import tiktoken

        _encoder = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _encoder = None
    return _encoder


def num_tokens_from_string(text: str) -> int:
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text, disallowed_special=()))
        except Exception:
            pass
    # 离线回退：中文约 1 字 1 token，英文约 4 字符 1 token，取折中
    return max(1, len(text) // 2)


def truncate(text: str, num: int) -> str:
    enc = _get_encoder()
    if enc is not None:
        try:
            toks = enc.encode(text, disallowed_special=())
            if len(toks) <= num:
                return text
            return enc.decode(toks[:num]) + " [trimmed]"
        except Exception:
            pass
    return text[: num * 2]


# ---------------------------------------------------------------- LLM 文件缓存
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, str] | None = None
_CACHE_PATH = os.environ.get("GRAPHRAG_LLM_CACHE", "out/graphrag_llm_cache.json")


def _cache_load():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            _CACHE = json.load(f)
    except Exception:
        _CACHE = {}
    return _CACHE


def _cache_key(llmnm, txt, history, genconf) -> str:
    raw = str(llmnm) + "\x00" + str(txt) + "\x00" + json.dumps(history, ensure_ascii=False, sort_keys=True) + "\x00" + json.dumps(genconf, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def get_llm_cache(llmnm, txt, history, genconf):
    with _CACHE_LOCK:
        return _cache_load().get(_cache_key(llmnm, txt, history, genconf))


def set_llm_cache(llmnm, txt, v, history, genconf):
    with _CACHE_LOCK:
        cache = _cache_load()
        cache[_cache_key(llmnm, txt, history, genconf)] = v
        try:
            os.makedirs(os.path.dirname(_CACHE_PATH) or ".", exist_ok=True)
            tmp = _CACHE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
            os.replace(tmp, _CACHE_PATH)
        except Exception:
            pass


# ---------------------------------------------------------------- 逐字移植的纯函数
def perform_variable_replacements(input: str, history: list[dict] | None = None, variables: dict | None = None) -> str:
    if history is None:
        history = []
    if variables is None:
        variables = {}
    result = input

    def replace_all(input: str) -> str:
        result = input
        for k, v in variables.items():
            result = result.replace(f"{{{k}}}", str(v))
        return result

    result = replace_all(result)
    for i, entry in enumerate(history):
        if entry.get("role") == "system":
            entry["content"] = replace_all(entry.get("content") or "")
    return result


def clean_str(input) -> str:
    import html

    if not isinstance(input, str):
        return input
    result = html.unescape(input.strip())
    return re.sub(r"[\"\x00-\x1f\x7f-\x9f]", "", result)


def get_from_to(node1, node2):
    if node1 < node2:
        return (node1, node2)
    else:
        return (node2, node1)


def handle_single_entity_extraction(record_attributes: list[str], chunk_key: str):
    if len(record_attributes) < 4 or record_attributes[0] != '"entity"':
        return None
    entity_name = clean_str(record_attributes[1].upper())
    if not entity_name.strip():
        return None
    entity_type = clean_str(record_attributes[2].upper())
    entity_description = clean_str(record_attributes[3])
    entity_source_id = chunk_key
    return dict(
        entity_name=entity_name.upper(),
        entity_type=entity_type.upper(),
        description=entity_description,
        source_id=entity_source_id,
    )


def handle_single_relationship_extraction(record_attributes: list[str], chunk_key: str):
    if len(record_attributes) < 5 or record_attributes[0] != '"relationship"':
        return None
    source = clean_str(record_attributes[1].upper())
    target = clean_str(record_attributes[2].upper())
    edge_description = clean_str(record_attributes[3])
    edge_keywords = clean_str(record_attributes[4])
    edge_source_id = chunk_key
    weight = float(record_attributes[-1]) if is_float_regex(record_attributes[-1]) else 1.0
    pair = sorted([source.upper(), target.upper()])
    return dict(
        src_id=pair[0],
        tgt_id=pair[1],
        weight=weight,
        description=edge_description,
        keywords=edge_keywords,
        source_id=edge_source_id,
        metadata={"created_at": time.time()},
    )


def pack_user_ass_to_openai_messages(*args: str):
    roles = ["user", "assistant"]
    return [{"role": roles[i % 2], "content": content} for i, content in enumerate(args)]


def split_string_by_multi_markers(content: str, markers: list[str]) -> list[str]:
    if not markers:
        return [content]
    results = re.split("|".join(re.escape(marker) for marker in markers), content)
    return [r.strip() for r in results if r.strip()]


def is_float_regex(value):
    return bool(re.match(r"^[-+]?[0-9]*\.?[0-9]+$", value))


def flat_uniq_list(arr, key):
    res = []
    for a in arr:
        a = a[key]
        if isinstance(a, list):
            res.extend(a)
        else:
            res.append(a)
    return list(set(res))
