# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Toolchain that imports the curated original files of the Taoqupo Reservoir (桃曲坡水库) archive (~94 pdf/doc/xls from `/home/scada/SmartTwinRes-skills/pdfs`, plus ~12 parameter charts converted to text via an offline VLM step) into a local RAGFlow v0.27.0 instance (API `http://localhost:9380/api/v1`) as 5 knowledge bases plus 1 tag KB, supporting GraphRAG / Raptor / tag-based soft reranking / metadata hard filtering. `RAGFLOW_API_BASE` / `CORPUS_ROOT` / `PUBLIC_PEM` / `DRAWING_KB_ID` are env-overridable, so the same code targets either the Linux server instance or a Windows workstation (see README 环境变量). Importing originals (not derived txt) is deliberate: RAGFlow citations must anchor into the source document pages. Read `docs/requirements.md` first (goals, constraints, acceptance criteria); `src/README.md` is the operator manual — note its 阶段0/语料源 section reflects the 2026-08-26 pivot away from `pdf_text_analysis/` derived txts.

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

# 扫描页（B/C 类）VLM 转写支线 —— 泾惠渠 E 盘（KB_PROFILE=jinghuiqu）：
python3 jhc_prepare.py --src "E:\bak\资料收集" --apply      # 页图按组合并 <组名>（合并）.pdf → out/jhc_corpus
python3 image_triage.py --src "E:\bak\资料收集"             # 分诊 A/B/C…（空白剔除依据）
python3 image_transcribe.py --src "E:\bak\资料收集"         # B/C 页离线 VLM 转写（断点续跑）
python3 image_transcribe.py --approve                      # 人工复核门（写 transcribe_approved.flag）
python3 transcribe_mount.py --src "E:\bak\资料收集"          # 壳模式挂载（dry-run 列计划 + preflight）
python3 transcribe_mount.py --src "E:\bak\资料收集" --apply  # 实际挂载（断点续跑，按 mount_state.json）
```

> 扫描页支线须按顺序跑：`jhc_prepare` 会剔空白页/去重副本，**分组与命名只认它的 `build_plan`**（`transcribe_mount` 直接复用，不另写分组逻辑）；含 B/C 页的组必须整组壳模式入库，组内每页都要有转写文本（混合组的"好页"也需补转）。

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
- **image_triage.py / image_transcribe.py**: scanned-page line for 泾惠渠 E 盘. Triage buckets every page A(blank)/B(handwritten-mixed)/C(low) via VLM; transcribe then does offline full-text transcription of B∪C with a CONF envelope (`CONF: HIGH|LOW | NOTES:` + Markdown), two-段式 instead of JSON because long output made models drop the JSON wrapper. Append-only jsonl (`out/triage/transcribe.jsonl`), resume by `--only`/`--redo`, `prompt_sha` stamps the prompt version so a prompt change re-runs only stale pages. `--approve` writes the human gate flag.
- **transcribe_mount.py**: mounts those transcriptions into the merged PDFs (stage 3-BC). Reuses `jhc_prepare.build_plan` for grouping (never a second grouping implementation), reuses the `shell_import` engine (UNSTART shell → 11-field meta → per-`## 第N页` chunks), and gates on the approve flag. `preflight` asserts flag/staging file/every-page-text before any delete; per-page anchors are re-checked against the built chunks after mounting. Serial, resumable via `out/triage/mount_state.json`.

## Domain rules (hard constraints)

- Tags are a **soft rerank signal only** (`topn_tags`); exact filtering always goes through `meta_data_filter` on metadata fields — never treat tags as filters.
- GraphRAG lives only on ds1/ds3 (`method=light`, `resolution=true`, 6 fixed entity types) and is configured solely via `parser_config` injection — zero RAGFlow upstream modifications (`/opt/git/ragflow` is off-limits except reading its `public.pem`).
- Corpus roots are read-only. The import source is `CORPUS_ROOT` = `/home/scada/SmartTwinRes-skills/pdfs`; `/home/scada/SmartTwinRes-skills/pdf_text_analysis` is the previous extraction round's output (legacy, used only by the vestigial tables stage).
- All runtime artifacts go to `out/`; never write products into `src/`. The mutable pipeline state (`out/{mapping.csv,import_state.json,setup_state.json}`) is **gitignored, not tracked** — it is environment-bound and OS-path-sensitive, and committing it has twice propagated local paths/instance ids to the server. `rel` values must stay POSIX (`config.normalize_rel`).
- The four human gates are mandatory and must not be scripted around: mapping.csv review, VLM approve flag, 10% OCR sampling, and the ds3 five-file pilot before any full import.
- Tests must stay network-free and isolate `OUT_DIR` to a temp dir (see the `_isolate_out` pattern in `src/tests/`).
- **Import is strictly serial**: `run_import.py` processes one file at a time (upload → parse → wait_document), never concurrent — LLM/embedding model services on the RAGFlow instance have limited concurrency and parallel parsing will overwhelm them. No `concurrent.futures`/`asyncio`/thread pools in any import path.
- **扫描页（B/C）走 VLM 文本，不走 OCR**：jhc_prepare 把扫描页图合并成 `<组名>（合并）.pdf`；凡组内含 B（手写/混写）或 C（低清）页，整组必须壳模式入库（`transcribe_mount.py`：上传保持 UNSTART → patch 11 字段元数据 → 按 `## 第N页` 挂 `image_transcribe.py` 的离线转写切片）。壳模式文档无解析分片，故**组内每页都必须有转写文本**——混合组须补转好页（实测 58 组 1619 页），否则该页文本在库内缺失。`build_chunks` 的 40 字残片下限会把"只有一行的标题页/图签"整页丢弃，挂载路径必须传 `min_chars=8`。
- **profile 会覆写 `OUT_DIR`**（`KB_PROFILE=jinghuiqu` → `out/jinghuiqu`）。分诊/转写/挂载产物固定在 profile 无关的 `out/triage/`（用 `jhc_constants.TRIAGE_JSONL_DEFAULT` 派生），而 `mapping.csv` / `setup_state.json` 才随 profile；路径写死 `OUT_DIR/"triage"` 会静默变成 0 目标。
- **B/C 转写的 prompt 变更是版本化事件**：行内 `prompt_sha` 标记 prompt 版本，`--redo` 子集重跑据此断点续跑（改了 prompt 只补未按新版本重跑的页）。批量补转须按"含 B/C 的合并组"取全集（`group_images`/`strip_page_suffix` 与 `jhc_prepare.build_plan` 同源，禁止另写一套分组逻辑）。
- **检索必须传 dataset id，不是库名**：`search_datasets` 的 `dataset_ids` 传 `jhc1` 这类库 key 会报 `code=102 Only owner of dataset jhc1 authorized`（服务端把字符串当 id 校验），看着像权限问题实为参数错误——一律从 `out/<profile>/setup_state.json` 取 id。库属主账号的 API key（`RAGFLOW_API_KEY`）文档读写 + `/retrieval` + `/datasets/search` 权限齐全，无需邮箱密码。百 MB 级合并 PDF 上传须调 `RAGFLOW_UPLOAD_TIMEOUT`（默认 120s 会写超时）。

## Environment hazard: CodeArts Agent (2026-10-03)

- The **CodeArts Agent** desktop app (`E:\Program Files\CodeArts Agent`, workspace metadata in repo `.codeartsdoer/`) twice overwrote tracked `src/` files with stale September buffers: 2026-10-03 15:36 (5 files, incl. run_setup.py / xls_normalize.py) and 20:35:49 (3 files: jhc_constants.py / jhc_prepare.py / tests/test_jhc_prepare.py). Corrupted copies were restored from HEAD each time; user decided to keep the app running (do not kill it without asking).
- **Session start rule**: before editing, run `git status` + `git diff`; if tracked files carry unexpected modifications, back them up to `%TEMP%\opencode\regress_backup_*`, restore with `git checkout HEAD -- <file>` (HEAD is also safe on origin/master), then `python -m pytest -q` (baseline 332 passed).
- Forensics: external writes appear in `%APPDATA%\codearts-agent\User\History\*\entries.json` as entries without a `source` field, timestamped to the millisecond of the write.

## Conventions

Project documentation, CLI prompts, and human-gate messages are written in Chinese; keep new user-facing strings and doc updates consistent with that. Deeper design rationale lives under `docs/` (`specs/` design v2, `plans/` 10-task TDD breakdown, `evaluation-2026-08-26.md` port review — including the four documented adaptations made when porting from `/home/scada/SmartTwinRes-skills/ragflow_import/`, `evaluation-vision-fusion-2026-09-01.md` 图表文本化导入评测与剩余 8 图优化方案 — 含 RAGFlow 表格切片/小数检索特性与 QA 行格式经验).
