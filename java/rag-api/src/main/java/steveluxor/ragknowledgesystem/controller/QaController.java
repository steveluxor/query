package steveluxor.ragknowledgesystem.controller;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;
import steveluxor.ragknowledgesystem.common.CurrentUser;
import steveluxor.ragknowledgesystem.common.Result;
import steveluxor.ragknowledgesystem.dto.AskRequest;
import steveluxor.ragknowledgesystem.service.QaService;

import java.util.List;

@RestController
@RequestMapping("/qa")
@Slf4j
public class QaController {

    private final QaService qaService;

    @Autowired
    public QaController(QaService qaService) {
        this.qaService = qaService;
    }

    @PostMapping("/ask")
    public Result ask(@RequestBody AskRequest request) {
        log.info("问答请求: question={}, sessionId={}", request.getQuestion(), request.getSessionId());
        return qaService.ask(request);
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
        log.info("批量删除问答历史: ids={}, userId={}", ids, ids);
        return qaService.deleteBatch(ids, userId);
    }

    @DeleteMapping("/session/{id}")
    public Result deleteSession(@PathVariable("id") Long id) {
        Long userId = CurrentUser.get();
        log.info("删除会话: sessionId={}, userId={}", id, userId);
        return qaService.deleteSession(id);
    }
}
