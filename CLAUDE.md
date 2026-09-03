# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Toolchain that imports the curated original files of the Taoqupo Reservoir (桃曲坡水库) archive (~70 pdf/doc/xls from `/home/scada/SmartTwinRes-skills/pdfs`, plus ~12 parameter charts converted to text via an offline VLM step) into a local RAGFlow v0.27.0 instance (API `http://localhost:9380/api/v1`) as 5 knowledge bases plus 1 tag KB, supporting GraphRAG / Raptor / tag-based soft reranking / metadata hard filtering. Importing originals (not derived txt) is deliberate: RAGFlow citations must anchor into the source document pages. Read `docs/requirements.md` first (goals, constraints, acceptance criteria); `src/README.md` is the operator manual — note its 阶段0/语料源 section reflects the 2026-08-26 pivot away from `pdf_text_analysis/` derived txts.

## Commands

Run everything from `src/` — modules import each other as top-level names (`from config import ...`), and tests depend on `python3 -m pytest` putting the cwd on `sys.path`.

```bash
# Environment (once)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # requests, pycryptodome, pytest

# Tests — fully mocked; never touch the network or the real out/
python3 -m pytest -q
python3 -m pytest tests/test_run_import.py -k resume   # single file / -k pattern

# Pipeline stages, in order:
python3 -m corpus                      # 阶段0: scan corpus → out/mapping.csv (human review gate)
python3 -m tables                      # 阶段1: table Markdown + Q/A row extraction (optional)
python3 vision_extract.py --limit 2    # 阶段1 VLM dry-run → review out/vision/compare_report.md
python3 vision_extract.py --approve    # human approval gate for VLM results
python3 run_setup.py --dry-run         # 阶段2: preview KB creation
python3 run_setup.py                   # create tag KB → parse → inject tag_kb_ids → register metadata schema
python3 run_import.py --dataset ds3 --limit 5 --apply   # 阶段3 pilot (mandatory before full import)
python3 inspect_chunks.py --dataset ds3 --limit 5      # 阶段3.5: chunk 切片审查报告（只读，out/qc/chunks_*.md）
python3 run_import.py --apply          # full import, ordered ds1→ds5
python3 run_qc.py                      # 阶段4: 6 acceptance questions + meta_data_filter drill
```

Credentials come only from env vars `RAGFLOW_EMAIL`/`RAGFLOW_PASSWORD` (plus `LLM_API_KEY` for vision_extract); alternatively set `RAGFLOW_API_KEY` (Bearer, from the Web UI system settings) and the login step is skipped entirely. Every mutating script defaults to dry-run; `--apply` is always explicit.

## Architecture

A staged pipeline whose scripts communicate through artifacts in `out/`:

```
corpus.py(pdfs/) ───▶ out/mapping.csv ─────┐
                                           ├──▶ run_import.py ◀─ out/setup_state.json ◀─ run_setup.py
vision_extract.py(12 charts) ──────────────┘         │
                                                     ▼
                                        out/import_state.json (resume)
run_qc.py / inspect_chunks.py ◀── RAGFlowClient.search/list_chunks
```

- **config.py** is the single source of truth: `DATASETS` (ds1–ds5, each with `chunk_method`/`parser_config`/`graphrag_config`/`raptor_config`), `TAG_KB`, `CORPUS_ROOT` (= `pdfs/`, the import source), `DIR_DATASET` (first-level dir → ds key; ds5 gets 02-安全鉴定与评价 + 03-施工图纸与设计 so it is not empty), the 11-field `METADATA_SCHEMA`, `FLOOD_EVENT_BY_SUBDIR`, `VISION_SOURCES` (12 charts), and keyword tables. Change KB shape here, nowhere else.
- **corpus.py**: scans `CORPUS_ROOT` for doc extensions only (`09-图像与多媒体`/`10-压缩包待处理` are excluded via SKIP_DIRS), classifies by first-level dir, SHA-256 + `_dup`-name dedup, derives metadata columns → `mapping.csv`. Unknown dirs abort with a collected list — never guessed.
- **tag_vocab.py / vision_extract.py**: preprocessing. VLM extraction cross-checks against `TEXTUALIZED_VALUES` (e.g. 百年一遇泄量 1454 m³/s) and blocks import until manual `--approve`. `tables.py` is legacy (only fires on .txt rows, which no longer exist in the corpus).
- **ragflow_client.py**: RSA-encrypted login (public key from `/opt/git/ragflow/conf/public.pem`) plus Dataset/Document/Metadata/Search/chunks API wrappers, all with timeouts and pagination. Parse completion polls `run` status (run `"3"` / progress ≥ 1 = done; `"4"` / progress < 0 = failed).
- **run_setup.py**: idempotent (reuses existing datasets by name; skips tag-vocab upload when the tag KB already has documents). Order matters: tag KB created and parsed *first*, then `tag_kb_ids` injected into ds1–ds5 `parser_config`, then metadata schema registered. Writes `out/setup_state.json` (ds key → dataset id).
- **run_import.py**: per file — upload (reusing an existing same-name doc on reruns) → `patch_document(meta_fields)` → parse → wait, persisting through `ImportStateMachine` after every step so reruns skip already-done files. Rows with non-empty `skip_reason` never import.
- **shell_import.py**: engine for the shell import mode (shared by image/xls fusion waves, e.g. the 12 PNG shells and the ds3 xls fusion): upload the original file but keep it UNSTART (never parse), patch 11-field metadata, then manually attach fusion-text chunks via the chunk API (chunks enter the index without parsing, bypassing the tag-phase LLM fallback). Preconditions are asserted before any delete (corpus file, fusion md, and every question-bank anchor present in the built chunks) so a bad path can never leave a deleted-doc hole; probe verification gates each file, serially. Rollback = re-upload + naive parse from the pre-delete chunk archive. The `out/*/rollout_*.py` wave scripts keep only targets/gate/CLI; engine logic lives here.
- **inspect_chunks.py**: post-pilot chunk review report (fragment/noise/duplicate flags, length stats, six human criteria) → `out/qc/chunks_*.md`.
- **run_qc.py**: Q1–Q4 exercise GraphRAG (`use_kg=true`); Q5–Q6 verify `meta_data_filter` hard filtering by flood_event etc. Reports land in `out/qc/`.

## Domain rules (hard constraints)

- Tags are a **soft rerank signal only** (`topn_tags`); exact filtering always goes through `meta_data_filter` on metadata fields — never treat tags as filters.
- GraphRAG lives only on ds1/ds3 (`method=light`, `resolution=true`, 6 fixed entity types) and is configured solely via `parser_config` injection — zero RAGFlow upstream modifications (`/opt/git/ragflow` is off-limits except reading its `public.pem`).
- Corpus roots are read-only. The import source is `CORPUS_ROOT` = `/home/scada/SmartTwinRes-skills/pdfs`; `/home/scada/SmartTwinRes-skills/pdf_text_analysis` is the previous extraction round's output (legacy, used only by the vestigial tables stage).
- All runtime artifacts go to `out/`; never write products into `src/`.
- The four human gates are mandatory and must not be scripted around: mapping.csv review, VLM approve flag, 10% OCR sampling, and the ds3 five-file pilot before any full import.
- Tests must stay network-free and isolate `OUT_DIR` to a temp dir (see the `_isolate_out` pattern in `src/tests/`).

## Conventions

Project documentation, CLI prompts, and human-gate messages are written in Chinese; keep new user-facing strings and doc updates consistent with that. Deeper design rationale lives under `docs/` (`specs/` design v2, `plans/` 10-task TDD breakdown, `evaluation-2026-08-26.md` port review — including the four documented adaptations made when porting from `/home/scada/SmartTwinRes-skills/ragflow_import/`, `evaluation-vision-fusion-2026-09-01.md` 图表文本化导入评测与剩余 8 图优化方案 — 含 RAGFlow 表格切片/小数检索特性与 QA 行格式经验).
