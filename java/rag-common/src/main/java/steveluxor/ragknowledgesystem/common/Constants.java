package steveluxor.ragknowledgesystem.common;

public class Constants {

    public static final String ROLE_USER = "USER";//用户角色
    public static final String ROLE_ADMIN = "ADMIN";//管理员角色

    public static final String DOC_STATUS_UPLOADED = "UPLOADED";//已上传文档状态
    public static final String DOC_STATUS_PROCESSING = "PROCESSING";//处理中文档状态
    public static final String DOC_STATUS_COMPLETED = "COMPLETED";//已完成文档状态
    public static final String DOC_STATUS_FAILED = "FAILED";//处理失败文档状态

    public static final String CODE_PREFIX = "CODE:";//验证码前缀

    public static final String USER_NOT_LOGIN="未登录，请先登录";
    public static final String JWT_EXPIRED="登录令牌无效或已过期";



    private Constants() {
    }
}
