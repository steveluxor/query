from app.core.infra.sparse_store import SparseStore


def test_sparse_query_keeps_document_ids_as_explicit_or_filters():
    query = SparseStore._build_query(["合同", "金额"], [3, 1, 3])

    assert query == "@terms:(合同|金额) (@document_id:[1 1]|@document_id:[3 3])"
    assert "[1 3]" not in query


def test_sparse_query_escapes_redisearch_special_characters():
    query = SparseStore._build_query(["C++", "foo-bar"], [1])

    assert "c++" in query
    assert "foo\\-bar" in query
