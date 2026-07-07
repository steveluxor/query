package steveluxor.ragknowledgesystem.service;

import steveluxor.ragknowledgesystem.common.Result;
import steveluxor.ragknowledgesystem.dto.AskRequest;

import java.util.List;

public interface QaService {
    Result ask(AskRequest request);

    Result getSessions();

    Result createSession();

    Result history(Long sessionId);

    Result delete(Long id, Long userId);

    Result deleteBatch(List<Long> ids, Long userId);

    Result deleteSession(Long sessionId);
}
