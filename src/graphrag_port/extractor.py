"""移植自 rag/graphrag/general/extractor.py（Extractor 基类）。

改动仅限依赖替换，合并/摘要逻辑逐字保留：
  - has_canceled / TaskCanceledException -> 去除（本地无任务取消，task_id 一律为空）
  - thread_pool_exec(get_llm_cache)      -> local_utils 的同步文件缓存
  - message_fit_in                       -> 直通（system 在本流程中恒为空串）
  - truncate / num_tokens_from_string    -> local_utils 实现
"""
import asyncio
import logging
import os
import re
from collections import Counter, defaultdict
from copy import deepcopy

import networkx as nx

from graphrag_port.graph_prompt_general import SUMMARIZE_DESCRIPTIONS_PROMPT
from graphrag_port.local_utils import (
    GraphChange,
    chat_limiter,
    flat_uniq_list,
    get_from_to,
    get_llm_cache,
    handle_single_entity_extraction,
    handle_single_relationship_extraction,
    set_llm_cache,
    split_string_by_multi_markers,
    truncate,
)

GRAPH_FIELD_SEP = "<SEP>"
DEFAULT_ENTITY_TYPES = ["organization", "person", "geo", "event", "category"]
ENTITY_EXTRACTION_MAX_GLEANINGS = 2
MAX_CONCURRENT_PROCESS_AND_EXTRACT_CHUNK = int(os.environ.get("MAX_CONCURRENT_PROCESS_AND_EXTRACT_CHUNK", 10))


class Extractor:
    _llm = None

    def __init__(self, llm_invoker, language: str | None = "English", entity_types: list[str] | None = None):
        self._llm = llm_invoker
        self._language = language
        self._entity_types = entity_types or DEFAULT_ENTITY_TYPES

    @staticmethod
    def _normalize_response_text(response):
        if isinstance(response, (list, tuple)):
            response = response[0] if response else ""
        if response is None:
            return ""
        return response if isinstance(response, str) else str(response)

    @staticmethod
    def _is_truncated_cache(response):
        return len((response or "").strip()) <= 1

    async def _async_chat(self, system, history, gen_conf={}, task_id=""):
        hist = deepcopy(history)
        conf = deepcopy(gen_conf)
        response = get_llm_cache(self._llm.llm_name, system, hist, conf)
        response = self._normalize_response_text(response)
        if self._is_truncated_cache(response):
            response = ""
        if response:
            return response
        response = ""
        for attempt in range(3):
            try:
                response = await self._llm.async_chat(system, hist, conf)
                response = self._normalize_response_text(response)
                response = re.sub(r"^.*</think>", "", response, flags=re.DOTALL)
                if response.find("**ERROR**") >= 0:
                    raise Exception(response)
                if not self._is_truncated_cache(response):
                    set_llm_cache(self._llm.llm_name, system, response, history, gen_conf)
                break
            except asyncio.TimeoutError:
                logging.warning("_async_chat timed out")
                raise  # timeout 不是瞬态错误，不重试
            except Exception as e:
                logging.warning(f"LLM call attempt {attempt + 1} failed: {e}")
                if attempt == 2:
                    raise
        return response

    def _entities_and_relations(self, chunk_key: str, records: list, tuple_delimiter: str):
        maybe_nodes = defaultdict(list)
        maybe_edges = defaultdict(list)
        ent_types = [t.lower() for t in self._entity_types]
        for record in records:
            record_attributes = split_string_by_multi_markers(record, [tuple_delimiter])

            if_entities = handle_single_entity_extraction(record_attributes, chunk_key)
            if if_entities is not None and if_entities.get("entity_type", "unknown").lower() in ent_types:
                maybe_nodes[if_entities["entity_name"]].append(if_entities)
                continue

            if_relation = handle_single_relationship_extraction(record_attributes, chunk_key)
            if if_relation is not None:
                maybe_edges[(if_relation["src_id"], if_relation["tgt_id"])].append(if_relation)
        return dict(maybe_nodes), dict(maybe_edges)

    async def __call__(self, doc_id: str, chunks: list[str], callback=None, task_id: str = ""):
        self.callback = callback
        start_ts = asyncio.get_running_loop().time()

        out_results = []
        error_count = 0
        max_errors = int(os.environ.get("GRAPHRAG_MAX_ERRORS", "3"))
        limiter = asyncio.Semaphore(MAX_CONCURRENT_PROCESS_AND_EXTRACT_CHUNK)

        async def worker(chunk_key_dp, idx, total):
            nonlocal error_count
            async with limiter:
                try:
                    await self._process_single_content(chunk_key_dp, idx, total, out_results, task_id)
                except Exception as e:
                    error_count += 1
                    error_msg = f"Error processing chunk {idx + 1}/{total}: {e}"
                    logging.warning(error_msg)
                    if self.callback:
                        self.callback(msg=error_msg)
                    if error_count > max_errors:
                        raise Exception(f"Maximum error count ({max_errors}) reached. Last error: {e}")

        tasks = [asyncio.create_task(worker((doc_id, ck), i, len(chunks))) for i, ck in enumerate(chunks)]
        try:
            await asyncio.gather(*tasks, return_exceptions=False)
        except Exception:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        maybe_nodes = defaultdict(list)
        maybe_edges = defaultdict(list)
        sum_token_count = 0
        for m_nodes, m_edges, token_count in out_results:
            for k, v in m_nodes.items():
                maybe_nodes[k].extend(v)
            for k, v in m_edges.items():
                maybe_edges[tuple(sorted(k))].extend(v)
            sum_token_count += token_count
        now = asyncio.get_running_loop().time()
        if self.callback:
            self.callback(msg=f"Entities and relationships extraction done, {len(maybe_nodes)} nodes, {len(maybe_edges)} edges, {sum_token_count} tokens, {now - start_ts:.2f}s.")

        all_entities_data = []
        await asyncio.gather(*[self._merge_nodes(en_nm, ents, all_entities_data, task_id) for en_nm, ents in maybe_nodes.items()])

        all_relationships_data = []
        await asyncio.gather(*[self._merge_edges(src, tgt, rels, all_relationships_data, task_id) for (src, tgt), rels in maybe_edges.items()])

        now = asyncio.get_running_loop().time()
        if self.callback:
            self.callback(msg=f"Merging done, {now - start_ts:.2f}s.")

        if not len(all_entities_data) and not len(all_relationships_data):
            logging.warning("Didn't extract any entities and relationships, maybe your LLM is not working")
        return all_entities_data, all_relationships_data

    async def _merge_nodes(self, entity_name: str, entities: list[dict], all_relationships_data, task_id=""):
        if not entities:
            return
        entity_type = sorted(
            Counter([dp["entity_type"] for dp in entities]).items(),
            key=lambda x: x[1],
            reverse=True,
        )[0][0]
        description = GRAPH_FIELD_SEP.join(sorted(set([dp["description"] for dp in entities])))
        already_source_ids = flat_uniq_list(entities, "source_id")
        description = await self._handle_entity_relation_summary(entity_name, description, task_id=task_id)
        node_data = dict(
            entity_type=entity_type,
            description=description,
            source_id=already_source_ids,
        )
        node_data["entity_name"] = entity_name
        all_relationships_data.append(node_data)

    async def _merge_edges(self, src_id: str, tgt_id: str, edges_data: list[dict], all_relationships_data=None, task_id=""):
        if not edges_data:
            return
        weight = sum([edge["weight"] for edge in edges_data])
        description = GRAPH_FIELD_SEP.join(sorted(set([edge["description"] for edge in edges_data])))
        description = await self._handle_entity_relation_summary(f"{src_id} -> {tgt_id}", description, task_id=task_id)
        keywords = flat_uniq_list(edges_data, "keywords")
        source_id = flat_uniq_list(edges_data, "source_id")
        edge_data = dict(src_id=src_id, tgt_id=tgt_id, description=description, keywords=keywords, weight=weight, source_id=source_id)
        all_relationships_data.append(edge_data)

    async def _merge_graph_nodes(self, graph: nx.Graph, nodes: list[str], change: GraphChange, task_id=""):
        if len(nodes) <= 1:
            return
        change.added_updated_nodes.add(nodes[0])
        change.removed_nodes.update(nodes[1:])
        nodes_set = set(nodes)
        node0_attrs = graph.nodes[nodes[0]]
        node0_neighbors = set(graph.neighbors(nodes[0]))
        for node1 in nodes[1:]:
            node1_attrs = graph.nodes[node1]
            node0_attrs["description"] += f"{GRAPH_FIELD_SEP}{node1_attrs['description']}"
            node0_attrs["source_id"] = sorted(set(node0_attrs["source_id"] + node1_attrs["source_id"]))
            for neighbor in list(graph.neighbors(node1)):
                change.removed_edges.add(get_from_to(node1, neighbor))
                if neighbor not in nodes_set:
                    edge1_attrs = graph.get_edge_data(node1, neighbor)
                    if neighbor in node0_neighbors:
                        change.added_updated_edges.add(get_from_to(nodes[0], neighbor))
                        edge0_attrs = graph.get_edge_data(nodes[0], neighbor)
                        edge0_attrs["weight"] += edge1_attrs["weight"]
                        edge0_attrs["description"] += f"{GRAPH_FIELD_SEP}{edge1_attrs['description']}"
                        for attr in ["keywords", "source_id"]:
                            edge0_attrs[attr] = sorted(set(edge0_attrs[attr] + edge1_attrs[attr]))
                        edge0_attrs["description"] = await self._handle_entity_relation_summary(f"({nodes[0]}, {neighbor})", edge0_attrs["description"], task_id=task_id)
                        graph.add_edge(nodes[0], neighbor, **edge0_attrs)
                    else:
                        graph.add_edge(nodes[0], neighbor, **edge1_attrs)
                        node0_neighbors.add(neighbor)
            graph.remove_node(node1)
        node0_attrs["description"] = await self._handle_entity_relation_summary(nodes[0], node0_attrs["description"], task_id=task_id)
        graph.nodes[nodes[0]].update(node0_attrs)

    async def _handle_entity_relation_summary(self, entity_or_relation_name: str, description: str, task_id="") -> str:
        summary_max_tokens = 512
        use_description = truncate(description, summary_max_tokens)
        description_list = use_description.split(GRAPH_FIELD_SEP)
        if len(description_list) <= 12:
            return use_description
        context_base = dict(
            entity_name=entity_or_relation_name,
            description_list=description_list,
            language=self._language,
        )
        use_prompt = SUMMARIZE_DESCRIPTIONS_PROMPT.format(**context_base)
        logging.info(f"Trigger summary: {entity_or_relation_name}")
        async with chat_limiter:
            summary = await self._async_chat("", [{"role": "user", "content": use_prompt}], {}, task_id)
        return summary
