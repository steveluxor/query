package steveluxor.ragknowledgesystem.config;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;
import steveluxor.ragknowledgesystem.interceptor.LoginInterceptor;
import steveluxor.ragknowledgesystem.interceptor.RateLimitInterceptor;

@Configuration
public class WebMvcConfig implements WebMvcConfigurer {

    private final LoginInterceptor loginInterceptor;
    private final RateLimitInterceptor rateLimitInterceptor;

    @Autowired
    public WebMvcConfig(LoginInterceptor loginInterceptor, RateLimitInterceptor rateLimitInterceptor) {
        this.loginInterceptor = loginInterceptor;
        this.rateLimitInterceptor = rateLimitInterceptor;
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        // 登录拦截器
        registry.addInterceptor(loginInterceptor)
                .addPathPatterns("/**")
                .excludePathPatterns(
                        "/user/login",
                        "/user/send-code"
                );

        // 限流拦截器（放在登录之后，需要用户已登录）
        registry.addInterceptor(rateLimitInterceptor)
                .addPathPatterns("/**")
                .excludePathPatterns(
                        "/user/login",
                        "/user/send-code"
                );
    }
}
