from pydantic import BaseModel


class IngestRequest(BaseModel):
    """文档导入请求"""
    file_path: str
    document_id: int
    file_name: str
    index_version: int = 1
    event_id: str | None = None


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
    # 仅由已鉴权的 Java 网关写入，用于运行态归属校验；不信任前端直传。
    user_id: int | None = None
    document_ids: list[int] | None = None
    document_versions: dict[int, int] | None = None
    history: list[HistoryItem] | None = None
    session_id: str | None = None
    strategy: str | None = None  # relevance / diversity / None(自动判断)
    preferences: dict | None = None  # Java 从数据库传来的已存储偏好


class StopRequest(BaseModel):
    """停止正在进行的问答"""
    run_id: str
    # 由 Java 网关注入，用于确认停止请求属于该运行的创建者。
    user_id: int | None = None
