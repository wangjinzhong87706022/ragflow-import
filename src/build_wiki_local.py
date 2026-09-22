"""本地构建 Wiki —— 复用 graphrag_port 抽取，叠加页面合成 + 互链 + 场景投影。

与 build_graph_local.py 同源思路（拉 RAGFlow chunks -> LLM 抽取 -> 合并消歧 -> 导出），
差异是 wiki 在图之上再生成"互链百科页面"，并按三场景分别出视图：
  - regulation（ds1）：每法规一页，条款互链 + 事项→条款反查
  - topology（ds3）  ：河网/测站/水库关系投影为交互力导向图谱
  - case（ds3）      ：每场洪水一案例页 + 时间线小节

产物（out/wiki_<场景>/ 下）：
  pages/<实体名>.md         单个 wiki 页面
  index.md                  入口/目录（按场景分组）
  wiki_<场景>.json          {entities, relations, pages, link_graph, reverse_index?}
  wiki_<场景>.html          自包含双 Tab 查看器（页面浏览器 + 关系图谱）
  ../wiki_ckpt/             doc 断点 + 页面缓存（增量）

用法：
  $env:RAGFLOW_API_KEY=...; $env:LLM_API_KEY=...
  python build_wiki_local.py --scenario regulation [--smoke] [--max-docs N] [--max-chunks N] [--force]
"""
import argparse
import asyncio
import glob
import json
import logging
import os
import re
import sys
import time

import requests
import urllib3

urllib3.disable_warnings()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from graphrag_port.llm_adapter import OpenAICompatLLM
from graphrag_port.light_extractor import GraphExtractor
from wiki_port import get_profile, PageSynthesizer, crosslinker

API_BASE = os.environ.get("RAGFLOW_API_BASE", "https://labragf.openagp.top:9080/api/v1")
API_KEY = os.environ.get("RAGFLOW_API_KEY", "")
# 数据集 key -> ID（沿用现有实例；DS_ID_OVERRIDE 可整体覆盖）
DATASET_IDS = {
    "ds1": os.environ.get("DS1_ID", "fda7a510a87c11f1998b3dc126099a8d"),  # 规程与预案
    "ds3": os.environ.get("DS3_ID", "fdfee2e4a87c11f1998b3dc126099a8d"),  # 洪水资料
}
DATASET_NAMES = {"ds1": "规程与预案", "ds3": "洪水资料"}
LANGUAGE = os.environ.get("GRAPHRAG_LANGUAGE", "Chinese")

OUT_DIR = "out"
CKPT_DIR = os.path.join(OUT_DIR, "wiki_ckpt")
DOC_CKPT = os.path.join(CKPT_DIR, "docs")
PAGE_CKPT = os.path.join(CKPT_DIR, "pages")
# 冒烟模式页合成封顶（页面数=实体数，大实体集会失控；封顶以约束 LLM 成本）
SMOKE_MAX_PAGES = int(os.environ.get("WIKI_SMOKE_MAX_PAGES", "15"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_wiki")


# ---------------------------------------------------------------- RAGFlow 取数
def hdr():
    return {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


def fetch_docs(ds_id):
    docs, page = [], 1
    while True:
        r = requests.get(f"{API_BASE}/datasets/{ds_id}/documents", headers=hdr(),
                         params={"page": page, "page_size": 100}, verify=False, timeout=30)
        j = r.json()
        assert j.get("code") == 0, f"列出文档失败: {j}"
        d = j["data"].get("docs", [])
        docs.extend(d)
        if len(docs) >= j["data"].get("total", 0) or not d:
            break
        page += 1
    return docs


def fetch_chunks(ds_id, doc_id):
    chunks, page = [], 1
    while True:
        r = requests.get(f"{API_BASE}/datasets/{ds_id}/documents/{doc_id}/chunks", headers=hdr(),
                         params={"page": page, "page_size": 100}, verify=False, timeout=30)
        j = r.json()
        assert j.get("code") == 0, f"列出 chunk 失败: {j}"
        d = j["data"].get("chunks", [])
        chunks.extend(d)
        if len(chunks) >= j["data"].get("total", 0) or not d:
            break
        page += 1
    texts = []
    for c in chunks:
        if c.get("available") is False or c.get("available_int") == 0:
            continue
        t = (c.get("content") or c.get("content_with_weight") or "").strip()
        if len(t) >= 20:
            texts.append(t)
    return texts


# ---------------------------------------------------------------- 抽取 + 断点
def _cb(doc_name):
    def cb(*args, msg="", **kwargs):
        log.info(f"[{doc_name}] {msg}")
    return cb


async def extract_doc(llm, profile, doc, texts, force=False):
    doc_id, doc_name = doc["id"], doc.get("name", doc["id"][:8])
    # 断点按「文档 + 场景」隔离：同库不同场景 entity_types 画像不同，不可互用
    ckpt = os.path.join(DOC_CKPT, f"{doc_id}.{profile.key}.json")
    if os.path.exists(ckpt) and not force:
        with open(ckpt, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("chunks", 0) >= len(texts):
            log.info(f"[{doc_name}] 断点有效（{cached['chunks']}/{len(texts)}），跳过")
            return cached
    extractor = GraphExtractor(llm, language=LANGUAGE, entity_types=profile.entity_types)
    t0 = time.time()
    entities, relationships = await extractor(doc_id, texts, callback=_cb(doc_name))
    res = {"doc_id": doc_id, "doc_name": doc_name, "chunks": len(texts),
           "seconds": round(time.time() - t0, 1), "entities": entities, "relationships": relationships}
    os.makedirs(DOC_CKPT, exist_ok=True)
    with open(ckpt, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    log.info(f"[{doc_name}] 抽取 {len(entities)} 实体 / {len(relationships)} 关系 / {res['seconds']}s")
    return res


# ---------------------------------------------------------------- 合并 + 归一
def _predicate(rel):
    kw = rel.get("keywords")
    kws = kw.split("<SEP>") if isinstance(kw, str) else list(kw or [])
    kws = [k.strip().lower() for k in kws if k and k.strip()]
    return (kws[0] if kws else "related"), kws


def merge_results(profile, doc_results):
    """跨文档合并：同名聚合 + 别名归一，产出规范化 nodes/edges。"""
    nodes, edges = {}, {}
    for dr in doc_results:
        for e in dr["entities"]:
            name = crosslinker.normalize_name(e["entity_name"])
            if name not in nodes:
                nodes[name] = {"name": name, "type": e["entity_type"],
                               "desc": set(e["description"].split("<SEP>")),
                               "src": set(e["source_id"])}
            else:
                nodes[name]["desc"].update(e["description"].split("<SEP>"))
                nodes[name]["src"].update(e["source_id"])
        for r in dr["relationships"]:
            s, t = crosslinker.normalize_name(r["src_id"]), crosslinker.normalize_name(r["tgt_id"])
            if s == t:
                continue  # 跳过自环
            pred, kws = _predicate(r)
            key = tuple(sorted([s, t]))
            if key not in edges:
                edges[key] = {"source": key[0], "target": key[1], "predicate": pred,
                              "keywords": set(kws), "weight": 0.0,
                              "desc": set(r["description"].split("<SEP>")),
                              "src": set(r["source_id"])}
            ed = edges[key]
            ed["weight"] += float(r.get("weight", 1) or 1)
            ed["desc"].update(r["description"].split("<SEP>"))
            ed["keywords"].update(kws)
            ed["src"].update(r["source_id"])

    edges = {k: v for k, v in edges.items() if k[0] in nodes and k[1] in nodes}
    norm_nodes = {n: {"name": n, "type": d["type"],
                      "description": "<SEP>".join(sorted(d["desc"])),
                      "source_id": sorted(d["src"])} for n, d in nodes.items()}
    norm_edges = [{"source": d["source"], "target": d["target"], "predicate": d["predicate"],
                   "keywords": sorted(d["keywords"]), "weight": round(d["weight"], 2),
                   "description": "<SEP>".join(sorted(d["desc"]))} for d in edges.values()]
    return norm_nodes, norm_edges


# ---------------------------------------------------------------- 页面渲染
def children_of(name, edges):
    kids = []
    for e in edges:
        if e["source"] == name:
            kids.append((e["target"], e.get("predicate", "")))
        elif e["target"] == name:
            kids.append((e["source"], e.get("predicate", "")))
    return kids


# ---------------------------------------------------------------- 页面渲染
def _prioritize_nodes(nodes, edges, page_unit, max_pages):
    """需封顶时按「page_unit 优先 + 关联度降序 + 名称」选前 N 个实体；否则原序。"""
    names = list(nodes)
    if not max_pages or len(names) <= max_pages:
        return names
    deg = {}
    for e in edges:
        deg[e["source"]] = deg.get(e["source"], 0) + 1
        deg[e["target"]] = deg.get(e["target"], 0) + 1
    names.sort(key=lambda n: (nodes[n]["type"] != page_unit, -deg.get(n, 0), n))
    return names[:max_pages]


async def synth_pages(profile, nodes, edges, doc_chunks, llm, force=False, max_pages=0):
    syn = PageSynthesizer(llm, cache_dir=PAGE_CKPT)
    edge_by_node = {}
    for e in edges:
        edge_by_node.setdefault(e["source"], []).append(e)
        edge_by_node.setdefault(e["target"], []).append(e)

    pages = {}
    names = _prioritize_nodes(nodes, edges, profile.page_unit, max_pages)
    if max_pages and len(names) < len(nodes):
        log.info(f"页面合成封顶 max_pages={max_pages}（共 {len(nodes)} 实体，仅合成高优先项）")
    for i, name in enumerate(names, 1):
        nd = nodes[name]
        src_ids = nd.get("source_id", [])
        src_texts = []
        for did in src_ids[:2]:  # 控制 prompt 体积：最多取 2 篇来源文档
            src_texts.extend(doc_chunks.get(did, [])[:4])
        adj = edge_by_node.get(name, [])
        md = await syn.synth(
            {"name": name, "type": nd["type"], "description": nd["description"]},
            adj, src_texts=src_texts[:12], force=force,
        )
        # 主页面追加"关联条目"小节，让条款/成员在页内可导航
        if nd["type"] == profile.page_unit or profile.page_unit == "all":
            kids = children_of(name, edges)
            if kids:
                md = md.rstrip() + "\n\n## 关联条目\n"
                for other, pred in kids[:30]:
                    md += f"- [[{other}]]（{pred or 'related'}）\n"
        pages[name] = md or f"# {name}\n\n（抽取到实体 {nd['type']}，暂无可合成内容）\n"
        if i % 10 == 0:
            log.info(f"页面合成 {i}/{len(names)}")
    return pages


def _strip_llm_see_also(md):
    """删除模型擅自生成的 "## See also" 段（到下一个 H2 或文末），交由系统统一补。"""
    return re.sub(r"(?is)\n+#{2,}\s*see also\b.*?(?=\n#{1,2}\s|\Z)", "", md or "").rstrip()


def finalize_pages(profile, pages, nodes, edges):
    """注入互链 + See also（+ regulation 反查小节 / case 时间线小节）。"""
    index = crosslinker.build_entity_index(nodes)
    out = {}
    for name, md in pages.items():
        linked = crosslinker.linkify(_strip_llm_see_also(md), index, name)
        existing = re.findall(r"\[\[([^\]]+)\]\]", linked)
        sa = crosslinker.see_also(name, edges, existing_links=existing)
        if sa:
            linked = linked.rstrip() + "\n\n## See also\n" + "\n".join(f"- [[{x}]]" for x in sa) + "\n"
        if profile.timeline:
            tl = crosslinker.timeline(name, edges, order_predicates=["triggered", "occurred_at"])
            if tl:
                header = "## 时间线" if any(t.get("has_year") for t in tl) else "## 事件关联"
                rows = []
                for t in tl[:20]:
                    m = re.search(r"(?:19|20)\d{2}", t["description"] + " " + t["other"])
                    pre = f"{m.group(0)}年、" if m else ""
                    pred = f"{t['predicate']}: " if t["predicate"] else ""
                    rows.append(f"- {pre}{pred}[[{t['other']}]] {t['description'][:60]}".rstrip())
                linked = linked.rstrip() + "\n\n" + header + "\n" + "\n".join(rows) + "\n"
        out[name] = linked
    return out


def build_index_md(profile, pages, nodes, ridx):
    lines = [f"# {profile.title}\n", f"> 数据集 {profile.dataset}（{DATASET_NAMES.get(profile.dataset, '')}）｜场景画像：{profile.key}\n"]
    if ridx:
        lines.append("\n## 事项 → 适用条款（反查）\n")
        for subject, articles in list(ridx.items())[:100]:
            links = "、".join(f"[[{a}]]" for a in articles[:20])
            lines.append(f"- **[[{subject}]]** → {links}\n")
    primary = [n for n in pages if nodes[n]["type"] == profile.page_unit] or list(pages)
    lines.append("\n## 条目索引\n")
    by_type = {}
    for n in sorted(pages):
        by_type.setdefault(nodes[n]["type"], []).append(n)
    for t in sorted(by_type, key=lambda x: (x != profile.page_unit, x)):
        lines.append(f"\n### {t}（{len(by_type[t])}）\n")
        for n in by_type[t]:
            lines.append(f"- [[{n}]]" + (" ⭐" if n in primary else "") + "\n")
    return "".join(lines)


# ---------------------------------------------------------------- 导出
def safe_filename(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name)[:80]


def _write_pages(pdir, pages):
    """先清 pages/ 旧 .md，再写本次页面，使目录与本次产出严格一致（避免换封顶值重跑残留）。"""
    os.makedirs(pdir, exist_ok=True)
    for stale in glob.glob(os.path.join(pdir, "*.md")):
        try:
            os.remove(stale)
        except OSError:
            pass
    written = []
    for name, md in pages.items():
        p = os.path.join(pdir, f"{safe_filename(name)}.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(md)
        written.append(p)
    return written


def export(profile, nodes, edges, pages, ridx, link_graph):
    scen = profile.key
    base = os.path.join(OUT_DIR, f"wiki_{scen}")
    pdir = os.path.join(base, "pages")
    _write_pages(pdir, pages)
    index_md = build_index_md(profile, pages, nodes, ridx)
    with open(os.path.join(base, "index.md"), "w", encoding="utf-8") as f:
        f.write(index_md)

    data = {
        "scenario": scen, "title": profile.title, "dataset": profile.dataset,
        "entities": [
            {"name": n, "type": d["type"], "description": d["description"],
             "page_html": pages.get(n, "")} for n, d in nodes.items()
        ],
        "relations": edges,
        "pages": pages,
        "link_graph": link_graph,
        "index_md": index_md,
    }
    if ridx is not None:
        data["reverse_index"] = ridx
    jpath = os.path.join(base, f"wiki_{scen}.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    hpath = os.path.join(base, f"wiki_{scen}.html")
    render_viewer(data, nodes, edges, hpath, profile)
    return base, jpath, hpath


def _graph_with_layout(nodes, edges):
    g_nodes = [{"id": n, "entity_type": d["type"], "description": d["description"]} for n, d in nodes.items()]
    g_edges = [{"source": e["source"], "target": e["target"], "weight": e.get("weight", 1),
                "keywords": ",".join(e.get("keywords", [])), "description": e.get("description", "")}
               for e in edges]
    graph = {"nodes": g_nodes, "edges": g_edges}
    try:
        from build_graph_local import compute_layout
        graph = compute_layout(graph)
    except Exception as ex:  # networkx 缺失等情况：退回前端随机+模拟
        log.warning(f"布局预计算不可用（{ex}），前端将实时模拟")
    return graph


# HTML 模板用 raw string，避免 \n 被 Python 解析（项目经验）
VIEWER_TMPL = r"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>__TITLE__</title>
<style>
html,body{margin:0;height:100%;font-family:'Microsoft YaHei',sans-serif;background:#0f1620;color:#dbe6f2}
#top{position:fixed;top:0;left:0;right:0;height:42px;background:#16212e;display:flex;align-items:center;padding:0 12px;gap:8px;z-index:20;border-bottom:1px solid #24344a}
#top b{margin-right:12px}
.tab{padding:5px 12px;border-radius:6px;cursor:pointer;font-size:14px;background:#1f2d3e}
.tab.on{background:#3a86ff;color:#fff}
#wrap{position:absolute;top:42px;bottom:0;left:0;right:0;display:flex}
#side{width:260px;overflow:auto;border-right:1px solid #24344a;padding:8px;font-size:13px}
#side a{display:block;color:#9fc4ea;text-decoration:none;padding:3px 6px;border-radius:4px;cursor:pointer}
#side a:hover{background:#1f2d3e}
#main{flex:1;overflow:auto;position:relative}
#page{padding:22px 30px;max-width:820px;line-height:1.8}
#page h1{color:#fff} #page h2{border-bottom:1px solid #24344a;padding-bottom:4px;margin-top:22px}
#page a.wl{color:#ffb454;cursor:pointer;text-decoration:none;border-bottom:1px dashed #ffb454}
#page code{background:#1f2d3e;padding:1px 4px;border-radius:3px}
#graph{position:absolute;inset:0;display:none}
canvas{display:block;width:100%;height:100%}
#tip{position:fixed;display:none;max-width:420px;background:#ffffdd;color:#222;padding:8px 10px;border-radius:6px;font-size:12px;line-height:1.5;white-space:pre-wrap;z-index:30;box-shadow:0 2px 8px #0008}
#search{padding:6px;border-bottom:1px solid #24344a}
#search input{width:96%;padding:5px;border-radius:5px;border:1px solid #2c405a;background:#0f1620;color:#dbe6f2}
.rev{font-size:12px;color:#8fa8c4;margin:4px 0}
</style></head><body>
<div id="top"><b>__TITLE__</b><span class="tab on" data-v="page">页面</span><span class="tab" data-v="graph">关系图谱</span></div>
<div id="wrap">
 <div id="side"><div id="search"><input id="q" placeholder="搜索条目/事项"></div><div id="list"></div></div>
 <div id="main"><div id="page"></div><div id="graph"><canvas id="c"></canvas></div></div>
</div>
<div id="tip"></div>
<script>
var DATA=__DATA__, G=__GRAPH__, COLORS=__COLORS__;
var pages=DATA.pages, nodes=DATA.entities, idx={};
nodes.forEach(function(n){idx[n.name]=n;});
function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
// 极简 Markdown 渲染（标题/加粗/列表/行内码/[[wikilink]]）
function md2html(md){
  var lines=(md||'').split(/\r?\n/), out=[], inUL=false;
  function inline(t){
    t=esc(t);
    t=t.replace(/\[\[([^\]]+)\]\]/g,function(_,x){return '<a class="wl" data-t="'+x.replace(/"/g,'')+'">'+x+'</a>';});
    t=t.replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>');
    t=t.replace(/`([^`]+)`/g,'<code>$1</code>');
    return t;
  }
  lines.forEach(function(L){
    var m;
    if((m=L.match(/^(#{1,4})\s+(.*)/))){if(inUL){out.push('</ul>');inUL=false;}var h=m[1].length;out.push('<h'+h+'>'+inline(m[2])+'</h'+h+'>');return;}
    if(/^\s*[-*]\s+/.test(L)){if(!inUL){out.push('<ul>');inUL=true;}out.push('<li>'+inline(L.replace(/^\s*[-*]\s+/,''))+'</li>');return;}
    if(inUL){out.push('</ul>');inUL=false;}
    if(L.trim()===''){out.push('<div style="height:8px"></div>');}else{out.push('<p>'+inline(L)+'</p>');}
  });
  if(inUL)out.push('</ul>');
  return out.join('\n');
}
function openPage(name){
  var md=pages[name]; if(md===undefined){md='# '+name+'\n\n（无该页面）';}
  var el=document.getElementById('page');
  el.innerHTML='<h1>'+esc(name)+'</h1>'+md2html(md.replace(/^#\s+[^\n]*\n/,''));
  el.scrollTop=0; document.getElementById('main').scrollTop=0;
  el.querySelectorAll('a.wl').forEach(function(a){a.onclick=function(){openPage(a.getAttribute('data-t'));};});
}
// 侧栏：可选 反查区(事项→条款) + 全部条目
function buildSide(){
  var list=document.getElementById('list'), html='';
  if(DATA.reverse_index&&Object.keys(DATA.reverse_index).length){
    html+='<div class="rev">事项 → 条款 反查</div>';
    Object.keys(DATA.reverse_index).slice(0,60).forEach(function(s){
      html+='<a data-open="'+s+'">◆ '+esc(s)+'</a>';});
  }
  Object.keys(pages).sort().forEach(function(n){html+='<a data-open="'+esc(n)+'">'+esc(n)+'</a>';});
  list.innerHTML=html;
  list.querySelectorAll('a[data-open]').forEach(function(a){a.onclick=function(){openPage(a.getAttribute('data-open'));};});
}
buildSide(); openPage(Object.keys(pages)[0]||(nodes[0]&&nodes[0].name)||'');
document.getElementById('q').onkeydown=function(e){if(e.key!=='Enter')return;var v=e.target.value.trim();
  var hit=Object.keys(pages).find(function(n){return n.indexOf(v)>=0;});if(hit){openPage(hit);}else if(DATA.reverse_index&&DATA.reverse_index[v]){openPage(DATA.reverse_index[v][0]);}};
// Tab 切换
var tabs=document.querySelectorAll('.tab');
tabs.forEach(function(t){t.onclick=function(){tabs.forEach(function(x){x.classList.remove('on');});t.classList.add('on');
  var v=t.getAttribute('data-v');document.getElementById('page').style.display=v==='page'?'block':'none';
  document.getElementById('graph').style.display=v==='graph'?'block':'none';if(v==='graph'){sizeCanvas();}};});
// ---------------- 力导向 canvas ----------------
var cv=document.getElementById('c'),ctx=cv.getContext('2d'),DPR=devicePixelRatio||1,W=0,H=0;
function sizeCanvas(){W=cv.clientWidth;H=cv.clientHeight;cv.width=W*DPR;cv.height=H*DPR;}
var hasPos=G.nodes.length>0&&isFinite(G.nodes[0].x);
var nn=[],ei={},adj={};
G.nodes.forEach(function(n,i){ei[n.id]=i;nn.push({id:n.id,type:n.entity_type,desc:(n.description||'').split('<SEP>').join('\n'),x:hasPos?n.x:(Math.random()-0.5)*800,y:hasPos?n.y:(Math.random()-0.5)*800,vx:0,vy:0,r:4,deg:0});});
var E=[];G.edges.forEach(function(e){var a=ei[e.source],b=ei[e.target];if(a==null||b==null)return;E.push({a:a,b:b,w:e.weight,kw:e.keywords,desc:(e.description||'').split('<SEP>').join('\n')});nn[a].deg++;nn[b].deg++;});
nn.forEach(function(n){n.r=4+Math.sqrt(n.deg)*1.6;});
E.forEach(function(e,i){(adj[e.a]=adj[e.a]||[]).push(i);(adj[e.b]=adj[e.b]||[]).push(i);});
var S=1,OX=0,OY=0,alpha=hasPos?0:1;
function toScr(x,y){return[x*S+OX,y*S+OY];}function fromScr(x,y){return[(x-OX)/S,(y-OY)/S];}
function tick(){if(alpha<0.003)return;alpha*=0.996;
  for(var i=0;i<nn.length;i++){var a=nn[i];for(var j=i+1;j<nn.length;j++){var b=nn[j];var dx=a.x-b.x,dy=a.y-b.y,d2=dx*dx+dy*dy;if(d2<0.01){dx=Math.random()-0.5;dy=Math.random()-0.5;d2=dx*dx+dy*dy+.01;}var d=Math.sqrt(d2),f=Math.min(10,1500/d2)*alpha;a.vx+=dx/d*f;a.vy+=dy/d*f;b.vx-=dx/d*f;b.vy-=dy/d*f;}}
  for(var k=0;k<E.length;k++){var p=nn[E[k].a],q=nn[E[k].b];var dx=q.x-p.x,dy=q.y-p.y,d=Math.sqrt(dx*dx+dy*dy)||1,f=Math.min(10,(d-90)*0.02)*alpha;p.vx+=dx/d*f;p.vy+=dy/d*f;q.vx-=dx/d*f;q.vy-=dy/d*f;}
  for(var t=0;t<nn.length;t++){var n=nn[t];n.vx-=n.x*0.01*alpha;n.vy-=n.y*0.01*alpha;n.vx*=0.8;n.vy*=0.8;var sp=Math.sqrt(n.vx*n.vx+n.vy*n.vy);if(sp>25){n.vx*=25/sp;n.vy*=25/sp;}n.x+=n.vx;n.y+=n.vy;}}
var sel=-1;
function draw(){ctx.setTransform(DPR,0,0,DPR,0,0);ctx.fillStyle='#0f1620';ctx.fillRect(0,0,W,H);ctx.setTransform(DPR*S,0,0,DPR*S,DPR*OX,DPR*OY);
  var nbr={};if(sel>=0){nbr[sel]=1;(adj[sel]||[]).forEach(function(e){nbr[E[e].a]=1;nbr[E[e].b]=1;});}
  E.forEach(function(e){var on=sel<0||(nbr[e.a]&&nbr[e.b]);ctx.strokeStyle=on?'rgba(160,190,220,'+(sel>=0?0.75:0.35)+')':'rgba(160,190,220,0.06)';ctx.lineWidth=Math.min(5,1+e.w/6)/S*0.8;ctx.beginPath();ctx.moveTo(nn[e.a].x,nn[e.a].y);ctx.lineTo(nn[e.b].x,nn[e.b].y);ctx.stroke();});
  nn.forEach(function(n,i){var on=sel<0||nbr[i];ctx.globalAlpha=on?1:0.15;ctx.beginPath();ctx.arc(n.x,n.y,n.r,0,6.2832);ctx.fillStyle=COLORS[(n.type||'').toUpperCase()]||'#9ecbff';ctx.fill();ctx.lineWidth=1.2/S;ctx.strokeStyle=(i===sel)?'#fff':'#0e1620';ctx.stroke();if((S*n.r>7||nbr[i]||i===sel)&&on){ctx.font=(12/S)+'px "Microsoft YaHei"';ctx.fillStyle='#e8f2fb';ctx.fillText(n.id.length>14?n.id.slice(0,13)+'…':n.id,n.x+n.r+2/S,n.y+4/S);}ctx.globalAlpha=1;});}
function loop(){tick();draw();requestAnimationFrame(loop);}
function pick(mx,my){var p=fromScr(mx,my),best=-1,bd=1e9;for(var i=0;i<nn.length;i++){var dx=p[0]-nn[i].x,dy=p[1]-nn[i].y,d=dx*dx+dy*dy,r=nn[i].r+6/S;if(d<r*r&&d<bd){bd=d;best=i;}}return best;}
var tip=document.getElementById('tip');
function showTip(html,x,y){tip.style.display='block';tip.textContent=html;tip.style.left=Math.min(x+14,innerWidth-440)+'px';tip.style.top=Math.min(y+14,innerHeight-200)+'px';}
var drag=-1,pan=null,moved=false;
cv.onmousedown=function(ev){moved=false;var h=pick(ev.clientX,ev.clientY-42);if(h>=0){drag=h;sel=h;}else{pan=[ev.clientX-OX,ev.clientY-42-OY];}};
cv.onmousemove=function(ev){var my=ev.clientY-42;if(drag>=0){var p=fromScr(ev.clientX,my);nn[drag].x=p[0];nn[drag].y=p[1];if(nn.length<800)alpha=Math.max(alpha,0.3);moved=true;return;}if(pan){OX=ev.clientX-pan[0];OY=my-pan[1];moved=true;return;}var n=pick(ev.clientX,my);if(n>=0){var e=nn[n];showTip(e.id+'  ['+e.type+']\n'+e.desc.slice(0,400)+'\n（双击打开页面）',ev.clientX,ev.clientY);cv.style.cursor='pointer';return;}tip.style.display='none';cv.style.cursor='default';};
addEventListener('mouseup',function(){drag=-1;pan=null;});
cv.onwheel=function(ev){ev.preventDefault();var k=Math.pow(1.15,ev.deltaY>0?-1:1);var p=fromScr(ev.clientX,ev.clientY-42);S*=k;OX=ev.clientX-p[0]*S;OY=ev.clientY-42-p[1]*S;};
cv.ondblclick=function(ev){var n=pick(ev.clientX,ev.clientY-42);if(n>=0){document.querySelector('.tab[data-v=page]').click();openPage(nn[n].id);}};
if(!hasPos){for(var w=0;w<250;w++)tick();}
sizeCanvas();addEventListener('resize',sizeCanvas);
if(G.nodes.length){var xs=nn.map(function(n){return n.x;}),ys=nn.map(function(n){return n.y;});var x0=Math.min.apply(0,xs),x1=Math.max.apply(0,xs),y0=Math.min.apply(0,ys),y1=Math.max.apply(0,ys);S=Math.min(W/(x1-x0+200),H/(y1-y0+200),1.5);if(!isFinite(S)||S<=0)S=1;OX=W/2-(x0+x1)/2*S;OY=H/2-(y0+y1)/2*S;}
loop();
</script></body></html>"""


def render_viewer(data, nodes, edges, hpath, profile):
    graph = _graph_with_layout(nodes, edges)
    palette = ["#e74c3c", "#3498db", "#9b59b6", "#f1c40f", "#2ecc71", "#e67e22",
               "#1abc9c", "#34495e", "#2980b9", "#8e44ad", "#16a085", "#d35400"]
    types = sorted({d["type"] for d in nodes.values()})
    colors = {t.upper(): c for t, c in zip(types, palette)}
    html = (VIEWER_TMPL
            .replace("__TITLE__", profile.title)
            .replace("__DATA__", json.dumps(data, ensure_ascii=False))
            .replace("__GRAPH__", json.dumps(graph, ensure_ascii=False))
            .replace("__COLORS__", json.dumps(colors, ensure_ascii=False)))
    with open(hpath, "w", encoding="utf-8") as f:
        f.write(html)
    return hpath


# ---------------------------------------------------------------- 主流程
async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True, choices=["regulation", "topology", "case"])
    ap.add_argument("--smoke", action="store_true", help="冒烟：1 文档 8 chunk")
    ap.add_argument("--max-docs", type=int, default=0)
    ap.add_argument("--max-chunks", type=int, default=0)
    ap.add_argument("--max-pages", type=int, default=0, help="只合成前 N 页（优先主实体）；smoke 默认 " + str(SMOKE_MAX_PAGES))
    ap.add_argument("--force", action="store_true", help="忽略断点与页面缓存重抽/重合成")
    args = ap.parse_args()

    if not API_KEY:
        sys.exit("[ERROR] 未设置 RAGFLOW_API_KEY")
    profile = get_profile(args.scenario)
    ds_id = os.environ.get("DS_ID_OVERRIDE") or DATASET_IDS[profile.dataset]
    llm = OpenAICompatLLM()

    docs = fetch_docs(ds_id)
    docs = [d for d in docs if d.get("run") == "DONE" or d.get("status") == "1"] or docs
    log.info(f"场景 {profile.key} | 数据集 {profile.dataset} 共 {len(docs)} 文档")
    if args.max_docs:
        docs = docs[: args.max_docs]

    doc_chunks = {}
    doc_results = []
    t0 = time.time()
    for i, doc in enumerate(docs, 1):
        texts = fetch_chunks(ds_id, doc["id"])
        if args.max_chunks:
            texts = texts[: args.max_chunks]
        if args.smoke:
            docs, texts = [doc], texts[:8]
        doc_chunks[doc["id"]] = texts
        log.info(f"({i}/{len(docs)}) {doc.get('name')} —— {len(texts)} chunks")
        if not texts:
            continue
        doc_results.append(await extract_doc(llm, profile, doc, texts, force=args.force))
        if args.smoke:
            break

    nodes, edges = merge_results(profile, doc_results)
    log.info(f"合并：{len(nodes)} 实体 / {len(edges)} 关系，开始页面合成…")

    max_pages = args.max_pages or (SMOKE_MAX_PAGES if args.smoke else 0)
    pages = await synth_pages(profile, nodes, edges, doc_chunks, llm, force=args.force, max_pages=max_pages)
    pages = finalize_pages(profile, pages, nodes, edges)
    link_graph = crosslinker.build_link_graph(pages)
    ridx = None
    if profile.reverse_index:
        ridx = crosslinker.reverse_index(
            nodes, edges, profile.reverse_index["source_type"], profile.reverse_index["target_type"])

    base, jpath, hpath = export(profile, nodes, edges, pages, ridx, link_graph)

    from collections import Counter
    type_dist = Counter(d["type"] for d in nodes.values())
    print("\n" + "=" * 60)
    print(f"Wiki 构建完成 | 场景 {profile.key} | 文档 {len(doc_results)} | 耗时 {time.time() - t0:.0f}s")
    print(f"实体 {len(nodes)} | 关系 {len(edges)} | 页面 {len(pages)} | 互链 {len(link_graph)}"
          + (f" | 反查项 {len(ridx)}" if ridx is not None else ""))
    for t, c in type_dist.most_common():
        print(f"  {t:14s} {c}")
    print(f"目录: {base}")
    print(f"JSON: {jpath}")
    print(f"HTML: {hpath}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
