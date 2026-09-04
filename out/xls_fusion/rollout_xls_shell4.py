#!/usr/bin/env python3
"""xls 壳迁移 wave-4 spec（批次三 = F1 收口 D3+D4：防洪减灾统计表 + 洪水过程(3) 2011 归正）。

引擎见 src/shell_import.py；本文件只留目标、评审门与 CLI（与 rollout_xls_shell3.py 同构）。

前置：fusion/ 2 份融合文本已过人工评审门（approved4.flag）。
流程（引擎内，严格串行）：pre-flight（语料/融合文本/题库锚点，不过拒删）
→ 存档→删文档→上传壳 UNSTART→patch 元数据→挂融合切片→探针验证。

用法：
  RAGFLOW_API_KEY=… python3 rollout_xls_shell4.py                # dry-run：pre-flight+列步骤
  RAGFLOW_API_KEY=… python3 rollout_xls_shell4.py --apply        # 正式执行（需 approved4.flag）
  RAGFLOW_API_KEY=… python3 rollout_xls_shell4.py --apply --only 防洪减灾统计表.xls
  RAGFLOW_API_KEY=… python3 rollout_xls_shell4.py --rollback "防洪减灾统计表.xls" [--apply]

状态：shell_state4.json（幂等）；删前全量块存档 xls_chunks_archive4.json；台账
manifest_xls_shell4.json。锚点断言数据源 = questions_xls.jsonl 的 fusion_doc 列。

前置已生效（2026-09-04，批次三枚举链）：config.py METADATA_SCHEMA 扩 2011-7 +
tag_vocab.py VOCAB_ROWS 16→17 + 测试 pin + out/tag_vocab/taoqupo_vocab.txt 再生 +
ds0 手挂 "\n2011-7" 块（17 块）+ ds1–ds5 put_metadata_config 重注册（flood_event 12 值）。

apply 后必做（P2-9/P2-9b 教训）：2 个新壳无 tag_feas，driver_wave4.py 逐文件回填
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
# 按 wave-1 口径统一记"全库"）。纠偏：
# ① 洪水过程(3).xls 错放在 05-2013 目录（档案级错误③）：表内标题均 20110729、
#    序列日 40752=2011-07-28 → flood_event 2013-7→2011-7（枚举扩，批次三）、
#    year 2013→2011、sub_category→2011年洪水(7-29)。
# ② 防洪减灾统计表.xls：flood_event 历年统计→2021-10 主值（2021 年渭河1/2/3号效益
#    统计，渭河3号 10·5 拦蓄 6470万m³ 占 70% 为主场次，REVIEW_WAVE4 裁定点）、
#    year 无→2021、doc_nature 技术→统计（全库首例统计类）。
# 执行顺序 = 现块数升序（3→9，最小文档先验证管线）。
TARGETS = [
    {
        "kb_name": "防洪减灾统计表.xls",
        "corpus_path": "06-历年洪水资料/08-历年洪水统计/防洪减灾统计表.xls",
        "fusion_md": HERE / "fusion" / "防洪减灾统计表.xls.md",
        "probe": {"question": "2021年三次渭河编号洪水合计拦蓄洪量是多少万立方米？", "anchor": "9230"},
        # [flood_event 历年统计→2021-10 主值裁定；year None→2021；doc_nature→统计。]
        "meta": {"doc_category": "洪水资料", "sub_category": "历年洪水统计",
                 "flood_event": "2021-10", "doc_type": "表格", "year": 2021,
                 "source_format": "excel", "quality": "high", "doc_nature": "统计",
                 "location": "全库", "flood_magnitude": "不适用",
                 "rel": "06-历年洪水资料/08-历年洪水统计/防洪减灾统计表.xls"},
        "keywords": ["防洪减灾", "2021", "渭河"],
    },
    {
        "kb_name": "洪水过程(3).xls",
        "corpus_path": "06-历年洪水资料/05-2013年洪水(7-22)/洪水过程.xls",
        "fusion_md": HERE / "fusion" / "洪水过程(3).xls.md",
        "probe": {"question": "2011年7·29洪水柳林断面洪峰流量是多少？", "anchor": "397"},
        # [目录错放纠偏：flood_event 2013-7→2011-7（枚举扩 2011-7）、year 2013→2011、
        #  sub_category 2013年洪水(7-22)→2011年洪水(7-29)。下泄表日期序列 40388/40389
        #  =2010-07-29/30 系上年复制残留，库水位逐点互证同场次（文本仲裁留痕②）。]
        "meta": {"doc_category": "洪水资料", "sub_category": "2011年洪水(7-29)",
                 "flood_event": "2011-7", "doc_type": "表格", "year": 2011,
                 "source_format": "excel", "quality": "high", "doc_nature": "技术",
                 "location": "全库", "flood_magnitude": "不适用",
                 "rel": "06-历年洪水资料/05-2013年洪水(7-22)/洪水过程.xls"},
        "keywords": ["7·29洪水", "2011", "洪水过程"],
    },
]

ARCHIVE = HERE / "xls_chunks_archive4.json"
STATE = HERE / "shell_state4.json"
MANIFEST = HERE / "manifest_xls_shell4.json"


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

    if args.apply and not (HERE / "approved4.flag").exists():
        raise SystemExit("人工评审门未过：缺 approved4.flag（先审阅 fusion/ 2 份新文本与 REVIEW_WAVE4.md，再 touch approved4.flag）")

    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)
    shell_import.run_wave(client, DS3, TARGETS, CORPUS, load_questions(), args.apply,
                          only=args.only, archive_path=ARCHIVE,
                          state_path=STATE, manifest_path=MANIFEST)


if __name__ == "__main__":
    main()
