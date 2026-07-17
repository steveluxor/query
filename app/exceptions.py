"""自定义异常类"""


class BizException(Exception):
    """业务异常"""
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class ErrorCode:
    """错误码常量"""
    PARAM_ERROR = 400
    NOT_FOUND = 404
    SERVER_ERROR = 500
    VECTOR_STORE_ERROR = 1001
    LLM_ERROR = 1002
    DOCUMENT_PARSE_ERROR = 1003


# ==================== v6 Architecture Exceptions ====================


class PlannerError(Exception):
    """Planner 生成或解析错误"""
    pass


class WorkflowValidationError(Exception):
    """TaskGraph 校验错误：结构、能力、可达性"""
    pass


class WorkflowExecutionError(Exception):
    """TaskGraph 执行错误：目标输出缺失、依赖不符"""
    pass


class AgentExecutionError(Exception):
    """Agent 执行异常（携带 agent 和 task_id 上下文）"""
    def __init__(self, message, *, agent="", task_id=""):
        self.agent = agent
        self.task_id = task_id
        super().__init__(f"[{agent}/{task_id}] {message}")


class ControlActionError(Exception):
    """ControlAction 处理错误：未知 action、处理失败"""
    pass
