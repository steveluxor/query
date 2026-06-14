package steveluxor.ragknowledgesystem.controller;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import steveluxor.ragknowledgesystem.common.Result;
import steveluxor.ragknowledgesystem.service.DocumentService;

@RestController
@RequestMapping("/document")
@Slf4j
public class DocumentController {
    private final DocumentService documentService;

    @Autowired
    public DocumentController(DocumentService documentService) {
        this.documentService = documentService;
    }

    @PostMapping("/upload")
    public Result uploadDocument(@RequestParam("file") MultipartFile file,
                                 @RequestParam("userId") Long userId,
                                 @RequestParam Integer permission) {
        log.info("上传文档请求: fileName={}, userId={}, permission={}", file.getOriginalFilename(), userId, permission);
        return documentService.uploadDocument(file, userId, permission);
    }

    @GetMapping("/{id}/url")
    public Result getDocumentUrl(@PathVariable("id") Long documentId) {
        log.info("获取文档URL请求: documentId={}", documentId);
        return documentService.getDocumentUrl(documentId);
    }

    @GetMapping("/list")
    public Result listDocuments(@RequestParam("userId") Long userId) {
        log.info("查询文档列表请求: userId={}", userId);
        return documentService.listDocuments(userId);
    }

    @DeleteMapping("/{id}")
    public Result deleteDocument(@PathVariable("id") Long documentId,
                                 @RequestParam("userId") Long userId) {
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
