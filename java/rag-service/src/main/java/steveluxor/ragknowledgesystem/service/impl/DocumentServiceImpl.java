package steveluxor.ragknowledgesystem.service.impl;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import steveluxor.ragknowledgesystem.common.Constants;
import steveluxor.ragknowledgesystem.common.Result;
import steveluxor.ragknowledgesystem.exception.BizException;
import steveluxor.ragknowledgesystem.entity.Document;
import steveluxor.ragknowledgesystem.mapper.DocumentMapper;
import steveluxor.ragknowledgesystem.service.DocumentService;
import steveluxor.ragknowledgesystem.service.FileService;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Map;

import static steveluxor.ragknowledgesystem.common.Constants.*;

@Service
@Slf4j
public class DocumentServiceImpl implements DocumentService {
    private static final String INGEST_PATH = "/ingest/document";
    private static final Duration INGEST_TIMEOUT = Duration.ofSeconds(120);

    private final String pythonBaseUrl;
    private final FileService fileService;
    private final DocumentMapper documentMapper;
    private final ObjectMapper objectMapper;
    private final HttpClient httpClient;

    @Autowired
    public DocumentServiceImpl(
            FileService fileService,
            DocumentMapper documentMapper,
            @org.springframework.beans.factory.annotation.Value("${ai-service.python-base-url:http://localhost:8000}") String pythonBaseUrl) {
        this.fileService = fileService;
        this.documentMapper = documentMapper;
        this.pythonBaseUrl = pythonBaseUrl;
        this.objectMapper = new ObjectMapper();
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .version(HttpClient.Version.HTTP_1_1)
                .build();
    }

    // 上传文档
    // 0: 公开权限
    // 1: 私有权限
    @Override
    public Result uploadDocument(MultipartFile file, Long userId, Integer permission) {
        if (file.isEmpty()) {
            throw new BizException(FILE_NOT_EMPTY);
        }

        File tempFile = null;
        try {
            String objectName = fileService.uploadFile(
                    file.getOriginalFilename(),
                    file.getInputStream(),
                    file.getSize(),
                    file.getContentType());

            Document document = Document.builder()
                    .userId(userId)
                    .fileName(file.getOriginalFilename())
                    .filePath(objectName)
                    .fileSize(file.getSize())
                    .fileType(file.getContentType())
                    .status(DOC_STATUS_UPLOADED)
                    .permission(permission)
                    .build();
            documentMapper.insert(document);
            Long docId = document.getId();

            // 触发向量化：从 MinIO 下载文件到临时目录，调用 Python AI 服务解析并存入 Chroma
            try {
                String fileName = file.getOriginalFilename();
                String suffix = "";
                if (fileName != null && fileName.contains(".")) {
                    suffix = fileName.substring(fileName.lastIndexOf("."));
                }
                tempFile = File.createTempFile("rag_", suffix);

                try (InputStream minioStream = fileService.getFileInputStream(objectName);
                     FileOutputStream fos = new FileOutputStream(tempFile)) {
                    minioStream.transferTo(fos);
                }

                Map<String, Object> ingestReq = Map.of(
                        "file_path", tempFile.getAbsolutePath(),
                        "document_id", docId,
                        "file_name", fileName != null ? fileName : "unknown"
                );
                String jsonBody = objectMapper.writeValueAsString(ingestReq);

                HttpRequest httpReq = HttpRequest.newBuilder()
                        .uri(URI.create(pythonBaseUrl + INGEST_PATH))
                        .header("Content-Type", "application/json; charset=utf-8")
                        .timeout(INGEST_TIMEOUT)
                        .POST(HttpRequest.BodyPublishers.ofString(jsonBody, StandardCharsets.UTF_8))
                        .build();

                HttpResponse<String> httpResp = httpClient.send(httpReq, HttpResponse.BodyHandlers.ofString());

                if (httpResp.statusCode() == 200) {
                    documentMapper.updateStatus(docId, Constants.DOC_STATUS_COMPLETED);
                    log.info("文档向量化成功: documentId={}", docId);
                } else {
                    documentMapper.updateStatus(docId, Constants.DOC_STATUS_FAILED);
                    log.error("文档向量化失败: status={}, body={}", httpResp.statusCode(), httpResp.body());
                }
            } catch (Exception e) {
                documentMapper.updateStatus(docId, Constants.DOC_STATUS_FAILED);
                log.error("文档向量化异常: documentId={}", docId, e);
            } finally {
                if (tempFile != null && tempFile.exists()) {
                    tempFile.delete();
                }
            }

            // 重新查询以获取数据库自动填充的字段（createTime、updateTime）
            Document saved = documentMapper.selectById(docId);

            log.info("文档上传成功: documentId={}, object={}", docId, objectName);
            return Result.ok(saved);
        } catch (Exception e) {
            log.error(DOC_UPLOAD_FAILED, e);
            throw new BizException(DOC_UPLOAD_FAILED + ": " + e.getMessage());
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
            // 先删除向量库中的文档切片
            try {
                HttpRequest delReq = HttpRequest.newBuilder()
                        .uri(URI.create(pythonBaseUrl + INGEST_PATH + "/" + documentId))
                        .timeout(Duration.ofSeconds(30))
                        .DELETE()
                        .build();
                httpClient.send(delReq, HttpResponse.BodyHandlers.ofString());
                log.info("向量库文档删除成功: documentId={}", documentId);
            } catch (Exception e) {
                log.warn("向量库文档删除失败（不影响本地删除）: documentId={}", documentId, e);
            }

            fileService.deleteFile(document.getFilePath());
            documentMapper.deleteById(documentId);

            log.info("文档删除成功: documentId={}", documentId);
            return Result.ok();
        } catch (Exception e) {
            log.error(DOC_DELETE_FAILED, e);
            throw new BizException(DOC_DELETE_FAILED);
        }
    }
}
