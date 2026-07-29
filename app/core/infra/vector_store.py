import logging
import threading

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from app.config import settings

logger = logging.getLogger(__name__)


class VectorStore:
    """向量数据库封装（Chroma + Ollama Embeddings）"""

    def __init__(self):
        embedding = OllamaEmbeddings(
            model=settings.embedding_model_name,
            base_url=settings.ollama_base_url,
        )
        self._db = Chroma(
            collection_name="rag_docs",
            embedding_function=embedding,
            persist_directory=settings.vector_store_path,
        )
        self._rebuild_lock = threading.Lock()
        self._delete_count = 0
        self._compact_threshold = 50

    def add_texts(self, texts: list[str], metadatas: list[dict]):
        """将文本块及其元数据向量化后存入"""
        self._db.add_texts(texts=texts, metadatas=metadatas)

    def similarity_search(self, query: str, k: int = 5, filter: dict | None = None):
        """语义检索最相关的 K 个文本块"""
        return self._db.similarity_search_with_score(query, k=k, filter=filter)

    def delete_by_document_id(self, document_id: int):
        """删除指定文档的所有向量"""
        results = self._db.get(where={"document_id": document_id})
        if results and results.get("ids"):
            self._db.delete(ids=results["ids"])
            self._delete_count += 1
            if self._delete_count >= self._compact_threshold:
                self.compact()

    def compact(self):
        """重建索引，释放 tombstone 占用的磁盘空间"""
        with self._rebuild_lock:
            all_data = self._db.get(include=["embeddings", "documents", "metadatas"])
            if not all_data.get("ids"):
                self._db.reset_collection()
                self._delete_count = 0
                return

            self._db.reset_collection()

            self._db._collection.add(
                ids=all_data["ids"],
                embeddings=all_data["embeddings"],
                documents=all_data["documents"],
                metadatas=all_data["metadatas"],
            )
            self._delete_count = 0
            logger.info("向量库压缩完成，保留 %d 条记录", len(all_data["ids"]))

    def get_all_chunks(self, filter: dict | None = None) -> list[tuple[Document, float]]:
        """获取全部 chunk（不走相似度搜索，用于聚合查询）"""
        results = self._db.get(where=filter) if filter else self._db.get()
        docs = results.get("documents", [])
        metadatas = results.get("metadatas", [])
        logger.info("get_all_chunks: 共 %d 个 chunk", len(docs))
        return [
            (Document(page_content=doc, metadata=meta), 0.0)
            for doc, meta in zip(docs, metadatas)
        ]

    def keyword_search(self, keywords: list[str], filter: dict | None = None,
                       max_results: int = 20) -> list[tuple[Document, float]]:
        """关键词搜索：在 chunk 内容中精确匹配关键词，返回匹配度评分"""
        results = self._db.get(where=filter) if filter else self._db.get()
        docs = results.get("documents", [])
        metadatas = results.get("metadatas", [])

        scored = []
        for doc, meta in zip(docs, metadatas):
            content_lower = doc.lower()
            matched = sum(1 for kw in keywords if kw.lower() in content_lower)
            if matched > 0:
                score = matched / len(keywords)
                scored.append((Document(page_content=doc, metadata=meta), score))

        scored.sort(key=lambda x: -x[1])
        logger.info("关键词搜索: keywords=%s, 匹配 %d 个 chunk", keywords, len(scored))
        return scored[:max_results]

    def get_document_names(self) -> dict[int, str]:
        """获取所有文档ID→文件名映射（用于文件名匹配回退）"""
        results = self._db.get(include=["metadatas"])
        doc_names = {}
        for meta in results.get("metadatas", []):
            did = meta.get("document_id")
            fn = meta.get("file_name", "")
            if did is not None and did not in doc_names:
                doc_names[did] = fn
        return doc_names

    def close(self):
        """释放 Chroma 底层资源（HTTP 连接、文件句柄）"""
        try:
            if hasattr(self._db, '_client'):
                self._db._client.close()
        except Exception as e:
            logger.warning("关闭 Chroma 客户端时出错: %s", e)
