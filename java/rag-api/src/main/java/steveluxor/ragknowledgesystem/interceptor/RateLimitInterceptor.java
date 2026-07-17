package steveluxor.ragknowledgesystem.interceptor;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.io.ClassPathResource;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;
import steveluxor.ragknowledgesystem.common.Constants;
import steveluxor.ragknowledgesystem.common.CurrentUser;
import steveluxor.ragknowledgesystem.common.Result;

import java.util.Collections;
import java.util.concurrent.ThreadLocalRandom;

@Component
public class RateLimitInterceptor implements HandlerInterceptor {

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private static final DefaultRedisScript<Long> RATE_LIMIT_SCRIPT = new DefaultRedisScript<>();

    static {
        RATE_LIMIT_SCRIPT.setLocation(new ClassPathResource("scripts/rate_limit.lua"));
        RATE_LIMIT_SCRIPT.setResultType(Long.class);
    }

    private final StringRedisTemplate redisTemplate;

    @Autowired
    public RateLimitInterceptor(StringRedisTemplate redisTemplate) {
        this.redisTemplate = redisTemplate;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) throws Exception {
        Long userId = CurrentUser.get();
        if (userId == null) {
            return true;
        }

        long now = System.currentTimeMillis();
        String key = Constants.RATE_LIMIT_PREFIX + userId;
        String uniqueMember = userId + ":" + now + ":" + ThreadLocalRandom.current().nextLong();
        Long result = redisTemplate.execute(RATE_LIMIT_SCRIPT,
                Collections.singletonList(key),
                String.valueOf(now),
                String.valueOf(Constants.RATE_LIMIT_WINDOW),
                String.valueOf(Constants.RATE_LIMIT_MAX),
                uniqueMember);

        if (result != null && result == 0) {
            response.setContentType("application/json;charset=UTF-8");
            response.setStatus(429);
            OBJECT_MAPPER.writeValue(response.getWriter(),
                    Result.fail(429, Constants.RATE_LIMIT_EXCEEDED));
            return false;
        }

        return true;
    }
}
