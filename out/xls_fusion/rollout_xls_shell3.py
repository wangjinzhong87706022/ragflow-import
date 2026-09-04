#!/usr/bin/env python3
"""xls 壳迁移 wave-3 spec（批次二 = F1 收口 D2：2010 三份 + D7 TB0207 年份纠偏）。

引擎见 src/shell_import.py；本文件只留目标、评审门与 CLI（与 rollout_xls_shell2.py 同构）。

前置：fusion/ 3 份融合文本已过人工评审门（approved3.flag）。
流程（引擎内，严格串行）：pre-flight（语料/融合文本/题库锚点，不过拒删）
→ 存档→删文档→上传壳 UNSTART→patch 元数据→挂融合切片→探针验证。

用法：
  RAGFLOW_API_KEY=… python3 rollout_xls_shell3.py                # dry-run：pre-flight+列步骤
  RAGFLOW_API_KEY=… python3 rollout_xls_shell3.py --apply        # 正式执行（需 approved3.flag）
  RAGFLOW_API_KEY=… python3 rollout_xls_shell3.py --apply --only 724-829两场洪水.xls
  RAGFLOW_API_KEY=… python3 rollout_xls_shell3.py --rollback "724-829两场洪水.xls" [--apply]

状态：shell_state3.json（幂等）；删前全量块存档 xls_chunks_archive3.json；台账
manifest_xls_shell3.json。锚点断言数据源 = questions_xls.jsonl 的 fusion_doc 列。

D7（TB0207.xls year 207→1997）不经壳迁移——2026-09-04 已用 patch_document 就地完成并
doc_meta 核验（out/xls_fusion/d7_tb0207_year_patch.json 留痕），不在本 TARGETS 内。

apply 后必做（P2-9/P2-9b 教训）：3 个新壳无 tag_feas，driver_wave3.py 逐文件回填
{洪水资料:10, 基础数据:8}（两维，勿加第三维——词表模长碰撞见
out/retrieval_tuning/P2_9B_WAVE2_COLLISION.md）。
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

# 元数据以 2026-09-04 活库只读核验值为基（11 schema 字段口径；location 活值 null，
# 按 wave-1 口径统一记"全库"）。纠偏两处：2001年洪水过程线 flood_event 历年统计→2010-7
# 且 year 2001→2010（表内标题 20100724 铁证）；724-829两场洪水 flood_event 其他→2010-7
# 主值（第二张摘录表 20030829 系内容层新发现，单值字段不扩 2003-8，评审裁定）。
# 执行顺序 = 现块数升序（16→24→43，最小文档先验证管线）。
TARGETS = [
    {
        "kb_name": "724-829两场洪水.xls",
        "corpus_path": "06-历年洪水资料/07-其他洪水事件/724-829两场洪水.xls",
        "fusion_md": HERE / "fusion" / "724-829两场洪水.xls.md",
        "probe": {"question": "2003年8·29洪水柳林断面洪峰流量是多少？", "anchor": "327.2"},
        # [flood_event 其他→2010-7 主值裁定；第二场 20030829 不扩枚举（单值字段），
        #  通过内容词"20030829"可检索。year None→2010（主场次）。]
        "meta": {"doc_category": "洪水资料", "sub_category": "其他洪水事件",
                 "flood_event": "2010-7", "doc_type": "表格", "year": 2010,
                 "source_format": "excel", "quality": "high", "doc_nature": "技术",
                 "location": "全库", "flood_magnitude": "不适用",
                 "rel": "06-历年洪水资料/07-其他洪水事件/724-829两场洪水.xls"},
        "keywords": ["7·24洪水", "2003", "摘录表"],
    },
    {
        "kb_name": "2010年下泄水量统计.xls",
        "corpus_path": "06-历年洪水资料/08-历年洪水统计/2010年下泄水量统计.xls",
        "fusion_md": HERE / "fusion" / "2010年下泄水量统计.xls.md",
        "probe": {"question": "2010年8·13洪水截至9月15日累计下泄水量是多少万立方米？", "anchor": "4802.42"},
        # [flood_event 历年统计→2010-8（表内标题 20100813）；year 2010 不变。]
        "meta": {"doc_category": "洪水资料", "sub_category": "2010年洪水(8-13)",
                 "flood_event": "2010-8", "doc_type": "表格", "year": 2010,
                 "source_format": "excel", "quality": "high", "doc_nature": "技术",
                 "location": "全库", "flood_magnitude": "不适用",
                 "rel": "06-历年洪水资料/08-历年洪水统计/2010年下泄水量统计.xls"},
        "keywords": ["8·13洪水", "2010", "下泄水量"],
    },
    {
        "kb_name": "2001年洪水过程线.xls",
        "corpus_path": "06-历年洪水资料/08-历年洪水统计/2001年洪水过程线.xls",
        "fusion_md": HERE / "fusion" / "2001年洪水过程线.xls.md",
        "probe": {"question": "2010年7·24洪水柳林断面洪峰流量是多少？", "anchor": "1100"},
        # [文件名 2001 系误名：表内两张主表标题均含 20100724，与 724-829摘录表逐值互证。
        #  flood_event 历年统计→2010-7、year 2001→2010（批次二裁定，REVIEW_WAVE3.md）。]
        "meta": {"doc_category": "洪水资料", "sub_category": "2010年洪水(7-24)",
                 "flood_event": "2010-7", "doc_type": "表格", "year": 2010,
                 "source_format": "excel", "quality": "high", "doc_nature": "技术",
                 "location": "全库", "flood_magnitude": "不适用",
                 "rel": "06-历年洪水资料/08-历年洪水统计/2001年洪水过程线.xls"},
        "keywords": ["7·24洪水", "2010", "过程线"],
    },
]

ARCHIVE = HERE / "xls_chunks_archive3.json"
STATE = HERE / "shell_state3.json"
MANIFEST = HERE / "manifest_xls_shell3.json"


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

    if args.apply and not (HERE / "approved3.flag").exists():
        raise SystemExit("人工评审门未过：缺 approved3.flag（先审阅 fusion/ 3 份新文本与 REVIEW_WAVE3.md，再 touch approved3.flag）")

    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)
    shell_import.run_wave(client, DS3, TARGETS, CORPUS, load_questions(), args.apply,
                          only=args.only, archive_path=ARCHIVE,
                          state_path=STATE, manifest_path=MANIFEST)


if __name__ == "__main__":
    main()
