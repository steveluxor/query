from pathlib import Path

import win32com.client
import pythoncom
from langchain_text_splitters.character import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader, TextLoader
from langchain_core.documents import Document


class WordDocLoader:
    """使用 Word COM 解析旧版 .doc 文件"""

    def __init__(self, file_path: str):
        self.file_path = file_path

    def load(self) -> list[Document]:
        pythoncom.CoInitialize()
        word = None
        doc = None
        try:
            word = win32com.client.Dispatch("Word.Application")
            word.Visible = False
            doc = word.Documents.Open(str(Path(self.file_path).resolve()))
            text = doc.Content.Text
            if not text.strip():
                return []
            return [Document(page_content=text)]
        finally:
            if doc:
                doc.Close(False)
            if word:
                word.Quit()
            pythoncom.CoUninitialize()


class ExcelLoader:
    """解析 xlsx 文件，每行转为一条 '列名: 值' 文本"""

    SUMMARY_KEYWORDS = {"总计", "合计", "小计", "汇总", "总计:", "合计:", "小计:"}

    def __init__(self, file_path: str):
        self.file_path = file_path

    def load(self) -> list[Document]:
        from openpyxl import load_workbook
        wb = load_workbook(self.file_path, read_only=True, data_only=True)
        docs = []
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            if rows:
                headers = [str(h) if h is not None else f"col{i}" for i, h in enumerate(rows[0])]
                for row in rows[1:]:
                    # 跳过汇总行（第一列包含"总计"/"合计"/"小计"等）
                    first_cell = str(row[0]).strip() if row[0] is not None else ""
                    if first_cell in self.SUMMARY_KEYWORDS:
                        continue
                    pairs = []
                    for header, value in zip(headers, row):
                        if value is not None:
                            pairs.append(f"{header}: {value}")
                    if pairs:
                        doc = Document(
                            page_content="\n".join(pairs),
                            metadata={"sheet_name": ws.title},
                        )
                        docs.append(doc)
        wb.close()
        return docs


class DocumentProcessor:
    """文档处理器：解析不同格式文件并按策略切片"""

    LOADER_MAP = {
        ".pdf": PyPDFLoader,
        ".docx": Docx2txtLoader,
        ".doc": WordDocLoader,
        ".txt": TextLoader,
        ".md": TextLoader,
        ".xlsx": ExcelLoader,
    }

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
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

        # Excel 每行已是独立完整记录，跳过切片
        if ext == ".xlsx":
            chunks = docs
        else:
            chunks = self.splitter.split_documents(docs)

        results = []
        for i, chunk in enumerate(chunks):
            metadata = {
                "document_id": document_id,
                "file_name": file_name,
                "chunk_index": i,
                "source": file_name,
            }
            # Excel chunk 保留 sheet_name 元数据
            if hasattr(chunk, "metadata") and "sheet_name" in chunk.metadata:
                metadata["sheet_name"] = chunk.metadata["sheet_name"]
            results.append({
                "text": chunk.page_content,
                "metadata": metadata,
            })
        return results
