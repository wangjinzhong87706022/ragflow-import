# 前端聊天流式响应中引用（reference）丢失 Bug 分析

- **日期**：2026-09-09
- **Bug 现象**：在 RAGFlow 前端 UI 中提问"你知道溢洪道的平面布置图吗？"时，助手回答中**没有引用文档和图片**；但通过 Playwright 测试（API 非流式调用）提问同一个问题时，回答**包含完整的引用和图片**
- **影响范围**：所有通过前端 UI 发起的流式 chat 请求，引用和图片均无法显示
- **根因**：前端流式合并逻辑 `pendingChunk = { ...pendingChunk, ...chunk }` 中，每个新 chunk 的 `reference: {}` 覆盖了最终 chunk 中的 reference

---

## 一、现象对比

### 1.1 Playwright 测试（有引用）

Playwright 测试脚本 `test_retrieval.py` 使用 API 非流式调用：

```python
# POST /api/v1/chats/{chat_id}/completions
json={"question": "你知道溢洪道的平面布置图吗？", "stream": False}
```

**返回结果**：
- 答案长度：1097 字符
- `reference.chunks`：8 个 chunk（含 `image_id`，可渲染图片）
- `reference.doc_aggs`：列表形式返回
- 答案中嵌入 `[ID:0]` 引用标记

### 1.2 前端 UI（无引用）

前端使用流式调用：

```typescript
// POST /api/v1/chat/completions
// stream: true, 前端 SSE 解析
```

**返回结果**：
- 答案正常生成（69 个 chunk 流式返回）
- **所有 chunk 的 `reference` 字段均为 `{}` 空对象**（前 68 个）
- 第 69 个 chunk（`final: true`）有完整 reference，但被覆盖

---

## 二、根因分析

### 2.1 后端流式返回逻辑

`api/db/services/dialog_service.py:987-1006`：

```python
if stream:
    # 流式返回：每个 chunk 的 reference 都是空对象
    for kind, value, state in _stream_with_think_delta(stream_iter):
        yield {"answer": value, "reference": {}, "audio_binary": ..., "final": False}
    
    # 最终 chunk 才包含完整 reference
    full_answer = last_state.full_text if last_state else ""
    if full_answer:
        final = await decorate_answer(answer)  # 这里生成 reference
        final["final"] = True
        yield final  # {"answer": "", "reference": {"chunks": [...], "doc_aggs": [...]}, "final": True}
```

**关键点**：reference 只在 `final=True` 的最后一个 chunk 中返回，前序 chunk 的 reference 都是 `{}`。

### 2.2 前端合并逻辑 Bug

`web/src/pages/next-chats/chat-stream/run-stream.ts:94`：

```typescript
for await (const chunk of parseCompletionEventStream(response)) {
  accumulatedAnswer = mergeAnswerChunk(accumulatedAnswer, chunk);
  pendingChunk = pendingChunk ? { ...pendingChunk, ...chunk } : chunk;
  //                                    ^^^^^^^^^^^^^^^^^^^^^^^
  //                                    Bug：对象覆盖，不是合并
  
  if (chunk.final || Date.now() - lastFlushAt >= AnswerFlushIntervalMs) {
    flushAnswer();  // 把 pendingChunk 写入 store
  }
}
```

**问题**：`{ ...pendingChunk, ...chunk }` 是浅覆盖，每个新 chunk 的 `reference: {}` 会覆盖最终 chunk 中的 `reference: {chunks: [...]}`。

### 2.3 验证数据

**流式调用实测**（`analyze_stream_reference.py`）：

| Chunk | answer_len | reference.chunks | reference.doc_aggs | 说明 |
|---|---|---|---|---|
| 1-68 | 10-26 | `[]` | `[]` | 流式中间帧，reference 为空 |
| 69 (final) | 0 | **8** | **3** | 最终帧，reference 完整 |

**前端合并结果**：
- 最终 `pendingChunk.reference` 被第 68 个 chunk 的 `{}` 覆盖
- `buildAssistantMessageFromAnswer()` 中的 `reference: answer.reference` 得到 `{}`
- 消息渲染时 `reference.doc_aggs` 为空，不显示引用文档

---

## 三、解决方案

### 3.1 方案 A：前端深合并（推荐）

修改 `run-stream.ts:94`，对 reference 字段做深合并：

```typescript
// 修复前
pendingChunk = pendingChunk ? { ...pendingChunk, ...chunk } : chunk;

// 修复后
pendingChunk = pendingChunk
  ? {
      ...pendingChunk,
      ...chunk,
      reference: {
        chunks: chunk.reference?.chunks ?? pendingChunk.reference?.chunks ?? [],
        doc_aggs: chunk.reference?.doc_aggs ?? pendingChunk.reference?.doc_aggs ?? [],
      },
    }
  : chunk;
```

### 3.2 方案 B：后端每个 chunk 都带 reference

修改 `dialog_service.py`，让每个 chunk 都携带完整的 reference：

```python
# 修复前
yield {"answer": value, "reference": {}, "audio_binary": ..., "final": False}

# 修复后
yield {"answer": value, "reference": kbinfos, "audio_binary": ..., "final": False}
```

**缺点**：增加网络传输量，每个 chunk 都携带完整 reference（可能较大）。

### 3.3 方案 C：只在 final chunk 时刷新

修改 `run-stream.ts`，只在 `chunk.final` 时才刷新 reference：

```typescript
for await (const chunk of parseCompletionEventStream(response)) {
  accumulatedAnswer = mergeAnswerChunk(accumulatedAnswer, chunk);
  
  if (chunk.final) {
    // final chunk 才合并 reference
    pendingChunk = pendingChunk ? { ...pendingChunk, ...chunk } : chunk;
  } else {
    // 非 final chunk 只合并 answer
    pendingChunk = pendingChunk 
      ? { ...pendingChunk, answer: accumulatedAnswer }
      : { answer: accumulatedAnswer };
  }
  
  if (chunk.final || Date.now() - lastFlushAt >= AnswerFlushIntervalMs) {
    flushAnswer();
  }
}
```

---

## 四、临时绕过方案

### 4.1 使用非流式调用

在需要引用的场景，使用 `stream=False` 调用：

```python
requests.post(
    f"{BASE}/api/v1/chat/completions",
    headers=h,
    json={"chat_id": chat_id, "question": question, "stream": False},
    timeout=300,
)
```

### 4.2 前端临时修复

在浏览器控制台手动检查 SSE 响应：
1. 打开 DevTools → Network
2. 过滤 `fetch` 或 `xhr`，找到 `chat/completions` 请求
3. 查看最后一个 `data:` 帧，其中包含完整 reference

---

## 五、相关代码位置

| 文件 | 行号 | 说明 |
|---|---|---|
| `api/db/services/dialog_service.py` | 987-1006 | 后端流式返回逻辑，reference 只在 final chunk 返回 |
| `web/src/pages/next-chats/chat-stream/run-stream.ts` | 94 | **Bug 位置**：浅覆盖合并 |
| `web/src/pages/next-chats/chat-stream/utils.ts` | 42-52 | `buildAssistantMessageFromAnswer()` 把 reference 写入消息 |
| `web/src/components/next-message-item/index.tsx` | 113-117 | 前端渲染引用文档列表 |
| `web/src/components/next-message-item/reference-image-list.tsx` | 124-161 | 前端渲染引用图片列表 |
| `web/src/services/chat-completion-stream.ts` | 41-84 | 前端流式请求构造 |

---

## 六、验证脚本

已创建验证脚本 `tools/analyze_stream_reference.py`，可复现此 bug：

```bash
python tools/analyze_stream_reference.py
```

输出示例：
```
[chunk 1-68] reference=空
[chunk 69 final] reference.chunks=8, doc_aggs=3
总 chunk 数: 69
有 reference 的 chunk: [69]
```

---

## 七、结论

这是一个**前端流式合并逻辑 bug**，与后端配置、问题表述无关。Playwright 测试因 `stream=False` 不受影响，前端 UI 因 `stream=True` 触发此 bug。

**修复优先级**：高（影响所有前端 chat 的引用和图片显示）