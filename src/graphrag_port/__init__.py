"""graphrag_port - 从 RAGFlow 源码移植的经典 GraphRAG（light 模式）提取代码

依赖解耦说明（对照 rag/graphrag 原版）：
  - rag.graphrag.utils      -> local_utils（去 redis/ES，LLM 缓存改为本地 JSON 文件）
  - rag.llm.chat_model.Base -> llm_adapter.OpenAICompatLLM（OpenAI 兼容端点）
  - api.db...has_canceled   -> 恒 False（本地运行无任务取消）
  - common.token_utils      -> 本地 num_tokens_from_string / truncate（tiktoken 失败时启发式回退）
"""
