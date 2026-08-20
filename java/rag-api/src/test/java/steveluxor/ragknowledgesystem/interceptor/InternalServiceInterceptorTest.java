package steveluxor.ragknowledgesystem.interceptor;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class InternalServiceInterceptorTest {

    @Test
    void permitsTheConfiguredInternalToken() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("X-Internal-Service-Token", "internal-token");

        assertTrue(new InternalServiceInterceptor("internal-token")
                .preHandle(request, new MockHttpServletResponse(), new Object()));
    }

    @Test
    void rejectsMissingOrUnconfiguredTokens() throws Exception {
        assertFalse(new InternalServiceInterceptor("internal-token")
                .preHandle(new MockHttpServletRequest(), new MockHttpServletResponse(), new Object()));
        assertFalse(new InternalServiceInterceptor("")
                .preHandle(new MockHttpServletRequest(), new MockHttpServletResponse(), new Object()));
    }
}
