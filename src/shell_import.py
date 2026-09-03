#!/usr/bin/env python3
"""壳模式导入引擎：原文件壳（UNSTART，不 parse）+ 元数据 patch + 手动挂融合切片。

图片（PNG/JPG）与 xls 的融合文本导入共用同一机制（设计见
docs/review-vision-xls-2026-09-03.md；机制依据均已在 v0.27.1 验证：手动挂载切片
免 parse 即入检索索引；壳保持 UNSTART 不产生解析分片、自然绕开 tag 阶段逐块 LLM
兜底；PUT status:"0" 静默失效，下架只能删除）。

单目标流程（严格串行）：
  pre-flight 断言（语料在 / 融合文本在 / 题库锚点全覆盖，任一不满足拒删）
  → 删前全量块存档 → 删旧文档 → 上传原文件保持 UNSTART → patch 元数据
  → 按 '## ' 小节挂融合切片 → 探针验证（FAIL 即停后续文件）
回滚 = 删现文档 → 重传原件 → 恢复存档元数据/解析配置 → naive 重解析。

out/ 下各 wave 脚本（rollout_xls_shell.py / rollout_png_shell.py 等）只保留
目标 spec、人工评审门与 CLI；引擎逻辑一律收编在本模块。
"""
import json
import time
from datetime import datetime
from pathlib import Path

import requests

from config import API_BASE, RAGFLOW_API_KEY

API = API_BASE
H = {"Authorization": f"Bearer {RAGFLOW_API_KEY}", "Content-Type": "application/json"}
VERIFY_SLEEPS = (5, 8)           # 探针两次尝试前的等待（v0.27.1 排序漂移容错）
POLL_SEC, DOC_TIMEOUT = 10, 900  # 回滚解析轮询


class PreflightError(RuntimeError):
    """pre-flight 断言失败（拒删，防“已删未传”窟窿）。"""


def load_json(path, default):
    if Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return default


def save_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def build_chunks(md_text, max_lines=10, max_chars=600):
    """按 '## ' 小节切分，节内按行分组（≤max_lines 且 ≤max_chars）；丢弃 <40 字残片。"""
    sections, cur = [], ["# 头部"]
    for line in md_text.splitlines():
        if line.startswith("## "):
            sections.append(cur)
            cur = [line]
        else:
            cur.append(line)
    sections.append(cur)
    chunks = []
    for sec in sections:
        header, buf, n = sec[0], [], 0
        body = [l for l in sec[1:] if l.strip()]
        for line in body:
            buf.append(line)
            n += len(line)
            if len(buf) >= max_lines or n >= max_chars:
                chunks.append((header + "\n" if header.startswith("## ") else "")
                              + "\n".join(buf))
                buf, n = [], 0
        if buf:
            prefix = header + "\n" if header.startswith("## ") else ""
            chunks.append(prefix + "\n".join(buf))
    return [c.strip() for c in chunks if len(c.strip()) >= 40]


def question_anchors(questions, kb_name):
    """题库中属于 kb_name 的全部锚点（all_keywords 并集，按出现序去重）。"""
    out = []
    for q in questions or []:
        if q.get("fusion_doc") == kb_name:
            for k in q.get("all_keywords") or []:
                if k not in out:
                    out.append(k)
    return out


def preflight(target, corpus_root, questions=None):
    """删文档前断言：语料在、融合文本在、题库锚点全部出现在构建切片里。
    任一不满足抛 PreflightError（汇总全部问题）；通过则返回构建好的切片列表。"""
    problems = []
    corpus = Path(corpus_root) / target["corpus_path"]
    md_path = Path(target["fusion_md"])
    if not corpus.is_file():
        problems.append(f"语料缺失: {corpus}")
    if not md_path.is_file():
        problems.append(f"融合文本缺失: {md_path}")
    pieces = build_chunks(md_path.read_text(encoding="utf-8")) if md_path.is_file() else []
    joined = "\n".join(pieces)
    missing = [a for a in question_anchors(questions, target["kb_name"]) if a not in joined]
    if missing:
        problems.append(f"题库锚点未覆盖: {missing}")
    if problems:
        raise PreflightError("；".join(problems))
    return pieces


def add_chunk(ds_id, doc_id, content, keywords):
    r = requests.post(f"{API}/datasets/{ds_id}/documents/{doc_id}/chunks",
                      headers=H, json={"content": content, "important_keywords": keywords},
                      timeout=30)
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"add_chunk 失败: {data}")
    return data["data"]["chunk"]["id"]


def delete_doc(ds_id, doc_id):
    r = requests.delete(f"{API}/datasets/{ds_id}/documents", headers=H,
                        json={"ids": [doc_id]}, timeout=30)
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"delete_doc 失败: {data}")


def patch_chunk(ds_id, doc_id, chunk_id, content=None, important_keywords=None, questions=None):
    """chunk 级就地修正（v0.27.1 PATCH …/chunks/{id}，服务端重切词入索引）。
    用于在库文本纠错（如读数裁定后的少数派数值、速查补行）；改完须同步 out/ 的
    fusion md 源，保持“md = 唯一真源、库内为其镜像”。"""
    body = {k: v for k, v in (("content", content),
                              ("important_keywords", important_keywords),
                              ("questions", questions)) if v is not None}
    r = requests.patch(f"{API}/datasets/{ds_id}/documents/{doc_id}/chunks/{chunk_id}",
                       headers=H, json=body, timeout=30)
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"patch_chunk 失败: {data}")
    return data


def snapshot_doc(client, ds_id, kb_name):
    d = client.find_document_by_name(ds_id, kb_name)
    if not d:
        return None
    return {"id": d["id"], "run": d.get("run"), "chunk_count": d.get("chunk_count"),
            "meta_fields": d.get("meta_fields"), "chunk_method": d.get("chunk_method"),
            "parser_config": d.get("parser_config")}


def verify(client, ds_id, doc_name, question, anchor, tries=2, top_n=30):
    """探针验证门：全返回集（v0.27.1 retrieval 恒返回 30 条，top_k 只影响排序）内
    目标文档切片含锚点即 PASS，返回命中切片的最佳排名。

    门设在 30 条而非 top10 的原因（PNG 试点同教训）：混合打分下整篇汇报类文档的
    关键词密度天然压过行文本切片，行切片常落在 10~30 名；top10 排名仅作遥测记录，
    检索质量验收由题库评测（top10 并集口径）承担。"""
    best = None
    for i in range(tries):
        time.sleep(VERIFY_SLEEPS[min(i, len(VERIFY_SLEEPS) - 1)])
        data = client.search_datasets([ds_id], question, top_k=10)
        for j, c in enumerate((data.get("chunks") or [])[:top_n]):
            cname = c.get("document_name") or c.get("docnm_kwd") or "?"
            if cname == doc_name and anchor in (c.get("content_with_weight") or ""):
                best = j + 1 if best is None else min(best, j + 1)
    return (best is not None), best


def process(client, ds_id, target, corpus_root, questions, apply_changes,
            state, manifest, archive_path=None, state_path=None, manifest_path=None):
    kb_name = target["kb_name"]
    if state.get(kb_name, {}).get("status") == "OK":
        print(f"  跳过（已完成）: {kb_name}", flush=True)
        return True
    print(f"== {kb_name} ==", flush=True)
    rec = manifest.setdefault(kb_name, {"kb_name": kb_name, "corpus_path": target["corpus_path"]})
    if not apply_changes:
        print("  [dry-run] 将执行: pre-flight→存档→删文档→上传壳→patch元数据→挂融合切片→探针验证",
              flush=True)
        return True

    # 0) pre-flight：不过拒删（在 archive/delete 之前，杜绝“已删未传”）
    try:
        pieces = preflight(target, corpus_root, questions)
    except PreflightError as e:
        rec["status"] = f"ERROR(pre-flight: {str(e)[:160]})"
        if manifest_path:
            save_json(manifest_path, manifest)
        print(f"  [拒删] pre-flight 未过: {e}", flush=True)
        return False

    old = snapshot_doc(client, ds_id, kb_name)
    if not old:
        print(f"  [异常] KB 中找不到 {kb_name}，跳过", flush=True)
        rec["status"] = "ERROR(旧文档不存在)"
        return False
    rec["old"] = {k: old[k] for k in ("id", "run", "chunk_count")}

    # 1) 删前全量块存档
    arch = load_json(archive_path, {}) if archive_path else {}
    if kb_name not in arch or not arch[kb_name]:
        chunks = client.list_chunks(ds_id, old["id"])
        arch[kb_name] = {"snapshot": old, "chunks": chunks}
        if archive_path:
            save_json(archive_path, arch)
        print(f"  存档 {len(chunks)} 块 → {Path(archive_path).name}", flush=True)
    else:
        print(f"  存档已存在（{len(arch[kb_name]['chunks'])} 块），跳过存档", flush=True)

    # 2) 删旧文档 → 3) 上传同名壳（不 parse）
    delete_doc(ds_id, old["id"])
    print(f"  已删旧文档 {old['id']}（{old['chunk_count']} 块）", flush=True)
    uploaded = client.upload_document(ds_id, Path(corpus_root) / target["corpus_path"],
                                      filename=kb_name)
    if isinstance(uploaded, list):
        uploaded = uploaded[0]
    shell_id = uploaded["id"]
    rec["shell_id"] = shell_id
    print(f"  壳已上传 {shell_id} run={uploaded.get('run')}", flush=True)
    if uploaded.get("run") not in (None, "0", 0, "UNSTART"):
        print(f"  [警告] 壳 run={uploaded.get('run')} 非 UNSTART，请检查！", flush=True)

    # 4) 元数据 → 5) 挂融合切片
    client.patch_document(ds_id, shell_id, target["meta"])
    print("  元数据已 patch", flush=True)
    for p in pieces:
        add_chunk(ds_id, shell_id, p, target["keywords"])
    print(f"  挂载 {len(pieces)} 切片", flush=True)
    rec["chunks"] = len(pieces)

    # 6) 探针验证门
    ok, rank = verify(client, ds_id, kb_name,
                      target["probe"]["question"], target["probe"]["anchor"])
    rec["verified"] = ok
    rec["probe_rank"] = rank
    rec["status"] = "OK" if ok else "FAIL(验证门)"
    rec["finished"] = datetime.now().isoformat(timespec="seconds")
    if manifest_path:
        save_json(manifest_path, manifest)
    if state_path:
        state[kb_name] = {"status": rec["status"], "shell_id": shell_id,
                          "chunks": len(pieces), "verified": ok}
        save_json(state_path, state)
    print(f"  ⇒ {kb_name}: {rec['status']}（探针 rank={rank}）", flush=True)
    return ok


def rollback(client, ds_id, target, corpus_root, archive_path):
    """回滚单目标：删壳 → 重传原件 → 恢复存档元数据/解析配置 → naive 重解析。"""
    arch = load_json(archive_path, {})
    kb_name = target["kb_name"]
    if kb_name not in arch:
        raise SystemExit(f"无存档，无法回滚: {kb_name}")
    snap = arch[kb_name]["snapshot"]
    old = client.find_document_by_name(ds_id, kb_name)
    if old:
        delete_doc(ds_id, old["id"])
        print(f"已删现文档 {old['id']}", flush=True)
    uploaded = client.upload_document(ds_id, Path(corpus_root) / target["corpus_path"],
                                      filename=kb_name)
    if isinstance(uploaded, list):
        uploaded = uploaded[0]
    did = uploaded["id"]
    meta = {k: v for k, v in (snap.get("meta_fields") or {}).items()
            if k in ("doc_category", "sub_category", "flood_event", "doc_type", "year",
                     "source_format", "quality", "responsible_dept", "doc_nature",
                     "location", "flood_magnitude", "rel")}
    if meta:
        client.patch_document(ds_id, did, meta)
    requests.put(f"{API}/datasets/{ds_id}/documents/{did}", headers=H,
                 json={"chunk_method": snap.get("chunk_method") or "naive",
                       "parser_config": snap.get("parser_config")
                       or {"auto_questions": 0, "auto_keywords": 0}}, timeout=30)
    requests.post(f"{API}/datasets/{ds_id}/documents/parse", headers=H,
                  json={"document_ids": [did]}, timeout=30)
    print(f"重传并解析中 {did}（naive）…", flush=True)
    t0 = time.time()
    while time.time() - t0 < DOC_TIMEOUT:
        time.sleep(POLL_SEC)
        d = client.find_document_by_name(ds_id, kb_name) or {}
        run = d.get("run")
        print(f"  {time.time()-t0:.0f}s run={run} chunks={d.get('chunk_count')}", flush=True)
        if run in ("DONE", "FAIL") or (isinstance(d.get("progress"), (int, float))
                                       and d["progress"] >= 1):
            print(f"回滚完成: {kb_name} run={run} chunks={d.get('chunk_count')}"
                  f"（存档原值 {snap['chunk_count']}）", flush=True)
            return
    raise SystemExit("回滚解析超时")


def run_wave(client, ds_id, targets, corpus_root, questions, apply_changes,
             only="", archive_path=None, state_path=None, manifest_path=None):
    """串行执行一批壳迁移；某文件验证 FAIL/异常即停止后续（其余保持 naive 现状）。"""
    state = load_json(state_path, {}) if state_path else {}
    manifest = load_json(manifest_path, {}) if manifest_path else {}
    print(f"目标 {len(targets)} 份（apply={apply_changes}）", flush=True)
    for t in targets:
        if only and t["kb_name"] != only:
            continue
        d = client.find_document_by_name(ds_id, t["kb_name"])
        cc = d.get("chunk_count") if d else "不在库"
        print(f"  [{t['kb_name']}] 现块数={cc} 探针锚点={t['probe']['anchor']}", flush=True)
    for t in targets:
        if only and t["kb_name"] != only:
            continue
        try:
            ok = process(client, ds_id, t, corpus_root, questions, apply_changes,
                         state, manifest, archive_path, state_path, manifest_path)
        except Exception as e:  # 单份失败不静默：记录并停止后续（串行安全）
            manifest.setdefault(t["kb_name"], {})["status"] = f"ERROR({str(e)[:120]})"
            if manifest_path:
                save_json(manifest_path, manifest)
            print(f"  ⇒ {t['kb_name']}: ERROR {e}", flush=True)
            ok = False
        if not ok and apply_changes:
            print("验证门未过，停止后续文件（其余保持 naive 现状）", flush=True)
            return False
    return True
