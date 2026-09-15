# RAGFlow 图 / 表题注（caption）链路修复工单

> 交付对象：部署 RAGFlow 实例那台机器上的 coding agent
> 编写日期：2026-09-15
> 目标实例：`https://labragf.openagp.top:9080`（前端）、Xinference `https://labxinf.openagp.top:9080`
> 参考源码：本地克隆 `e:\git\ragflow`
>
> ⚠️ **版本差异提醒**：本地克隆版本**旧于**线上实例（例如线上已有 `table_vision_enhance`、`rerank_candidates_count` 等本地没有或行号不同的内容）。
> 本文所有代码位置均给出**文件 + 函数名 + 代码特征**，请以线上实际代码为准按特征定位，**不要只按行号盲改**。

---

## 0. 问题背景

知识库中的标准类 PDF（表格、示意图很多）在问答时出现**"图 / 表数据缺失"**：

- 问「2.5次抛物线表有哪些数据？」，模型回答"表2 的具体数据未在知识库文本中列出，**仅提及见表2**"；
- 问「图10 的应急转移流程图是什么？」，模型回答"知识库中未直接提供图10的图像数据"。

排查结论：**不是数据没进库**，而是解析阶段"图/表本体"与"题注（图号/表号）"没有建立可靠关联，导致：

1. 媒体 chunk 里缺少图号/表号这类**可检索锚点**，精确查询命中不了；
2. 题注与表体高度雷同（表1/表2 的公式、正文逐字相同）时，检索排序分不开；
3. 图片 chunk 的可检索文本几乎完全依赖图注，图注一旦缺失，该块等于"有图无字"。

其中 **P0 是本次最直接的根因**，必须先改。

---

## 1. 修复清单

| # | 优先级 | 文件 | 问题一句话 | 影响 |
|---|---|---|---|---|
| P0 | **最高** | `deepdoc/parser/mineru_parser.py` | MinerU 路径把 `image_caption`/`image_footnote` 丢掉，VLM 描述不含图号 | 图片块检索不到（"图10"类查询必失败） |
| P1 | 高 | `rag/app/naive.py` | 调 `append_context2table_image4pdf` 时**参数传错位** | 只配 `table_context_size` 时上下文回填完全不生效 |
| P2 | 中 | `deepdoc/vision/table_structure_recognizer.py` | `is_caption()` 正则过严，判不出"表2"（无空格） | DeepDOC 路径题注识别不到 |
| P2 | 中 | `deepdoc/parser/pdf_parser.py` | `_extract_table_figure()` 题注配对无距离上限、无兜底 | 题注错挂到别的图/表，或被静默丢弃 |
| P2 | 低 | `api/utils/validation_utils.py` + `api/apps/restful_apis/dataset_api.py` | `image_context_size`/`table_context_size` 等字段无法通过 API 下发 | 用户配置不生效（详见 `docs/ragflow-issue-image-context-size.md`） |

> 说明：P0 已有现成补丁文件 `docs/code/patches/mineru-vlm-figure-caption.patch`（49 行，可 `git apply`）。
> 若线上代码与本地差异较大，请按下面 P0 的 before/after 手工核对后再改。

---

## 2. P0：MinerU 路径 VLM 描述丢失图注

### 2.1 现象

用 MinerU 解析的文档，图片 chunk 的 `content_with_weight` 只包含（被截断的）图注残留，例如：

```
。人员应急转移是指遭遇特大暴雨洪水……某水库溃坝洪水淹没区域人员和财产转移命令下达和实施的流程图如图10所示。图10
```

块里没有图10 的图形内容描述，也没有"图10 xxx"的完整题注 → 检索「图10」时该块无法被有效命中。

### 2.2 根因

`deepdoc/parser/mineru_parser.py::_enhance_images_with_vlm` 只把**图片本身 + 通用 prompt** 交给 VLM：

```python
# 修改前
prompt = vision_llm_figure_describe_prompt(language=language or "English")

def worker(idx, item):
    try:
        with Image.open(item["img_path"]) as img:
            img.load()
            desc = vision_llm_chunk(binary=img, vision_model=vision_model, prompt=prompt)
        return idx, (desc or "").strip()
    except Exception as e:
        logging.warning(f"[MinerU] VLM description failed for image #{idx}: {e}")
        return idx, ""
```

VLM 是"看图说话"，**天然不会知道这张图是"图10"**，因此描述里没有图号、也无法利用图注做消歧。

对照 DeepDOC 路径的 `deepdoc/parser/figure_parser.py`（约 174-190 行）——同样是 VLM 描述，但它会把题注拼进上下文：

```python
context_above = ck.get("context_above", "")
context_below = ck.get("context_below", "")
if context_above or context_below:
    prompt = vision_llm_figure_describe_prompt_with_context(
        context_above=ck.get("context_above") + ck.get("text", ""),   # ← 题注也在这里
        context_below=ck.get("context_below"),
        language=lang,
    )
else:
    prompt = vision_llm_figure_describe_prompt(language=lang)
```

`rag/prompts/vision_llm_figure_describe_prompt_with_context.md` 里本来就是为它准备的槽位：

```markdown
## CONTEXT (ABOVE)

{{ context_above }}

## CONTEXT (BELOW)

{{ context_below }}
```

### 2.3 修改代码（最终版）

文件：`deepdoc/parser/mineru_parser.py`
函数：`_enhance_images_with_vlm`

**修改前**

```python
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from rag.app.picture import vision_llm_chunk
        from rag.prompts.generator import vision_llm_figure_describe_prompt

        image_jobs = [(idx, item) for idx, item in enumerate(outputs) if item.get("type") == MinerUContentType.IMAGE and item.get("img_path") and os.path.exists(item["img_path"])]
        if not image_jobs:
            return

        if callback:
            callback(0.78, f"[MinerU] Generating VLM descriptions for {len(image_jobs)} images...")

        prompt = vision_llm_figure_describe_prompt(language=language or "English")

        def worker(idx, item):
            try:
                with Image.open(item["img_path"]) as img:
                    img.load()
                    desc = vision_llm_chunk(binary=img, vision_model=vision_model, prompt=prompt)
                return idx, (desc or "").strip()
            except Exception as e:
                logging.warning(f"[MinerU] VLM description failed for image #{idx}: {e}")
                return idx, ""
```

**修改后**

```python
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from rag.app.picture import vision_llm_chunk
        from rag.prompts.generator import (
            vision_llm_figure_describe_prompt,
            vision_llm_figure_describe_prompt_with_context,
        )

        image_jobs = [(idx, item) for idx, item in enumerate(outputs) if item.get("type") == MinerUContentType.IMAGE and item.get("img_path") and os.path.exists(item["img_path"])]
        if not image_jobs:
            return

        if callback:
            callback(0.78, f"[MinerU] Generating VLM descriptions for {len(image_jobs)} images...")

        default_prompt = vision_llm_figure_describe_prompt(language=language or "English")

        def figure_caption(item):
            """MinerU 给出的图注 / 脚注，作为 VLM 的上下文（通常含图号与主题）。"""
            parts = [*(item.get("image_caption") or []), *(item.get("image_footnote") or [])]
            return " ".join(p.strip() for p in parts if isinstance(p, str) and p.strip()).strip()

        def worker(idx, item):
            try:
                caption = figure_caption(item)
                if caption:
                    # 对齐 deepdoc 的 VisionFigureParser（deepdoc/parser/figure_parser.py:180-186）：
                    # 把图注作为 CONTEXT (ABOVE) 交给 VLM，使其描述能带上图号（如「图10」），
                    # 否则纯看图描述天然不含编号，检索「图10」时定位不到该块。
                    prompt = vision_llm_figure_describe_prompt_with_context(
                        context_above=caption,
                        context_below="",
                        language=language or "English",
                    )
                else:
                    prompt = default_prompt
                logging.debug(
                    "[MinerU] image #%s VLM prompt=%s caption_len=%s",
                    idx, "with_context" if caption else "default", len(caption),
                )
                with Image.open(item["img_path"]) as img:
                    img.load()
                    desc = vision_llm_chunk(binary=img, vision_model=vision_model, prompt=prompt)
                return idx, (desc or "").strip()
            except Exception as e:
                logging.warning(f"[MinerU] VLM description failed for image #{idx}: {e}")
                return idx, ""
```

**要点**
- 复用现成模板与现成函数，**无新增依赖、无新增 import 之外的改动**；
- 有图注时走 `..._with_context`，否则回落到原 `default_prompt`，**不影响无图注的图片**；
- 新增一条 `logging.debug` 便于排查"到底走了哪种 prompt"。

### 2.4 验证方法

1. 改完重启解析服务，重新解析一份含图的文档；
2. 拉该文档的 image chunk，检查 `content_with_weight` 是否包含图号字样（如"图10"），
   并且图注内容（如流程图方框文字）是否被读出来；
3. 用带图号的查询做检索，确认该块能被召回。

### 2.5 补丁文件

已生成：`docs/code/patches/mineru-vlm-figure-caption.patch`（基于本地 `e:\git\ragflow`，49 行）

```bash
# 在 RAGFlow 源码根目录
git apply --check docs/code/patches/mineru-vlm-figure-caption.patch   # 先试跑
git apply docs/code/patches/mineru-vlm-figure-caption.patch
python -m py_compile deepdoc/parser/mineru_parser.py                  # 语法自检
```

---

## 3. P1：媒体块上下文回填参数错位

### 3.1 现象

即使把 `table_context_size` / `image_context_size` 配好，媒体 chunk 也拿不到周边正文（题注/上下文），导致检索锚点更弱。

### 3.2 根因

文件：`rag/app/naive.py`，PDF 分支（`re.search(r"\.pdf$", filename, re.IGNORECASE)` 内，`tokenize_table` 调用之前）

```python
        if table_context_size or image_context_size:
            tables = append_context2table_image4pdf(
                sections,
                tables,
                image_context_size,          # ← 这里传的是 image_context_size
                section_page_offset=from_page if name == "mineru" else 0,
            )
```

而函数签名是（`rag/nlp/__init__.py`）：

```python
def append_context2table_image4pdf(sections: list, tabls: list, table_context_size=0, return_context=False, section_page_offset: int = 0):
    if table_context_size <= 0:
        return [] if return_context else tabls
```

后果：
- 只设置 `table_context_size`、没设 `image_context_size` 时 → 函数收到 `0` → **直接原样返回，上下文一条都不加**；
- 只设置 `image_context_size` 时"看起来能用"，但语义错位：表格与图片被同一个数字控制，且覆盖条件与传参不是同一个变量。

> 补充说明（避免误判）：`append_context2table_image4pdf` 内部**已经支持图片**（通过 `isinstance(tb, list)` 区分图片/表格并还原），并非"只处理表格"。真正的问题只是上面这一处**传参错位**。

### 3.3 修改代码

**修改前**

```python
        if table_context_size or image_context_size:
            tables = append_context2table_image4pdf(
                sections,
                tables,
                image_context_size,
                section_page_offset=from_page if name == "mineru" else 0,
            )
```

**修改后**

```python
        if table_context_size or image_context_size:
            # 该函数对表格与图片使用同一个上下文长度参数，取两者较大值，
            # 避免「只配了 table_context_size 却传了 image_context_size(0)」导致回填整体失效。
            media_context_size = max(table_context_size, image_context_size)
            tables = append_context2table_image4pdf(
                sections,
                tables,
                media_context_size,
                section_page_offset=from_page if name == "mineru" else 0,
            )
```

### 3.4 附带确认项（不建议改，需评估）

同文件 `naive.py` 末尾：

```python
    # if table_context_size or image_context_size:
    #    attach_media_context(res, table_context_size, image_context_size)
```

`attach_media_context`（`rag/nlp/__init__.py`）是另一个"按位置给 image/table chunk 补前后句"的兜底函数，当前被注释。
**注意**：它与 `append_context2table_image4pdf` 功能重叠，PDF 分支已走后者，开启它会造成上下文**重复拼接**。
**本工单不要求改动此项**，如需启用请先做 A/B 对比。

---

## 4. P2：DeepDOC 路径的题注识别与配对

> 仅影响 `layout_recognize=DeepDOC` 的文档；MinerU 路径不经过这里。若实例主要用 MinerU，可**延后处理**。

### 4.1 `is_caption()` 正则过严

文件：`deepdoc/vision/table_structure_recognizer.py`

```python
    @staticmethod
    def is_caption(bx):
        patt = [
            r"[图表]+[ 0-9:：]{2,}",
            r"(?i)Fig\.?\s*\d+",
            r"(?i)Figure\s+\d+",
            r"(?i)Table\s+\d+",
        ]
        if any([re.match(p, bx["text"].strip()) for p in patt]) or bx.get("layout_type", "").find("caption") >= 0:
            return True
        return False
```

问题：
- 用 `re.match`（从头匹配）+ `[ 0-9:：]{2,}` 要求"图/表"后**至少 2 个**空格/数字/冒号字符 → **"表2"（数字紧贴、无空格）判不出来**，只有"表 2"才行；
- "表D.0.15"（中间夹字母）也判不出来。

**修改前**

```python
        patt = [
            r"[图表]+[ 0-9:：]{2,}",
            r"(?i)Fig\.?\s*\d+",
            r"(?i)Figure\s+\d+",
            r"(?i)Table\s+\d+",
        ]
        if any([re.match(p, bx["text"].strip()) for p in patt]) or bx.get("layout_type", "").find("caption") >= 0:
            return True
        return False
```

**修改后**

```python
        text = bx["text"].strip()
        patt = [
            # 允许「表2」「表 2」「表2.5」「表D.0.15」「图10」「图A.1」等写法
            r"[图表]\s*[A-Za-z]?\s*[0-9]",
            r"(?i)Fig\.?\s*\d+",
            r"(?i)Figure\s+\d+",
            r"(?i)Table\s+\d+",
        ]
        if any([re.search(p, text) for p in patt]) or bx.get("layout_type", "").find("caption") >= 0:
            return True
        return False
```

要点：`re.match` → `re.search`；模式改为 `[图表]\s*[A-Za-z]?\s*[0-9]`。

> ⚠️ 此改动会**扩大**题注识别范围，可能让更多正文行被判为题注。建议改完对一份样例 PDF 做一次回归（对比 boxes 里 caption 的数量与内容），确认没有把普通段落误判。

### 4.2 `_extract_table_figure()` 题注配对缺陷

文件：`deepdoc/parser/pdf_parser.py`

```python
            tk, tv = nearest(tables)
            fk, fv = nearest(figures)
            # if min(tv, fv) > 2000:
            #    i += 1
            #    continue
            if tv < fv and tk:
                tables[tk].insert(0, c)
            elif fk:
                figures[fk].insert(0, c)
            self.boxes.pop(i)
```

三个问题：

1. **距离阈值被注释掉** → 相隔很远、甚至跨栏跨页的题注也会被吸附到某个表/图上；
2. **只比较"谁更近"**（`tv < fv`），不校验是否同栏 / 同 `layoutno` / 是否在表的上方或下方 → 表图相邻的版面极易**错挂**；
3. **`self.boxes.pop(i)` 无条件执行** → 当 `tk` 与 `fk` 都为空（附近没有识别出的表/图），或 `tv >= fv` 但 `fk` 为空时，题注被**静默丢弃**：既不在表格里，也不在正文里。

**修改后（建议）**

```python
            tk, tv = nearest(tables)
            fk, fv = nearest(figures)
            # 1) 恢复距离上限：超过阈值说明题注并不属于任何媒体，应当留在正文里
            #    阈值用版面平均行高做尺度，避免不同 DPI 下失效（原硬编码 2000 与 DPI 强耦合）
            page_idx = c["page_number"] - 1
            mh = self.mean_height[page_idx] if 0 <= page_idx < len(self.mean_height) else 10
            max_dis = (mh * 30) ** 2
            if min(tv, fv) > max_dis:
                i += 1          # 保留在正文，不 pop
                continue
            if tv < fv and tk:
                tables[tk].insert(0, c)
                self.boxes.pop(i)
            elif fk:
                figures[fk].insert(0, c)
                self.boxes.pop(i)
            else:
                # 2) 无归属：保留为正文，绝不静默丢弃
                i += 1
```

要点：
- 恢复距离上限（用 `mean_height` 归一化）；
- **只有在真正挂载成功时才 `pop`**，抽不到归属就留在正文（宁缺勿错）。

> ⚠️ 该函数同时被 `__call__` 与 `_parse_loaded_window_into_bboxes` 调用，改动会影响所有 DeepDOC 解析结果，**必须重新解析后做回归对比**。

---

## 5. P2：`parser_config` 字段无法通过 API 下发

### 5.1 现象

```bash
PUT /api/v1/datasets/{id}
{"parser_config": {"image_context_size": 256}}
# → Field: <parser_config.image_context_size> - Message: <Extra inputs are not permitted>
```

### 5.2 根因（两处）

1. `api/utils/validation_utils.py::ParserConfig`（本地约 450-477 行）白名单里**没有** `image_context_size` / `table_context_size` / `table_vision_enhance` / `mineru_*` 等字段，基类 `extra="forbid"` 直接拒绝；
2. 数据集级更新接口缺少**文档级**那种 `ext` 提升逻辑：

```python
# api/apps/restful_apis/document_api.py（文档级，有提升）
if update_doc_req.parser_config:
    req["parser_config"].update(update_doc_req.parser_config.ext)
```

而 `api/apps/restful_apis/dataset_api.py::update()` 走的是 `dataset_api_service.update_dataset(...)`，没有等价的 ext 提升。

### 5.3 修改建议

**方案 A（推荐）**：在 `ParserConfig` 白名单中补齐字段，让 API 直接接受顶层写法：

```python
class ParserConfig(Base):
    ...
    image_context_size: Annotated[int, Field(default=0, ge=0, le=2048)]
    table_context_size: Annotated[int, Field(default=0, ge=0, le=2048)]
    table_vision_enhance: Annotated[bool, Field(default=False)]
    mineru_lang: Annotated[str | None, Field(default=None)]
    mineru_parse_method: Annotated[str | None, Field(default=None)]
    mineru_table_enable: Annotated[bool | None, Field(default=None)]
    mineru_formula_enable: Annotated[bool | None, Field(default=None)]
```

**方案 B（兜底）**：在数据集级更新逻辑里补上与文档级一致的 ext 提升：

```python
if req.get("parser_config") and isinstance(req["parser_config"].get("ext"), dict):
    req["parser_config"].update(req["parser_config"].pop("ext"))
```

> 现状说明：目前**可行的工作区**是"文档级 PATCH + `parser_config.ext`"（服务端会把 ext 提升到顶层）。
> 本机实操已验证该路径可用，参见 `docs/ragflow-issue-image-context-size.md`。

---

## 6. 部署步骤

```bash
# 1) 备份
git -C <ragflow-root> rev-parse HEAD            # 记录当前 commit
git -C <ragflow-root> stash                     # 若有本地改动

# 2) 应用 P0 补丁（若与线上差异大，请按 §2.3 手工改）
git apply docs/code/patches/mineru-vlm-figure-caption.patch

# 3) 按需应用 P1 / P2（§3、§4、§5）

# 4) 语法自检
python -m py_compile deepdoc/parser/mineru_parser.py
python -m py_compile rag/app/naive.py
python -m py_compile deepdoc/vision/table_structure_recognizer.py
python -m py_compile deepdoc/parser/pdf_parser.py

# 5) 重新构建 / 重启解析相关容器（task_executor 所在服务必须重启，它加载解析代码）
docker compose build ragflow
docker compose up -d
```

> **补丁只对"之后重新解析"的文档生效**，存量 chunk 不会被自动重写。
> 要让效果落到已有文档上，需对目标文档执行重新解析（例：SL_T 720-2026，约 7 分钟）。

---

## 7. 验证清单

改完后按下表逐项确认（建议对同一份含图/含表的标准类 PDF）：

| # | 检查项 | 通过标准 |
|---|---|---|
| 1 | 解析日志 | 出现 `[MinerU] image #N VLM prompt=with_context caption_len=...` |
| 2 | image chunk 内容 | `content_with_weight` 含图号（如"图10"），且含图注文字 / 图形要素 |
| 3 | table chunk 内容 | 含"表 X"编号 + 表体；若走 DeepDOC，题注出现在块首或 `<caption>` 内 |
| 4 | 检索（带图号/表号查询） | 对应媒体块出现在 top-N |
| 5 | 端到端问答 | 「2.5次抛物线表有哪些数据？」能答出 `1.00 / 0.62 / 0.45 / 0.36 / 0.29 / 0.23 / 0.15`；「图10 流程图」能给出方框内容 |
| 6 | 回归 | 纯文本类问题（如"溃坝过程"）答案不劣化 |

---

## 8. 风险与回滚

| 修改 | 风险 | 回滚 |
|---|---|---|
| P0 VLM 图注 | 极低。只在有 `image_caption` 时换 prompt；最坏情况描述变差，不影响正确性 | 还原函数或 `git revert` |
| P1 参数错位 | 极低。仅修正取值 | 还原一行 |
| P2 `is_caption` 放宽 | **中**。可能把正文行误判为题注 | 必须做样例回归；异常则回滚 |
| P2 题注配对 | **中高**。影响所有 DeepDOC 解析结果 | 必须整文重新解析后回归；建议先在测试库验证 |
| P2 ParserConfig | 低。新增可选字段 | 还原 schema |

**统一回滚**：

```bash
git -C <ragflow-root> checkout -- <改动的文件>
docker compose build ragflow && docker compose up -d
```

---

## 9. 方案 A：文档迁移 / 重解析流水线（如何让补丁生效）

> **两个前提认知**（最容易踩坑的地方）：
> 1. 解析类补丁**只对"之后重新解析"的文档生效**，存量 chunk 不会自动重写；
> 2. **重解析会清空 chunk 上的 `important_keywords`**，使"表1/表2 排序打平"类问题立刻回归。
>    （实测：重解析后 表1 0.3499 / 表2 0.3482；补完关键词后 表2 0.3544 反超 表1 0.350）
>
> 所以「重解析 → 补关键词」必须**成对执行**。

### 9.1 一键流水线

脚本：`src/reparse_and_inject.py`（关键词抽取规则复用 `src/inject_media_keywords.py`，**两者须同目录**）

```bash
# 单文档：重解析 + 补关键词（推荐）
python src/reparse_and_inject.py \
  --base-url https://<ragflow-host>:9080/api/v1 \
  --api-key <API_KEY> \
  --dataset-id <DS> --document-id <DOC>

# 整库：逐个串行重解析 + 补关键词（耗时较长，慎用）
python src/reparse_and_inject.py ... --dataset-id <DS> --all-documents

# 文档已重解析过，只补关键词
python src/reparse_and_inject.py ... --dataset-id <DS> --all-documents --skip-parse

# 只重解析，关键词稍后补
python src/reparse_and_inject.py ... --dataset-id <DS> --document-id <DOC> --skip-inject
```

脚本行为：
1. 触发 `POST /datasets/{ds}/documents/parse`；
2. 轮询 `GET /datasets/{ds}/documents?id=...` 直到 `run=DONE/FAIL`（默认上限 1800s，容忍解析期间的传输抖动，若文档已在 RUNNING 则先等待）；
3. 拉取全部 chunk，按题注规则抽取"表X / 图Y"写入 `important_keywords`（**幂等**，已存在则跳过）。

可用 `--replace` 切换为覆盖模式（清掉历史脏关键词）。

### 9.2 迁移到新租户的推荐流程（不动老租户）

以「把老租户某文档迁到新租户独立 dataset 验证」为例：

```bash
# 1) 从老租户下载原文件
GET  /api/v1/datasets/{OLD_DS}/documents/{OLD_DOC}   -H "Authorization: Bearer <OLD_KEY>"  -o doc.pdf

# 2) 在新租户建独立 dataset（只带白名单字段）
POST /api/v1/datasets        -H "Authorization: Bearer <NEW_KEY>"
     {"name":"<name>","chunk_method":"naive"}
     → 记下 dataset_id

# 3) 上传
POST /api/v1/datasets/{NEW_DS}/documents             (multipart, file=doc.pdf)
     → 记下 document_id

# 4) 写解析配置（顶层字段会被 schema 拒绝，必须走文档级 ext 提升，详见 §5）
PATCH /api/v1/datasets/{NEW_DS}/documents/{NEW_DOC}
     {"chunk_method":"naive",
      "parser_config":{"chunk_token_num":512,"layout_recognize":"MinerU",
                       "ext":{"mineru_lang":"Chinese",
                              "mineru_table_enable":true,"mineru_formula_enable":true,
                              "mineru_parse_method":"auto",
                              "table_vision_enhance":true,
                              "table_context_size":256,"image_context_size":256}}}

# 5) 重解析 + 补关键词（一条命令）
python src/reparse_and_inject.py --base-url ... --api-key <NEW_KEY> \
    --dataset-id <NEW_DS> --document-id <NEW_DOC>
```

### 9.3 ⚠️ 迁移前的两个硬前提

| 前提 | 说明 | 检查方法 |
|---|---|---|
| **租户必须配 Vision 模型** | 否则 MinerU 的 VLM 图片描述会被**静默跳过**，图号与图形内容永远补不出来——这是"图10 查不到"的另一大根因，**光打补丁无效** | `GET /api/v1/models`，确认存在 `model_type` 含 `vision` 的模型 |
| 解析配置必须写在**文档级** | 数据集级 `POST/PUT /datasets` 会因 `ParserConfig` 白名单拒收这些字段 | 用 `PATCH /datasets/{ds}/documents/{doc}` + `parser_config.ext` |

> 实测对照（同一份 SL_T 720-2026）：
> - 老租户模型列表只有 ocr / chat / embedding / rerank，**无 vision** → 图片块仅 280 字符（只有被截断的图注"，如图10所示。图10"）；
> - 新租户有 `Qwen3.8-27B-Q4_K_M.gguf`、`MiniMax-M3`（均含 vision）→ 同一文档图片块 570~640 字符，含完整图号、图注与图形描述。

### 9.4 验证清单（重解析后逐项确认）

| # | 检查项 | 通过标准（本次实测值） |
|---|---|---|
| 1 | 解析日志 | 出现 `[MinerU] image #N VLM prompt=with_context caption_len=...` |
| 2 | 图片块内容 | 含图号 + 图注 + 图形要素（如"图10 …"后面跟着各方框文字） |
| 3 | 表格块内容 | 含"表 X"编号 + 表体 + 题注 |
| 4 | 带编号检索 | 目标媒体块排第 1（实测 图10 **0.5026**；表2 **0.3544**，均高于第二名） |
| 5 | 端到端问答 | 表2 答出 `1.00/0.62/0.45/0.36/0.29/0.23/0.15`；图10 逐框列出 7 个方框 |
| 6 | 回归 | 纯文本问题（如"溃坝过程"）答案不劣化 |

### 9.5 实测数据（2026-09-15 · 新租户隔离复现）

| 项 | 重解析前 | 重解析后（P0 + P1 生效） |
|---|---|---|
| chunks | 131（image 11 / table 15 / text 105） | 131（image 11 / table 15 / text 105） |
| 图10 块描述 | 笼统（"seven rectangular boxes…"） | **完整列出 7 个方框** |
| 图10 检索 | — | **0.5026 排第 1** |
| 图10 问答 | ❌ "知识库中未直接提供图10的图像数据" | ✅ **完整答出七个方框 + 流程逻辑** |
| 表2 检索 | 表1 0.3499 / 表2 0.3482（打平） | **表2 0.3544 第 1** / 表1 0.350 |
| 表2 问答 | — | ✅ 完整数值 |
| 关键词覆盖 | 23 / 131 | 23 / 131（重解析后由脚本自动补齐） |

---

## 10. 附：本工单**不涉及**的两个问题（避免改错方向）

1. **`top_k` 过大导致 rerank CUDA OOM（全站 500）**：属于**配置问题**，不是代码 bug。
   已在实例侧把 assistant 的 `top_k` 由 1024 调为 16（`PUT /api/v1/chats/{id}`），对话恢复正常。
   若后续仍出现 `500 ... /v1/rerank`，请优先检查 Xinference 上 `bge-reranker-v2-m3` 实例是否 OOM 掉线，而不是改代码。
   若要根治，方向是给 rerank 模型独立 GPU / 改用 CPU / 压低保 chat 模型的显存占用。

2. **`important_keywords` 注入表号/图号**：属于**数据后处理**，已有脚本
   `src/inject_media_keywords.py`（支持 `--apply`、`--replace`），不需要改 RAGFlow 程序。
   如需长期固化，可在 `rag/svr/task_executor_refactor/chunk_post_processor.py` 的关键词阶段
   （`doc_keyword_extraction`）中并入规则关键词，但这属于增强项，非本工单必需。
