from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from app.config import settings
from app.core.vector_store import VectorStore


PROMPT_TEMPLATE = """你是一个智能问答助手。以下是从不同文档中检索到的相关内容，每段都标注了来源文件名。

{context}

{history}

用户问题：{question}

要求：
1. 只引用与问题相关的文档内容，忽略无关文档
2. 引用时请注明来源文件名
3. 如果没有任何文档与问题相关，直接用你的知识正常回答
4. 如果部分信息在文档中、部分不在，请分开说明"""

REWRITE_SYSTEM_PROMPT = """你是一个查询改写助手。根据对话历史，将用户的最新问题改写为独立、自包含的查询，使得不需要看历史也能理解查询意图。

只返回改写后的查询，不要任何额外内容。如果问题本身已经很清晰，直接返回原问题。"""


class RAGEngine:
    """RAG 引擎：向量检索 → 构建 Prompt → LLM 生成"""

    def __init__(self, vector_store: VectorStore):
        self.vector_store = vector_store
        self.llm = ChatOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model_name,
            temperature=0.1,
            max_tokens=2048,
        )
        self.prompt = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)

    # ChromaDB 余弦距离阈值：高于此值视为不相关，排除
    SCORE_THRESHOLD = 0.85
    MAX_HISTORY_TURNS = 5

    def _format_history(self, history: list | None) -> str:
        """将历史问答格式化为可读文本（兼容 dict 和 HistoryItem）"""
        if not history:
            return ""
        lines = ["<历史对话>"]
        for h in history:
            if isinstance(h, dict):
                q = h.get("question", "")
                a = h.get("answer", "")
            else:
                q = getattr(h, "question", "")
                a = getattr(h, "answer", "")
            lines.append(f"  用户：{q}")
            lines.append(f"  助手：{a}")
            lines.append("")
        lines.append("</历史对话>")
        return "\n".join(lines)

    def _rewrite_query(self, question: str, history: list[dict] | None) -> str:
        """如果问题模糊且有历史，改写为自包含查询"""
        if not history:
            return question

        history_text = self._format_history(history)
        prompt = f"{REWRITE_SYSTEM_PROMPT}\n\n对话历史：\n{history_text}\n\n最新问题：{question}\n\n改写后的查询："
        try:
            response = self.llm.invoke(prompt)
            rewritten = response.content.strip()
            if rewritten:
                return rewritten
        except Exception:
            pass
        return question

    def answer(self, question: str, top_k: int = 5, document_ids: list[int] | None = None,
               history: list[dict] | None = None):
        """执行完整 RAG 流程：查询改写 → 向量检索 → 构建 prompt → LLM 生成"""
        # 0. 查询改写：模糊问题结合历史改写为自包含查询
        search_query = self._rewrite_query(question, history)

        # 1. 向量检索（多取几倍结果，便于后续过滤 + 跨文档均匀分配）
        filter_expr = None
        if document_ids:
            filter_expr = {"document_id": {"$in": document_ids}}
        results = self.vector_store.similarity_search(search_query, k=top_k * 4, filter=filter_expr)

        # 2. 按文档 ID 分组，同时过滤低分结果
        groups = {}
        for doc, score in results:
            if score > self.SCORE_THRESHOLD:
                continue
            did = doc.metadata.get("document_id")
            if did not in groups:
                groups[did] = []
            groups[did].append((doc, score))

        # 3. 轮询选取：确保各文档均匀参与
        selected = []
        doc_ids = list(groups.keys())
        while len(selected) < top_k and doc_ids:
            for did in list(doc_ids):
                if groups[did]:
                    selected.append(groups[did].pop(0))
                    if len(selected) >= top_k:
                        break
                if not groups[did]:
                    doc_ids.remove(did)

        # 最终按相关性分数降序排列（分数越低越相关）
        selected.sort(key=lambda x: x[1])

        # 4. 构建上下文（每段标注来源文件名）
        context_parts = []
        sources = []
        seen = set()
        for doc, score in selected:
            source_name = doc.metadata.get("file_name", "未知文档")
            context_parts.append(f"[来自文件: {source_name}]\n{doc.page_content}")
            uid = doc.metadata.get("document_id", "")
            if uid not in seen:
                seen.add(uid)
                sources.append({
                    "document_id": uid,
                    "file_name": source_name,
                    "content": doc.page_content[:200],
                    "score": round(score, 4),
                })

        context = "\n\n".join(context_parts)

        # 5. 构建 Prompt → 调 LLM
        history_text = self._format_history(history)
        messages = self.prompt.format_messages(question=question, context=context, history=history_text)
        response = self.llm.invoke(messages)

        return {
            "answer": response.content,
            "sources": sources,
        }
