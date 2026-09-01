package steveluxor.ragknowledgesystem.service.impl;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import jakarta.annotation.PreDestroy;
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
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

@Service
@Slf4j
public class QaServiceImpl implements QaService {

    private static final String ASK_PATH = "/qa/ask";
    private static final Duration TIMEOUT = Duration.ofSeconds(300);
    private static final int TITLE_MAX_LENGTH = 50;
    private static final long HEARTBEAT_INTERVAL_MS = 15_000L;

    private final String pythonBaseUrl;
    private final String callbackToken;
    private final QaHistoryMapper qaHistoryMapper;
    private final QaSessionMapper qaSessionMapper;
    private final DocumentMapper documentMapper;
    private final StringRedisTemplate redisTemplate;
    private final ObjectMapper objectMapper;
    private final HttpClient httpClient;
    private final MinioClient minioClient;
    private final String bucketName;
    // 专用有界线程池，避免阻塞公共 ForkJoinPool 线程
    private final ExecutorService sseExecutor;
    private final ScheduledExecutorService heartbeatScheduler;

    @Autowired
    public QaServiceImpl(
            QaHistoryMapper qaHistoryMapper,
            QaSessionMapper qaSessionMapper,
            DocumentMapper documentMapper,
            StringRedisTemplate redisTemplate,
            MinioClient minioClient,
            @org.springframework.beans.factory.annotation.Value("${minio.bucket-name}") String bucketName,
            @org.springframework.beans.factory.annotation.Value("${ai-service.python-base-url:http://localhost:8000}") String pythonBaseUrl,
            @org.springframework.beans.factory.annotation.Value("${ai-service.callback-token:}") String callbackToken) {
        this.qaHistoryMapper = qaHistoryMapper;
        this.qaSessionMapper = qaSessionMapper;
        this.documentMapper = documentMapper;
        this.redisTemplate = redisTemplate;
        this.minioClient = minioClient;
        this.bucketName = bucketName;
        this.pythonBaseUrl = pythonBaseUrl;
        this.callbackToken = callbackToken;
        this.sseExecutor = Executors.newCachedThreadPool();
        this.heartbeatScheduler = Executors.newSingleThreadScheduledExecutor();
        this.objectMapper = new ObjectMapper();
        this.objectMapper.registerModule(new JavaTimeModule());
        this.objectMapper.disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .version(HttpClient.Version.HTTP_1_1)
                .build();
    }

    @PreDestroy
    public void shutdown() {
        sseExecutor.shutdownNow();
        heartbeatScheduler.shutdownNow();
        log.info("[SSE] SSE executor 与心跳调度器已关闭");
    }

    // ==================== SSE 透传 ====================

    @Override
    public SseEmitter streamRuntime(String runId, String lastEventId) {
        Long userId = CurrentUser.get();
        return proxySse(pythonBaseUrl + "/qa/runtime/" + runId + "?user_id=" + userId,
                "runtime", runId, lastEventId);
    }

    @Override
    @SuppressWarnings("unchecked")
    public Result getActiveRuntime(Long sessionId) {
        Long userId = CurrentUser.get();
        QaSession session = qaSessionMapper.selectById(sessionId);
        if (session == null || !Objects.equals(session.getUserId(), userId)) {
            throw new BizException(QA_SESSION_NOT_EXIST);
        }
        try {
            String url = pythonBaseUrl + "/qa/active-runtime?session_id=" + sessionId + "&user_id=" + userId;
            HttpRequest request = HttpRequest.newBuilder()
                    .uri(URI.create(url))
                    .timeout(Duration.ofSeconds(10))
                    .GET()
                    .build();
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() != 200) {
                log.warn("[RUN] 查询活动运行失败: sessionId={}, status={}", sessionId, response.statusCode());
                return Result.fail("查询进行中的回答失败");
            }
            return Result.ok(objectMapper.readValue(response.body(), Map.class));
        } catch (Exception e) {
            log.error("[RUN] 查询活动运行异常: sessionId={}", sessionId, e);
            return Result.fail("查询进行中的回答失败");
        }
    }

    @Override
    public Result stop(String runId) {
        try {
            Long userId = CurrentUser.get();
            HttpRequest httpReq = HttpRequest.newBuilder()
                    .uri(URI.create(pythonBaseUrl + "/qa/stop"))
                    .header("Content-Type", "application/json; charset=utf-8")
                    .timeout(Duration.ofSeconds(10))
                    .POST(HttpRequest.BodyPublishers.ofString(
                            "{\"run_id\": \"" + runId + "\", \"user_id\": " + userId + "}", StandardCharsets.UTF_8))
                    .build();
            HttpResponse<String> httpResp = httpClient.send(httpReq, HttpResponse.BodyHandlers.ofString());
            if (httpResp.statusCode() == 200) {
                log.info("[STOP] 停止请求已发送: runId={}", runId);
                return Result.ok("停止请求已发送");
            }
            log.warn("[STOP] Python 返回异常: runId={}, status={}, body={}",
                    runId, httpResp.statusCode(), httpResp.body());
            return Result.fail("Python 停止失败: HTTP " + httpResp.statusCode());
        } catch (Exception e) {
            log.error("[STOP] 转发停止请求失败: runId={}, error={}", runId, e.getMessage());
            return Result.fail("停止失败: " + e.getMessage());
        }
    }

    /**
     * 通用 SSE 透传：连接 Python SSE 端点，逐行转发给前端
     * - 专用线程池执行，不占公共 ForkJoinPool
     * - 校验 Python 状态码：非 200 时发一条 runtime_error 事件，不把错误体当 SSE 转发
     * - 心跳：15s 定时发 comment 注释，避免长 Planner 阶段 nginx/前端提前断开
     */
    private SseEmitter proxySse(String pythonUrl, String streamType, String runId, String lastEventId) {
        SseEmitter emitter = new SseEmitter(300_000L); // 5 分钟超时

        ScheduledFuture<?> heartbeat = heartbeatScheduler.scheduleAtFixedRate(() -> {
            try {
                emitter.send(SseEmitter.event().comment("ping"));
            } catch (Exception ignored) {
                // 发送失败说明连接已断开，onCompletion/onError 会取消心跳
            }
        }, HEARTBEAT_INTERVAL_MS, HEARTBEAT_INTERVAL_MS, TimeUnit.MILLISECONDS);

        sseExecutor.execute(() -> {
            try {
                HttpRequest.Builder requestBuilder = HttpRequest.newBuilder()
                        .uri(URI.create(pythonUrl))
                        .header("Accept", "text/event-stream")
                        .timeout(Duration.ofSeconds(300));
                if (lastEventId != null && !lastEventId.isBlank()) {
                    requestBuilder.header("Last-Event-ID", lastEventId);
                }
                HttpRequest httpReq = requestBuilder.GET().build();

                HttpResponse<java.io.InputStream> response = httpClient.send(httpReq, HttpResponse.BodyHandlers.ofInputStream());
                int statusCode = response.statusCode();
                log.info("[SSE-{}] Python 连接成功, status={}", streamType, statusCode);

                if (statusCode != 200) {
                    // 校验状态码：向前端透传 runtime_error，而非把 Python 错误体当 SSE 转发
                    String errorBody = readErrorBody(response);
                    String errorMsg = "AI 服务返回错误 (HTTP " + statusCode + ")";
                    if (!errorBody.isBlank()) {
                        errorMsg += ": " + errorBody;
                    }
                    log.error("[SSE-{}] Python 返回非 200: runId={}, status={}", streamType, runId, statusCode);
                    Map<String, Object> payload = new HashMap<>();
                    payload.put("type", "runtime_error");
                    payload.put("data", Map.of("error", errorMsg));
                    try {
                        emitter.send(SseEmitter.event()
                                .name("runtime_error")
                                .data(objectMapper.writeValueAsString(payload)));
                    } catch (Exception sendEx) {
                        log.warn("[SSE-{}] 发送 runtime_error 失败: {}", streamType, sendEx.getMessage());
                    }
                    emitter.completeWithError(new RuntimeException("Python SSE 返回状态码 " + statusCode));
                    return;
                }

                try (BufferedReader reader = new BufferedReader(new InputStreamReader(response.body(), StandardCharsets.UTF_8))) {
                    String eventType = "message";
                    String eventId = null;
                    String data = null;
                    int eventCount = 0;
                    String line;
                    while ((line = reader.readLine()) != null) {
                        if (line.startsWith("event: ")) {
                            eventType = line.substring(7).trim();
                        } else if (line.startsWith("id: ")) {
                            eventId = line.substring(4).trim();
                        } else if (line.startsWith("data: ")) {
                            data = line.substring(6);
                        } else if (line.isEmpty() && data != null) {
                            eventCount++;
                            log.info("[SSE-{}] 转发事件 #{}: type={}", streamType, eventCount, eventType);
                            try {
                                SseEmitter.SseEventBuilder event = SseEmitter.event()
                                        .name(eventType)
                                        .data(data);
                                if (eventId != null && !eventId.isBlank()) {
                                    event.id(eventId);
                                }
                                emitter.send(event);
                            } catch (Exception e) {
                                log.warn("[SSE-{}] 转发失败: {}", streamType, e.getMessage());
                                break;
                            }
                            eventType = "message";
                            eventId = null;
                            data = null;
                        }
                    }
                    log.info("[SSE-{}] 流结束, 共转发 {} 个事件", streamType, eventCount);
                }
                emitter.complete();
            } catch (Exception e) {
                log.error("[SSE-{}] Python 连接失败: runId={}, error={}", streamType, runId, e.getMessage());
                try {
                    emitter.completeWithError(e);
                } catch (Exception ignored) {
                }
            } finally {
                heartbeat.cancel(false);
            }
        });

        emitter.onTimeout(() -> {
            log.warn("[SSE-{}] 超时: runId={}", streamType, runId);
            heartbeat.cancel(false);
        });
        emitter.onError(t -> {
            log.warn("[SSE-{}] 错误: runId={}, error={}", streamType, runId, t.getMessage());
            heartbeat.cancel(false);
        });
        emitter.onCompletion(() -> heartbeat.cancel(false));

        return emitter;
    }

    /**
     * 读取 Python 非 200 响应的错误体（最多 20 行）
     */
    private String readErrorBody(HttpResponse<java.io.InputStream> response) {
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(response.body(), StandardCharsets.UTF_8))) {
            return reader.lines().limit(20).collect(Collectors.joining("\n"));
        } catch (Exception e) {
            log.warn("[SSE] 读取错误体失败: {}", e.getMessage());
            return "";
        }
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
            Map<Integer, Integer> documentVersions = accessibleDocs.stream()
                    .filter(doc -> doc.getActiveIndexVersion() != null)
                    .collect(Collectors.toMap(doc -> doc.getId().intValue(), Document::getActiveIndexVersion));

            Map<String, Object> pythonReq = new HashMap<>();
            pythonReq.put("question", request.getQuestion());
            pythonReq.put("user_id", userId);
            if (!accessibleDocIds.isEmpty()) {
                pythonReq.put("document_ids", accessibleDocIds);
                pythonReq.put("document_versions", documentVersions);
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
        // 清除该记录所属 session 的 Redis 缓存，避免前端 history() 命中已删记录
        purgeSessionCache(history.getSessionId());
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
        // 清除受影响 session 的 Redis 缓存
        histories.stream()
                .map(QaHistory::getSessionId)
                .filter(Objects::nonNull)
                .distinct()
                .forEach(this::purgeSessionCache);
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
        purgeSessionCache(sessionId);

        log.info("删除会话及历史: sessionId={}, userId={}", sessionId, userId);
        return Result.ok();
    }

    /**
     * 清除 session 在 Redis 中的三类缓存（持久化记忆 / 历史 / 会话历史），
     * 避免删除后前端 history() 命中缓存看到已删记录。
     */
    private void purgeSessionCache(Long sessionId) {
        if (sessionId == null) return;
        String sid = String.valueOf(sessionId);
        redisTemplate.delete(QA_MEMORY_PREFIX + sid);
        redisTemplate.delete(QA_HISTORY_PREFIX + sid);
        redisTemplate.delete(QA_SESSION_HISTORY_PREFIX + sid);
        log.info("Redis 缓存已清除: sessionId={}", sid);
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
