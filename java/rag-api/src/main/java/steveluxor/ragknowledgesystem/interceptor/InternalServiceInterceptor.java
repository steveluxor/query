package steveluxor.ragknowledgesystem.interceptor;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.servlet.HandlerInterceptor;
import steveluxor.ragknowledgesystem.common.Result;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

@Component
public class InternalServiceInterceptor implements HandlerInterceptor {
    private static final String TOKEN_HEADER = "X-Internal-Service-Token";
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    private final String internalToken;

    public InternalServiceInterceptor(@Value("${ai-service.internal-token:}") String internalToken) {
        this.internalToken = internalToken;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) throws Exception {
        String token = request.getHeader(TOKEN_HEADER);
        if (!StringUtils.hasText(internalToken) || !StringUtils.hasText(token)
                || !MessageDigest.isEqual(internalToken.getBytes(StandardCharsets.UTF_8), token.getBytes(StandardCharsets.UTF_8))) {
            response.setContentType("application/json;charset=UTF-8");
            response.setStatus(HttpStatus.UNAUTHORIZED.value());
            OBJECT_MAPPER.writeValue(response.getWriter(), Result.fail(HttpStatus.UNAUTHORIZED.value(), "internal service token 校验失败"));
            return false;
        }
        return true;
    }
}
