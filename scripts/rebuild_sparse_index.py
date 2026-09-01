"""Backfill RediSearch from the existing Chroma collection."""

import sys
from pathlib import Path

# Support both ``python scripts/rebuild_sparse_index.py`` and module execution.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.infra.sparse_store import SparseStore
from app.core.infra.vector_store import VectorStore


def main():
    vector_store = VectorStore()
    sparse_store = SparseStore()
    count = sparse_store.rebuild(vector_store.get_index_records())
    print(f"Rebuilt RediSearch index with {count} chunks.")


if __name__ == "__main__":
    main()
