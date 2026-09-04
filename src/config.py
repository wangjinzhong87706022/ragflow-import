import os
import pathlib

# API
# 凭据仅经环境变量注入；下面的占位默认值会在 RAGFlowClient 构造时 fail-fast
API_BASE = "http://localhost:9380/api/v1"
RAGFLOW_EMAIL = os.getenv("RAGFLOW_EMAIL", "placeholder@example.com")
RAGFLOW_PASSWORD = os.getenv("RAGFLOW_PASSWORD", "placeholder")
# API-key（Web UI 系统设置生成）：设置后 RAGFlowClient 走 Bearer 免登录模式，无需邮箱/密码
RAGFLOW_API_KEY = os.getenv("RAGFLOW_API_KEY", "")
PUBLIC_PEM = "/opt/git/ragflow/conf/public.pem"

# VLM 端点与模型（vision_extract.py 阶段1 用；LLM_API_KEY 凭据只走环境变量）
# 模型名经 /v1/models 实查（capabilities 含 multimodal），与 requirements §2 一致
LLM_API_ENDPOINT = os.getenv("LLM_API_ENDPOINT", "https://llm.openagp.top:9080/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen3.8-27B-Q4_K_M.gguf")
# 单次读图调用的读超时：27B Q4 多模态生成单张可超分钟，120s 曾把正常生成掐成失败
VISION_CALL_TIMEOUT = int(os.getenv("LLM_READ_TIMEOUT", "600"))

# Corpus roots (read-only)
# CORPUS_ROOT = 导入语料源：整理后的原始文件库（pdf/doc/xls 直导 → RAGFlow 引用可锚定原文）
# DERIVED_ROOT / ORIGINALS_ROOT = 上一轮文本抽取流程的遗留根（tables.py 等兼容用，不再直接导）
CORPUS_ROOT = pathlib.Path("/home/scada/SmartTwinRes-skills/pdfs")
DERIVED_ROOT = pathlib.Path("/home/scada/SmartTwinRes-skills/pdf_text_analysis")
ORIGINALS_ROOT = CORPUS_ROOT

# 可导入的文档扩展名（图片走 VLM 预处理管线，不入扫描范围）
DOC_EXTS = (".pdf", ".docx", ".doc", ".xls", ".xlsx")

# 目录→知识库指派（2026-08-26 语料源切换：pdfs/ 一级目录即分类）
DIR_DATASET = {
    "01-核心文档-四案":   "ds1",
    "02-安全鉴定与评价":  "ds5",
    "03-施工图纸与设计":  "ds5",
    "04-确权划界":        "ds2",
    "05-基础数据与曲线":  "ds2",   # 本目录为纯图片，经 VLM 预处理后另行导入
    "06-历年洪水资料":    "ds3",
    "07-管理资料":        "ds4",
    "08-政策文件":        "ds4",
}

# Output root — <project-root>/out，随代码位置自适应（项目根 = src/ 的上一级）
# 注意：不在 import 时建目录（无副作用）；由各写入方自行 mkdir(parents=True)
OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "out"

# Datasets (spec §2.1 + §2.2)
# graphrag/raptor 一律嵌套在 parser_config 内下发（v0.27.0 的 KB.parser_config 就是这个形状；
# PUT dataset 是 deep-merge，兄弟顶层键永远不会生效）。注意创建期默认：
#   naive → raptor.use_raptor=True 且 graphrag.use_graphrag=True；laws/paper → 均为 False。
# 因此凡需要"关掉"的库都必须显式发 False，否则默认开启（review P0-4）。
DATASETS = [
    {
        "key": "ds1",
        "name": "规程与预案",
        "chunk_method": "laws",
        "parser_config": {
            "chunk_token_num": 512,
            "auto_keywords": 10,
            "auto_questions": 3,
            "topn_tags": 3,
            "tag_kb_ids": [],
            # laws 创建默认 graphrag/raptor 均为 False → 显式打开 GraphRAG 与 Raptor
            "graphrag": {
                "use_graphrag": True,
                "method": "light",
                "entity_types": ["FloodEvent", "Station", "Structure", "Person", "Regulation", "Parameter"],
                "resolution": True,
            },
            "raptor": {"use_raptor": True},
        },
    },
    # ds2: naive, chunk_token_num=256, auto_keywords=8, topn_tags=3, no graphrag, no raptor
    {
        "key": "ds2",
        "name": "基础数据",
        "chunk_method": "naive",
        "parser_config": {
            "chunk_token_num": 256,
            "auto_keywords": 8,
            "auto_questions": 0,
            "topn_tags": 3,
            "tag_kb_ids": [],
            "graphrag": {"use_graphrag": False},   # naive 默认 True，必须显式关闭
            "raptor": {"use_raptor": False},
        },
    },
    # ds3: naive, chunk_token_num=256, auto_questions=5, topn_tags=3, graphrag same as ds1, no raptor
    {
        "key": "ds3",
        "name": "洪水资料",
        "chunk_method": "naive",
        "parser_config": {
            "chunk_token_num": 256,
            "auto_keywords": 0,
            "auto_questions": 5,
            "topn_tags": 3,
            "tag_kb_ids": [],
            "graphrag": {
                "use_graphrag": True,
                "method": "light",
                "entity_types": ["FloodEvent", "Station", "Structure", "Person", "Regulation", "Parameter"],
                "resolution": True,
            },
            "raptor": {"use_raptor": False},       # naive 默认 True，必须显式关闭
        },
    },
    # ds4: naive, chunk_token_num=256, auto_keywords=8, topn_tags=2, no graphrag, no raptor
    {
        "key": "ds4",
        "name": "组织管理",
        "chunk_method": "naive",
        "parser_config": {
            "chunk_token_num": 256,
            "auto_keywords": 8,
            "auto_questions": 0,
            "topn_tags": 2,
            "tag_kb_ids": [],
            "graphrag": {"use_graphrag": False},   # naive 默认 True，必须显式关闭
            "raptor": {"use_raptor": False},
        },
    },
    # ds5: paper, chunk_token_num=512, auto_keywords=8, topn_tags=3, no graphrag, raptor use_raptor=True
    {
        "key": "ds5",
        "name": "工程资料",
        "chunk_method": "paper",
        "parser_config": {
            "chunk_token_num": 512,
            "auto_keywords": 8,
            "auto_questions": 0,
            "topn_tags": 3,
            "tag_kb_ids": [],
            "graphrag": {"use_graphrag": False},
            "raptor": {"use_raptor": True},
        },
    },
]

TAG_KB = {"key": "ds0", "name": "桃曲坡标签库", "chunk_method": "tag"}

# 11 metadata fields (spec §4.1)
METADATA_SCHEMA = [
    {"key": "doc_category",    "type": "string",  "description": "文档大类",           "enum": ["规程预案","基础数据","洪水资料","组织管理","工程资料"]},
    {"key": "sub_category",    "type": "string",  "description": "子类",               "enum": None},
    {"key": "flood_event",     "type": "string",  "description": "关联洪水事件",        "enum": ["2021-10","2021-09","2020-8","2019-9","2013-7","2011-7","2010-8","2010-7","2018-8","2008-8","其他","历年统计"]},
    {"key": "doc_type",        "type": "string",  "description": "文档形态",           "enum": ["文本","表格","图片","图纸"]},
    {"key": "year",            "type": "number",  "description": "年份",               "enum": None},
    {"key": "source_format",   "type": "string",  "description": "来源格式",           "enum": ["pdf","word","excel","ocr_jpg","ocr_png","native_xlsx"]},
    {"key": "quality",         "type": "string",  "description": "OCR质量分级",         "enum": ["high","medium","low"]},
    {"key": "responsible_dept","type": "string",  "description": "责任/发文部门",        "enum": None},
    {"key": "doc_nature",      "type": "string",  "description": "文件性质",           "enum": ["法规","技术","管理","统计"]},
    {"key": "location",        "type": "string",  "description": "工程部位",            "enum": ["主坝","副坝一","副坝二","溢洪道","高洞","低洞","放水塔","库区","全库"]},
    {"key": "flood_magnitude", "type": "string",  "description": "洪水量级",            "enum": ["百年一遇","千年一遇","一般洪水","不适用"]},
]

# Top-level file assignments：已废弃（语料源切换后由 DIR_DATASET 按一级目录指派）

# Flood event by subdir (spec §6 阶段0, flood_event field derivation)
# 事件命名与 METADATA_SCHEMA.flood_event 枚举、tag_vocab.VOCAB_ROWS 三处对齐
# （test_flood_event_sources_are_mutually_consistent 锁定）；2019 场次按档案目录
# 标注 9-14 归为 2019-9，2021 年另有 09 子场记 2021-09。
#
# P1-5 纠偏（2026-09-03）：档案目录 "06-2008年洪水(8-22)" 系档案方命名错误——
# 目录内表格数据实为 2018年8月21日洪水（表内标题 20180821、Excel 序列日 43333=
# 2018-08-21、《较大洪水统计表》180821 行逐值互证；档案目录 2007→2010 年间并无
# 2008 场次）。映射值改 "2018-8"；枚举保留 "2008-8" 仅为历史值域完整，语料中已无
# 2008 场次。此为档案级错误，已另行整理反馈文案（docs/archive-feedback-2026-09-03.md）。
#
# F1 批次二（2026-09-04）枚举扩 "2010-8"/"2010-7"：08-历年洪水统计 下三份表格按内容
# 实为 2010 场次（2001年洪水过程线.xls 标题 20100724、2010年下泄水量统计.xls 标题
# 20100813、Excel 序列日 40402=2010-08-12 互证），但该目录本身混装 1997/2021/历年
# 多类文件，故不改目录映射（保持"历年统计"），场次值走 shell TARGETS 逐文件元数据
# （2001年洪水过程线.xls→2010-7、2010年下泄水量统计.xls→2010-8、724-829两场洪水.xls
# →2010-7 主值；其第二张摘录表 20030829 系内容层新发现，单值字段不扩 2003-8，
# 见 out/xls_fusion/REVIEW_WAVE3.md 裁定点）。
FLOOD_EVENT_BY_SUBDIR = {
    "02-2021年洪水调度":  "2021-10",
    "03-2020年洪水(8-16)": "2020-8",
    "04-2019年洪水(9-14)": "2019-9",
    "05-2013年洪水(7-22)": "2013-7",
    "06-2008年洪水(8-22)": "2018-8",
    "07-其他洪水事件":      "其他",
    "08-历年洪水统计":     "历年统计",
}

# QA table detection keywords (spec §6 阶段1)
QA_TABLE_KEYWORDS = [
    "降雨量统计", "水位库容曲线推求", "防洪调度效益统计", "洪水统计",
    "洪水过程", "受损统计", "弃水计算", "下泄水量统计",
]

# VLM vision sources（2026-08-26 扩展：全部参数/管理图表，rel 相对 CORPUS_ROOT）
VISION_SOURCES = [
    ("05-基础数据与曲线/01-水库基本信息/水库基本信息.jpg",   "水库基本信息"),
    ("05-基础数据与曲线/02-大坝剖面图.jpg",                   "大坝剖面图"),
    ("05-基础数据与曲线/03-溢洪道信息/溢洪道图1.jpg",         "溢洪道图1"),
    ("05-基础数据与曲线/03-溢洪道信息/溢洪道图2.jpg",         "溢洪道图2"),
    ("05-基础数据与曲线/05-库容水位对照表.jpg",               "库容水位对照表"),
    ("05-基础数据与曲线/06-泄流曲线.jpg",                     "泄流曲线"),
    ("05-基础数据与曲线/07-抢险物资信息/物资图1.jpg",         "物资图1"),
    ("05-基础数据与曲线/07-抢险物资信息/物资图2.jpg",         "物资图2"),
    ("07-管理资料/01-组织架构与责任人/三个责任人.jpg",        "三个责任人"),
    ("07-管理资料/01-组织架构与责任人/中心架构图.png",        "中心架构图"),
    ("07-管理资料/02-注册登记证/大坝注册登记证.png",          "大坝注册登记证"),
    ("07-管理资料/03-供配水计划/各部门用水需求.jpg",          "各部门用水需求"),
]

# Textualized known values for cross-check (spec §6 阶段1)
TEXTUALIZED_VALUES = {
    "百年一遇泄量": "1454 m³/s",
    "千年一遇泄量": "2218 m³/s",
    "汛限水位":     "788.5 m",
}

# Longest-first for multi-pattern extraction
LOCATION_KEYWORDS = sorted([
    "主坝", "副坝一", "副坝二", "溢洪道", "高洞", "低洞", "放水塔", "库区", "全库",
], key=len, reverse=True)

# 多模式提取依赖"长词优先"匹配顺序——短词在前会把'管理局'错配到
# '桃曲坡水库管理局'的前缀上（review P2）
DEPT_KEYWORDS = sorted([
    "防汛办", "管理局", "设计院", "水务局", "应急管理局", "水利局",
    "桃曲坡水库管理局", "陕西省桃曲坡灌区",
], key=len, reverse=True)

SKIP_DIRS = {
    "video_analysis", "extracted", "repaired",          # 派生流程遗留
    "09-图像与多媒体", "10-压缩包待处理",                # 现场影像/未整理压缩包，不入 KB
}

# Import columns (spec §4.1)
IMPORT_COLS = [
    "rel", "dataset_key", "doc_category", "sub_category",
    "flood_event", "doc_type", "year", "source_format", "quality",
    "responsible_dept", "doc_nature", "location", "flood_magnitude",
    "skip_reason", "duplicate_of", "sha256",
]
