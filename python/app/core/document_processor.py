from pathlib import Path

from langchain_text_splitters.character import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader, TextLoader


class DocumentProcessor:
    """文档处理器：解析不同格式文件并按策略切片"""

    LOADER_MAP = {
        ".pdf": PyPDFLoader,
        ".docx": Docx2txtLoader,
        ".txt": TextLoader,
        ".md": TextLoader,
    }

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 100):
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", "。", "；", "，", " ", ""],
        )

    def process(self, file_path: str, document_id: int, file_name: str) -> list[dict]:
        """解析文档并按策略切片，返回 [{text, metadata}, ...]"""
        path = Path(file_path)
        ext = path.suffix.lower()

        loader_cls = self.LOADER_MAP.get(ext)
        if loader_cls is None:
            raise ValueError(f"不支持的文件格式: {ext}")

        loader = loader_cls(str(path))
        docs = loader.load()

        chunks = self.splitter.split_documents(docs)
        results = []
        for i, chunk in enumerate(chunks):
            results.append({
                "text": chunk.page_content,
                "metadata": {
                    "document_id": document_id,
                    "file_name": file_name,
                    "chunk_index": i,
                    "source": file_name,
                },
            })
        return results
