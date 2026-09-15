# RAGFlow 上游 Issue 草稿：`image_context_size` / `table_context_size` 无法通过 API 设置

## 问题标题

`ParserConfig` API schema 缺少 `image_context_size` / `table_context_size` 字段，且数据集级别更新无 `ext` 提升逻辑

## 问题描述

`image_context_size` 和 `table_context_size` 是 RAGFlow 解析器的重要配置参数，用于给图片/表格 chunk 附加周边 OCR 文本上下文，让 VLM 描述更准确。这些参数在数据库默认值、解析代码中都存在，但**无法通过 API 正常设置**。

### 完整的问题链

1. **API schema 拒绝这两个字段**
   - `api/utils/validation_utils.py:450-477` 的 `ParserConfig` 白名单没有 `image_context_size` / `table_context_size`
   - 基类 `extra="forbid"`（line 352）直接拒绝未知字段
   - 尝试 PUT `/api/v1/datasets/{id}` 带 `parser_config.image_context_size` 返回：
     ```
     Field: <parser_config.image_context_size> - Message: <Extra inputs are not permitted>
     ```

2. **前端 `extractParserConfigExt` 把这两个字段放入 `ext`**
   - `web/src/hooks/parser-config-utils.ts:88-135` 的 `extractParserConfigExt` 不知道这两个字段
   - 它们被放入 `parserExt`，然后合并到 `ext` 字段中（line 134）
   - 提交的 payload 变成 `{ parser_config: { ext: { image_context_size: 256, ... } } }`

3. **数据集级别 API 没有 `ext` 提升逻辑**
   - 文档级别 API 有提升逻辑：`api/apps/restful_apis/document_api.py:292-293`
     ```python
     if update_doc_req.parser_config:
         req["parser_config"].update(update_doc_req.parser_config.ext)
     ```
   - 但**数据集级别 API（`dataset_api.py`）没有类似的逻辑**
   - 结果：通过 UI 在数据集级别设置 `image_context_size`，字段留在 `ext` 中，后端解析代码从顶层读取不到

4. **后端解析代码从顶层读取**
   - `deepdoc/parser/figure_parser.py:123`: `context_size = max(0, int(parser_config.get("image_context_size", 0) or 0))`
   - `rag/app/paper.py:239`: `image_ctx = max(0, int(parser_config.get("image_context_size", 0) or 0))`
   - `rag/app/naive.py:1033`: `image_context_size = max(0, int(parser_config.get("image_context_size", 0) or 0))`
   - `rag/app/picture.py:59`: `image_ctx = max(0, int(parser_config.get("image_context_size", 0) or 0))`
   - 都是从 `parser_config` 顶层读取，不是从 `ext` 中读取

### 数据库和解析代码确认有这两个字段

- `api/db/db_models.py:1280` (Knowledgebase): `parser_config = JSONField(default={..., "table_context_size": 0, "image_context_size": 0})`
- `api/db/db_models.py:1324` (Document): 同上
- `api/utils/api_utils.py:362-363`: 默认值中有 `"table_context_size": 0, "image_context_size": 0`

### 前端 UI 只在 naive 方法下暴露设置入口

- `web/src/components/chunk-method-dialog/index.tsx:388-390`: `ImageContextWindow` 只在 `DocumentParserType.Naive` 时显示
- 但 `image_context_size` 对 paper/manual/book/picture 方法也有效（解析代码都读取）
- UI 组件 `ImageContextWindow` 的 max=256（`common-item.tsx:268`）

## 影响

- 用户无法通过 API 或 UI 在数据集级别设置 `image_context_size` / `table_context_size`
- 只有文档级别 API 可以设置（利用 `ext` 提升逻辑），但需要手动构造 `ext` payload
- VLM 图片描述质量无法通过正常配置途径改善

## 复现步骤

1. 尝试通过 API 设置：
   ```bash
   curl -X PUT "https://ragflow/api/v1/datasets/{kb_id}" \
     -H "Authorization: Bearer {token}" \
     -d '{"parser_config": {"image_context_size": 256}}'
   # 返回: Extra inputs are not permitted
   ```

2. 尝试通过 UI 设置（在 naive 方法下设置 ImageContextWindow 滑块）：
   - 前端提交 `{ parser_config: { ext: { image_context_size: 256 } } }`
   - 数据集级别 API 不提升 `ext`，字段留在 `ext` 中
   - 后端解析代码从顶层读取，得到 0

## 修复建议（三选一）

### 方案 A：在 `ParserConfig` schema 中加入这两个字段（推荐）

```python
# api/utils/validation_utils.py:450
class ParserConfig(Base):
    ...
    image_context_size: Annotated[int, Field(default=0, ge=0, le=2048)]
    table_context_size: Annotated[int, Field(default=0, ge=0, le=2048)]
```

这样 API 直接接受顶层字段，前端 `extractParserConfigExt` 也会把它们当作已知字段保留在顶层。

### 方案 B：在数据集级别 API 添加 `ext` 提升逻辑

与 `document_api.py:292-293` 保持一致，在 `dataset_api.py` 的更新逻辑中添加：
```python
if update_kb_req.parser_config:
    req["parser_config"].update(update_kb_req.parser_config.ext)
```

### 方案 C：在 `extractParserConfigExt` 的已知字段列表中加入这两个字段

```typescript
// web/src/hooks/parser-config-utils.ts:88
const {
    ...
    image_context_size,  // 新增
    table_context_size,  // 新增
    ext,
    ...parserExt
} = parserConfig;
return {
    ...
    image_context_size,  // 保留在顶层
    table_context_size,  // 保留在顶层
    ext: { ...ext, ...parserExt },
};
```

## 环境

- RAGFlow 版本：从源码分析（2026年9月）
- 分析文件：
  - `api/utils/validation_utils.py:450-477` (ParserConfig schema)
  - `api/apps/restful_apis/document_api.py:292-293` (文档级别 ext 提升)
  - `web/src/hooks/parser-config-utils.ts:88-135` (前端 extractParserConfigExt)
  - `deepdoc/parser/figure_parser.py:123` (后端读取 image_context_size)
  - `api/db/db_models.py:1280,1324` (数据库默认值)

## 实际验证

通过文档级别 API 的 `ext` 提升逻辑，成功设置 `image_context_size=256` 并重新解析一张 paper 方法文档：
- VLM 描述从 "Engineering Plan" 改善为 "Technical drawing / Engineering plan layout with data tables"
- 新增识别出：溢流堰、导流墩、配电房、闸孔编号（左1孔-左3孔、右1孔-右3孔）、高程值（790.5, 788.5, 773.0, 745.0）
- 检索 sim 分数从 0.489 提升到 0.493

这证明 `image_context_size` 参数本身有效，只是配置途径有 bug。