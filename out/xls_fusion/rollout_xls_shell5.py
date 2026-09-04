#!/usr/bin/env python3
"""xls 壳迁移 wave-5 spec（批次四 = F1 收口 D6：水位库容曲线推求.xls 局部 fuse）。

引擎见 src/shell_import.py；本文件只留目标、评审门与 CLI（与 rollout_xls_shell4.py 同构）。

前置：fusion/水位库容曲线推求.xls.md 已过人工评审门（approved5.flag）。
局部 fuse 口径（F1 triage D6 裁定）：查对表整米锚点+插值规则，不做逐行 QA——
曲线主题的"图"侧由 ds2《05-库容水位对照表》壳覆盖（1997 实测表），本壳覆盖
"簿"侧（2008年8月反推曲线 + 逆时序推求 + 070729 过程线 + 2007/2008 来水量年统计）。

流程（引擎内，严格串行）：pre-flight（语料/融合文本/题库锚点，不过拒删）
→ 存档（523 块 F1 之最全量快照）→ 删文档 → 上传壳 UNSTART → patch 元数据
→ 挂融合切片（12 块）→ 探针验证。

用法：
  RAGFLOW_API_KEY=… python3 rollout_xls_shell5.py                # dry-run：pre-flight+列步骤
  RAGFLOW_API_KEY=… python3 rollout_xls_shell5.py --apply        # 正式执行（需 approved5.flag）
  RAGFLOW_API_KEY=… python3 rollout_xls_shell5.py --rollback "水位库容曲线推求.xls" [--apply]

状态：shell_state5.json（幂等）；删前全量块存档 xls_chunks_archive5.json；台账
manifest_xls_shell5.json。锚点断言数据源 = questions_xls.jsonl 的 fusion_doc 列。

元数据：11 schema 字段按 2026-09-04 活库只读核验值原样保留（本批无纠偏裁定项，
flood_event=历年统计 多场次口径不变；year 活值 None，裁定默认改 2008——主口径
反推/年表为 2008 年，2007 内容以标题自明；若裁 keep None 改 TARGETS 一处）。
location 活值 null，按 wave-1 口径统一记"全库"。

apply 后必做（P2-9/P2-9b 教训）：新壳无 tag_feas，driver_wave5.py 逐块回填
{洪水资料:10, 基础数据:8}（两维，勿加第三维——本簿问句面 2008/2007 token 混杂，
不满足 P2-9b"全部问句激活同一标签"前提，见 out/retrieval_tuning/P2_9B_WAVE2_COLLISION.md）。
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path("/opt/wangjz/ragflow-import/src")
sys.path.insert(0, str(SRC))

from config import PUBLIC_PEM, RAGFLOW_API_KEY, RAGFLOW_EMAIL, RAGFLOW_PASSWORD  # noqa: E402
from ragflow_client import RAGFlowClient  # noqa: E402
import shell_import  # noqa: E402

DS3 = "d6fcb56ea1d711f19d7235ad4ea699d4"
CORPUS = Path("/home/scada/SmartTwinRes-skills/pdfs")

# 元数据以 2026-09-04 活库只读核验值为基（11 schema 字段原样；仅 year None→2008 为
# 裁定点，主口径反推说明与水文要素年表均 2008 年）。本批无纠偏项。
# 523 块（F1 之最）→ 12 块，ds3 垃圾块收敛的最后一块拼图。
TARGETS = [
    {
        "kb_name": "水位库容曲线推求.xls",
        "corpus_path": "06-历年洪水资料/08-历年洪水统计/水位库容曲线推求.xls",
        "fusion_md": HERE / "fusion" / "水位库容曲线推求.xls.md",
        "probe": {"question": "校核洪水位790.5米在2008年反推查对表中对应库容是多少万立方米？",
                  "anchor": "4478"},
        # [11 字段活库原样（year None→2008 裁定点）；本批无纠偏。]
        "meta": {"doc_category": "洪水资料", "sub_category": "历年洪水统计",
                 "flood_event": "历年统计", "doc_type": "表格", "year": 2008,
                 "source_format": "excel", "quality": "high", "doc_nature": "技术",
                 "location": "全库", "flood_magnitude": "不适用",
                 "rel": "06-历年洪水资料/08-历年洪水统计/水位库容曲线推求.xls"},
        "keywords": ["水位库容", "查对表", "库容曲线"],
    },
]

ARCHIVE = HERE / "xls_chunks_archive5.json"
STATE = HERE / "shell_state5.json"
MANIFEST = HERE / "manifest_xls_shell5.json"


def load_questions():
    lines = (HERE / "questions_xls.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(l) for l in lines if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default="", help="只处理指定 KB 名")
    ap.add_argument("--rollback", default="", help="回滚指定 KB 名（破坏性：须与 --apply 同用；否则只打印预案）")
    args = ap.parse_args()

    if args.rollback:
        target = next((t for t in TARGETS if t["kb_name"] == args.rollback), None)
        if not target:
            raise SystemExit(f"未知目标: {args.rollback}")
        if not args.apply:
            arch = json.loads(ARCHIVE.read_text(encoding="utf-8")) if ARCHIVE.exists() else {}
            rec = arch.get(args.rollback) or {}
            snap = rec.get("snapshot") or {}
            schema_keys = ("doc_category", "sub_category", "flood_event", "doc_type", "year",
                           "source_format", "quality", "responsible_dept", "doc_nature",
                           "location", "flood_magnitude", "rel")
            meta = {k: v for k, v in (snap.get("meta_fields") or {}).items() if k in schema_keys}
            print(f"[dry-run] 回滚预案: {args.rollback}")
            print("  1) 删 KB 现文档（重传同名 shell，id 以届时查询为准）")
            print(f"  2) 重传语料 {target['corpus_path']}")
            print(f"  3) 恢复存档元数据（仅 11 schema 字段，污染键不恢复）: {meta}")
            print(f"  4) 恢复解析配置 {snap.get('chunk_method')} + naive 重解析（存档 {len(rec.get('chunks') or [])} 块兜底）")
            print("确认执行请加 --apply：--rollback … --apply")
            return
        client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                               api_key=RAGFLOW_API_KEY)
        shell_import.rollback(client, DS3, target, CORPUS, ARCHIVE)
        return

    if args.apply and not (HERE / "approved5.flag").exists():
        raise SystemExit("人工评审门未过：缺 approved5.flag（先审阅 fusion/水位库容曲线推求.xls.md 与 REVIEW_WAVE5.md，再 touch approved5.flag）")

    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)
    shell_import.run_wave(client, DS3, TARGETS, CORPUS, load_questions(), args.apply,
                          only=args.only, archive_path=ARCHIVE,
                          state_path=STATE, manifest_path=MANIFEST)


if __name__ == "__main__":
    main()
