#!/usr/bin/env python3
"""xls 壳迁移 wave-2 spec（批次一 = F1 收口 D1+D5：S 级 5 份 + dup 独有 sheet 并入）。

引擎见 src/shell_import.py；本文件只留目标、评审门与 CLI（与 rollout_xls_shell.py 同构）。

前置：fusion/ 5 份融合文本已过人工评审门（approved2.flag）。
流程（引擎内，严格串行）：pre-flight（语料/融合文本/题库锚点，不过拒删）
→ 存档→删文档→上传壳 UNSTART→patch 元数据→挂融合切片→探针验证。

用法：
  RAGFLOW_API_KEY=… python3 rollout_xls_shell2.py                # dry-run：pre-flight+列步骤
  RAGFLOW_API_KEY=… python3 rollout_xls_shell2.py --apply        # 正式执行（需 approved2.flag）
  RAGFLOW_API_KEY=… python3 rollout_xls_shell2.py --apply --only 降雨量统计.xls
  RAGFLOW_API_KEY=… python3 rollout_xls_shell2.py --rollback "降雨量统计.xls"            # 只打印预案
  RAGFLOW_API_KEY=… python3 rollout_xls_shell2.py --rollback "降雨量统计.xls" --apply    # 真正回滚单份

状态：shell_state2.json（幂等）；删前全量块存档 xls_chunks_archive2.json；台账
manifest_xls_shell2.json。锚点断言数据源 = questions_xls.jsonl 的 fusion_doc 列。

apply 后必做（P2-9 教训）：15 个旧壳 + 本批 5 个新壳均无 tag_feas，需按元数据词表跑
ES _update_by_query 回填，否则跨库/多库检索场景吃亏（out/retrieval_tuning/REPORT.md）。
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

# 元数据以 2026-09-04 活库只读核验值为基（11 schema 字段口径；location 活值为 null，
# 按 wave-1 口径统一记"全库"）。唯一纠偏：洪水统计.xls flood_event 2019-7→2019-9
# （评审批次一裁定：2019-7 不在枚举，与 FLOOD_EVENT_BY_SUBDIR"04-2019年洪水(9-14)"→
# 2019-9 矛盾；活库 id 63250820 同错）。执行顺序 = 现块数升序（最小文档先验证管线）。
TARGETS = [
    {
        "kb_name": "降雨量统计.xls",
        "corpus_path": "06-历年洪水资料/05-2013年洪水(7-22)/降雨量统计.xls",
        "fusion_md": HERE / "fusion" / "降雨量统计.xls.md",
        "probe": {"question": "2013年7·22洪水柳林洪峰流量和折合入库洪峰各是多少？", "anchor": "258"},
        "meta": {"doc_category": "洪水资料", "sub_category": "2013年洪水(7-22)",
                 "flood_event": "2013-7", "doc_type": "表格", "year": 2013,
                 "source_format": "excel", "quality": "high", "doc_nature": "技术",
                 "location": "全库", "flood_magnitude": "不适用",
                 "rel": "06-历年洪水资料/05-2013年洪水(7-22)/降雨量统计.xls"},
        "keywords": ["7·22洪水", "2013", "降雨量"],
    },
    {
        "kb_name": "洪水统计.xls",
        "corpus_path": "06-历年洪水资料/04-2019年洪水(9-14)/洪水统计.xls",
        "fusion_md": HERE / "fusion" / "洪水统计.xls.md",
        "probe": {"question": "9·14洪水最大一日和五日洪水总量是多少万立方米？", "anchor": "1065"},
        # [flood_event 2019-7→2019-9 系批次一纠偏：2019-7 不在枚举，且与
        #  FLOOD_EVENT_BY_SUBDIR 映射矛盾（04-2019年洪水(9-14)→2019-9）；
        #  活库 id 63250820 同错。apply 后 doc_meta 索引随 patch_document 自动同步
        #  （P1-5 实证 5/5）；注意 --rollback 会按存档快照打回 2019-7，回滚后需复核]
        "meta": {"doc_category": "洪水资料", "sub_category": "2019年洪水(9-14)",
                 "flood_event": "2019-9", "doc_type": "表格", "year": 2019,
                 "source_format": "excel", "quality": "high", "doc_nature": "技术",
                 "location": "全库", "flood_magnitude": "不适用",
                 "rel": "06-历年洪水资料/04-2019年洪水(9-14)/洪水统计.xls"},
        "keywords": ["9·14洪水", "2019", "洪水统计"],
    },
    {
        "kb_name": "洪水过程(2).xls",
        "corpus_path": "06-历年洪水资料/03-2020年洪水(8-16)/洪水过程.xls",
        "fusion_md": HERE / "fusion" / "洪水过程(2).xls.md",
        "probe": {"question": "2020年8·16洪水时水库累计泄水多少？泄水用途是什么？", "anchor": "536.92"},
        "meta": {"doc_category": "洪水资料", "sub_category": "2020年洪水(8-16)",
                 "flood_event": "2020-8", "doc_type": "表格", "year": 2020,
                 "source_format": "excel", "quality": "high", "doc_nature": "技术",
                 "location": "全库", "flood_magnitude": "不适用",
                 "rel": "06-历年洪水资料/03-2020年洪水(8-16)/洪水过程.xls"},
        "keywords": ["8·16洪水", "2020", "泄水"],
    },
    {
        "kb_name": "洪水过程(1).xls",
        "corpus_path": "06-历年洪水资料/02-2021年洪水调度/9-25洪水/洪水过程.xls",
        "fusion_md": HERE / "fusion" / "洪水过程(1).xls.md",
        "probe": {"question": "9·25洪水总量是多少？期间最高库水位是多少？", "anchor": "2567.22"},
        "meta": {"doc_category": "洪水资料", "sub_category": "2021年洪水调度",
                 "flood_event": "2021-09", "doc_type": "表格", "year": 2021,
                 "source_format": "excel", "quality": "high", "doc_nature": "技术",
                 "location": "全库", "flood_magnitude": "不适用",
                 "rel": "06-历年洪水资料/02-2021年洪水调度/9-25洪水/洪水过程.xls"},
        "keywords": ["9·25洪水", "2021", "泄水"],
    },
    {
        "kb_name": "洪水过程.xls",
        "corpus_path": "06-历年洪水资料/02-2021年洪水调度/10-3洪水/洪水过程.xls",
        "fusion_md": HERE / "fusion" / "洪水过程.xls.md",
        "probe": {"question": "2021年11月桃曲坡水库溢洪道溢流持续多少天？累计溢流量多少万立方米？",
                  "anchor": "679"},
        # [本文本并入 洪水过程_dup.xls 独有 sheet（入库洪水/桃库溢洪道溢流/降雨量预报值），
        #  dup 本身不入库（D5 裁定）；rel 保持语料在库文件路径]
        "meta": {"doc_category": "洪水资料", "sub_category": "2021年洪水调度",
                 "flood_event": "2021-10", "doc_type": "表格", "year": 2021,
                 "source_format": "excel", "quality": "high", "doc_nature": "技术",
                 "location": "全库", "flood_magnitude": "不适用",
                 "rel": "06-历年洪水资料/02-2021年洪水调度/10-3洪水/洪水过程.xls"},
        "keywords": ["10·3洪水", "2021", "溢洪道"],
    },
]

ARCHIVE = HERE / "xls_chunks_archive2.json"
STATE = HERE / "shell_state2.json"
MANIFEST = HERE / "manifest_xls_shell2.json"


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
            # 评审 F2 同款：rollback 破坏性操作须显式 --apply；不加只打印预案（不连库）
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

    if args.apply and not (HERE / "approved2.flag").exists():
        raise SystemExit("人工评审门未过：缺 approved2.flag（先审阅 fusion/ 5 份新文本与 REVIEW_WAVE2.md，再 touch approved2.flag）")

    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)
    shell_import.run_wave(client, DS3, TARGETS, CORPUS, load_questions(), args.apply,
                          only=args.only, archive_path=ARCHIVE,
                          state_path=STATE, manifest_path=MANIFEST)


if __name__ == "__main__":
    main()
