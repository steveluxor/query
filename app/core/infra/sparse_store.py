"""RediSearch-backed BM25 sparse retrieval for Chroma chunks."""

import logging
import re
from collections.abc import Iterable

import jieba
import redis
from langchain_core.documents import Document
from redis.exceptions import ResponseError

from app.config import settings

logger = logging.getLogger(__name__)


class SparseStore:
    """Keep BM25 documents in a dedicated RediSearch service.

    Chroma remains the source of dense vectors. Both stores use ``chunk_id`` so
    RRF can join their ranked result lists deterministically.
    """

    KEY_PREFIX = "rag:bm25:chunk:"

    def __init__(self):
        self.index_name = settings.search_redis_index_name
        self.client = redis.Redis(
            host=settings.search_redis_host,
            port=settings.search_redis_port,
            password=settings.search_redis_password or None,
            decode_responses=True,
        )

    def ensure_index(self):
        try:
            info = self.client.execute_command("FT.INFO", self.index_name)
            attributes = info.get("attributes", []) if isinstance(info, dict) else []
            if not any(item.get("attribute") == "index_version" for item in attributes if isinstance(item, dict)):
                self.client.execute_command("FT.ALTER", self.index_name, "SCHEMA", "ADD", "index_version", "NUMERIC", "SORTABLE")
                logger.info("RediSearch 索引 %s 已增加 index_version 字段", self.index_name)
            return
        except ResponseError as error:
            if "unknown index name" not in str(error).lower():
                raise
        self.client.execute_command(
            "FT.CREATE", self.index_name,
            "ON", "HASH",
            "PREFIX", "1", self.KEY_PREFIX,
            "SCHEMA",
            "chunk_id", "TAG",
            "document_id", "NUMERIC", "SORTABLE",
            "index_version", "NUMERIC", "SORTABLE",
            "file_name", "TEXT", "NOSTEM",
            "terms", "TEXT", "NOSTEM",
            "content", "TEXT", "NOINDEX",
        )
        logger.info("已创建 RediSearch 索引 %s", self.index_name)

    @staticmethod
    def _terms(content: str) -> str:
        tokens = []
        for token in jieba.lcut(content):
            token = token.strip().lower()
            if not token:
                continue
            if len(token) == 1 and not token.isalnum():
                continue
            tokens.append(token)
        return " ".join(tokens)

    @staticmethod
    def _escape_term(term: str) -> str:
        return re.sub(r"([\\\\\-@{}\[\]()|~*:%])", r"\\\1", term)

    @classmethod
    def _build_query(cls, terms: list[str], document_ids: list[int] | None = None) -> str:
        """Build a RediSearch query without widening the document ACL filter."""
        escaped = [cls._escape_term(term.lower()) for term in terms if term.strip()]
        if not escaped:
            return ""

        query = f"@terms:({'|'.join(escaped)})"
        if document_ids:
            # A numeric range such as [1 3] would also match document 2. Keep
            # each permitted document as a one-value range joined by OR.
            allowed = "|".join(
                f"@document_id:[{int(document_id)} {int(document_id)}]"
                for document_id in sorted(set(document_ids))
            )
            query += f" ({allowed})"
        return query

    @classmethod
    def _key(cls, chunk_id: str) -> str:
        return f"{cls.KEY_PREFIX}{chunk_id}"

    def replace_document(self, document_id: int, records: Iterable[tuple[str, str, dict]], index_version: int | None = None):
        self.ensure_index()
        if index_version is None:
            self.delete_document(document_id)
        pipe = self.client.pipeline(transaction=True)
        count = 0
        for chunk_id, content, metadata in records:
            if metadata.get("document_id") != document_id or (index_version is not None and metadata.get("index_version", 1) != index_version):
                continue
            pipe.hset(self._key(chunk_id), mapping={
                "chunk_id": chunk_id,
                "document_id": str(document_id),
                "index_version": str(metadata.get("index_version", 1)),
                "file_name": str(metadata.get("file_name", "")),
                "terms": self._terms(content),
                "content": content,
            })
            count += 1
        if count:
            pipe.execute()
        logger.info("RediSearch 已索引文档 %d 的 %d 个 chunk", document_id, count)

    def rebuild(self, records: Iterable[tuple[str, str, dict]]):
        self.ensure_index()
        keys = list(self.client.scan_iter(match=f"{self.KEY_PREFIX}*", count=500))
        if keys:
            self.client.delete(*keys)
        pipe = self.client.pipeline(transaction=True)
        count = 0
        for chunk_id, content, metadata in records:
            document_id = metadata.get("document_id")
            if document_id is None:
                continue
            pipe.hset(self._key(chunk_id), mapping={
                "chunk_id": chunk_id,
                "document_id": str(document_id),
                "index_version": str(metadata.get("index_version", 1)),
                "file_name": str(metadata.get("file_name", "")),
                "terms": self._terms(content),
                "content": content,
            })
            count += 1
        if count:
            pipe.execute()
        logger.info("RediSearch 全量重建完成，共 %d 个 chunk", count)
        return count

    def delete_document(self, document_id: int):
        self.ensure_index()
        response = self.client.execute_command(
            "FT.SEARCH", self.index_name, f"@document_id:[{document_id} {document_id}]",
            "NOCONTENT", "LIMIT", "0", "10000",
        )
        if isinstance(response, dict):
            keys = [item["id"] for item in response.get("results", [])]
        else:
            keys = response[1:]
        if keys:
            self.client.delete(*keys)
            logger.info("RediSearch 已删除文档 %d 的 %d 个 chunk", document_id, len(keys))

    def search(self, terms: list[str], document_ids: list[int] | None = None,
               limit: int = 60) -> list[tuple[Document, float]]:
        self.ensure_index()
        query = self._build_query(terms, document_ids)
        if not query:
            return []
        response = self.client.execute_command(
            "FT.SEARCH", self.index_name, query,
            "WITHSCORES", "RETURN", "5", "chunk_id", "document_id", "index_version", "file_name", "content",
            "SCORER", "BM25", "LIMIT", "0", str(limit),
        )
        result = []
        if isinstance(response, dict):
            matches = [
                (float(item["score"]), item.get("extra_attributes", {}))
                for item in response.get("results", [])
            ]
        else:
            matches = [
                (float(response[index + 1]),
                 dict(zip(response[index + 2][::2], response[index + 2][1::2])))
                for index in range(1, len(response), 3)
            ]
        for score, fields in matches:
            result.append((Document(
                page_content=fields.get("content", ""),
                metadata={
                    "chunk_id": fields.get("chunk_id", ""),
                    "document_id": int(fields["document_id"]),
                    "index_version": int(fields.get("index_version", 1)),
                    "file_name": fields.get("file_name", ""),
                },
            ), score))
        logger.info("BM25 搜索: terms=%s, 命中 %d 个 chunk", terms, len(result))
        return result
