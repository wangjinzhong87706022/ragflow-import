#!/usr/bin/env python3
"""铺开：photo_triage 发现的 3 件图片资产 → ds3 壳导入（新文档流，非替换流）。

与 rollout_png_shell.py / rollout_xls_shell.py 的差异：那两批是把库内已有
融合 md 文档迁到图片壳名下（引擎 process() 的删旧换新流）；本批 3 件是
**新入库**（09-图像与多媒体 拣出的文件/图表类，从不在库），因此不走删旧，
只做：pre-flight → 上传原图壳（UNSTART）→ patch 元数据 → 挂融合切片 → 探针。
引擎逻辑全部复用 src/shell_import.py，本脚本只保留目标 spec、评审门与 CLI。

人工评审门：--apply 需 fusion/APPROVED.flag 存在（评审说明见 fusion/REVIEW.md）。
用法：python3 rollout_photo_shell.py [--dry-run] [--apply] [--only 名称子串]
产物：shell_manifest.json（台账）。
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path("/opt/wangjz/ragflow-import/src")
sys.path.insert(0, str(SRC))

from config import PUBLIC_PEM, RAGFLOW_API_KEY, RAGFLOW_EMAIL, RAGFLOW_PASSWORD  # noqa: E402
from ragflow_client import RAGFlowClient  # noqa: E402
import shell_import as eng  # noqa: E402

DS3 = "d6fcb56ea1d711f19d7235ad4ea699d4"  # 洪水资料
CORPUS = Path("/home/scada/SmartTwinRes-skills/pdfs")
APPROVED_FLAG = HERE / "fusion" / "APPROVED.flag"

DOC_BASE = {
    "doc_category": "洪水资料",
    "quality": "medium",
    "doc_type": "图片",
    "year": 2021,
    "flood_magnitude": "不适用",
}

TARGETS = [
    {
        "kb_name": "汛旱情通报第27期_长江委_20211005_p1.png",
        "corpus_path": "06-历年洪水资料/01-水情通报/汛旱情通报第27期_长江委_20211005_p1.png",
        "fusion_md": HERE / "fusion" / "汛旱情通报第27期_长江委_20211005_p1.md",
        "meta": {**DOC_BASE, "sub_category": "水情通报", "doc_nature": "管理",
                 "source_format": "ocr_png", "flood_event": "",
                 "location": "不适用",
                 "rel": "06-历年洪水资料/01-水情通报/汛旱情通报第27期_长江委_20211005_p1.png"},
        "keywords": ["汛旱情通报", "长江委", "第27期"],
        "probe": {"question": "长江委2021年第27期汛旱情通报预报白河站最大流量是多少？",
                  "anchor": "14000"},
    },
    {
        "kb_name": "汛旱情通报第27期_长江委_20211005_p2.png",
        "corpus_path": "06-历年洪水资料/01-水情通报/汛旱情通报第27期_长江委_20211005_p2.png",
        "fusion_md": HERE / "fusion" / "汛旱情通报第27期_长江委_20211005_p2.md",
        "meta": {**DOC_BASE, "sub_category": "水情通报", "doc_nature": "管理",
                 "source_format": "ocr_png", "flood_event": "",
                 "location": "不适用",
                 "rel": "06-历年洪水资料/01-水情通报/汛旱情通报第27期_长江委_20211005_p2.png"},
        "keywords": ["汛旱情通报", "长江委", "第27期"],
        "probe": {"question": "汛旱情通报第27期要求确保哪些在建工程安全度汛？",
                  "anchor": "碾盘山"},
    },
    {
        "kb_name": "水库出入库水量过程线_2021年9-10月.jpg",
        "corpus_path": "06-历年洪水资料/02-2021年洪水调度/水库出入库水量过程线_2021年9-10月.jpg",
        "fusion_md": HERE / "fusion" / "水库出入库水量过程线_2021年9-10月.md",
        "meta": {**DOC_BASE, "sub_category": "2021年洪水调度", "doc_nature": "技术",
                 "source_format": "ocr_jpg", "flood_event": "2021-10",
                 "location": "库区",
                 "rel": "06-历年洪水资料/02-2021年洪水调度/水库出入库水量过程线_2021年9-10月.jpg"},
        "keywords": ["出入库水量", "过程线", "2021"],
        "probe": {"question": "2021年9-10月水库出入库水量单日峰值出现在哪天？",
                  "anchor": "1570"},
    },
]

# pre-flight 题库锚点：每份文档速查区关键值必须全部覆盖于构建切片
QUESTIONS = [
    {"fusion_doc": "汛旱情通报第27期_长江委_20211005_p1.png",
     "all_keywords": ["第27期", "胡向阳", "14000", "7700", "17000", "186.0"]},
    {"fusion_doc": "汛旱情通报第27期_长江委_20211005_p2.png",
     "all_keywords": ["水工程调度", "航电梯级", "碾盘山", "新集", "雅口",
                      "陕西省水利厅", "度汛方案"]},
    {"fusion_doc": "水库出入库水量过程线_2021年9-10月.jpg",
     "all_keywords": ["日出库量", "日入库量", "累计出库量", "累计入库量",
                      "10/5日", "1570", "9000"]},
]


def process_new(client, ds_id, target, corpus_root, apply_changes):
    """新文档壳导入：不删任何东西；已在库且有切片则视为完成。"""
    kb_name = target["kb_name"]
    print(f"== {kb_name} ==", flush=True)
    # 0) pre-flight：语料在 / 融合文本在 / 锚点全覆盖（复用引擎，拒执行语义相同）
    pieces = eng.preflight(target, corpus_root, QUESTIONS)

    existing = client.find_document_by_name(ds_id, kb_name)
    if existing and int(existing.get("chunk_count") or 0) > 0:
        print(f"  跳过（已在库 {existing['id'][:8]}，{existing['chunk_count']} 切片）", flush=True)
        return {"status": "SKIP(已在库)", "id": existing["id"],
                "chunks": existing["chunk_count"]}

    if not apply_changes:
        print(f"  [dry-run] 将执行: 上传壳→patch元数据→挂 {len(pieces)} 切片→探针验证", flush=True)
        return {"status": "DRY-RUN", "chunks_planned": len(pieces)}

    # 1) 上传壳（不 parse）——新文档流无删除，天然无“已删未传”窟窿
    if existing:
        shell = existing
        print(f"  复用空壳 {shell['id'][:8]}", flush=True)
    else:
        uploaded = client.upload_document(ds_id, Path(corpus_root) / target["corpus_path"],
                                          filename=kb_name)
        shell = uploaded[0] if isinstance(uploaded, list) else uploaded
    if shell.get("run") not in (None, "0", 0, "UNSTART"):
        print(f"  [警告] 壳 run={shell.get('run')} 非 UNSTART，请检查！", flush=True)

    # 2) 元数据 → 3) 挂融合切片
    client.patch_document(ds_id, shell["id"], target["meta"])
    print("  元数据已 patch", flush=True)
    for p in pieces:
        eng.add_chunk(ds_id, shell["id"], p, target["keywords"])
    print(f"  挂载 {len(pieces)} 切片", flush=True)

    # 4) 探针验证门（复用引擎：两次尝试容排序漂移）
    ok, sim = eng.verify(client, ds_id, kb_name,
                         target["probe"]["question"], target["probe"]["anchor"])
    status = "OK" if ok else "FAIL(验证门)"
    print(f"  ⇒ {kb_name}: {status}（探针 sim={sim}）", flush=True)
    return {"status": status, "id": shell["id"], "chunks": len(pieces),
            "similarity": sim}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只处理 kb_name 含此子串的目标")
    ap.add_argument("--apply", action="store_true", help="执行导入（默认 dry-run）")
    args = ap.parse_args()

    if args.apply and not APPROVED_FLAG.exists():
        raise SystemExit(f"评审门未过：缺 {APPROVED_FLAG}（见 fusion/REVIEW.md，"
                         f"评审通过后 touch 之再 --apply）")

    targets = [t for t in TARGETS if not args.only or args.only in t["kb_name"]]
    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)
    print(f"目标 {len(targets)} 份（apply={args.apply}）", flush=True)
    for t in targets:
        d = client.find_document_by_name(DS3, t["kb_name"])
        print(f"  [{t['kb_name']}] 现块数={d.get('chunk_count') if d else '不在库'}"
              f" 探针锚点={t['probe']['anchor']}", flush=True)

    manifest = eng.load_json(HERE / "shell_manifest.json", {})
    stopped = False
    for t in targets:
        if stopped:
            manifest.setdefault(t["kb_name"], {})["status"] = "NOT-RUN(前序FAIL停)"
            continue
        try:
            rec = process_new(client, DS3, t, CORPUS, args.apply)
        except Exception as e:  # pre-flight 拒绝 / API 异常：记录并停止后续
            rec = {"status": f"ERROR({str(e)[:160]})"}
            stopped = True
        rec["corpus_path"] = t["corpus_path"]
        rec["finished"] = datetime.now().isoformat(timespec="seconds")
        manifest[t["kb_name"]] = rec
        eng.save_json(HERE / "shell_manifest.json", manifest)

    n_ok = sum(1 for r in manifest.values() if str(r.get("status", "")).startswith("OK"))
    print(f"\n==== 完成 {n_ok}/{len(targets)} OK；台账 shell_manifest.json ====", flush=True)
    for t in targets:
        print(f"  {manifest.get(t['kb_name'], {}).get('status', '?'):<24} {t['kb_name']}",
              flush=True)


if __name__ == "__main__":
    main()
