"""
profiles/jinghuiqu.py — 泾惠渠灌区项目知识库画像（KB_PROFILE=jinghuiqu 时覆盖 config 默认值）。

语料源：D:\\doc\\taineng\\泾惠渠项目\\资料收集（805 文件，扫描件占比大）。
本画像配合 jhc_prepare.py（阶段 0.5）使用：多页扫描件图片先合并为
``<组名>（合并）.pdf``，与原生文档一起归集到暂存语料根 out/jhc_corpus/，
再由 ``python -m corpus`` 按一级目录=知识库指派。

一期边界（见方案 2026-09-22）：
- 按来源部门分 3 库（工程管理处 / 工程建设处资料 / 计划处）
- 三库统一 naive、graphrag/raptor 显式关闭（防 naive 创建默认 True）
- 不建标签库（不同用户/API key，桃曲坡词表不可复用）
"""
import os
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent   # 项目根（src/ 上一级）

# 原始资料根（jhc_prepare 输入，只读）与暂存语料根（jhc_prepare 输出 = 导入源）
JHC_SRC_ROOT = pathlib.Path(os.getenv("JHC_SRC_ROOT", r"D:\doc\taineng\泾惠渠项目\资料收集"))
CORPUS_ROOT = pathlib.Path(os.getenv("JHC_CORPUS_ROOT", str(_ROOT / "out" / "jhc_corpus")))

# 项目专属 OUT_DIR：隔离 mapping.csv / setup_state.json / import_state.json
OUT_DIR = _ROOT / "out" / "jinghuiqu"

# 一级目录 = 知识库（暂存根由 jhc_prepare 保证全覆盖；根目录散落文档归入计划处）
DIR_DATASET = {
    "工程管理处":    "jhc1",
    "工程建设处资料": "jhc2",
    "计划处":        "jhc3",
}

# 3 个业务库：naive / chunk 512 / auto_keywords 8；graphrag+raptor 一期全关
_NAIVE_PARSER_CONFIG = {
    "chunk_token_num": 512,
    "auto_keywords": 8,
    "auto_questions": 0,
    "topn_tags": 3,
    "tag_kb_ids": [],
    "graphrag": {"use_graphrag": False},   # naive 默认 True，必须显式关闭
    "raptor": {"use_raptor": False},       # naive 默认 True，必须显式关闭
}

DATASETS = [
    {"key": "jhc1", "name": "泾惠渠-工程管理处",    "chunk_method": "naive",
     "parser_config": dict(_NAIVE_PARSER_CONFIG)},
    {"key": "jhc2", "name": "泾惠渠-工程建设处资料", "chunk_method": "naive",
     "parser_config": dict(_NAIVE_PARSER_CONFIG)},
    {"key": "jhc3", "name": "泾惠渠-计划处",        "chunk_method": "naive",
     "parser_config": dict(_NAIVE_PARSER_CONFIG)},
]

# 一期不建标签库（run_setup 据此跳过 ds0 与词表上传，tag_kb_ids 下发 []）
USE_TAG_KB = False

# dataset key → 元数据推导（corpus.derive_metadata 使用）
DOC_CATEGORY_BY_DS = {"jhc1": "工程管理", "jhc2": "工程建设", "jhc3": "计划审批"}
DOC_NATURE_BY_DS = {"jhc1": "管理", "jhc2": "技术", "jhc3": "管理"}

# 洪水事件语义不适用于泾惠渠语料（字段保留但恒为空，run_import 自动省略空值）
FLOOD_EVENT_BY_SUBDIR = {}

# 元数据 schema：沿用 11 字段架构（run_import._META_FIELD_MAP 不动），仅换值域
METADATA_SCHEMA = [
    {"key": "doc_category",    "type": "string", "description": "文档大类",    "enum": ["工程管理", "工程建设", "计划审批"]},
    {"key": "sub_category",    "type": "string", "description": "子类（二级目录）", "enum": None},
    {"key": "flood_event",     "type": "string", "description": "关联洪水事件（本项目不使用）", "enum": None},
    {"key": "doc_type",        "type": "string", "description": "文档形态",    "enum": ["文本", "表格", "图片", "图纸"]},
    {"key": "year",            "type": "string", "description": "年份",        "enum": None},
    {"key": "source_format",   "type": "string", "description": "来源格式",    "enum": ["pdf", "word", "excel", "merged_pdf"]},
    {"key": "quality",         "type": "string", "description": "OCR质量分级",  "enum": ["high", "medium", "low"]},
    {"key": "responsible_dept", "type": "string", "description": "责任/发文部门", "enum": None},
    {"key": "doc_nature",      "type": "string", "description": "文件性质",    "enum": ["法规", "技术", "管理", "统计"]},
    {"key": "location",        "type": "string", "description": "工程部位",    "enum": None},
    {"key": "flood_magnitude", "type": "string", "description": "洪水量级（本项目不使用）", "enum": None},
]

# 多模式提取依赖"长词优先"匹配顺序（同桃曲坡教训：短词在前会把前缀错配）
LOCATION_KEYWORDS = sorted([
    "张家山水库", "贺兰水库", "西郊水库", "总干渠", "干支渠道",
    "张家山", "贺兰", "西郊", "支渠", "干渠", "渠道", "灌区", "枢纽",
], key=len, reverse=True)

DEPT_KEYWORDS = sorted([
    "工程管理处", "工程建设处", "计划处", "维修养护大队",
    "防汛办", "泾惠渠管理局", "泾惠渠灌区",
], key=len, reverse=True)

# 压缩包经 jhc_prepare 校验后统一打 skip 标记；此处仅挡杂项目录
SKIP_DIRS = {"__MACOSX", ".claude", "Thumbs.db目录"}

# 可导入扩展名与桃曲坡一致（图片已在预处理阶段合并为 PDF，不直导）
DOC_EXTS = (".pdf", ".docx", ".doc", ".xls", ".xlsx")
