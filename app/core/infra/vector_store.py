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
        normalized = []
        ids = []
        for index, metadata in enumerate(metadatas):
            item = dict(metadata)
            chunk_id = self._chunk_id(item, fallback=f"chunk:{index}")
            item["chunk_id"] = chunk_id
            normalized.append(item)
            ids.append(chunk_id)
        self._db.add_texts(texts=texts, metadatas=normalized, ids=ids)

    @staticmethod
    def _chunk_id(metadata: dict, fallback: str) -> str:
        existing = metadata.get("chunk_id")
        if existing:
            return str(existing)
        document_id = metadata.get("document_id")
        chunk_index = metadata.get("chunk_index")
        if document_id is not None and chunk_index is not None:
            version = int(metadata.get("index_version", 1))
            return f"doc:{document_id}:v:{version}:chunk:{chunk_index}"
        return fallback

    def get_index_records(self) -> list[tuple[str, str, dict]]:
        """返回全部 chunk，并为旧 Chroma 数据回填稳定 chunk_id。"""
        results = self._db.get()
        ids = results.get("ids", [])
        docs = results.get("documents", [])
        metadatas = results.get("metadatas", [])
        updates, records = [], []
        for chroma_id, content, metadata in zip(ids, docs, metadatas):
            item = dict(metadata or {})
            chunk_id = self._chunk_id(item, fallback=str(chroma_id))
            if "index_version" not in item:
                item["index_version"] = 1
            if item.get("chunk_id") != chunk_id:
                item["chunk_id"] = chunk_id
                updates.append((chroma_id, item))
            records.append((chunk_id, content, item))
        if updates:
            self._db._collection.update(
                ids=[item[0] for item in updates],
                metadatas=[item[1] for item in updates],
            )
            logger.info("为 %d 条已有 Chroma chunk 回填稳定 ID", len(updates))
        return records

    def get_document_index_records(self, document_id: int) -> list[tuple[str, str, dict]]:
        records = self.get_index_records()
        return [record for record in records if record[2].get("document_id") == document_id]

    def similarity_search(self, query: str, k: int = 5, filter: dict | None = None):
        """语义检索最相关的 K 个文本块"""
        return self._db.similarity_search_with_score(query, k=k, filter=filter)

    def delete_by_document_id(self, document_id: int, index_version: int | None = None):
        """删除指定文档的所有向量"""
        where = {"document_id": document_id}
        if index_version is not None:
            where = {"$and": [{"document_id": document_id}, {"index_version": index_version}]}
        results = self._db.get(where=where)
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
