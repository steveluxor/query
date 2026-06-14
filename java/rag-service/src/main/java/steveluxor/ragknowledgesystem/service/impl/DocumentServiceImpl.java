package steveluxor.ragknowledgesystem.service.impl;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PostConstruct;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.connection.stream.MapRecord;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import steveluxor.ragknowledgesystem.common.Constants;
import steveluxor.ragknowledgesystem.common.Result;
import steveluxor.ragknowledgesystem.exception.BizException;
import steveluxor.ragknowledgesystem.entity.Document;
import steveluxor.ragknowledgesystem.mapper.DocumentMapper;
import steveluxor.ragknowledgesystem.service.DocumentService;
import steveluxor.ragknowledgesystem.service.FileService;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

import static steveluxor.ragknowledgesystem.common.Constants.*;

@Service
@Slf4j
public class DocumentServiceImpl implements DocumentService {
    private final String pythonBaseUrl;
    private final FileService fileService;
    private final DocumentMapper documentMapper;
    private final StringRedisTemplate redisTemplate;
    private final ObjectMapper objectMapper;
    private final HttpClient httpClient;

    @Autowired
    public DocumentServiceImpl(
            FileService fileService,
            DocumentMapper documentMapper,
            StringRedisTemplate redisTemplate,
            @org.springframework.beans.factory.annotation.Value("${ai-service.python-base-url:http://localhost:8000}") String pythonBaseUrl) {
        this.fileService = fileService;
        this.documentMapper = documentMapper;
        this.redisTemplate = redisTemplate;
        this.pythonBaseUrl = pythonBaseUrl;
        this.objectMapper = new ObjectMapper();
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .version(HttpClient.Version.HTTP_1_1)
                .build();
    }

    @PostConstruct
    public void initStreamGroup() {
        try {
            // 创建消费者组（如果不存在）
            redisTemplate.opsForStream().createGroup(STREAM_INGEST_KEY, STREAM_INGEST_GROUP);
            log.info("Stream 消费者组初始化完成");
        } catch (Exception e) {
            // 消费者组已存在，忽略错误
            log.info("Stream 消费者组已存在");
        }
    }

    // ============================================
    // 公共方法
    // ============================================

    private void deleteVector(Long documentId) {
        try {
            HttpRequest delReq = HttpRequest.newBuilder()
                    .uri(URI.create(pythonBaseUrl + INGEST_PATH + "/" + documentId))
                    .timeout(Duration.ofSeconds(30))
                    .DELETE()
                    .build();
            httpClient.send(delReq, HttpResponse.BodyHandlers.ofString());
            log.info("向量删除成功: documentId={}", documentId);
        } catch (Exception e) {
            log.warn("向量删除失败（不影响后续操作）: documentId={}", documentId, e);
        }
    }

    private void deleteMinioFile(String filePath) {
        try {
            fileService.deleteFile(filePath);
            log.info("文件删除成功: filePath={}", filePath);
        } catch (Exception e) {
            log.warn("文件删除失败（不影响后续操作）: filePath={}", filePath, e);
        }
    }

    private String uploadToMinio(MultipartFile file) throws Exception {
        return fileService.uploadFile(
                file.getOriginalFilename(),
                file.getInputStream(),
                file.getSize(),
                file.getContentType());
    }


    // 上传文档
    // 0: 公开权限
    // 1: 私有权限
    @Override
    public Result uploadDocument(MultipartFile file, Long userId, Integer permission) {
        if (file.isEmpty()) {
            throw new BizException(FILE_NOT_EMPTY);
        }

        // 分布式锁：防止同名文件同时上传
        String fileName = file.getOriginalFilename();
        String lockKey = FILE_LOCK_PREFIX + fileName;
        String lockValue = UUID.randomUUID().toString();

        Boolean locked = redisTemplate.opsForValue().setIfAbsent(lockKey, lockValue, FILE_LOCK_TTL, TimeUnit.SECONDS);
        if (!Boolean.TRUE.equals(locked)) {
            throw new BizException(FILE_NAME_EXISTS);
        }

        try {
            String objectName = uploadToMinio(file);

            Document document = Document.builder()
                    .userId(userId)
                    .fileName(fileName)
                    .filePath(objectName)
                    .fileSize(file.getSize())
                    .fileType(file.getContentType())
                    .status(DOC_STATUS_UPLOADED)
                    .permission(permission)
                    .build();
            documentMapper.insert(document);
            Long docId = document.getId();

            // 写入 Redis Stream，异步处理向量化
            Map<String, String> message = new HashMap<>();
            message.put("documentId", String.valueOf(docId));
            message.put("filePath", objectName);
            message.put("fileName", fileName);
            redisTemplate.opsForStream().add(STREAM_INGEST_KEY, message);
            log.info("文档上传成功，已写入 Stream: documentId={}", docId);

            // 重新查询以获取数据库自动填充的字段（createTime、updateTime）
            Document saved = documentMapper.selectById(docId);

            return Result.ok(saved);
        } catch (Exception e) {
            log.error(DOC_UPLOAD_FAILED, e);
            throw new BizException(DOC_UPLOAD_FAILED + ": " + e.getMessage());
        } finally {
            redisTemplate.delete(lockKey);
        }
    }

    // 获取文档URL
    @Override
    public Result getDocumentUrl(Long documentId) {
        Document document = documentMapper.selectById(documentId);
        if (document == null) {
            throw new BizException(FILE_NOT_EXIST);
        }

        try {
            String url = fileService.getFileUrl(document.getFilePath());
            return Result.ok(url);
        } catch (Exception e) {
            log.error(DOC_GET_URL_FAILED, e);
            throw new BizException(DOC_GET_URL_FAILED);
        }
    }

    @Override
    public Result listDocuments(Long userId) {
        log.info("查询文档列表: userId={}", userId);
        List<Document> list = documentMapper.selectByUserId(userId);
        return Result.ok(list);
    }

    /**
     *
     * @param documentId
     * @param userId
     * @return
     */
    @Override
    public Result deleteDocument(Long documentId, Long userId) {
        Document document = documentMapper.selectById(documentId);
        if (document == null) {
            throw new BizException(FILE_NOT_EXIST);
        }
        if (!document.getUserId().equals(userId)) {
            throw new BizException(FILE_NO_PERMISSION);
        }

        try {
            deleteVector(documentId);
            deleteMinioFile(document.getFilePath());
            documentMapper.deleteById(documentId);

            log.info("文档删除成功: documentId={}", documentId);
            return Result.ok();
        } catch (Exception e) {
            log.error(DOC_DELETE_FAILED, e);
            throw new BizException(DOC_DELETE_FAILED);
        }
    }

    @Override
    public Result reIngestDocument(Long documentId, Long userId) {
        Document document = documentMapper.selectById(documentId);
        if (document == null) {
            throw new BizException(FILE_NOT_EXIST);
        }
        if (!document.getUserId().equals(userId)) {
            throw new BizException(FILE_NO_PERMISSION);
        }

        try {
            deleteVector(documentId);
            documentMapper.updateStatus(documentId, Constants.DOC_STATUS_PROCESSING);

            // 写入 Redis Stream，异步处理向量化
            Map<String, String> message = new HashMap<>();
            message.put("documentId", String.valueOf(documentId));
            message.put("filePath", document.getFilePath());
            message.put("fileName", document.getFileName());
            redisTemplate.opsForStream().add(STREAM_INGEST_KEY, message);
            log.info("重新向量化任务已写入 Stream: documentId={}", documentId);

            return Result.ok();
        } catch (Exception e) {
            documentMapper.updateStatus(documentId, Constants.DOC_STATUS_FAILED);
            log.error("重新向量化异常: documentId={}", documentId, e);
            throw new BizException(DOC_UPLOAD_FAILED);
        }
    }

    @Override
    public Result checkDuplicate(String fileName, Long userId) {
        List<Document> docs = documentMapper.selectByFileName(fileName);
        if (docs.isEmpty()) {
            return Result.ok(Map.of("exists", false));
        }

        Document existing = docs.get(0);
        boolean isOwner = existing.getUserId().equals(userId);
        return Result.ok(Map.of(
                "exists", true,
                "isOwner", isOwner,
                "existingId", existing.getId()
        ));
    }

    @Override
    public Result overwriteDocument(Long oldDocId, MultipartFile file, Long userId, Integer permission) {
        Document oldDoc = documentMapper.selectById(oldDocId);
        if (oldDoc == null) {
            throw new BizException(FILE_NOT_EXIST);
        }
        if (!oldDoc.getUserId().equals(userId)) {
            throw new BizException(FILE_NO_PERMISSION);
        }

        try {
            deleteVector(oldDocId);
            deleteMinioFile(oldDoc.getFilePath());

            String objectName = uploadToMinio(file);

            oldDoc.setFilePath(objectName);
            oldDoc.setFileSize(file.getSize());
            oldDoc.setFileType(file.getContentType());
            oldDoc.setStatus(DOC_STATUS_UPLOADED);
            oldDoc.setPermission(permission);
            documentMapper.updateDocument(oldDoc);

            // 写入 Redis Stream，异步处理向量化
            Map<String, String> message = new HashMap<>();
            message.put("documentId", String.valueOf(oldDocId));
            message.put("filePath", objectName);
            message.put("fileName", file.getOriginalFilename());
            redisTemplate.opsForStream().add(STREAM_INGEST_KEY, message);
            log.info("覆盖上传任务已写入 Stream: documentId={}", oldDocId);

            Document saved = documentMapper.selectById(oldDocId);
            return Result.ok(saved);
        } catch (Exception e) {
            documentMapper.updateStatus(oldDocId, Constants.DOC_STATUS_FAILED);
            log.error("覆盖上传异常: documentId={}", oldDocId, e);
            throw new BizException(DOC_UPLOAD_FAILED + ": " + e.getMessage());
        }
    }

    @Override
    public void updateDocumentStatus(Long documentId, String status) {
        documentMapper.updateStatus(documentId, status);
        log.info("文档状态已更新: documentId={}, status={}", documentId, status);
    }
}
