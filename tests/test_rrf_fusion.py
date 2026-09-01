from langchain_core.documents import Document

from app.core.rag_engine import RAGEngine


def _item(chunk_id: str):
    return Document(page_content=chunk_id, metadata={"chunk_id": chunk_id}), 0.0


def test_rrf_fuses_and_deduplicates_rankings():
    dense = [_item("a"), _item("b")]
    sparse = [_item("b"), _item("c")]

    result = RAGEngine._rrf_fuse(dense, sparse)

    assert [doc.metadata["chunk_id"] for doc, _ in result] == ["b", "a", "c"]
    assert all(score < 0 for _, score in result)


def test_rrf_ignores_chunks_without_stable_id():
    missing = Document(page_content="missing", metadata={})
    result = RAGEngine._rrf_fuse([(missing, 0.0)], [_item("a")])
    assert [doc.metadata["chunk_id"] for doc, _ in result] == ["a"]
