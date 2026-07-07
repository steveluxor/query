package steveluxor.ragknowledgesystem.interceptor;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;
import steveluxor.ragknowledgesystem.common.Constants;
import steveluxor.ragknowledgesystem.common.CurrentUser;
import steveluxor.ragknowledgesystem.common.JwtUtils;
import steveluxor.ragknowledgesystem.common.Result;

@Component
public class LoginInterceptor implements HandlerInterceptor {

    // 用于序列化和反序列化 JSON 对象
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();


    private final JwtUtils jwtUtils;

    @Autowired
    public LoginInterceptor(JwtUtils jwtUtils) {
        this.jwtUtils = jwtUtils;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) throws Exception {
        String authHeader = request.getHeader("Authorization");

        if (authHeader == null || !authHeader.startsWith("Bearer ")) {
            response.setContentType("application/json;charset=UTF-8");
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            OBJECT_MAPPER.writeValue(response.getWriter(), Result.fail(HttpServletResponse.SC_UNAUTHORIZED, Constants.USER_NOT_LOGIN));
            return false;
        }

        String token = authHeader.substring(7);
        try {
            Long userId = jwtUtils.getUserId(token);
            request.setAttribute("userId", userId);
            CurrentUser.set(userId);
            return true;
        } catch (Exception e) {
            response.setContentType("application/json;charset=UTF-8");
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            OBJECT_MAPPER.writeValue(response.getWriter(), Result.fail(HttpServletResponse.SC_UNAUTHORIZED, Constants.JWT_EXPIRED));
            return false;
        }
    }

    @Override
    public void afterCompletion(HttpServletRequest request, HttpServletResponse response, Object handler, Exception ex) {
        CurrentUser.remove();
    }
}
