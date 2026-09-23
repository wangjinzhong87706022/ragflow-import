"""
run_setup — idempotent dataset creation for the 桃曲坡 RAGFlow import.

Procedure (dry_run-aware):
  1. Connect via RAGFlowClient.
  2. List existing datasets → name→id lookup.
  3. For each dataset (TAG_KB ds0 + DATASETS ds1..ds5):
       reuse id if name already exists, else create.
  4. TAG_KB special handling:
       write vocab → upload_tag_vocab → wait_parse.
  5. Update parser_config for ds1..ds5: inject tag_kb_ids AND push the nested
       graphrag/raptor blocks from config.DATASETS (PUT is deep-merged server-side,
       so this is the only way to override creation-time naive defaults).
  6. Apply METADATA_SCHEMA to ds1..ds5 (tag KB ds0 has no metadata semantics).
  7. Persist OUT_DIR/setup_state.json.
  8. Print summary.
"""

import sys
import json
from pathlib import Path

import requests

from config import (
    DATASETS, TAG_KB, METADATA_SCHEMA, OUT_DIR,
    RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM, RAGFLOW_API_KEY,
    USE_TAG_KB, TAG_VOCAB_FILENAME,
)
from ragflow_client import RAGFlowClient
from tag_vocab import write_vocab_txt


def run_setup(dry_run: bool = False) -> dict[str, dict]:
    """
    Create or reuse datasets and write setup_state.json.

    Returns:
        dict mapping ds_key → {"name": ..., "id": ...}
    """
    # ------------------------------------------------------------------
    # Step 1 – connect
    # ------------------------------------------------------------------
    try:
        client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM, api_key=RAGFLOW_API_KEY)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)
    except requests.RequestException as exc:
        print(f"[ERROR] Could not connect to RAGFlow: {exc}")
        print("Hint: make sure RAGFlow is running and RAGFLOW_EMAIL / RAGFLOW_PASSWORD are set.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 2 – list existing datasets (read-only, always called)
    # ------------------------------------------------------------------
    try:
        existing_datasets = client.list_datasets()
    except requests.RequestException as exc:
        print(f"[ERROR] list_datasets() failed — request error: {exc}")
        print("Hint: make sure RAGFlow is running.")
        sys.exit(1)

    existing_lookup: dict[str, str] = {ds["name"]: ds["id"] for ds in existing_datasets}
    print(f"[INFO] Found {len(existing_lookup)} existing dataset(s): {list(existing_lookup.keys())}")

    # ------------------------------------------------------------------
    # Step 3 – create or reuse datasets (TAG_KB first, then DATASETS)
    # ------------------------------------------------------------------
    all_defs = ([TAG_KB] if USE_TAG_KB else []) + DATASETS   # ds0 然后 ds1..ds5
    state: dict[str, dict] = {}
    created: list[str] = []
    reused: list[str] = []

    for ds_def in all_defs:
        key = ds_def["key"]
        name = ds_def["name"]
        chunk_method = ds_def["chunk_method"]

        if name in existing_lookup:
            ds_id = existing_lookup[name]
            reused.append(name)
            print(f"[INFO] Reusing existing dataset '{name}' (id={ds_id})")
        elif dry_run:
            ds_id = "<would-create-id>"
            created.append(name)
            print(f"[dry_run] Would create dataset '{name}' (chunk_method={chunk_method})")
        else:
            result = client.create_dataset(name, chunk_method)
            ds_id = result["id"]
            created.append(name)
            print(f"[INFO] Created dataset '{name}' (id={ds_id})")

        state[key] = {"name": name, "id": ds_id}

    # ------------------------------------------------------------------
    # Step 4 – TAG_KB special handling（幂等：库内已有文档则跳过上传；
    #   USE_TAG_KB=False 的画像整体跳过——不同用户的知识库不可复用桃曲坡词表）
    # ------------------------------------------------------------------
    tag_kb_id = state["ds0"]["id"] if USE_TAG_KB else None

    vocab_path = OUT_DIR / "tag_vocab" / TAG_VOCAB_FILENAME
    if not USE_TAG_KB:
        print("[INFO] USE_TAG_KB=False——跳过标签库创建与词表上传（tag_kb_ids=[]）")
    elif dry_run:
        print(f"[dry_run] Would write vocab to {vocab_path}, upload and parse it in the tag KB")
    else:
        existing_docs = client.list_documents(tag_kb_id)
        if existing_docs:
            print(
                f"[INFO] 标签库 '{TAG_KB['name']}' 已有 {len(existing_docs)} 个文档——"
                "跳过词表上传/解析（重复上传会扭曲 topn_tags 权重）"
            )
        else:
            write_vocab_txt(vocab_path)
            print(f"[INFO] Wrote tag vocab to {vocab_path}")
            upload_result = client.upload_tag_vocab(tag_kb_id, vocab_path)
            print(f"[INFO] Uploaded tag vocab for dataset '{TAG_KB['name']}'")
            # v0.27.0 上传接口的 data 是**文档 dict 列表**（与 upload_document 同契约），
            # 旧代码只认 dict 形状 → tag_doc_id 恒为 None → 显式 parse 永不触发。
            # 这里两种形状都兼容，确保词表真的开始解析。
            if isinstance(upload_result, dict):
                upload_docs = [upload_result]
            elif isinstance(upload_result, list):
                upload_docs = [d for d in upload_result if isinstance(d, dict)]
            else:
                upload_docs = []
            tag_doc_ids = [d["id"] for d in upload_docs if d.get("id")]
            if tag_doc_ids:
                client.parse_documents(tag_kb_id, tag_doc_ids)
                print(f"[INFO] Triggered parse for tag vocab doc(s) {tag_doc_ids}")
            else:
                print(
                    "[WARN] 上传响应中未解析出 doc_id——跳过显式 parse，"
                    "改由服务端自动解析（若等待超时请检查上传接口返回形状）"
                )
            client.wait_parse(tag_kb_id, timeout=300)
            print(f"[INFO] Tag KB parse complete for '{TAG_KB['name']}'")

    # ------------------------------------------------------------------
    # Step 5 – parser_config 下发：tag_kb_ids + 嵌套 graphrag/raptor（ds1..ds5）
    #   服务端 PUT deep-merge；graphrag.use_graphrag/raptor.use_raptor 的显式 False
    #   是压掉创建期 naive 默认 True 的唯一途径（review P0-4）。
    # ------------------------------------------------------------------
    for ds_def in DATASETS:            # ds1..ds5 only
        key = ds_def["key"]
        ds_id = state[key]["id"]
        parser_config = {
            **ds_def["parser_config"],
            "tag_kb_ids": [tag_kb_id] if USE_TAG_KB else [],
        }

        if dry_run:
            g_on = parser_config["graphrag"]["use_graphrag"]
            r_on = parser_config["raptor"]["use_raptor"]
            print(f"[dry_run] Would update_dataset({key}, tag_kb_ids={parser_config['tag_kb_ids']}, "
                  f"graphrag={g_on}, raptor={r_on})")
        else:
            client.update_dataset(ds_id, parser_config)
            print(f"[INFO] Updated dataset '{ds_def['name']}' "
                  f"(tag_kb_id={tag_kb_id}, graphrag={parser_config['graphrag']['use_graphrag']}, "
                  f"raptor={parser_config['raptor']['use_raptor']})")

    # ------------------------------------------------------------------
    # Step 6 – apply METADATA_SCHEMA to ds1..ds5（标签库 ds0 无元数据语义）
    # ------------------------------------------------------------------
    for ds_def in DATASETS:            # ds1..ds5 only
        key = ds_def["key"]
        ds_id = state[key]["id"]

        if dry_run:
            print(f"[dry_run] Would put_metadata_config({key})")
        else:
            client.put_metadata_config(ds_id, METADATA_SCHEMA)
            print(f"[INFO] Applied metadata schema to '{ds_def['name']}'")

    # ------------------------------------------------------------------
    # Step 7 – persist state
    # ------------------------------------------------------------------
    state_path = OUT_DIR / "setup_state.json"
    if dry_run:
        print(f"[dry_run] Would write {state_path}")
    else:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print(f"[INFO] Wrote {state_path}")

    # ------------------------------------------------------------------
    # Step 8 – summary
    # ------------------------------------------------------------------
    print("\n=== Dataset Summary ===")
    for ds_key, entry in state.items():
        status = "(created)" if entry["name"] in created else "(reused)"
        print(f"  {ds_key}: {entry['name']} id={entry['id']} {status}")

    return state


def main() -> None:
    """
    CLI 入口（CLAUDE.md 阶段2 命令）。

    变更类脚本一律默认 dry-run，``--apply`` 必须显式给出；两旗标互斥。
    （P0-6：此前本文件缺 main/__main__，``python3 run_setup.py --dry-run``
    是静默空跑——live 冒烟发现的。）
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="RAGFlow 建库：创建/复用 ds0–ds5、下发 parser_config 与元数据 schema"
    )
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="只打印将执行的动作，不修改服务端")
    parser.add_argument("--apply", action="store_true",
                        help="实际建库并下发配置（显式确认）")
    args = parser.parse_args()

    if args.apply and args.dry_run:
        parser.error("--apply 与 --dry-run 互斥")

    run_setup(dry_run=not args.apply)


if __name__ == "__main__":
    main()
