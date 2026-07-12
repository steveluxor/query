from pydantic import BaseModel


class IngestRequest(BaseModel):
    """文档导入请求"""
    file_path: str
    document_id: int
    file_name: str


class IngestResponse(BaseModel):
    document_id: int
    status: str


class HistoryItem(BaseModel):
    """单轮问答历史"""
    question: str
    answer: str
    is_agg: bool = False


class QuestionRequest(BaseModel):
    """问答请求"""
    question: str
    document_ids: list[int] | None = None
    top_k: int = 5
    history: list[HistoryItem] | None = None
    session_id: str | None = None
    strategy: str | None = None  # relevance / diversity / None(自动判断)
    preferences: dict | None = None  # Java 从数据库传来的已存储偏好


class Source(BaseModel):
    document_id: int
    file_name: str
    content: str
    score: float


class AnswerResponse(BaseModel):
    answer: str
    sources: list[Source]
    is_agg: bool = False
    tools_called: list[str] = []
    session_id: str | None = None
    memory_data: dict | None = None
    reflection_count: int = 0
    plan: list[str] | None = None
