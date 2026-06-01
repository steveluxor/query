import chromadb
from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings
from app.config import settings


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

    def add_texts(self, texts: list[str], metadatas: list[dict]) -> list[str]:
        """将文本块及其元数据向量化后存入，返回 ID 列表"""
        ids = self._db.add_texts(texts=texts, metadatas=metadatas)
        return ids

    def similarity_search(self, query: str, k: int = 5, filter: dict | None = None):
        """语义检索最相关的 K 个文本块"""
        return self._db.similarity_search_with_score(query, k=k, filter=filter)

    def delete_by_document_id(self, document_id: int):
        """删除指定文档的所有向量"""
        results = self._db.get(where={"document_id": document_id})
        if results and results.get("ids"):
            self._db.delete(ids=results["ids"])
