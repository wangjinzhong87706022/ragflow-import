"""
jhc_constants.py — 泾惠渠预处理与导入管线共享的纯常量。

本模块刻意不 import config，避免触发 KB_PROFILE 画像加载副作用；
jhc_prepare.py（阶段0）、profiles/jinghuiqu.py、corpus.py（经 config 转发）
均从此处读取，消除此前 jhc_prepare 与 profile 间的双源真理漂移风险。
"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根（本文件位于 src/ 下，上两级即项目根）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 合并 PDF 标记（corpus.scan 据此置 source_format=merged_pdf）；全角括号防与
# 原生文件名冲突，且保证同目录多个分组产物互不覆盖。
# corpus.source_format_from_name 经 config.MERGED_TAG 引用同一常量。
MERGED_TAG = "（合并）"

# 根目录散落文件归入的一级目录（= jhc3 计划处）
ROOT_DOC_TARGET = "计划处"

# 可导入文档扩展名（jhc_prepare 复制 + corpus 扫描 + profile 声明共用）
DOC_EXTS = frozenset({".pdf", ".docx", ".doc", ".xls", ".xlsx"})
# 扫描件图片扩展名（jhc_prepare 合并 PDF 输入）
IMG_EXTS = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff"})
# 压缩包扩展名（jhc_prepare 检查旁侧解压目录）
ARCHIVE_EXTS = frozenset({".rar", ".zip"})
# 目录遍历时无条件忽略的杂项（jhc_prepare.JUNK_DIRS 与 profile.SKIP_DIRS 子集）
JUNK_DIRS = frozenset({"__MACOSX", ".claude"})
# 杂项文件名（Thumbs.db 是文件非目录，按文件名过滤而非目录）
JUNK_FILES = frozenset({"Thumbs.db", ".DS_Store"})

# 原始资料根（jhc_prepare 输入，只读）与暂存语料根（jhc_prepare 输出 = 导入源）
# env 覆盖优先，与 profiles/jinghuiqu.py 保持同一来源
JHC_SRC_ROOT_DEFAULT = Path(os.getenv("JHC_SRC_ROOT", r"D:\doc\taineng\泾惠渠项目\资料收集"))
JHC_CORPUS_ROOT_DEFAULT = Path(os.getenv("JHC_CORPUS_ROOT", str(PROJECT_ROOT / "out" / "jhc_corpus")))

# VLM 图片分诊结果（image_triage.py 产出，out/ 下非 git 跟踪）：
# jhc_prepare 据此在合并组内剔除 content=blank 的空白页
TRIAGE_JSONL_DEFAULT = PROJECT_ROOT / "out" / "triage" / "image_triage.jsonl"
# 合并 PDF 页图预处理上限：长边超过则降采样至此像素并重编码 JPEG（质量 85），
# 防超大页撑爆解析器、也防旧转换式 FlateDecode 体积膨胀
MERGE_MAX_LONG_SIDE = 3500
MERGE_JPEG_QUALITY = 85