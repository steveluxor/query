package steveluxor.ragknowledgesystem.service.impl;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Autowired;
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
    private static final String INGEST_PATH = "/ingest/document";

    private final String pythonBaseUrl;
    private final FileService fileService;
    private final DocumentMapper documentMapper;
    private final StringRedisTemplate redisTemplate;
    private final RabbitTemplate rabbitTemplate;
    private final HttpClient httpClient;

    @Autowired
    public DocumentServiceImpl(
            FileService fileService,
            DocumentMapper documentMapper,
            StringRedisTemplate redisTemplate,
            RabbitTemplate rabbitTemplate,
            @org.springframework.beans.factory.annotation.Value("${ai-service.python-base-url:http://localhost:8000}") String pythonBaseUrl) {
        this.fileService = fileService;
        this.documentMapper = documentMapper;
        this.redisTemplate = redisTemplate;
        this.rabbitTemplate = rabbitTemplate;
        this.pythonBaseUrl = pythonBaseUrl;
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .version(HttpClient.Version.HTTP_1_1)
                .build();
    }

    // ============================================
    // 公共方法
    // ============================================

    private void deleteVector(Long documentId) throws Exception {
        try {
            HttpRequest delReq = HttpRequest.newBuilder()
                    .uri(URI.create(pythonBaseUrl + INGEST_PATH + "/" + documentId))
                    .timeout(Duration.ofSeconds(30))
                    .DELETE()
                    .build();
            httpClient.send(delReq, HttpResponse.BodyHandlers.ofString());
            log.info("向量删除成功: documentId={}", documentId);
        } catch (Exception e) {
            log.error("向量删除失败: documentId={}", documentId, e);
            throw new Exception("向量删除失败: " + e.getMessage(), e);
        }
    }

    private void deleteMinioFile(String filePath) throws Exception {
        try {
            fileService.deleteFile(filePath);
            log.info("文件删除成功: filePath={}", filePath);
        } catch (Exception e) {
            log.error("文件删除失败: filePath={}", filePath, e);
            throw new Exception("文件删除失败: " + e.getMessage(), e);
        }
    }

    private String uploadToMinio(MultipartFile file) throws Exception {
        return fileService.uploadFile(
                file.getOriginalFilename(),
                file.getInputStream(),
                file.getSize(),
                file.getContentType());
    }

    private void sendIngestMessage(Long documentId, String filePath, String fileName) {
        Map<String, String> message = new HashMap<>();
        message.put("documentId", String.valueOf(documentId));
        message.put("filePath", filePath);
        message.put("fileName", fileName);
        rabbitTemplate.convertAndSend(RABBITMQ_INGEST_EXCHANGE, RABBITMQ_INGEST_ROUTING_KEY, message);
        log.info("向量化任务已写入队列: documentId={}", documentId);
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

            sendIngestMessage(docId, objectName, fileName);

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
    public Result deleteDocument(Long documentId, Long userId) throws Exception {
        Document document = documentMapper.selectById(documentId);
        if (document == null) {
            throw new BizException(FILE_NOT_EXIST);
        }
        if (!document.getUserId().equals(userId)) {
            throw new BizException(FILE_NO_PERMISSION);
        }

        deleteVector(documentId);
        deleteMinioFile(document.getFilePath());
        documentMapper.deleteById(documentId);

        log.info("文档删除成功: documentId={}", documentId);
        return Result.ok();
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

            sendIngestMessage(documentId, document.getFilePath(), document.getFileName());

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
            oldDoc.setFileName(file.getOriginalFilename());
            oldDoc.setPermission(permission);
            documentMapper.updateDocument(oldDoc);

            sendIngestMessage(oldDocId, objectName, file.getOriginalFilename());

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
