package steveluxor.ragknowledgesystem.service.impl;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Service;
import steveluxor.ragknowledgesystem.entity.Document;
import steveluxor.ragknowledgesystem.mapper.DocumentMapper;
import steveluxor.ragknowledgesystem.service.DocumentSummaryCacheService;

import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;

import static steveluxor.ragknowledgesystem.common.Constants.DOCUMENT_SUMMARY_CACHE_PREFIX;
import static steveluxor.ragknowledgesystem.common.Constants.DOCUMENT_SUMMARY_EXPIRE_MAX_SECONDS;
import static steveluxor.ragknowledgesystem.common.Constants.DOCUMENT_SUMMARY_EXPIRE_MIN_SECONDS;
import static steveluxor.ragknowledgesystem.common.Constants.DOCUMENT_SUMMARY_REDIS_TTL_SECONDS;
import static steveluxor.ragknowledgesystem.common.Constants.DOCUMENT_SUMMARY_REFRESH_LOCK_PREFIX;
import static steveluxor.ragknowledgesystem.common.Constants.DOCUMENT_SUMMARY_REFRESH_LOCK_TTL_SECONDS;
import static steveluxor.ragknowledgesystem.common.Constants.DOCUMENT_SUMMARY_VERSION_PREFIX;

@Service
@Slf4j
public class DocumentSummaryCacheServiceImpl implements DocumentSummaryCacheService {
    private static final DefaultRedisScript<Long> RELEASE_LOCK_SCRIPT = new DefaultRedisScript<>(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
            Long.class);
    private static final DefaultRedisScript<Long> WRITE_IF_VERSION_SCRIPT = new DefaultRedisScript<>(
            "local version = redis.call('get', KEYS[2]); "
                    + "if (not version and ARGV[1] == '') or version == ARGV[1] then "
                    + "redis.call('set', KEYS[1], ARGV[2], 'EX', ARGV[3]); return 1 end; return 0",
            Long.class);

    private final StringRedisTemplate redisTemplate;
    private final DocumentMapper documentMapper;
    private final ObjectMapper objectMapper;

    public DocumentSummaryCacheServiceImpl(StringRedisTemplate redisTemplate,
                                           DocumentMapper documentMapper,
                                           ObjectMapper objectMapper) {
        this.redisTemplate = redisTemplate;
        this.documentMapper = documentMapper;
        this.objectMapper = objectMapper;
    }

    @Override
    public Map<Long, String> getSummaries(Collection<Long> documentIds) {
        List<Long> ids = normalizeIds(documentIds);
        if (ids.isEmpty()) {
            return Map.of();
        }

        try {
            return getCachedSummaries(ids);
        } catch (Exception e) {
            log.warn("文档摘要 Redis 读取失败，降级查询 MySQL: count={}", ids.size(), e);
            return loadSummaries(ids);
        }
    }

    @Override
    public void saveSummary(Long documentId, String summary) {
        if (documentId == null || summary == null || summary.isBlank()) {
            throw new IllegalArgumentException("documentId 和 summary 不能为空");
        }

        // 先推进版本，阻止已开始的旧刷新在本次写入后覆盖新摘要。
        advanceVersion(documentId);
        Document document = new Document();
        document.setId(documentId);
        document.setSummary(summary);
        documentMapper.updateDocument(document);
        writeCache(documentId, summary);
    }

    @Override
    public void invalidate(Long documentId) {
        if (documentId != null) {
            advanceVersion(documentId);
            deleteCache(documentId);
        }
    }

    private Map<Long, String> getCachedSummaries(List<Long> ids) {
        List<String> keys = ids.stream().map(this::cacheKey).toList();
        List<String> rawValues = redisTemplate.opsForValue().multiGet(keys);
        Map<Long, String> result = new LinkedHashMap<>();
        Map<Long, String> stale = new LinkedHashMap<>();
        List<Long> refreshIds = new ArrayList<>();
        long now = System.currentTimeMillis() / 1000;

        for (int i = 0; i < ids.size(); i++) {
            CacheEntry entry = parseEntry(rawValues == null ? null : rawValues.get(i));
            if (entry == null) {
                refreshIds.add(ids.get(i));
            } else if (entry.expireAt > now) {
                result.put(ids.get(i), entry.summary);
            } else {
                stale.put(ids.get(i), entry.summary);
                refreshIds.add(ids.get(i));
            }
        }

        if (!refreshIds.isEmpty()) {
            result.putAll(refreshExpired(refreshIds, stale));
        }
        return result;
    }

    private Map<Long, String> refreshExpired(List<Long> refreshIds, Map<Long, String> stale) {
        Map<Long, String> result = new LinkedHashMap<>();
        Map<Long, RefreshLock> locks = new LinkedHashMap<>();
        List<Long> waitForRefresh = new ArrayList<>();

        for (Long documentId : refreshIds) {
            String token = UUID.randomUUID().toString();
            Boolean acquired = redisTemplate.opsForValue().setIfAbsent(
                    lockKey(documentId), token, DOCUMENT_SUMMARY_REFRESH_LOCK_TTL_SECONDS, TimeUnit.SECONDS);
            if (Boolean.TRUE.equals(acquired)) {
                locks.put(documentId, new RefreshLock(token, currentVersion(documentId)));
            } else if (stale.containsKey(documentId)) {
                // 旧摘要可用于相关性过滤；持锁请求会负责异步式刷新，避免阻塞并发读取。
                result.put(documentId, stale.get(documentId));
            } else {
                waitForRefresh.add(documentId);
            }
        }

        try {
            Map<Long, String> loaded = loadSummaries(new ArrayList<>(locks.keySet()));
            List<Long> versionChanged = new ArrayList<>();
            for (Map.Entry<Long, String> entry : loaded.entrySet()) {
                RefreshLock lock = locks.get(entry.getKey());
                if (lock != null && writeCacheIfVersion(entry.getKey(), entry.getValue(), lock.version)) {
                    result.put(entry.getKey(), entry.getValue());
                } else {
                    versionChanged.add(entry.getKey());
                }
            }
            locks.keySet().stream()
                    .filter(documentId -> !loaded.containsKey(documentId))
                    .forEach(this::deleteCache);
            if (!versionChanged.isEmpty()) {
                result.putAll(readFreshEntries(versionChanged));
            }
        } finally {
            locks.forEach((documentId, lock) -> releaseLock(documentId, lock.token));
        }

        if (!waitForRefresh.isEmpty()) {
            result.putAll(waitForRefresh(waitForRefresh));
        }
        return result;
    }

    private Map<Long, String> waitForRefresh(List<Long> ids) {
        for (int attempt = 0; attempt < 3; attempt++) {
            try {
                Thread.sleep(50L);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }

            Map<Long, String> refreshed = readFreshEntries(ids);
            if (refreshed.size() == ids.size()) {
                return refreshed;
            }
        }
        return readFreshEntries(ids);
    }

    private Map<Long, String> readFreshEntries(List<Long> ids) {
        List<String> rawValues = redisTemplate.opsForValue().multiGet(ids.stream().map(this::cacheKey).toList());
        Map<Long, String> result = new LinkedHashMap<>();
        long now = System.currentTimeMillis() / 1000;
        for (int i = 0; i < ids.size(); i++) {
            CacheEntry entry = parseEntry(rawValues == null ? null : rawValues.get(i));
            if (entry != null && entry.expireAt > now) {
                result.put(ids.get(i), entry.summary);
            }
        }
        return result;
    }

    private Map<Long, String> loadSummaries(Collection<Long> ids) {
        List<Long> normalized = normalizeIds(ids);
        if (normalized.isEmpty()) {
            return Map.of();
        }

        Map<Long, String> result = new LinkedHashMap<>();
        for (Document document : documentMapper.selectSummariesByIds(normalized)) {
            if (document.getSummary() != null && !document.getSummary().isBlank()) {
                result.put(document.getId(), document.getSummary());
            }
        }
        return result;
    }

    private void writeCache(Long documentId, String summary) {
        try {
            redisTemplate.opsForValue().set(cacheKey(documentId), cacheValue(summary),
                    DOCUMENT_SUMMARY_REDIS_TTL_SECONDS, TimeUnit.SECONDS);
        } catch (Exception e) {
            log.warn("文档摘要写入 Redis 失败: documentId={}", documentId, e);
        }
    }

    private boolean writeCacheIfVersion(Long documentId, String summary, String expectedVersion) {
        try {
            Long result = redisTemplate.execute(WRITE_IF_VERSION_SCRIPT,
                    List.of(cacheKey(documentId), versionKey(documentId)),
                    expectedVersion == null ? "" : expectedVersion,
                    cacheValue(summary),
                    String.valueOf(DOCUMENT_SUMMARY_REDIS_TTL_SECONDS));
            return Long.valueOf(1L).equals(result);
        } catch (Exception e) {
            log.warn("文档摘要版本写入 Redis 失败: documentId={}", documentId, e);
            return false;
        }
    }

    private String cacheValue(String summary) throws Exception {
        long expireAt = System.currentTimeMillis() / 1000
                + ThreadLocalRandom.current().nextLong(
                DOCUMENT_SUMMARY_EXPIRE_MIN_SECONDS, DOCUMENT_SUMMARY_EXPIRE_MAX_SECONDS + 1);
        return objectMapper.writeValueAsString(Map.of("summary", summary, "expire_at", expireAt));
    }

    private CacheEntry parseEntry(String raw) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        try {
            JsonNode node = objectMapper.readTree(raw);
            JsonNode expireAt = node.get("expire_at");
            if (expireAt == null) {
                expireAt = node.get("expireAt");
            }
            String summary = node.path("summary").asText();
            if (expireAt == null || summary.isBlank()) {
                return null;
            }
            return new CacheEntry(summary, expireAt.asLong());
        } catch (Exception e) {
            return null;
        }
    }

    private List<Long> normalizeIds(Collection<Long> documentIds) {
        if (documentIds == null) {
            return List.of();
        }
        return documentIds.stream()
                .filter(id -> id != null && id > 0)
                .collect(java.util.stream.Collectors.toCollection(LinkedHashSet::new))
                .stream()
                .toList();
    }

    private String cacheKey(Long documentId) {
        return DOCUMENT_SUMMARY_CACHE_PREFIX + documentId;
    }

    private String lockKey(Long documentId) {
        return DOCUMENT_SUMMARY_REFRESH_LOCK_PREFIX + documentId;
    }

    private String versionKey(Long documentId) {
        return DOCUMENT_SUMMARY_VERSION_PREFIX + documentId;
    }

    private String currentVersion(Long documentId) {
        return redisTemplate.opsForValue().get(versionKey(documentId));
    }

    private void advanceVersion(Long documentId) {
        try {
            redisTemplate.opsForValue().increment(versionKey(documentId));
            redisTemplate.expire(versionKey(documentId), DOCUMENT_SUMMARY_REDIS_TTL_SECONDS, TimeUnit.SECONDS);
        } catch (Exception e) {
            log.warn("文档摘要 Redis 版本更新失败: documentId={}", documentId, e);
        }
    }

    private void deleteCache(Long documentId) {
        try {
            redisTemplate.delete(cacheKey(documentId));
        } catch (Exception e) {
            log.warn("文档摘要 Redis 失效失败: documentId={}", documentId, e);
        }
    }

    private void releaseLock(Long documentId, String token) {
        redisTemplate.execute(RELEASE_LOCK_SCRIPT, List.of(lockKey(documentId)), token);
    }

    private record CacheEntry(String summary, long expireAt) {
    }

    private record RefreshLock(String token, String version) {
    }
}
