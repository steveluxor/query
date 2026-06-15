package steveluxor.ragknowledgesystem.common;

public class Constants {

    public static final String ROLE_USER = "USER";//用户角色
    public static final String ROLE_ADMIN = "ADMIN";//管理员角色

    public static final String DOC_STATUS_UPLOADED = "UPLOADED";//已上传文档状态
    public static final String DOC_STATUS_PROCESSING = "PROCESSING";//处理中文档状态
    public static final String DOC_STATUS_COMPLETED = "COMPLETED";//已完成文档状态
    public static final String DOC_STATUS_FAILED = "FAILED";//处理失败文档状态

    public static final String CODE_PREFIX = "CODE:";//验证码前缀

    // ====== 提示信息 ======

    // 认证相关
    public static final String USER_NOT_LOGIN = "未登录，请先登录";
    public static final String JWT_EXPIRED = "登录令牌无效或已过期";
    public static final String PHONE_FORMAT_ERROR = "手机号格式错误";
    public static final String CODE_SEND_SUCCESS = "验证码发送成功";
    public static final String CODE_ERROR = "验证码错误";

    // 用户相关
    public static final String USER_NOT_EXIST = "用户不存在";
    public static final String USERNAME_EXISTS = "用户名已存在";
    public static final String USER_UPDATE_SUCCESS = "更新成功";
    public static final String USER_DELETE_SUCCESS = "删除成功";

    // 文档相关
    public static final String FILE_NOT_EMPTY = "文件不能为空";
    public static final String FILE_NOT_EXIST = "文件不存在";
    public static final String FILE_NO_PERMISSION = "无权删除他人文件";
    public static final String DOC_UPLOAD_FAILED = "文档上传失败";
    public static final String DOC_DELETE_FAILED = "文档删除失败";
    public static final String DOC_GET_URL_FAILED = "获取文档URL失败";

    // 问答相关
    public static final String AI_SERVICE_ERROR_PREFIX = "AI 服务返回错误: ";
    public static final String AI_SERVICE_NOT_STARTED = "AI 服务未启动，请先启动 Python 服务";
    public static final String QA_PROCESS_FAILED_PREFIX = "问答处理失败: ";
    public static final String QA_RECORD_NOT_EXIST = "记录不存在";
    public static final String QA_SELECT_RECORD_FIRST = "请选择要删除的记录";
    public static final String QA_SESSION_NOT_EXIST = "会话不存在";

    // 通用
    public static final String SERVER_ERROR = "服务器内部错误";

    // Redis 问答缓存
    public static final String QA_CACHE_PREFIX = "qa:cache:";
    public static final Long QA_CACHE_TTL = 30L; // 分钟

    // Redis 接口限流
    public static final String RATE_LIMIT_PREFIX = "rate:";
    public static final Long RATE_LIMIT_MAX = 30L; // 每分钟最大请求数
    public static final Long RATE_LIMIT_WINDOW = 60L; // 限流窗口（秒）
    public static final String RATE_LIMIT_EXCEEDED = "请求过于频繁，请稍后再试";

    // Redis 文件锁（防止同名文件同时上传）
    public static final String FILE_LOCK_PREFIX = "lock:ingest:";
    public static final Long FILE_LOCK_TTL = 120L; // 秒（与向量化超时一致）
    public static final String FILE_NAME_EXISTS = "文件名已存在，请修改文件名";

    // RabbitMQ（异步向量化）
    public static final String RABBITMQ_INGEST_EXCHANGE = "ingest.exchange";
    public static final String RABBITMQ_INGEST_QUEUE = "ingest.queue";
    public static final String RABBITMQ_INGEST_ROUTING_KEY = "ingest.routing";

    private Constants() {
    }
}
