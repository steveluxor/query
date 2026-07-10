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
