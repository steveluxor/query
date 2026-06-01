package steveluxor.ragknowledgesystem.service.impl;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import steveluxor.ragknowledgesystem.common.CurrentUser;
import steveluxor.ragknowledgesystem.common.Result;
import steveluxor.ragknowledgesystem.dto.AskRequest;
import steveluxor.ragknowledgesystem.entity.Document;
import steveluxor.ragknowledgesystem.entity.QaHistory;
import steveluxor.ragknowledgesystem.entity.QaSession;
import steveluxor.ragknowledgesystem.mapper.DocumentMapper;
import steveluxor.ragknowledgesystem.mapper.QaHistoryMapper;
import steveluxor.ragknowledgesystem.mapper.QaSessionMapper;
import steveluxor.ragknowledgesystem.service.QaService;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Service
@Slf4j
public class QaServiceImpl implements QaService {

    private static final String ASK_PATH = "/qa/ask";
    private static final Duration TIMEOUT = Duration.ofSeconds(60);
    private static final int TITLE_MAX_LENGTH = 50;

    private final String pythonBaseUrl;
    private final QaHistoryMapper qaHistoryMapper;
    private final QaSessionMapper qaSessionMapper;
    private final DocumentMapper documentMapper;
    private final ObjectMapper objectMapper;
    private final HttpClient httpClient;

    @Autowired
    public QaServiceImpl(
            QaHistoryMapper qaHistoryMapper,
            QaSessionMapper qaSessionMapper,
            DocumentMapper documentMapper,
            @org.springframework.beans.factory.annotation.Value("${ai-service.python-base-url:http://localhost:8000}") String pythonBaseUrl) {
        this.qaHistoryMapper = qaHistoryMapper;
        this.qaSessionMapper = qaSessionMapper;
        this.documentMapper = documentMapper;
        this.pythonBaseUrl = pythonBaseUrl;
        this.objectMapper = new ObjectMapper();
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .version(HttpClient.Version.HTTP_1_1)
                .build();
    }

    @Override
    public Result ask(AskRequest request) {
        Long userId = CurrentUser.get();
        try {
            List<Document> accessibleDocs = documentMapper.selectByUserId(userId);
            List<Integer> accessibleDocIds = accessibleDocs.stream()
                    .map(doc -> doc.getId().intValue())
                    .collect(Collectors.toList());

            Map<String, Object> pythonReq = new HashMap<>();
            pythonReq.put("question", request.getQuestion());
            if (!accessibleDocIds.isEmpty()) {
                pythonReq.put("document_ids", accessibleDocIds);
            }

            // 获取最近对话历史，传递给 Python 端用于查询改写 + 上下文理解
            if (request.getSessionId() != null) {
                List<QaHistory> recentHistory = qaHistoryMapper.selectBySessionId(request.getSessionId(), userId);
                if (!recentHistory.isEmpty()) {
                    int size = recentHistory.size();
                    List<QaHistory> recentFive = recentHistory.subList(Math.max(0, size - 5), size);
                    List<Map<String, String>> historyList = recentFive.stream()
                            .map(h -> {
                                Map<String, String> item = new HashMap<>();
                                item.put("question", h.getQuestion());
                                item.put("answer", h.getAnswer());
                                return item;
                            })
                            .collect(Collectors.toList());
                    pythonReq.put("history", historyList);
                }
            }
            String jsonBody = objectMapper.writeValueAsString(pythonReq);
            log.info("发送到 Python: body={}", jsonBody);

            HttpRequest httpReq = HttpRequest.newBuilder()
                    .uri(URI.create(pythonBaseUrl + ASK_PATH))
                    .header("Content-Type", "application/json; charset=utf-8")
                    .timeout(TIMEOUT)
                    .POST(HttpRequest.BodyPublishers.ofString(jsonBody, StandardCharsets.UTF_8))
                    .build();

            HttpResponse<String> httpResp = httpClient.send(httpReq, HttpResponse.BodyHandlers.ofString());

            if (httpResp.statusCode() != 200) {
                String errorBody = httpResp.body();
                log.error("Python AI 服务返回异常: status={}, body={}", httpResp.statusCode(), errorBody);
                return Result.fail("AI 服务返回错误: " + errorBody);
            }

            Map<String, Object> pythonResp = objectMapper.readValue(httpResp.body(), Map.class);
            String answer = (String) pythonResp.getOrDefault("answer", "");
            Object sources = pythonResp.getOrDefault("sources", List.of());
            String sourcesJson = objectMapper.writeValueAsString(sources);

            // 保存问答历史
            QaHistory history = QaHistory.builder()
                    .userId(userId)
                    .sessionId(request.getSessionId())
                    .question(request.getQuestion())
                    .answer(answer)
                    .sources(sourcesJson)
                    .createUser(userId)
                    .build();
            qaHistoryMapper.insert(history);

            // 首次提问时自动设置会话标题
            if (request.getSessionId() != null) {
                String title = request.getQuestion();
                if (title.length() > TITLE_MAX_LENGTH) {
                    title = title.substring(0, TITLE_MAX_LENGTH) + "...";
                }
                qaSessionMapper.updateTitle(request.getSessionId(), title);
            }

            log.info("问答成功: userId={}, question={}", userId, request.getQuestion());
            return Result.ok(history);
        } catch (java.net.ConnectException e) {
            log.error("Python AI 服务连接失败: {}", e.getMessage());
            return Result.fail("AI 服务未启动，请先启动 Python 服务");
        } catch (Exception e) {
            log.error("问答失败", e);
            return Result.fail("问答处理失败: " + e.getMessage());
        }
    }

    @Override
    public Result getSessions() {
        Long userId = CurrentUser.get();
        List<QaSession> list = qaSessionMapper.selectByUserId(userId);
        return Result.ok(list);
    }

    @Override
    public Result createSession() {
        Long userId = CurrentUser.get();
        QaSession session = QaSession.builder()
                .userId(userId)
                .createUser(userId)
                .build();
        qaSessionMapper.insert(session);
        log.info("创建会话: userId={}, sessionId={}", userId, session.getId());
        return Result.ok(session);
    }

    @Override
    public Result history(Long sessionId) {
        Long userId = CurrentUser.get();
        List<QaHistory> list = qaHistoryMapper.selectBySessionId(sessionId, userId);
        return Result.ok(list);
    }

    @Override
    public Result delete(Long id, Long userId) {
        QaHistory history = qaHistoryMapper.selectByUserId(userId).stream()
                .filter(h -> h.getId().equals(id))
                .findFirst().orElse(null);
        if (history == null) {
            return Result.fail("记录不存在");
        }
        qaHistoryMapper.deleteById(id);
        return Result.ok();
    }

    @Override
    public Result deleteBatch(List<Long> ids, Long userId) {
        if (ids == null || ids.isEmpty()) {
            return Result.fail("请选择要删除的记录");
        }
        qaHistoryMapper.deleteBatch(ids, userId);
        return Result.ok();
    }

    @Override
    public Result deleteSession(Long sessionId) {
        Long userId = CurrentUser.get();
        QaSession session = qaSessionMapper.selectById(sessionId);
        if (session == null || !session.getUserId().equals(userId)) {
            return Result.fail("会话不存在");
        }
        qaHistoryMapper.deleteBySessionId(sessionId, userId);
        qaSessionMapper.deleteById(sessionId);
        log.info("删除会话及历史: sessionId={}, userId={}", sessionId, userId);
        return Result.ok();
    }
}
