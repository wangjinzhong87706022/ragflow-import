#!/usr/bin/env python3
"""xls 壳迁移 wave spec（引擎见 src/shell_import.py；本文件只留目标、评审门与 CLI）。

前置：fusion/*.md 三份融合文本已过人工评审门（approved.flag）。
流程（引擎内，严格串行）：pre-flight（语料/融合文本/题库锚点，不过拒删）
→ 存档→删文档→上传壳 UNSTART→patch 元数据→挂融合切片→探针验证。

用法：
  RAGFLOW_API_KEY=… python3 rollout_xls_shell.py                # dry-run：只列步骤
  RAGFLOW_API_KEY=… python3 rollout_xls_shell.py --apply        # 正式执行（需 approved.flag）
  RAGFLOW_API_KEY=… python3 rollout_xls_shell.py --apply --only 洪水统计\(1\).xls
  RAGFLOW_API_KEY=… python3 rollout_xls_shell.py --rollback "洪水统计(1).xls"  # 回滚单份

状态：shell_state.json（幂等，OK 的跳过）；删前全量块存档 xls_chunks_archive.json；
台账 manifest_xls_shell.json。锚点断言数据源 = questions_xls.jsonl 的 fusion_doc 列。
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

# 目标 spec：kb 名 / 语料相对路径 / 融合文本 / 探针(问句+锚点) / 11字段元数据 / 挂片关键词
# [洪水统计(1) 元数据按事件纠偏建议值：目录 2008 实为 2018.8.21 洪水，见 REVIEW.md 裁定点 1]
TARGETS = [
    {
        "kb_name": "洪水统计(1).xls",
        "corpus_path": "06-历年洪水资料/06-2008年洪水(8-22)/洪水统计.xls",
        "fusion_md": HERE / "fusion" / "洪水统计(1).md",
        "probe": {"question": "8.21洪水瑶曲站日降雨量是多少毫米", "anchor": "148"},
        "meta": {"doc_category": "洪水资料", "sub_category": "2018年洪水(8-21)",
                 "flood_event": "其他", "doc_type": "表格", "year": 2018,
                 "source_format": "excel", "quality": "high", "doc_nature": "技术",
                 "location": "全库", "flood_magnitude": "不适用",
                 "rel": "06-历年洪水资料/06-2008年洪水(8-22)/洪水统计.xls"},
        "keywords": ["8.21洪水", "2018", "雨量站"],
    },
    {
        "kb_name": "较大洪水统计表.xls",
        "corpus_path": "06-历年洪水资料/08-历年洪水统计/较大洪水统计表.xls",
        "fusion_md": HERE / "fusion" / "较大洪水统计表.md",
        "probe": {"question": "桃曲坡流域实测最大洪峰流量的洪水是哪一场", "anchor": "1353"},
        "meta": {"doc_category": "洪水资料", "sub_category": "历年洪水统计",
                 "flood_event": "历年统计", "doc_type": "表格",
                 "source_format": "excel", "quality": "high", "doc_nature": "统计",
                 "location": "全库", "flood_magnitude": "不适用",
                 "rel": "06-历年洪水资料/08-历年洪水统计/较大洪水统计表.xls"},
        "keywords": ["较大洪水", "洪峰流量", "洪水目录"],
    },
    {
        "kb_name": "2011年下泄水量统计.xls",
        "corpus_path": "06-历年洪水资料/08-历年洪水统计/2011年下泄水量统计.xls",
        "fusion_md": HERE / "fusion" / "2011年下泄水量统计.md",
        "probe": {"question": "2011年桃曲坡水库累计弃水量是多少万立方米", "anchor": "10561"},
        "meta": {"doc_category": "洪水资料", "sub_category": "历年洪水统计",
                 "flood_event": "历年统计", "doc_type": "表格", "year": 2011,
                 "source_format": "excel", "quality": "high", "doc_nature": "统计",
                 "location": "全库", "flood_magnitude": "不适用",
                 "rel": "06-历年洪水资料/08-历年洪水统计/2011年下泄水量统计.xls"},
        "keywords": ["2011", "泄水过程", "弃水量"],
    },
]

ARCHIVE = HERE / "xls_chunks_archive.json"
STATE = HERE / "shell_state.json"
MANIFEST = HERE / "manifest_xls_shell.json"


def load_questions():
    lines = (HERE / "questions_xls.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(l) for l in lines if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default="", help="只处理指定 KB 名")
    ap.add_argument("--rollback", default="", help="回滚指定 KB 名（重传+naive 重解析）")
    args = ap.parse_args()

    if args.rollback:
        target = next((t for t in TARGETS if t["kb_name"] == args.rollback), None)
        if not target:
            raise SystemExit(f"未知目标: {args.rollback}")
        client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                               api_key=RAGFLOW_API_KEY)
        shell_import.rollback(client, DS3, target, CORPUS, ARCHIVE)
        return

    if args.apply and not (HERE / "approved.flag").exists():
        raise SystemExit("人工评审门未过：缺 approved.flag（先审阅 fusion/*.md 再 touch approved.flag）")

    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM,
                           api_key=RAGFLOW_API_KEY)
    shell_import.run_wave(client, DS3, TARGETS, CORPUS, load_questions(), args.apply,
                          only=args.only, archive_path=ARCHIVE,
                          state_path=STATE, manifest_path=MANIFEST)


if __name__ == "__main__":
    main()
