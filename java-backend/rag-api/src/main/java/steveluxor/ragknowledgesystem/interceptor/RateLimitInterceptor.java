package steveluxor.ragknowledgesystem.interceptor;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;
import steveluxor.ragknowledgesystem.common.Constants;
import steveluxor.ragknowledgesystem.common.CurrentUser;
import steveluxor.ragknowledgesystem.common.Result;

import java.util.concurrent.TimeUnit;

@Component
public class RateLimitInterceptor implements HandlerInterceptor {

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private final StringRedisTemplate redisTemplate;

    @Autowired
    public RateLimitInterceptor(StringRedisTemplate redisTemplate) {
        this.redisTemplate = redisTemplate;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) throws Exception {
        Long userId = CurrentUser.get();
        if (userId == null) {
            return true; // 未登录用户不进行限流（由 LoginInterceptor 处理）
        }

        // 生成限流 Key：rate:{userId}:{当前分钟}
        long currentMinute = System.currentTimeMillis() / 1000 / 60;
        String key = Constants.RATE_LIMIT_PREFIX + userId + ":" + currentMinute;

        // 原子递增
        Long count = redisTemplate.opsForValue().increment(key);
        if (count != null && count == 1) {
            // 第一次请求，设置 60 秒过期
            redisTemplate.expire(key, Constants.RATE_LIMIT_WINDOW, TimeUnit.SECONDS);
        }

        // 判断是否超过限制
        if (count != null && count > Constants.RATE_LIMIT_MAX) {
            response.setContentType("application/json;charset=UTF-8");
            response.setStatus(429);
            OBJECT_MAPPER.writeValue(response.getWriter(),
                    Result.fail(429, Constants.RATE_LIMIT_EXCEEDED));
            return false;
        }

        return true;
    }
}
