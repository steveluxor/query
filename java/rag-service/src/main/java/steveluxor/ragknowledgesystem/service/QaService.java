package steveluxor.ragknowledgesystem.service;

import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import steveluxor.ragknowledgesystem.common.Result;
import steveluxor.ragknowledgesystem.dto.AskRequest;

import java.util.List;
import java.util.Map;

public interface QaService {
    Result ask(AskRequest request);

    SseEmitter streamRuntime(String runId);

    Result stop(String runId);

    Result handleCallback(Map<String, Object> body);

    Result getSessions();

    Result createSession();

    Result history(Long sessionId);

    Result delete(Long id, Long userId);

    Result deleteBatch(List<Long> ids, Long userId);

    Result deleteSession(Long sessionId);
}
