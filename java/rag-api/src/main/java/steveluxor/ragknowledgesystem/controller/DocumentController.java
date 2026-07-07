package steveluxor.ragknowledgesystem.controller;

import io.minio.errors.*;
import jakarta.servlet.http.HttpServletResponse;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import steveluxor.ragknowledgesystem.common.JwtUtils;
import steveluxor.ragknowledgesystem.common.Result;
import steveluxor.ragknowledgesystem.entity.Document;
import steveluxor.ragknowledgesystem.mapper.DocumentMapper;
import steveluxor.ragknowledgesystem.service.DocumentService;
import steveluxor.ragknowledgesystem.service.FileService;

import java.io.InputStream;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

@RestController
@RequestMapping("/document")
@Slf4j
public class DocumentController {
    private final DocumentService documentService;
    private final FileService fileService;
    private final DocumentMapper documentMapper;
    private final JwtUtils jwtUtils;

    @Autowired
    public DocumentController(DocumentService documentService,
                               FileService fileService,
                               DocumentMapper documentMapper,
                               JwtUtils jwtUtils) {
        this.documentService = documentService;
        this.fileService = fileService;
        this.documentMapper = documentMapper;
        this.jwtUtils = jwtUtils;
    }

    @PostMapping("/upload")
    public Result uploadDocument(@RequestParam("file") MultipartFile file,
                                 @RequestParam("userId") Long userId,
                                 @RequestParam Integer permission) {
        log.info("上传文档请求: fileName={}, userId={}, permission={}", file.getOriginalFilename(), userId, permission);
        return documentService.uploadDocument(file, userId, permission);
    }

    @GetMapping("/{id}/download")
    public void downloadDocument(@PathVariable("id") Long documentId,
                                 @RequestParam(value = "token", required = false) String token,
                                 HttpServletResponse response) throws Exception {
        log.info("下载文件请求: documentId={}, hasToken={}", documentId, token != null);
        Document document = documentMapper.selectById(documentId);
        if (document == null) {
            response.setStatus(404);
            return;
        }

        try (InputStream stream = fileService.getFileInputStream(document.getFilePath())) {
            String encodedFileName = URLEncoder.encode(document.getFileName(), StandardCharsets.UTF_8).replace("+", "%20");
            response.setContentType(document.getFileType());
            response.setHeader("Content-Disposition", "attachment; filename*=UTF-8''" + encodedFileName);
            stream.transferTo(response.getOutputStream());
            response.getOutputStream().flush();
        }
    }

    @GetMapping("/list")
    public Result listDocuments(@RequestParam("userId") Long userId) {
        log.info("查询文档列表请求: userId={}", userId);
        return documentService.listDocuments(userId);
    }

    @DeleteMapping("/{id}")
    public Result deleteDocument(@PathVariable("id") Long documentId,
                                 @RequestParam("userId") Long userId) throws Exception {
        log.info("删除文档请求: documentId={}, userId={}", documentId, userId);
        return documentService.deleteDocument(documentId, userId);
    }

    @PostMapping("/{id}/re-ingest")
    public Result reIngestDocument(@PathVariable("id") Long documentId,
                                   @RequestParam("userId") Long userId) {
        log.info("重新向量化请求: documentId={}, userId={}", documentId, userId);
        return documentService.reIngestDocument(documentId, userId);
    }

    @GetMapping("/check-duplicate")
    public Result checkDuplicate(@RequestParam("fileName") String fileName,
                                 @RequestParam("userId") Long userId) {
        log.info("检查文件名重复: fileName={}, userId={}", fileName, userId);
        return documentService.checkDuplicate(fileName, userId);
    }

    @PostMapping("/{id}/overwrite")
    public Result overwriteDocument(@PathVariable("id") Long documentId,
                                    @RequestParam("file") MultipartFile file,
                                    @RequestParam("userId") Long userId,
                                    @RequestParam Integer permission) {
        log.info("覆盖上传文档请求: documentId={}, fileName={}, userId={}", documentId, file.getOriginalFilename(), userId);
        return documentService.overwriteDocument(documentId, file, userId, permission);
    }

    @PutMapping("/{id}/status")
    public Result updateStatus(@PathVariable("id") Long documentId,
                               @RequestBody java.util.Map<String, String> body) {
        log.info("更新文档状态: documentId={}, status={}", documentId, body.get("status"));
        documentService.updateDocumentStatus(documentId, body.get("status"));
        return Result.ok();
    }
}
