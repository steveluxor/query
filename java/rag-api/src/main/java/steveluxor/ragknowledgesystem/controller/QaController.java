package steveluxor.ragknowledgesystem.controller;
import java.lang.Long;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import steveluxor.ragknowledgesystem.common.CurrentUser;
import steveluxor.ragknowledgesystem.common.Result;
import steveluxor.ragknowledgesystem.dto.AskRequest;
import steveluxor.ragknowledgesystem.service.QaService;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/qa")
@Slf4j
public class QaController {

    private final QaService qaService;

    @Value("${ai-service.callback-token:}")
    private String callbackToken;

    @Autowired
    public QaController(QaService qaService) {
        this.qaService = qaService;
    }

    @PostMapping("/ask")
    public Result ask(@RequestBody AskRequest request) {
        log.info("问答请求: question={}, sessionId={}", request.getQuestion(), request.getSessionId());
        return qaService.ask(request);
    }

    @GetMapping(value = "/runtime/{runId}", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter streamRuntime(@PathVariable("runId") String runId) {
        log.info("SSE Runtime 连接: runId={}", runId);
        return qaService.streamRuntime(runId);
    }

    @PostMapping("/callback")
    public ResponseEntity<?> callback(
            @RequestBody Map<String, Object> body,
            @RequestHeader(value = "X-Callback-Token", defaultValue = "") String token) {
        if (!callbackToken.isBlank() && !callbackToken.equals(token)) {
            log.warn("Python Callback 鉴权失败: token 不匹配");
            return ResponseEntity.status(HttpStatus.FORBIDDEN).body(Result.fail("callback token 校验失败"));
        }
        log.info("Python Callback: sessionId={}", body.get("session_id"));
        return ResponseEntity.ok(qaService.handleCallback(body));
    }

    @GetMapping("/sessions")
    public Result getSessions() {
        Long userId = CurrentUser.get();
        log.info("查询会话列表: userId={}", userId);
        return qaService.getSessions();
    }

    @PostMapping("/session")
    public Result createSession() {
        Long userId = CurrentUser.get();
        log.info("新建会话: userId={}", userId);
        return qaService.createSession();
    }

    @GetMapping("/history")
    public Result history(@RequestParam("sessionId") Long sessionId) {
        Long userId = CurrentUser.get();
        log.info("查询问答历史: userId={}, sessionId={}", userId, sessionId);
        return qaService.history(sessionId);
    }

    @DeleteMapping("/history/{id}")
    public Result deleteHistory(@PathVariable("id") Long id) {
        Long userId = CurrentUser.get();
        log.info("删除问答历史: id={}, userId={}", id, userId);
        return qaService.delete(id, userId);
    }

    @DeleteMapping("/history/batch")
    public Result deleteBatch(@RequestBody List<Long> ids) {
        Long userId = CurrentUser.get();
        log.info("批量删除问答历史: ids={}, userId={}", ids, userId);
        return qaService.deleteBatch(ids, userId);
    }

    @DeleteMapping("/session/{id}")
    public Result deleteSession(@PathVariable("id") Long id) {
        Long userId = CurrentUser.get();
        log.info("删除会话: sessionId={}, userId={}", id, userId);
        return qaService.deleteSession(id);
    }
}
