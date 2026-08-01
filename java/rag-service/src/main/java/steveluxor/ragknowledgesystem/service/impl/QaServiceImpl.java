package steveluxor.ragknowledgesystem.service.impl;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import steveluxor.ragknowledgesystem.common.CurrentUser;
import steveluxor.ragknowledgesystem.common.Result;
import steveluxor.ragknowledgesystem.dto.AskRequest;
import steveluxor.ragknowledgesystem.exception.BizException;

import static steveluxor.ragknowledgesystem.common.Constants.*;

import steveluxor.ragknowledgesystem.entity.Document;
import steveluxor.ragknowledgesystem.entity.QaHistory;
import steveluxor.ragknowledgesystem.entity.QaSession;
import steveluxor.ragknowledgesystem.mapper.DocumentMapper;
import steveluxor.ragknowledgesystem.mapper.QaHistoryMapper;
import steveluxor.ragknowledgesystem.mapper.QaSessionMapper;
import steveluxor.ragknowledgesystem.service.QaService;

import io.minio.MinioClient;
import io.minio.PutObjectArgs;
import io.minio.RemoveObjectArgs;

import java.io.BufferedReader;
import java.io.ByteArrayInputStream;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import org.springframework.util.DigestUtils;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.concurrent.CompletableFuture;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

@Service
@Slf4j
public class QaServiceImpl implements QaService {

    private static final String ASK_PATH = "/qa/ask";
    private static final Duration TIMEOUT = Duration.ofSeconds(300);
    private static final int TITLE_MAX_LENGTH = 50;

    private final String pythonBaseUrl;
    private final QaHistoryMapper qaHistoryMapper;
    private final QaSessionMapper qaSessionMapper;
    private final DocumentMapper documentMapper;
    private final StringRedisTemplate redisTemplate;
    private final ObjectMapper objectMapper;
    private final HttpClient httpClient;
    private final MinioClient minioClient;
    private final String bucketName;

    @Autowired
    public QaServiceImpl(
            QaHistoryMapper qaHistoryMapper,
            QaSessionMapper qaSessionMapper,
            DocumentMapper documentMapper,
            StringRedisTemplate redisTemplate,
            MinioClient minioClient,
            @org.springframework.beans.factory.annotation.Value("${minio.bucket-name}") String bucketName,
            @org.springframework.beans.factory.annotation.Value("${ai-service.python-base-url:http://localhost:8000}") String pythonBaseUrl) {
        this.qaHistoryMapper = qaHistoryMapper;
        this.qaSessionMapper = qaSessionMapper;
        this.documentMapper = documentMapper;
        this.redisTemplate = redisTemplate;
        this.minioClient = minioClient;
        this.bucketName = bucketName;
        this.pythonBaseUrl = pythonBaseUrl;
        this.objectMapper = new ObjectMapper();
        this.objectMapper.registerModule(new JavaTimeModule());
        this.objectMapper.disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .version(HttpClient.Version.HTTP_1_1)
                .build();
    }

    // ==================== SSE 透传 ====================

    @Override
    public SseEmitter streamRuntime(String runId) {
        return proxySse(pythonBaseUrl + "/qa/runtime/" + runId, "runtime", runId);
    }

    @Override
    public SseEmitter streamAnswer(String runId) {
        return proxySse(pythonBaseUrl + "/qa/answer/" + runId, "answer", runId);
    }

    /**
     * 通用 SSE 透传：连接 Python SSE 端点，逐行转发给前端
     */
    private SseEmitter proxySse(String pythonUrl, String streamType, String runId) {
        SseEmitter emitter = new SseEmitter(300_000L); // 5 分钟超时

        CompletableFuture.runAsync(() -> {
            try {
                HttpRequest httpReq = HttpRequest.newBuilder()
                        .uri(URI.create(pythonUrl))
                        .header("Accept", "text/event-stream")
                        .timeout(Duration.ofSeconds(300))
                        .GET()
                        .build();

                // 使用 ofInputStream + BufferedReader 实现逐行流式转发
                // BodyHandlers.ofLines() 会缓冲整个响应体，不适合 SSE
                HttpResponse<java.io.InputStream> response = httpClient.send(httpReq, HttpResponse.BodyHandlers.ofInputStream());
                try (BufferedReader reader = new BufferedReader(new InputStreamReader(response.body(), StandardCharsets.UTF_8))) {
                    String line;
                    while ((line = reader.readLine()) != null) {
                        if (line.startsWith("data: ")) {
                            String data = line.substring(6);
                            try {
                                emitter.send(SseEmitter.event()
                                        .name("message")
                                        .data(data));
                            } catch (Exception e) {
                                log.warn("[SSE-{}] 转发失败: {}", streamType, e.getMessage());
                                break;
                            }
                        }
                    }
                }
                emitter.complete();
            } catch (Exception e) {
                log.error("[SSE-{}] Python 连接失败: runId={}, error={}", streamType, runId, e.getMessage());
                try {
                    emitter.completeWithError(e);
                } catch (Exception ignored) {
                }
            }
        });

        emitter.onTimeout(() -> log.warn("[SSE-{}] 超时: runId={}", streamType, runId));
        emitter.onError(t -> log.warn("[SSE-{}] 错误: runId={}, error={}", streamType, runId, t.getMessage()));

        return emitter;
    }

    // ==================== Python Callback 持久化 ====================

    @Override
    @SuppressWarnings("unchecked")
    public Result handleCallback(Map<String, Object> body) {
        try {
            Long sessionId = body.get("session_id") != null
                    ? Long.valueOf(String.valueOf(body.get("session_id"))) : null;
            Map<String, Object> result = (Map<String, Object>) body.get("result");
            if (result == null) {
                return Result.fail("callback body 缺少 result");
            }

            // 从 session 查 userId
            if (sessionId == null) {
                return Result.fail("callback body 缺少 session_id");
            }
            QaSession session = qaSessionMapper.selectById(sessionId);
            if (session == null) {
                return Result.fail("session 不存在: " + sessionId);
            }
            Long userId = session.getUserId();

            String answer = (String) result.getOrDefault("answer", "");
            Object sources = result.getOrDefault("sources", List.of());
            String sourcesJson = objectMapper.writeValueAsString(sources);
            Boolean isAgg = (Boolean) result.getOrDefault("is_agg", false);
            Object plan = result.get("plan");
            Object agentTrace = result.get("agent_trace");
            String generatedCode = (String) result.getOrDefault("generated_code", "");
            String codeStdout = (String) result.getOrDefault("code_stdout", "");
            String codeError = (String) result.getOrDefault("code_error", "");
            Boolean codeSuccess = (Boolean) result.getOrDefault("code_success", true);
            String question = (String) result.getOrDefault("question", "");

            // 1. 写入 AgentMemory 快照到 Redis
            Object memoryData = result.get("memory_data");
            if (memoryData != null) {
                String memoryJson = objectMapper.writeValueAsString(memoryData);
                String memoryKey = QA_MEMORY_PREFIX + sessionId;
                redisTemplate.opsForValue().set(memoryKey, memoryJson, QA_MEMORY_TTL_SECONDS, TimeUnit.SECONDS);
                log.info("[Callback] AgentMemory 写入 Redis: sessionId={}", sessionId);

                // preferences 变化写入数据库
                try {
                    Map<String, Object> memMap = (Map<String, Object>) memoryData;
                    Object prefDirtyObj = memMap.get("preferences_dirty");
                    boolean prefDirty = prefDirtyObj instanceof Boolean
                            ? (Boolean) prefDirtyObj
                            : prefDirtyObj instanceof Number
                            ? ((Number) prefDirtyObj).intValue() != 0
                            : Boolean.parseBoolean(String.valueOf(prefDirtyObj));
                    if (prefDirty) {
                        Object prefs = memMap.get("preferences");
                        if (prefs != null) {
                            qaSessionMapper.updatePreferences(sessionId, objectMapper.writeValueAsString(prefs));
                            log.info("[Callback] preferences 写入数据库: sessionId={}", sessionId);
                        }
                    }
                } catch (Exception e) {
                    log.warn("[Callback] memory_data 结构异常，跳过 preferences: {}", e.getMessage());
                }
            }

            // 2. 写入对话历史到 Redis
            Map<String, Object> historyItem = new HashMap<>();
            historyItem.put("question", question);
            historyItem.put("answer", answer);
            historyItem.put("is_agg", isAgg);
            String historyKey = QA_HISTORY_PREFIX + sessionId;
            redisTemplate.opsForList().rightPush(historyKey, objectMapper.writeValueAsString(historyItem));
            redisTemplate.expire(historyKey, QA_MEMORY_TTL_SECONDS, TimeUnit.SECONDS);

            // 3. 上传 base64 图片到 MinIO
            String imageUrlsJson = null;
            Object imageUrlsObj = result.get("image_urls");
            if (imageUrlsObj instanceof List<?> imageList && !imageList.isEmpty()) {
                List<String> minioObjectNames = new ArrayList<>();
                for (Object item : imageList) {
                    if (item instanceof String base64Str && !base64Str.isEmpty()) {
                        try {
                            byte[] imageBytes = Base64.getDecoder().decode(base64Str);
                            String objectName = "charts/" + UUID.randomUUID() + ".png";
                            minioClient.putObject(PutObjectArgs.builder()
                                    .bucket(bucketName)
                                    .object(objectName)
                                    .stream(new ByteArrayInputStream(imageBytes), imageBytes.length, -1)
                                    .contentType("image/png")
                                    .build());
                            minioObjectNames.add(objectName);
                        } catch (Exception e) {
                            log.warn("[Callback] 图片上传 MinIO 失败: {}", e.getMessage());
                        }
                    }
                }
                if (!minioObjectNames.isEmpty()) {
                    imageUrlsJson = objectMapper.writeValueAsString(minioObjectNames);
                }
            }

            // 4. 保存 QaHistory 到 MySQL
            String planJson = plan != null ? objectMapper.writeValueAsString(plan) : null;
            String agentTraceJson = agentTrace != null ? objectMapper.writeValueAsString(agentTrace) : null;

            QaHistory history = QaHistory.builder()
                    .userId(userId)
                    .sessionId(sessionId)
                    .question(question)
                    .answer(answer)
                    .sources(sourcesJson)
                    .isAgg(isAgg)
                    .imageUrls(imageUrlsJson)
                    .plan(planJson)
                    .agentTrace(agentTraceJson)
                    .generatedCode(generatedCode != null && !generatedCode.isEmpty() ? generatedCode : null)
                    .codeStdout(codeStdout != null && !codeStdout.isEmpty() ? codeStdout : null)
                    .codeError(codeError != null && !codeError.isEmpty() ? codeError : null)
                    .codeSuccess(codeSuccess)
                    .createUser(userId)
                    .build();
            qaHistoryMapper.insert(history);

            // 5. 写入 Redis 会话历史缓存
            String sessionHistoryKey = QA_SESSION_HISTORY_PREFIX + sessionId;
            String historyFullJson = objectMapper.writeValueAsString(history);
            redisTemplate.opsForList().rightPush(sessionHistoryKey, historyFullJson);
            redisTemplate.expire(sessionHistoryKey, QA_SESSION_HISTORY_TTL_SECONDS, TimeUnit.SECONDS);

            // 6. 更新会话标题
            if (question != null && !question.isEmpty()) {
                String title = question.length() > TITLE_MAX_LENGTH
                        ? question.substring(0, TITLE_MAX_LENGTH) + "..." : question;
                qaSessionMapper.updateTitle(sessionId, title);
            }

            log.info("[Callback] 持久化完成: sessionId={}, userId={}", sessionId, userId);
            return Result.ok();
        } catch (Exception e) {
            log.error("[Callback] 持久化失败", e);
            return Result.fail("持久化失败: " + e.getMessage());
        }
    }

    @Override
    public Result ask(AskRequest request) {
        Long userId = CurrentUser.get();
        try {
            // 1. 构建 Python 请求（document_ids, session_id, history, preferences）
            List<Document> accessibleDocs = documentMapper.selectByUserId(userId);
            List<Integer> accessibleDocIds = accessibleDocs.stream()
                    .map(doc -> doc.getId().intValue())
                    .collect(Collectors.toList());

            Map<String, Object> pythonReq = new HashMap<>();
            pythonReq.put("question", request.getQuestion());
            if (!accessibleDocIds.isEmpty()) {
                pythonReq.put("document_ids", accessibleDocIds);
            }
            if (request.getStrategy() != null) {
                pythonReq.put("strategy", request.getStrategy());
            }

            if (request.getSessionId() != null) {
                String sessionIdStr = String.valueOf(request.getSessionId());
                pythonReq.put("session_id", sessionIdStr);

                // Redis 无记忆时发送全量历史供 Python 重建 AgentMemory
                String memoryKey = QA_MEMORY_PREFIX + sessionIdStr;
                Boolean memoryExists = redisTemplate.hasKey(memoryKey);

                if (!Boolean.TRUE.equals(memoryExists)) {
                    QaSession session = qaSessionMapper.selectById(request.getSessionId());
                    if (session != null && session.getPreferences() != null) {
                        pythonReq.put("preferences", objectMapper.readValue(session.getPreferences(), Map.class));
                    }
                    List<QaHistory> allHistory = qaHistoryMapper.selectBySessionId(request.getSessionId(), userId);
                    if (!allHistory.isEmpty()) {
                        List<Map<String, Object>> historyList = allHistory.stream()
                                .map(h -> {
                                    Map<String, Object> item = new HashMap<>();
                                    item.put("question", h.getQuestion());
                                    item.put("answer", h.getAnswer());
                                    item.put("is_agg", Boolean.TRUE.equals(h.getIsAgg()));
                                    return item;
                                })
                                .collect(Collectors.toList());
                        pythonReq.put("history", historyList);
                        log.info("Redis 无记忆，发送全量历史: sessionId={}, count={}", sessionIdStr, historyList.size());
                    }
                }
            }

            String jsonBody = objectMapper.writeValueAsString(pythonReq);
            log.info("发送到 Python: body={}", jsonBody);

            // 2. 调用 Python /qa/ask — 立即返回 {run_id, session_id}
            HttpRequest httpReq = HttpRequest.newBuilder()
                    .uri(URI.create(pythonBaseUrl + ASK_PATH))
                    .header("Content-Type", "application/json; charset=utf-8")
                    .timeout(Duration.ofSeconds(30))
                    .POST(HttpRequest.BodyPublishers.ofString(jsonBody, StandardCharsets.UTF_8))
                    .build();

            HttpResponse<String> httpResp = httpClient.send(httpReq, HttpResponse.BodyHandlers.ofString());

            if (httpResp.statusCode() != 200) {
                String errorBody = httpResp.body();
                log.error("Python AI 服务返回异常: status={}, body={}", httpResp.statusCode(), errorBody);
                throw new BizException(AI_SERVICE_ERROR_PREFIX + errorBody);
            }

            // 3. 透传 run_id + session_id 给前端，持久化由 Python callback 完成
            Map<String, Object> pythonResp = objectMapper.readValue(httpResp.body(), Map.class);
            String runId = (String) pythonResp.get("run_id");
            Object sessionId = pythonResp.get("session_id");

            log.info("问答已提交: userId={}, runId={}, sessionId={}", userId, runId, sessionId);

            Map<String, Object> resultData = new HashMap<>();
            resultData.put("run_id", runId);
            resultData.put("session_id", sessionId);
            return Result.ok(resultData);

        } catch (java.net.ConnectException e) {
            log.error("Python AI 服务连接失败: {}", e.getMessage());
            throw new BizException(AI_SERVICE_NOT_STARTED);
        } catch (Exception e) {
            log.error("问答失败", e);
            throw new BizException(QA_PROCESS_FAILED_PREFIX + e.getMessage());
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

        // 1. 先查 Redis 会话历史缓存
        String sessionHistoryKey = QA_SESSION_HISTORY_PREFIX + sessionId;
        List<String> cached = redisTemplate.opsForList().range(sessionHistoryKey, 0, -1);
        if (cached != null && !cached.isEmpty()) {
            log.info("会话历史缓存命中: sessionId={}", sessionId);
            List<QaHistory> list = cached.stream()
                    .map(json -> {
                        try { return objectMapper.readValue(json, QaHistory.class); }
                        catch (Exception e) { log.warn("解析缓存失败: {}", e.getMessage()); return null; }
                    })
                    .filter(h -> h != null)
                    .collect(Collectors.toList());
            return Result.ok(list);
        }

        // 2. Redis 没有，查 MySQL
        List<QaHistory> list = qaHistoryMapper.selectBySessionId(sessionId, userId);

        // 3. 回填 Redis
        if (!list.isEmpty()) {
            list.forEach(h -> {
                try {
                    String json = objectMapper.writeValueAsString(h);
                    redisTemplate.opsForList().rightPush(sessionHistoryKey, json);
                } catch (Exception e) {
                    log.warn("回填 Redis 失败: {}", e.getMessage());
                }
            });
            redisTemplate.expire(sessionHistoryKey, QA_SESSION_HISTORY_TTL_SECONDS, TimeUnit.SECONDS);
            log.info("会话历史回填 Redis: sessionId={}, count={}", sessionId, list.size());
        }

        return Result.ok(list);
    }

    @Override
    public Result delete(Long id, Long userId) {
        QaHistory history = qaHistoryMapper.selectByUserId(userId).stream()
                .filter(h -> h.getId().equals(id))
                .findFirst().orElse(null);
        if (history == null) {
            throw new BizException(QA_RECORD_NOT_EXIST);
        }
        deleteMinioImages(history);
        qaHistoryMapper.deleteById(id);
        return Result.ok();
    }

    @Override
    public Result deleteBatch(List<Long> ids, Long userId) {
        if (ids == null || ids.isEmpty()) {
            throw new BizException(QA_SELECT_RECORD_FIRST);
        }
        // 先查出记录，删除 MinIO 图片
        List<QaHistory> histories = qaHistoryMapper.selectByUserId(userId).stream()
                .filter(h -> ids.contains(h.getId()))
                .collect(Collectors.toList());
        histories.forEach(this::deleteMinioImages);
        qaHistoryMapper.deleteBatch(ids, userId);
        return Result.ok();
    }

    @Transactional(rollbackFor = Exception.class)
    @Override
    public Result deleteSession(Long sessionId) {
        Long userId = CurrentUser.get();
        QaSession session = qaSessionMapper.selectById(sessionId);
        if (session == null || !session.getUserId().equals(userId)) {
            throw new BizException(QA_SESSION_NOT_EXIST);
        }
        // 先查出记录，删除 MinIO 图片
        List<QaHistory> histories = qaHistoryMapper.selectBySessionId(sessionId, userId);
        histories.forEach(this::deleteMinioImages);
        qaHistoryMapper.deleteBySessionId(sessionId, userId);
        qaSessionMapper.deleteById(sessionId);

        // 清除 Redis 中的持久化记忆
        String sessionIdStr = String.valueOf(sessionId);
        redisTemplate.delete(QA_MEMORY_PREFIX + sessionIdStr);
        redisTemplate.delete(QA_HISTORY_PREFIX + sessionIdStr);
        redisTemplate.delete(QA_SESSION_HISTORY_PREFIX + sessionIdStr);
        log.info("Redis 记忆已清除: sessionId={}", sessionIdStr);

        log.info("删除会话及历史: sessionId={}, userId={}", sessionId, userId);
        return Result.ok();
    }

    /**
     * 删除问答记录关联的 MinIO 图片
     */
    private void deleteMinioImages(QaHistory history) {
        if (history == null || history.getImageUrls() == null) return;
        try {
            List<String> objectNames = objectMapper.readValue(history.getImageUrls(),
                    objectMapper.getTypeFactory().constructCollectionType(List.class, String.class));
            for (String objName : objectNames) {
                try {
                    minioClient.removeObject(RemoveObjectArgs.builder()
                            .bucket(bucketName).object(objName).build());
                    log.info("删除 MinIO 图片: {}", objName);
                } catch (Exception e) {
                    log.warn("删除 MinIO 图片失败: {}", objName, e);
                }
            }
        } catch (Exception e) {
            log.warn("解析 imageUrls 失败: {}", e.getMessage());
        }
    }
}
